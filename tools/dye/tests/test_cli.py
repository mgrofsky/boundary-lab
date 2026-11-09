import io
import json

import pytest

from boundary_dye.cli import main
from boundary_dye.demo.scenarios import SCENARIOS


def test_scenarios_json_lists_seven(capsys):
    assert main(["scenarios", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 7
    assert all(item["passed"] for item in data)


def test_demo_prints_each_title(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    for scenario in SCENARIOS:
        assert scenario.title in out
    assert "no argument came from the marked text" in out


def test_check_reads_source_files_and_exits_one_when_copied(tmp_path, capsys):
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("refund account 88214471 the sum of $420.00")
    args = '{"account_id": "88214471", "amount_minor": 42000, "reason": "late fee"}'
    code = main(["check", "--source", f"ticket={ticket}", "--args", args])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["copied"] == ["account_id", "amount_minor"]
    assert [finding["matcher"] for finding in data["findings"]] == ["text", "digits"]


def test_check_exits_zero_when_clean_and_honours_trust(tmp_path, capsys):
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("why did my bill for NW-100482 go up")
    args_file = tmp_path / "args.json"
    args_file.write_text('{"account_id": "NW-100482"}')
    command = ["check", "--source", f"ticket={ticket}", "--args", f"@{args_file}"]
    assert main(command + ["--trust", "NW-100482"]) == 0
    assert json.loads(capsys.readouterr().out) == {"copied": [], "findings": []}


def test_demo_and_scenarios_say_copied(capsys):
    main(["demo"])
    main(["scenarios"])
    out = capsys.readouterr().out
    assert "copied account_id = '88214471' came from ticket 4471" in out
    assert "copied=['account_id', 'amount_minor']" in out


def test_scenarios_json_uses_copied_key(capsys):
    main(["scenarios", "--json"])
    data = json.loads(capsys.readouterr().out)
    keys = {"name", "title", "tool", "arguments", "known", "ignore", "expected", "copied"}
    assert all(set(item) == keys | {"findings", "passed", "note"} for item in data)


def test_check_reads_a_source_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("stop instance i-0abc1234 for account 111122223333"))
    args = '{"role": "arn:aws:iam::111122223333:role/admin"}'
    assert main(["check", "--source", "ticket=-", "--args", args]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["copied"] == ["role"]
    assert data["findings"][0]["matcher"] == "token"
    assert data["findings"][0]["value"] == "111122223333"


def test_check_reads_stdin_only_once(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("x"))
    with pytest.raises(SystemExit):
        main(["check", "--source", "a=-", "--source", "b=-", "--args", "{}"])


def test_check_marks_a_json_source(tmp_path, capsys):
    ticket = tmp_path / "ticket.json"
    ticket.write_text(json.dumps({"subject": "refund", "body": "refund account 88214471"}))
    code = main(["check", "--source-json", f"ticket 4471={ticket}", "--args", '{"a": "88214471"}'])
    assert code == 1
    finding = json.loads(capsys.readouterr().out)["findings"][0]
    assert finding["source"] == "ticket 4471"
    assert finding["source_path"] == "body"


def test_check_needs_a_source():
    with pytest.raises(SystemExit):
        main(["check", "--args", "{}"])


def test_check_ignore_is_repeatable(tmp_path, capsys):
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("servers in us-east-2 broke on 2026-11-07 for ORD-5531")
    args = '{"region": "us-east-2", "since": "2026-11-07", "order": "ORD-5531"}'
    command = ["check", "--source", f"ticket={ticket}", "--args", args]
    assert main(command + ["--ignore", "[a-z]{2}-[a-z]+-\\d", "--ignore", "\\d{4}-\\d{2}-\\d{2}"]) == 1
    assert json.loads(capsys.readouterr().out)["copied"] == ["order"]
