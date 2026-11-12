"""The proxy with a client and a server that speak MCP 2026-07-28."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from .mcp_helpers import META, Proxy, http, text_of, wait_for
from .test_mcp_proxy import blocked_code, calls_to, pending_ids, post

CAPTURE = Path(__file__).parents[3] / ".hermes" / "spec" / "claude_code_wire_capture.log"
POLICY_TOOLS = ["echo", "fail", "rpc_error", "sleep", "mutate", "crash", "ask_roots",
                "files.read-text", "guarded", "pay", "ask_input"]
PROTOCOL_KEYS = {"io.modelcontextprotocol/protocolVersion", "io.modelcontextprotocol/clientInfo",
                 "io.modelcontextprotocol/clientCapabilities"}
SUBSCRIPTION = "io.modelcontextprotocol/subscriptionId"


def client_lines():
    if not CAPTURE.exists():
        pytest.skip("the wire capture is not in this checkout")
    return [json.loads(line[3:]) for line in CAPTURE.read_text().splitlines()
            if line.startswith("C> ")]


@pytest.fixture
def modern(tmp_path):
    started = Proxy(tmp_path, "--approval-port", "-1", modern=True, init=False)
    yield started
    started.close()


def notifications(proxy, method):
    return [m for m in list(proxy.lines.queue) if m.get("method") == method]


def test_the_client_lines_of_the_capture_get_the_policy_tools(modern):
    lines = client_lines()
    for message in lines:
        modern.send(message)
    discover = modern.recv_id("server-discover-probe-1")
    assert discover["result"]["supportedVersions"] == ["2026-07-28"]
    for msg_id in (1, 2, 3, 4):
        result = modern.recv_id(msg_id)["result"]
        assert [tool["name"] for tool in result["tools"]] == POLICY_TOOLS
        assert result["resultType"] == "complete"
        assert result["ttlMs"] == 60000 and result["cacheScope"] == "private"
        assert result["_meta"] == {"io.modelcontextprotocol/serverInfo":
                                   {"name": "fake", "version": "1"}}
        assert "nextCursor" not in result
    assert modern.recv_id(0)["result"]["resources"] == []
    # listen:0 is cancelled at once, so its acknowledgment may come after the cancel and
    # is then dropped. Every acknowledgment the client sees names the client's own id.
    acks = wait_for(lambda: [ack for ack in notifications(
        modern, "notifications/subscriptions/acknowledged")
        if ack["params"]["_meta"][SUBSCRIPTION] == "listen:1"])
    assert acks[0]["params"]["notifications"] == lines[-2]["params"]["notifications"]
    for ack in notifications(modern, "notifications/subscriptions/acknowledged"):
        assert ack["params"]["_meta"][SUBSCRIPTION] in ("listen:0", "listen:1")
    # The proxy's own tools/list carries the client's protocol fields and nothing else.
    for sent in [m for m in modern.server_log() if m.get("method") == "tools/list"]:
        assert set(sent["params"]["_meta"]) == PROTOCOL_KEYS
        assert sent["params"]["_meta"] == lines[-1]["params"]["_meta"]


def test_every_tools_list_fetches_the_server_list_again(modern):
    modern.initialize()
    for _ in range(3):
        modern.request("tools/list", {})
    pages = [m for m in modern.server_log() if m.get("method") == "tools/list"]
    assert len(pages) == 3 * 6


def test_a_call_uses_the_list_already_fetched(modern):
    modern.initialize()
    modern.request("tools/list", {})
    before = len([m for m in modern.server_log() if m.get("method") == "tools/list"])
    modern.call("echo", {"text": "a"})
    modern.call("echo", {"text": "b"})
    after = len([m for m in modern.server_log() if m.get("method") == "tools/list"])
    assert after == before


def test_results_the_proxy_writes_say_complete(modern):
    modern.initialize()
    assert modern.request("tools/list", {})["result"]["resultType"] == "complete"
    blocked = modern.call("secret", {})
    assert blocked_code(blocked) == "UNKNOWN_TOOL"
    assert blocked["result"]["resultType"] == "complete"
    waiting = modern.call("guarded", {"note": "n"})
    assert blocked_code(waiting) == "NEEDS_APPROVAL"
    assert waiting["result"]["resultType"] == "complete"


def test_an_allowed_call_returns_the_server_result(modern):
    modern.initialize()
    reply = modern.call("echo", {"text": "hi"})
    assert reply["result"]["resultType"] == "complete"
    assert json.loads(text_of(reply)) == {"tool": "echo", "args": {"text": "hi"}}
    [sent] = calls_to(modern, "echo")
    assert sent["params"]["_meta"] == META


def test_the_proxy_requests_carry_no_protocol_fields_before_a_client_sends_them(tmp_path):
    legacy = Proxy(tmp_path)
    try:
        legacy.request("tools/list", {})
        [first, *_] = [m for m in legacy.server_log() if m.get("method") == "tools/list"]
        assert "_meta" not in first["params"]
    finally:
        legacy.close()


def test_an_input_round_is_checked_as_its_own_call_and_completes(tmp_path):
    proxy = Proxy(tmp_path, "--approval-port", "-1", "--db", str(tmp_path / "latch.db"),
                  modern=True)
    try:
        first = proxy.call("ask_input", {"topic": "t"})["result"]
        assert first["resultType"] == "input_required"
        assert set(first["inputRequests"]) == {"confirm"}
        state = first["requestState"]
        answers = {"confirm": {"action": "accept", "content": {"ok": True}}}
        second = proxy.request("tools/call", {"name": "ask_input", "arguments": {"topic": "t"},
                                              "inputResponses": answers,
                                              "requestState": state})
        assert second["result"]["resultType"] == "complete"
        assert json.loads(text_of(second)) == {
            "tool": "ask_input", "args": {"topic": "t"}, "inputResponses": answers,
            "requestState": state}
        sent = [m["params"] for m in calls_to(proxy, "ask_input")]
        assert sent[0] == {"name": "ask_input", "arguments": {"topic": "t"}, "_meta": META}
        assert sent[1] == {"name": "ask_input", "arguments": {"topic": "t"}, "_meta": META,
                           "inputResponses": answers, "requestState": state}
    finally:
        proxy.close()
    rows = [json.loads(line) for line in subprocess.run(
        [sys.executable, "-m", "boundary_latch", "audit", str(tmp_path / "latch.db"), "--json"],
        capture_output=True, text=True, check=True).stdout.splitlines()]
    executed = [row for row in rows if row["tool"] == "ask_input" and row["event"] == "execute"]
    assert len(executed) == 2
    assert len({row["operation_id"] for row in executed}) == 2
    summaries = [row["summary"] for row in rows
                 if row["tool"] == "ask_input" and row["event"] == "prepare"]
    assert summaries[0] == 'ask_input(topic="t")'
    assert '$inputResponses={"confirm"' in summaries[1]
    assert f'$requestState="{state}"' in summaries[1]


INPUT_POLICY = """
[limits]
max_actions_per_run = 1000
run_lifetime_seconds = 0

[tools.ask_input]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
approval.mode = "always"
"""


def test_the_approval_page_shows_the_input_round_under_its_own_headings(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=INPUT_POLICY, modern=True)
    try:
        proxy.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "ask_input", "arguments": {"topic": "t"},
            "inputResponses": {"confirm": {"action": "accept", "content": {"ok": "<b>x</b>"}}},
            "requestState": "<script>s</script>"}})
        [op] = wait_for(lambda: pending_ids(proxy))
        _, _, page = http(proxy.page_url())
        assert "<h3>Input responses</h3>" in page and "<h3>Request state</h3>" in page
        assert "&lt;script&gt;s&lt;/script&gt;" in page and "<script>s" not in page
        assert "&lt;b&gt;x&lt;/b&gt;" in page
        arguments = page.split("<h3>Arguments</h3>")[1].split("</pre>")[0]
        assert "$requestState" not in arguments and "topic" in arguments
        post(proxy, {"action": "approve", "operation_id": op})
        reply = proxy.recv_id(5)
        assert reply["result"]["resultType"] == "input_required"
    finally:
        proxy.close()
