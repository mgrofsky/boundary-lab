from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import logging
import math
import numbers
import time
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from . import flow
from .errors import ConfigError, StorageError, ToolFailed
from .events import to_event
from .models import (
    CallInfo,
    Decision,
    ExecutionResult,
    OperationStatus,
    PreparedAction,
    ToolSpec,
    UserContext,
)
from .policy import LABELS, Policy, ToolPolicy, load_policy
from .reasons import Code, explain
from .store import CLOSED, RECORDED, FlowCheck, OperationExists, SQLiteStore, run_deadline

logger = logging.getLogger("boundary_latch")

SUMMARY_LIMIT = 300

REFERENCE_LIMIT = 128
ERROR_LIMIT = 200
NOTE_LIMIT = 200

# Returned by field_value when the arguments have no value for a policy field.
NO_VALUE = object()

RESERVE_CODES = {
    "approval_missing": Code.APPROVAL_REQUIRED,
    "approval_expired": Code.APPROVAL_EXPIRED,
    "run_expired": Code.RUN_EXPIRED,
    "run_limit": Code.RUN_LIMIT,
    "hash_mismatch": Code.OPERATION_CONFLICT,
}


def field_value(args: BaseModel, name: str) -> Any:
    """The value of the argument a policy names, or NO_VALUE when there is none.

    The name matches a field's name first, then a field's alias, so a model
    whose fields use aliases still works.
    """
    fields = type(args).model_fields
    if name in fields:
        return getattr(args, name)
    for key, field in fields.items():
        if field.alias == name:
            return getattr(args, key)
    return NO_VALUE


def amount_value(args: BaseModel, name: str) -> Any:
    """The amount as a finite number, or None when it is missing or not a number.

    A bool is not an amount, and neither is NaN or infinity, since each would
    compare in a way that could skip the approval the policy asks for.
    """
    value = field_value(args, name)
    if isinstance(value, bool) or not isinstance(value, numbers.Real | Decimal):
        return None
    try:
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, ArithmeticError):
        return None


def error_text(exc: BaseException) -> str:
    """The error's type name, a colon, a space, and the first line of its message."""
    lines = str(exc).splitlines()
    return f"{type(exc).__name__}: {lines[0] if lines else ''}"[:ERROR_LIMIT]


class Latch:
    """Checks tool calls against a policy before they run."""

    def __init__(
        self,
        policy: Policy,
        store: SQLiteStore,
        clock: Callable[[], float] = time.time,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ):
        """on_event, if set, is called with each audit row as a dict after it is written."""
        self.policy, self.store, self.clock = policy, store, clock
        self.on_event = on_event
        self._tools: dict[str, ToolSpec] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="latch"
        )

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        store: SQLiteStore,
        clock: Callable[[], float] = time.time,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> Latch:
        return cls(load_policy(path), store, clock, on_event)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ConfigError(f"tool {spec.name} is already registered")
        self._set_tool(spec)

    def _set_tool(self, spec: ToolSpec) -> None:
        """Add a tool or replace one with the same name.

        The MCP proxy uses this when a person accepts a changed tool definition.
        An operation prepared for the old version then returns OPERATION_CONFLICT.
        """
        if spec.name not in self.policy.tools:
            raise ConfigError(f"tool {spec.name} has no policy entry")
        cfg = spec.args_model.model_config
        if cfg.get("extra") != "forbid":
            raise ConfigError(f"args model for {spec.name} must set extra='forbid'")
        self._tools[spec.name] = spec

    def _drop_tool(self, name: str) -> None:
        """Forget a tool, so a call to it returns UNKNOWN_TOOL."""
        self._tools.pop(name, None)

    def tool_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def prepare(
        self,
        ctx: UserContext,
        tool: str,
        arguments: dict[str, Any],
        operation_id: str,
        *,
        copied: Any = (),
    ) -> Decision | PreparedAction:
        """Check a tool call and save it, ready to run.

        copied lists argument paths, such as "account_id" or "items[0].sku", whose
        values were copied from text the model read. It also accepts any object with
        a .copied attribute holding such paths, such as a Dye report.
        """
        requested = self._copied_input(copied)
        now = self.clock()
        limits = self.policy.limits
        deadline = self._deadline(ctx, now)
        if now >= deadline:
            return self._deny_prepare(ctx, tool, operation_id, Code.RUN_EXPIRED, now)
        self.store.mark_touch(ctx.org_id, ctx.run_id, now)

        # Submitting the same operation ID again does not count against the proposal limit.
        existing = self.store.get_operation(ctx.org_id, operation_id)
        if existing is None:
            count = self.store.count_submission(ctx.org_id, ctx.run_id)
            cap = limits.max_proposals_per_run
            if cap is not None and count > cap:
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

        if tp.approval.mode == "over_amount" and amount_value(
            args, tp.approval.amount_field
        ) is None:
            return self._deny_prepare(ctx, tool, operation_id, Code.INVALID_ARGUMENTS, now)

        paths = self._copied_fields(spec, requested)
        code = self._check_access(ctx, spec, tp, args, paths)
        if code is not None:
            return self._deny_prepare(ctx, tool, operation_id, code, now)
        summary = self._summary(spec, args)
        if summary is None:
            return self._deny_prepare(ctx, tool, operation_id, Code.DESCRIBE_FAILED, now)

        now = self.clock()
        if now >= deadline:
            return self._deny_prepare(ctx, tool, operation_id, Code.RUN_EXPIRED, now)

        needs_approval = self._needs_approval(tp, args, paths)
        canonical = json.dumps(args.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        request_hash = self._request_hash(ctx, spec, canonical, operation_id)

        if existing is not None:
            stored = frozenset(json.loads(existing["copied"]))
            # The approver must have seen every copied field, so a new one is a conflict.
            if existing["request_hash"] != request_hash or not paths <= stored:
                return self._deny_prepare(ctx, tool, operation_id, Code.OPERATION_CONFLICT, now)
            if existing["state"] in CLOSED:
                return self._deny_prepare(ctx, tool, operation_id, Code.OPERATION_CLOSED, now)
            needs_approval = bool(existing["needs_approval"])
            paths = stored
            summary = existing["summary"]

        # The flow rule, from the run's labels as they are now. Execute checks it again.
        if existing is not None and existing["state"] in RECORDED:
            flow_reason = existing["flow_reason"]
        else:
            flow_reason = self._flow_reason(
                self.store.run_labels(ctx.org_id, ctx.run_id), spec.name, tp, paths
            )
        if flow_reason and self.policy.flow.all_three == "deny":
            return self._deny_prepare(ctx, tool, operation_id, Code.FLOW_BLOCKED, now,
                                      flow_reason=flow_reason)
        stricter = needs_approval or bool(flow_reason)
        if existing is not None and existing["state"] not in RECORDED and (
            stricter != needs_approval or flow_reason != existing["flow_reason"]
        ):
            self.store.set_flow(ctx.org_id, operation_id, stricter, flow_reason, now)
        needs_approval = stricter
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
                        "copied": json.dumps(sorted(paths)),
                        "summary": summary,
                        "flow_reason": flow_reason,
                    }
                )
            except OperationExists:
                return self._deny_prepare(ctx, tool, operation_id, Code.OPERATION_CONFLICT, now)

        self._audit("prepare", code, ctx, tool, operation_id, now, summary=summary,
                    copied=json.dumps(sorted(paths)), flow_reason=flow_reason)
        return self._build_prepared(ctx, spec, operation_id, needs_approval, request_hash,
                                    canonical, code, paths, summary, flow_reason)

    def mark_run(self, ctx: UserContext, label: str, note: str = "") -> None:
        """Add a label to the run itself, for example when a user uploads a file.

        note is one line of at most 200 characters, kept as the label's source. The
        flow reason names it only as "marked by the app".
        """
        if label not in LABELS:
            raise ValueError(f"label must be one of {', '.join(LABELS)}")
        if not isinstance(note, str):
            raise ValueError("note must be a string")  # noqa: TRY004 one error type for callers
        if len(note) > NOTE_LIMIT:
            raise ValueError(f"note must be at most {NOTE_LIMIT} characters")
        if not note.isprintable():
            raise ValueError("note must be one line with no control characters")
        now = self.clock()
        self.store.mark_run(ctx.org_id, ctx.run_id, label,
                            {"source": note, "operation_id": None}, now)
        self._write_audit(
            "mark_run", str(Code.ALLOWED), ctx.org_id, run_id=ctx.run_id,
            user_id=ctx.user_id, now=now, summary=note, labels=json.dumps([label]),
        )

    def _flow_reason(
        self, held: dict[str, dict[str, Any]], tool: str, tp: ToolPolicy,
        copied: frozenset[str] = frozenset(),
    ) -> str:
        """The flow reason when the rule gates this call, or "" when it does not.

        A call with a copied value is checked as if the run already held untrusted,
        from that copied value. A tool with no labels is still never gated.
        """
        if copied and "untrusted" not in held:
            held = {**held, "untrusted": {"tool": tool, "copied": True}}
        labels = tp.labels or ()
        if not flow.applies(self.policy.flow.all_three, held, labels):
            return ""
        return flow.reason(held, labels, tool)

    def approve(
        self, prepared: PreparedAction, approver: UserContext, reference: str | None = None
    ) -> Decision:
        """Approve a pending request. reference names the evidence, such as a ticket."""
        return self._decide(prepared, approver, True, self._check_reference(reference))

    def deny(
        self, prepared: PreparedAction, approver: UserContext, reference: str | None = None
    ) -> Decision:
        """Deny a pending request. reference names the evidence, such as a ticket."""
        return self._decide(prepared, approver, False, self._check_reference(reference))

    def pending(self, org_id: str, run_id: str | None = None) -> list[OperationStatus]:
        """Prepared operations that need approval and have no decision yet, oldest first."""
        return [self._status(row) for row in self.store.pending(org_id, run_id)]

    def cancel(self, prepared: PreparedAction, by: UserContext) -> Decision:
        """Stop a prepared or approved operation before it runs.

        The requester can cancel, and so can a user in the same organization with the
        tool's approve_permission. A cancelled operation returns CANCELLED.
        """
        now = self.clock()

        def answer(code: Code, explanation: str | None = None) -> Decision:
            self._write_audit(
                "cancel",
                str(code),
                prepared.org_id,
                run_id=prepared.run_id,
                operation_id=prepared.operation_id,
                user_id=by.user_id,
                tool=prepared.tool,
                now=now,
                labels=self._labels_json(prepared.tool),
                flow_reason=prepared.flow_reason or None,
            )
            return Decision(
                allowed=False,
                code=code,
                explanation=explanation or explain(code),
                operation_id=prepared.operation_id,
            )

        if by.org_id != prepared.org_id:
            return answer(Code.IDENTITY_MISMATCH)
        binding = self._validate_prepared(prepared)
        if isinstance(binding, Code):
            return answer(binding)
        _, tp, _ = binding
        requester = by.user_id == prepared.user_id
        approver = tp.approve_permission is not None and tp.approve_permission in by.permissions
        if not (requester or approver):
            return answer(
                Code.APPROVER_NOT_ALLOWED,
                "Only the requester or a user with this tool's approval permission can cancel.",
            )
        result = self.store.cancel(prepared.org_id, prepared.operation_id,
                                   prepared.request_hash, now)
        if result == "cancelled":
            return answer(Code.CANCELLED)
        if result == "closed":
            return answer(self._row_code(prepared))
        if result == "already_recorded":
            return answer(Code.ALREADY_RECORDED)
        return answer(Code.OPERATION_CONFLICT)

    @staticmethod
    def _check_reference(reference: str | None) -> str | None:
        if reference is None:
            return None
        if not isinstance(reference, str):
            raise ValueError("reference must be a string")  # noqa: TRY004 one error type for callers
        if len(reference) > REFERENCE_LIMIT:
            raise ValueError(f"reference must be at most {REFERENCE_LIMIT} characters")
        if not reference.isprintable():
            raise ValueError("reference must not contain control characters")
        return reference

    def _row_code(self, prepared: PreparedAction) -> Code:
        row = self.store.get_operation(prepared.org_id, prepared.operation_id)
        return Code(row["code"])

    def _reference(self, org_id: str, operation_id: str) -> str | None:
        approval = self.store.get_approval(org_id, operation_id)
        return None if approval is None else approval["reference"]

    def _decide(
        self, prepared: PreparedAction, approver: UserContext, approve: bool,
        reference: str | None,
    ) -> Decision:
        now = self.clock()
        event = "approve" if approve else "deny"

        def answer(code: Code, explanation: str | None = None) -> Decision:
            self._write_audit(
                event,
                str(code),
                prepared.org_id,
                run_id=prepared.run_id,
                operation_id=prepared.operation_id,
                user_id=approver.user_id,
                tool=prepared.tool,
                now=now,
                reference=reference,
                labels=self._labels_json(prepared.tool),
                flow_reason=prepared.flow_reason or None,
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
            reference,
            run_idle=limits.run_idle_seconds,
            flow_reason=prepared.flow_reason,
        )
        if result == "closed":
            return answer(self._row_code(prepared))
        failures = {
            "already_recorded": Code.ALREADY_RECORDED,
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
        except ToolFailed as exc:
            return self._finish(prepared, "failed", Code.FAILED, t0, error=exc)
        except TimeoutError as exc:
            # The thread keeps running on its own; the outcome is unknown.
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0, error=exc)
        except Exception as exc:  # noqa: BLE001 any other handler error is uncertain
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0, error=exc)
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
        except ToolFailed as exc:
            return self._finish(prepared, "failed", Code.FAILED, t0, error=exc)
        except TimeoutError as exc:
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0, error=exc)
        except Exception as exc:  # noqa: BLE001 any other handler error is uncertain
            return self._finish(prepared, "uncertain", Code.UNCERTAIN, t0, error=exc)
        return self._finish(prepared, "succeeded", Code.EXECUTED, t0, value)

    def operation_status(self, org_id: str, operation_id: str) -> OperationStatus | None:
        row = self.store.get_operation(org_id, operation_id)
        if row is None:
            return None
        return self._status(row)

    def _status(self, row: dict[str, Any]) -> OperationStatus:
        return OperationStatus(
            operation_id=row["operation_id"],
            state=row["state"],
            code=Code(row["code"]),
            tool=row["tool"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            needs_approval=bool(row["needs_approval"]),
            user_id=row["user_id"],
            run_id=row["run_id"],
            summary=row["summary"],
            copied=frozenset(json.loads(row["copied"])),
            reference=self._reference(row["org_id"], row["operation_id"]),
            flow_reason=row["flow_reason"],
        )

    def _gate(
        self, prepared: PreparedAction, ctx: UserContext
    ) -> ExecutionResult | tuple[ToolSpec, dict[str, Any], CallInfo]:
        """Recheck who is asking and their access, then reserve the operation to run once."""
        now = self.clock()

        def refuse(code: Code, flow_reason: str = "") -> ExecutionResult:
            reference = self._reference(prepared.org_id, prepared.operation_id)
            self._audit("execute", code, ctx, prepared.tool, prepared.operation_id, now,
                        outcome="denied", reference=reference, flow_reason=flow_reason)
            return ExecutionResult(
                operation_id=prepared.operation_id,
                outcome="denied",
                code=code,
                explanation=self._explain(code, flow_reason),
                reference=reference,
            )

        same = (ctx.user_id, ctx.org_id, ctx.run_id) == (
            prepared.user_id,
            prepared.org_id,
            prepared.run_id,
        )
        if not same:
            return refuse(Code.IDENTITY_MISMATCH)
        if self._copied_denied(prepared):
            return refuse(Code.COPIED_ARGUMENTS)
        binding = self._validate_prepared(prepared)
        if isinstance(binding, Code):
            return refuse(binding)
        spec, tp, checked = binding
        code = self._check_access(ctx, spec, tp, checked, prepared.copied)
        if code is not None:
            return refuse(code)

        now = self.clock()
        limits = self.policy.limits
        deadline = self._deadline(ctx, now)
        result = self.store.reserve(
            ctx.org_id,
            prepared.operation_id,
            ctx.run_id,
            prepared.request_hash,
            now,
            prepared.needs_approval,
            deadline,
            limits.max_actions_per_run,
            clock=self.clock,
            flow_check=self._flow_check(spec.name, tp, prepared.copied),
            add_labels=self._labels_to_add(spec.name, tp, prepared),
        )
        if result in ("flow_blocked", "flow_approval"):
            row = self.store.get_operation(ctx.org_id, prepared.operation_id)
            code = Code.FLOW_BLOCKED if result == "flow_blocked" else Code.NEEDS_APPROVAL
            return refuse(code, row["flow_reason"])
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
                reference=self._reference(ctx.org_id, prepared.operation_id),
            )
        if result == "closed":
            return refuse(self._row_code(prepared))
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

    def _flow_check(self, tool: str, tp: ToolPolicy, copied: frozenset[str]) -> FlowCheck:
        """The check reserve runs inside its transaction, with the run's labels then."""
        mode = self.policy.flow.all_three

        def check(held: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
            reason = self._flow_reason(held, tool, tp, copied)
            return (mode, reason) if reason else None

        return check

    @staticmethod
    def _labels_to_add(
        tool: str, tp: ToolPolicy, prepared: PreparedAction
    ) -> dict[str, dict[str, Any]]:
        """The labels a call gives the run once it is reserved, with what set each."""
        op = prepared.operation_id
        labels = {label: {"tool": tool, "operation_id": op} for label in tp.labels or ()}
        if prepared.copied:
            labels.setdefault("untrusted", {"tool": tool, "copied": True, "operation_id": op})
        return labels

    @staticmethod
    def _explain(code: Code, flow_reason: str = "") -> str:
        return f"{explain(code)} {flow_reason}" if flow_reason else explain(code)

    def _deadline(self, ctx: UserContext, now: float) -> float:
        """Start the run if it is new and return when it expires."""
        limits = self.policy.limits
        started_at, _ = self.store.touch_run(ctx.org_id, ctx.run_id, now)
        last_at = self.store.last_touch(ctx.org_id, ctx.run_id)
        return run_deadline(started_at, last_at, limits.run_lifetime_seconds,
                            limits.run_idle_seconds)

    def _validate_prepared(
        self, prepared: PreparedAction
    ) -> Code | tuple[ToolSpec, ToolPolicy, BaseModel]:
        """Check that the saved request still matches the tool, policy, and stored operation."""
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
        needs_approval = self._needs_approval(tp, checked, prepared.copied)
        if (prepared.request_hash != expected_hash or prepared.tool_version != spec.version
                or (needs_approval and not prepared.needs_approval)):
            return Code.OPERATION_CONFLICT
        row = self.store.get_operation(prepared.org_id, prepared.operation_id)
        if row is None or (
            row["request_hash"], row["run_id"], row["user_id"], row["tool"],
            row["tool_version"], frozenset(json.loads(row["copied"])),
        ) != (
            expected_hash, prepared.run_id, prepared.user_id, spec.name,
            spec.version, prepared.copied,
        ):
            return Code.OPERATION_CONFLICT
        # The flow rule can raise the stored need for approval after prepare, never lower
        # it. A request copy that needs approval while the stored row does not is a conflict.
        if prepared.needs_approval and not row["needs_approval"]:
            return Code.OPERATION_CONFLICT
        return spec, tp, checked

    def _finish(
        self, prepared: PreparedAction, outcome: str, code: Code, t0: float, value: Any = None,
        error: BaseException | None = None,
    ) -> ExecutionResult:
        """Save the outcome. The handler already ran, so a storage error is reported, not raised."""
        duration_ms = int((time.perf_counter() - t0) * 1000)
        now = self.clock()
        recorded = True
        message = None if error is None else error_text(error)
        reference = None
        try:
            reference = self._reference(prepared.org_id, prepared.operation_id)
            row = self.store.get_operation(prepared.org_id, prepared.operation_id)
            self.store.finish(prepared.org_id, prepared.operation_id, outcome, str(code), now)
            self._write_audit(
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
                reference=reference,
                labels=self._labels_json(prepared.tool),
                flow_reason=(row or {}).get("flow_reason") or None,
            )
        except StorageError as exc:
            recorded, message = False, error_text(exc)
        return ExecutionResult(
            operation_id=prepared.operation_id,
            outcome=outcome,
            code=code,
            explanation=explain(code),
            value=value,
            duration_ms=duration_ms,
            reference=reference,
            error=message,
            recorded=recorded,
        )

    def _check_access(
        self, ctx: UserContext, spec: ToolSpec, tp: ToolPolicy, args: BaseModel,
        copied: frozenset[str],
    ) -> Code | None:
        if tp.require_permission not in ctx.permissions:
            return Code.MISSING_PERMISSION
        if tp.copied_arguments == "deny" and copied:
            return Code.COPIED_ARGUMENTS
        recipients = tp.allowed_recipients or []
        if tp.recipient_field is not None:
            recipient = field_value(args, tp.recipient_field)
            if not isinstance(recipient, str) or recipient not in recipients:
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
    def _summary(spec: ToolSpec, args: BaseModel) -> str | None:
        """One line from the tool's describe function, or None when it fails."""
        if spec.describe is None:
            return ""
        try:
            text = spec.describe(args)
        except Exception:  # noqa: BLE001 any describe error blocks the call
            return None
        if not isinstance(text, str):
            return None
        text = "".join(" " if not ch.isprintable() else ch for ch in text)
        return " ".join(text.split())[:SUMMARY_LIMIT]

    @staticmethod
    def _copied_input(copied: Any) -> frozenset[str]:
        """Read the paths from an iterable of strings or an object with a .copied attribute."""
        if hasattr(copied, "copied"):
            copied = copied.copied
        if isinstance(copied, str):
            copied = (copied,)
        paths = frozenset(copied)
        if not all(isinstance(path, str) for path in paths):
            raise TypeError("copied must hold argument paths as strings")
        return paths

    @staticmethod
    def _copied_fields(spec: ToolSpec, paths: frozenset[str]) -> frozenset[str]:
        """Keep the paths whose top level field names a field of this tool's arguments."""
        fields = spec.args_model.model_fields
        return frozenset(p for p in paths if p.split(".")[0].split("[")[0] in fields)

    def _copied_denied(self, prepared: PreparedAction) -> bool:
        spec = self._tools.get(prepared.tool)
        tp = self.policy.tools.get(prepared.tool)
        if spec is None or tp is None or tp.copied_arguments != "deny":
            return False
        return bool(self._copied_fields(spec, frozenset(prepared.copied)))

    @staticmethod
    def _needs_approval(tp: ToolPolicy, args: BaseModel, copied: frozenset[str]) -> bool:
        if tp.copied_arguments == "approve" and copied:
            return True
        if tp.approval.mode == "always":
            return True
        if tp.approval.mode == "over_amount":
            # A missing or non number amount needs approval, so it never skips the check.
            amount = amount_value(args, tp.approval.amount_field)
            return amount is None or amount > tp.approval.threshold_minor
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
        copied: frozenset[str],
        summary: str,
        flow_reason: str = "",
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
            copied=copied,
            summary=summary,
            flow_reason=flow_reason,
        )
        prepared._args_json = canonical
        return prepared

    def _deny_prepare(
        self, ctx: UserContext, tool: str, operation_id: str, code: Code, now: float,
        flow_reason: str = "",
    ) -> Decision:
        self._audit("prepare", code, ctx, tool, operation_id, now, flow_reason=flow_reason)
        return Decision(
            allowed=False, code=code, explanation=self._explain(code, flow_reason),
            operation_id=operation_id,
        )

    def _labels_json(self, tool: str) -> str | None:
        """The tool's labels from the policy as a JSON list, or None when it sets none."""
        tp = self.policy.tools.get(tool)
        return None if tp is None or tp.labels is None else json.dumps(tp.labels)

    def _write_audit(self, *args: Any, **kwargs: Any) -> None:
        row = self.store.audit(*args, **kwargs)
        if self.on_event is None:
            return
        # A listener error is logged and never changes the decision.
        try:
            self.on_event(to_event(row))
        except Exception:
            logger.warning("on_event failed for audit row %s", row.get("id"), exc_info=True)

    def _audit(
        self,
        event: str,
        code: Code,
        ctx: UserContext,
        tool: str,
        operation_id: str,
        now: float,
        outcome: str | None = None,
        summary: str | None = None,
        copied: str | None = None,
        reference: str | None = None,
        flow_reason: str = "",
    ) -> None:
        self._write_audit(
            event,
            str(code),
            ctx.org_id,
            run_id=ctx.run_id,
            operation_id=operation_id,
            user_id=ctx.user_id,
            tool=tool,
            outcome=outcome,
            now=now,
            summary=summary,
            copied=copied,
            reference=reference,
            labels=self._labels_json(tool),
            flow_reason=flow_reason or None,
        )
