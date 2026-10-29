"""One demo session shared by the terminal and browser demos.

The session holds one Northwind backend, one store and one Latch, plus the pending
prepared actions in memory. The store keeps its records across a restart. The fake
backend and the pending actions do not.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from ..models import PreparedAction, UserContext
from ..store import SQLiteStore
from .northwind import Northwind
from .profiles import ORG_ID, PROFILES, context
from .tools import build_latch


class DemoSession:
    def __init__(
        self,
        db_path: str = ":memory:",
        clock: Callable[[], float] = time.time,
        timeout_seconds: float = 30.0,
    ):
        self.run_id = "demo-" + uuid.uuid4().hex[:8]
        self.nw = Northwind()
        self.store = SQLiteStore(db_path)
        self.latch = build_latch(self.nw, self.store, clock=clock, timeout_seconds=timeout_seconds)
        self.pending: dict[str, PreparedAction] = {}

    def close(self) -> None:
        self.store.close()

    def ctx(self, profile: str) -> UserContext:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile}")
        return context(profile, self.run_id)

    def profiles(self) -> dict[str, Any]:
        people = [
            {"name": name, "label": p["label"], "permissions": sorted(p["permissions"])}
            for name, p in PROFILES.items()
        ]
        tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.args_model.model_json_schema(),
            }
            for spec in self.latch.tool_specs()
        ]
        return {"profiles": people, "tools": tools}

    def state(self) -> dict[str, Any]:
        return {"run_id": self.run_id, **self.nw.snapshot()}

    def propose(
        self,
        profile: str,
        tool: str,
        arguments: dict[str, Any],
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        op = operation_id or uuid.uuid4().hex
        result = self.latch.prepare(self.ctx(profile), tool, arguments, op)
        if isinstance(result, PreparedAction):
            self.pending[op] = result
            data = {**result.model_dump(mode="json"), "allowed": True,
                    "arguments": result.arguments}
        else:
            data = {**result.model_dump(mode="json"), "operation_id": op,
                    "needs_approval": False}
        return {**data, "state": self.state()}

    def approve(self, profile: str, operation_id: str) -> dict[str, Any]:
        return self._decide(profile, operation_id, approve=True)

    def deny(self, profile: str, operation_id: str) -> dict[str, Any]:
        return self._decide(profile, operation_id, approve=False)

    def _decide(self, profile: str, operation_id: str, approve: bool) -> dict[str, Any]:
        ctx = self.ctx(profile)
        prepared = self.pending.get(operation_id)
        if prepared is None:
            return self._recorded(operation_id)
        decide = self.latch.approve if approve else self.latch.deny
        result = decide(prepared, ctx)
        return {**result.model_dump(mode="json"), "operation_id": operation_id,
                "state": self.state()}

    def run(self, profile: str, operation_id: str) -> dict[str, Any]:
        ctx = self.ctx(profile)
        prepared = self.pending.get(operation_id)
        if prepared is None:
            # Nothing pending here, for example after a restart: answer from the record.
            return self._recorded(operation_id)
        result = self.latch.execute(prepared, ctx)
        return {**result.model_dump(mode="json"), "state": self.state()}

    def status(self, operation_id: str) -> dict[str, Any]:
        status = self.latch.operation_status(ORG_ID, operation_id)
        if status is None:
            return {"operation_id": operation_id, "found": False}
        return {**status.model_dump(mode="json"), "found": True}

    def audit(self) -> dict[str, Any]:
        return {"rows": self.store.audit_rows(ORG_ID)}

    def _recorded(self, operation_id: str) -> dict[str, Any]:
        """The stored outcome for an operation this session holds no pending action for."""
        status = self.status(operation_id)
        if status["found"]:
            status["replayed"] = True
        else:
            status["explanation"] = "No pending action or record has this operation id."
        return {**status, "state": self.state()}
