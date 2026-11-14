"""The relay between one MCP client and one MCP server, with Latch on every tools/call."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import threading
import time
import uuid
import webbrowser
from collections import OrderedDict
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
from .tools import RESERVED

logger = logging.getLogger("boundary_latch.mcp")

LINE_LIMIT = 64 * 1024 * 1024
STOP_SECONDS = 5.0
OPEN = ("prepared", "approved")
# How many times one call may go back to waiting because the flow rule changed its reason.
FLOW_ROUNDS = 3
SUBSCRIPTION = "io.modelcontextprotocol/subscriptionId"
PAGE_KEYS = {"inputResponses": "input_responses", "requestState": "request_state"}
TASK_UPDATE = "Blocked by Latch: input to a running task is not checked yet"
# The only tasks methods that pass through. Every other tasks/ method is refused.
TASK_METHODS = frozenset({"tasks/get", "tasks/cancel"})
# The params keys of a tools/call the proxy understands. Any other key is refused.
CALL_KEYS = frozenset({"name", "arguments", "_meta", "inputResponses", "requestState"})
# decide() answers this when the form's digest does not match the operation now.
STALE_PAGE = "STALE_PAGE"
# The params._meta keys of a tools/call that reach the server. They are outside the
# request hash and the approval page, so only protocol and tracing keys pass.
META_KEYS = frozenset({
    "io.modelcontextprotocol/protocolVersion", "io.modelcontextprotocol/clientInfo",
    "io.modelcontextprotocol/clientCapabilities", "io.modelcontextprotocol/logLevel",
    "progressToken", "traceparent", "tracestate", "baggage",
})


# How many input requests from the server the proxy keeps. The oldest is dropped first.
INPUT_RECORDS = 1000
# How long the proxy waits for the server to exit after its output ends.
EOF_GRACE = 1.0
PROXY_ERROR = ("Blocked by Latch: PROXY_ERROR. The proxy hit an error while handling this "
               "call. If the call already reached the server, its outcome is unknown.")


class ChildGone(Exception):
    """The server exited before it answered."""


class ProxyTimeout(Exception):
    """The server did not answer a request of the proxy's own in time."""


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


@dataclass(frozen=True)
class Asked:
    """What a server asked for when it answered a tools/call with input_required."""

    state: str | None
    keys: frozenset[str]


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
        # How many notifications/tools/list_changed came in. A fetch compares it at its
        # start and end, so a change during the fetch keeps the list stale.
        self._list_changes = 0
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
        # The protocol fields of the latest client request that had them. None until then,
        # which is the older era: the proxy's own requests then carry no _meta.
        self._protocol: dict[str, Any] | None = None
        # Top level fields of the server's last tools/list page, other than tools.
        self._list_fields: dict[str, Any] = {}
        # The client's params._meta for each operation, forwarded with its tools/call.
        self._call_meta: dict[str, dict[str, Any] | None] = {}
        # Open subscriptions/listen requests: the proxy's id to the client's id.
        self._listens: dict[int, Any] = {}
        # The input each tool and arguments wait for, from the server's input_required
        # results: round_key -> Asked. A client round must match one of these.
        self._asked: OrderedDict[str, Asked] = OrderedDict()
        # _meta key names already logged as dropped.
        self._dropped_meta: set[str] = set()

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

    def with_protocol(self, params: dict[str, Any]) -> dict[str, Any]:
        """Params for a message of the proxy's own, with the client's protocol fields."""
        if self._protocol is None:
            return params
        return {**params, "_meta": dict(self._protocol)}

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request of the proxy's own to the child and wait for the answer.

        It waits at most --tool-timeout seconds, so a server that never answers cannot
        hold the tool list lock forever. On timeout the server is told to cancel.
        """
        child_id = self._new_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._to_child[child_id] = ("proxy", future)
        try:
            self.to_child({"jsonrpc": "2.0", "id": child_id, "method": method,
                           "params": self.with_protocol(params)})
            return await asyncio.wait_for(future, self.settings.tool_timeout)
        except TimeoutError:
            with contextlib.suppress(ChildGone):
                self.to_child({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": self.with_protocol(
                                   {"requestId": child_id, "reason": "timed out"})})
            raise ProxyTimeout(f"the server did not answer {method} in time") from None
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
        if self._refused_task(message, kind):
            return
        if method in ("tools/call", "tools/list"):
            if kind == "notification":
                logger.warning("dropped a %s notification with no id", method)
                return
            msg_id = message["id"]
            if method == "tools/list":
                self.keep_protocol(message.get("params"))
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
        self.keep_protocol(message.get("params"))
        child_id = self._new_id()
        self._to_child[child_id] = ("client", message["id"])
        if method == "subscriptions/listen":
            self._listens[child_id] = message["id"]
        self.to_child({**message, "id": child_id})

    def keep_protocol(self, params: Any) -> None:
        """Keep the protocol fields of a client request the proxy accepted.

        One odd request must not change every later request the proxy makes, so a
        set of fields is kept only when it looks right, and it replaces the old set
        as a whole.
        """
        fields = wire.protocol_fields(params)
        if fields and wire.sound_protocol(fields):
            self._protocol = fields

    def _refused_task(self, message: dict[str, Any], kind: str) -> bool:
        """Refuse a tasks method that is not on the allow list. True when it was refused.

        tasks/get and tasks/cancel pass through. Any other tasks method, such as
        tasks/update, could feed input into a call that already ran through Latch with
        nothing checked. So could a get or cancel that carries an input round.
        """
        method = message.get("method")
        if not isinstance(method, str) or not method.startswith("tasks/"):
            return False
        params = message.get("params")
        carries = isinstance(params, dict) and any(key in params for key in PAGE_KEYS)
        if method in TASK_METHODS and not carries:
            return False
        name = wire.printable(repr(method)[:80])
        if kind == "notification":
            logger.warning("dropped a %s notification: tasks input is not checked yet", name)
            return True
        logger.warning("refused %s: input to a running task is not checked yet", name)
        self.to_client(wire.error(message["id"], wire.METHOD_NOT_FOUND, TASK_UPDATE))
        return True

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
                # The client ignores any answer that comes later, so forget the request.
                # A subscriptions/listen may never be answered at all.
                self._to_child.pop(child_id, None)
                self._listens.pop(child_id, None)
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
                               "params": self.with_protocol(
                                   {"requestId": child_id, "reason": "cancelled"})})

    # Messages from the child

    def on_child_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message, _ = wire.parse(line)
            kind = wire.check_child(message)
        except wire.BadMessage:
            logger.warning("dropped a line from the server that is not a JSON-RPC message")
            return
        if kind == "response":
            child_id = message["id"]
            proxy_id = isinstance(child_id, int) and not isinstance(child_id, bool)
            target = self._to_child.pop(child_id, None) if proxy_id else None
            if target is None:
                logger.warning("dropped a server response with an unknown id")
                return
            owner, value = target
            if owner == "proxy":
                if not value.done():
                    value.set_result(message)
                return
            if self._open_listen(message["id"]):
                # A subscription that ended: its result names the subscription too.
                message = self._client_subscription(message, "result")
                del self._listens[message["id"]]
            self.to_client({**message, "id": value})
            return
        method = message.get("method")
        if method == "notifications/tools/list_changed":
            self._tools_known = False
            self._list_changes += 1
        if kind == "notification":
            if self._ended_subscription(message):
                logger.info("dropped a server notification for a subscription that ended")
                return
            message = self._client_subscription(message, "params")
            if method == "notifications/cancelled":
                message = self._cancelled_listen(message)
        self.to_client(message)

    def _open_listen(self, value: Any) -> bool:
        """True when value is the proxy's id of an open listen. Only an int can be one."""
        return isinstance(value, int) and not isinstance(value, bool) and value in self._listens

    def _ended_subscription(self, message: dict[str, Any]) -> bool:
        """True for a notification tagged with a subscription that is not open.

        Every id the server sees is the proxy's, so the client could not match it. The
        client has cancelled that subscription and would ignore it anyway. An id that
        is not an int was never the proxy's.
        """
        params = message.get("params")
        meta = params.get("_meta") if isinstance(params, dict) else None
        if not isinstance(meta, dict) or SUBSCRIPTION not in meta:
            return False
        return not self._open_listen(meta[SUBSCRIPTION])

    def _client_subscription(self, message: dict[str, Any], part: str) -> dict[str, Any]:
        """Put the client's listen id where the server put the proxy's.

        A subscription's id is the JSON-RPC id of its subscriptions/listen request, and
        the server only saw the proxy's id for it.
        """
        body = message.get(part)
        meta = body.get("_meta") if isinstance(body, dict) else None
        sub_id = meta.get(SUBSCRIPTION) if isinstance(meta, dict) else None
        if not self._open_listen(sub_id):
            return message
        meta = {**meta, SUBSCRIPTION: self._listens[sub_id]}
        return {**message, part: {**body, "_meta": meta}}

    def _cancelled_listen(self, message: dict[str, Any]) -> dict[str, Any]:
        """A server that ends a subscription cancels its listen request by the proxy's id."""
        params = message.get("params")
        request_id = params.get("requestId") if isinstance(params, dict) else None
        if not self._open_listen(request_id):
            return message
        client_id = self._listens.pop(request_id)
        self._to_child.pop(request_id, None)
        return {**message, "params": {**params, "requestId": client_id}}

    def child_gone(self) -> None:
        for owner, value in list(self._to_child.values()):
            if owner == "proxy" and not value.done():
                value.set_exception(ChildGone("the server exited"))
        self._to_child.clear()

    # tools/call

    async def answer_call(self, call: Call, params: Any) -> None:
        try:
            reply = await self.run_call(call, params)
        except ProxyTimeout as exc:
            logger.warning("refused a tools/call: %s", exc)
            reply = ("result", wire.text_result(PROXY_ERROR))
        except Exception:
            logger.exception("refused a tools/call after an error in the proxy")
            reply = ("result", wire.text_result(PROXY_ERROR))
        finally:
            self._calls.pop(wire.id_key(call.msg_id), None)
        if call.cancelled:
            return
        kind, value = reply
        if kind == "error":
            self.to_client({"jsonrpc": "2.0", "id": call.msg_id, "error": value})
        else:
            self.to_client(wire.result(call.msg_id, value))

    def _blocked(self, code: Code, extra: str = "", text: str | None = None
                 ) -> tuple[str, Any]:
        text = (text or explain(code)) + (f" {extra}" if extra else "")
        return "result", wire.blocked(str(code), text)

    def _refuse_changed(self, name: str, op_id: str) -> tuple[str, Any]:
        self.latch._write_audit(
            "prepare", str(Code.TOOL_CHANGED), self.settings.org, run_id=self.run_id,
            operation_id=op_id, user_id=self.settings.user, tool=name, now=self.latch.clock(),
        )
        return self._blocked(Code.TOOL_CHANGED)

    @staticmethod
    def call_input(params: Any) -> tuple[str, Any, dict[str, Any] | None] | str:
        """The tool name, the arguments Latch checks, and the _meta to forward.

        Returns the text of an invalid params error instead when the params hold a key
        the proxy does not check, or a field of the wrong type. inputResponses and
        requestState go into the arguments under reserved names, so the request hash,
        the summary and the approval page cover them.
        """
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return "tools/call needs a name"
        unknown = sorted(set(params) - CALL_KEYS)
        if unknown:
            return f"tools/call params has keys the proxy does not check: {', '.join(unknown)}"
        meta = params.get("_meta")
        if "_meta" in params and not isinstance(meta, dict):
            return "tools/call _meta must be an object"
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if isinstance(arguments, dict):
            taken = sorted(set(arguments) & set(RESERVED.values()))
            if taken:
                return f"tools/call arguments may not use the reserved name {taken[0]}"
        for param, reserved in RESERVED.items():
            if param not in params:
                continue
            value = params[param]
            kind = dict if param == "inputResponses" else str
            if not isinstance(value, kind):
                return f"tools/call {param} must be {'an object' if kind is dict else 'a string'}"
            if isinstance(arguments, dict):
                arguments = {**arguments, reserved: value}
        return params["name"], arguments, meta if "_meta" in params else None

    def forwarded_meta(self, meta: dict[str, Any] | None) -> dict[str, Any] | None:
        """The part of a call's _meta that reaches the server: the listed keys only.

        _meta is outside the request hash and the approval page, and the repeat call
        that runs supplies it. So a vendor key could change between what a person
        approved and what runs. Every other key is dropped, and its name logged once.
        """
        if meta is None:
            return None
        for key in meta:
            if key in META_KEYS:
                continue
            label = wire.printable(repr(key)[:80])
            if label not in self._dropped_meta:
                self._dropped_meta.add(label)
                logger.warning("dropped _meta key %s from a tools/call: only protocol and "
                               "tracing keys reach the server", label)
        return {key: value for key, value in meta.items() if key in META_KEYS}

    async def run_call(self, call: Call, params: Any) -> tuple[str, Any]:
        checked = self.call_input(params)
        if isinstance(checked, str):
            return "error", {"code": wire.INVALID_PARAMS, "message": checked}
        name, arguments, meta = checked
        if any(param in params for param in RESERVED):
            key = self.round_key(name, arguments)
            if not self.asked_for(key, params.get("inputResponses"), params.get("requestState")):
                return self._refuse_round(name)
        meta = self.forwarded_meta(meta)
        self.keep_protocol(params)
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
            return self._refused_repeat(prepared, op_id, key)
        try:
            for _ in range(FLOW_ROUNDS):
                if prepared.needs_approval:
                    waited = await self._await_approval(call, prepared, key)
                    if waited is not None:
                        return waited
                # The server may have changed the tool during the wait. Fetch the list
                # again if it did, so the call never runs under a definition nobody saw.
                await self.ensure_tools()
                if name in self.changed:
                    return self._refuse_changed(name, op_id)
                self._call_meta[op_id] = meta
                outcome, reply = await self.execute(prepared)
                if not self._needs_flow_approval(outcome):
                    return self.map_outcome(outcome, reply)
                # The flow rule applied after prepare. A person must see the reason and
                # approve, so prepare again to get it and wait like any other approval.
                prepared = self.latch.prepare(self.ctx, name, arguments, op_id)
                if isinstance(prepared, Decision):
                    return self._refused_repeat(prepared, op_id, key)
            return self._blocked(Code.NEEDS_APPROVAL, text=outcome.explanation)
        finally:
            # A call still waiting keeps its operation, so a repeat of it reuses the id.
            status = self.latch.operation_status(self.settings.org, op_id)
            if status is None or status.state not in OPEN:
                self._open_ops.pop(key, None)
                self._call_meta.pop(op_id, None)

    # Input rounds

    @staticmethod
    def round_key(name: str, arguments: Any) -> str:
        """The tool and its own arguments, without the input round, as canonical JSON."""
        if isinstance(arguments, dict):
            arguments = {key: value for key, value in arguments.items()
                         if key not in RESERVED.values()}
        return wire.canonical([name, arguments])

    def asked_for(self, key: str, answers: Any, state: Any) -> bool:
        """True when the server asked for this round: same state, and only keys it asked."""
        asked = self._asked.get(key)
        if asked is None or asked.state != state:
            return False
        return isinstance(answers, dict | None) and set(answers or {}) <= asked.keys

    def remember_request(self, key: str, value: dict[str, Any]) -> None:
        """Keep what an input_required result asks for. It replaces any earlier one."""
        self._asked.pop(key, None)
        state, requests = value.get("requestState"), value.get("inputRequests")
        if not isinstance(state, str | None) or not isinstance(requests, dict):
            logger.warning("the server asked for input in a shape the proxy does not know, "
                           "so no answer to it will pass")
            return
        self._asked[key] = Asked(state=state, keys=frozenset(requests))
        while len(self._asked) > INPUT_RECORDS:
            self._asked.popitem(last=False)

    def _refuse_round(self, name: str) -> tuple[str, Any]:
        """Refuse an input round the server did not ask for, before prepare."""
        logger.warning("refused a tools/call that carries input the server did not ask for")
        self.latch._write_audit(
            "prepare", str(Code.INPUT_NOT_REQUESTED), self.settings.org, run_id=self.run_id,
            operation_id=str(uuid.uuid4()), user_id=self.settings.user, tool=name,
            now=self.latch.clock(),
        )
        return self._blocked(Code.INPUT_NOT_REQUESTED)

    def _refused_repeat(self, decision: Decision, op_id: str, key: str) -> tuple[str, Any]:
        """Answer a refused prepare. An operation still open for it can never run now.

        In deny mode a repeat of a waiting call can come back FLOW_BLOCKED. The
        operation is then cancelled and taken off the page, and a call waiting on it
        wakes up.
        """
        waiting = self.prepared.pop(op_id, None)
        status = self.latch.operation_status(self.settings.org, op_id)
        if waiting is not None and status is not None and status.state in OPEN:
            self.latch.cancel(waiting, self.ctx)
        if self._open_ops.get(key) == op_id:
            self._open_ops.pop(key, None)
        self._wake(op_id)
        return self._blocked(decision.code, text=decision.explanation)

    @staticmethod
    def _needs_flow_approval(outcome: ExecutionResult) -> bool:
        return outcome.outcome == "denied" and outcome.code == Code.NEEDS_APPROVAL

    async def _await_approval(self, call: Call, prepared: PreparedAction, key: str
                              ) -> tuple[str, Any] | None:
        """Wait for a person. None when the call is approved and may run."""
        op_id = prepared.operation_id
        if not self.settings.page_on:
            self.latch.cancel(prepared, self.ctx)
            extra = "The approval page is off."
            if prepared.flow_reason:
                extra += f" {prepared.flow_reason}"
            return self._blocked(Code.NEEDS_APPROVAL, extra)
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
            return self._blocked(status.code if status else Code.OPERATION_CONFLICT)
        return None

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

    async def execute(self, prepared: PreparedAction
                      ) -> tuple[ExecutionResult, dict[str, Any] | None]:
        """Run the operation once. Calls that share an operation id share the answer."""
        op_id = prepared.operation_id
        task = self._running.get(op_id)
        if task is None:
            task = self._spawn(self._execute(prepared))
            self._running[op_id] = task
            task.add_done_callback(lambda _: self._running.pop(op_id, None))
        return await asyncio.shield(task)

    async def _execute(self, prepared: PreparedAction
                       ) -> tuple[ExecutionResult, dict[str, Any] | None]:
        op_id = prepared.operation_id
        try:
            outcome = await self.latch.aexecute(prepared, self.ctx)
        finally:
            self.prepared.pop(op_id, None)
        return outcome, self._replies.pop(op_id, None)

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
        return self._blocked(outcome.code, text=outcome.explanation)

    async def handler(self, args: dict[str, Any], info: CallInfo) -> Any:
        """The Latch handler. It is the only place a tools/call is sent to the server."""
        params: dict[str, Any] = {"name": info.tool}
        # Latch checked the input round under reserved names. The server gets it back
        # where MCP puts it, never inside arguments.
        arguments = dict(args)
        for param, reserved in RESERVED.items():
            if reserved in arguments:
                params[param] = arguments.pop(reserved)
        params["arguments"] = arguments
        key = self.round_key(info.tool, arguments)
        if any(param in params for param in RESERVED):
            # Checked again here, since another copy of the round may have used it up.
            if not self.asked_for(key, params.get("inputResponses"), params.get("requestState")):
                self._replies[info.operation_id] = {"result": wire.blocked(
                    str(Code.INPUT_NOT_REQUESTED), explain(Code.INPUT_NOT_REQUESTED))}
                raise ToolFailed("the server no longer asks for this input")
            self._asked.pop(key, None)
        child_id = self._new_id()
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._to_child[child_id] = ("proxy", future)
        self._in_flight[info.operation_id] = child_id
        meta = self._call_meta.get(info.operation_id)
        if meta is not None:
            params["_meta"] = meta
        try:
            self.to_child({"jsonrpc": "2.0", "id": child_id, "method": "tools/call",
                           "params": params})
            reply = await future
        except asyncio.CancelledError:
            with contextlib.suppress(ChildGone):
                self.to_child({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": self.with_protocol(
                                   {"requestId": child_id, "reason": "timed out"})})
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
        if value.get("resultType") == "input_required":
            self.remember_request(key, value)
        return value

    # Approval page actions, run on the event loop

    def page_state(self) -> dict[str, Any]:
        now = time.time()
        pending = []
        for status in self.latch.pending(self.settings.org, self.run_id):
            prepared = self.prepared.get(status.operation_id)
            if prepared is None:
                continue
            arguments = prepared.arguments
            item = {
                "operation_id": status.operation_id, "tool": status.tool,
                "summary": prepared.summary, "arguments": arguments,
                "flow_reason": prepared.flow_reason, "digest": self.form_digest(prepared),
                "waited": max(0, int(now - status.created_at)),
            }
            # The input round is shown under its own headings, not among the arguments.
            for param, reserved in RESERVED.items():
                if reserved in arguments:
                    item[PAGE_KEYS[param]] = arguments.pop(reserved)
            pending.append(item)
        changed = [{"tool": name, "hash": item.hash, "old": item.old, "new": item.new}
                   for name, item in self.changed.items()]
        return {"pending": pending, "changed": changed}

    @staticmethod
    def form_digest(prepared: PreparedAction) -> str:
        """What the person saw: the request hash, the summary and the flow reason."""
        body = wire.canonical([prepared.request_hash, prepared.summary, prepared.flow_reason])
        return hashlib.sha256(body.encode()).hexdigest()

    def decide(self, op_id: str, approve: bool, digest: str = "") -> str:
        """Approve or deny the operation, only if the page showed what is current now.

        The digest comes from the form. It must match the operation as it would be
        approved, so a page drawn before the flow reason changed cannot approve it.
        """
        prepared = self.prepared.get(op_id)
        if prepared is None:
            return "UNKNOWN_OPERATION"
        status = self.latch.operation_status(self.settings.org, op_id)
        if (status is None or status.flow_reason != prepared.flow_reason
                or not hmac.compare_digest(digest.encode(),
                                           self.form_digest(prepared).encode())):
            logger.warning("refused a decision from an out of date approval page")
            return STALE_PAGE
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
            if not await self._child_exited(child_reader):
                return await self._stop_after_reader()
            self.child_gone()
            await self._settle()
            code = await self.child.wait()
            return code if code >= 0 else 128 - code
        child_reader.cancel()
        await self.stop_child()
        for task in list(self._tasks):
            task.cancel()
        return 0

    async def _child_exited(self, reader: asyncio.Task) -> bool:
        """After the reader ended: True when the server exited, as a crash or a close does.

        The reader may also have ended with an error, or the server may have closed its
        output and still run. Then nothing more can come back from it.
        """
        error = None if reader.cancelled() else reader.exception()
        if error is not None:
            logger.error("stopped reading the server after an error in the proxy: %s",
                         type(error).__name__)
            return False
        try:
            await asyncio.wait_for(self.child.wait(), EOF_GRACE)
        except TimeoutError:
            logger.error("the server closed its output but still runs, so the proxy stops it")
            return False
        return True

    async def _stop_after_reader(self) -> int:
        """Answer every call in flight with PROXY_ERROR, stop the server, and exit 1."""
        for call in list(self._calls.values()):
            if not call.cancelled:
                call.cancelled = True
                self.to_client(wire.result(call.msg_id, wire.text_result(PROXY_ERROR)))
        self.child_gone()
        await self.stop_child()
        for task in list(self._tasks):
            task.cancel()
        return 1

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
            try:
                self.on_child_line(line)
            except Exception as exc:  # noqa: BLE001 one bad line must never stop the proxy
                logger.warning("dropped a line from the server after an error in the proxy: %s",
                               type(exc).__name__)
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
