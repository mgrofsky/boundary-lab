from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from boundary_latch.engine import Latch
from boundary_latch.errors import ConfigError
from boundary_latch.models import Decision, PreparedAction, ToolSpec
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code, explain
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, LookupArgs, make_ctx, no_calls


def denied(result, code: Code) -> bool:
    return (
        isinstance(result, Decision)
        and not result.allowed
        and result.code == code
        and result.explanation == explain(code)
    )


def submissions(latch: Latch, run_id: str = "run1") -> int:
    return latch.store.touch_run("northwind", run_id, latch.clock())[1]


def test_allowed_lookup_returns_a_prepared_action(latch, handlers):
    result = latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    assert isinstance(result, PreparedAction)
    assert result.needs_approval is False
    assert result.code == Code.ALLOWED
    assert result.explanation == explain(Code.ALLOWED)
    assert result.arguments == {"account_id": "A1"}
    assert len(result.request_hash) == 64
    assert latch.store.get_operation("northwind", "op1")["state"] == "prepared"
    assert no_calls(handlers)


def test_send_message_needs_approval(latch, handlers):
    args = {"to": "help@northwind.example", "body": "hello"}
    result = latch.prepare(make_ctx(), "send_message", args, "op1")
    assert isinstance(result, PreparedAction)
    assert result.needs_approval is True
    assert result.code == Code.NEEDS_APPROVAL
    assert no_calls(handlers)


def test_refund_needs_approval_only_over_the_threshold(latch, handlers):
    small = latch.prepare(make_ctx(), "refund", {"account_id": "A1", "amount_minor": 5000}, "op1")
    large = latch.prepare(make_ctx(), "refund", {"account_id": "A1", "amount_minor": 5001}, "op2")
    assert small.needs_approval is False
    assert large.needs_approval is True
    assert no_calls(handlers)


def test_unknown_tool(latch, handlers):
    result = latch.prepare(make_ctx(), "delete_everything", {}, "op1")
    assert denied(result, Code.UNKNOWN_TOOL)
    assert result.operation_id == "op1"
    assert no_calls(handlers)


def test_extra_argument_field_is_invalid(latch, handlers):
    args = {"account_id": "A1", "role": "secret-admin-value"}
    result = latch.prepare(make_ctx(), "lookup_account", args, "op1")
    assert denied(result, Code.INVALID_ARGUMENTS)
    assert "secret-admin-value" not in result.explanation
    assert "role" not in result.explanation
    assert no_calls(handlers)


def test_missing_permission(latch, handlers):
    ctx = make_ctx(permissions={"messages:send"})
    result = latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op1")
    assert denied(result, Code.MISSING_PERMISSION)
    assert no_calls(handlers)


def test_other_account_is_access_denied(latch, handlers):
    result = latch.prepare(make_ctx(), "lookup_account", {"account_id": "A2"}, "op1")
    assert denied(result, Code.ACCESS_DENIED)
    assert no_calls(handlers)


def test_hook_raising_is_access_hook_failed(latch, handlers):
    result = latch.prepare(make_ctx(), "lookup_account", {"account_id": "BOOM"}, "op1")
    assert denied(result, Code.ACCESS_HOOK_FAILED)
    assert no_calls(handlers)


def test_recipient_outside_the_list(latch, handlers):
    args = {"to": "attacker@evil.example", "body": "hello"}
    result = latch.prepare(make_ctx(), "send_message", args, "op1")
    assert denied(result, Code.RECIPIENT_NOT_ALLOWED)
    assert no_calls(handlers)


def test_run_limit_counts_denials(latch, handlers):
    ctx = make_ctx()
    for n in range(19):
        assert isinstance(latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, f"op{n}"),
                          PreparedAction)
    assert denied(latch.prepare(ctx, "lookup_account", {"account_id": "A2"}, "op19"),
                  Code.ACCESS_DENIED)
    assert submissions(latch) == 20
    assert denied(latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op20"),
                  Code.RUN_LIMIT)
    assert submissions(latch) == 21
    assert denied(latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op21"),
                  Code.RUN_LIMIT)
    assert submissions(latch) == 22
    assert no_calls(handlers)


def test_run_expired(latch, clock, handlers):
    ctx = make_ctx()
    assert isinstance(latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op1"),
                      PreparedAction)
    clock.now += 901
    result = latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op2")
    assert denied(result, Code.RUN_EXPIRED)
    assert no_calls(handlers)


def test_same_operation_id_with_other_args_is_a_conflict(latch, handlers):
    ctx = make_ctx()
    first = latch.prepare(ctx, "refund", {"account_id": "A1", "amount_minor": 100}, "op1")
    assert isinstance(first, PreparedAction)
    second = latch.prepare(ctx, "refund", {"account_id": "A1", "amount_minor": 200}, "op1")
    assert denied(second, Code.OPERATION_CONFLICT)
    assert no_calls(handlers)


def test_same_operation_id_and_args_resumes(latch, handlers):
    ctx = make_ctx()
    first = latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op1")
    count = submissions(latch)
    again = latch.prepare(ctx, "lookup_account", {"account_id": "A1"}, "op1")
    assert isinstance(again, PreparedAction)
    assert again.request_hash == first.request_hash
    assert again.arguments == first.arguments
    assert submissions(latch) == count
    assert no_calls(handlers)


def test_every_prepare_is_audited_without_arguments(latch):
    latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    latch.prepare(make_ctx(), "lookup_account", {"account_id": "A2"}, "op2")
    rows = latch.store.audit_rows("northwind")
    assert [(r["event"], r["code"]) for r in rows] == [
        ("prepare", "ALLOWED"),
        ("prepare", "ACCESS_DENIED"),
    ]
    assert "A2" not in repr(rows)


def test_register_rejects_args_without_extra_forbid(clock):
    class Loose(BaseModel):
        account_id: str

    engine = Latch(parse_policy(POLICY), SQLiteStore(), clock=clock)
    with pytest.raises(ConfigError):
        engine.register(
            ToolSpec(name="lookup_account", version="1", args_model=Loose, handler=MagicMock())
        )


def test_register_rejects_a_tool_without_policy(clock):
    engine = Latch(parse_policy(POLICY), SQLiteStore(), clock=clock)
    with pytest.raises(ConfigError):
        engine.register(
            ToolSpec(name="wire_money", version="1", args_model=LookupArgs, handler=MagicMock())
        )


def test_register_rejects_a_duplicate(latch):
    with pytest.raises(ConfigError):
        latch.register(
            ToolSpec(name="lookup_account", version="1", args_model=LookupArgs, handler=MagicMock())
        )
    assert [spec.name for spec in latch.tool_specs()] == ["send_message", "lookup_account", "refund"]
