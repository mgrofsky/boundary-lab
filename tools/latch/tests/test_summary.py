from unittest.mock import MagicMock

from boundary_latch.models import Decision, PreparedAction, ToolSpec
from boundary_latch.reasons import Code, explain
from boundary_latch.store import SQLiteStore

from .conftest import RefundArgs, build_latch, make_ctx

ARGS = {"account_id": "A1", "amount_minor": 100}


def refund_spec(describe=None, handler=None):
    return ToolSpec(name="refund", version="1", args_model=RefundArgs,
                    handler=handler or MagicMock(return_value="ok"), describe=describe)


def test_no_describe_gives_an_empty_summary(clock):
    latch = build_latch(clock, refund_spec())
    prepared = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert prepared.summary == ""
    assert latch.operation_status("northwind", "op1").summary == ""


def test_describe_gets_the_validated_arguments(clock):
    seen = []

    def describe(args):
        seen.append(args)
        return f"refund {args.amount_minor} cents on {args.account_id}"

    latch = build_latch(clock, refund_spec(describe))
    prepared = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert isinstance(seen[0], RefundArgs)
    assert prepared.summary == "refund 100 cents on A1"
    assert latch.operation_status("northwind", "op1").summary == "refund 100 cents on A1"


def test_summary_is_one_line(clock):
    latch = build_latch(clock, refund_spec(lambda args: "  pay\n\tA1\x00  now\r\n"))
    prepared = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert prepared.summary == "pay A1 now"


def test_summary_is_cut_at_300_characters(clock):
    latch = build_latch(clock, refund_spec(lambda args: "x" * 500))
    prepared = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert prepared.summary == "x" * 300


def test_describe_that_raises_blocks_the_call(clock):
    def describe(args):
        raise RuntimeError("bad")

    handler = MagicMock()
    latch = build_latch(clock, refund_spec(describe, handler))
    result = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert isinstance(result, Decision)
    assert result.code == Code.DESCRIBE_FAILED
    assert result.explanation == explain(Code.DESCRIBE_FAILED)
    assert latch.store.get_operation("northwind", "op1") is None
    assert handler.call_count == 0


def test_describe_that_returns_something_else_blocks_the_call(clock):
    latch = build_latch(clock, refund_spec(lambda args: 42))
    result = latch.prepare(make_ctx(), "refund", ARGS, "op1")
    assert result.code == Code.DESCRIBE_FAILED


def test_summary_is_in_the_prepare_audit_row(clock):
    latch = build_latch(clock, refund_spec(lambda args: "refund A1"))
    latch.prepare(make_ctx(), "refund", ARGS, "op1")
    row = latch.store.audit_rows("northwind")[-1]
    assert row["event"] == "prepare"
    assert row["summary"] == "refund A1"


def test_summary_is_not_part_of_the_request_hash(clock):
    store = SQLiteStore()
    first = build_latch(clock, refund_spec(lambda args: "one"), store=store)
    a = first.prepare(make_ctx(), "refund", ARGS, "op1")
    second = build_latch(clock, refund_spec(lambda args: "two"), store=store)
    b = second.prepare(make_ctx(), "refund", ARGS, "op1")
    assert isinstance(b, PreparedAction)
    assert a.request_hash == b.request_hash
    # A repeat prepare keeps the summary saved the first time.
    assert b.summary == "one"
