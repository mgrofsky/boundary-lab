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


def with_counts():
    """SUMMARY plus the counts a 0.3.0 report writes: 21 of 24 caught, 3 of 32 good failed."""
    from redline.intervals import interval

    summary = json.loads(json.dumps(SUMMARY))
    summary["end_to_end"]["counts"] = {
        "bad": {"caught": interval(21, 24)},
        "good": {"wrongly_failed": interval(3, 32)},
    }
    return summary


def test_use_interval_tests_recall_low_end_and_false_alarm_high_end():
    summary = with_counts()
    assert thresholds.check(summary, min_recall=0.8, max_false_alarm=0.1) == []
    lines = thresholds.check(summary, min_recall=0.8, max_false_alarm=0.3, use_interval=True)
    assert lines == [
        "recall on holdout is 0.875 (95% interval 0.690 to 0.957), "
        "its low end is below the minimum of 0.800",
    ]
    lines = thresholds.check(summary, max_false_alarm=0.2, use_interval=True)
    assert lines == [
        "false alarm rate on holdout is 0.094 (95% interval 0.032 to 0.242), "
        "its high end is above the maximum of 0.200",
    ]


def test_use_interval_leaves_kappa_alone():
    summary = with_counts()
    assert thresholds.check(summary, min_kappa=0.7, use_interval=True) == []


def test_use_interval_without_counts_is_an_error():
    with pytest.raises(thresholds.NoCounts, match="no counts"):
        thresholds.check(SUMMARY, min_recall=0.5, use_interval=True)


def test_gate_command_use_interval(tmp_path, capsys):
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(with_counts()))
    args = ["gate", "--summary", str(path), "--min-recall", "0.8"]
    assert cli.main(args) == 0
    assert cli.main(args + ["--use-interval"]) == 1
    assert "0.875 (95% interval 0.690 to 0.957)" in capsys.readouterr().out


def test_gate_command_use_interval_without_counts_exits_2(summary_file, capsys):
    args = ["gate", "--summary", summary_file, "--min-recall", "0.5", "--use-interval"]
    assert cli.main(args) == 2
    assert "no counts" in capsys.readouterr().err
