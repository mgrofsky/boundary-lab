"""The proxy after the third review: the tool list and a cancel, checked again before a call runs."""

import asyncio
import json

from boundary_latch.engine import Latch
from boundary_latch.mcp.pins import Pins
from boundary_latch.mcp.proxy import Proxy as InProcess
from boundary_latch.mcp.proxy import Settings
from boundary_latch.policy import parse_policy
from boundary_latch.store import SQLiteStore

from .mcp_helpers import POLICY, Proxy, wait_for
from .test_mcp_proxy import blocked_code, calls_to, changed_hash, pending_ids, post, start_call

ECHO_POLICY = '[tools.echo]\nrequire_permission = "mcp:use"\n'
ECHO_GUARDED = POLICY.replace(
    ECHO_POLICY, ECHO_POLICY + 'approve_permission = "mcp:approve"\napproval.mode = "always"\n')


# End to end: the server changes a tool while a call to it waits for approval


def test_a_tool_that_changes_while_its_call_waits_does_not_run_when_approved(tmp_path):
    assert ECHO_GUARDED != POLICY
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=ECHO_GUARDED)
    try:
        start_call(proxy, "echo", {"text": "hi"}, 7)
        [op] = wait_for(lambda: pending_ids(proxy))
        assert "result" in proxy.call("mutate", {})
        wait_for(lambda: [m for m in list(proxy.lines.queue)
                          if m.get("method") == "notifications/tools/list_changed"])
        assert post(proxy, {"action": "approve", "operation_id": op})[0] == 303
        assert blocked_code(proxy.recv_id(7)) == "TOOL_CHANGED"
        assert calls_to(proxy, "echo") == []
        # The approval was for the pinned definition. It does not carry over to the new one.
        shown = wait_for(lambda: changed_hash(proxy))
        assert post(proxy, {"action": "accept", "tool": "echo", "hash": shown})[2] == "ACCEPTED"
        assert blocked_code(proxy.call("echo", {"text": "hi"})) == "OPERATION_CONFLICT"
        start_call(proxy, "echo", {"text": "hi"}, 8)
        [fresh] = wait_for(lambda: pending_ids(proxy))
        assert fresh != op
        assert calls_to(proxy, "echo") == []
    finally:
        proxy.close()


# In process, with a stand in for the server that answers tools/list and tools/call

UNIT_POLICY = """
[tools.echo]
require_permission = "use"

[tools.guarded]
require_permission = "use"
approve_permission = "approve"
approval.mode = "always"
"""

PLAIN = {"type": "object", "properties": {}}
ECHO = {"name": "echo", "description": "Echo.", "inputSchema": PLAIN}
GUARDED = {"name": "guarded", "description": "Needs approval.", "inputSchema": PLAIN}
LIST_CHANGED = b'{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}'


class Server:
    """Stands in for the server's stdin. It answers on the next turn of the loop.

    during_list holds one step for each tools/list, run while the proxy waits for
    that answer.
    """

    def __init__(self):
        self.tools = [ECHO, GUARDED]
        self.sent = []
        self.during_list = []
        self.proxy = None

    def write(self, data):
        message = json.loads(data)
        self.sent.append(message)
        loop = asyncio.get_running_loop()
        if message.get("method") == "tools/list":
            if self.during_list:
                loop.call_soon(self.during_list.pop(0))
            result = {"tools": list(self.tools)}
        elif message.get("method") == "tools/call":
            result = {"content": [{"type": "text", "text": "ran"}]}
        else:
            return
        answer = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        loop.call_soon(self.proxy.on_child_line, json.dumps(answer).encode())

    def is_closing(self):
        return False

    def methods(self):
        return [message.get("method") for message in self.sent]


class Child:
    def __init__(self, stdin):
        self.stdin = stdin


def make_proxy():
    latch = Latch(parse_policy(UNIT_POLICY), SQLiteStore())
    settings = Settings(permissions=frozenset({"use"}), approver_permissions=frozenset({"approve"}),
                        approval_wait=5.0, open_browser=False)
    out = []
    proxy = InProcess(latch, settings, Pins(), lambda data: out.append(json.loads(data)))
    server = Server()
    server.proxy = proxy
    proxy.child = Child(server)
    return proxy, server, out


def call(proxy, tool, msg_id=1):
    proxy.on_client({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                     "params": {"name": tool, "arguments": {}}})


def cancel(proxy, msg_id=1):
    proxy.on_client({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": msg_id}})


async def settle(proxy):
    """Wait until every task the proxy started is done."""
    while proxy._tasks:
        await asyncio.wait_for(asyncio.gather(*proxy._tasks), 10)


async def waiting(proxy):
    """The one call on the approval page, once it is there."""
    for _ in range(500):
        items = proxy.page_state()["pending"]
        if items:
            return items[0]
        await asyncio.sleep(0.01)
    raise AssertionError("no call waits for approval")


def audit(proxy):
    return [(row["event"], row["code"]) for row in proxy.latch.store.audit_rows("local")]


def state_of(proxy, op_id):
    return proxy.latch.operation_status("local", op_id).state


def blocked(out, msg_id=1):
    [reply] = [message for message in out if message.get("id") == msg_id]
    return blocked_code(reply)


def test_a_tool_that_leaves_the_list_while_its_call_waits_does_not_run_when_approved():
    proxy, server, out = make_proxy()

    async def scenario():
        call(proxy, "guarded")
        item = await waiting(proxy)
        server.tools = [ECHO]
        proxy.on_child_line(LIST_CHANGED)
        assert proxy.decide(item["operation_id"], True, item["digest"]) == "APPROVED"
        await settle(proxy)

    asyncio.run(scenario())
    assert server.methods() == ["tools/list", "tools/list"]
    assert blocked(out) == "UNKNOWN_TOOL"


def test_a_tool_that_stays_the_same_still_runs_after_a_list_change_during_the_wait():
    proxy, server, out = make_proxy()

    async def scenario():
        call(proxy, "guarded")
        item = await waiting(proxy)
        proxy.on_child_line(LIST_CHANGED)
        assert proxy.decide(item["operation_id"], True, item["digest"]) == "APPROVED"
        await settle(proxy)

    asyncio.run(scenario())
    assert server.methods() == ["tools/list", "tools/list", "tools/call"]
    [reply] = [message for message in out if message.get("id") == 1]
    assert reply["result"]["content"][0]["text"] == "ran"
