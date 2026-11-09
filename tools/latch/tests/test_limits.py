"""Run limits: proposals counted at prepare, actions counted at execute."""

import threading
from unittest.mock import MagicMock

import pytest

from boundary_latch.engine import Latch
from boundary_latch.errors import ConfigError
from boundary_latch.models import Decision, PreparedAction, ToolSpec
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, LookupArgs, account_hook, make_ctx

LOOKUP = {"account_id": "A1"}


def make_latch(clock, limits: str, handler=None):
    engine = Latch(parse_policy(f"[limits]\n{limits}\n{POLICY}"), SQLiteStore(), clock=clock)
    handler = handler or MagicMock(return_value={"balance_minor": 1})
    engine.register(ToolSpec(name="lookup_account", version="1", args_model=LookupArgs,
                             handler=handler, access_hook=account_hook))
    return engine, handler


def prepare(engine, op, ctx=None):
    result = engine.prepare(ctx or make_ctx(), "lookup_account", LOOKUP, op)
    assert isinstance(result, PreparedAction), result
    return result


def test_proposals_are_not_limited_by_default(clock):
    engine, _ = make_latch(clock, "max_actions_per_run = 2")
    for n in range(30):
        prepare(engine, f"op{n}")


def test_max_proposals_per_run_is_counted_at_prepare(clock):
    engine, handler = make_latch(clock, "max_proposals_per_run = 3")
    for n in range(3):
        prepare(engine, f"op{n}")
    # Preparing a stored operation again does not count.
    prepare(engine, "op0")
    blocked = engine.prepare(make_ctx(), "lookup_account", LOOKUP, "op3")
    assert isinstance(blocked, Decision)
    assert blocked.code == Code.RUN_LIMIT
    assert handler.call_count == 0
    # Another run has its own count.
    prepare(engine, "other", make_ctx(run_id="run2"))


def test_max_proposals_counts_denied_proposals(clock):
    engine, _ = make_latch(clock, "max_proposals_per_run = 2")
    denied = engine.prepare(make_ctx(), "lookup_account", {"account_id": "A2"}, "op1")
    assert denied.code == Code.ACCESS_DENIED
    prepare(engine, "op2")
    assert engine.prepare(make_ctx(), "lookup_account", LOOKUP, "op3").code == Code.RUN_LIMIT


def test_max_proposals_must_be_at_least_one():
    with pytest.raises(ConfigError):
        parse_policy(f"[limits]\nmax_proposals_per_run = 0\n{POLICY}")


def test_max_actions_per_run_is_counted_at_execute(clock):
    engine, handler = make_latch(clock, "max_actions_per_run = 2")
    prepared = [prepare(engine, f"op{n}") for n in range(3)]
    assert engine.execute(prepared[0], make_ctx()).code == Code.EXECUTED
    assert engine.execute(prepared[1], make_ctx()).code == Code.EXECUTED
    result = engine.execute(prepared[2], make_ctx())
    assert result.outcome == "denied"
    assert result.code == Code.RUN_LIMIT
    assert handler.call_count == 2
    status = engine.operation_status("northwind", "op2")
    assert (status.state, status.code) == ("denied", Code.RUN_LIMIT)
    last = engine.store.audit_rows("northwind")[-1]
    assert (last["event"], last["code"], last["outcome"]) == ("execute", "RUN_LIMIT", "denied")
    # The refused operation stays refused.
    again = engine.execute(prepared[2], make_ctx())
    assert again.code == Code.RUN_LIMIT
    assert handler.call_count == 2


def test_repeat_prepare_after_a_run_limit_refusal_is_closed(clock):
    engine, _ = make_latch(clock, "max_actions_per_run = 1")
    first, second = prepare(engine, "op1"), prepare(engine, "op2")
    engine.execute(first, make_ctx())
    assert engine.execute(second, make_ctx()).code == Code.RUN_LIMIT
    again = engine.prepare(make_ctx(), "lookup_account", LOOKUP, "op2")
    assert isinstance(again, Decision)
    assert again.code == Code.OPERATION_CLOSED


def test_failed_and_uncertain_runs_count_toward_the_limit(clock):
    def boom(args, info):
        raise RuntimeError("down")

    engine, _ = make_latch(clock, "max_actions_per_run = 1", handler=boom)
    first, second = prepare(engine, "op1"), prepare(engine, "op2")
    assert engine.execute(first, make_ctx()).code == Code.UNCERTAIN
    assert engine.execute(second, make_ctx()).code == Code.RUN_LIMIT


def test_two_threads_cannot_both_pass_the_limit(clock):
    gate = threading.Event()

    def slow(args, info):
        gate.wait(2)
        return "ok"

    handler = MagicMock(side_effect=slow)
    engine, _ = make_latch(clock, "max_actions_per_run = 1", handler=handler)
    prepared = [prepare(engine, f"op{n}") for n in range(2)]
    results = [None, None]

    def run(i):
        results[i] = engine.execute(prepared[i], make_ctx())

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    gate.set()
    for thread in threads:
        thread.join(5)
    codes = sorted(str(result.code) for result in results)
    assert codes == ["EXECUTED", "RUN_LIMIT"]
    assert handler.call_count == 1
