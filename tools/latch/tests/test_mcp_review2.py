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


# Input rounds must be ones the server asked for

ANSWER = {"confirm": {"action": "accept", "content": {"ok": True}}}


@pytest.fixture
def rounds(tmp_path):
    started = Proxy(tmp_path, "--approval-port", "-1", "--db", str(tmp_path / "latch.db"),
                    modern=True)
    yield started
    started.close()


def round_call(proxy, tool, args, answers=None, state=None):
    params = {"name": tool, "arguments": args}
    if answers is not None:
        params["inputResponses"] = answers
    if state is not None:
        params["requestState"] = state
    return proxy.request("tools/call", params)


def rounds_sent(proxy):
    return [m["params"] for m in proxy.server_calls() if "inputResponses" in m["params"]
            or "requestState" in m["params"]]


def audit_rows(tmp_path):
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "boundary_latch", "audit",
                          str(tmp_path / "latch.db"), "--json"],
                         capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines()]


def first_round(proxy, topic="t"):
    return proxy.call("ask_input", {"topic": topic})["result"]


@pytest.mark.parametrize(("tool", "args"), [("echo", {"text": "x"}),
                                            ("ask_input", {"topic": "t"})])
def test_a_made_up_round_is_refused_before_prepare(rounds, tmp_path, tool, args):
    from .test_mcp_proxy import blocked_code

    reply = round_call(rounds, tool, args, ANSWER, "made-up")
    assert blocked_code(reply) == "INPUT_NOT_REQUESTED"
    assert "This call carries an answer the server did not ask for." in \
        reply["result"]["content"][0]["text"]
    assert rounds_sent(rounds) == []
    rounds.close()
    rows = [row for row in audit_rows(tmp_path) if row["tool"] == tool]
    assert [(row["event"], row["code"]) for row in rows] == [("prepare", "INPUT_NOT_REQUESTED")]


@pytest.mark.parametrize("change", ["state", "key", "args", "no_state"])
def test_a_round_that_does_not_match_what_the_server_asked_is_refused(rounds, change):
    from .test_mcp_proxy import blocked_code

    state = first_round(rounds)["requestState"]
    args, answers = {"topic": "t"}, dict(ANSWER)
    if change == "state":
        state = state + "x"
    elif change == "key":
        answers["other"] = {"action": "accept"}
    elif change == "args":
        args = {"topic": "u"}
    else:
        state = None
    assert blocked_code(round_call(rounds, "ask_input", args, answers, state)) == \
        "INPUT_NOT_REQUESTED"
    assert rounds_sent(rounds) == []


def test_a_matching_round_is_used_up_once_it_is_sent(rounds):
    from .test_mcp_proxy import blocked_code

    state = first_round(rounds)["requestState"]
    done = round_call(rounds, "ask_input", {"topic": "t"}, ANSWER, state)
    assert done["result"]["resultType"] == "complete"
    again = round_call(rounds, "ask_input", {"topic": "t"}, ANSWER, state)
    assert blocked_code(again) == "INPUT_NOT_REQUESTED"
    assert len(rounds_sent(rounds)) == 1


def test_a_round_may_answer_some_of_the_requests_or_none(rounds):
    state = first_round(rounds)["requestState"]
    reply = round_call(rounds, "ask_input", {"topic": "t"}, {}, state)
    assert reply["result"]["resultType"] == "complete"
    assert len(rounds_sent(rounds)) == 1


def test_a_round_with_no_state_matches_a_request_with_no_state(rounds):
    from .test_mcp_proxy import blocked_code

    first = first_round(rounds, "nostate")
    assert "requestState" not in first
    assert blocked_code(round_call(rounds, "ask_input", {"topic": "nostate"}, ANSWER,
                                   "s")) == "INPUT_NOT_REQUESTED"
    done = round_call(rounds, "ask_input", {"topic": "nostate"}, ANSWER)
    assert done["result"]["resultType"] == "complete"


def test_a_new_request_replaces_the_old_one(rounds):
    from .test_mcp_proxy import blocked_code

    old = first_round(rounds)["requestState"]
    new = first_round(rounds)["requestState"]
    assert old != new
    assert blocked_code(round_call(rounds, "ask_input", {"topic": "t"}, ANSWER, old)) == \
        "INPUT_NOT_REQUESTED"
    done = round_call(rounds, "ask_input", {"topic": "t"}, ANSWER, new)
    assert done["result"]["resultType"] == "complete"


def test_two_copies_of_one_round_send_it_once(rounds):
    state = first_round(rounds)["requestState"]
    params = {"name": "ask_input", "arguments": {"topic": "t"}, "inputResponses": ANSWER,
              "requestState": state}
    for msg_id in (71, 72):
        rounds.send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params})
    replies = [rounds.recv_id(71), rounds.recv_id(72)]
    assert len(rounds_sent(rounds)) == 1
    texts = [r["result"]["content"][0]["text"] for r in replies]
    assert sum("INPUT_NOT_REQUESTED" in text for text in texts) == 1


def test_a_round_that_waits_for_a_person_can_be_repeated_until_it_runs(tmp_path):
    from .test_mcp_proto import INPUT_POLICY
    from .test_mcp_proxy import pending_ids, post

    proxy = Proxy(tmp_path, "--approval-wait", "0", policy=INPUT_POLICY, modern=True)
    try:
        assert "waits for a person" in proxy.call("ask_input", {"topic": "t"})["result"][
            "content"][0]["text"]
        [first] = pending_ids(proxy)
        post(proxy, {"action": "approve", "operation_id": first})
        state = proxy.call("ask_input", {"topic": "t"})["result"]["requestState"]
        for _ in range(2):
            text = round_call(proxy, "ask_input", {"topic": "t"}, ANSWER, state)["result"][
                "content"][0]["text"]
            assert "waits for a person" in text
        [op] = pending_ids(proxy)
        assert op in text
        post(proxy, {"action": "approve", "operation_id": op})
        done = round_call(proxy, "ask_input", {"topic": "t"}, ANSWER, state)
        assert done["result"]["resultType"] == "complete"
        assert len(rounds_sent(proxy)) == 1
    finally:
        proxy.close()


@pytest.mark.parametrize("value", [
    {"requestState": 5, "inputRequests": {"a": {}}},
    {"requestState": "s", "inputRequests": ["a"]},
    {"requestState": "s"},
])
def test_an_odd_input_request_is_not_kept_and_drops_the_old_one(value):
    proxy = unit_proxy()
    key = proxy.round_key("echo", {"text": "x"})
    proxy.remember_request(key, {"requestState": "s", "inputRequests": {"a": {}}})
    assert proxy.asked_for(key, {"a": {}}, "s")
    proxy.remember_request(key, value)
    assert not proxy.asked_for(key, {"a": {}}, "s")
    assert not proxy.asked_for(key, {}, 5)


def test_the_input_request_table_drops_the_oldest(monkeypatch):
    from boundary_latch.mcp import proxy as module

    monkeypatch.setattr(module, "INPUT_RECORDS", 3)
    proxy = unit_proxy()
    keys = [proxy.round_key("echo", {"text": str(n)}) for n in range(4)]
    for key in keys:
        proxy.remember_request(key, {"inputRequests": {"a": {}}})
    assert not proxy.asked_for(keys[0], {"a": {}}, None)
    assert all(proxy.asked_for(key, {"a": {}}, None) for key in keys[1:])


def test_the_round_key_leaves_out_the_input_round():
    proxy = unit_proxy()
    assert proxy.round_key("t", {"a": 1, "$requestState": "s", "$inputResponses": {}}) == \
        proxy.round_key("t", {"a": 1})


# Messages from the child never stop the proxy

SUB = "io.modelcontextprotocol/subscriptionId"


@pytest.mark.parametrize("proto", [False, True])
def test_odd_subscription_ids_from_the_server_are_dropped(tmp_path, proto):
    proxy = Proxy(tmp_path, "--approval-port", "-1", modern=proto)
    try:
        lines = [json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                             "params": {"_meta": {SUB: odd}}})
                 for odd in ([1], {"a": 1}, 1.0, "1", True)]
        lines.append(json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                 "params": {"requestId": [1]}}))
        assert "result" in proxy.request("test/emit", {"lines": lines})
        assert "result" in proxy.request("ping", {})
        assert proxy.call("echo", {"text": "after"})["result"].get("isError") is not True
        seen = [m for m in list(proxy.lines.queue) if m.get("method") == "notifications/message"]
        assert seen == []
    finally:
        assert proxy.close() == 0


def test_the_server_closing_its_output_while_alive_stops_it_and_the_proxy(tmp_path):
    import os
    import time

    proxy = Proxy(tmp_path, "--approval-port", "-1")
    proxy.send({"jsonrpc": "2.0", "id": 61, "method": "tools/call",
                "params": {"name": "sleep", "arguments": {"seconds": 30}}})
    wait_for(lambda: proxy.server_calls())
    proxy.send({"jsonrpc": "2.0", "id": 62, "method": "test/close", "params": {}})
    reply = proxy.recv_id(61)
    assert reply["result"]["content"][0]["text"].startswith("Blocked by Latch: PROXY_ERROR.")
    assert proxy.proc.wait(20) != 0
    pid = wait_for(lambda: [m["pid"] for m in proxy.server_log() if "pid" in m])[0]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the server is still running")
    proxy.close()


def test_an_error_on_one_server_line_drops_that_line_only():
    import asyncio

    proxy = unit_proxy()
    seen = []

    def handle(line):
        if b"bad" in line:
            raise TypeError("\x1b[31m from the server")
        seen.append(line)

    proxy.on_child_line = handle

    class Child:
        stdout = None
        stdin = None

    async def main():
        reader = asyncio.StreamReader()
        for line in (b"bad\n", b"good\n"):
            reader.feed_data(line)
        reader.feed_eof()
        proxy.child = Child()
        proxy.child.stdout = reader
        await proxy._read_child()

    asyncio.run(main())
    assert seen == [b"good\n"]


def test_a_bool_response_id_never_answers_a_proxy_request():
    import asyncio

    proxy = unit_proxy()

    async def main():
        future = asyncio.get_running_loop().create_future()
        proxy._to_child[1] = ("proxy", future)
        proxy._listens[1] = "client"
        proxy.on_child_line(b'{"jsonrpc":"2.0","id":true,"result":{}}')
        proxy.on_child_line(b'{"jsonrpc":"2.0","method":"n","params":{"_meta":{"' +
                            SUB.encode() + b'":true}}}')
        return future.done()

    assert asyncio.run(main()) is False
