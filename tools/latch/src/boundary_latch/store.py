from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import StorageError

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  org_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at REAL NOT NULL,
  submissions INTEGER NOT NULL DEFAULT 0, last_at REAL, labels TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (org_id, run_id));
CREATE TABLE IF NOT EXISTS operations (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, run_id TEXT NOT NULL,
  user_id TEXT NOT NULL, tool TEXT NOT NULL, tool_version TEXT NOT NULL,
  request_hash TEXT NOT NULL, needs_approval INTEGER NOT NULL,
  state TEXT NOT NULL, code TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  copied TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
  flow_reason TEXT NOT NULL DEFAULT '', PRIMARY KEY (org_id, operation_id));
CREATE TABLE IF NOT EXISTS approvals (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, approver_id TEXT NOT NULL,
  request_hash TEXT NOT NULL, decision TEXT NOT NULL, decided_at REAL NOT NULL,
  expires_at REAL NOT NULL, consumed_at REAL, reference TEXT,
  flow_reason TEXT NOT NULL DEFAULT '', PRIMARY KEY (org_id, operation_id));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, org_id TEXT NOT NULL,
  run_id TEXT, operation_id TEXT, user_id TEXT, tool TEXT, event TEXT NOT NULL,
  code TEXT NOT NULL, outcome TEXT, duration_ms INTEGER, summary TEXT, copied TEXT,
  reference TEXT, labels TEXT, flow_reason TEXT);
"""

OPERATION_COLUMNS = (
    "org_id",
    "operation_id",
    "run_id",
    "user_id",
    "tool",
    "tool_version",
    "request_hash",
    "needs_approval",
    "state",
    "code",
    "created_at",
    "updated_at",
    "copied",
    "summary",
    "flow_reason",
)

# Values for the optional operation columns when a caller leaves them out.
OPERATION_DEFAULTS: dict[str, Any] = {"copied": "[]", "summary": "", "flow_reason": ""}

# Columns added after 0.2.0. A database written by an older release gets them when opened.
ADDED_COLUMNS = {
    "runs": [("last_at", "REAL"), ("labels", "TEXT NOT NULL DEFAULT '{}'")],
    "operations": [
        ("copied", "TEXT NOT NULL DEFAULT '[]'"),
        ("summary", "TEXT NOT NULL DEFAULT ''"),
        ("flow_reason", "TEXT NOT NULL DEFAULT ''"),
    ],
    "approvals": [("reference", "TEXT"), ("flow_reason", "TEXT NOT NULL DEFAULT ''")],
    "audit": [("summary", "TEXT"), ("copied", "TEXT"), ("reference", "TEXT"),
              ("labels", "TEXT"), ("flow_reason", "TEXT")],
}

RECORDED = ("reserved", "succeeded", "failed", "uncertain")
# States that can never run again. The operation row's code says why.
CLOSED = ("denied", "cancelled")


# Called inside the reserve transaction with the run's labels. Returns (mode, reason)
# when the flow rule gates the call, or None.
FlowCheck = Callable[[dict[str, dict[str, Any]]], "tuple[str, str] | None"]


def run_deadline(started_at: float, last_at: float | None, lifetime: int, idle: int) -> float:
    """The time a run expires. A lifetime or idle time of 0 sets no limit."""
    deadline = math.inf
    if lifetime:
        deadline = started_at + lifetime
    if idle:
        deadline = min(deadline, (started_at if last_at is None else last_at) + idle)
    return deadline


class OperationExists(Exception):
    """An operation with this organization and operation ID is already stored."""


class ApprovalExists(Exception):
    """An approval or denial for this operation is already stored."""


class _Connection(sqlite3.Connection):
    """A connection tests can change, since the base type is read-only."""


class SQLiteStore:
    """Runs, operations, approvals, and audit records in one SQLite database.

    One connection is shared by every thread, and a lock guards every method.
    Every sqlite3 error becomes StorageError.
    """

    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(
                str(path),
                isolation_level=None,
                check_same_thread=False,
                factory=_Connection,
            )
            self._conn.row_factory = sqlite3.Row
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._add_missing_columns()
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the store: {exc}") from exc

    def _add_missing_columns(self) -> None:
        for table, columns in ADDED_COLUMNS.items():
            have = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                raise StorageError(f"could not close the store: {exc}") from exc

    def touch_run(self, org_id: str, run_id: str, now: float) -> tuple[float, int]:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO runs (org_id, run_id, started_at, last_at)"
                    " VALUES (?, ?, ?, ?)",
                    (org_id, run_id, now, now),
                )
                row = self._conn.execute(
                    "SELECT started_at, submissions FROM runs WHERE org_id = ? AND run_id = ?",
                    (org_id, run_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"touch_run failed: {exc}") from exc
            return row["started_at"], row["submissions"]

    def last_touch(self, org_id: str, run_id: str) -> float | None:
        """When the run last had activity, or when it started for a run from 0.2.0."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(last_at, started_at) AS last FROM runs"
                    " WHERE org_id = ? AND run_id = ?",
                    (org_id, run_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"last_touch failed: {exc}") from exc
            return None if row is None else row["last"]

    def mark_touch(self, org_id: str, run_id: str, now: float) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE runs SET last_at = MAX(COALESCE(last_at, started_at), ?)"
                    " WHERE org_id = ? AND run_id = ?",
                    (now, org_id, run_id),
                )
            except sqlite3.Error as exc:
                raise StorageError(f"mark_touch failed: {exc}") from exc

    def count_submission(self, org_id: str, run_id: str) -> int:
        with self._lock:
            try:
                row = self._conn.execute(
                    "UPDATE runs SET submissions = submissions + 1"
                    " WHERE org_id = ? AND run_id = ? RETURNING submissions",
                    (org_id, run_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"count_submission failed: {exc}") from exc
            if row is None:
                raise StorageError(f"run {run_id} was never started")
            return row["submissions"]

    def get_operation(self, org_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM operations WHERE org_id = ? AND operation_id = ?",
                    (org_id, operation_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"get_operation failed: {exc}") from exc
            return None if row is None else dict(row)

    def insert_operation(self, row: dict[str, Any]) -> None:
        columns = ", ".join(OPERATION_COLUMNS)
        marks = ", ".join("?" for _ in OPERATION_COLUMNS)
        with self._lock:
            try:
                self._conn.execute(
                    f"INSERT INTO operations ({columns}) VALUES ({marks})",
                    tuple(row.get(name, OPERATION_DEFAULTS.get(name)) for name in OPERATION_COLUMNS),
                )
            except sqlite3.IntegrityError as exc:
                raise OperationExists(row["operation_id"]) from exc
            except sqlite3.Error as exc:
                raise StorageError(f"insert_operation failed: {exc}") from exc

    def set_state(self, org_id: str, operation_id: str, state: str, code: str, now: float) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE operations SET state = ?, code = ?, updated_at = ?"
                    " WHERE org_id = ? AND operation_id = ?",
                    (state, code, now, org_id, operation_id),
                )
            except sqlite3.Error as exc:
                raise StorageError(f"set_state failed: {exc}") from exc

    def record_approval(
        self,
        org_id: str,
        operation_id: str,
        approver_id: str,
        request_hash: str,
        decision: str,
        now: float,
        expires_at: float,
        reference: str | None = None,
        flow_reason: str = "",
    ) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO approvals (org_id, operation_id, approver_id, request_hash,"
                    " decision, decided_at, expires_at, reference, flow_reason)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (org_id, operation_id, approver_id, request_hash, decision, now, expires_at,
                     reference, flow_reason),
                )
            except sqlite3.IntegrityError as exc:
                raise ApprovalExists(operation_id) from exc
            except sqlite3.Error as exc:
                raise StorageError(f"record_approval failed: {exc}") from exc

    def get_approval(self, org_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM approvals WHERE org_id = ? AND operation_id = ?",
                    (org_id, operation_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"get_approval failed: {exc}") from exc
            return None if row is None else dict(row)

    def decide_approval(
        self, org_id: str, operation_id: str, run_id: str, approver_id: str,
        expected_hash: str, decision: str, clock: Callable[[], float],
        run_lifetime: int, approval_lifetime: int, reference: str | None = None,
        run_idle: int = 0, flow_reason: str = "",
    ) -> str:
        """Save the decision and update the pending operation together, or save neither.

        flow_reason is the flow reason the approver saw, kept with the approval.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                now = clock()
                op = self._conn.execute(
                    "SELECT * FROM operations WHERE org_id = ? AND operation_id = ?",
                    (org_id, operation_id),
                ).fetchone()
                run = self._conn.execute(
                    "SELECT started_at, last_at FROM runs WHERE org_id = ? AND run_id = ?",
                    (org_id, run_id),
                ).fetchone()
                if op is None or op["request_hash"] != expected_hash or op["run_id"] != run_id:
                    result = "hash_mismatch"
                elif op["state"] in RECORDED:
                    result = "already_recorded"
                elif op["state"] in CLOSED:
                    result = "closed"
                elif self.get_approval(org_id, operation_id) is not None:
                    result = "approval_used"
                elif run is None or now >= run_deadline(
                    run["started_at"], run["last_at"], run_lifetime, run_idle
                ):
                    result = "run_expired"
                else:
                    end = run_deadline(run["started_at"], None, run_lifetime, 0)
                    expires_at = min(now + approval_lifetime, end)
                    self.record_approval(org_id, operation_id, approver_id, expected_hash,
                                         decision, now, expires_at, reference, flow_reason)
                    code = "APPROVED" if decision == "approved" else "DENIED_BY_APPROVER"
                    self.set_state(org_id, operation_id, decision, code, now)
                    result = "recorded"
                self._conn.execute("COMMIT")
            except (sqlite3.Error, StorageError) as exc:
                if self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise StorageError(f"decide_approval failed: {exc}") from exc
            return result

    def reserve(
        self,
        org_id: str,
        operation_id: str,
        run_id: str,
        expected_hash: str,
        now: float,
        needs_approval: bool,
        run_deadline: float,
        max_actions: int,
        *,
        clock: Callable[[], float] | None = None,
        flow_check: FlowCheck | None = None,
        add_labels: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        """Reserve an operation to run once, saving all changes together.

        Use the approval requirement and run ID saved in the database.
        flow_check gets the run's labels as they are inside this transaction and
        returns (mode, reason) when the flow rule gates the call, or None.
        add_labels are written to the run, with the time, when the call is reserved.
        Returns "reserved", "already_recorded", "closed", "approval_missing",
        "approval_expired", "run_expired", "run_limit", "flow_blocked",
        "flow_approval" or "hash_mismatch".
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if clock is not None:
                    now = clock()
                result = self._reserve(
                    org_id,
                    operation_id,
                    run_id,
                    expected_hash,
                    now,
                    needs_approval,
                    run_deadline,
                    max_actions,
                    flow_check,
                    add_labels or {},
                )
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                if self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise StorageError(f"reserve failed: {exc}") from exc
            return result

    def _reserve(
        self,
        org_id: str,
        operation_id: str,
        run_id: str,
        expected_hash: str,
        now: float,
        needs_approval: bool,
        run_deadline: float,
        max_actions: int,
        flow_check: FlowCheck | None,
        add_labels: dict[str, dict[str, Any]],
    ) -> str:
        op = self._conn.execute(
            "SELECT state, request_hash, run_id, needs_approval FROM operations"
            " WHERE org_id = ? AND operation_id = ?",
            (org_id, operation_id),
        ).fetchone()
        if op is None or op["request_hash"] != expected_hash or op["run_id"] != run_id:
            return "hash_mismatch"
        if op["state"] in RECORDED:
            return "already_recorded"
        if op["state"] in CLOSED:
            return "closed"
        if now >= run_deadline:
            return "run_expired"
        placeholders = ", ".join("?" for _ in RECORDED)
        used = self._conn.execute(
            "SELECT COUNT(*) FROM operations WHERE org_id = ? AND run_id = ?"
            f" AND state IN ({placeholders})",
            (org_id, run_id, *RECORDED),
        ).fetchone()[0]
        if used >= max_actions:
            # Refused for good: the operation is closed with the RUN_LIMIT code.
            self._conn.execute(
                "UPDATE operations SET state = 'denied', code = 'RUN_LIMIT', updated_at = ?"
                " WHERE org_id = ? AND operation_id = ?",
                (now, org_id, operation_id),
            )
            return "run_limit"
        held = self._labels(org_id, run_id)
        gate = flow_check(held) if flow_check is not None else None
        if gate is not None:
            refused = self._flow_refusal(org_id, operation_id, now, *gate)
            if refused is not None:
                return refused
        if op["needs_approval"] or gate is not None:
            approval = self._conn.execute(
                "SELECT * FROM approvals WHERE org_id = ? AND operation_id = ?",
                (org_id, operation_id),
            ).fetchone()
            if approval is None or approval["decision"] != "approved":
                return "approval_missing"
            if approval["consumed_at"] is not None:
                return "already_recorded"
            if approval["request_hash"] != expected_hash:
                return "hash_mismatch"
            if approval["expires_at"] <= now:
                return "approval_expired"
            self._conn.execute(
                "UPDATE approvals SET consumed_at = ? WHERE org_id = ? AND operation_id = ?",
                (now, org_id, operation_id),
            )
        self._conn.execute(
            "UPDATE operations SET state = 'reserved', updated_at = ?"
            " WHERE org_id = ? AND operation_id = ?",
            (now, org_id, operation_id),
        )
        self._conn.execute(
            "UPDATE runs SET last_at = MAX(COALESCE(last_at, started_at), ?)"
            " WHERE org_id = ? AND run_id = ?",
            (now, org_id, run_id),
        )
        self._add_labels(org_id, run_id, held, add_labels, now)
        return "reserved"

    def _flow_refusal(
        self, org_id: str, operation_id: str, now: float, mode: str, reason: str
    ) -> str | None:
        """Close or hold back a call the flow rule gates. None when it may go on.

        In approve mode the call goes on only with an approval given for this reason.
        Otherwise the operation goes back to waiting, so a person sees the new reason.
        """
        if mode == "deny":
            self._conn.execute(
                "UPDATE operations SET state = 'denied', code = 'FLOW_BLOCKED',"
                " flow_reason = ?, updated_at = ? WHERE org_id = ? AND operation_id = ?",
                (reason, now, org_id, operation_id),
            )
            return "flow_blocked"
        approval = self._conn.execute(
            "SELECT decision, flow_reason FROM approvals WHERE org_id = ? AND operation_id = ?",
            (org_id, operation_id),
        ).fetchone()
        if approval is not None and approval["decision"] == "approved" \
                and approval["flow_reason"] == reason:
            return None
        self._conn.execute(
            "DELETE FROM approvals WHERE org_id = ? AND operation_id = ?"
            " AND decision = 'approved' AND consumed_at IS NULL",
            (org_id, operation_id),
        )
        self._conn.execute(
            "UPDATE operations SET state = 'prepared', code = 'NEEDS_APPROVAL',"
            " needs_approval = 1, flow_reason = ?, updated_at = ?"
            " WHERE org_id = ? AND operation_id = ?",
            (reason, now, org_id, operation_id),
        )
        return "flow_approval"

    def _labels(self, org_id: str, run_id: str) -> dict[str, dict[str, Any]]:
        row = self._conn.execute(
            "SELECT labels FROM runs WHERE org_id = ? AND run_id = ?", (org_id, run_id)
        ).fetchone()
        return {} if row is None or not row["labels"] else json.loads(row["labels"])

    def _add_labels(
        self, org_id: str, run_id: str, held: dict[str, dict[str, Any]],
        new: dict[str, dict[str, Any]], now: float,
    ) -> bool:
        """Add labels the run does not hold yet. The first thing that set a label stays."""
        missing = {label: {**entry, "at": now} for label, entry in new.items()
                   if label not in held}
        if not missing:
            return False
        self._conn.execute(
            "UPDATE runs SET labels = ? WHERE org_id = ? AND run_id = ?",
            (json.dumps({**held, **missing}, sort_keys=True), org_id, run_id),
        )
        return True

    def run_labels(self, org_id: str, run_id: str) -> dict[str, dict[str, Any]]:
        """label -> what first set it, for the labels this run holds."""
        with self._lock:
            try:
                return self._labels(org_id, run_id)
            except (sqlite3.Error, ValueError) as exc:
                raise StorageError(f"run_labels failed: {exc}") from exc

    def mark_run(
        self, org_id: str, run_id: str, label: str, entry: dict[str, Any], now: float
    ) -> bool:
        """Start the run if it is new and add a label to it. False if it held it already."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "INSERT OR IGNORE INTO runs (org_id, run_id, started_at, last_at)"
                    " VALUES (?, ?, ?, ?)",
                    (org_id, run_id, now, now),
                )
                added = self._add_labels(org_id, run_id, self._labels(org_id, run_id),
                                         {label: entry}, now)
                self._conn.execute("COMMIT")
            except (sqlite3.Error, ValueError) as exc:
                if self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise StorageError(f"mark_run failed: {exc}") from exc
            return added

    def set_flow(
        self, org_id: str, operation_id: str, needs_approval: bool, flow_reason: str, now: float
    ) -> None:
        """Save a fresh flow answer on an operation that has not run or closed."""
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE operations SET needs_approval = MAX(needs_approval, ?),"
                    " flow_reason = ?, code = CASE WHEN state = 'prepared' AND ? THEN"
                    " 'NEEDS_APPROVAL' ELSE code END, updated_at = ?"
                    " WHERE org_id = ? AND operation_id = ? AND state IN ('prepared', 'approved')",
                    (int(needs_approval), flow_reason, int(needs_approval), now, org_id,
                     operation_id),
                )
            except sqlite3.Error as exc:
                raise StorageError(f"set_flow failed: {exc}") from exc

    def cancel(self, org_id: str, operation_id: str, expected_hash: str, now: float) -> str:
        """Mark a prepared or approved operation cancelled, unless it was reserved or closed.

        Returns "cancelled", "already_recorded", "closed" or "hash_mismatch".
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                op = self._conn.execute(
                    "SELECT state, request_hash FROM operations"
                    " WHERE org_id = ? AND operation_id = ?",
                    (org_id, operation_id),
                ).fetchone()
                if op is None or op["request_hash"] != expected_hash:
                    result = "hash_mismatch"
                elif op["state"] in RECORDED:
                    result = "already_recorded"
                elif op["state"] in CLOSED:
                    result = "closed"
                else:
                    self._conn.execute(
                        "UPDATE operations SET state = 'cancelled', code = 'CANCELLED',"
                        " updated_at = ? WHERE org_id = ? AND operation_id = ?",
                        (now, org_id, operation_id),
                    )
                    result = "cancelled"
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                if self._conn.in_transaction:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise StorageError(f"cancel failed: {exc}") from exc
            return result

    def pending(self, org_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        """Prepared operations that need approval and have no decision yet, oldest first."""
        query = (
            "SELECT o.* FROM operations o WHERE o.org_id = ? AND o.state = 'prepared'"
            " AND o.needs_approval = 1 AND NOT EXISTS (SELECT 1 FROM approvals a"
            " WHERE a.org_id = o.org_id AND a.operation_id = o.operation_id)"
        )
        params: list[Any] = [org_id]
        if run_id is not None:
            query += " AND o.run_id = ?"
            params.append(run_id)
        query += " ORDER BY o.created_at, o.rowid"
        with self._lock:
            try:
                rows = self._conn.execute(query, params).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"pending failed: {exc}") from exc
            return [dict(row) for row in rows]

    def finish(self, org_id: str, operation_id: str, outcome: str, code: str, now: float) -> None:
        self.set_state(org_id, operation_id, outcome, code, now)

    def audit(
        self,
        event: str,
        code: str,
        org_id: str,
        run_id: str | None = None,
        operation_id: str | None = None,
        user_id: str | None = None,
        tool: str | None = None,
        outcome: str | None = None,
        duration_ms: int | None = None,
        now: float = 0.0,
        summary: str | None = None,
        copied: str | None = None,
        reference: str | None = None,
        labels: str | None = None,
        flow_reason: str | None = None,
    ) -> dict[str, Any]:
        """Write one audit row and return it as a dict."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "INSERT INTO audit (at, org_id, run_id, operation_id, user_id, tool, event,"
                    " code, outcome, duration_ms, summary, copied, reference, labels,"
                    " flow_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " RETURNING *",
                    (now, org_id, run_id, operation_id, user_id, tool, event, code, outcome,
                     duration_ms, summary, copied, reference, labels, flow_reason),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"audit failed: {exc}") from exc
            return dict(row)

    def audit_rows(self, org_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE org_id = ? ORDER BY id", (org_id,)
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"audit_rows failed: {exc}") from exc
            return [dict(row) for row in rows]


def read_audit(
    path: str | Path, org_id: str | None = None, after_id: int = 0, last: int | None = None
) -> list[dict[str, Any]]:
    """Read audit rows from a database file without changing it, oldest first.

    after_id skips rows up to that id. last keeps only the newest rows.
    """
    try:
        conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise StorageError(f"could not open {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM audit WHERE id > ?"
    params: list[Any] = [after_id]
    if org_id is not None:
        query += " AND org_id = ?"
        params.append(org_id)
    query += " ORDER BY id DESC" if last is not None else " ORDER BY id"
    if last is not None:
        query += " LIMIT ?"
        params.append(last)
    try:
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.Error as exc:
        raise StorageError(f"could not read the audit table: {exc}") from exc
    finally:
        conn.close()
    return rows[::-1] if last is not None else rows
