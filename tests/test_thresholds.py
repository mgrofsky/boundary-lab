import json

import pytest

from redline import cli, thresholds

SUMMARY = {
    "end_to_end": {"bad": {"caught": 0.875}, "good": {"wrongly_failed": 0.09375}},
    "attack_end_to_end": {"bad": {"caught": 0.81}, "good": {"wrongly_failed": 0.15}},
    "judge": {"holdout": {"cohen_kappa": 0.78}, "attack": {"cohen_kappa": 0.61}},
}


def test_no_breach_gives_no_lines():
    assert thresholds.check(SUMMARY, min_recall=0.8, max_false_alarm=0.1, min_kappa=0.7) == []
    assert thresholds.check(SUMMARY) == []


def test_each_breach_gives_one_plain_line():
    lines = thresholds.check(SUMMARY, min_recall=0.99, max_false_alarm=0.05, min_kappa=0.8)
    assert lines == [
        "recall on holdout is 0.875, below the minimum of 0.990",
        "false alarm rate on holdout is 0.094, above the maximum of 0.050",
        "kappa on holdout is 0.780, below the minimum of 0.800",
    ]


def test_attack_reads_the_attack_numbers():
    lines = thresholds.check(SUMMARY, min_recall=0.85, max_false_alarm=0.1, attack=True)
    assert lines == [
        "recall on attack answers is 0.810, below the minimum of 0.850",
        "false alarm rate on attack answers is 0.150, above the maximum of 0.100",
    ]


def test_a_value_on_the_line_holds():
    assert thresholds.check(SUMMARY, min_recall=0.875, max_false_alarm=0.09375) == []


@pytest.fixture
def summary_file(tmp_path):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(SUMMARY))
    return str(path)


def test_gate_command_exits_0_when_every_floor_holds(summary_file, capsys):
    assert cli.main(["gate", "--summary", summary_file, "--min-recall", "0.8"]) == 0
    assert capsys.readouterr().out == ""


def test_gate_command_prints_breaches_and_exits_1(summary_file, capsys):
    args = ["gate", "--summary", summary_file, "--min-recall", "0.99", "--min-kappa", "0.9"]
    assert cli.main(args) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "recall on holdout is 0.875, below the minimum of 0.990",
        "kappa on holdout is 0.780, below the minimum of 0.900",
    ]


def test_gate_command_attack_flag(summary_file, capsys):
    args = ["gate", "--summary", summary_file, "--min-kappa", "0.7", "--attack"]
    assert cli.main(args) == 1
    assert capsys.readouterr().out == "kappa on attack answers is 0.610, below the minimum of 0.700\n"


def test_gate_command_reports_a_missing_summary(tmp_path):
    with pytest.raises(SystemExit, match="no summary"):
        cli.main(["gate", "--summary", str(tmp_path / "missing.json"), "--min-recall", "0.5"])
