import importlib.util
import json

import pytest

from boundary_latch import ConfigError
from boundary_latch.demo import server
from boundary_latch.demo.session import DemoSession
from boundary_latch.live import LiveConfig

# fastapi is imported only inside the tests, so test_package still sees a clean process.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None, reason="needs boundary-latch[demo]"
)


def http(app, base_url):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url=base_url)

TOKEN = "test-token-123"
ORIGIN = "http://127.0.0.1:8765"
GOOD = {"Origin": ORIGIN, "X-Latch-Token": TOKEN}


def make_client(**kwargs):
    app = server.create_app(DemoSession(), TOKEN, port=8765, **kwargs)
    return http(app, "http://127.0.0.1:8765")


@pytest.fixture
def client():
    return make_client()


def post(client, path, body=None, headers=GOOD):
    return client.post(path, json=body or {}, headers=headers)


def refund(client, amount=6000, operation_id=None):
    body = {"profile": "sam", "tool": "issue_refund",
            "arguments": {"account_id": "A1", "amount_minor": amount}}
    if operation_id:
        body["operation_id"] = operation_id
    return post(client, "/api/propose", body)


def test_index_carries_the_token(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f'<meta name="latch-token" content="{TOKEN}">' in response.text


def test_security_headers_on_every_response(client):
    for response in (client.get("/"), client.get("/api/state"), post(client, "/api/run", {},
                                                                     headers={})):
        csp = response.headers["content-security-policy"]
        assert csp == server.CSP
        assert "script-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_post_without_token_is_forbidden(client):
    response = post(client, "/api/propose", {}, headers={"Origin": ORIGIN})
    assert response.status_code == 403


def test_post_with_a_wrong_token_is_forbidden(client):
    response = post(client, "/api/propose", {}, headers={**GOOD, "X-Latch-Token": "nope"})
    assert response.status_code == 403


def test_post_from_another_origin_is_forbidden(client):
    response = post(client, "/api/propose", {}, headers={**GOOD, "Origin": "http://evil.example"})
    assert response.status_code == 403


def test_post_without_origin_is_forbidden(client):
    response = post(client, "/api/propose", {}, headers={"X-Latch-Token": TOKEN})
    assert response.status_code == 403


def test_post_must_be_json(client):
    response = client.post("/api/propose", content="profile=sam", headers={
        **GOOD, "Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 403


def test_another_host_is_refused(client):
    for host in ("evil.example", "127.0.0.1:9999", "127.0.0.1"):
        assert client.get("/", headers={"Host": host}).status_code == 400
        assert client.get("/api/state", headers={"Host": host}).status_code == 400


def test_localhost_is_accepted(client):
    response = client.get("/api/state", headers={"Host": "localhost:8765"})
    assert response.status_code == 200
    origin = {**GOOD, "Host": "localhost:8765", "Origin": "http://localhost:8765"}
    assert post(client, "/api/propose", {"profile": "sam", "tool": "lookup_account",
                                         "arguments": {"account_id": "A1"}},
                headers=origin).status_code == 200


def test_refund_over_the_threshold_needs_approval_then_runs(client):
    proposed = refund(client).json()
    assert proposed["needs_approval"] is True
    assert proposed["code"] == "NEEDS_APPROVAL"
    op = proposed["operation_id"]

    early = post(client, "/api/run", {"profile": "sam", "operation_id": op}).json()
    assert early["code"] == "APPROVAL_REQUIRED"

    approved = post(client, "/api/approve", {"profile": "jordan", "operation_id": op}).json()
    assert approved["code"] == "APPROVED"

    ran = post(client, "/api/run", {"profile": "sam", "operation_id": op}).json()
    assert ran["code"] == "EXECUTED"
    assert ran["state"]["accounts"]["A1"]["balance_minor"] == 4000

    again = post(client, "/api/run", {"profile": "sam", "operation_id": op}).json()
    assert again["replayed"] is True
    assert client.get("/api/state").json()["accounts"]["A1"]["balance_minor"] == 4000


def test_deny(client):
    op = refund(client).json()["operation_id"]
    denied = post(client, "/api/deny", {"profile": "jordan", "operation_id": op}).json()
    assert denied["code"] == "DENIED_BY_APPROVER"


def test_unknown_profile_is_a_bad_request(client):
    response = post(client, "/api/propose", {"profile": "mallory", "tool": "lookup_account",
                                             "arguments": {"account_id": "A1"}})
    assert response.status_code == 400


def test_extra_body_fields_are_refused(client):
    response = post(client, "/api/run", {"profile": "sam", "operation_id": "x", "approved": True})
    assert response.status_code == 422


def test_profiles_and_audit(client):
    profiles = client.get("/api/profiles").json()
    assert [p["name"] for p in profiles["profiles"]] == ["sam", "priya", "jordan"]
    assert [t["name"] for t in profiles["tools"]] == [
        "lookup_account", "send_message", "issue_refund"]
    refund(client)
    rows = client.get("/api/audit").json()["rows"]
    assert rows


def test_static_files_are_a_whitelist(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    for path in ("/static/../pyproject.toml", "/static/%2e%2e/pyproject.toml",
                 "/static/..%2fpyproject.toml", "/static/index.html",
                 "/static/pyproject.toml", "/static/policy.toml"):
        assert client.get(path).status_code == 404, path


def test_scenario_runs_and_matches(client):
    result = post(client, "/api/scenario/duplicate_refund").json()
    assert result["observed"] == result["expected"]
    assert result["passed"] is True
    changes = [step["state_change"] for step in result["steps"]]
    assert "A1 balance 100.00 -> 80.00" in changes
    assert result["steps"][-1]["code"] == "ALREADY_RECORDED"


def test_unknown_scenario_is_not_found(client):
    assert post(client, "/api/scenario/nope").status_code == 404


def test_scenario_list(client):
    names = [s["name"] for s in client.get("/api/scenarios").json()["scenarios"]]
    assert len(names) == 9
    assert "duplicate_refund" in names


def test_live_turn_is_off_by_default(client):
    response = post(client, "/api/live/turn", {"profile": "sam", "text": "hi"})
    assert response.status_code == 409
    assert response.json()["detail"] == "live mode is off"
    assert client.get("/api/state").json()["live"] == {"on": False, "model": None}


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def test_live_turn_when_on():
    call = {"id": "c1", "type": "function", "function": {
        "name": "lookup_account", "arguments": json.dumps({"account_id": "A1"})}}
    fake = FakeClient([
        {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "Balance is 100.00."}}]},
    ])
    config = LiveConfig("http://model.test/v1", "small-model", None)
    client = make_client(live=config, chat_client=fake)
    assert client.get("/api/state").json()["live"] == {"on": True, "model": "small-model"}
    result = post(client, "/api/live/turn", {"profile": "sam", "text": "balance?"}).json()
    assert [e["event"] for e in result["events"]] == ["tool", "assistant"]
    assert result["events"][0]["code"] == "EXECUTED"
    assert result["state"]["accounts"]["A1"]["balance_minor"] == 10000


def test_live_turn_reports_a_malformed_response():
    fake = FakeClient([{"choices": []}])
    config = LiveConfig("http://model.test/v1", "small-model", None)
    client = make_client(live=config, chat_client=fake)
    response = post(client, "/api/live/turn", {"profile": "sam", "text": "hi"})
    assert response.status_code == 502
    assert response.json()["detail"] == "model response refused: response has no choices"


def test_serve_refuses_a_public_host():
    for host in ("0.0.0.0", "192.168.1.5", "localhost.evil.example", "::"):
        with pytest.raises(ConfigError):
            server.serve(host=host)


def test_serve_live_needs_the_env(monkeypatch):
    monkeypatch.delenv("LATCH_BASE_URL", raising=False)
    monkeypatch.delenv("LATCH_MODEL", raising=False)
    with pytest.raises(ConfigError, match="LATCH_BASE_URL"):
        server.serve(live=True)


def test_serve_hands_a_loopback_app_to_uvicorn(monkeypatch, tmp_path, capsys):
    seen = {}

    def run(app, host, port, **kwargs):
        # The session closes when uvicorn returns, so the app is tried while it runs.
        seen.update(host=host, port=port)
        client = http(app, "http://127.0.0.1:8799")
        page = client.get("/").text
        token = page.split('name="latch-token" content="')[1].split('"')[0]
        seen["token"] = token
        headers = {"Origin": "http://127.0.0.1:8799", "X-Latch-Token": token}
        seen["status"] = client.post("/api/propose", json={
            "profile": "sam", "tool": "lookup_account", "arguments": {"account_id": "A1"}},
            headers=headers).status_code

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", run)
    server.serve(port=8799, db=str(tmp_path / "demo.sqlite"))
    assert (seen["host"], seen["port"], seen["status"]) == ("127.0.0.1", 8799, 200)
    assert len(seen["token"]) >= 40
    assert "http://127.0.0.1:8799" in capsys.readouterr().out
