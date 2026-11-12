"""amount_field and recipient_field with values of the wrong type fail closed."""

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from boundary_latch.engine import Latch
from boundary_latch.models import Decision, PreparedAction, ToolSpec
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code
from boundary_latch.store import SQLiteStore

from .conftest import make_ctx

POLICY = """
[tools.pay]
require_permission = "pay"
approve_permission = "pay:approve"
approval.mode = "over_amount"
approval.amount_field = "amount"
approval.threshold_minor = 5000

[tools.mail]
require_permission = "pay"
recipient_field = "to"
allowed_recipients = ["ok@example.com"]

[tools.missing_amount]
require_permission = "pay"
approve_permission = "pay:approve"
approval.mode = "over_amount"
approval.amount_field = "not_there"
approval.threshold_minor = 5000

[tools.missing_recipient]
require_permission = "pay"
recipient_field = "not_there"
allowed_recipients = ["ok@example.com"]
"""


class AnyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Any = None
    to: Any = None


class AliasArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Any = Field(default=None, alias="amount")


def build(args_model=AnyArgs) -> tuple[Latch, MagicMock]:
    handler = MagicMock(return_value="ran")
    latch = Latch(parse_policy(POLICY), SQLiteStore())
    for name in ("pay", "mail", "missing_amount", "missing_recipient"):
        latch.register(ToolSpec(name=name, version="1", args_model=args_model, handler=handler))
    return latch, handler


CTX = make_ctx(permissions={"pay"})


@pytest.mark.parametrize("amount", [True, False, "9000", None, [9000], {"a": 1}, float("nan"),
                                    float("inf"), Decimal("NaN")])
def test_an_amount_that_is_not_a_number_is_refused(amount):
    latch, handler = build()
    decision = latch.prepare(CTX, "pay", {"amount": amount}, "op1")
    assert isinstance(decision, Decision)
    assert decision.code == Code.INVALID_ARGUMENTS
    assert handler.call_count == 0


@pytest.mark.parametrize(("amount", "needs"), [(9000, True), (100, False), (9000.5, True),
                                               (100.5, False), (Decimal(9000), True),
                                               (-5, False)])
def test_a_number_amount_still_decides_approval(amount, needs):
    latch, _ = build()
    prepared = latch.prepare(CTX, "pay", {"amount": amount}, "op1")
    assert isinstance(prepared, PreparedAction)
    assert prepared.needs_approval is needs


def test_an_amount_field_the_model_lacks_is_refused():
    latch, handler = build()
    decision = latch.prepare(CTX, "missing_amount", {"amount": 1}, "op1")
    assert decision.code == Code.INVALID_ARGUMENTS
    assert handler.call_count == 0


def test_an_aliased_amount_field_is_read_by_its_field_name_or_alias():
    latch, _ = build(AliasArgs)
    prepared = latch.prepare(CTX, "pay", {"amount": 9000}, "op1")
    assert isinstance(prepared, PreparedAction)
    assert prepared.needs_approval is True


@pytest.mark.parametrize("to", [None, 1, ["ok@example.com"], {"to": "ok@example.com"}, True])
def test_a_recipient_that_is_not_a_string_is_refused(to):
    latch, handler = build()
    decision = latch.prepare(CTX, "mail", {"to": to}, "op1")
    assert decision.code == Code.RECIPIENT_NOT_ALLOWED
    assert handler.call_count == 0


def test_a_recipient_field_the_model_lacks_is_refused():
    latch, handler = build()
    decision = latch.prepare(CTX, "missing_recipient", {"to": "ok@example.com"}, "op1")
    assert decision.code == Code.RECIPIENT_NOT_ALLOWED
    assert handler.call_count == 0


def test_an_allowed_recipient_still_runs():
    latch, handler = build()
    prepared = latch.prepare(CTX, "mail", {"to": "ok@example.com"}, "op1")
    assert isinstance(prepared, PreparedAction)
    assert latch.execute(prepared, CTX).code == Code.EXECUTED
    assert handler.call_count == 1


def test_needs_approval_is_true_for_an_amount_that_is_not_a_number():
    latch, _ = build()
    tp = latch.policy.tools["pay"]
    assert Latch._needs_approval(tp, AnyArgs(amount=True), frozenset()) is True
    assert Latch._needs_approval(tp, AnyArgs(amount=float("nan")), frozenset()) is True
