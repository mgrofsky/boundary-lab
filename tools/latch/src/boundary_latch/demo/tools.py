"""The three Northwind tools and the Latch that guards them."""

from __future__ import annotations

import time
from collections.abc import Callable
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..engine import Latch
from ..models import CallInfo, ToolSpec
from ..policy import Policy, parse_policy
from ..store import SQLiteStore
from .northwind import Northwind


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str


class SendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str
    body: str = Field(max_length=500)


class RefundArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    amount_minor: int = Field(ge=1)


def demo_policy() -> Policy:
    text = resources.files("boundary_latch.demo").joinpath("policy.toml").read_text()
    return parse_policy(text)


def tool_specs(nw: Northwind, timeout_seconds: float = 30.0) -> list[ToolSpec]:
    def lookup(args: dict[str, Any], info: CallInfo) -> dict[str, Any]:
        return nw.lookup(args["account_id"])

    def send(args: dict[str, Any], info: CallInfo) -> dict[str, Any]:
        return nw.send(args["to"], args["body"])

    def refund(args: dict[str, Any], info: CallInfo) -> dict[str, Any]:
        return nw.refund(args["account_id"], args["amount_minor"], info.operation_id)

    def owns_account(ctx, args: dict[str, Any]) -> bool:
        return nw.can_see(ctx.user_id, args["account_id"])

    return [
        ToolSpec(
            name="lookup_account",
            version="1",
            args_model=LookupArgs,
            handler=lookup,
            access_hook=owns_account,
            description="Look up the balance of one customer account.",
            timeout_seconds=timeout_seconds,
        ),
        ToolSpec(
            name="send_message",
            version="1",
            args_model=SendArgs,
            handler=send,
            description="Send a message to a Northwind support address.",
            timeout_seconds=timeout_seconds,
        ),
        ToolSpec(
            name="issue_refund",
            version="1",
            args_model=RefundArgs,
            handler=refund,
            access_hook=owns_account,
            description="Refund an amount in cents to one customer account.",
            timeout_seconds=timeout_seconds,
        ),
    ]


def build_latch(
    nw: Northwind,
    store: SQLiteStore,
    clock: Callable[[], float] = time.time,
    timeout_seconds: float = 30.0,
) -> Latch:
    latch = Latch(demo_policy(), store, clock=clock)
    for spec in tool_specs(nw, timeout_seconds):
        latch.register(spec)
    return latch
