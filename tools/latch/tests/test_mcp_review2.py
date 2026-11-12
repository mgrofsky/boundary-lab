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
