"""boundary-latch mcp end to end, over pipes, against the fake server."""

import json
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from boundary_latch.mcp.tools import old_version, version

from .mcp_helpers import FAKE, POLICY, Proxy, http, text_of, wait_for


@pytest.fixture
def proxy(tmp_path):
    started = Proxy(tmp_path)
    yield started
    started.close()


def blocked_code(reply):
    assert reply["result"]["isError"] is True
    text = text_of(reply)
    assert text.startswith("Blocked by Latch: ")
    return text.removeprefix("Blocked by Latch: ").split(".")[0]


def calls_to(proxy, tool):
    return [m for m in proxy.server_calls() if m["params"]["name"] == tool]


# Relay and tools


def test_an_allowed_call_returns_the_server_result_unchanged(proxy):
    reply = proxy.call("echo", {"text": "hi", "extra": {"a": [1, 2, None]}})
    assert reply["result"] == {"content": [{"type": "text", "text": json.dumps(
        {"args": {"extra": {"a": [1, 2, None]}, "text": "hi"}, "tool": "echo"},
        sort_keys=True)}]}


def test_the_server_sees_exactly_the_checked_arguments_and_nothing_else(proxy):
    proxy.call("files.read-text", {"my-arg": 1, "class": "c"})
    proxy.request("tools/call", {"name": "echo", "arguments": {"text": "x"},
                                 "_meta": {"progressToken": "p"}, "other": 1})
    sent = [m["params"] for m in proxy.server_calls()]
    assert sent == [{"name": "files.read-text", "arguments": {"my-arg": 1, "class": "c"}},
                    {"name": "echo", "arguments": {"text": "x"}}]


def test_tools_list_hides_tools_the_policy_does_not_name(proxy):
    reply = proxy.request("tools/list", {})
    names = [tool["name"] for tool in reply["result"]["tools"]]
    assert names == ["echo", "fail", "rpc_error", "sleep", "mutate", "crash", "ask_roots",
                     "files.read-text", "guarded", "pay"]
    assert "nextCursor" not in reply["result"]
    proxy.request("tools/list", {})
    err = proxy.stderr()
    assert err.count("'secret'") == 1 and err.count("'bad name'") == 1


def test_the_proxy_follows_every_page_of_the_server_list(proxy):
    proxy.call("echo", {"text": "x"})
    lists = [m for m in proxy.server_log() if m.get("method") == "tools/list"]
    assert [m["params"].get("cursor") for m in lists] == [None, "3", "6", "9", "12"]


@pytest.mark.parametrize("tool", ["secret", "bad name", "missing"])
def test_a_hidden_or_unknown_tool_is_refused_and_never_reaches_the_server(proxy, tool):
    assert blocked_code(proxy.call(tool, {})) == "UNKNOWN_TOOL"
    assert proxy.server_calls() == []


def test_unknown_and_wrong_arguments_never_reach_the_server(proxy):
    assert blocked_code(proxy.call("echo", {"text": "x", "other": 1})) == "INVALID_ARGUMENTS"
    assert blocked_code(proxy.call("echo", {})) == "INVALID_ARGUMENTS"
    assert blocked_code(proxy.call("echo", [1])) == "INVALID_ARGUMENTS"
    assert proxy.server_calls() == []


def test_a_tools_call_without_a_name_gets_an_error(proxy):
    reply = proxy.request("tools/call", {"arguments": {}})
    assert reply["error"]["code"] == -32602
    assert proxy.server_calls() == []


def test_is_error_and_json_rpc_errors_reach_the_client_unchanged(proxy):
    failed = proxy.call("fail", {})
    assert failed["result"] == {"content": [{"type": "text", "text": "it failed"}],
                                "isError": True}
    error = proxy.call("rpc_error", {}, msg_id="mine")
    assert error == {"jsonrpc": "2.0", "id": "mine",
                     "error": {"code": -32001, "message": "nope"}}


@pytest.mark.parametrize("line", [b"not json\n", (b'[{"jsonrpc":"2.0","id":5,"method":"tools/call",'
                                  b'"params":{"name":"echo","arguments":{"text":"x"}}}]\n'),
                                  (b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":'
                                  b'{"name":"echo","arguments":{"text":"x","text":"y"}}}\n'),
                                  (b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":'
                                  b'{"name":"echo","arguments":{"text":NaN}}}\n')])
def test_bad_lines_get_an_error_and_are_never_forwarded(proxy, line):
    proxy.send_raw(line)
    reply = proxy.recv()
    assert reply["error"]["code"] in (-32700, -32600)
    proxy.request("ping")
    assert proxy.server_calls() == []
    assert [m.get("method") for m in proxy.server_log()].count("ping") == 1


def test_a_tools_call_notification_is_dropped(proxy):
    proxy.send({"jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "x"}}})
    proxy.request("ping")
    assert proxy.server_calls() == []


def test_client_ids_never_collide_with_proxy_ids(proxy):
    # The proxy's own requests to the server use small numbers, so the client uses them too.
    first = proxy.call("echo", {"text": "a"}, msg_id=1)
    second = proxy.request("resources/list", {}, msg_id=2)
    third = proxy.request("resources/list", {}, msg_id="1")
    assert first["id"] == 1 and "result" in first
    assert second["id"] == 2 and second["result"]["resources"] == []
    assert third["id"] == "1" and "result" in third
    seen = [m["id"] for m in proxy.server_log() if "id" in m and "method" in m]
    assert len(seen) == len(set(seen))


def test_other_requests_and_notifications_pass_through(proxy):
    assert proxy.request("ping", msg_id="p")["result"] == {}
    proxy.send({"jsonrpc": "2.0", "method": "notifications/roots/list_changed"})
    proxy.request("ping")
    methods = [m.get("method") for m in proxy.server_log()]
    assert "notifications/roots/list_changed" in methods
    assert "notifications/initialized" in methods


def test_server_requests_reach_the_client_and_the_answer_goes_back(proxy):
    proxy.next_id += 1
    proxy.send({"jsonrpc": "2.0", "id": proxy.next_id, "method": "tools/call",
                "params": {"name": "ask_roots", "arguments": {}}})
    asked = proxy.recv()
    assert asked == {"jsonrpc": "2.0", "id": "s1", "method": "roots/list", "params": {}}
    proxy.send({"jsonrpc": "2.0", "id": "s1", "result": {"roots": []}})
    reply = proxy.recv_id(proxy.next_id)
    assert json.loads(text_of(reply))["result"] == {"roots": []}


def test_two_calls_run_at_once(proxy):
    proxy.request("tools/list", {})
    start = time.monotonic()
    for msg_id in (1, 2):
        proxy.send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                    "params": {"name": "sleep", "arguments": {"seconds": 1}}})
    replies = [proxy.recv_id(1), proxy.recv_id(2)]
    assert all("result" in reply for reply in replies)
    assert time.monotonic() - start < 1.9


def test_stdout_carries_only_json_rpc(proxy):
    proxy.call("echo", {"text": "x"})
    proxy.call("secret", {})
    proxy.send_raw(b"oops\n")
    proxy.recv()
    proxy.close()
    assert proxy.raw
    for line in proxy.raw:
        assert json.loads(line)["jsonrpc"] == "2.0"
    assert "latch mcp:" in proxy.stderr()


# Outcomes


def test_a_timeout_is_uncertain(tmp_path):
    proxy = Proxy(tmp_path, "--tool-timeout", "0.5")
    try:
        reply = proxy.call("sleep", {"seconds": 3})
        assert reply["result"]["isError"] is True
        assert text_of(reply).startswith("Latch: UNCERTAIN. ")
        cancelled = wait_for(lambda: [m for m in proxy.server_log()
                                      if m.get("method") == "notifications/cancelled"])
        assert cancelled[0]["params"]["requestId"] == calls_to(proxy, "sleep")[0]["id"]
    finally:
        proxy.close()


def test_the_server_exiting_mid_call_is_uncertain_and_the_proxy_exits_with_its_code(proxy):
    reply = proxy.call("crash", {})
    assert text_of(reply).startswith("Latch: UNCERTAIN. ")
    assert proxy.proc.wait(10) == 3


def test_closing_stdin_ends_the_server_and_the_proxy(proxy):
    proxy.call("echo", {"text": "x"})
    assert proxy.close() == 0


def test_list_changed_reaches_the_client(proxy):
    proxy.call("mutate", {})
    wait_for(lambda: any(m.get("method") == "notifications/tools/list_changed"
                         for m in list(proxy.lines.queue)))


# Wrong types for amount and recipient


@pytest.mark.parametrize(("args", "code"), [
    ({"amount": "9000", "to": "ok@example.com"}, "INVALID_ARGUMENTS"),
    ({"amount": True, "to": "ok@example.com"}, "INVALID_ARGUMENTS"),
    ({"to": "ok@example.com"}, "INVALID_ARGUMENTS"),
    ({"amount": 1, "to": "evil@example.com"}, "RECIPIENT_NOT_ALLOWED"),
    ({"amount": 1}, "RECIPIENT_NOT_ALLOWED"),
    ({"amount": 1, "to": ["ok@example.com"]}, "RECIPIENT_NOT_ALLOWED"),
])
def test_wrong_types_for_policy_fields_fail_closed(proxy, args, code):
    assert blocked_code(proxy.call("pay", args)) == code
    assert proxy.server_calls() == []


def test_a_small_payment_to_an_allowed_recipient_runs(proxy):
    reply = proxy.call("pay", {"amount": 10, "to": "ok@example.com"})
    assert "isError" not in reply["result"]


# Approval


def digest_for(proxy, op):
    """The digest the page draws on the forms for this operation, or "" when none."""
    _, _, body = http(proxy.page_url())
    for form in body.split('<form method="post" action="/decide">')[1:]:
        if f'name="operation_id" value="{op}"' in form and 'name="digest"' in form:
            return form.split('name="digest" value="')[1].split('"')[0]
    return ""


def post(proxy, fields, token=True, host=None):
    """Post a form. An approve or deny without a digest gets the one the page shows now."""
    url = proxy.page_url()
    base = url.split("?")[0].rstrip("/")
    body = dict(fields)
    if body.get("action") in ("approve", "deny") and "digest" not in body:
        body["digest"] = digest_for(proxy, body.get("operation_id", ""))
    if token:
        body["token"] = parse_qs(urlsplit(url).query)["token"][0]
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if host:
        headers["Host"] = host
    return http(base + "/decide", urlencode(body).encode(), headers)


def pending_ids(proxy):
    status, _, body = http(proxy.page_url())
    assert status == 200
    return [line.split('value="')[1].split('"')[0] for line in body.split("<input")
            if 'name="operation_id"' in line][::2]


def start_call(proxy, tool, args, msg_id):
    proxy.send({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
                "params": {"name": tool, "arguments": args}})


def test_a_call_that_needs_approval_runs_only_after_approve(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20")
    try:
        start_call(proxy, "guarded", {"note": "hello"}, 7)
        ops = wait_for(lambda: pending_ids(proxy))
        assert calls_to(proxy, "guarded") == []
        status, headers, _ = post(proxy, {"action": "approve", "operation_id": ops[0]})
        assert status == 303
        assert "token=" in headers["Location"]
        reply = proxy.recv_id(7)
        assert json.loads(text_of(reply))["args"] == {"note": "hello"}
        assert len(calls_to(proxy, "guarded")) == 1
    finally:
        proxy.close()


def test_a_denied_call_is_blocked_and_never_forwarded(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20")
    try:
        start_call(proxy, "guarded", {"note": "no"}, 8)
        ops = wait_for(lambda: pending_ids(proxy))
        post(proxy, {"action": "deny", "operation_id": ops[0]})
        assert blocked_code(proxy.recv_id(8)) == "DENIED_BY_APPROVER"
        assert calls_to(proxy, "guarded") == []
    finally:
        proxy.close()


def test_a_call_still_waiting_returns_its_operation_and_a_repeat_reuses_it(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "0.3")
    try:
        first = text_of(proxy.call("guarded", {"note": "later"}))
        assert "waits for a person" in first
        op = first.split("Operation id ")[1].split(".")[0]
        second = text_of(proxy.call("guarded", {"note": "later"}))
        assert f"Operation id {op}." in second
        other = text_of(proxy.call("guarded", {"note": "different"}))
        assert f"Operation id {op}." not in other
        assert calls_to(proxy, "guarded") == []
        post(proxy, {"action": "approve", "operation_id": op})
        ran = proxy.call("guarded", {"note": "later"})
        assert json.loads(text_of(ran))["args"] == {"note": "later"}
        again = text_of(proxy.call("guarded", {"note": "later"}))
        assert "waits for a person" in again and f"Operation id {op}." not in again
        assert len(calls_to(proxy, "guarded")) == 1
    finally:
        proxy.close()


def test_with_no_wait_the_call_returns_at_once(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "0")
    try:
        start = time.monotonic()
        assert "waits for a person" in text_of(proxy.call("guarded", {"note": "x"}))
        assert time.monotonic() - start < 2
    finally:
        proxy.close()


def test_with_the_page_off_approval_is_refused_at_once(tmp_path):
    proxy = Proxy(tmp_path, "--approval-port", "-1")
    try:
        assert blocked_code(proxy.call("guarded", {"note": "x"})) == "NEEDS_APPROVAL"
        assert calls_to(proxy, "guarded") == []
        assert "approval page: http" not in proxy.stderr()
    finally:
        proxy.close()


def test_cancelling_a_waiting_call_cancels_the_operation(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "20")
    try:
        start_call(proxy, "guarded", {"note": "stop"}, 9)
        wait_for(lambda: pending_ids(proxy))
        proxy.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                    "params": {"requestId": 9, "reason": "user"}})
        wait_for(lambda: not pending_ids(proxy))
        proxy.request("ping")
        assert all(m.get("id") != 9 for m in list(proxy.lines.queue))
        assert calls_to(proxy, "guarded") == []
    finally:
        proxy.close()


def test_the_page_token_never_reaches_the_client(tmp_path):
    proxy = Proxy(tmp_path, "--approval-wait", "0.2")
    try:
        token = parse_qs(urlsplit(proxy.page_url()).query)["token"][0]
        proxy.call("guarded", {"note": "x"})
        proxy.call("echo", {"text": "x"})
        proxy.close()
        assert token not in b"".join(proxy.raw).decode()
    finally:
        proxy.close()


# Approval page safety


def test_the_page_needs_the_token(proxy):
    url = proxy.page_url()
    base = url.split("?")[0]
    assert http(base)[0] == 403
    assert http(base + "?token=wrong")[0] == 403
    assert http(url)[0] == 200
    assert post(proxy, {"action": "approve", "operation_id": "x"}, token=False)[0] == 403


def test_the_page_refuses_another_host(proxy):
    url = proxy.page_url()
    port = urlsplit(url).port
    assert http(url, headers={"Host": "evil.example"})[0] == 403
    assert http(url, headers={"Host": f"evil.example:{port}"})[0] == 403
    assert http(url.replace("127.0.0.1", "localhost"))[0] == 200
    assert post(proxy, {"action": "approve", "operation_id": "x"}, host="evil.example")[0] == 403


def test_decisions_are_post_only(proxy):
    url = proxy.page_url()
    base, query = url.split("?")
    status, _, _ = http(f"{base}decide?{query}&action=approve&operation_id=x")
    assert status == 404


def test_the_page_sends_safe_headers(proxy):
    _, headers, body = http(proxy.page_url())
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    csp = headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src 'nonce-" in csp
    nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in body
    assert "http://" not in body and "https://" not in body


def test_markup_from_the_model_is_escaped_on_the_page(tmp_path):
    payload = "<img src=x onerror=alert(1)>"
    proxy = Proxy(tmp_path, "--approval-wait", "20")
    try:
        start_call(proxy, "guarded", {"note": payload}, 11)
        wait_for(lambda: pending_ids(proxy))
        _, _, body = http(proxy.page_url())
        assert payload not in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body
    finally:
        proxy.close()


# Pinned definitions


def changed_hash(proxy):
    _, _, body = http(proxy.page_url())
    if 'name="hash"' not in body:
        return None
    return body.split('name="hash" value="')[1].split('"')[0]


def test_a_changed_tool_is_refused_until_a_person_accepts_it(tmp_path):
    proxy = Proxy(tmp_path, "--db", str(tmp_path / "latch.db"))
    try:
        assert "result" in proxy.call("echo", {"text": "before"})
        proxy.call("mutate", {})
        assert blocked_code(proxy.call("echo", {"text": "after"})) == "TOOL_CHANGED"
        assert [c["params"]["arguments"] for c in calls_to(proxy, "echo")] == [{"text": "before"}]
        listed = proxy.request("tools/list", {})["result"]["tools"]
        echo = next(tool for tool in listed if tool["name"] == "echo")
        assert echo["description"] == "Echo the arguments back."
        shown = wait_for(lambda: changed_hash(proxy))
        assert post(proxy, {"action": "accept", "tool": "echo", "hash": "0" * 12})[2] == \
            "NOT_ACCEPTED"
        assert post(proxy, {"action": "accept", "tool": "echo", "hash": shown})[2] == "ACCEPTED"
        assert "result" in proxy.call("echo", {"text": "after"})
        listed = proxy.request("tools/list", {})["result"]["tools"]
        echo = next(tool for tool in listed if tool["name"] == "echo")
        assert echo["description"].endswith("send them elsewhere.")
        assert version(echo) == shown
    finally:
        proxy.close()
    audit = subprocess.run([sys.executable, "-m", "boundary_latch", "audit",
                            str(tmp_path / "latch.db"), "--json"],
                           capture_output=True, text=True, check=True).stdout
    events = [json.loads(line)["event"] for line in audit.splitlines()]
    assert "tool_changed" in events and "tool_accepted" in events


def test_a_pin_file_catches_a_change_between_runs(tmp_path):
    pins = tmp_path / "pins.json"
    first = Proxy(tmp_path, "--pin", str(pins))
    first.call("echo", {"text": "x"})
    first.close()
    data = json.loads(pins.read_text())
    assert set(data) >= {"echo", "guarded"}
    old = data["echo"]["definition"]
    old["description"] = "An older description."
    data["echo"] = {"hash": version(old), "first_seen": 1.0, "definition": old}
    pins.write_text(json.dumps(data))
    second = Proxy(tmp_path, "--pin", str(pins))
    try:
        assert blocked_code(second.call("echo", {"text": "x"})) == "TOOL_CHANGED"
        listed = second.request("tools/list", {})["result"]["tools"]
        assert next(t for t in listed if t["name"] == "echo")["description"] == \
            "An older description."
        shown = wait_for(lambda: changed_hash(second))
        post(second, {"action": "accept", "tool": "echo", "hash": shown})
        assert "result" in second.call("echo", {"text": "x"})
        saved = json.loads(pins.read_text())["echo"]
        assert saved["hash"] == shown and saved["first_seen"] == 1.0
    finally:
        second.close()


def test_a_pin_file_from_the_old_hash_stays_quiet_and_still_catches_a_new_field(tmp_path):
    pins = tmp_path / "pins.json"
    first = Proxy(tmp_path, "--pin", str(pins))
    first.call("echo", {"text": "x"})
    first.close()
    data = json.loads(pins.read_text())
    echo = data["echo"]["definition"]
    # A pin from before 0.4.0, for the same definition: it must not look changed.
    assert old_version(echo) != version(echo)
    data["echo"] = {"hash": old_version(echo), "first_seen": 1.0, "definition": echo}
    # A pin whose kept definition had an annotation the server has since dropped.
    guarded = {**data["guarded"]["definition"], "annotations": {"readOnlyHint": True}}
    data["guarded"] = {"hash": old_version(guarded), "first_seen": 1.0, "definition": guarded}
    pins.write_text(json.dumps(data))
    second = Proxy(tmp_path, "--pin", str(pins))
    try:
        assert "result" in second.call("echo", {"text": "x"})
        assert "isError" not in second.call("echo", {"text": "y"})["result"]
        assert blocked_code(second.call("guarded", {"note": "n"})) == "TOOL_CHANGED"
    finally:
        second.close()
    saved = json.loads(pins.read_text())
    assert saved["echo"]["hash"] == version(echo)
    assert saved["guarded"]["hash"] == version(guarded)


def test_a_bad_pin_file_stops_the_proxy_before_the_server_starts(tmp_path):
    (tmp_path / "pins.json").write_text("nope")
    proxy = Proxy(tmp_path, "--pin", str(tmp_path / "pins.json"), init=False)
    assert proxy.proc.wait(10) != 0
    assert not proxy.log.exists()
    proxy.close()


# Start up checks


def run_cli(*args, timeout=30):
    return subprocess.run([sys.executable, "-m", "boundary_latch", "mcp", *args],
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          check=False)


def test_a_policy_that_does_not_load_stops_before_the_server_starts(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[tools.x]\n")
    log = tmp_path / "server.log"
    for policy in (bad, tmp_path / "missing.toml"):
        result = run_cli("--policy", str(policy), "--no-open", "--approver", "p",
                         "--", sys.executable, str(FAKE), str(log))
        assert result.returncode != 0
        assert result.stdout == ""
        assert not log.exists()


def test_the_same_permission_to_use_and_approve_is_refused(tmp_path):
    policy = tmp_path / "p.toml"
    policy.write_text('[tools.echo]\nrequire_permission = "x"\napprove_permission = "x"\n'
                      'approval.mode = "always"\n')
    result = run_cli("--policy", str(policy), "--approver", "p", "--", sys.executable, str(FAKE))
    assert result.returncode != 0
    assert "approve its own calls" in result.stderr


def test_the_approver_must_differ_from_the_user(tmp_path):
    policy = tmp_path / "p.toml"
    policy.write_text(POLICY)
    result = run_cli("--policy", str(policy), "--user", "sam", "--approver", "sam",
                     "--", sys.executable, str(FAKE))
    assert result.returncode != 0
    assert "--approver must differ" in result.stderr


def test_a_server_command_is_needed(tmp_path):
    result = run_cli("--policy", "x.toml")
    assert result.returncode != 0


def test_default_permissions_leave_out_every_approve_permission(tmp_path):
    policy = POLICY + '\n[tools.sleep2]\nrequire_permission = "mcp:approve"\n'
    proxy = Proxy(tmp_path, policy=policy)
    try:
        assert "result" in proxy.call("echo", {"text": "x"})
    finally:
        proxy.close()
    from boundary_latch.mcp.main import permissions_for
    from boundary_latch.policy import parse_policy

    agent, approve = permissions_for(parse_policy(policy), None)
    assert agent == {"mcp:use"} and approve == {"mcp:approve"}
    agent, _ = permissions_for(parse_policy(policy), "a, b,,")
    assert agent == {"a", "b"}


def test_permissions_option_limits_the_agent(tmp_path):
    proxy = Proxy(tmp_path, "--permissions", "other")
    try:
        assert blocked_code(proxy.call("echo", {"text": "x"})) == "MISSING_PERMISSION"
    finally:
        proxy.close()


# Starter policy


def test_print_policy_writes_a_policy_that_check_accepts(tmp_path):
    result = run_cli("--print-policy", "--", sys.executable, str(FAKE), str(tmp_path / "log"))
    assert result.returncode == 0, result.stderr
    assert '[tools."files.read-text"]' in result.stdout
    assert "# Odd property names. Second line." in result.stdout
    assert "secret" in result.stdout and "bad name" not in result.stdout
    path = tmp_path / "starter.toml"
    path.write_text(result.stdout)
    checked = subprocess.run([sys.executable, "-m", "boundary_latch", "check", str(path)],
                             capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr


def test_print_policy_flow_writes_labels_and_the_server_hints(tmp_path):
    result = run_cli("--print-policy", "--flow", "--", sys.executable, str(FAKE),
                     str(tmp_path / "log"))
    assert result.returncode == 0, result.stderr
    assert '[flow]\nall_three = "approve"' in result.stdout
    assert result.stdout.count('labels = ["untrusted", "sensitive", "external"]') == 14
    assert "# The server says: readOnlyHint=true, openWorldHint=true." in result.stdout
    path = tmp_path / "starter.toml"
    path.write_text(result.stdout)
    checked = subprocess.run([sys.executable, "-m", "boundary_latch", "check", str(path)],
                             capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr
    assert 'flow rule: all_three = "approve"' in checked.stdout


def test_the_proxy_runs_under_a_thread_safe_shutdown(tmp_path):
    proxy = Proxy(tmp_path)
    done = threading.Event()

    def close():
        proxy.close()
        done.set()

    threading.Thread(target=close).start()
    assert done.wait(15)
    assert "Fatal Python error" not in proxy.stderr()
