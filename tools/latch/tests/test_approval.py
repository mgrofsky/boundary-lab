from boundary_latch.models import Decision, PreparedAction
from boundary_latch.reasons import Code, explain

from .conftest import make_approver, make_ctx, no_calls

BIG_REFUND = {"account_id": "A1", "amount_minor": 6000}


def prepare_big_refund(latch, operation_id="op1") -> PreparedAction:
    prepared = latch.prepare(make_ctx(), "refund", BIG_REFUND, operation_id)
    assert isinstance(prepared, PreparedAction) and prepared.needs_approval
    return prepared


def is_code(result, code: Code, allowed: bool = False) -> bool:
    return isinstance(result, Decision) and result.allowed is allowed and result.code == code


def test_approve_records_the_approval(latch, clock, handlers):
    prepared = prepare_big_refund(latch)
    clock.now += 10
    result = latch.approve(prepared, make_approver())
    assert is_code(result, Code.APPROVED, allowed=True)
    assert result.explanation == explain(Code.APPROVED)
    assert result.operation_id == "op1"
    row = latch.store.get_operation("northwind", "op1")
    assert (row["state"], row["code"]) == ("approved", "APPROVED")
    approval = latch.store.get_approval("northwind", "op1")
    assert approval["approver_id"] == "jordan"
    assert approval["decision"] == "approved"
    assert approval["expires_at"] == clock.now + 600
    assert no_calls(handlers)


def test_approval_never_outlives_the_run(latch, clock, handlers):
    prepared = prepare_big_refund(latch)
    clock.now += 800
    assert is_code(latch.approve(prepared, make_approver()), Code.APPROVED, allowed=True)
    assert latch.store.get_approval("northwind", "op1")["expires_at"] == 1000.0 + 900
    assert no_calls(handlers)


def test_approver_without_permission(latch, handlers):
    prepared = prepare_big_refund(latch)
    approver = make_ctx(user_id="priya", run_id="other")
    assert is_code(latch.approve(prepared, approver), Code.APPROVER_NOT_ALLOWED)
    assert is_code(latch.deny(prepared, approver), Code.APPROVER_NOT_ALLOWED)
    assert latch.store.get_operation("northwind", "op1")["state"] == "prepared"
    assert no_calls(handlers)


def test_requester_cannot_approve_their_own_action(latch, handlers):
    prepared = prepare_big_refund(latch)
    self_approver = make_approver(user_id="sam")
    assert is_code(latch.approve(prepared, self_approver), Code.APPROVER_NOT_ALLOWED)
    assert latch.store.get_approval("northwind", "op1") is None
    assert no_calls(handlers)


def test_approver_from_another_org(latch, handlers):
    prepared = prepare_big_refund(latch)
    outsider = make_approver(org_id="contoso")
    assert is_code(latch.approve(prepared, outsider), Code.IDENTITY_MISMATCH)
    assert is_code(latch.deny(prepared, outsider), Code.IDENTITY_MISMATCH)
    assert no_calls(handlers)


def test_approve_twice(latch, handlers):
    prepared = prepare_big_refund(latch)
    assert is_code(latch.approve(prepared, make_approver()), Code.APPROVED, allowed=True)
    assert is_code(latch.approve(prepared, make_approver()), Code.APPROVAL_USED)
    assert no_calls(handlers)


def test_deny_after_approve(latch, handlers):
    prepared = prepare_big_refund(latch)
    latch.approve(prepared, make_approver())
    assert is_code(latch.deny(prepared, make_approver()), Code.APPROVAL_USED)
    assert latch.store.get_operation("northwind", "op1")["state"] == "approved"
    assert no_calls(handlers)


def test_deny_records_the_denial(latch, handlers):
    prepared = prepare_big_refund(latch)
    result = latch.deny(prepared, make_approver())
    assert is_code(result, Code.DENIED_BY_APPROVER)
    row = latch.store.get_operation("northwind", "op1")
    assert (row["state"], row["code"]) == ("denied", "DENIED_BY_APPROVER")
    assert latch.store.get_approval("northwind", "op1")["decision"] == "denied"
    assert is_code(latch.approve(prepared, make_approver()), Code.DENIED_BY_APPROVER)
    assert no_calls(handlers)


def test_approve_after_reserve_is_already_recorded(latch, handlers):
    prepared = prepare_big_refund(latch)
    latch.store.set_state("northwind", "op1", "reserved", "APPROVED", latch.clock())
    assert is_code(latch.approve(prepared, make_approver()), Code.ALREADY_RECORDED)
    assert no_calls(handlers)


def test_approve_a_changed_request_is_a_conflict(latch, handlers):
    prepared = prepare_big_refund(latch)
    forged = prepared.model_copy(update={"request_hash": "0" * 64})
    assert is_code(latch.approve(forged, make_approver()), Code.OPERATION_CONFLICT)
    assert latch.store.get_approval("northwind", "op1") is None
    assert no_calls(handlers)


def test_approve_an_action_that_needs_no_approval(latch, handlers):
    prepared = latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    result = latch.approve(prepared, make_approver())
    assert is_code(result, Code.APPROVAL_REQUIRED)
    assert result.explanation == "This action does not need approval."
    assert no_calls(handlers)


def test_approve_and_deny_are_audited(latch, handlers):
    first = prepare_big_refund(latch, "op1")
    second = prepare_big_refund(latch, "op2")
    latch.approve(first, make_approver())
    latch.deny(second, make_approver())
    rows = [r for r in latch.store.audit_rows("northwind") if r["event"] != "prepare"]
    assert [(r["event"], r["code"], r["user_id"]) for r in rows] == [
        ("approve", "APPROVED", "jordan"),
        ("deny", "DENIED_BY_APPROVER", "jordan"),
    ]
    assert no_calls(handlers)
