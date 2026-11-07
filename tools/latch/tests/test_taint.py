from unittest.mock import MagicMock

import pytest

from boundary_latch.engine import Latch
from boundary_latch.errors import ConfigError
from boundary_latch.models import Decision, PreparedAction, ToolSpec, UserContext
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code, explain
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, RefundArgs, account_hook, make_ctx

REFUND_LINE = 'require_permission = "refunds:create"'
DENY_POLICY = POLICY.replace(REFUND_LINE, REFUND_LINE + '\ntainted_arguments = "deny"')
REFUND_ARGS = {"account_id": "A1", "amount_minor": 100}


def tainted_ctx(*fields: str) -> UserContext:
    base = make_ctx()
    return UserContext(
        user_id=base.user_id,
        org_id=base.org_id,
        permissions=base.permissions,
        run_id=base.run_id,
        tainted_fields=frozenset(fields),
    )


@pytest.fixture
def deny_latch(clock):
    handler = MagicMock(return_value="refunded")
    engine = Latch(parse_policy(DENY_POLICY), SQLiteStore(), clock=clock)
    engine.register(
        ToolSpec(
            name="refund",
            version="1",
            args_model=RefundArgs,
            handler=handler,
            access_hook=account_hook,
        )
    )
    return engine, handler


def test_default_policy_allows_tainted_fields(latch, handlers):
    result = latch.prepare(tainted_ctx("account_id"), "lookup_account", {"account_id": "A1"}, "op1")
    assert isinstance(result, PreparedAction)


def test_deny_policy_blocks_a_tainted_argument(deny_latch):
    engine, handler = deny_latch
    result = engine.prepare(tainted_ctx("account_id"), "refund", REFUND_ARGS, "op1")
    assert isinstance(result, Decision)
    assert result.allowed is False
    assert result.code == Code.TAINTED_ARGUMENTS
    assert result.explanation == explain(Code.TAINTED_ARGUMENTS)
    assert engine.store.get_operation("northwind", "op1") is None
    assert handler.call_count == 0


def test_deny_policy_ignores_fields_this_tool_does_not_take(deny_latch):
    engine, _ = deny_latch
    result = engine.prepare(tainted_ctx("to"), "refund", REFUND_ARGS, "op1")
    assert isinstance(result, PreparedAction)


def test_execute_rechecks_taint_with_the_context_it_is_given(deny_latch):
    engine, handler = deny_latch
    prepared = engine.prepare(make_ctx(), "refund", REFUND_ARGS, "op1")
    assert isinstance(prepared, PreparedAction)
    result = engine.execute(prepared, tainted_ctx("account_id"))
    assert result.outcome == "denied"
    assert result.code == Code.TAINTED_ARGUMENTS
    assert handler.call_count == 0


def test_policy_rejects_other_taint_values():
    with pytest.raises(ConfigError):
        parse_policy(POLICY.replace(REFUND_LINE, REFUND_LINE + '\ntainted_arguments = "maybe"'))
