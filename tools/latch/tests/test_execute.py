import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict

from boundary_latch.errors import ConfigError, StorageError, ToolFailed
from boundary_latch.models import CallInfo, ExecutionResult, PreparedAction, ToolSpec
from boundary_latch.reasons import Code, explain
from boundary_latch.store import SQLiteStore

from .conftest import build_latch, make_approver, make_ctx, no_calls

TASK_CTX = {"messages:send", "accounts:read", "refunds:create", "tasks:run"}


class Details(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    details: Details


TASK_ARGS = {"title": "close ticket", "details": {"note": "original"}}


def task_spec(handler, timeout_seconds: float = 30.0) -> ToolSpec:
    return ToolSpec(
        name="task",
        version="1",
        args_model=TaskArgs,
        handler=handler,
        timeout_seconds=timeout_seconds,
    )


def task_ctx():
    return make_ctx(permissions=TASK_CTX)


def prepare_task(latch, operation_id="op1") -> PreparedAction:
    prepared = latch.prepare(task_ctx(), "task", TASK_ARGS, operation_id)
    assert isinstance(prepared, PreparedAction)
    return prepared


def prepare_lookup(latch, operation_id="op1") -> PreparedAction:
    prepared = latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, operation_id)
    assert isinstance(prepared, PreparedAction)
    return prepared


def prepare_big_refund(latch, operation_id="op1") -> PreparedAction:
    args = {"account_id": "A1", "amount_minor": 6000}
    prepared = latch.prepare(make_ctx(), "refund", args, operation_id)
    assert isinstance(prepared, PreparedAction) and prepared.needs_approval
    return prepared


def check(result: ExecutionResult, outcome: str, code: Code, replayed: bool = False) -> None:
    assert result.outcome == outcome
    assert result.code == code
    assert result.replayed is replayed
    assert result.explanation == explain(code)


def test_allowed_action_executes_once(latch, handlers):
    prepared = prepare_lookup(latch)
    result = latch.execute(prepared, make_ctx())
    check(result, "succeeded", Code.EXECUTED)
    assert result.value == {"balance_minor": 10000}
    assert result.operation_id == "op1"
    handler = handlers["lookup_account"]
    assert handler.call_count == 1
    args, info = handler.call_args.args
    assert args == {"account_id": "A1"}
    assert info == CallInfo(
        operation_id="op1",
        run_id="run1",
        user_id="sam",
        org_id="northwind",
        tool="lookup_account",
        tool_version="1",
    )
    again = latch.execute(prepared, make_ctx())
    check(again, "succeeded", Code.EXECUTED, replayed=True)
    assert again.value is None
    assert handler.call_count == 1


def test_permission_revoked_before_execute(latch, handlers):
    prepared = prepare_lookup(latch)
    revoked = make_ctx(permissions={"messages:send"})
    check(latch.execute(prepared, revoked), "denied", Code.MISSING_PERMISSION)
    assert latch.store.get_operation("northwind", "op1")["state"] == "prepared"
    assert no_calls(handlers)


def test_forged_context_is_identity_mismatch(latch, handlers):
    prepared = prepare_lookup(latch)
    forged = make_ctx(user_id="priya")
    check(latch.execute(prepared, forged), "denied", Code.IDENTITY_MISMATCH)
    other_run = make_ctx(run_id="run2")
    check(latch.execute(prepared, other_run), "denied", Code.IDENTITY_MISMATCH)
    assert no_calls(handlers)


def test_approval_required_before_execute(latch, handlers):
    prepared = prepare_big_refund(latch)
    check(latch.execute(prepared, make_ctx()), "denied", Code.APPROVAL_REQUIRED)
    assert no_calls(handlers)


def test_approval_then_execute(latch, handlers):
    prepared = prepare_big_refund(latch)
    latch.approve(prepared, make_approver())
    check(latch.execute(prepared, make_ctx()), "succeeded", Code.EXECUTED)
    assert handlers["refund"].call_count == 1
    assert latch.store.get_approval("northwind", "op1")["consumed_at"] is not None


def test_denied_action_does_not_execute(latch, handlers):
    prepared = prepare_big_refund(latch)
    latch.deny(prepared, make_approver())
    check(latch.execute(prepared, make_ctx()), "denied", Code.DENIED_BY_APPROVER)
    assert no_calls(handlers)


def test_approval_expires(latch, clock, handlers):
    prepared = prepare_big_refund(latch)
    latch.approve(prepared, make_approver())
    clock.now += 601
    check(latch.execute(prepared, make_ctx()), "denied", Code.APPROVAL_EXPIRED)
    assert no_calls(handlers)


def test_run_expires(latch, clock, handlers):
    prepared = prepare_lookup(latch)
    clock.now += 901
    check(latch.execute(prepared, make_ctx()), "denied", Code.RUN_EXPIRED)
    assert no_calls(handlers)


def test_handler_gets_the_snapshot_not_the_mutated_copy(clock):
    handler = MagicMock(return_value="done")
    latch = build_latch(clock, task_spec(handler))
    prepared = prepare_task(latch)
    copy = prepared.arguments
    copy["details"]["note"] = "tampered"
    copy["title"] = "tampered"
    check(latch.execute(prepared, task_ctx()), "succeeded", Code.EXECUTED)
    args, _ = handler.call_args.args
    assert args == TASK_ARGS


def test_tool_failed_records_failed(latch, handlers):
    handlers["lookup_account"].side_effect = ToolFailed("account locked")
    prepared = prepare_lookup(latch)
    check(latch.execute(prepared, make_ctx()), "failed", Code.FAILED)
    check(latch.execute(prepared, make_ctx()), "failed", Code.FAILED, replayed=True)
    assert handlers["lookup_account"].call_count == 1


def test_other_exception_records_uncertain(latch, handlers):
    handlers["lookup_account"].side_effect = ValueError("boom")
    prepared = prepare_lookup(latch)
    check(latch.execute(prepared, make_ctx()), "uncertain", Code.UNCERTAIN)
    again = latch.execute(prepared, make_ctx())
    check(again, "uncertain", Code.UNCERTAIN, replayed=True)
    assert again.value is None
    assert handlers["lookup_account"].call_count == 1


def test_timeout_records_uncertain(clock):
    release = threading.Event()
    calls = []

    def slow(args, info):
        calls.append(info.operation_id)
        release.wait(2)
        return "late"

    latch = build_latch(clock, task_spec(slow, timeout_seconds=0.1))
    prepared = prepare_task(latch)
    start = time.perf_counter()
    try:
        result = latch.execute(prepared, task_ctx())
        elapsed = time.perf_counter() - start
    finally:
        release.set()
    check(result, "uncertain", Code.UNCERTAIN)
    assert elapsed < 0.5
    assert latch.store.get_operation("northwind", "op1")["state"] == "uncertain"
    check(latch.execute(prepared, task_ctx()), "uncertain", Code.UNCERTAIN, replayed=True)
    assert calls == ["op1"]


def test_execute_refuses_an_async_handler(clock):
    calls = []

    async def handler(args, info):
        calls.append(info)

    latch = build_latch(clock, task_spec(handler))
    prepared = prepare_task(latch)
    with pytest.raises(ConfigError):
        latch.execute(prepared, task_ctx())
    assert calls == []
    assert latch.store.get_operation("northwind", "op1")["state"] == "prepared"


async def test_aexecute_runs_an_async_handler(clock):
    calls = []

    async def handler(args, info):
        calls.append(args)
        return "async done"

    latch = build_latch(clock, task_spec(handler))
    prepared = prepare_task(latch)
    result = await latch.aexecute(prepared, task_ctx())
    check(result, "succeeded", Code.EXECUTED)
    assert result.value == "async done"
    check(await latch.aexecute(prepared, task_ctx()), "succeeded", Code.EXECUTED, replayed=True)
    assert calls == [TASK_ARGS]


async def test_aexecute_runs_a_sync_handler(latch, handlers):
    prepared = prepare_lookup(latch)
    result = await latch.aexecute(prepared, make_ctx())
    check(result, "succeeded", Code.EXECUTED)
    assert handlers["lookup_account"].call_count == 1


async def test_aexecute_timeout_records_uncertain(clock):
    async def slow(args, info):
        await asyncio.sleep(2)

    latch = build_latch(clock, task_spec(slow, timeout_seconds=0.1))
    prepared = prepare_task(latch)
    start = time.perf_counter()
    result = await latch.aexecute(prepared, task_ctx())
    assert time.perf_counter() - start < 0.5
    check(result, "uncertain", Code.UNCERTAIN)


async def test_aexecute_tool_failed_records_failed(clock):
    async def handler(args, info):
        raise ToolFailed("nope")

    latch = build_latch(clock, task_spec(handler))
    prepared = prepare_task(latch)
    check(await latch.aexecute(prepared, task_ctx()), "failed", Code.FAILED)


def test_deny_after_execute_is_already_recorded(latch, handlers):
    prepared = prepare_big_refund(latch)
    latch.approve(prepared, make_approver())
    check(latch.execute(prepared, make_ctx()), "succeeded", Code.EXECUTED)
    denial = latch.deny(prepared, make_approver())
    assert denial.code == Code.ALREADY_RECORDED
    assert handlers["refund"].call_count == 1


def test_concurrent_execute_runs_the_handler_once(clock, tmp_path):
    handler = MagicMock(return_value="done")
    store = SQLiteStore(tmp_path / "latch.sqlite")
    latch = build_latch(clock, task_spec(handler), store=store)
    prepared = prepare_task(latch)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        result = latch.execute(prepared, task_ctx())
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(results) == 8
    assert sum(not r.replayed for r in results) == 1
    assert handler.call_count == 1
    store.close()


def test_storage_failure_dispatches_nothing(latch, handlers, monkeypatch):
    prepared = prepare_lookup(latch)

    def boom(*args, **kwargs):
        raise StorageError("disk full")

    monkeypatch.setattr(latch.store, "reserve", boom)
    with pytest.raises(StorageError):
        latch.execute(prepared, make_ctx())
    assert no_calls(handlers)
