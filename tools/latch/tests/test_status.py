import threading

from boundary_latch.models import OperationStatus, PreparedAction, ToolSpec
from boundary_latch.reasons import Code

from .conftest import build_latch, make_ctx, no_calls
from .test_execute import TaskArgs, prepare_task, task_ctx


def test_unknown_operation_is_none(latch):
    assert latch.operation_status("northwind", "missing") is None


def test_status_after_prepare_and_run(latch, clock, handlers):
    prepared = latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    assert isinstance(prepared, PreparedAction)
    status = latch.operation_status("northwind", "op1")
    assert status == OperationStatus(
        operation_id="op1",
        state="prepared",
        code=Code.ALLOWED,
        tool="lookup_account",
        created_at=1000.0,
        updated_at=1000.0,
    )
    clock.now += 5
    latch.execute(prepared, make_ctx())
    handler = handlers["lookup_account"]
    calls = handler.call_count
    status = latch.operation_status("northwind", "op1")
    assert (status.state, status.code) == ("succeeded", Code.EXECUTED)
    assert status.updated_at == 1005.0
    assert handler.call_count == calls == 1


def test_status_does_not_cross_orgs(latch):
    latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    assert latch.operation_status("contoso", "op1") is None


def test_status_after_timeout_is_uncertain(clock):
    release = threading.Event()
    calls = []

    def slow(args, info):
        calls.append(info.operation_id)
        release.wait(2)

    spec = ToolSpec(
        name="task", version="1", args_model=TaskArgs, handler=slow, timeout_seconds=0.1
    )
    latch = build_latch(clock, spec)
    prepared = prepare_task(latch)
    try:
        latch.execute(prepared, task_ctx())
    finally:
        release.set()
    status = latch.operation_status("northwind", "op1")
    assert (status.state, status.code) == ("uncertain", Code.UNCERTAIN)
    assert calls == ["op1"]


def test_status_never_calls_a_handler(latch, handlers):
    latch.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
    for _ in range(3):
        latch.operation_status("northwind", "op1")
    assert no_calls(handlers)
