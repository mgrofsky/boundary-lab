from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import ConfigError
from .models import Decision, PreparedAction, ToolSpec, UserContext
from .policy import Policy, ToolPolicy, load_policy
from .reasons import Code, explain
from .store import OperationExists, SQLiteStore


class Latch:
    """Checks tool calls against a policy before they run."""

    def __init__(
        self, policy: Policy, store: SQLiteStore, clock: Callable[[], float] = time.time
    ):
        self.policy, self.store, self.clock = policy, store, clock
        self._tools: dict[str, ToolSpec] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="latch"
        )

    @classmethod
    def from_toml(
        cls, path: str | Path, store: SQLiteStore, clock: Callable[[], float] = time.time
    ) -> Latch:
        return cls(load_policy(path), store, clock)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ConfigError(f"tool {spec.name} is already registered")
        if spec.name not in self.policy.tools:
            raise ConfigError(f"tool {spec.name} has no policy entry")
        cfg = spec.args_model.model_config
        if cfg.get("extra") != "forbid":
            raise ConfigError(f"args model for {spec.name} must set extra='forbid'")
        self._tools[spec.name] = spec

    def tool_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def prepare(
        self, ctx: UserContext, tool: str, arguments: dict[str, Any], operation_id: str
    ) -> Decision | PreparedAction:
        now = self.clock()
        limits = self.policy.limits
        started_at, _ = self.store.touch_run(ctx.org_id, ctx.run_id, now)
        if now > started_at + limits.run_lifetime_seconds:
            return self._deny_prepare(ctx, tool, operation_id, Code.RUN_EXPIRED, now)

        # A resubmitted operation id does not count against the run limit.
        existing = self.store.get_operation(ctx.org_id, operation_id)
        if existing is None:
            count = self.store.count_submission(ctx.org_id, ctx.run_id)
            if count > limits.max_actions_per_run:
                return self._deny_prepare(ctx, tool, operation_id, Code.RUN_LIMIT, now)

        spec = self._tools.get(tool)
        if spec is None:
            return self._deny_prepare(ctx, tool, operation_id, Code.UNKNOWN_TOOL, now)
        tp = self.policy.tools.get(tool)
        if tp is None:
            return self._deny_prepare(ctx, tool, operation_id, Code.NO_POLICY, now)

        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError:
            return self._deny_prepare(ctx, tool, operation_id, Code.INVALID_ARGUMENTS, now)

        code = self._check_access(ctx, spec, tp, args)
        if code is not None:
            return self._deny_prepare(ctx, tool, operation_id, code, now)

        needs_approval = self._needs_approval(tp, args)
        canonical = json.dumps(args.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        request_hash = self._request_hash(ctx, spec, canonical)

        if existing is not None:
            if existing["request_hash"] != request_hash:
                return self._deny_prepare(ctx, tool, operation_id, Code.OPERATION_CONFLICT, now)
            needs_approval = bool(existing["needs_approval"])
        code = Code.NEEDS_APPROVAL if needs_approval else Code.ALLOWED
        if existing is None:
            try:
                self.store.insert_operation(
                    {
                        "org_id": ctx.org_id,
                        "operation_id": operation_id,
                        "run_id": ctx.run_id,
                        "user_id": ctx.user_id,
                        "tool": spec.name,
                        "tool_version": spec.version,
                        "request_hash": request_hash,
                        "needs_approval": int(needs_approval),
                        "state": "prepared",
                        "code": str(code),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            except OperationExists:
                return self._deny_prepare(ctx, tool, operation_id, Code.OPERATION_CONFLICT, now)

        self._audit("prepare", code, ctx, tool, operation_id, now)
        return self._build_prepared(ctx, spec, operation_id, needs_approval, request_hash,
                                    canonical, code)

    def _check_access(
        self, ctx: UserContext, spec: ToolSpec, tp: ToolPolicy, args: BaseModel
    ) -> Code | None:
        if tp.require_permission not in ctx.permissions:
            return Code.MISSING_PERMISSION
        recipients = tp.allowed_recipients or []
        if tp.recipient_field is not None and getattr(args, tp.recipient_field) not in recipients:
            return Code.RECIPIENT_NOT_ALLOWED
        if spec.access_hook is not None:
            try:
                ok = spec.access_hook(ctx, args.model_dump())
            except Exception:  # noqa: BLE001 any hook error blocks the call
                return Code.ACCESS_HOOK_FAILED
            if not ok:
                return Code.ACCESS_DENIED
        return None

    @staticmethod
    def _needs_approval(tp: ToolPolicy, args: BaseModel) -> bool:
        if tp.approval.mode == "always":
            return True
        if tp.approval.mode == "over_amount":
            return getattr(args, tp.approval.amount_field) > tp.approval.threshold_minor
        return False

    def _request_hash(self, ctx: UserContext, spec: ToolSpec, canonical: str) -> str:
        body = json.dumps(
            {
                "tool": spec.name,
                "version": spec.version,
                "policy": self.policy.digest,
                "user_id": ctx.user_id,
                "org_id": ctx.org_id,
                "args": canonical,
            },
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()

    @staticmethod
    def _build_prepared(
        ctx: UserContext,
        spec: ToolSpec,
        operation_id: str,
        needs_approval: bool,
        request_hash: str,
        canonical: str,
        code: Code,
    ) -> PreparedAction:
        prepared = PreparedAction.model_construct(
            operation_id=operation_id,
            run_id=ctx.run_id,
            user_id=ctx.user_id,
            org_id=ctx.org_id,
            tool=spec.name,
            tool_version=spec.version,
            needs_approval=needs_approval,
            request_hash=request_hash,
            code=code,
            explanation=explain(code),
        )
        prepared._args_json = canonical
        return prepared

    def _deny_prepare(
        self, ctx: UserContext, tool: str, operation_id: str, code: Code, now: float
    ) -> Decision:
        self._audit("prepare", code, ctx, tool, operation_id, now)
        return Decision(
            allowed=False, code=code, explanation=explain(code), operation_id=operation_id
        )

    def _audit(
        self, event: str, code: Code, ctx: UserContext, tool: str, operation_id: str, now: float
    ) -> None:
        self.store.audit(
            event,
            str(code),
            ctx.org_id,
            run_id=ctx.run_id,
            operation_id=operation_id,
            user_id=ctx.user_id,
            tool=tool,
            now=now,
        )
