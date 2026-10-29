"""A fake Northwind backend held in memory. It forgets everything on restart."""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

from ..errors import ToolFailed

ACCOUNTS = {
    "A1": {"owner": "sam", "balance_minor": 10000, "refundable_minor": 10000},
    "A2": {"owner": "priya", "balance_minor": 20000, "refundable_minor": 2500},
}

USERS = {
    "sam": {"accounts": ["A1"]},
    "priya": {"accounts": ["A2"]},
    "jordan": {"accounts": ["A1", "A2"]},
}


def money(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02d}"


class Northwind:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = copy.deepcopy(ACCOUNTS)
        self.users: dict[str, dict[str, Any]] = copy.deepcopy(USERS)
        self.outbox: list[dict[str, Any]] = []
        self.refunds: dict[str, dict[str, Any]] = {}
        self.delay_seconds: float = 0.0
        self.fail_after_commit: bool = False
        self._lock = threading.Lock()

    def can_see(self, user_id: str, account_id: str) -> bool:
        user = self.users.get(user_id)
        return user is not None and account_id in user["accounts"]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(
                {"accounts": self.accounts, "outbox": self.outbox, "refunds": self.refunds}
            )

    def lookup(self, account_id: str) -> dict[str, Any]:
        with self._lock:
            return {"account_id": account_id, **self.accounts[account_id]}

    def send(self, to: str, body: str) -> dict[str, Any]:
        with self._lock:
            message = {"id": len(self.outbox) + 1, "to": to, "body": body}
            self.outbox.append(message)
            return dict(message)

    def refund(self, account_id: str, amount_minor: int, operation_id: str) -> dict[str, Any]:
        with self._lock:
            if operation_id in self.refunds:
                raise ToolFailed("already refunded under this operation id")
            account = self.accounts[account_id]
            if amount_minor > account["refundable_minor"]:
                raise ToolFailed("over refundable balance")
            account["balance_minor"] -= amount_minor
            account["refundable_minor"] -= amount_minor
            record = {"account_id": account_id, "amount_minor": amount_minor}
            self.refunds[operation_id] = record
        # The refund is committed. A slow or broken link after this point is the
        # case Latch records as uncertain.
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.fail_after_commit:
            raise RuntimeError("connection lost after the refund was committed")
        return {"operation_id": operation_id, **record}
