from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .errors import StorageError

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  org_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at REAL NOT NULL,
  submissions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (org_id, run_id));
CREATE TABLE IF NOT EXISTS operations (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, run_id TEXT NOT NULL,
  user_id TEXT NOT NULL, tool TEXT NOT NULL, tool_version TEXT NOT NULL,
  request_hash TEXT NOT NULL, needs_approval INTEGER NOT NULL,
  state TEXT NOT NULL, code TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (org_id, operation_id));
CREATE TABLE IF NOT EXISTS approvals (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, approver_id TEXT NOT NULL,
  request_hash TEXT NOT NULL, decision TEXT NOT NULL, decided_at REAL NOT NULL,
  expires_at REAL NOT NULL, consumed_at REAL,
  PRIMARY KEY (org_id, operation_id));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, org_id TEXT NOT NULL,
  run_id TEXT, operation_id TEXT, user_id TEXT, tool TEXT, event TEXT NOT NULL,
  code TEXT NOT NULL, outcome TEXT, duration_ms INTEGER);
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
)

RECORDED = ("reserved", "succeeded", "failed", "uncertain")


class OperationExists(Exception):
    """An operation with this org and operation id is already stored."""


class ApprovalExists(Exception):
    """An approval or denial for this operation is already stored."""


class _Connection(sqlite3.Connection):
    """A plain connection that tests may patch, since the base type is read only."""


class SQLiteStore:
    """Runs, operations, approvals and audit rows in one SQLite database.

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
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the store: {exc}") from exc

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
                    "INSERT OR IGNORE INTO runs (org_id, run_id, started_at) VALUES (?, ?, ?)",
                    (org_id, run_id, now),
                )
                row = self._conn.execute(
                    "SELECT started_at, submissions FROM runs WHERE org_id = ? AND run_id = ?",
                    (org_id, run_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StorageError(f"touch_run failed: {exc}") from exc
            return row["started_at"], row["submissions"]

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
                    tuple(row[name] for name in OPERATION_COLUMNS),
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
    ) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO approvals (org_id, operation_id, approver_id, request_hash,"
                    " decision, decided_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (org_id, operation_id, approver_id, request_hash, decision, now, expires_at),
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
    ) -> str:
        """Reserve an operation for one dispatch inside a single transaction.

        Returns "reserved", "already_recorded", "denied", "approval_missing",
        "approval_expired", "run_expired", "run_limit" or "hash_mismatch".
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                result = self._reserve(
                    org_id,
                    operation_id,
                    run_id,
                    expected_hash,
                    now,
                    needs_approval,
                    run_deadline,
                    max_actions,
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
    ) -> str:
        op = self._conn.execute(
            "SELECT state, request_hash FROM operations WHERE org_id = ? AND operation_id = ?",
            (org_id, operation_id),
        ).fetchone()
        if op is None or op["request_hash"] != expected_hash:
            return "hash_mismatch"
        if op["state"] in RECORDED:
            return "already_recorded"
        if op["state"] == "denied":
            return "denied"
        if now > run_deadline:
            return "run_expired"
        placeholders = ", ".join("?" for _ in RECORDED)
        used = self._conn.execute(
            "SELECT COUNT(*) FROM operations WHERE org_id = ? AND run_id = ?"
            f" AND state IN ({placeholders})",
            (org_id, run_id, *RECORDED),
        ).fetchone()[0]
        if used >= max_actions:
            return "run_limit"
        if needs_approval:
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
        return "reserved"

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
    ) -> None:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO audit (at, org_id, run_id, operation_id, user_id, tool, event,"
                    " code, outcome, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, org_id, run_id, operation_id, user_id, tool, event, code, outcome,
                     duration_ms),
                )
            except sqlite3.Error as exc:
                raise StorageError(f"audit failed: {exc}") from exc

    def audit_rows(self, org_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM audit WHERE org_id = ? ORDER BY id", (org_id,)
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"audit_rows failed: {exc}") from exc
            return [dict(row) for row in rows]
