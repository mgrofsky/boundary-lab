"""Approval references, the pending list, cancel, and closed operations."""

import pytest

from boundary_latch.models import Decision, OperationStatus, PreparedAction
from boundary_latch.reasons import Code, explain

from .conftest import make_approver, make_ctx

MESSAGE = {"to": "help@northwind.example", "body": "hello"}
BIG_REFUND = {"account_id": "A1", "amount_minor": 6000}
SMALL_REFUND = {"account_id": "A1", "amount_minor": 100}


def propose(latch, op, tool="send_message", args=MESSAGE, ctx=None):
    prepared = latch.prepare(ctx or make_ctx(), tool, args, op)
    assert isinstance(prepared, PreparedAction)
    return prepared


# Approval reference


def test_approve_stores_the_reference(latch, handlers):
    prepared = propose(latch, "op1")
    decision = latch.approve(prepared, make_approver(), reference="slack:C1/1234.5")
    assert decision.code == Code.APPROVED
    status = latch.operation_status("northwind", "op1")
    assert status.reference == "slack:C1/1234.5"
    result = latch.execute(prepared, make_ctx())
    assert result.code == Code.EXECUTED
    assert result.reference == "slack:C1/1234.5"
    rows = latch.store.audit_rows("northwind")
    assert [r["reference"] for r in rows if r["event"] == "approve"] == ["slack:C1/1234.5"]
    assert rows[-1]["event"] == "execute"
    assert rows[-1]["reference"] == "slack:C1/1234.5"


def test_deny_stores_the_reference(latch):
    prepared = propose(latch, "op1")
    decision = latch.deny(prepared, make_approver(), reference="TICKET-9")
    assert decision.code == Code.DENIED_BY_APPROVER
    assert latch.operation_status("northwind", "op1").reference == "TICKET-9"
    result = latch.execute(prepared, make_ctx())
    assert result.reference == "TICKET-9"


def test_no_reference_is_none(latch):
    prepared = propose(latch, "op1")
    latch.approve(prepared, make_approver())
    assert latch.operation_status("northwind", "op1").reference is None
    assert latch.execute(prepared, make_ctx()).reference is None


@pytest.mark.parametrize("bad", ["x" * 129, "line\nbreak", "tab\there", "bell\x07", 12])
def test_invalid_reference_raises(latch, bad):
    prepared = propose(latch, "op1")
    with pytest.raises(ValueError):
        latch.approve(prepared, make_approver(), reference=bad)
    with pytest.raises(ValueError):
        latch.deny(prepared, make_approver(), reference=bad)
    # Nothing was recorded, so a valid approval still works.
    assert latch.approve(prepared, make_approver(), reference="x" * 128).code == Code.APPROVED


# Operation status


def test_status_has_the_new_fields(latch):
    propose(latch, "op1")
    status = latch.operation_status("northwind", "op1")
    assert status.needs_approval is True
    assert status.user_id == "sam"
    assert status.run_id == "run1"
    assert status.summary == ""
    assert status.copied == frozenset()
    assert status.reference is None


# Pending list


def test_pending_lists_waiting_operations_oldest_first(latch, clock):
    propose(latch, "op1")
    clock.now += 1
    propose(latch, "small", "refund", SMALL_REFUND)
    clock.now += 1
    propose(latch, "op2", "refund", BIG_REFUND)
    clock.now += 1
    propose(latch, "other-run", ctx=make_ctx(run_id="run2"))
    clock.now += 1
    decided = propose(latch, "op3")
    latch.approve(decided, make_approver())
    pending = latch.pending("northwind")
    assert all(isinstance(item, OperationStatus) for item in pending)
    assert [item.operation_id for item in pending] == ["op1", "op2", "other-run"]
    assert [item.operation_id for item in latch.pending("northwind", run_id="run2")] == [
        "other-run"
    ]
    assert latch.pending("elsewhere") == []


def test_pending_drops_denied_and_cancelled(latch):
    denied = propose(latch, "op1")
    latch.deny(denied, make_approver())
    cancelled = propose(latch, "op2")
    latch.cancel(cancelled, make_ctx())
    assert latch.pending("northwind") == []


# Cancel


def test_requester_can_cancel(latch, handlers):
    prepared = propose(latch, "op1", "refund", SMALL_REFUND)
    decision = latch.cancel(prepared, make_ctx())
    assert isinstance(decision, Decision)
    assert decision.code == Code.CANCELLED
    assert decision.explanation == explain(Code.CANCELLED)
    assert decision.operation_id == "op1"
    result = latch.execute(prepared, make_ctx())
    assert result.outcome == "denied"
    assert result.code == Code.CANCELLED
    assert handlers["refund"].call_count == 0
    status = latch.operation_status("northwind", "op1")
    assert (status.state, status.code) == ("cancelled", Code.CANCELLED)
    rows = latch.store.audit_rows("northwind")
    cancel_rows = [r for r in rows if r["event"] == "cancel"]
    assert cancel_rows[0]["code"] == "CANCELLED"
    assert cancel_rows[0]["user_id"] == "sam"


def test_approver_can_cancel_an_approved_operation(latch, handlers):
    prepared = propose(latch, "op1")
    latch.approve(prepared, make_approver())
    assert latch.cancel(prepared, make_approver()).code == Code.CANCELLED
    assert latch.execute(prepared, make_ctx()).code == Code.CANCELLED
    assert handlers["send_message"].call_count == 0


def test_cancel_by_someone_else_is_refused(latch):
    prepared = propose(latch, "op1")
    stranger = make_ctx(user_id="priya", permissions={"messages:send"})
    decision = latch.cancel(prepared, stranger)
    assert decision.code == Code.APPROVER_NOT_ALLOWED
    assert latch.operation_status("northwind", "op1").state == "prepared"


def test_cancel_from_another_org_is_refused(latch):
    prepared = propose(latch, "op1")
    decision = latch.cancel(prepared, make_ctx(org_id="other"))
    assert decision.code == Code.IDENTITY_MISMATCH
    assert latch.operation_status("northwind", "op1").state == "prepared"


def test_cancel_after_the_run_is_already_recorded(latch):
    prepared = propose(latch, "op1", "refund", SMALL_REFUND)
    latch.execute(prepared, make_ctx())
    assert latch.cancel(prepared, make_ctx()).code == Code.ALREADY_RECORDED
    assert latch.operation_status("northwind", "op1").state == "succeeded"


def test_cancel_while_reserved_is_already_recorded(latch):
    prepared = propose(latch, "op1", "refund", SMALL_REFUND)
    latch.store.set_state("northwind", "op1", "reserved", "ALLOWED", latch.clock())
    assert latch.cancel(prepared, make_ctx()).code == Code.ALREADY_RECORDED


def test_cancel_a_denied_operation_keeps_the_denial(latch):
    prepared = propose(latch, "op1")
    latch.deny(prepared, make_approver())
    assert latch.cancel(prepared, make_ctx()).code == Code.DENIED_BY_APPROVER
    assert latch.operation_status("northwind", "op1").code == Code.DENIED_BY_APPROVER


def test_approve_after_cancel_is_refused(latch):
    prepared = propose(latch, "op1")
    latch.cancel(prepared, make_ctx())
    assert latch.approve(prepared, make_approver()).code == Code.CANCELLED


# Closed operations


def test_closed_explanation():
    assert explain(Code.OPERATION_CLOSED) == (
        "This operation was denied, cancelled or refused earlier, so it cannot be prepared "
        "again. Use a new operation ID."
    )


def test_repeat_prepare_after_deny_is_closed(latch):
    prepared = propose(latch, "op1")
    latch.deny(prepared, make_approver())
    again = latch.prepare(make_ctx(), "send_message", MESSAGE, "op1")
    assert isinstance(again, Decision)
    assert again.code == Code.OPERATION_CLOSED


def test_repeat_prepare_after_cancel_is_closed(latch):
    prepared = propose(latch, "op1")
    latch.cancel(prepared, make_ctx())
    again = latch.prepare(make_ctx(), "send_message", MESSAGE, "op1")
    assert again.code == Code.OPERATION_CLOSED


def test_a_changed_request_on_a_closed_operation_is_still_a_conflict(latch):
    prepared = propose(latch, "op1")
    latch.deny(prepared, make_approver())
    changed = {"to": "help@northwind.example", "body": "changed"}
    again = latch.prepare(make_ctx(), "send_message", changed, "op1")
    assert again.code == Code.OPERATION_CONFLICT
