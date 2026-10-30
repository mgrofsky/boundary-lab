"""Example users for the demo, labeled so nobody mistakes them for real people."""

from __future__ import annotations

from ..models import UserContext

ORG_ID = "northwind"

AGENT = frozenset({"accounts:read", "messages:send", "refunds:issue"})
SUPERVISOR = frozenset({"accounts:read", "messages:approve", "refunds:approve"})

PROFILES = {
    "sam": {"label": "Sam (support agent, example user)", "permissions": AGENT},
    "priya": {"label": "Priya (support agent, example user)", "permissions": AGENT},
    "jordan": {"label": "Jordan (supervisor, example user)", "permissions": SUPERVISOR},
}


def context(name: str, run_id: str) -> UserContext:
    profile = PROFILES[name]
    return UserContext(
        user_id=name, org_id=ORG_ID, permissions=profile["permissions"], run_id=run_id
    )
