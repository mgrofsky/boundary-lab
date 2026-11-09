"""Run lifetime and idle time."""

from unittest.mock import MagicMock

import pytest

from boundary_latch.engine import Latch
from boundary_latch.errors import ConfigError
from boundary_latch.models import Decision, PreparedAction, ToolSpec
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, LookupArgs, SendMessageArgs, account_hook, make_approver, make_ctx

LOOKUP = {"account_id": "A1"}
MESSAGE = {"to": "help@northwind.example", "body": "hello"}


def make_latch(clock, limits: str):
    engine = Latch(parse_policy(f"[limits]\n{limits}\n{POLICY}"), SQLiteStore(), clock=clock)
    engine.register(ToolSpec(name="lookup_account", version="1", args_model=LookupArgs,
                             handler=MagicMock(return_value=1), access_hook=account_hook))
    engine.register(ToolSpec(name="send_message", version="1", args_model=SendMessageArgs,
                             handler=MagicMock(return_value="sent")))
    return engine


def lookup(engine, op):
    return engine.prepare(make_ctx(), "lookup_account", LOOKUP, op)


def test_policy_defaults():
    limits = parse_policy(POLICY).limits
    assert limits.run_lifetime_seconds == 900
    assert limits.run_idle_seconds == 0


@pytest.mark.parametrize("line", ["run_lifetime_seconds = -1", "run_idle_seconds = -1"])
def test_negative_times_are_rejected(line):
    with pytest.raises(ConfigError):
        parse_policy(f"[limits]\n{line}\n{POLICY}")


def test_zero_lifetime_means_no_limit(clock):
    engine = make_latch(clock, "run_lifetime_seconds = 0")
    first = lookup(engine, "op1")
    assert isinstance(first, PreparedAction)
    clock.now += 10_000_000
    assert engine.execute(first, make_ctx()).code == Code.EXECUTED
    assert isinstance(lookup(engine, "op2"), PreparedAction)


def test_zero_lifetime_approval_still_expires(clock):
    engine = make_latch(clock, "run_lifetime_seconds = 0")
    prepared = engine.prepare(make_ctx(), "send_message", MESSAGE, "op1")
    clock.now += 5000
    assert engine.approve(prepared, make_approver()).code == Code.APPROVED
    clock.now += 601
    assert engine.execute(prepared, make_ctx()).code == Code.APPROVAL_EXPIRED


def test_idle_time_expires_a_quiet_run(clock):
    engine = make_latch(clock, "run_lifetime_seconds = 0\nrun_idle_seconds = 60")
    assert isinstance(lookup(engine, "op1"), PreparedAction)
    clock.now += 59
    assert isinstance(lookup(engine, "op2"), PreparedAction)
    clock.now += 59
    assert isinstance(lookup(engine, "op3"), PreparedAction)
    clock.now += 60
    expired = lookup(engine, "op4")
    assert isinstance(expired, Decision)
    assert expired.code == Code.RUN_EXPIRED
    # An expired call does not count as activity.
    clock.now += 1
    assert lookup(engine, "op5").code == Code.RUN_EXPIRED


def test_idle_time_is_checked_at_execute(clock):
    engine = make_latch(clock, "run_idle_seconds = 60")
    prepared = lookup(engine, "op1")
    clock.now += 60
    result = engine.execute(prepared, make_ctx())
    assert result.outcome == "denied"
    assert result.code == Code.RUN_EXPIRED


def test_execute_counts_as_activity(clock):
    engine = make_latch(clock, "run_idle_seconds = 60")
    first, second = lookup(engine, "op1"), lookup(engine, "op2")
    clock.now += 50
    assert engine.execute(first, make_ctx()).code == Code.EXECUTED
    clock.now += 50
    assert engine.execute(second, make_ctx()).code == Code.EXECUTED


def test_idle_time_is_checked_at_approve(clock):
    engine = make_latch(clock, "run_idle_seconds = 60")
    prepared = engine.prepare(make_ctx(), "send_message", MESSAGE, "op1")
    clock.now += 60
    assert engine.approve(prepared, make_approver()).code == Code.RUN_EXPIRED


def test_lifetime_still_applies_with_idle_time(clock):
    engine = make_latch(clock, "run_lifetime_seconds = 100\nrun_idle_seconds = 60")
    for _ in range(3):
        assert isinstance(lookup(engine, f"op{clock.now}"), PreparedAction)
        clock.now += 40
    assert lookup(engine, "late").code == Code.RUN_EXPIRED


def test_the_run_records_its_last_touch(clock):
    engine = make_latch(clock, "run_idle_seconds = 60")
    lookup(engine, "op1")
    clock.now += 30
    lookup(engine, "op2")
    assert engine.store.last_touch("northwind", "run1") == clock.now
