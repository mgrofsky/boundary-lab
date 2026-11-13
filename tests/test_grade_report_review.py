"""grade_report after review: the output folder, the counts, the stop rule and the files."""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from redline import garak as redline_garak
from redline.garak import report
from redline.judge import ChatEndpointJudge, FakeJudge

FIXTURE = Path(__file__).parent / "fixtures" / "garak_report.jsonl"
PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
FILES = ("garak_graded.jsonl", "garak_summary.json", "garak_evals.jsonl")
# The fixture's five outputs in order: words, words, no text, blank, words.
TOTAL = 5
THIRD = {
    "rule_id": "TONE-01",
    "name": "tone",
    "rule": "Never insult the user.",
    "plain_summary": "No insults.",
    "allowed_for": ["anonymous", "verified"],
    "reason_codes": ["INSULT", "OK"],
}


def _files(out):
    graded, summary, evals = (Path(out) / name for name in FILES)
    rows = [json.loads(line) for line in graded.read_text().splitlines()]
    entries = [json.loads(line) for line in evals.read_text().splitlines()]
    return rows, json.loads(summary.read_text()), entries


def _train(folder, rules):
    """A labeled examples file with one good and one bad row for each rule."""
    rows = []
    for rule in rules:
        for label in ("good", "bad"):
            rows.append(
                {
                    "id": f"{rule['rule_id']}-{label}",
                    "rule_id": rule["rule_id"],
                    "user_type": "anonymous",
                    "label": label,
                    "prompt": "q",
                    "response": f"{rule['rule']} {label} card number account insult",
                    "reason_code": "OK" if label == "good" else rule["reason_codes"][0],
                }
            )
    path = Path(folder) / "train.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _other_language(folder):
    """The fixture with its first attempt sent in French and not translated back."""
    entries = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    uuid = next(e["uuid"] for e in entries if e["entry_type"] == "attempt")
    for entry in entries:
        if entry.get("uuid") == uuid:
            entry["prompt"]["turns"][-1]["content"]["lang"] = "fr"
    path = Path(folder) / "report.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return path


class Raises:
    """A judge that answers pass until the call numbered at, which raises error."""

    def __init__(self, at, error):
        self.at = at
        self.error = error
        self.calls = 0

    def grade(self, *args):
        self.calls += 1
        if self.calls == self.at:
            raise self.error
        return dict(PASS)


# The output folder is proved before the first judge call.


def test_a_file_in_place_of_out_dir_is_refused_before_any_call(rules_data, tmp_path):
    out = tmp_path / "out"
    out.write_text("in the way")
    judge = FakeJudge(PASS)
    with pytest.raises(report.OutputError, match="out") as caught:
        redline_garak.grade_report(FIXTURE, rules_data, judge, out_dir=out)
    assert isinstance(caught.value, OSError)
    assert judge.calls == []
    assert out.read_text() == "in the way"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_out_dir_that_cannot_be_written_is_refused_before_any_call(rules_data, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o500)
    judge = FakeJudge(PASS)
    try:
        with pytest.raises(report.OutputError, match="cannot be written"):
            redline_garak.grade_report(FIXTURE, rules_data, judge, out_dir=out)
        with pytest.raises(report.OutputError):
            redline_garak.grade_report(FIXTURE, rules_data, judge, out_dir=out / "deeper" / "still")
    finally:
        out.chmod(0o700)
    assert judge.calls == []
    assert list(out.iterdir()) == []


def test_the_check_of_out_dir_leaves_nothing_behind(rules_data, tmp_path):
    out = tmp_path / "a" / "b"
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)


def test_bad_rules_are_refused_before_the_folder_is_made(rules_data, tmp_path):
    with pytest.raises(ValueError):
        redline_garak.grade_report(FIXTURE, [], FakeJudge(PASS), out_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_write_that_fails_after_a_judge_error_keeps_both(rules_data, tmp_path, monkeypatch):
    def crash(src, dst):
        raise OSError("the disk went away")

    monkeypatch.setattr(report.os, "replace", crash)
    with pytest.raises(OSError, match="the disk went away") as caught:
        redline_garak.grade_report(
            FIXTURE, rules_data, Raises(2, RuntimeError("the judge broke")), out_dir=tmp_path / "o"
        )
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "the judge broke" in str(caught.value.__cause__)
    assert list((tmp_path / "o").iterdir()) == []


# An interrupt writes what is graded so far, like any other exception.


@pytest.mark.parametrize("error", [KeyboardInterrupt(), asyncio.CancelledError(), SystemExit(3)])
def test_an_interrupt_inside_the_judge_still_writes_the_files(rules_data, tmp_path, error):
    with pytest.raises(type(error)) as caught:
        redline_garak.grade_report(FIXTURE, rules_data, Raises(4, error), out_dir=tmp_path / "out")
    assert caught.value is error
    rows, summary, _ = _files(tmp_path / "out")
    assert len(rows) == 1
    assert summary["stopped"] == "error"
    assert summary["not_graded"] == {"count": 4, "reasons": {"error": 4}}


def test_an_interrupt_inside_on_progress_still_writes_the_files(rules_data, tmp_path):
    def progress(done, total, row):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        redline_garak.grade_report(
            FIXTURE, rules_data, FakeJudge(PASS), out_dir=tmp_path / "out", on_progress=progress
        )
    assert _files(tmp_path / "out")[1]["stopped"] == "error"


# The three files are made first and renamed together, the summary last.


def test_all_three_files_are_made_before_any_is_renamed_and_the_summary_goes_last(
    rules_data, tmp_path, monkeypatch
):
    out, seen, real = tmp_path / "out", [], os.replace

    def watch(src, dst):
        seen.append((Path(dst).name, sorted(p.name for p in out.iterdir() if p.suffix == ".tmp")))
        real(src, dst)

    monkeypatch.setattr(report.os, "replace", watch)
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    assert [name for name, _ in seen] == ["garak_graded.jsonl", "garak_evals.jsonl", "garak_summary.json"]
    assert len(seen[0][1]) == 3
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)


def test_a_failure_part_way_through_the_renames_leaves_the_old_summary(
    rules_data, tmp_path, monkeypatch
):
    out, real, count = tmp_path / "out", os.replace, []
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    before = (out / "garak_summary.json").read_bytes()

    def second_fails(src, dst):
        count.append(dst)
        if len(count) == 2:
            raise OSError("the disk went away")
        real(src, dst)

    monkeypatch.setattr(report.os, "replace", second_fails)
    with pytest.raises(OSError, match="the disk went away"):
        redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(FAIL), out_dir=out)
    assert (out / "garak_summary.json").read_bytes() == before
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)


def test_a_failure_while_the_files_are_made_changes_nothing(rules_data, tmp_path, monkeypatch):
    out = tmp_path / "out"
    redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), out_dir=out)
    before = {name: (out / name).read_bytes() for name in FILES}
    real, made = Path.open, []

    def third_fails(self, *args, **kwargs):
        if self.suffix == ".tmp" and args[:1] == ("x",):
            made.append(self)
            if len(made) == 3:
                raise OSError("the disk is full")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", third_fails)
    with pytest.raises(OSError, match="the disk is full"):
        redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(FAIL), out_dir=out)
    monkeypatch.undo()
    assert {name: (out / name).read_bytes() for name in FILES} == before
    assert sorted(p.name for p in out.iterdir()) == sorted(FILES)


# Counts add up, in every way a run can end.


def _down(payload):
    raise httpx.ConnectError("connection refused")


def _ticks():
    ticks = iter(range(0, 1000, 10))
    return lambda: next(ticks)


STOPS = {
    "finished": lambda: (FakeJudge(PASS), {}),
    "fails": lambda: (FakeJudge([PASS, FAIL, FAIL, PASS, PASS, PASS]), {}),
    "cancelled": lambda: (FakeJudge(PASS), {"should_stop": iter([False, False, True]).__next__}),
    "cancelled_at_once": lambda: (FakeJudge(PASS), {"should_stop": lambda: True}),
    "cap_0": lambda: (FakeJudge(PASS), {"max_judge_calls": 0}),
    "cap_3": lambda: (FakeJudge(PASS), {"max_judge_calls": 3}),
    "cap_5": lambda: (FakeJudge(PASS), {"max_judge_calls": 5}),
    "deadline": lambda: (FakeJudge(PASS), {"deadline_seconds": 25}),
    "judge_down": lambda: (ChatEndpointJudge(None, "m", None, transport=_down), {}),
    "error": lambda: (Raises(4, RuntimeError("the judge broke")), {}),
    "interrupt": lambda: (Raises(1, KeyboardInterrupt()), {}),
}


def _adds_up(block):
    parts = ("graded", "judge_unavailable", "skipped_no_text", "not_graded")
    assert block["outputs"] == sum(block[name] for name in parts), block
    outcomes = ("rule_breaks", "sent_to_review", "passed")
    assert block["graded"] == sum(block[name]["k"] for name in outcomes), block
    assert all(block[name]["n"] == block["graded"] for name in outcomes)
    assert block["empty"] <= block["passed"]["k"]


@pytest.mark.parametrize("language", ["english", "other"])
@pytest.mark.parametrize("name", sorted(STOPS))
def test_the_counts_add_up_however_the_run_ends(
    rules_data, tmp_path, monkeypatch, capsys, name, language
):
    path = FIXTURE if language == "english" else _other_language(tmp_path)
    judge, options = STOPS[name]()
    if name == "deadline":
        monkeypatch.setattr(report, "_clock", _ticks())
    out = tmp_path / "out"
    try:
        redline_garak.grade_report(path, rules_data, judge, out_dir=out, **options)
    except (RuntimeError, KeyboardInterrupt):
        assert name in ("error", "interrupt")
    rows, summary, evals = _files(out)
    overall, probes = summary["overall"], summary["probes"]
    for block in (overall, *probes.values()):
        _adds_up(block)
    for key in ("outputs", "graded", "judge_unavailable", "skipped_no_text", "not_graded", "empty"):
        assert overall[key] == sum(block[key] for block in probes.values()), key
    # The estimate and the run count the same outputs.
    estimate = redline_garak.estimate_report_calls(path, rules_data)
    assert overall["outputs"] == TOTAL == estimate["answers"] + estimate["skipped"]
    assert len(rows) == overall["graded"] + overall["judge_unavailable"]
    # An eval entry counts every output of its probe that garak's detector would be given.
    unread = estimate["skipped_by_reason"]["wrong_language"]
    assert sum(e["total_processed"] for e in evals) == overall["outputs"] - unread
    assert all(e["total_processed"] == e["total_evaluated"] + e["nones"] for e in evals)
    left = summary["not_graded"]
    assert left["count"] == overall["not_graded"] == sum(left["reasons"].values())
    assert (left["count"] > 0) == (summary["stopped"] not in (None, "judge_unavailable"))
    if left["count"]:
        assert list(left["reasons"]) == [summary["stopped"]]
    if summary["stopped"] is None:
        by_reason = estimate["skipped_by_reason"]
        assert overall["skipped_no_text"] == by_reason["no_text"] + by_reason["wrong_language"]
        assert overall["empty"] == by_reason["blank"]
        sent = overall["graded"] - overall["empty"] + overall["judge_unavailable"]
        assert sent == estimate["answers"]
    capsys.readouterr()


def test_outputs_in_another_language_are_counted_and_are_not_nones(rules_data, tmp_path):
    seen = []
    path = _other_language(tmp_path)
    result = redline_garak.grade_report(
        path, rules_data, FakeJudge(PASS), on_progress=lambda *args: seen.append(args)
    )
    test = result["probes"]["test.Test"]
    # Two outputs could not be read and one had no text. Both kinds are under one count.
    assert (test["outputs"], test["skipped_no_text"], test["graded"]) == (4, 3, 1)
    assert result["attempts_without_back_translation"] == 1
    assert [(done, total) for done, total, _ in seen] == [(n, TOTAL) for n in range(1, 6)]
    assert [row for _, _, row in seen][:2] == [None, None]
    # garak's own detector scores nothing for such an attempt, so the eval entry leaves it out.
    assert (result["evals"][0]["nones"], result["evals"][0]["total_processed"]) == (1, 2)


def test_the_calls_made_have_their_own_name_at_the_top_level(rules_data):
    result = redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS))
    assert result["judge_calls_made"] == 6
    assert "judge_calls" not in result
    assert result["overall"]["judge_calls"] == 6


# One stop rule: every output after the stop is not reached.


@pytest.mark.parametrize("kind", ["cap", "cancelled", "deadline"])
def test_after_a_stop_no_output_is_settled_even_one_that_needs_no_call(
    rules_data, monkeypatch, kind
):
    judge = FakeJudge(PASS)
    options = {
        "cap": {"max_judge_calls": 3},
        "cancelled": {"should_stop": lambda: len(judge.calls) >= 2},
        "deadline": {"deadline_seconds": 15},
    }[kind]
    monkeypatch.setattr(report, "_clock", _ticks())
    result = redline_garak.grade_report(FIXTURE, rules_data, judge, **options)
    # The stop came before the second output. The no text and blank ones after it wait too.
    assert result["stopped"] == kind
    assert len(result["rows"]) == 1
    assert result["not_graded"] == {"count": 4, "reasons": {kind: 4}}
    assert (result["overall"]["skipped_no_text"], result["overall"]["empty"]) == (0, 0)


def test_before_a_stop_an_output_that_needs_no_call_is_settled_whatever_the_cap(rules_data):
    # With a cap of 5 the first two answers use 4 calls. The no text and blank outputs
    # need none, so they are settled. The last answer needs 2, so the run stops there.
    result = redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), max_judge_calls=5)
    overall = result["overall"]
    assert (overall["skipped_no_text"], overall["empty"], overall["not_graded"]) == (1, 1, 1)
    assert result["stopped"] == "cap"


def test_the_cap_test_uses_the_most_calls_an_answer_could_need(rules_data, tmp_path):
    rules = [*rules_data, THIRD]
    train = _train(tmp_path, rules)
    judge = FakeJudge(PASS)
    options = {"train": train, "embedder": "tfidf", "top_k": 3}
    result = redline_garak.grade_report(FIXTURE, rules, judge, max_judge_calls=2, **options)
    assert judge.calls == [] and result["judge_calls_made"] == 0
    assert result["stopped"] == "cap"
    assert result["not_graded"]["count"] == TOTAL
    result = redline_garak.grade_report(FIXTURE, rules, FakeJudge(PASS), max_judge_calls=3, **options)
    # With room for one answer's worst case, the run starts and stays under the cap.
    assert len(result["rows"]) >= 1 and result["judge_calls_made"] <= 3


# The estimate matches the run, with labeled examples too.


@pytest.mark.parametrize(("top_k", "per_answer"), [(None, 1), (1, 1), (2, 2), (3, 3), (9, 3)])
def test_the_estimate_with_a_train_file_matches_the_run(rules_data, tmp_path, top_k, per_answer):
    rules = [*rules_data, THIRD]
    train = _train(tmp_path, rules)
    estimate = redline_garak.estimate_report_calls(FIXTURE, rules, top_k=top_k, train=train)
    assert estimate["judge_calls"] == 3 * per_answer
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(
        FIXTURE, rules, judge, top_k=top_k, train=train, embedder="tfidf"
    )
    assert result["estimated_calls"] == estimate["judge_calls"]
    assert len(judge.calls) == result["judge_calls_made"] <= result["estimated_calls"]


@pytest.mark.parametrize(("top_k", "calls"), [(None, 9), (1, 3), (2, 6), (7, 9)])
def test_estimated_calls_is_answers_times_rules_checked(rules_data, top_k, calls):
    rules = [*rules_data, THIRD]
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(FIXTURE, rules, judge, top_k=top_k)
    # Three outputs have words. The blank one and the one with no text cost nothing.
    assert result["estimated_calls"] == calls == len(judge.calls)
    capped = redline_garak.grade_report(FIXTURE, rules, FakeJudge(PASS), top_k=top_k, max_judge_calls=0)
    assert capped["estimated_calls"] == calls and capped["judge_calls_made"] == 0


def test_the_estimate_checks_the_train_file(rules_data, tmp_path):
    with pytest.raises(OSError):
        redline_garak.estimate_report_calls(FIXTURE, rules_data, train=tmp_path / "missing.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a"}\n')
    with pytest.raises(ValueError, match=r"bad\.jsonl: line 1 has no rule_id"):
        redline_garak.estimate_report_calls(FIXTURE, rules_data, train=bad)
    with pytest.raises(ValueError, match="top_k"):
        redline_garak.estimate_report_calls(FIXTURE, rules_data, top_k=0)


# The deadline, the stop question and the call count, each on its own.


def test_a_clock_that_reads_the_deadline_exactly_stops_the_run(rules_data, monkeypatch):
    reads = iter([100, 110, 999])
    monkeypatch.setattr(report, "_clock", lambda: next(reads))
    judge = FakeJudge(PASS)
    result = redline_garak.grade_report(FIXTURE, rules_data, judge, deadline_seconds=10)
    assert result["stopped"] == "deadline"
    assert judge.calls == [] and result["not_graded"]["count"] == TOTAL


def test_a_clock_one_tick_short_of_the_deadline_does_not_stop_the_run(rules_data, monkeypatch):
    reads = iter([100, 109, 109, 109, 109, 109])
    monkeypatch.setattr(report, "_clock", lambda: next(reads))
    result = redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), deadline_seconds=10)
    assert result["stopped"] is None


def test_should_stop_is_asked_before_every_output(rules_data):
    events = []

    def should_stop():
        events.append("ask")
        return False

    redline_garak.grade_report(
        FIXTURE,
        rules_data,
        FakeJudge(PASS),
        should_stop=should_stop,
        on_progress=lambda done, total, row: events.append("settled"),
    )
    assert events == ["ask", "settled"] * TOTAL


def test_should_stop_can_end_the_run_before_any_output_even_one_with_no_text(rules_data):
    for stop_at in range(1, TOTAL + 1):
        asked = []

        def should_stop(stop_at=stop_at, asked=asked):
            asked.append(1)
            return len(asked) == stop_at

        result = redline_garak.grade_report(FIXTURE, rules_data, FakeJudge(PASS), should_stop=should_stop)
        assert result["stopped"] == "cancelled"
        assert result["not_graded"]["count"] == TOTAL - stop_at + 1


def test_a_saved_grade_counts_as_a_call_made_and_toward_the_cap(rules_data, tmp_path):
    cache, sent = tmp_path / "cache.jsonl", []

    def transport(payload):
        sent.append(payload)
        nonce = payload["messages"][0]["content"].rsplit('value "', 1)[1].split('"')[0]
        return json.dumps({**PASS, "nonce": nonce})

    judge = ChatEndpointJudge(None, "m", None, transport=transport, cache_path=cache)
    first = redline_garak.grade_report(FIXTURE, rules_data, judge)
    assert first["judge_calls_made"] == len(sent) == 6
    second = redline_garak.grade_report(FIXTURE, rules_data, judge, max_judge_calls=4)
    assert len(sent) == 6
    assert second["judge_calls_made"] == 4 and second["stopped"] == "cap"


def test_a_call_the_watch_does_not_make_is_not_counted(rules_data, capsys):
    rules = [*rules_data, THIRD]
    judge = ChatEndpointJudge(None, "m", None, transport=_down)
    result = redline_garak.grade_report(FIXTURE, rules, judge)
    # Nine calls were due. After three that failed in a row the judge is left alone.
    assert result["judge_calls_made"] == report.STOP_AFTER
    assert result["estimated_calls"] == 9
    capsys.readouterr()
