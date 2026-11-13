"""grade_report: grade a garak report from Python, with progress, a cap and a clean stop."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import redline
from redline import Grader, cli, policy
from redline import garak as redline_garak
from redline.garak import report
from redline.judge import ChatEndpointJudge, FakeJudge

FIXTURE = Path(__file__).parent / "fixtures" / "garak_report.jsonl"
PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
FILES = ("garak_graded.jsonl", "garak_summary.json", "garak_evals.jsonl")
# The fixture's five outputs in order: words, words, no text, blank, words.
TOTAL = 5


def _read(out):
    """Read the three files. Each must be whole: JSON, or one JSON object per line."""
    assert sorted(p.name for p in Path(out).iterdir()) == sorted(FILES)
    graded, summary, evals = (Path(out) / name for name in FILES)
    for path in (graded, evals):
        assert path.read_text() == "" or path.read_text().endswith("\n")
    rows = [json.loads(line) for line in graded.read_text().splitlines()]
    entries = [json.loads(line) for line in evals.read_text().splitlines()]
    return rows, json.loads(summary.read_text()), entries


class Raises:
    """A judge that answers pass until the call numbered at, which raises."""

    def __init__(self, at):
        self.at = at
        self.calls = 0

    def grade(self, *args):
        self.calls += 1
        if self.calls == self.at:
            raise RuntimeError("the judge broke")
        return dict(PASS)


def test_it_returns_the_summary_and_writes_nothing_without_out_dir(
    rules_data, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(FIXTURE, rules_data, judge)
    assert list(tmp_path.iterdir()) == []
    assert result["report"] == "garak_report.jsonl"
    assert result["mode"] == "rules_only"
    assert result["judge"] == "live"
    assert result["overall"]["graded"] == 4
    assert [r["outcome"] for r in result["rows"]] == ["pass"] * 4
    assert [e["probe"] for e in result["evals"]] == ["test.Test", "test.Blank"]
    assert result["stopped"] is None
    assert result["not_graded"] == {"count": 0, "reasons": {}}
    assert result["judge_calls_made"] == len(judge.calls) == 6
    assert result["estimated_calls"] == 6


def test_the_files_hold_the_return_value_without_rows_and_evals(rules_data, tmp_path):
    out = tmp_path / "out"
    result = redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(FAIL), out_dir=out)
    rows, summary, evals = _read(out)
    assert rows == result["rows"]
    assert evals == result["evals"]
    assert summary == {k: v for k, v in result.items() if k not in ("rows", "evals")}


def test_rules_may_be_a_path_a_policy_or_data(rules_data, write_rules):
    path = write_rules(rules_data)
    for rules in (path, policy.load_policy(path), rules_data):
        result = redline_garak.grade_report(FIXTURE, rules, FakeJudge(PASS), top_k=1)
        assert result["judge_calls_made"] == 3


def test_it_needs_a_judge_object(rules_data):
    with pytest.raises(ValueError, match="judge"):
        redline_garak.grade_report(FIXTURE, rules_data, None)


def test_an_unknown_user_type_is_refused_before_any_call(rules_data):
    judge = FakeJudge(PASS)
    with pytest.raises(ValueError, match="user_type 'guest'"):
        redline_garak.grade_report(FIXTURE, rules_data, judge, user_type="guest")
    assert judge.calls == []


def test_on_progress_is_called_after_each_answer(rules_data):
    seen = []
    redline_garak.grade_report(
        FIXTURE, rules_data, FakeJudge(PASS), on_progress=lambda *args: seen.append(args)
    )
    assert [(done, total) for done, total, _ in seen] == [(n, TOTAL) for n in range(1, 6)]
    rows = [row for _, _, row in seen]
    assert rows[2] is None
    assert rows[3]["reason_code"] == "EMPTY_ANSWER"
    assert [r["output"] for r in rows if r] == [0, 1, 1, 0]


def test_an_exception_in_on_progress_stops_the_run_and_propagates(rules_data, tmp_path):
    def progress(done, total, row):
        if done == 2:
            raise RuntimeError("the page went away")

    judge = FakeJudge(PASS)
    with pytest.raises(RuntimeError, match="the page went away"):
        redline_garak.grade_report(
            FIXTURE, rules_data, judge, out_dir=tmp_path / "out", on_progress=progress
        )
    assert len(judge.calls) == 4
    rows, summary, _ = _read(tmp_path / "out")
    assert len(rows) == 2
    assert summary["stopped"] == "error"
    assert summary["not_graded"] == {"count": 3, "reasons": {"error": 3}}


def test_should_stop_ends_the_run_cleanly(rules_data, tmp_path):
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(
        FIXTURE,
        rules_data,
        judge,
        out_dir=tmp_path / "out",
        should_stop=lambda: len(judge.calls) >= 4,
    )
    assert result["stopped"] == "cancelled"
    assert result["not_graded"] == {"count": 3, "reasons": {"cancelled": 3}}
    assert result["judge_calls_made"] == 4
    rows, summary, evals = _read(tmp_path / "out")
    assert len(rows) == 2
    assert summary["overall"]["outputs"] == TOTAL
    assert summary["overall"]["not_graded"] == 3
    # A probe the run never reached is still listed, and its outputs have no score.
    assert summary["probes"]["test.Blank"]["not_graded"] == 1
    assert evals[1] == {**evals[1], "probe": "test.Blank", "nones": 1, "total_processed": 1}
    assert evals[0]["nones"] == 2 and evals[0]["total_processed"] == 4


@pytest.mark.parametrize(
    ("cap", "calls", "rows", "left"),
    [
        (0, 0, 0, 5),
        (1, 0, 0, 5),
        (3, 2, 1, 4),
        # The outputs with no text and no words need no call, so they are settled.
        (5, 4, 3, 1),
        (6, 6, 4, 0),
        (100, 6, 4, 0),
    ],
)
def test_the_cap_is_never_passed_and_no_answer_is_half_graded(
    rules_data, tmp_path, cap, calls, rows, left
):
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(
        FIXTURE, rules_data, judge, out_dir=tmp_path / "out", max_judge_calls=cap
    )
    assert len(judge.calls) == result["judge_calls_made"] == calls
    assert calls <= cap
    assert len(result["rows"]) == rows
    assert all(len(r.get("rules_checked", [1, 2])) == 2 for r in result["rows"])
    assert result["stopped"] == ("cap" if left else None)
    assert result["not_graded"] == {"count": left, "reasons": {"cap": left} if left else {}}
    assert result["overall"]["judge_unavailable"] == 0
    assert result["estimated_calls"] == 6
    _read(tmp_path / "out")


def test_the_cap_counts_what_top_k_needs(rules_data):
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(FIXTURE, rules_data, judge, top_k=1, max_judge_calls=2)
    assert len(judge.calls) == 2
    assert result["not_graded"]["count"] == 1
    assert result["estimated_calls"] == 3


def test_a_bad_cap_or_deadline_is_refused(rules_data):
    with pytest.raises(ValueError, match="max_judge_calls"):
        redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), max_judge_calls=-1)
    with pytest.raises(ValueError, match="deadline_seconds"):
        redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), deadline_seconds=-1)


def test_the_deadline_is_checked_before_each_answer(rules_data, tmp_path, monkeypatch):
    # The clock is read once at the start and once before each answer. Each read adds 10.
    ticks = iter(range(0, 1000, 10))
    monkeypatch.setattr(report, "_clock", lambda: next(ticks))
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(
        FIXTURE, rules_data, judge, out_dir=tmp_path / "out", deadline_seconds=25
    )
    assert result["stopped"] == "deadline"
    assert len(result["rows"]) == 2
    assert result["not_graded"] == {"count": 3, "reasons": {"deadline": 3}}
    _read(tmp_path / "out")


def test_a_judge_that_is_down_leaves_three_whole_files(rules_data, tmp_path, capsys):
    def down(payload):
        raise httpx.ConnectError("connection refused")

    judge = ChatEndpointJudge(None, "m", None, transport=down)
    result = redline_garak.grade_report(FIXTURE, rules_data, judge, out_dir=tmp_path / "out")
    assert result["stopped"] == "judge_unavailable"
    assert result["overall"]["judge_unavailable"] == 3
    assert result["not_graded"] == {"count": 0, "reasons": {}}
    assert result["judge_calls_made"] == 3
    rows, _, _ = _read(tmp_path / "out")
    assert [r["outcome"] for r in rows] == ["not_evaluated", "not_evaluated", "pass", "not_evaluated"]
    assert "could not be reached" in capsys.readouterr().err


def test_an_exception_in_the_judge_leaves_three_whole_files_and_propagates(rules_data, tmp_path):
    judge = Raises(at=4)
    with pytest.raises(RuntimeError, match="the judge broke"):
        redline_garak.grade_report(FIXTURE, rules_data, judge, out_dir=tmp_path / "out")
    rows, summary, evals = _read(tmp_path / "out")
    # The second answer was half way through its rules, so it has no row.
    assert len(rows) == 1
    assert summary["stopped"] == "error"
    assert summary["not_graded"] == {"count": 4, "reasons": {"error": 4}}
    assert summary["judge_calls_made"] == 4
    assert sum(e["nones"] for e in evals) == 4


def test_files_are_written_through_a_temporary_name(rules_data, tmp_path, monkeypatch):
    out = tmp_path / "out"
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    before = {name: (out / name).read_bytes() for name in FILES}

    def crash(src, dst):
        raise OSError("the disk went away")

    monkeypatch.setattr(report.os, "replace", crash)
    with pytest.raises(OSError, match="the disk went away"):
        redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(FAIL), out_dir=out)
    monkeypatch.undo()
    # The files of the first run are whole, and no temporary file is left behind.
    assert {name: (out / name).read_bytes() for name in FILES} == before
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)


def test_the_files_get_the_access_rights_of_any_new_file(rules_data, tmp_path):
    out = tmp_path / "out"
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    plain = out / "plain.txt"
    plain.write_text("x")
    assert {(out / name).stat().st_mode for name in FILES} == {plain.stat().st_mode}


def test_grade_report_works_with_garak_not_importable(rules_data, tmp_path):
    code = (
        "import json, sys\n"
        "sys.modules['garak'] = None\n"
        "from redline.garak import estimate_report_calls, grade_report\n"
        "from redline.judge import FakeJudge\n"
        "report, rules = sys.argv[1], json.loads(sys.argv[2])\n"
        "answer = {'verdict': 'pass', 'severity': 0, 'reason_code': 'OK', 'reason_text': 'ok'}\n"
        "result = grade_report(report, rules, FakeJudge(answer), out_dir=sys.argv[3])\n"
        "assert estimate_report_calls(report, rules)['judge_calls'] == result['judge_calls_made']\n"
        "assert not any(name.split('.')[0] == 'garak' and sys.modules[name] for name in sys.modules)\n"
        "print(result['overall']['graded'])\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(FIXTURE), json.dumps(rules_data), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parent.parent)},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "4"
    _read(tmp_path / "out")


# Cost before a run.


@pytest.mark.parametrize(("top_k", "per_answer"), [(None, 2), (1, 1), (2, 2), (5, 2)])
def test_estimate_calls_matches_a_real_run(rules_data, top_k, per_answer):
    judge = FakeJudge(PASS)
    grader = Grader.from_rules(rules_data, judge, top_k=top_k)
    for n in range(3):
        grader.grade("q", f"an answer about a card number, take {n}", "anonymous")
    assert redline.estimate_calls(3, rules_data, top_k=top_k) == len(judge.calls) == 3 * per_answer


def test_estimate_calls_counts_repeat_attempts(rules_data):
    calls = []

    def transport(payload):
        calls.append(payload)
        nonce = payload["messages"][0]["content"].rsplit('value "', 1)[1].split('"')[0]
        return json.dumps({**PASS, "nonce": nonce})

    judge = ChatEndpointJudge(None, "m", None, transport=transport)
    grader = Grader.from_rules(rules_data, judge, attempts=3)
    grader.grade("q", "an answer", "anonymous")
    assert redline.estimate_calls(1, rules_data, attempts=3) == len(calls) == 6


def test_estimate_calls_takes_a_path_and_refuses_bad_numbers(rules_data, write_rules):
    assert redline.estimate_calls(10, write_rules(rules_data)) == 20
    assert redline.estimate_calls(0, rules_data) == 0
    for bad in ({"n_answers": -1}, {"top_k": 0}, {"attempts": 0}):
        with pytest.raises(ValueError):
            redline.estimate_calls(**{"n_answers": 1, "rules": rules_data, **bad})


@pytest.mark.parametrize(("top_k", "calls"), [(None, 6), (1, 3), (2, 6)])
def test_estimate_report_calls_matches_a_real_run(rules_data, top_k, calls):
    estimate = redline_garak.estimate_report_calls(FIXTURE, rules_data, top_k=top_k)
    assert estimate == {
        "answers": 3,
        "skipped": 2,
        "skipped_by_reason": {"no_text": 1, "blank": 1, "wrong_language": 0},
        "judge_calls": calls,
    }
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(FIXTURE, rules_data, judge, top_k=top_k)
    assert len(judge.calls) == result["estimated_calls"] == calls


def test_estimate_report_calls_counts_outputs_in_another_language(rules_data, tmp_path):
    entries = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    uuid = next(e["uuid"] for e in entries if e["entry_type"] == "attempt")
    for entry in entries:
        if entry.get("uuid") == uuid:
            entry["prompt"]["turns"][-1]["content"]["lang"] = "fr"
    path = tmp_path / "report.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    estimate = redline_garak.estimate_report_calls(path, rules_data)
    assert estimate["answers"] == 1
    assert estimate["skipped_by_reason"] == {"no_text": 1, "blank": 1, "wrong_language": 2}
    assert estimate["judge_calls"] == 2


# The command.


def _args(tmp_path, rules, *more):
    return ["garak-grade", str(FIXTURE), "--rules", str(rules), "--out", str(tmp_path / "out"), *more]


def test_the_command_caps_judge_calls_and_exits_1(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    judge = FakeJudge(PASS)
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: judge)
    args = _args(tmp_path, write_rules(rules_data), "--max-judge-calls", "3")
    assert cli.main(args) == 1
    assert len(judge.calls) == 2
    _, summary, _ = _read(tmp_path / "out")
    assert summary["stopped"] == "cap"
    lines = [line for line in capsys.readouterr().err.splitlines() if "not graded" in line]
    assert len(lines) == 1
    assert "4 outputs were not graded" in lines[0] and "--max-judge-calls" in lines[0]


def test_the_command_writes_the_files_when_a_saved_grade_is_missing(
    rules_data, write_rules, tmp_path
):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    args = _args(tmp_path, write_rules(rules_data), "--judge", "replay", "--cache", str(cache))
    with pytest.raises(SystemExit, match="--missing-grade review"):
        cli.main(args)
    rows, summary, _ = _read(tmp_path / "out")
    assert rows == []
    assert summary["stopped"] == "error"
    assert summary["judge"] == "replay"


def test_dry_run_prints_the_estimate_and_needs_no_judge(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    for name in ("REDLINE_JUDGE_BASE_URL", "REDLINE_JUDGE_MODEL", "REDLINE_JUDGE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_live_judge", lambda *a, **k: pytest.fail("built a judge"))
    rules = write_rules(rules_data)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert cli.main(["garak-grade", str(FIXTURE), "--rules", str(rules), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 answers to grade" in out
    assert "at most 6 judge calls" in out
    assert "2 skipped" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert cli.main(["garak-grade", str(FIXTURE), "--rules", str(rules), "--dry-run", "--top-k", "1"]) == 0
    assert "at most 3 judge calls" in capsys.readouterr().out


def test_dry_run_with_a_train_file_counts_one_rule_per_answer(
    rules_data, write_rules, tmp_path, capsys
):
    rules = write_rules(rules_data)
    args = ["garak-grade", str(FIXTURE), "--rules", str(rules), "--dry-run", "--train", "t.jsonl"]
    assert cli.main(args) == 0
    assert "at most 3 judge calls" in capsys.readouterr().out


def test_without_dry_run_the_command_needs_out(rules_data, write_rules, capsys):
    args = ["garak-grade", str(FIXTURE), "--rules", str(write_rules(rules_data))]
    assert cli.main(args) == 2
    assert "--out" in capsys.readouterr().err


def test_dry_run_reports_a_bad_rules_file_plainly(tmp_path):
    with pytest.raises(SystemExit, match="redline: .*expected a list of rules"):
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: 1\n")
        cli.main(["garak-grade", str(FIXTURE), "--rules", str(bad), "--dry-run"])
