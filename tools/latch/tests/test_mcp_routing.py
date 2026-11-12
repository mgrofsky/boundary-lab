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


@pytest.mark.parametrize("message", [
    {"jsonrpc": "2.0", "id": 1, "method": "roots/list", "result": {}},
    {"jsonrpc": "2.0", "method": "notifications/x", "error": {"code": 1, "message": "m"}},
    {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": "m"}},
    {"jsonrpc": "1.0", "method": "notifications/x"},
    {"method": "notifications/x"},
    {"jsonrpc": "2.0", "id": 1},
    {"jsonrpc": "2.0", "id": None, "method": "roots/list"},
    {"jsonrpc": "2.0", "id": True, "result": {}},
])
def test_server_messages_that_are_not_valid_json_rpc_are_dropped(proxy, message, caplog):
    proxy._to_child[1] = ("client", "c1")
    with caplog.at_level("WARNING", logger="boundary_latch.mcp"):
        proxy.on_child_line(line(message))
    assert proxy.out == []
    assert "not a JSON-RPC message" in caplog.text


def test_a_server_request_still_reaches_the_client(proxy):
    asked = {"jsonrpc": "2.0", "id": "s1", "method": "roots/list", "params": {}}
    proxy.on_child_line(line(asked))
    assert proxy.out == [asked]


META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}}
SUB = "io.modelcontextprotocol/subscriptionId"


def listen(proxy, client_id):
    proxy.on_client({"jsonrpc": "2.0", "id": client_id, "method": "subscriptions/listen",
                     "params": {"_meta": META, "notifications": {"toolsListChanged": True}}})
    return proxy.child.stdin.lines[-1]["id"]


def test_listen_passes_through_and_its_notifications_name_the_client_id(proxy):
    child_id = listen(proxy, "listen:0")
    sent = proxy.child.stdin.lines[-1]
    assert sent["method"] == "subscriptions/listen" and sent["params"]["_meta"] == META
    proxy.on_child_line(line({"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
                              "params": {"notifications": {"toolsListChanged": True},
                                         "_meta": {SUB: child_id}}}))
    proxy._tools_known = True
    proxy.on_child_line(line({"jsonrpc": "2.0", "method": "notifications/tools/list_changed",
                              "params": {"_meta": {SUB: child_id}}}))
    assert proxy._tools_known is False
    assert [m["params"]["_meta"][SUB] for m in proxy.out] == ["listen:0", "listen:0"]


def test_a_cancelled_listen_drops_its_mapping_and_later_notifications(proxy):
    child_id = listen(proxy, 5)
    proxy.on_client({"jsonrpc": "2.0", "method": "notifications/cancelled",
                     "params": {"requestId": 5}})
    assert proxy.child.stdin.lines[-1]["params"]["requestId"] == child_id
    assert proxy._to_child == {} and proxy._listens == {}
    proxy._tools_known = True
    proxy.on_child_line(line({"jsonrpc": "2.0", "method": "notifications/tools/list_changed",
                              "params": {"_meta": {SUB: child_id}}}))
    proxy.on_child_line(line({"jsonrpc": "2.0", "id": child_id, "result": {}}))
    assert proxy.out == []
    # A list change still makes the proxy forget its list, whatever _meta it carries.
    assert proxy._tools_known is False


def test_a_listen_the_server_ends_names_the_client_id(proxy):
    first = listen(proxy, "a")
    second = listen(proxy, "b")
    proxy.on_child_line(line({"jsonrpc": "2.0", "id": first, "result": {
        "resultType": "complete", "_meta": {SUB: first}}}))
    proxy.on_child_line(line({"jsonrpc": "2.0", "method": "notifications/cancelled",
                              "params": {"requestId": second}}))
    assert proxy.out == [
        {"jsonrpc": "2.0", "id": "a", "result": {"resultType": "complete", "_meta": {SUB: "a"}}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "b"}},
    ]
    assert proxy._listens == {} and proxy._to_child == {}


def test_tasks_update_is_refused_and_tasks_get_and_cancel_pass_through(proxy, caplog):
    with caplog.at_level("WARNING", logger="boundary_latch.mcp"):
        proxy.on_client({"jsonrpc": "2.0", "id": 9, "method": "tasks/update",
                         "params": {"taskId": "t", "inputResponses": {}}})
    assert proxy.out == [{"jsonrpc": "2.0", "id": 9, "error": {
        "code": -32601,
        "message": "Blocked by Latch: input to a running task is not checked yet"}}]
    assert "tasks/update" in caplog.text
    proxy.on_client({"jsonrpc": "2.0", "id": 10, "method": "tasks/get", "params": {"taskId": "t"}})
    proxy.on_client({"jsonrpc": "2.0", "id": 11, "method": "tasks/cancel",
                     "params": {"taskId": "t"}})
    assert [m["method"] for m in proxy.child.stdin.lines] == ["tasks/get", "tasks/cancel"]


def test_the_proxy_keeps_only_the_protocol_fields_of_the_latest_client_request(proxy):
    assert proxy.with_protocol({"cursor": "1"}) == {"cursor": "1"}
    proxy.on_client({"jsonrpc": "2.0", "id": 1, "method": "resources/list",
                     "params": {"_meta": {**META, "progressToken": "p", "other/key": 1}}})
    proxy.on_client({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
    assert proxy.with_protocol({}) == {"_meta": META}
