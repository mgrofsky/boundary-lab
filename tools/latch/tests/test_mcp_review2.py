"""The proxy after the second review: input rounds, _meta, tasks, and messages from the child."""

import json

import pytest

from .mcp_helpers import Proxy, wait_for

TASK_TEXT = "Blocked by Latch: input to a running task is not checked yet"


@pytest.fixture
def modern(tmp_path):
    started = Proxy(tmp_path, "--approval-port", "-1", modern=True)
    yield started
    started.close()


def sent_methods(proxy):
    return [m.get("method") for m in proxy.server_log()]


# Tasks


def test_a_tasks_update_with_no_id_is_dropped_not_forwarded(modern):
    modern.send({"jsonrpc": "2.0", "method": "tasks/update",
                 "params": {"taskId": "t", "inputResponses": {"a": {}}}})
    modern.request("ping", {})
    assert "tasks/update" not in sent_methods(modern)
    assert "dropped" in modern.stderr() and "tasks/update" in modern.stderr()


@pytest.mark.parametrize("method", ["tasks/update", "tasks/result", "tasks/anything"])
def test_tasks_methods_other_than_get_and_cancel_are_refused(modern, method):
    reply = modern.request(method, {"taskId": "t"})
    assert reply["error"] == {"code": -32601, "message": TASK_TEXT}
    modern.send({"jsonrpc": "2.0", "method": method, "params": {"taskId": "t"}})
    modern.request("ping", {})
    assert method not in sent_methods(modern)


@pytest.mark.parametrize("method", ["tasks/get", "tasks/cancel"])
@pytest.mark.parametrize("field", [{"inputResponses": {"a": {}}}, {"requestState": "s"}])
def test_get_and_cancel_with_an_input_round_are_refused(modern, method, field):
    reply = modern.request(method, {"taskId": "t", **field})
    assert reply["error"] == {"code": -32601, "message": TASK_TEXT}
    assert method not in sent_methods(modern)


def test_tasks_get_and_cancel_pass_through(modern):
    reply = modern.request("tasks/get", {"taskId": "t"})
    assert reply["result"]["status"] == "completed"
    modern.request("tasks/cancel", {"taskId": "t"})
    wait_for(lambda: "tasks/cancel" in sent_methods(modern))


def test_a_refused_tasks_request_does_not_set_the_protocol_fields(modern):
    odd = {"io.modelcontextprotocol/protocolVersion": "9999-01-01",
           "io.modelcontextprotocol/clientCapabilities": {}}
    modern.request("tasks/update", {"taskId": "t", "_meta": odd})
    # A list request with an empty _meta leaves the kept fields as they are.
    modern.request("tools/list", {"_meta": {}})
    [listed, *_] = [m for m in modern.server_log() if m.get("method") == "tools/list"]
    assert listed["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert json.dumps(listed).count("9999") == 0


# The protocol fields the proxy keeps

VERSION = "io.modelcontextprotocol/protocolVersion"
INFO = "io.modelcontextprotocol/clientInfo"
CAPS = "io.modelcontextprotocol/clientCapabilities"
GOOD = {VERSION: "2026-07-28", INFO: {"name": "c"}, CAPS: {}}


def unit_proxy():
    from boundary_latch.mcp.pins import Pins

    from .test_mcp_units import make_proxy

    return make_proxy(Pins())


@pytest.mark.parametrize("meta", [
    {VERSION: "x" * 33, CAPS: {}},
    {VERSION: "2026 07 28", CAPS: {}},
    {VERSION: "2026-07-28\x1b[0m", CAPS: {}},
    {VERSION: "2026_07_28", CAPS: {}},
    {VERSION: "", CAPS: {}},
    {VERSION: 20260728, CAPS: {}},
    {CAPS: {}, INFO: {}},
    {VERSION: "2026-07-28", INFO: "c"},
    {VERSION: "2026-07-28", CAPS: []},
])
def test_odd_protocol_fields_are_not_kept(meta):
    proxy = unit_proxy()
    proxy.keep_protocol({"_meta": GOOD})
    proxy.keep_protocol({"_meta": meta})
    assert proxy._protocol == GOOD


def test_the_kept_fields_are_replaced_as_a_whole():
    proxy = unit_proxy()
    proxy.keep_protocol({"_meta": GOOD})
    proxy.keep_protocol({"_meta": {VERSION: "v2.1", "other": 1}})
    assert proxy._protocol == {VERSION: "v2.1"}
    assert proxy.with_protocol({"a": 1}) == {"a": 1, "_meta": {VERSION: "v2.1"}}


def test_a_call_refused_for_its_params_does_not_set_the_protocol_fields(modern):
    odd = {VERSION: "1999-01-01", CAPS: {}}
    reply = modern.request("tools/call", {"name": "echo", "arguments": {"text": "x"},
                                          "other": 1, "_meta": odd})
    assert reply["error"]["code"] == -32602
    modern.request("tools/list", {"_meta": {}})
    listed = [m for m in modern.server_log() if m.get("method") == "tools/list"]
    assert listed and all(m["params"]["_meta"][VERSION] == "2026-07-28" for m in listed)


# _meta on a tools/call

ALLOWED_META = {
    **GOOD, "io.modelcontextprotocol/logLevel": "info", "progressToken": "p",
    "traceparent": "00-abc-def-01", "tracestate": "a=b", "baggage": "k=v",
}


def test_only_the_listed_meta_keys_reach_the_server(modern):
    meta = {**ALLOWED_META, "vendor/actAs": "admin", "other": {"x": 1}}
    for text in ("a", "b"):
        modern.request("tools/call", {"name": "echo", "arguments": {"text": text},
                                      "_meta": meta})
    sent = [m["params"]["_meta"] for m in modern.server_calls()]
    assert sent == [ALLOWED_META, ALLOWED_META]
    err = modern.stderr()
    assert err.count("'vendor/actAs'") == 1 and err.count("'other'") == 1
    assert "admin" not in err


def test_a_long_or_odd_meta_key_is_logged_safely(modern):
    key = "\x1b[31m" + "k" * 200
    modern.request("tools/call", {"name": "echo", "arguments": {"text": "a"},
                                  "_meta": {**GOOD, key: 1}})
    assert modern.server_calls()[0]["params"]["_meta"] == GOOD
    err = modern.stderr()
    assert "\x1b" not in err
    assert repr(key)[:80] in err and "k" * 100 not in err
