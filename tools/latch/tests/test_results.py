"""Execution results carry the handler error and never raise after the handler ran."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from boundary_latch.errors import StorageError, ToolFailed
from boundary_latch.models import ToolSpec
from boundary_latch.reasons import Code

from .conftest import LookupArgs, build_latch, make_ctx

LOOKUP = {"account_id": "A1"}


def run_with(clock, handler, timeout=5.0, monkeypatch=None, broken=None, use_async=False):
    spec = ToolSpec(name="lookup_account", version="1", args_model=LookupArgs, handler=handler,
                    timeout_seconds=timeout)
    latch = build_latch(clock, spec)
    prepared = latch.prepare(make_ctx(), "lookup_account", LOOKUP, "op1")
    if broken:
        def fail(*args, **kwargs):
            raise StorageError("disk full\nsecond line")

        monkeypatch.setattr(latch.store, broken, fail)
    if use_async:
        return latch, asyncio.run(latch.aexecute(prepared, make_ctx()))
    return latch, latch.execute(prepared, make_ctx())


def test_success_has_no_error(clock):
    _, result = run_with(clock, MagicMock(return_value=1))
    assert result.error is None
    assert result.recorded is True
    assert result.reference is None


def test_tool_failed_sets_the_error(clock):
    def handler(args, info):
        raise ToolFailed("card declined\nmore detail")

    _, result = run_with(clock, handler)
    assert result.code == Code.FAILED
    assert result.error == "ToolFailed: card declined"


def test_other_errors_set_the_error(clock):
    def handler(args, info):
        raise KeyError("x" * 500)

    _, result = run_with(clock, handler)
    assert result.code == Code.UNCERTAIN
    assert result.error.startswith("KeyError: ")
    assert len(result.error) == 200


def test_timeout_sets_the_error(clock):
    def handler(args, info):
        time.sleep(0.5)

    _, result = run_with(clock, handler, timeout=0.05)
    assert result.code == Code.UNCERTAIN
    assert result.error.startswith("TimeoutError:")


def test_async_errors_set_the_error(clock):
    async def handler(args, info):
        raise ValueError("bad value")

    _, result = run_with(clock, handler, use_async=True)
    assert result.code == Code.UNCERTAIN
    assert result.error == "ValueError: bad value"


@pytest.mark.parametrize("broken", ["finish", "audit"])
def test_storage_failure_after_the_handler_does_not_raise(clock, monkeypatch, broken):
    handler = MagicMock(return_value={"balance_minor": 5})
    _, result = run_with(clock, handler, monkeypatch=monkeypatch, broken=broken)
    assert handler.call_count == 1
    assert result.outcome == "succeeded"
    assert result.code == Code.EXECUTED
    assert result.value == {"balance_minor": 5}
    assert result.recorded is False
    assert result.error == "StorageError: disk full"


def test_storage_failure_keeps_the_handler_outcome(clock, monkeypatch):
    def handler(args, info):
        raise ToolFailed("no")

    _, result = run_with(clock, handler, monkeypatch=monkeypatch, broken="finish")
    assert result.outcome == "failed"
    assert result.recorded is False
    assert result.error == "StorageError: disk full"


def test_async_storage_failure_does_not_raise(clock, monkeypatch):
    async def handler(args, info):
        return 1

    _, result = run_with(clock, handler, monkeypatch=monkeypatch, broken="finish",
                         use_async=True)
    assert result.outcome == "succeeded"
    assert result.recorded is False
