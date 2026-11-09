import sqlite3

from boundary_latch.store import SQLiteStore

# The tables as Latch 0.2.0 created them.
OLD_SCHEMA = """
CREATE TABLE runs (
  org_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at REAL NOT NULL,
  submissions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (org_id, run_id));
CREATE TABLE operations (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, run_id TEXT NOT NULL,
  user_id TEXT NOT NULL, tool TEXT NOT NULL, tool_version TEXT NOT NULL,
  request_hash TEXT NOT NULL, needs_approval INTEGER NOT NULL,
  state TEXT NOT NULL, code TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (org_id, operation_id));
CREATE TABLE approvals (
  org_id TEXT NOT NULL, operation_id TEXT NOT NULL, approver_id TEXT NOT NULL,
  request_hash TEXT NOT NULL, decision TEXT NOT NULL, decided_at REAL NOT NULL,
  expires_at REAL NOT NULL, consumed_at REAL,
  PRIMARY KEY (org_id, operation_id));
CREATE TABLE audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, org_id TEXT NOT NULL,
  run_id TEXT, operation_id TEXT, user_id TEXT, tool TEXT, event TEXT NOT NULL,
  code TEXT NOT NULL, outcome TEXT, duration_ms INTEGER);
"""


def make_old_database(path):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO runs VALUES ('o', 'r', 10.0, 1)")
    conn.execute(
        "INSERT INTO operations VALUES ('o', 'op1', 'r', 'u', 't', '1', 'h', 0,"
        " 'prepared', 'ALLOWED', 10.0, 10.0)"
    )
    conn.execute("INSERT INTO audit (at, org_id, event, code) VALUES (10.0, 'o', 'prepare', 'ALLOWED')")
    conn.commit()
    conn.close()


def columns(store, table):
    return {row["name"] for row in store._conn.execute(f"PRAGMA table_info({table})")}


def test_a_0_2_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    make_old_database(path)
    store = SQLiteStore(path)
    row = store.get_operation("o", "op1")
    assert row["copied"] == "[]"
    assert row["summary"] == ""
    assert {"copied", "summary"} <= columns(store, "operations")
    assert {"summary", "copied", "reference"} <= columns(store, "audit")
    assert "reference" in columns(store, "approvals")
    store.close()
    # Opening it a second time changes nothing.
    again = SQLiteStore(path)
    assert again.get_operation("o", "op1")["copied"] == "[]"
    again.close()
