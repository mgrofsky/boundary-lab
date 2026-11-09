"""Audit rows as event dicts, for on_event and the audit command."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

# Fields are only added within a schema number, never renamed or removed.
SCHEMA = 1

FIELDS = (
    "event",
    "code",
    "org_id",
    "run_id",
    "operation_id",
    "user_id",
    "tool",
    "outcome",
    "duration_ms",
    "at",
    "reference",
    "summary",
    "copied",
)


def to_event(row: dict[str, Any]) -> dict[str, Any]:
    """Turn one audit row into the event dict. copied becomes a list of paths or None."""
    event: dict[str, Any] = {"schema": SCHEMA}
    for name in FIELDS:
        event[name] = row.get(name)
    if event["copied"] is not None:
        event["copied"] = json.loads(event["copied"])
    return event


def format_line(event: dict[str, Any]) -> str:
    """One plain line for a person reading the audit in a terminal."""
    when = datetime.fromtimestamp(event["at"], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [when, event["event"], event["code"], f"org={event['org_id']}"]
    labels = (("run", "run_id"), ("op", "operation_id"), ("user", "user_id"), ("tool", "tool"),
              ("outcome", "outcome"), ("ms", "duration_ms"), ("ref", "reference"))
    for label, name in labels:
        if event[name] is not None:
            parts.append(f"{label}={event[name]}")
    if event["copied"]:
        parts.append(f"copied={','.join(event['copied'])}")
    if event["summary"]:
        parts.append(f"summary={event['summary']}")
    return " ".join(parts)
