"""A judge that cannot be reached: the output is not evaluated, never a pass or a hit."""

import json
import logging
from pathlib import Path

import httpx
import pytest

from redline import cli
from redline import judge as judge_module
from redline.garak import report
from redline.judge import ChatEndpointJudge, http_transport

FIXTURE = Path(__file__).parent / "fixtures" / "garak_report.jsonl"
ARGS = ("what is my balance", "It is $40.", "verified", "The user is verified.")
NONE = {"good": [], "bad": []}
ADDRESS = "http://judge.invalid:4321/v1"


def _reply(verdict, payload):
    nonce = payload["messages"][0]["content"].rsplit('value "', 1)[1].split('"')[0]
    code = "OK" if verdict == "pass" else "CARD_ECHOED"
    return json.dumps(
        {"verdict": verdict, "severity": 0.5, "reason_code": code, "reason_text": "x", "nonce": nonce}
    )


class Line:
    """A transport that follows a script: "down" raises as a dead endpoint does."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if step == "down":
            raise httpx.ConnectError(f"connection refused to {ADDRESS}")
        if step == "401":
            request = httpx.Request("POST", ADDRESS)
            raise httpx.HTTPStatusError(
                "401", request=request, response=httpx.Response(401, request=request)
            )
        if step == "junk":
            return "this is not json"
        return _reply(step, payload)


def _grader(rules, script, **kwargs):
    line = Line(script)
    judge = ChatEndpointJudge(None, "m", None, transport=line)
    return report.build_grader(rules, judge, **kwargs), line


@pytest.mark.parametrize("step", ["down", "401"])
def test_the_judge_marks_a_call_it_could_not_make(acct_rule, step):
    answer = ChatEndpointJudge(None, "m", None, transport=Line([step])).grade(acct_rule, NONE, *ARGS)
    # The verdict and the code are what they always were. Only the mark is new.
    assert (answer["verdict"], answer["reason_code"]) == ("review", "JUDGE_ERROR")
    assert answer["unreachable"] is True


def test_a_reply_that_cannot_be_read_is_not_marked(acct_rule):
    answer = ChatEndpointJudge(None, "m", None, transport=Line(["junk"])).grade(acct_rule, NONE, *ARGS)
    assert (answer["verdict"], answer["reason_code"]) == ("review", "JUDGE_ERROR")
    assert "unreachable" not in answer

    def odd(payload):
        raise KeyError("choices")

    answer = ChatEndpointJudge(None, "m", None, transport=odd).grade(acct_rule, NONE, *ARGS)
    assert "unreachable" not in answer


def _refuse(monkeypatch):
    posts, waits = [], []

    def post(*args, **kwargs):
        posts.append(args)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(judge_module, "_sleep", waits.append)
    return posts, waits


def test_the_default_retry_count_is_what_it_was(monkeypatch):
    posts, waits = _refuse(monkeypatch)
    with pytest.raises(httpx.ConnectError):
        http_transport("http://unused", "k")({})
    assert len(posts) == 6 and waits == [2, 4, 8, 16, 32]
    del posts[:], waits[:]
    judge = ChatEndpointJudge("http://unused", "m", "k")
    with pytest.raises(httpx.ConnectError):
        judge.transport({})
    assert len(posts) == 6 and waits == [2, 4, 8, 16, 32]


def test_the_retry_count_is_a_constructor_argument(monkeypatch):
    posts, waits = _refuse(monkeypatch)
    judge = ChatEndpointJudge("http://unused", "m", "k", tries=2, backoff=(1,))
    with pytest.raises(httpx.ConnectError):
        judge.transport({})
    assert len(posts) == 2 and waits == [1]
    with pytest.raises(ValueError, match="tries"):
        ChatEndpointJudge("http://unused", "m", "k", tries=0)


def test_an_output_the_judge_could_not_be_called_for_is_not_evaluated(rules_data, write_rules, capsys):
    grader, _ = _grader(write_rules(rules_data), ["down"], top_k=1)
    graded = report.grade_text(grader, "q", "an answer", "anonymous")
    assert graded["outcome"] == "not_evaluated"
    assert graded["reason_code"] == "JUDGE_UNAVAILABLE"
    assert report.score(graded["outcome"]) is None
    assert report.score(graded["outcome"], 0.9) is None


def test_a_reply_with_no_usable_verdict_is_still_a_review(rules_data, write_rules, capsys):
    grader, _ = _grader(write_rules(rules_data), ["junk"], top_k=1)
    graded = report.grade_text(grader, "q", "an answer", "anonymous")
    assert (graded["outcome"], graded["reason_code"]) == ("review", "JUDGE_ERROR")
    assert report.score(graded["outcome"]) == 0.5
    assert capsys.readouterr().err == ""


def test_a_break_the_judge_did_find_is_kept_when_another_rule_could_not_be_checked(
    rules_data, write_rules
):
    grader, _ = _grader(write_rules(rules_data), ["down", "fail"])
    graded = report.grade_text(grader, "q", "an answer", "anonymous")
    assert graded["outcome"] == "fail"
    grader, _ = _grader(write_rules(rules_data), ["pass", "down"])
    assert report.grade_text(grader, "q", "an answer", "anonymous")["outcome"] == "not_evaluated"
    grader, _ = _grader(write_rules(rules_data), ["junk", "down"])
    assert report.grade_text(grader, "q", "an answer", "anonymous")["outcome"] == "not_evaluated"


def test_after_three_misses_in_a_row_the_judge_is_not_called_again(rules_data, write_rules, capsys):
    grader, line = _grader(write_rules(rules_data), ["down"], top_k=1)
    outcomes = [
        report.grade_text(grader, "q", f"answer {n}", "anonymous")["outcome"] for n in range(6)
    ]
    assert outcomes == ["not_evaluated"] * 6
    assert line.calls == 3
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 2
    assert err[0].startswith("redline: the judge model could not be reached")
    assert "stopped calling" in err[1]
    assert "judge.invalid" not in "".join(err) and "4321" not in "".join(err)


def test_a_success_resets_the_count(rules_data, write_rules, capsys):
    script = ["down", "down", "pass", "down", "down", "pass", "pass"]
    grader, line = _grader(write_rules(rules_data), script, top_k=1)
    outcomes = [
        report.grade_text(grader, "q", f"answer {n}", "anonymous")["outcome"] for n in range(7)
    ]
    assert outcomes == ["not_evaluated"] * 2 + ["pass"] + ["not_evaluated"] * 2 + ["pass"] * 2
    assert line.calls == 7
    # The first miss is said once, and the judge was never dropped.
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_the_outage_is_logged_as_a_warning_without_the_address(rules_data, write_rules, caplog):
    grader, _ = _grader(write_rules(rules_data), ["down"], top_k=1, name="redline.PolicyRules")
    with caplog.at_level(logging.WARNING):
        for n in range(4):
            report.grade_text(grader, "q", f"answer {n}", "anonymous")
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(messages) == 2 and all(m.startswith("redline.PolicyRules: ") for m in messages)
    assert "judge.invalid" not in caplog.text


def test_rows_and_counts_keep_unevaluated_outputs_apart(rules_data, write_rules):
    # The fixture has three outputs with words. top_k 1 makes one call for each.
    grader, _ = _grader(write_rules(rules_data), ["fail", "down", "pass"], top_k=1)
    rows, skipped = report.grade_attempts(report.read_attempts(FIXTURE), grader, "anonymous")
    assert [r["outcome"] for r in rows] == ["fail", "not_evaluated", "pass", "pass"]
    assert rows[1]["reason_code"] == "JUDGE_UNAVAILABLE"
    overall = report.summarize(rows, skipped)["overall"]
    assert (overall["outputs"], overall["graded"], overall["judge_unavailable"]) == (5, 3, 1)
    assert overall["rule_breaks"]["n"] == 3 and overall["passed"]["n"] == 3
    assert overall["sent_to_review"]["k"] == 0
    assert overall["judge_calls"] == 2
    test, _ = report.eval_entries(rows, skipped)
    assert (test["passed"], test["fails"], test["nones"]) == (1, 1, 2)
    assert (test["total_evaluated"], test["total_processed"]) == (2, 4)
    assert report.summarize(rows, skipped)["probes"]["test.Blank"]["judge_unavailable"] == 0


def _live(monkeypatch, script):
    line = Line(script)
    seen = {}

    def live_judge(cache, save=True, **kwargs):
        seen.update(kwargs)
        return ChatEndpointJudge(None, "m", None, transport=line, cache_path=cache)

    monkeypatch.setattr(cli, "_live_judge", live_judge)
    return line, seen


def test_garak_grade_warns_and_exits_1_when_the_judge_is_down(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    line, seen = _live(monkeypatch, ["down"])
    out = tmp_path / "out"
    args = ["garak-grade", str(FIXTURE), "--rules", str(write_rules(rules_data)), "--out", str(out)]
    assert cli.main(args) == 1
    assert line.calls == 3
    # The garak paths try twice with a short wait, not six times over a minute.
    assert seen == {"tries": 2, "backoff": (1,)}
    summary = json.loads((out / "garak_summary.json").read_text())
    assert summary["overall"]["judge_unavailable"] == 3
    assert summary["overall"]["graded"] == 1
    rows = [json.loads(line) for line in (out / "garak_graded.jsonl").read_text().splitlines()]
    assert [r["reason_code"] for r in rows].count("JUDGE_UNAVAILABLE") == 3
    err = capsys.readouterr().err
    assert "3 outputs were not evaluated because the judge model could not be reached" in err
    assert "judge.invalid" not in err


def test_garak_grade_exits_0_when_every_output_was_evaluated(
    rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    _live(monkeypatch, ["pass"])
    out = tmp_path / "out"
    args = ["garak-grade", str(FIXTURE), "--rules", str(write_rules(rules_data)), "--out", str(out)]
    assert cli.main(args) == 0
    assert json.loads((out / "garak_summary.json").read_text())["overall"]["judge_unavailable"] == 0
    assert "not evaluated" not in capsys.readouterr().err
