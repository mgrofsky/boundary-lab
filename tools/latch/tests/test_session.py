import json

import pytest

from boundary_latch.demo.scenarios import FakeClock
from boundary_latch.demo.session import DemoSession

REFUND = "issue_refund"


@pytest.fixture
def session():
    demo = DemoSession(":memory:", clock=FakeClock())
    yield demo
    demo.close()


def refund(minor: int) -> dict:
    return {"account_id": "A1", "amount_minor": minor}


def test_refund_over_the_threshold_needs_a_supervisor(session):
    proposed = session.propose("sam", REFUND, refund(6000))
    assert proposed["needs_approval"] is True
    assert proposed["code"] == "NEEDS_APPROVAL"
    op = proposed["operation_id"]
    assert len(op) == 32

    assert session.run("sam", op)["code"] == "APPROVAL_REQUIRED"
    assert session.approve("jordan", op)["code"] == "APPROVED"

    ran = session.run("sam", op)
    assert ran["code"] == "EXECUTED"
    assert ran["replayed"] is False
    assert ran["state"]["accounts"]["A1"]["balance_minor"] == 4000

    again = session.run("sam", op)
    assert again["replayed"] is True
    assert again["state"]["accounts"]["A1"]["balance_minor"] == 4000

    changed = session.propose("sam", REFUND, refund(7000), operation_id=op)
    assert changed["code"] == "OPERATION_CONFLICT"
    assert changed["allowed"] is False


def test_results_are_plain_json(session):
    proposed = session.propose("sam", REFUND, refund(2000))
    ran = session.run("sam", proposed["operation_id"])
    for result in (proposed, ran, session.state(), session.status(proposed["operation_id"]),
                   session.audit(), session.profiles()):
        assert json.loads(json.dumps(result)) == result


def test_deny_blocks_the_run(session):
    op = session.propose("sam", REFUND, refund(6000))["operation_id"]
    assert session.deny("jordan", op)["code"] == "DENIED_BY_APPROVER"
    assert session.run("sam", op)["code"] == "DENIED_BY_APPROVER"
    assert session.state()["accounts"]["A1"]["balance_minor"] == 10000


def test_a_denied_proposal_is_not_kept_pending(session):
    proposed = session.propose("sam", "lookup_account", {"account_id": "A2"})
    assert proposed["code"] == "ACCESS_DENIED"
    assert proposed["needs_approval"] is False
    assert session.run("sam", proposed["operation_id"])["found"] is False


def test_profiles_list_sample_identities_and_tools(session):
    data = session.profiles()
    names = [p["name"] for p in data["profiles"]]
    assert names == ["sam", "priya", "jordan"]
    assert all("sample identity" in p["label"] for p in data["profiles"])
    tools = {t["name"]: t for t in data["tools"]}
    assert set(tools) == {"lookup_account", "send_message", "issue_refund"}
    assert "amount_minor" in tools[REFUND]["parameters"]["properties"]


def test_state_status_and_audit(session):
    state = session.state()
    assert state["run_id"].startswith("demo-") and len(state["run_id"]) == 13
    assert state["outbox"] == [] and state["refunds"] == {}
    op = session.propose("sam", REFUND, refund(2000))["operation_id"]
    session.run("sam", op)
    status = session.status(op)
    assert status["found"] is True
    assert status["state"] == "succeeded" and status["code"] == "EXECUTED"
    assert session.state()["refunds"][op]["amount_minor"] == 2000
    events = [row["event"] for row in session.audit()["rows"]]
    assert events == ["prepare", "execute"]
    assert session.status("missing")["found"] is False


def test_unknown_profile_is_refused(session):
    with pytest.raises(ValueError, match="unknown profile"):
        session.propose("mallory", REFUND, refund(100))


def test_a_restart_keeps_the_record_but_not_the_backend(tmp_path):
    db = str(tmp_path / "demo.sqlite")
    first = DemoSession(db)
    op = first.propose("sam", REFUND, refund(2000))["operation_id"]
    assert first.run("sam", op)["code"] == "EXECUTED"
    first.close()

    second = DemoSession(db)
    try:
        assert second.run_id != first.run_id
        replay = second.run("sam", op)
        assert replay["code"] == "EXECUTED"
        assert replay["replayed"] is True
        assert second.nw.refunds == {}
        assert second.state()["accounts"]["A1"]["balance_minor"] == 10000
    finally:
        second.close()
