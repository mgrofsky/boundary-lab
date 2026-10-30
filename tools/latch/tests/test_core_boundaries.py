"""Regression coverage for approvals across engine and operation lifecycles."""

import threading

import pytest
from pydantic import BaseModel, ConfigDict

from boundary_latch import Latch, PreparedAction, SQLiteStore, ToolSpec
from boundary_latch.errors import StorageError
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code

from .conftest import make_approver, make_ctx


class PaymentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_minor: int


def payment_engine(clock, *, store=None, approval="never", version="1", hook=None):
    policy = parse_policy(f'''
        [tools.payment]
        require_permission = "payments:create"
        approve_permission = "payments:approve"
        approval.mode = "{approval}"
    ''')
    calls = []

    def pay(args, info):
        calls.append((args, info))
        return "paid"

    engine = Latch(policy, store or SQLiteStore(), clock=clock)
    engine.register(ToolSpec(name="payment", version=version, args_model=PaymentArgs,
                             handler=pay, access_hook=hook))
    return engine, calls


def payer(run_id="run1"):
    return make_ctx(permissions={"payments:create"}, run_id=run_id)


def payment(engine, operation_id="payment1", run_id="run1"):
    action = engine.prepare(payer(run_id), "payment", {"amount_minor": 100}, operation_id)
    assert isinstance(action, PreparedAction)
    return action


@pytest.mark.parametrize("change", ["policy", "version"])
def test_execute_rejects_action_from_an_outdated_engine(clock, change):
    old, _ = payment_engine(clock)
    action = payment(old)
    current, calls = payment_engine(clock, store=old.store,
                                    approval="always" if change == "policy" else "never",
                                    version="2" if change == "version" else "1")
    assert current.execute(action, payer()).code == Code.OPERATION_CONFLICT
    assert calls == []


async def test_aexecute_rejects_action_from_an_outdated_engine(clock):
    old, _ = payment_engine(clock)
    action = payment(old)
    current, calls = payment_engine(clock, store=old.store, approval="always")
    assert (await current.aexecute(action, payer())).code == Code.OPERATION_CONFLICT
    assert calls == []


def test_approve_rejects_action_from_an_outdated_engine(clock):
    old, _ = payment_engine(clock, approval="always")
    action = payment(old)
    current, calls = payment_engine(clock, store=old.store, approval="always", version="2")
    assert current.approve(action, make_approver()).code == Code.OPERATION_CONFLICT
    assert current.store.get_approval("northwind", action.operation_id) is None
    assert calls == []


@pytest.mark.parametrize("change", ["approval_flag", "arguments"])
def test_execute_rejects_changed_approval_binding(clock, change):
    engine, calls = payment_engine(clock, approval="always")
    action = payment(engine)
    if change == "approval_flag":
        action = action.model_copy(update={"needs_approval": False})
    else:
        assert engine.approve(action, make_approver()).allowed
        action._args_json = '{"amount_minor":9999}'
    assert engine.execute(action, payer()).code == Code.OPERATION_CONFLICT
    assert calls == []


def test_existing_operation_cannot_move_to_a_new_run(clock):
    engine, calls = payment_engine(clock)
    payment(engine)
    clock.now += 901
    resumed = engine.prepare(payer("fresh"), "payment", {"amount_minor": 100}, "payment1")
    assert resumed.code == Code.OPERATION_CONFLICT
    assert calls == []


def test_approval_and_concurrent_dispatch_keep_the_terminal_state(clock, monkeypatch):
    engine, calls = payment_engine(clock, approval="always")
    action = payment(engine)
    inserted = engine.store.record_approval
    finished = threading.Event()
    results = []
    threads = []

    def execute():
        results.append(engine.execute(action, payer()))
        finished.set()

    def insert_and_dispatch(*args, **kwargs):
        result = inserted(*args, **kwargs)
        thread = threading.Thread(target=execute)
        threads.append(thread)
        thread.start()
        # Force the vulnerable interleaving only when the insert was not transactional.
        # An atomic decision keeps execute waiting on the store lock until it commits.
        if not engine.store._conn.in_transaction:
            assert finished.wait(2)
        return result

    monkeypatch.setattr(engine.store, "record_approval", insert_and_dispatch)
    assert engine.approve(action, make_approver()).allowed
    for thread in threads:
        thread.join(2)
    assert finished.is_set()
    assert results[0].outcome == "succeeded"
    assert engine.operation_status("northwind", action.operation_id).state == "succeeded"
    assert engine.execute(action, payer()).outcome == "succeeded"
    assert len(calls) == 1


def test_run_deadline_is_checked_after_the_access_hook(clock):
    slow = [False]

    def access(ctx, args):
        if slow[0]:
            clock.now += 901
        return True

    engine, calls = payment_engine(clock, hook=access)
    action = payment(engine)
    slow[0] = True
    assert engine.execute(action, payer()).code == Code.RUN_EXPIRED
    assert calls == []


def test_run_deadline_is_checked_after_acquiring_sqlite_write_lock(clock):
    engine, calls = payment_engine(clock)
    action = payment(engine)

    def lock_acquired(sql):
        if sql == "BEGIN IMMEDIATE":
            clock.now += 901

    engine.store._conn.set_trace_callback(lock_acquired)
    assert engine.execute(action, payer()).code == Code.RUN_EXPIRED
    assert calls == []


def test_prepare_deadline_is_checked_after_the_access_hook(clock):
    def slow_access(ctx, args):
        clock.now += 901
        return True

    engine, calls = payment_engine(clock, hook=slow_access)
    result = engine.prepare(payer(), "payment", {"amount_minor": 100}, "payment1")
    assert result.code == Code.RUN_EXPIRED
    assert engine.operation_status("northwind", "payment1") is None
    assert calls == []


def test_access_hook_cannot_change_the_checked_dispatch_snapshot(clock):
    action = None

    def access(ctx, args):
        if action is not None:
            action._args_json = '{"amount_minor":9999}'
        return True

    engine, calls = payment_engine(clock, hook=access)
    action = payment(engine)
    assert engine.execute(action, payer()).outcome == "succeeded"
    assert calls[0][0] == {"amount_minor": 100}


def test_approval_state_failure_rolls_back_its_decision(clock, monkeypatch):
    engine, calls = payment_engine(clock, approval="always")
    action = payment(engine)

    def unavailable(*args, **kwargs):
        raise StorageError("simulated state write failure")

    with monkeypatch.context() as patch:
        patch.setattr(engine.store, "set_state", unavailable)
        with pytest.raises(StorageError):
            engine.approve(action, make_approver())
    assert engine.store.get_approval("northwind", action.operation_id) is None
    assert engine.operation_status("northwind", action.operation_id).state == "prepared"
    assert engine.execute(action, payer()).code == Code.APPROVAL_REQUIRED
    assert calls == []
    assert engine.approve(action, make_approver()).allowed
    assert engine.execute(action, payer()).outcome == "succeeded"


def test_post_dispatch_storage_failure_keeps_the_reservation(clock, monkeypatch):
    engine, calls = payment_engine(clock)
    action = payment(engine)

    def unavailable(*args, **kwargs):
        raise StorageError("simulated result write failure")

    with monkeypatch.context() as patch:
        patch.setattr(engine.store, "finish", unavailable)
        with pytest.raises(StorageError):
            engine.execute(action, payer())
    assert len(calls) == 1
    assert engine.operation_status("northwind", action.operation_id).state == "reserved"
    replay = engine.execute(action, payer())
    assert replay.replayed and replay.outcome == "reserved"
    assert len(calls) == 1


def test_approval_deadline_is_checked_after_sqlite_write_lock(clock):
    engine, calls = payment_engine(clock, approval="always")
    action = payment(engine)

    def lock_acquired(sql):
        if sql == "BEGIN IMMEDIATE":
            clock.now += 901

    engine.store._conn.set_trace_callback(lock_acquired)
    assert engine.approve(action, make_approver()).code == Code.RUN_EXPIRED
    assert engine.store.get_approval("northwind", action.operation_id) is None
    assert calls == []


def test_reservation_checks_persisted_approval_requirement_and_run(clock):
    engine, calls = payment_engine(clock, approval="always")
    action = payment(engine)
    assert engine.store.reserve("northwind", action.operation_id, "run1", action.request_hash,
                                clock.now, False, clock.now + 900, 20) == "approval_missing"
    assert engine.store.reserve("northwind", action.operation_id, "other", action.request_hash,
                                clock.now, True, clock.now + 900, 20) == "hash_mismatch"
    assert calls == []
