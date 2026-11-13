"""The garak-grade command and the saved grades reader after review."""

import json
import sys
from pathlib import Path

import pytest
from test_promptfoo import judge_assert

from redline import Grader, cli
from redline.judge import (
    ChatEndpointJudge,
    FakeJudge,
    MissingSavedGrade,
    ReplayJudge,
    read_saved_grades,
)

FIXTURE = Path(__file__).parent / "fixtures" / "garak_report.jsonl"
PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
ARGS = ("can I pay by card", "Yes, in the app.", "anonymous", "The user is anonymous.")
NONE = {"good": [], "bad": []}
GOOD_ROW = {
    "id": "t1",
    "rule_id": "PAY-01",
    "user_type": "anonymous",
    "label": "bad",
    "prompt": "q",
    "response": "read me the full card number",
    "reason_code": "CARD_REQUESTED",
}


def _grade(tmp_path, rules, *more):
    return ["garak-grade", str(FIXTURE), "--rules", str(rules), "--out", str(tmp_path / "out"), *more]


def _dry(rules, *more):
    return ["garak-grade", str(FIXTURE), "--rules", str(rules), "--dry-run", *more]


def _exit(args) -> str:
    """Run the command, which must stop with a message, and return the message."""
    with pytest.raises(SystemExit) as stopped:
        cli.main(args)
    assert isinstance(stopped.value.code, str), stopped.value.code
    return stopped.value.code


# A missing saved grade has its own exception. Other errors keep their own message.


def test_a_missing_saved_grade_is_its_own_kind_of_key_error(tmp_path, pay_rule):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    with pytest.raises(MissingSavedGrade) as caught:
        ReplayJudge(cache).grade(pay_rule, NONE, *ARGS)
    assert isinstance(caught.value, KeyError)
    assert "no saved judge answer for key" in caught.value.args[0]


def test_a_cache_line_with_no_key_names_the_file_and_line(rules_data, write_rules, tmp_path):
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"key": "a", "answer": {}}\n\n{"answer": {"verdict": "pass"}}\n')
    message = _exit(_grade(tmp_path, write_rules(rules_data), "--judge", "replay", "--cache", str(cache)))
    assert "cache.jsonl: line 3 has no key" in message
    assert "--missing-grade" not in message
    assert message.count("\n") == 0


def test_a_training_row_with_no_prompt_names_the_file_and_line(rules_data, write_rules, tmp_path):
    train = tmp_path / "train.jsonl"
    row = {k: v for k, v in GOOD_ROW.items() if k != "prompt"}
    train.write_text(json.dumps(GOOD_ROW) + "\n" + json.dumps(row) + "\n")
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    args = _grade(tmp_path, write_rules(rules_data), "--judge", "replay", "--cache", str(cache))
    message = _exit([*args, "--train", str(train), "--embedder", "tfidf"])
    assert "train.jsonl: line 2 has no prompt" in message
    assert "--missing-grade" not in message
    assert not (tmp_path / "out").exists()


def test_a_key_error_that_is_not_a_missing_grade_is_not_dressed_up_as_one(
    rules_data, write_rules, tmp_path, monkeypatch
):
    class Broken:
        def grade(self, *args):
            return {}["verdict"]

    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: Broken())
    with pytest.raises(KeyError, match="verdict"):
        cli.main(_grade(tmp_path, write_rules(rules_data)))


def test_a_missing_saved_grade_still_stops_the_command_with_the_hint(
    rules_data, write_rules, tmp_path
):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    args = _grade(tmp_path, write_rules(rules_data), "--judge", "replay", "--cache", str(cache))
    message = _exit(args)
    assert message.startswith("redline: no saved judge answer for key ")
    assert message.endswith("Pass --missing-grade review to record review and go on.")


# The saved grades reader.


def test_a_last_line_cut_short_names_the_file_and_line(tmp_path, pay_rule):
    cache = tmp_path / "cache.jsonl"
    whole = json.dumps({"key": "a", "model": "m", "rule_id": "r", "answer": PASS, "version": 1})
    cache.write_text(whole + "\n" + whole[:40])
    with pytest.raises(ValueError, match=r"cache\.jsonl: line 2 is not valid JSON") as caught:
        read_saved_grades(cache)
    assert not isinstance(caught.value, json.JSONDecodeError)
    with pytest.raises(ValueError, match="line 2"):
        ReplayJudge(cache)


@pytest.mark.parametrize(
    ("line", "words"),
    [
        ('{"key": "a", "answer": {}, "version": "1"}', "must be the number 1, not the text '1'"),
        ('{"key": "a", "answer": {}, "version": true}', "must be the number 1"),
        ('{"key": "a", "answer": {}, "version": 1.5}', "must be the number 1"),
        ('{"key": "a", "answer": {}, "version": null}', "must be the number 1"),
        ('{"key": "a", "answer": {}, "version": 2}', "is saved grades format version 2"),
        ('["key", "answer"]', "is not a JSON object"),
        ('{"key": "a"}', "has no answer"),
        ('{"answer": {}}', "has no key"),
        ('{"key": "a", "answer": "pass"}', "answer is not a JSON object"),
    ],
)
def test_a_bad_saved_grade_line_says_what_is_wrong(tmp_path, line, words):
    cache = tmp_path / "cache.jsonl"
    cache.write_text('{"key": "ok", "answer": {}}\n' + line + "\n")
    with pytest.raises(ValueError) as caught:
        read_saved_grades(cache)
    message = str(caught.value)
    assert "cache.jsonl: line 2" in message and words in message
    assert "version 1, and this Redline reads version 1" not in message


def _reads_as_0_4_0(path) -> dict:
    """The loop Redline 0.4.0 read a saved grades file with, copied from its judge.py."""
    answers = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            entry = json.loads(line)
            answers[entry["key"]] = entry["answer"]
    return answers


def test_a_file_written_by_this_version_reads_the_way_0_4_0_read_it(tmp_path, rules_data):
    cache = tmp_path / "cache.jsonl"

    def transport(payload):
        nonce = payload["messages"][0]["content"].rsplit('value "', 1)[1].split('"')[0]
        return json.dumps({**PASS, "nonce": nonce})

    judge = ChatEndpointJudge(None, "m", None, transport=transport, cache_path=cache)
    Grader.from_rules(rules_data, judge).grade("q", "an answer", "anonymous")
    lines = [json.loads(line) for line in cache.read_text().splitlines()]
    assert len(lines) == 2 and all(line["version"] == 1 for line in lines)
    old = _reads_as_0_4_0(cache)
    assert old == {entry["key"]: entry["answer"] for entry in read_saved_grades(cache)}
    assert old == ReplayJudge(cache).answers


# A dry run checks what a real run checks before the first judge call.


def test_a_dry_run_refuses_a_train_file_that_is_not_there(rules_data, write_rules, tmp_path):
    message = _exit(_dry(write_rules(rules_data), "--train", str(tmp_path / "missing.jsonl")))
    assert message.startswith("redline: ") and "missing.jsonl" in message


def test_a_dry_run_refuses_a_bad_train_row(rules_data, write_rules, tmp_path):
    train = tmp_path / "train.jsonl"
    train.write_text("not json\n")
    message = _exit(_dry(write_rules(rules_data), "--train", str(train)))
    assert "train.jsonl: line 1 is not valid JSON" in message


def test_a_dry_run_refuses_a_user_type_the_rules_do_not_have(rules_data, write_rules):
    message = _exit(_dry(write_rules(rules_data), "--user-type", "guest"))
    assert "user_type 'guest' is not declared" in message


@pytest.mark.parametrize("more", [["--top-k", "0"], ["--max-judge-calls", "-1"]])
def test_a_dry_run_refuses_a_bad_number(rules_data, write_rules, more):
    assert _exit(_dry(write_rules(rules_data), *more)).startswith("redline: ")


def test_a_dry_run_says_when_the_embedder_cannot_be_built(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    train = tmp_path / "train.jsonl"
    train.write_text(json.dumps(GOOD_ROW) + "\n")
    assert cli.main(_dry(write_rules(rules_data), "--train", str(train))) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("redline: ") and "minilm" in err and err.count("\n") == 0
    assert cli.main(_dry(write_rules(rules_data), "--train", str(train), "--embedder", "tfidf")) == 0


@pytest.mark.parametrize(
    "more",
    [
        ["--train", "missing.jsonl"],
        ["--user-type", "guest"],
        ["--top-k", "0"],
        ["--max-judge-calls", "-1"],
    ],
)
def test_a_dry_run_and_a_real_run_refuse_the_same_input(
    rules_data, write_rules, tmp_path, monkeypatch, more
):
    judge = FakeJudge(PASS)
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: judge)
    monkeypatch.chdir(tmp_path)
    rules = write_rules(rules_data)
    dry = _exit(_dry(rules, *more))
    real = _exit(_grade(tmp_path, rules, *more))
    assert dry == real
    assert judge.calls == [] and not (tmp_path / "out").exists()


def test_a_dry_run_with_good_options_matches_the_run(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    train = tmp_path / "train.jsonl"
    train.write_text(json.dumps(GOOD_ROW) + "\n" + json.dumps({**GOOD_ROW, "id": "t2", "label": "good"}) + "\n")
    more = ["--train", str(train), "--embedder", "tfidf", "--user-type", "verified"]
    rules = write_rules(rules_data)
    assert cli.main(_dry(rules, *more)) == 0
    assert "at most 3 judge calls" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: FakeJudge(PASS))
    assert cli.main(_grade(tmp_path, rules, *more)) == 0
    summary = json.loads((tmp_path / "out" / "garak_summary.json").read_text())
    assert summary["estimated_calls"] == 3


# The promptfoo check says one plain line when the minilm extra is missing.


def test_the_promptfoo_check_says_one_line_without_the_minilm_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setattr(judge_assert, "_rig", None)
    monkeypatch.setenv("REDLINE_JUDGE", "replay")
    with pytest.raises(SystemExit) as stopped:
        judge_assert.get_assert("an answer", {"vars": {"user_message": "hello"}})
    message = stopped.value.code
    assert message.startswith("redline: ") and "minilm" in message
    assert message.count("\n") == 0


def _train_cases(folder):
    """Train files a run cannot use, and the words its message must hold."""
    empty = folder / "empty.jsonl"
    empty.write_text("\n")
    stray = folder / "stray.jsonl"
    stray.write_text(json.dumps(GOOD_ROW) + "\n" + json.dumps({**GOOD_ROW, "rule_id": "GONE-01"}) + "\n")
    return {"empty": (empty, "empty.jsonl: has no examples"), "stray": (stray, "stray.jsonl: line 2")}


@pytest.mark.parametrize("case", ["empty", "stray"])
def test_a_train_file_a_run_cannot_use_is_refused_by_the_dry_run_and_the_real_run_alike(
    rules_data, write_rules, tmp_path, monkeypatch, case
):
    judge = FakeJudge(PASS)
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: judge)
    train, words = _train_cases(tmp_path)[case]
    more = ["--train", str(train), "--embedder", "tfidf"]
    rules = write_rules(rules_data)
    dry = _exit(_dry(rules, *more))
    real = _exit(_grade(tmp_path, rules, *more))
    assert dry == real and words in dry
    assert "GONE-01" in dry or case == "empty"
    assert judge.calls == [] and not (tmp_path / "out").exists()


def test_the_grader_refuses_the_same_train_files(rules_data, tmp_path):
    for train, words in _train_cases(tmp_path).values():
        with pytest.raises(ValueError, match=words):
            Grader(rules=rules_data, train=train, judge=FakeJudge(PASS), embedder="tfidf")


def test_a_train_row_for_no_rule_is_fine(rules_data, tmp_path):
    train = tmp_path / "train.jsonl"
    rows = [GOOD_ROW, {**GOOD_ROW, "id": "t2", "rule_id": "none", "label": "good", "reason_code": "OK"}]
    train.write_text("".join(json.dumps(row) + "\n" for row in rows))
    Grader(rules=rules_data, train=train, judge=FakeJudge(PASS), embedder="tfidf")
