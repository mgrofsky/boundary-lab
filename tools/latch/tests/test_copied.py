from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from boundary_latch.engine import Latch
from boundary_latch.errors import ConfigError
from boundary_latch.models import Decision, PreparedAction, ToolSpec, UserContext
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code, explain
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, LookupArgs, RefundArgs, account_hook, make_ctx

REFUND_LINE = 'require_permission = "refunds:create"'
LOOKUP_LINE = 'require_permission = "accounts:read"'
DENY_POLICY = POLICY.replace(REFUND_LINE, REFUND_LINE + '\ncopied_arguments = "deny"')
APPROVE_POLICY = POLICY.replace(
    LOOKUP_LINE,
    LOOKUP_LINE + '\napprove_permission = "accounts:approve"\ncopied_arguments = "approve"',
)
REFUND_ARGS = {"account_id": "A1", "amount_minor": 100}
LOOKUP_ARGS = {"account_id": "A1"}


@dataclass
class FakeReport:
    copied: frozenset


def make_latch(clock, text):
    handlers = {"refund": MagicMock(return_value="refunded"),
                "lookup": MagicMock(return_value={"balance_minor": 1})}
    engine = Latch(parse_policy(text), SQLiteStore(), clock=clock)
    engine.register(ToolSpec(name="refund", version="1", args_model=RefundArgs,
                             handler=handlers["refund"], access_hook=account_hook))
    engine.register(ToolSpec(name="lookup_account", version="1", args_model=LookupArgs,
                             handler=handlers["lookup"], access_hook=account_hook))
    return engine, handlers


@pytest.fixture
def deny_latch(clock):
    return make_latch(clock, DENY_POLICY)


@pytest.fixture
def approve_latch(clock):
    return make_latch(clock, APPROVE_POLICY)


def test_user_context_has_no_copied_field():
    with pytest.raises(ValidationError):
        UserContext(user_id="u", org_id="o", permissions=frozenset(), run_id="r",
                    marked_fields=frozenset({"x"}))
    assert "copied" not in UserContext.model_fields


def test_default_policy_allows_copied_fields(latch, handlers):
    result = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1",
                           copied=["account_id"])
    assert isinstance(result, PreparedAction)
    assert result.copied == frozenset({"account_id"})
    assert result.needs_approval is False


def test_deny_policy_blocks_a_copied_argument(deny_latch):
    engine, handlers = deny_latch
    result = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1", copied=["account_id"])
    assert isinstance(result, Decision)
    assert result.allowed is False
    assert result.code == Code.COPIED_ARGUMENTS
    assert result.explanation == explain(Code.COPIED_ARGUMENTS)
    assert engine.store.get_operation("northwind", "op1") is None
    assert handlers["refund"].call_count == 0


def test_copied_explanation_text():
    assert explain(Code.COPIED_ARGUMENTS) == (
        "One or more argument values were copied from text the model read, "
        "and this tool's policy blocks that."
    )


def test_nested_paths_count_by_their_top_level_field(deny_latch):
    engine, _ = deny_latch
    result = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1",
                            copied=["account_id.inner", "amount_minor[0]"])
    assert result.code == Code.COPIED_ARGUMENTS


def test_paths_for_other_fields_are_dropped(deny_latch):
    engine, _ = deny_latch
    result = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1", copied=["to", "items[0].sku"])
    assert isinstance(result, PreparedAction)
    assert result.copied == frozenset()


def test_an_object_with_a_copied_attribute_is_accepted(deny_latch):
    engine, _ = deny_latch
    report = FakeReport(copied=frozenset({"account_id"}))
    result = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1", copied=report)
    assert result.code == Code.COPIED_ARGUMENTS


def test_a_single_string_is_one_path(deny_latch):
    engine, _ = deny_latch
    result = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1", copied="account_id")
    assert result.code == Code.COPIED_ARGUMENTS


def test_copied_is_keyword_only(latch):
    with pytest.raises(TypeError):
        latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1", ["account_id"])


def test_execute_rechecks_the_copied_paths_on_the_prepared_action(deny_latch):
    engine, handlers = deny_latch
    prepared = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1")
    assert isinstance(prepared, PreparedAction)
    forged = prepared.model_copy(update={"copied": frozenset({"account_id"})})
    forged._args_json = prepared._args_json
    result = engine.execute(forged, make_ctx())
    assert result.outcome == "denied"
    assert result.code == Code.COPIED_ARGUMENTS
    assert handlers["refund"].call_count == 0


def test_dropping_copied_paths_from_the_prepared_action_is_a_conflict(clock):
    engine, handlers = make_latch(clock, POLICY)
    prepared = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1", copied=["account_id"])
    forged = prepared.model_copy(update={"copied": frozenset()})
    forged._args_json = prepared._args_json
    result = engine.execute(forged, make_ctx())
    assert result.code == Code.OPERATION_CONFLICT
    assert handlers["refund"].call_count == 0


def test_approve_policy_needs_approval_for_copied_values(approve_latch):
    engine, handlers = approve_latch
    prepared = engine.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1",
                              copied=["account_id"])
    assert isinstance(prepared, PreparedAction)
    assert prepared.needs_approval is True
    assert prepared.code == Code.NEEDS_APPROVAL
    blocked = engine.execute(prepared, make_ctx())
    assert blocked.code == Code.APPROVAL_REQUIRED
    approver = make_ctx(user_id="jordan", permissions={"accounts:approve"}, run_id="r2")
    assert engine.approve(prepared, approver).code == Code.APPROVED
    result = engine.execute(prepared, make_ctx())
    assert result.code == Code.EXECUTED
    assert handlers["lookup"].call_count == 1


def test_approve_policy_without_copied_values_runs_directly(approve_latch):
    engine, _ = approve_latch
    prepared = engine.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1")
    assert prepared.needs_approval is False
    assert engine.execute(prepared, make_ctx()).code == Code.EXECUTED


def test_approve_policy_needs_approve_permission():
    text = '[tools.lookup_account]\nrequire_permission = "a"\ncopied_arguments = "approve"\n'
    with pytest.raises(ConfigError, match="approve_permission"):
        parse_policy(text)


def test_policy_rejects_other_copied_values():
    with pytest.raises(ConfigError):
        parse_policy(POLICY.replace(REFUND_LINE, REFUND_LINE + '\ncopied_arguments = "maybe"'))


def test_copied_paths_are_stored_on_the_operation(latch):
    latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1", copied=["account_id"])
    status = latch.operation_status("northwind", "op1")
    assert status.copied == frozenset({"account_id"})


def test_repeat_prepare_with_a_new_copied_path_is_a_conflict(latch):
    first = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1")
    assert isinstance(first, PreparedAction)
    again = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1",
                          copied=["account_id"])
    assert isinstance(again, Decision)
    assert again.code == Code.OPERATION_CONFLICT


def test_repeat_prepare_with_fewer_copied_paths_keeps_the_stored_set(approve_latch):
    engine, _ = approve_latch
    first = engine.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1",
                           copied=["account_id"])
    again = engine.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1")
    assert isinstance(again, PreparedAction)
    assert again.copied == first.copied == frozenset({"account_id"})
    assert again.needs_approval is True


def test_copied_paths_are_not_part_of_the_request_hash(latch):
    a = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op1")
    b = latch.prepare(make_ctx(run_id="run2"), "lookup_account", LOOKUP_ARGS, "op1x",
                      copied=["account_id"])
    assert a.request_hash != b.request_hash  # different run and id
    c = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op2", copied=["account_id"])
    d = latch.prepare(make_ctx(), "lookup_account", LOOKUP_ARGS, "op2")
    assert c.request_hash == d.request_hash

