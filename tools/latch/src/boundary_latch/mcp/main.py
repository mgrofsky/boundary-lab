"""boundary-latch mcp: options, start up checks, and the starter policy."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import logging
import os
import sys
import threading
from typing import Any

from .. import __version__
from ..engine import Latch
from ..errors import ConfigError, StorageError
from ..policy import Policy, load_policy
from ..store import SQLiteStore
from . import wire
from .catalog import MAX_PAGES
from .pins import Pins
from .proxy import LINE_LIMIT, STOP_SECONDS, Proxy, Settings, stdin_lines
from .tools import starter_policy

logger = logging.getLogger("boundary_latch.mcp")

LIST_SECONDS = 30.0
PROTOCOL = "2025-06-18"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy", help="path to a TOML policy file")
    parser.add_argument("--user", default="agent", help='the agent\'s user id (default "agent")')
    parser.add_argument("--org", default="local", help='organization id (default "local")')
    parser.add_argument("--permissions", default=None, metavar="A,B",
                        help="permissions the agent holds (default: every require_permission "
                             "in the policy, and never an approve_permission)")
    parser.add_argument("--approver", default=None,
                        help="user id of the person on the approval page (default: your login)")
    parser.add_argument("--db", default=":memory:",
                        help="SQLite file for operations and audit rows (default: memory)")
    parser.add_argument("--tool-timeout", type=float, default=60.0, metavar="SECONDS",
                        help="how long a tool call may take (default 60)")
    parser.add_argument("--approval-wait", type=float, default=120.0, metavar="SECONDS",
                        help="how long a call waits for a decision (default 120, 0 = no wait)")
    parser.add_argument("--approval-port", type=int, default=0, metavar="N",
                        help="approval page port (default 0 = any free port, -1 = no page)")
    parser.add_argument("--no-open", action="store_true",
                        help="do not open the approval page in a browser")
    parser.add_argument("--pin", default=None, metavar="FILE",
                        help="JSON file that keeps tool definitions pinned between runs")
    parser.add_argument("--print-policy", action="store_true",
                        help="print a starter policy for the server's tools and exit")
    parser.add_argument("server", nargs=argparse.REMAINDER,
                        help="-- then the server command and its arguments")


def fail(message: str) -> int:
    print(f"boundary-latch mcp: {message}", file=sys.stderr)
    return 2


def permissions_for(policy: Policy, given: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """The agent's permissions and the approver's permissions."""
    approve = frozenset(tp.approve_permission for tp in policy.tools.values()
                        if tp.approve_permission)
    if given is None:
        agent = frozenset(tp.require_permission for tp in policy.tools.values()) - approve
    else:
        agent = frozenset(p.strip() for p in given.split(",") if p.strip())
        overlap = sorted(agent & approve)
        if overlap:
            logger.warning("the agent holds approve permissions: %s", ", ".join(overlap))
    return agent, approve


def check_policy(policy: Policy) -> str | None:
    for name, tp in policy.tools.items():
        if tp.approve_permission is not None and tp.approve_permission == tp.require_permission:
            return (f"tool {name} uses {tp.require_permission} as both require_permission and "
                    "approve_permission, so the agent could approve its own calls")
    return None


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("latch mcp: %(message)s"))
    root = logging.getLogger("boundary_latch")
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False


def private_stdout() -> Any:
    """Keep the real stdout for JSON-RPC only and point fd 1 at stderr.

    A stray print, a library, or a browser launcher then cannot write into the
    protocol stream.
    """
    out = os.dup(1)
    os.dup2(2, 1)
    lock = threading.Lock()

    def write(data: bytes) -> None:
        with lock:
            view = memoryview(data)
            while view:
                view = view[os.write(out, view):]

    return write


def run(args: argparse.Namespace) -> int:
    command = list(args.server)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        return fail("give the server command after --")
    setup_logging()
    if args.print_policy:
        return print_policy(command)
    if not args.policy:
        return fail("--policy is required")
    try:
        policy = load_policy(args.policy)
    except ConfigError as exc:
        return fail(str(exc))
    except OSError as exc:
        return fail(f"could not read {args.policy}: {exc.strerror}")
    problem = check_policy(policy)
    if problem:
        return fail(problem)
    approver = args.approver or getpass.getuser()
    if approver == args.user:
        return fail("--approver must differ from --user, since nobody approves their own call")
    agent, approve = permissions_for(policy, args.permissions)
    try:
        pins = Pins(args.pin)
        store = SQLiteStore(args.db)
    except (ConfigError, StorageError) as exc:
        return fail(str(exc))
    settings = Settings(
        user=args.user, org=args.org, approver=approver, permissions=agent,
        approver_permissions=approve, tool_timeout=args.tool_timeout,
        approval_wait=max(0.0, args.approval_wait), page_on=args.approval_port != -1,
        open_browser=not args.no_open,
    )
    latch = Latch(policy, store)
    write = private_stdout()
    try:
        return asyncio.run(serve(latch, settings, pins, command, args.approval_port, write))
    finally:
        with contextlib.suppress(StorageError):
            store.close()


async def serve(latch: Latch, settings: Settings, pins: Pins, command: list[str],
                port: int, write: Any) -> int:
    from .page import ApprovalPage

    loop = asyncio.get_running_loop()
    proxy = Proxy(latch, settings, pins, write)
    page = None
    if settings.page_on:
        page = ApprovalPage(proxy, loop, port)
        page.start()
        proxy.page_url = page.url
        logger.info("approval page: %s", page.url)
    else:
        logger.info("approval page is off; calls that need approval are refused")
    queue: asyncio.Queue = asyncio.Queue()
    threading.Thread(target=stdin_lines, args=(loop, queue), daemon=True).start()
    try:
        return await proxy.serve(command, queue)
    except OSError as exc:
        logger.error("could not start the server: %s", exc)
        return 1
    finally:
        if page is not None:
            page.close()


def print_policy(command: list[str]) -> int:
    try:
        tools = asyncio.run(list_tools(command))
    except (OSError, ValueError, TypeError, TimeoutError) as exc:
        logger.error("could not list the server's tools: %s", exc)
        return 1
    text, skipped = starter_policy(tools)
    for name in skipped:
        logger.warning("left out tool %r: the name does not fit Latch's pattern", name[:80])
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


async def list_tools(command: list[str]) -> list[dict[str, Any]]:
    """Start the server, do the MCP handshake, and read every page of tools/list."""
    child = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        limit=LINE_LIMIT,
    )
    assert child.stdin is not None and child.stdout is not None

    async def ask(msg_id: int, method: str, params: dict[str, Any]) -> Any:
        child.stdin.write(wire.dump({"jsonrpc": "2.0", "id": msg_id, "method": method,
                                     "params": params}))
        await child.stdin.drain()
        while True:
            line = await child.stdout.readline()
            if not line:
                raise ValueError("the server exited")
            try:
                message, _ = wire.parse(line)
            except wire.BadMessage:
                continue
            if isinstance(message, dict) and message.get("id") == msg_id \
                    and "method" not in message:
                if "error" in message:
                    raise ValueError(f"{method} failed")
                return message.get("result")

    async def talk() -> list[dict[str, Any]]:
        await ask(1, "initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "boundary-latch", "version": __version__},
        })
        child.stdin.write(wire.dump({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools: list[dict[str, Any]] = []
        cursor = None
        for page in range(MAX_PAGES):
            params = {} if cursor is None else {"cursor": cursor}
            result = await ask(2 + page, "tools/list", params)
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise TypeError("tools/list has no tools list")
            tools += [tool for tool in result["tools"] if isinstance(tool, dict)]
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str):
                return tools
        raise ValueError("tools/list did not end")

    try:
        return await asyncio.wait_for(talk(), LIST_SECONDS)
    finally:
        with contextlib.suppress(Exception):
            child.stdin.close()
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                child.terminate()
            try:
                await asyncio.wait_for(child.wait(), STOP_SECONDS)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
                await child.wait()
