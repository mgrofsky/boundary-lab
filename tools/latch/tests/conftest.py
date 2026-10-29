from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from boundary_latch.engine import Latch
from boundary_latch.models import ToolSpec, UserContext
from boundary_latch.policy import parse_policy
from boundary_latch.store import SQLiteStore

POLICY = """
[tools.send_message]
require_permission = "messages:send"
approve_permission = "messages:approve"
recipient_field = "to"
allowed_recipients = ["help@northwind.example"]
approval.mode = "always"

[tools.lookup_account]
require_permission = "accounts:read"

[tools.refund]
require_permission = "refunds:create"
approve_permission = "refunds:approve"
approval.mode = "over_amount"
approval.amount_field = "amount_minor"
approval.threshold_minor = 5000

[tools.task]
require_permission = "tasks:run"

[tools.payment]
require_permission = "payments:create"
approve_permission = "payments:approve"
approval.mode = "always"
"""

ACCOUNTS = {"sam": {"A1"}, "priya": {"A2"}}


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class SendMessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str
    body: str


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str


class RefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    amount_minor: int = Field(gt=0)


def account_hook(ctx: UserContext, args: dict) -> bool:
    if args["account_id"] == "BOOM":
        raise RuntimeError("lookup service down")
    return args["account_id"] in ACCOUNTS.get(ctx.user_id, set())


def make_ctx(user_id="sam", permissions=None, org_id="northwind", run_id="run1") -> UserContext:
    if permissions is None:
        permissions = {"messages:send", "accounts:read", "refunds:create"}
    return UserContext(
        user_id=user_id, org_id=org_id, permissions=frozenset(permissions), run_id=run_id
    )


def make_approver(user_id="jordan", org_id="northwind") -> UserContext:
    return make_ctx(
        user_id=user_id,
        permissions={"messages:approve", "refunds:approve", "payments:approve"},
        org_id=org_id,
        run_id="approver-run",
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def handlers() -> dict[str, MagicMock]:
    return {
        "send_message": MagicMock(return_value="sent"),
        "lookup_account": MagicMock(return_value={"balance_minor": 10000}),
        "refund": MagicMock(return_value="refunded"),
    }


@pytest.fixture
def latch(clock, handlers) -> Latch:
    engine = Latch(parse_policy(POLICY), SQLiteStore(), clock=clock)
    engine.register(
        ToolSpec(
            name="send_message",
            version="1",
            args_model=SendMessageArgs,
            handler=handlers["send_message"],
        )
    )
    engine.register(
        ToolSpec(
            name="lookup_account",
            version="1",
            args_model=LookupArgs,
            handler=handlers["lookup_account"],
            access_hook=account_hook,
        )
    )
    engine.register(
        ToolSpec(
            name="refund",
            version="1",
            args_model=RefundArgs,
            handler=handlers["refund"],
            access_hook=account_hook,
        )
    )
    return engine


def build_latch(clock, *specs: ToolSpec, store: SQLiteStore | None = None) -> Latch:
    engine = Latch(parse_policy(POLICY), store or SQLiteStore(), clock=clock)
    for spec in specs:
        engine.register(spec)
    return engine


def no_calls(handlers: dict[str, MagicMock]) -> bool:
    return all(handler.call_count == 0 for handler in handlers.values())
