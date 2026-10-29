import json

from pydantic import BaseModel, ConfigDict

from boundary_latch.models import PreparedAction, ToolSpec
from boundary_latch.reasons import Code

from .conftest import build_latch, make_approver, make_ctx

CARD = "4111111111111111"


class PaymentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card: str
    amount_minor: int


def leaky_handler(args, info):
    raise RuntimeError("token=sk-secret")


def test_audit_rows_hold_no_arguments_or_messages(clock):
    spec = ToolSpec(name="payment", version="1", args_model=PaymentArgs, handler=leaky_handler)
    latch = build_latch(clock, spec)
    ctx = make_ctx(user_id="sam", permissions={"payments:create"})
    prepared = latch.prepare(ctx, "payment", {"card": CARD, "amount_minor": 1200}, "op1")
    assert isinstance(prepared, PreparedAction)
    assert latch.approve(prepared, make_approver()).code == Code.APPROVED
    result = latch.execute(prepared, ctx)
    assert result.code == Code.UNCERTAIN

    rows = latch.store.audit_rows("northwind")
    blob = json.dumps(rows)
    assert "4111" not in blob
    assert "sk-secret" not in blob
    assert "token=" not in blob
    assert "1200" not in blob
    for row in rows:
        assert {"at", "org_id", "event", "code"} <= set(row)
    assert [row["event"] for row in rows] == ["prepare", "approve", "execute"]
    assert [row["user_id"] for row in rows] == ["sam", "jordan", "sam"]
    execute_row = rows[-1]
    assert isinstance(execute_row["duration_ms"], int)
    assert execute_row["outcome"] == "uncertain"
