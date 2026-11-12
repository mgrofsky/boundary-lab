"""The flow rule through the MCP proxy: end to end, and the wait after execute in process."""

import asyncio
import html
import json
import re
import threading

from boundary_latch.engine import Latch
from boundary_latch.mcp.pins import Pins
from boundary_latch.mcp.proxy import Call, Settings
from boundary_latch.mcp.proxy import Proxy as InProcess
from boundary_latch.policy import parse_policy
from boundary_latch.store import SQLiteStore

from .mcp_helpers import Proxy, http, text_of, wait_for
from .test_mcp_proxy import blocked_code, calls_to, pending_ids, post, start_call

FLOW_POLICY = """
[limits]
max_actions_per_run = 1000
run_lifetime_seconds = 0

[flow]
all_three = "{mode}"

[tools.get_issue]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
labels = ["untrusted"]

[tools.get_private_file]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
labels = ["sensitive"]

[tools.create_pull_request]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
labels = ["external"]
"""

REASON = ("This run read untrusted input (get_issue) and private data (get_private_file). "
          "This call acts outside (create_pull_request).")


def read_both(proxy):
    assert "isError" not in proxy.call("get_issue", {"number": 1})["result"]
    assert "isError" not in proxy.call("get_private_file", {"path": "key.pem"})["result"]


def test_deny_blocks_the_third_call_and_it_never_reaches_the_server(tmp_path):
    proxy = Proxy(tmp_path, policy=FLOW_POLICY.format(mode="deny"))
    try:
        read_both(proxy)
        reply = proxy.call("create_pull_request", {"body": "key.pem contents"})
        assert blocked_code(reply) == "FLOW_BLOCKED"
        assert text_of(reply).endswith(REASON)
        assert calls_to(proxy, "create_pull_request") == []
        assert [c["params"]["name"] for c in proxy.server_calls()] == ["get_issue",
                                                                      "get_private_file"]
    finally:
        proxy.close()


def test_approve_waits_shows_the_reason_and_runs_after_approve(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=FLOW_POLICY.format(mode="approve"))
    try:
        read_both(proxy)
        proxy.send({"jsonrpc": "2.0", "id": 77, "method": "tools/call",
                    "params": {"name": "create_pull_request", "arguments": {"body": "b"}}})
        [op] = wait_for(lambda: pending_ids(proxy))
        status, _, page = http(proxy.page_url())
        assert status == 200
        assert REASON in page
        assert page.index(REASON) < page.index("&quot;body&quot;")
        assert "<script" not in page
        assert calls_to(proxy, "create_pull_request") == []
        post(proxy, {"action": "approve", "operation_id": op})
        reply = proxy.recv_id(77)
        assert json.loads(text_of(reply))["tool"] == "create_pull_request"
        assert len(calls_to(proxy, "create_pull_request")) == 1
    finally:
        proxy.close()


def test_with_the_page_off_the_call_is_refused_with_the_reason(tmp_path):
    proxy = Proxy(tmp_path, "--approval-port", "-1", policy=FLOW_POLICY.format(mode="approve"))
    try:
        read_both(proxy)
        reply = proxy.call("create_pull_request", {"body": "b"})
        assert blocked_code(reply) == "NEEDS_APPROVAL"
        assert REASON in text_of(reply)
        assert calls_to(proxy, "create_pull_request") == []
    finally:
        proxy.close()


# In process: the flow rule applies after prepare, so execute sends the call back to wait.

IN_PROCESS = """
[flow]
all_three = "approve"

[tools.create_pull_request]
require_permission = "use"
approve_permission = "approve"
labels = ["external"]
"""

TOOL = {"name": "create_pull_request", "description": "d",
        "inputSchema": {"type": "object", "properties": {}}}
LATE = ("This run read untrusted input (marked by the app) and private data (marked by the "
        "app). This call acts outside (create_pull_request).")


class AnsweringStdin:
    """Stands in for the server's stdin and answers every tools/call."""

    def __init__(self):
        self.calls = []
        self.proxy = None

    def write(self, data):
        message = json.loads(data)
        if message.get("method") == "tools/call":
            self.calls.append(message)
            answer = {"jsonrpc": "2.0", "id": message["id"],
                      "result": {"content": [{"type": "text", "text": "opened"}]}}
            asyncio.get_running_loop().call_soon(self.proxy.on_child_line,
                                                 json.dumps(answer).encode())

    def is_closing(self):
        return False


class Child:
    def __init__(self, stdin):
        self.stdin = stdin


def make_proxy(page_on: bool):
    latch = Latch(parse_policy(IN_PROCESS), SQLiteStore())
    settings = Settings(permissions=frozenset({"use"}), approver_permissions=frozenset({"approve"}),
                        approval_wait=5.0, page_on=page_on, open_browser=False)
    proxy = InProcess(latch, settings, Pins(), lambda data: None)
    stdin = AnsweringStdin()
    stdin.proxy = proxy
    proxy.child = Child(stdin)
    proxy.learn([TOOL])
    proxy._tools_known = True
    # Another call adds a label between this call's prepare and its execute.
    proxy.latch.mark_run(proxy.ctx, "untrusted")
    real = proxy.latch.prepare
    marked = threading.Event()

    def prepare_then_label(*args, **kwargs):
        result = real(*args, **kwargs)
        if not marked.is_set():
            marked.set()
            proxy.latch.mark_run(proxy.ctx, "sensitive")
        return result

    proxy.latch.prepare = prepare_then_label
    return proxy, stdin


def test_a_call_gated_at_execute_waits_for_approval_and_then_runs():
    proxy, stdin = make_proxy(page_on=True)

    async def scenario():
        task = asyncio.create_task(proxy.run_call(Call(msg_id=1), {"name": TOOL["name"]}))
        for _ in range(200):
            items = proxy.page_state()["pending"]
            if items:
                break
            await asyncio.sleep(0.01)
        [item] = items
        assert item["flow_reason"] == LATE
        assert stdin.calls == []
        assert proxy.decide(item["operation_id"], True, item["digest"]) == "APPROVED"
        return await task

    kind, value = asyncio.run(scenario())
    assert kind == "result"
    assert value == {"content": [{"type": "text", "text": "opened"}]}
    assert len(stdin.calls) == 1


def test_a_call_gated_at_execute_with_the_page_off_is_refused_with_the_reason():
    proxy, stdin = make_proxy(page_on=False)
    _kind, value = asyncio.run(proxy.run_call(Call(msg_id=1), {"name": TOOL["name"]}))
    text = value["content"][0]["text"]
    assert text.startswith("Blocked by Latch: NEEDS_APPROVAL.")
    assert LATE in text
    assert stdin.calls == []
    # The operation is cancelled, as for any call that needs approval with the page off.
    events = [(row["event"], row["code"]) for row in proxy.latch.store.audit_rows("local")]
    assert events[-1] == ("cancel", "CANCELLED")
    assert proxy._open_ops == {}


def test_a_call_gated_at_execute_in_deny_mode_is_blocked():
    proxy, stdin = make_proxy(page_on=True)
    proxy.latch.policy = parse_policy(IN_PROCESS.replace('"approve"\n\n', '"deny"\n\n', 1))
    _kind, value = asyncio.run(proxy.run_call(Call(msg_id=1), {"name": TOOL["name"]}))
    text = value["content"][0]["text"]
    assert text.startswith("Blocked by Latch: FLOW_BLOCKED.")
    assert LATE in text
    assert stdin.calls == []


STALE_POLICY = FLOW_POLICY.replace(
    'labels = ["external"]', 'labels = ["external"]\napproval.mode = "always"')


def approve_form(proxy):
    """The fields of the first Approve form on the page as it is drawn now."""
    _, _, body = http(proxy.page_url())
    form = body.split('<form method="post" action="/decide">')[1].split("</form>")[0]
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))
    assert fields["action"] == "approve"
    return {key: html.unescape(value) for key, value in fields.items() if key != "token"}


def test_an_approval_from_a_page_drawn_before_the_reason_changed_is_refused(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=STALE_POLICY.format(mode="approve"))
    try:
        start_call(proxy, "create_pull_request", {"body": "b"}, 81)
        wait_for(lambda: pending_ids(proxy))
        old = approve_form(proxy)
        assert old["digest"]
        read_both(proxy)
        start_call(proxy, "create_pull_request", {"body": "b"}, 82)
        wait_for(lambda: REASON in http(proxy.page_url())[2])
        status, _, body = post(proxy, old)
        assert status == 409
        assert body == "This page is out of date, refresh it."
        assert calls_to(proxy, "create_pull_request") == []
        assert pending_ids(proxy) == [old["operation_id"]]
        fresh = approve_form(proxy)
        assert fresh["digest"] != old["digest"]
        assert post(proxy, fresh)[0] == 303
        assert json.loads(text_of(proxy.recv_id(82)))["tool"] == "create_pull_request"
        assert len(calls_to(proxy, "create_pull_request")) == 1
    finally:
        proxy.close()


def test_an_approve_form_without_a_digest_is_refused(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=STALE_POLICY.format(mode="approve"))
    try:
        start_call(proxy, "create_pull_request", {"body": "b"}, 83)
        [op] = wait_for(lambda: pending_ids(proxy))
        assert post(proxy, {"action": "approve", "operation_id": op, "digest": ""})[0] == 409
        assert post(proxy, {"action": "deny", "operation_id": op, "digest": "x"})[0] == 409
        assert pending_ids(proxy) == [op]
    finally:
        proxy.close()


def test_a_repeat_that_the_flow_rule_blocks_closes_the_waiting_operation(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20", policy=STALE_POLICY.format(mode="deny"))
    try:
        start_call(proxy, "create_pull_request", {"body": "b"}, 91)
        [op] = wait_for(lambda: pending_ids(proxy))
        read_both(proxy)
        reply = proxy.call("create_pull_request", {"body": "b"}, msg_id=92)
        assert blocked_code(reply) == "FLOW_BLOCKED"
        assert text_of(reply).endswith(REASON)
        assert blocked_code(proxy.recv_id(91)) == "CANCELLED"
        assert pending_ids(proxy) == []
        assert post(proxy, {"action": "approve", "operation_id": op, "digest": "x"})[2] == \
            "UNKNOWN_OPERATION"
        assert calls_to(proxy, "create_pull_request") == []
    finally:
        proxy.close()
