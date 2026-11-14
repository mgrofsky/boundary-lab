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
# The initialize handshake version, for a server of the older era.
PROTOCOL = "2025-06-18"
# Versions of the 2026-07-28 form this build speaks, oldest first.
MODERN_VERSIONS = ("2026-07-28",)
# How long the server/discover probe waits before it treats the server as older.
PROBE_SECONDS = 5.0
UNSUPPORTED_VERSION = -32022
SKIP_REASONS = {
    "name": "the name does not fit Latch's pattern",
    "duplicate": "the server listed this name before, and only the first is written",
    "reserved": "its inputSchema uses $inputResponses or $requestState, which the proxy keeps",
}


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
    parser.add_argument("--flow", action="store_true",
                        help="with --print-policy, turn on the flow rule and label every tool")
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


class SafeFormatter(logging.Formatter):
    """A last guard for the terminal: no control character but a newline is written.

    Text from the server is made safe where it is logged, with wire.printable. This
    also covers a traceback that quotes it.
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return "".join(ch if ch.isprintable() or ch == "\n" else "?" for ch in text)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SafeFormatter("latch mcp: %(message)s"))
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
        return print_policy(command, flow=args.flow)
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


def print_policy(command: list[str], flow: bool = False) -> int:
    try:
        tools = asyncio.run(list_tools(command))
    except (OSError, ValueError, TypeError, TimeoutError) as exc:
        logger.error("could not list the server's tools: %s", wire.printable(str(exc)))
        return 1
    text, skipped = starter_policy(tools, flow=flow)
    for name, why in skipped:
        logger.warning("left out tool %s: %s", wire.printable(repr(name)[:80]), SKIP_REASONS[why])
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


def modern_meta(version: str) -> dict[str, Any]:
    """The protocol fields boundary-latch sends on its own 2026-07-28 requests."""
    return {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "boundary-latch", "version": __version__},
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def discovered_version(answer: dict[str, Any] | None) -> str | None:
    """The newest version both sides know, or None when the server is of the older era.

    A server/discover result lists supportedVersions. An UnsupportedProtocolVersion
    error lists data.supported; the spec says that server is modern too, so the probe
    must not fall back to initialize. Any other answer, or none, means an older
    server. A modern server that shares no version is an error.
    """
    if not isinstance(answer, dict):
        return None
    supported: Any = None
    result, error = answer.get("result"), answer.get("error")
    if isinstance(result, dict):
        supported = result.get("supportedVersions")
    elif isinstance(error, dict) and error.get("code") == UNSUPPORTED_VERSION:
        data = error.get("data")
        supported = data.get("supported") if isinstance(data, dict) else None
    if not isinstance(supported, list):
        return None
    shared = sorted(set(MODERN_VERSIONS) & {v for v in supported if isinstance(v, str)})
    if not shared:
        offered = wire.printable(", ".join(str(v) for v in supported))
        raise ValueError(f"the server supports {offered}, and this build speaks "
                         f"{', '.join(MODERN_VERSIONS)}")
    return shared[-1]


async def list_tools(command: list[str]) -> list[dict[str, Any]]:
    """Start the server, agree on the protocol, and read every page of tools/list.

    It probes with server/discover first. A server that answers it gets the
    2026-07-28 form, with the protocol fields on every request. A server that
    refuses it or stays silent gets the initialize handshake of the older era.
    """
    child = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        limit=LINE_LIMIT,
    )
    assert child.stdin is not None and child.stdout is not None

    async def answer(msg_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
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
                return message

    async def ask(msg_id: int, method: str, params: dict[str, Any]) -> Any:
        message = await answer(msg_id, method, params)
        if "error" in message:
            raise ValueError(f"{method} failed")
        return message.get("result")

    async def probe() -> str | None:
        try:
            reply = await asyncio.wait_for(
                answer(1, "server/discover", {"_meta": modern_meta(MODERN_VERSIONS[-1])}),
                PROBE_SECONDS)
        except TimeoutError:
            return None
        return discovered_version(reply)

    async def talk() -> list[dict[str, Any]]:
        version = await probe()
        extra: dict[str, Any] = {}
        if version is None:
            await ask(2, "initialize", {
                "protocolVersion": PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "boundary-latch", "version": __version__},
            })
            child.stdin.write(wire.dump({"jsonrpc": "2.0",
                                         "method": "notifications/initialized"}))
        else:
            extra = {"_meta": modern_meta(version)}
        tools: list[dict[str, Any]] = []
        cursor = None
        for page in range(MAX_PAGES):
            params = {} if cursor is None else {"cursor": cursor}
            result = await ask(3 + page, "tools/list", {**params, **extra})
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
