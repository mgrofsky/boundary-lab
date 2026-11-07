import json

from boundary_dye.cli import main
from boundary_dye.demo.scenarios import SCENARIOS


def test_scenarios_json_lists_five(capsys):
    assert main(["scenarios", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 5
    assert all(item["passed"] for item in data)


def test_demo_prints_each_title(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    for scenario in SCENARIOS:
        assert scenario.title in out
    assert "no argument came from the marked text" in out


def test_check_reads_source_files_and_exits_one_when_tainted(tmp_path, capsys):
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("refund account 88214471 the sum of $420.00")
    args = '{"account_id": "88214471", "amount_minor": 42000, "reason": "late fee"}'
    code = main(["check", "--source", f"ticket={ticket}", "--args", args])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["tainted"] == ["account_id", "amount_minor"]
    assert [finding["matcher"] for finding in data["findings"]] == ["text", "digits"]


def test_check_exits_zero_when_clean(tmp_path, capsys):
    ticket = tmp_path / "ticket.txt"
    ticket.write_text("why did my bill go up")
    args_file = tmp_path / "args.json"
    args_file.write_text('{"account_id": "NW-100482"}')
    assert main(["check", "--source", f"ticket={ticket}", "--args", f"@{args_file}"]) == 0
    assert json.loads(capsys.readouterr().out) == {"tainted": [], "findings": []}
