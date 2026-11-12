"""The relay between one MCP client and one MCP server, with Latch on every tools/call."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..engine import Latch
from ..errors import ToolFailed
from ..models import CallInfo, Decision, ExecutionResult, PreparedAction, UserContext
from ..reasons import Code, explain
from . import wire
from .catalog import Changed, ToolCatalog
from .pins import Pins

logger = logging.getLogger("boundary_latch.mcp")

LINE_LIMIT = 64 * 1024 * 1024
STOP_SECONDS = 5.0
OPEN = ("prepared", "approved")


class ChildGone(Exception):
    """The server exited before it answered."""


@dataclass
class Settings:
    user: str = "agent"
    org: str = "local"
    approver: str = "person"
    permissions: frozenset[str] = frozenset()
    approver_permissions: frozenset[str] = frozenset()
    tool_timeout: float = 60.0
    approval_wait: float = 120.0
    page_on: bool = True
    open_browser: bool = True


@dataclass
class Call:
    """A tools/call from the client while the proxy handles it."""

    msg_id: Any
    op_id: str | None = None
    cancelled: bool = False
    task: asyncio.Task | None = None


class Proxy(ToolCatalog):
    def __init__(self, latch: Latch, settings: Settings, pins: Pins,
                 write_client: Callable[[bytes], None]):
        self.latch, self.settings, self.pins = latch, settings, pins
        self.write_client = write_client
        self.run_id = str(uuid.uuid4())
        self.ctx = UserContext(user_id=settings.user, org_id=settings.org,
                               permissions=settings.permissions, run_id=self.run_id)
        self.approver = UserContext(user_id=settings.approver, org_id=settings.org,
                                    permissions=settings.approver_permissions,
                                    run_id="approval-page")
        self.child: asyncio.subprocess.Process | None = None
        self.page_url: str | None = None
        self._next_id = 0
        self._to_child: dict[int, tuple[str, Any]] = {}
        self._calls: dict[tuple[str, Any], Call] = {}
        self._tools_known = False
        self._tools_lock = asyncio.Lock()
        self._order: list[str] = []
        self.served: dict[str, dict[str, Any]] = {}
        self.changed: dict[str, Changed] = {}
        self._hidden_logged: set[str] = set()
        self._open_ops: dict[str, str] = {}
        self.prepared: dict[str, PreparedAction] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._replies: dict[str, dict[str, Any]] = {}
        self._in_flight: dict[str, int] = {}
        self._opened = False
        self._tasks: set[asyncio.Task] = set()

    # Writing

    def to_client(self, message: dict[str, Any]) -> None:
        self.write_client(wire.dump(message))

    def to_child(self, message: dict[str, Any]) -> None:
        if self.child is None or self.child.stdin is None or self.child.stdin.is_closing():
            raise ChildGone("the server is not running")
        self.child.stdin.write(wire.dump(message))

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request of the proxy's own to the child and wait for the answer."""
        child_id = self._new_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._to_child[child_id] = ("proxy", future)
        try:
            self.to_child({"jsonrpc": "2.0", "id": child_id, "method": method, "params": params})
            return await future
        finally:
            self._to_child.pop(child_id, None)

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # Messages from the client

    def on_client_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message = wire.check_client(line)
        except wire.BadMessage as exc:
            logger.warning("refused a client line: %s", exc.message)
            self.to_client(wire.error(exc.msg_id, exc.code, exc.message))
            return
        self.on_client(message)

    def on_client(self, message: dict[str, Any]) -> None:
        kind = wire.kind(message)
        method = message.get("method")
        if kind == "response":
            self.to_child(message)
            return
        if method in ("tools/call", "tools/list"):
            if kind == "notification":
                logger.warning("dropped a %s notification with no id", method)
                return
            msg_id = message["id"]
            if method == "tools/list":
                self._spawn(self.answer_list(msg_id))
                return
            call = Call(msg_id=msg_id)
            self._calls[wire.id_key(msg_id)] = call
            call.task = self._spawn(self.answer_call(call, message.get("params")))
            return
        if kind == "notification":
            if method == "notifications/cancelled":
                self.on_cancelled(message)
                return
            self.to_child(message)
            return
        child_id = self._new_id()
        self._to_child[child_id] = ("client", message["id"])
        self.to_child({**message, "id": child_id})

    def on_cancelled(self, message: dict[str, Any]) -> None:
        params = message.get("params")
        request_id = params.get("requestId") if isinstance(params, dict) else None
        if not wire.valid_id(request_id):
            return
        call = self._calls.get(wire.id_key(request_id))
        if call is not None:
            self._cancel_call(call)
            return
        for child_id, (owner, client_id) in list(self._to_child.items()):
            if owner == "client" and wire.id_key(client_id) == wire.id_key(request_id):
                self.to_child({**message, "params": {**params, "requestId": child_id}})
                return

    def _cancel_call(self, call: Call) -> None:
        call.cancelled = True
        op_id = call.op_id
        if op_id is None:
            return
        prepared = self.prepared.get(op_id)
        status = self.latch.operation_status(self.settings.org, op_id)
        if prepared is not None and status is not None and status.state in OPEN:
            self.latch.cancel(prepared, self.ctx)
            self._wake(op_id)
            return
        child_id = self._in_flight.get(op_id)
        if child_id is not None:
            with contextlib.suppress(ChildGone):
                self.to_child({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": child_id, "reason": "cancelled"}})

    # Messages from the child

    def on_child_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message, _ = wire.parse(line)
            if not isinstance(message, dict):
                raise wire.BadMessage(wire.INVALID_REQUEST, "not an object")
            kind = wire.kind(message)
        except wire.BadMessage:
            logger.warning("dropped a line from the server that is not a JSON-RPC message")
            return
        if kind == "response":
            target = self._to_child.pop(message["id"], None) if isinstance(
                message["id"], int) else None
            if target is None:
                logger.warning("dropped a server response with an unknown id")
                return
            owner, value = target
            if owner == "proxy":
                if not value.done():
                    value.set_result(message)
                return
            self.to_client({**message, "id": value})
            return
        if message.get("method") == "notifications/tools/list_changed":
            self._tools_known = False
        self.to_client(message)

    def child_gone(self) -> None:
        for owner, value in list(self._to_child.values()):
            if owner == "proxy" and not value.done():
                value.set_exception(ChildGone("the server exited"))
        self._to_child.clear()

    # tools/call

    async def answer_call(self, call: Call, params: Any) -> None:
        try:
            reply = await self.run_call(call, params)
        except Exception:
            logger.exception("refused a tools/call after an error in the proxy")
            reply = ("result", wire.text_result(
                "Blocked by Latch: PROXY_ERROR. The proxy hit an error while handling this "
                "call. If the call already reached the server, its outcome is unknown."
            ))
        finally:
            self._calls.pop(wire.id_key(call.msg_id), None)
        if call.cancelled:
            return
        kind, value = reply
        if kind == "error":
            self.to_client({"jsonrpc": "2.0", "id": call.msg_id, "error": value})
        else:
            self.to_client(wire.result(call.msg_id, value))

    def _blocked(self, code: Code, extra: str = "") -> tuple[str, Any]:
        text = explain(code) + (f" {extra}" if extra else "")
        return "result", wire.blocked(str(code), text)

    def _refuse_changed(self, name: str, op_id: str) -> tuple[str, Any]:
        self.latch._write_audit(
            "prepare", str(Code.TOOL_CHANGED), self.settings.org, run_id=self.run_id,
            operation_id=op_id, user_id=self.settings.user, tool=name, now=self.latch.clock(),
        )
        return self._blocked(Code.TOOL_CHANGED)

    async def run_call(self, call: Call, params: Any) -> tuple[str, Any]:
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return "error", {"code": wire.INVALID_PARAMS, "message": "tools/call needs a name"}
        name = params["name"]
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        await self.ensure_tools()
        key = wire.canonical([name, arguments])
        op_id = self._open_ops.get(key)
        if op_id is not None:
            status = self.latch.operation_status(self.settings.org, op_id)
            if status is None or status.state not in OPEN:
                self._open_ops.pop(key, None)
                op_id = None
        op_id = op_id or str(uuid.uuid4())
        call.op_id = op_id
        if name in self.changed:
            return self._refuse_changed(name, op_id)
        prepared = self.latch.prepare(self.ctx, name, arguments, op_id)
        if isinstance(prepared, Decision):
            return self._blocked(prepared.code)
        if prepared.needs_approval:
            if not self.settings.page_on:
                self.latch.cancel(prepared, self.ctx)
                return self._blocked(Code.NEEDS_APPROVAL, "The approval page is off.")
            self.prepared[op_id] = prepared
            self._open_ops[key] = op_id
            state = await self.wait_for_decision(op_id)
            if call.cancelled:
                return self._blocked(Code.CANCELLED)
            if state == "prepared":
                return "result", wire.text_result(
                    f"This call waits for a person to approve it. Operation id {op_id}. "
                    "Repeat the same call later, with the same arguments, to get the result."
                )
            if state != "approved":
                status = self.latch.operation_status(self.settings.org, op_id)
                self._open_ops.pop(key, None)
                return self._blocked(status.code if status else Code.OPERATION_CONFLICT)
        if name in self.changed:
            return self._refuse_changed(name, op_id)
        try:
            return await self.execute(prepared)
        finally:
            self._open_ops.pop(key, None)

    async def wait_for_decision(self, op_id: str) -> str:
        status = self.latch.operation_status(self.settings.org, op_id)
        if status is None or status.state != "prepared":
            return status.state if status else "missing"
        self.open_page()
        if self.settings.approval_wait > 0:
            event = self._events.setdefault(op_id, asyncio.Event())
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), self.settings.approval_wait)
        status = self.latch.operation_status(self.settings.org, op_id)
        return status.state if status else "missing"

    def _wake(self, op_id: str) -> None:
        event = self._events.pop(op_id, None)
        if event is not None:
            event.set()

    def open_page(self) -> None:
        if self._opened or not self.settings.open_browser or self.page_url is None:
            return
        self._opened = True
        threading.Thread(target=webbrowser.open, args=(self.page_url,), daemon=True).start()

    async def execute(self, prepared: PreparedAction) -> tuple[str, Any]:
        """Run the operation once. Calls that share an operation id share the answer."""
        op_id = prepared.operation_id
        task = self._running.get(op_id)
        if task is None:
            task = self._spawn(self._execute(prepared))
            self._running[op_id] = task
            task.add_done_callback(lambda _: self._running.pop(op_id, None))
        return await asyncio.shield(task)

    async def _execute(self, prepared: PreparedAction) -> tuple[str, Any]:
        op_id = prepared.operation_id
        try:
            outcome = await self.latch.aexecute(prepared, self.ctx)
        finally:
            self.prepared.pop(op_id, None)
        reply = self._replies.pop(op_id, None)
        return self.map_outcome(outcome, reply)

    def map_outcome(self, outcome: ExecutionResult, reply: dict[str, Any] | None
                    ) -> tuple[str, Any]:
        if outcome.outcome == "succeeded" and not outcome.replayed:
            return "result", outcome.value
        if outcome.outcome == "failed" and not outcome.replayed and reply is not None:
            if "error" in reply:
                return "error", reply["error"]
            return "result", reply["result"]
        if outcome.outcome == "uncertain" and not outcome.replayed:
            return "result", wire.text_result(f"Latch: {outcome.code}. {outcome.explanation}")
        return self._blocked(outcome.code)

    async def handler(self, args: dict[str, Any], info: CallInfo) -> Any:
        """The Latch handler. It is the only place a tools/call is sent to the server."""
        child_id = self._new_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._to_child[child_id] = ("proxy", future)
        self._in_flight[info.operation_id] = child_id
        try:
            self.to_child({"jsonrpc": "2.0", "id": child_id, "method": "tools/call",
                           "params": {"name": info.tool, "arguments": args}})
            reply = await future
        except asyncio.CancelledError:
            with contextlib.suppress(ChildGone):
                self.to_child({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": child_id, "reason": "timed out"}})
            raise
        finally:
            self._to_child.pop(child_id, None)
            self._in_flight.pop(info.operation_id, None)
        if "error" in reply:
            self._replies[info.operation_id] = reply
            raise ToolFailed("the server answered with a JSON-RPC error")
        value = reply.get("result")
        if not isinstance(value, dict):
            raise TypeError("the server's tools/call result is not an object")
        if value.get("isError") is True:
            self._replies[info.operation_id] = reply
            raise ToolFailed("the tool reported an error")
        return value

    # Approval page actions, run on the event loop

    def page_state(self) -> dict[str, Any]:
        now = time.time()
        pending = []
        for status in self.latch.pending(self.settings.org, self.run_id):
            prepared = self.prepared.get(status.operation_id)
            if prepared is None:
                continue
            pending.append({
                "operation_id": status.operation_id, "tool": status.tool,
                "summary": status.summary, "arguments": prepared.arguments,
                "waited": max(0, int(now - status.created_at)),
            })
        changed = [{"tool": name, "hash": item.hash, "old": item.old, "new": item.new}
                   for name, item in self.changed.items()]
        return {"pending": pending, "changed": changed}

    def decide(self, op_id: str, approve: bool) -> str:
        prepared = self.prepared.get(op_id)
        if prepared is None:
            return "UNKNOWN_OPERATION"
        act = self.latch.approve if approve else self.latch.deny
        decision = act(prepared, self.approver, reference="approval-page")
        self._wake(op_id)
        return str(decision.code)

    # Running

    async def serve(self, command: list[str], read_client: asyncio.Queue) -> int:
        self.child = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        child_reader = asyncio.get_running_loop().create_task(self._read_child())
        client_reader = asyncio.get_running_loop().create_task(self._read_client(read_client))
        done, _ = await asyncio.wait({child_reader, client_reader},
                                     return_when=asyncio.FIRST_COMPLETED)
        if child_reader in done:
            client_reader.cancel()
            self.child_gone()
            await self._settle()
            code = await self.child.wait()
            return code if code >= 0 else 128 - code
        child_reader.cancel()
        await self.stop_child()
        for task in list(self._tasks):
            task.cancel()
        return 0

    async def _settle(self) -> None:
        """Let calls that were waiting on the server write their answers."""
        busy = [task for task in self._tasks if not task.done()]
        if busy:
            await asyncio.wait(busy, timeout=1.0)

    async def stop_child(self) -> None:
        child = self.child
        if child is None or child.returncode is not None:
            return
        with contextlib.suppress(Exception):
            child.stdin.close()
        with contextlib.suppress(ProcessLookupError):
            child.terminate()
        try:
            await asyncio.wait_for(child.wait(), STOP_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                child.kill()
            await child.wait()

    async def _read_child(self) -> None:
        assert self.child is not None and self.child.stdout is not None
        while True:
            try:
                line = await self.child.stdout.readline()
            except ValueError:
                logger.warning("dropped a line from the server longer than the limit")
                continue
            if not line:
                return
            self.on_child_line(line)
            await self._drain()

    async def _read_client(self, queue: asyncio.Queue) -> None:
        while True:
            line = await queue.get()
            if line is None:
                return
            try:
                self.on_client_line(line)
            except ChildGone:
                logger.warning("the server is gone, so a client message was not sent")
            await self._drain()

    async def _drain(self) -> None:
        if self.child is not None and self.child.stdin is not None:
            with contextlib.suppress(ConnectionError):
                await self.child.stdin.drain()


def stdin_lines(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, fd: int = 0) -> None:
    """Read lines from a file descriptor in a thread and hand them to the loop.

    It reads with os.read so no Python file lock is held when the process exits.
    """
    buffer = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            if buffer:
                loop.call_soon_threadsafe(queue.put_nowait, buffer)
            loop.call_soon_threadsafe(queue.put_nowait, None)
            return
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            loop.call_soon_threadsafe(queue.put_nowait, line)
