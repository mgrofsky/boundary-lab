from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .errors import ConfigError, ToolFailed
from .models import (
    CallInfo,
    Decision,
    ExecutionResult,
    OperationStatus,
    PreparedAction,
    ToolSpec,
    UserContext,
)
from .policy import Policy, ToolPolicy, load_policy
from .reasons import Code, explain
from .store import OperationExists, SQLiteStore

RESERVE_CODES = {
    "denied": Code.DENIED_BY_APPROVER,
    "approval_missing": Code.APPROVAL_REQUIRED,
    "approval_expired": Code.APPROVAL_EXPIRED,
    "run_expired": Code.RUN_EXPIRED,
    "run_limit": Code.RUN_LIMIT,
    "hash_mismatch": Code.OPERATION_CONFLICT,
}


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
        if now >= started_at + limits.run_lifetime_seconds:
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

        now = self.clock()
        if now >= started_at + limits.run_lifetime_seconds:
            return self._deny_prepare(ctx, tool, operation_id, Code.RUN_EXPIRED, now)

        needs_approval = self._needs_approval(tp, args)
        canonical = json.dumps(args.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        request_hash = self._request_hash(ctx, spec, canonical, operation_id)

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

    def approve(self, prepared: PreparedAction, approver: UserContext) -> Decision:
        return self._decide(prepared, approver, approve=True)

    def deny(self, prepared: PreparedAction, approver: UserContext) -> Decision:
        return self._decide(prepared, approver, approve=False)

    def _decide(self, prepared: PreparedAction, approver: UserContext, approve: bool) -> Decision:
        now = self.clock()
        event = "approve" if approve else "deny"

        def answer(code: Code, explanation: str | None = None) -> Decision:
            self.store.audit(
                event,
                str(code),
                prepared.org_id,
                run_id=prepared.run_id,
                operation_id=prepared.operation_id,
                user_id=approver.user_id,
                tool=prepared.tool,
                now=now,
            )
            return Decision(
                allowed=code == Code.APPROVED,
                code=code,
                explanation=explanation or explain(code),
                operation_id=prepared.operation_id,
            )

        if approver.org_id != prepared.org_id:
            return answer(Code.IDENTITY_MISMATCH)
        binding = self._validate_prepared(prepared)
        if isinstance(binding, Code):
            return answer(binding)
        _, tp, _ = binding
        if approve and not prepared.needs_approval:
            return answer(Code.APPROVAL_REQUIRED, "This action does not need approval.")
        if (
            tp.approve_permission is None
            or tp.approve_permission not in approver.permissions
            or approver.user_id == prepared.user_id
        ):
            return answer(Code.APPROVER_NOT_ALLOWED)

        limits = self.policy.limits
        result = self.store.decide_approval(
            prepared.org_id,
            prepared.operation_id,
            prepared.run_id,
            approver.user_id,
            prepared.request_hash,
            "approved" if approve else "denied",
            self.clock,
            limits.run_lifetime_seconds,
            limits.approval_lifetime_seconds,
        )
        failures = {
            "already_recorded": Code.ALREADY_RECORDED,
            "denied": Code.DENIED_BY_APPROVER,
            "approval_used": Code.APPROVAL_USED,
            "hash_mismatch": Code.OPERATION_CONFLICT,
            "run_expired": Code.RUN_EXPIRED,
        }
        if result != "recorded":
            return answer(failures[result])
        code = Code.APPROVED if approve else Code.DENIED_BY_APPROVER
        return answer(code)

    def execute(self, prepared: PreparedAction, ctx: UserContext) -> ExecutionResult:
        spec = self._tools.get(prepared.tool)
        if spec is not None and inspect.iscoroutinefunction(spec.handler):
            raise ConfigError("async handler needs aexecute")
        gate = self._gate(prepared, ctx)
        if isinstance(gate, ExecutionResult):
            return gate
        spec, args, info = gate
        t0 = time.perf_counter()
        future = self._pool.submit(spec.handler, args, info)
        try:
            value = future.result(timeout=spec.timeout_seconds)
        except ToolFailed:
            return self._finish(prepared, "failed", Code.FAILED, t0)
        except TimeoutError:
            # The thread keeps running on its own; the outcome is unknown.
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0)
        except Exception:  # noqa: BLE001 any other handler error is uncertain
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0)
        return self._finish(prepared, "succeeded", Code.EXECUTED, t0, value)

    async def aexecute(self, prepared: PreparedAction, ctx: UserContext) -> ExecutionResult:
        gate = self._gate(prepared, ctx)
        if isinstance(gate, ExecutionResult):
            return gate
        spec, args, info = gate
        t0 = time.perf_counter()
        if inspect.iscoroutinefunction(spec.handler):
            call = spec.handler(args, info)
        else:
            loop = asyncio.get_running_loop()
            call = loop.run_in_executor(self._pool, spec.handler, args, info)
        try:
            value = await asyncio.wait_for(call, spec.timeout_seconds)
        except ToolFailed:
            return self._finish(prepared, "failed", Code.FAILED, t0)
        except TimeoutError:
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0)
        except Exception:  # noqa: BLE001 any other handler error is uncertain
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0)
        return self._finish(prepared, "succeeded", Code.EXECUTED, t0, value)

    def operation_status(self, org_id: str, operation_id: str) -> OperationStatus | None:
        row = self.store.get_operation(org_id, operation_id)
        if row is None:
            return None
        return OperationStatus(
            operation_id=row["operation_id"],
            state=row["state"],
            code=Code(row["code"]),
            tool=row["tool"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _gate(
        self, prepared: PreparedAction, ctx: UserContext
    ) -> ExecutionResult | tuple[ToolSpec, dict[str, Any], CallInfo]:
        """Recheck identity and access, then reserve the operation for one dispatch."""
        now = self.clock()

        def refuse(code: Code) -> ExecutionResult:
            self._audit("execute", code, ctx, prepared.tool, prepared.operation_id, now,
                        outcome="denied")
            return ExecutionResult(
                operation_id=prepared.operation_id,
                outcome="denied",
                code=code,
                explanation=explain(code),
            )

        same = (ctx.user_id, ctx.org_id, ctx.run_id) == (
            prepared.user_id,
            prepared.org_id,
            prepared.run_id,
        )
        if not same:
            return refuse(Code.IDENTITY_MISMATCH)
        binding = self._validate_prepared(prepared)
        if isinstance(binding, Code):
            return refuse(binding)
        spec, tp, checked = binding
        code = self._check_access(ctx, spec, tp, checked)
        if code is not None:
            return refuse(code)

        now = self.clock()
        limits = self.policy.limits
        started_at, _ = self.store.touch_run(ctx.org_id, ctx.run_id, now)
        result = self.store.reserve(
            ctx.org_id,
            prepared.operation_id,
            ctx.run_id,
            prepared.request_hash,
            now,
            prepared.needs_approval,
            started_at + limits.run_lifetime_seconds,
            limits.max_actions_per_run,
            clock=self.clock,
        )
        if result == "already_recorded":
            row = self.store.get_operation(ctx.org_id, prepared.operation_id)
            # A row still reserved is running now or was cut off before it finished.
            code = Code.ALREADY_RECORDED if row["state"] == "reserved" else Code(row["code"])
            return ExecutionResult(
                operation_id=prepared.operation_id,
                outcome=row["state"],
                code=code,
                explanation=explain(code),
                replayed=True,
            )
        if result != "reserved":
            return refuse(RESERVE_CODES[result])

        args = checked.model_dump()
        info = CallInfo(
            operation_id=prepared.operation_id,
            run_id=prepared.run_id,
            user_id=prepared.user_id,
            org_id=prepared.org_id,
            tool=spec.name,
            tool_version=spec.version,
        )
        return spec, args, info

    def _validate_prepared(
        self, prepared: PreparedAction
    ) -> Code | tuple[ToolSpec, ToolPolicy, BaseModel]:
        """Bind the argument snapshot to the current registry, policy and stored operation."""
        spec = self._tools.get(prepared.tool)
        if spec is None:
            return Code.UNKNOWN_TOOL
        tp = self.policy.tools.get(prepared.tool)
        if tp is None:
            return Code.NO_POLICY
        try:
            checked = spec.args_model.model_validate(prepared.arguments)
        except (ValidationError, ValueError, AttributeError, TypeError):
            return Code.INVALID_ARGUMENTS
        canonical = json.dumps(checked.model_dump(mode="json"), sort_keys=True,
                               separators=(",", ":"))
        expected_hash = self._request_hash(prepared, spec, canonical, prepared.operation_id)
        needs_approval = self._needs_approval(tp, checked)
        if (prepared.request_hash != expected_hash or prepared.tool_version != spec.version
                or prepared.needs_approval != needs_approval):
            return Code.OPERATION_CONFLICT
        row = self.store.get_operation(prepared.org_id, prepared.operation_id)
        if row is None or (
            row["request_hash"], row["run_id"], row["user_id"], row["tool"],
            row["tool_version"], bool(row["needs_approval"])
        ) != (
            expected_hash, prepared.run_id, prepared.user_id, spec.name,
            spec.version, needs_approval
        ):
            return Code.OPERATION_CONFLICT
        return spec, tp, checked

    def _finish(
        self, prepared: PreparedAction, outcome: str, code: Code, t0: float, value: Any = None
    ) -> ExecutionResult:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        now = self.clock()
        self.store.finish(prepared.org_id, prepared.operation_id, outcome, str(code), now)
        self.store.audit(
            "execute",
            str(code),
            prepared.org_id,
            run_id=prepared.run_id,
            operation_id=prepared.operation_id,
            user_id=prepared.user_id,
            tool=prepared.tool,
            outcome=outcome,
            duration_ms=duration_ms,
            now=now,
        )
        return ExecutionResult(
            operation_id=prepared.operation_id,
            outcome=outcome,
            code=code,
            explanation=explain(code),
            value=value,
            duration_ms=duration_ms,
        )

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

    def _request_hash(
        self, ctx: UserContext | PreparedAction, spec: ToolSpec, canonical: str,
        operation_id: str,
    ) -> str:
        body = json.dumps(
            {
                "tool": spec.name,
                "version": spec.version,
                "policy": self.policy.digest,
                "user_id": ctx.user_id,
                "org_id": ctx.org_id,
                "run_id": ctx.run_id,
                "operation_id": operation_id,
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
        self,
        event: str,
        code: Code,
        ctx: UserContext,
        tool: str,
        operation_id: str,
        now: float,
        outcome: str | None = None,
    ) -> None:
        self.store.audit(
            event,
            str(code),
            ctx.org_id,
            run_id=ctx.run_id,
            operation_id=operation_id,
            user_id=ctx.user_id,
            tool=tool,
            outcome=outcome,
            now=now,
        )
