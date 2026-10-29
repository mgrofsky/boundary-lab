"""Sample identities for the demo. They are labeled so nobody mistakes them for real users."""

from __future__ import annotations

from ..models import UserContext

ORG_ID = "northwind"

AGENT = frozenset({"accounts:read", "messages:send", "refunds:issue"})
SUPERVISOR = frozenset({"accounts:read", "messages:approve", "refunds:approve"})

PROFILES = {
    "sam": {"label": "Sam (customer agent, sample identity)", "permissions": AGENT},
    "priya": {"label": "Priya (customer agent, sample identity)", "permissions": AGENT},
    "jordan": {"label": "Jordan (supervisor, sample identity)", "permissions": SUPERVISOR},
}


def context(name: str, run_id: str) -> UserContext:
    profile = PROFILES[name]
    return UserContext(
        user_id=name, org_id=ORG_ID, permissions=profile["permissions"], run_id=run_id
    )
