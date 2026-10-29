import sqlite3

import pytest

from boundary_latch.errors import StorageError
from boundary_latch.store import ApprovalExists, OperationExists, SQLiteStore

NOW = 1000.0


def op_row(operation_id="op1", needs_approval=False, request_hash="h1"):
    return {
        "org_id": "o1",
        "operation_id": operation_id,
        "run_id": "r1",
        "user_id": "u1",
        "tool": "refund",
        "tool_version": "1",
        "request_hash": request_hash,
        "needs_approval": int(needs_approval),
        "state": "prepared",
        "code": "ALLOWED",
        "created_at": NOW,
        "updated_at": NOW,
    }


def reserve(store, operation_id="op1", expected_hash="h1", now=NOW + 1, needs_approval=False):
    return store.reserve(
        "o1",
        operation_id,
        "r1",
        expected_hash,
        now,
        needs_approval,
        run_deadline=NOW + 900,
        max_actions=20,
    )


@pytest.fixture
def store():
    s = SQLiteStore()
    yield s
    s.close()


def test_touch_run_keeps_the_first_start(store):
    assert store.touch_run("o1", "r1", NOW) == (NOW, 0)
    assert store.touch_run("o1", "r1", NOW + 50) == (NOW, 0)


def test_count_submission_increments(store):
    store.touch_run("o1", "r1", NOW)
    assert store.count_submission("o1", "r1") == 1
    assert store.count_submission("o1", "r1") == 2


def test_insert_operation_twice_raises(store):
    store.insert_operation(op_row())
    with pytest.raises(OperationExists):
        store.insert_operation(op_row())
    assert store.get_operation("o1", "op1")["state"] == "prepared"
    assert store.get_operation("o1", "missing") is None


def test_reserve_once_then_already_recorded(store):
    store.insert_operation(op_row())
    assert reserve(store) == "reserved"
    assert store.get_operation("o1", "op1")["state"] == "reserved"
    assert reserve(store) == "already_recorded"


def test_reserve_after_finish_is_already_recorded(store):
    store.insert_operation(op_row())
    reserve(store)
    store.finish("o1", "op1", "succeeded", "EXECUTED", NOW + 2)
    row = store.get_operation("o1", "op1")
    assert (row["state"], row["code"]) == ("succeeded", "EXECUTED")
    assert reserve(store) == "already_recorded"


def test_reserve_denied_operation(store):
    store.insert_operation(op_row())
    store.set_state("o1", "op1", "denied", "DENIED_BY_APPROVER", NOW + 1)
    assert reserve(store) == "denied"


def test_reserve_after_run_deadline(store):
    store.insert_operation(op_row())
    assert reserve(store, now=NOW + 901) == "run_expired"


def test_reserve_with_different_hash(store):
    store.insert_operation(op_row())
    assert reserve(store, expected_hash="other") == "hash_mismatch"


def test_reserve_without_approval_row(store):
    store.insert_operation(op_row(needs_approval=True))
    assert reserve(store, needs_approval=True) == "approval_missing"


def test_reserve_with_expired_approval(store):
    store.insert_operation(op_row(needs_approval=True))
    store.record_approval("o1", "op1", "boss", "h1", "approved", NOW, NOW + 5)
    assert reserve(store, now=NOW + 10, needs_approval=True) == "approval_expired"


def test_reserve_with_approval_for_a_different_hash(store):
    store.insert_operation(op_row(needs_approval=True))
    store.record_approval("o1", "op1", "boss", "other", "approved", NOW, NOW + 600)
    assert reserve(store, needs_approval=True) == "hash_mismatch"


def test_reserve_consumes_the_approval(store):
    store.insert_operation(op_row(needs_approval=True))
    store.record_approval("o1", "op1", "boss", "h1", "approved", NOW, NOW + 600)
    assert store.get_approval("o1", "op1")["consumed_at"] is None
    assert reserve(store, needs_approval=True) == "reserved"
    assert store.get_approval("o1", "op1")["consumed_at"] == NOW + 1
    assert reserve(store, needs_approval=True) == "already_recorded"


def test_record_approval_twice_raises(store):
    store.insert_operation(op_row(needs_approval=True))
    store.record_approval("o1", "op1", "boss", "h1", "approved", NOW, NOW + 600)
    with pytest.raises(ApprovalExists):
        store.record_approval("o1", "op1", "boss", "h1", "denied", NOW, NOW + 600)


def test_reserve_stops_at_the_run_limit(store):
    for name in ("op1", "op2"):
        store.insert_operation(op_row(operation_id=name))
    assert store.reserve("o1", "op1", "r1", "h1", NOW, False, NOW + 900, 1) == "reserved"
    assert store.reserve("o1", "op2", "r1", "h1", NOW, False, NOW + 900, 1) == "run_limit"


def test_audit_rows_return_the_event(store):
    store.audit("prepare", "ALLOWED", "o1", run_id="r1", operation_id="op1", now=NOW)
    store.audit("prepare", "ALLOWED", "o2", now=NOW)
    rows = store.audit_rows("o1")
    assert len(rows) == 1
    assert rows[0]["event"] == "prepare"
    assert rows[0]["code"] == "ALLOWED"
    assert rows[0]["operation_id"] == "op1"


def test_operation_survives_a_restart(tmp_path):
    path = tmp_path / "latch.sqlite"
    first = SQLiteStore(path)
    first.insert_operation(op_row())
    reserve(first)
    first.close()
    second = SQLiteStore(path)
    assert second.get_operation("o1", "op1")["state"] == "reserved"
    assert reserve(second) == "already_recorded"
    second.close()


def test_storage_failure_becomes_storage_error(store, monkeypatch):
    store.insert_operation(op_row())

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store._conn, "execute", boom)
    with pytest.raises(StorageError):
        reserve(store)


def test_storage_failure_rolls_back_the_reserve(store, monkeypatch):
    store.insert_operation(op_row())
    real = store._conn.execute

    def fail_on_update(sql, *args):
        if sql.lstrip().startswith("UPDATE"):
            raise sqlite3.OperationalError("disk I/O error")
        return real(sql, *args)

    monkeypatch.setattr(store._conn, "execute", fail_on_update)
    with pytest.raises(StorageError):
        reserve(store)
    monkeypatch.undo()
    assert store.get_operation("o1", "op1")["state"] == "prepared"
    assert reserve(store) == "reserved"
