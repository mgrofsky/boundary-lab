"""The proxy's message routing, in process, with a stand in for the server's stdin."""

import json

import pytest

from boundary_latch.engine import Latch
from boundary_latch.mcp.pins import Pins
from boundary_latch.mcp.proxy import ChildGone, Proxy, Settings
from boundary_latch.policy import parse_policy
from boundary_latch.store import SQLiteStore


class Pipe:
    def __init__(self):
        self.lines = []

    def write(self, data):
        self.lines.append(json.loads(data))

    def is_closing(self):
        return False


class Child:
    def __init__(self):
        self.stdin = Pipe()


@pytest.fixture
def proxy():
    latch = Latch(parse_policy('[tools.echo]\nrequire_permission = "use"\n'), SQLiteStore())
    out = []
    started = Proxy(latch, Settings(permissions=frozenset({"use"})), Pins(),
                    lambda data: out.append(json.loads(data)))
    started.child = Child()
    started.out = out
    return started


def line(message):
    return json.dumps(message).encode()


def test_a_cancel_for_a_relayed_request_uses_the_server_side_id(proxy):
    proxy.on_client_line(line({"jsonrpc": "2.0", "id": "c1", "method": "resources/read",
                               "params": {"uri": "x"}}))
    sent = proxy.child.stdin.lines[0]
    assert sent["id"] != "c1"
    proxy.on_client_line(line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": "c1", "reason": "r"}}))
    assert proxy.child.stdin.lines[1] == {"jsonrpc": "2.0", "method": "notifications/cancelled",
                                          "params": {"requestId": sent["id"], "reason": "r"}}


def test_a_cancel_for_an_unknown_request_is_dropped(proxy):
    proxy.on_client_line(line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                               "params": {"requestId": 99}}))
    assert proxy.child.stdin.lines == []


def test_the_answer_goes_back_under_the_client_id(proxy):
    proxy.on_client_line(line({"jsonrpc": "2.0", "id": 5, "method": "ping"}))
    sent = proxy.child.stdin.lines[0]
    proxy.on_child_line(line({"jsonrpc": "2.0", "id": sent["id"], "result": {}}))
    assert proxy.out == [{"jsonrpc": "2.0", "id": 5, "result": {}}]


@pytest.mark.parametrize("message", [{"jsonrpc": "2.0", "id": 777, "result": {}},
                                     {"jsonrpc": "2.0", "id": "x", "result": {}}])
def test_a_server_answer_with_an_unknown_id_is_dropped(proxy, message):
    proxy.on_child_line(line(message))
    assert proxy.out == []


def test_server_junk_is_dropped(proxy):
    proxy.on_child_line(b"not json")
    proxy.on_child_line(b"[1]")
    assert proxy.out == []


def test_list_changed_forgets_the_list_and_passes_on(proxy):
    proxy._tools_known = True
    proxy.on_child_line(line({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))
    assert proxy._tools_known is False
    assert proxy.out == [{"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}]


@pytest.mark.parametrize("method", ["tools/call", "tools/list"])
def test_tool_notifications_never_reach_the_server(proxy, method):
    proxy.on_client_line(line({"jsonrpc": "2.0", "method": method, "params": {"name": "echo"}}))
    assert proxy.child.stdin.lines == []
    assert proxy.out == []


def test_a_gone_server_fails_waiting_proxy_requests(proxy):
    import asyncio

    async def go():
        task = asyncio.ensure_future(proxy.request("tools/list", {}))
        await asyncio.sleep(0)
        proxy.child_gone()
        with pytest.raises(ChildGone):
            await task

    asyncio.run(go())
