"""A starter app with one tool, create_ticket, checked by Latch before it runs."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from boundary_latch import (
    CallInfo,
    Decision,
    Latch,
    SQLiteStore,
    ToolSpec,
    UserContext,
    load_policy,
)

POLICY = Path(__file__).resolve().parent / "policy.toml"

# Your real ticket system goes here. This list stands in for it.
tickets: list[dict[str, Any]] = []


class TicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=2000)


def create_ticket(args: dict[str, Any], info: CallInfo) -> dict[str, Any]:
    ticket = {"id": len(tickets) + 1, "title": args["title"], "body": args["body"],
              "opened_by": info.user_id}
    tickets.append(ticket)
    return ticket


def build(store: SQLiteStore | None = None) -> Latch:
    latch = Latch(load_policy(POLICY), store or SQLiteStore("app.sqlite"))
    latch.register(
        ToolSpec(
            name="create_ticket",
            version="1",
            args_model=TicketArgs,
            handler=create_ticket,
            description="Open a support ticket.",
        )
    )
    return latch


def main() -> None:
    latch = build()
    # Replace this with your authenticated user.
    ctx = UserContext(
        user_id="sample-user",
        org_id="sample-org",
        permissions=frozenset({"tickets:create"}),
        run_id=uuid.uuid4().hex,
    )
    arguments = {"title": "Printer is out of paper", "body": "Third floor."}
    prepared = latch.prepare(ctx, "create_ticket", arguments, operation_id=uuid.uuid4().hex)
    if isinstance(prepared, Decision):
        print(f"{prepared.code}: {prepared.explanation}")
        return
    result = latch.execute(prepared, ctx)
    print(f"{result.code}: {result.value}")


if __name__ == "__main__":
    main()
