"""The on_event callback and the boundary-latch audit command."""

import json
import logging
from unittest.mock import MagicMock

import pytest

from boundary_latch import cli
from boundary_latch.engine import Latch
from boundary_latch.models import ToolSpec
from boundary_latch.policy import parse_policy
from boundary_latch.reasons import Code
from boundary_latch.store import SQLiteStore

from .conftest import POLICY, LookupArgs, SendMessageArgs, account_hook, make_approver, make_ctx

FIELDS = {"schema", "event", "code", "org_id", "run_id", "operation_id", "user_id", "tool",
          "outcome", "duration_ms", "at", "reference", "summary", "copied", "labels",
          "flow_reason"}
MESSAGE = {"to": "help@northwind.example", "body": "hello"}


def make_latch(clock, store=None, on_event=None):
    engine = Latch(parse_policy(POLICY), store or SQLiteStore(), clock=clock, on_event=on_event)
    engine.register(ToolSpec(name="lookup_account", version="1", args_model=LookupArgs,
                             handler=MagicMock(return_value=1), access_hook=account_hook))
    engine.register(ToolSpec(name="send_message", version="1", args_model=SendMessageArgs,
                             handler=MagicMock(return_value="sent"),
                             describe=lambda args: f"message to {args.to}"))
    return engine


def full_flow(engine):
    prepared = engine.prepare(make_ctx(), "send_message", MESSAGE, "op1", copied=["to"])
    engine.approve(prepared, make_approver(), reference="T-1")
    engine.execute(prepared, make_ctx())
    other = engine.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op2")
    engine.cancel(other, make_ctx())
    engine.prepare(make_ctx(), "lookup_account", {"account_id": "A2"}, "op3")


def test_on_event_gets_every_audit_row(clock):
    events = []
    engine = make_latch(clock, on_event=events.append)
    full_flow(engine)
    rows = engine.store.audit_rows("northwind")
    assert len(events) == len(rows) == 6
    assert [e["event"] for e in events] == ["prepare", "approve", "execute", "prepare",
                                            "cancel", "prepare"]
    for event in events:
        assert set(event) == FIELDS
        assert event["schema"] == 1
    prepare, approve, execute = events[:3]
    assert prepare["summary"] == "message to help@northwind.example"
    assert prepare["copied"] == ["to"]
    assert prepare["code"] == "NEEDS_APPROVAL"
    assert approve["reference"] == "T-1"
    assert approve["user_id"] == "jordan"
    assert execute["outcome"] == "succeeded"
    assert execute["reference"] == "T-1"
    assert isinstance(execute["duration_ms"], int)
    assert events[4]["code"] == "CANCELLED"
    assert events[5]["code"] == "ACCESS_DENIED"
    json.dumps(events)


def test_on_event_errors_are_logged_and_ignored(clock, caplog):
    def broken(event):
        raise RuntimeError("listener down")

    engine = make_latch(clock, on_event=broken)
    with caplog.at_level(logging.WARNING, logger="boundary_latch"):
        prepared = engine.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")
        result = engine.execute(prepared, make_ctx())
    assert result.code == Code.EXECUTED
    assert result.recorded is True
    warnings = [r for r in caplog.records if r.name == "boundary_latch"]
    assert len(warnings) == 2
    assert all(r.levelno == logging.WARNING for r in warnings)


def test_no_event_when_the_audit_row_was_not_written(clock, monkeypatch):
    events = []
    engine = make_latch(clock, on_event=events.append)
    prepared = engine.prepare(make_ctx(), "lookup_account", {"account_id": "A1"}, "op1")

    def fail(*args, **kwargs):
        from boundary_latch.errors import StorageError

        raise StorageError("disk full")

    monkeypatch.setattr(engine.store, "audit", fail)
    result = engine.execute(prepared, make_ctx())
    assert result.recorded is False
    assert [e["event"] for e in events] == ["prepare"]


# The audit command


@pytest.fixture
def database(tmp_path, clock):
    path = tmp_path / "latch.sqlite"
    store = SQLiteStore(path)
    full_flow(make_latch(clock, store=store))
    other = make_latch(clock, store=store)
    other.prepare(make_ctx(org_id="contoso"), "lookup_account", {"account_id": "A1"}, "x1")
    store.close()
    return path


def test_audit_prints_one_line_per_row(database, capsys):
    assert cli.main(["audit", str(database)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 7
    assert "prepare NEEDS_APPROVAL" in lines[0]
    assert "op=op1" in lines[0]
    assert "summary=message to help@northwind.example" in lines[0]
    assert "ref=T-1" in lines[1]
    assert "contoso" in lines[-1]


def test_audit_json_prints_the_event_dicts(database, capsys):
    assert cli.main(["audit", str(database), "--json", "--org", "northwind"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 6
    assert all(set(event) == FIELDS and event["schema"] == 1 for event in events)
    assert events[0]["copied"] == ["to"]


def test_audit_last_n(database, capsys):
    assert cli.main(["audit", str(database), "--last", "2", "--json"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["operation_id"] for e in events] == ["op3", "x1"]


def test_audit_follow_prints_new_rows_until_interrupted(database, capsys, monkeypatch, clock):
    polls = []

    def fake_sleep(seconds):
        polls.append(seconds)
        if len(polls) == 1:
            store = SQLiteStore(database)
            make_latch(clock, store=store).prepare(
                make_ctx(), "lookup_account", {"account_id": "A1"}, "late"
            )
            store.close()
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    assert cli.main(["audit", str(database), "--org", "northwind", "--follow", "--json"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) == 7
    assert events[-1]["operation_id"] == "late"
    assert polls == [1, 1]


def test_audit_missing_file(tmp_path, capsys):
    assert cli.main(["audit", str(tmp_path / "nope.sqlite")]) == 1
    assert "no Latch database" in capsys.readouterr().err
    assert not (tmp_path / "nope.sqlite").exists()


def test_a_dotted_mcp_tool_name_is_stored_and_printed_as_one_token(clock, tmp_path):
    policy = parse_policy('[tools."files.read-text"]\nrequire_permission = "files"\n')
    store = SQLiteStore(tmp_path / "latch.db")
    engine = Latch(policy, store, clock=clock)
    engine.register(ToolSpec(name="files.read-text", version="1", args_model=LookupArgs,
                             handler=MagicMock(return_value="text")))
    ctx = make_ctx(permissions={"files"})
    prepared = engine.prepare(ctx, "files.read-text", {"account_id": "A1"}, "op1")
    assert engine.execute(prepared, ctx).code == Code.EXECUTED
    rows = store.audit_rows("northwind")
    assert {row["tool"] for row in rows} == {"files.read-text"}
    line = cli.format_line(cli.to_event(rows[-1]))
    assert " tool=files.read-text " in line
