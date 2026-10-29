import importlib.util

import pytest

from boundary_latch.demo import server
from boundary_latch.demo.session import DemoSession

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None, reason="needs boundary-latch[demo]"
)

PAYLOAD = "<img src=x onerror=alert(1)>"
HEADERS = {"Origin": "http://127.0.0.1:8765", "X-Latch-Token": "t"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    app = server.create_app(DemoSession(), "t", port=8765)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def send(client, to):
    body = {"profile": "sam", "tool": "send_message", "arguments": {"to": to, "body": PAYLOAD}}
    return client.post("/api/propose", json=body, headers=HEADERS).json()


def test_markup_to_an_outside_address_is_refused(client):
    assert send(client, "attacker@evil.example")["code"] == "RECIPIENT_NOT_ALLOWED"
    assert PAYLOAD not in client.get("/api/audit").text


def test_markup_stays_a_json_string(client):
    proposed = send(client, "help@northwind.example")
    assert proposed["code"] == "NEEDS_APPROVAL"
    op = proposed["operation_id"]
    approved = client.post("/api/approve", json={"profile": "jordan", "operation_id": op},
                           headers=HEADERS).json()
    assert approved["code"] == "APPROVED"
    ran = client.post("/api/run", json={"profile": "sam", "operation_id": op},
                      headers=HEADERS).json()
    assert ran["code"] == "EXECUTED"

    audit = client.get("/api/audit")
    assert audit.json()["rows"]
    assert PAYLOAD not in audit.text

    state = client.get("/api/state")
    assert state.headers["content-type"] == "application/json"
    assert state.json()["outbox"][0]["body"] == PAYLOAD
    assert state.headers["x-content-type-options"] == "nosniff"
