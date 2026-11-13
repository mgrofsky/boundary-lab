"""One judge object can serve many Graders. Each Grader's role stays its own."""

import copy
import json
import pickle
import threading
from pathlib import Path

import httpx
import pytest

from redline import Grader, gate
from redline.garak import grade_report, report
from redline.judge import ASSISTANT_ROLE, ChatEndpointJudge, ReplayJudge, build_prompt, prompt_key

FIXTURE = Path(__file__).parent / "fixtures" / "garak_report.jsonl"
REPLY = '{"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "x", "nonce": "%s"}'
ROLE_A = "a billing assistant"


def _transport(sent):
    def send(payload):
        text = payload["messages"][0]["content"]
        sent.append(text)
        return REPLY % text.rsplit('value "', 1)[1].split('"')[0]

    return send


def _role(prompt_text):
    return prompt_text.splitlines()[0].split("one answer from ")[1].split(" against ONE")[0]


def _tenants(rules_data, judge):
    """Tenant A's rules name a role. Tenant B's do not."""
    with_role = {"assistant_role": ROLE_A, "rules": rules_data}
    return Grader.from_rules(with_role, judge, top_k=1), Grader.from_rules(rules_data, judge, top_k=1)


def test_two_tenants_share_one_judge_and_each_keeps_its_role(rules_data):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    a, b = _tenants(rules_data, judge)
    for grader in (a, b, a, b, b, a):
        grader.grade("q", "an answer", "anonymous")
    assert [_role(text) for text in sent] == [
        ROLE_A,
        ASSISTANT_ROLE,
        ROLE_A,
        ASSISTANT_ROLE,
        ASSISTANT_ROLE,
        ROLE_A,
    ]
    assert (a.assistant_role, b.assistant_role) == (ROLE_A, ASSISTANT_ROLE)


def test_a_grader_never_changes_the_judge_it_was_given(rules_data):
    judge = ChatEndpointJudge(None, "m", None, transport=_transport([]), assistant_role="my own")
    before = dict(vars(judge))
    a, _ = _tenants(rules_data, judge)
    a.grade("q", "an answer", "anonymous")
    grade_report(FIXTURE, rules_data, judge)
    report.build_grader(rules_data, judge)
    Grader.from_rules(rules_data, judge, attempts=2).grade("q", "an answer", "anonymous")
    assert vars(judge) == before


def test_grade_report_does_not_leave_its_default_role_on_the_judge(rules_data):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    result = grade_report(FIXTURE, rules_data, judge, top_k=1)
    assert result["assistant_role"] == report.ASSISTANT_ROLE
    assert {_role(text) for text in sent} == {report.ASSISTANT_ROLE}
    del sent[:]
    Grader.from_rules(rules_data, judge, top_k=1).grade("q", "an answer", "anonymous")
    assert _role(sent[0]) == ASSISTANT_ROLE


def test_the_role_reaches_repeat_attempts(rules_data):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    Grader.from_rules({"assistant_role": ROLE_A, "rules": rules_data}, judge, attempts=3).grade(
        "q", "an answer", "anonymous"
    )
    assert [_role(text) for text in sent] == [ROLE_A] * 6


def test_one_saved_file_replays_for_each_tenant_under_its_own_role(rules_data, tmp_path):
    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge(None, "m", None, transport=_transport([]), cache_path=cache)
    a, b = _tenants([rules_data[1]], judge)
    a.grade("q", "an answer", "anonymous")
    b.grade("q", "an answer", "anonymous")
    keys = [json.loads(line)["key"] for line in cache.read_text().splitlines()]
    assert len(keys) == 2 and keys[0] != keys[1]
    replay = ReplayJudge(cache)
    ra, rb = _tenants([rules_data[1]], replay)
    for grader in (ra, rb, ra):
        assert grader.grade("q", "an answer", "anonymous")["outcome"] == "pass"
    assert replay.missing_lookups == 0
    # A Grader still shows what its judge counted.
    assert ra.judge.missing_lookups == 0


def test_the_default_key_is_what_build_prompt_gives_with_no_role(pay_rule, tmp_path, rules_data):
    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge(None, "m", None, transport=_transport([]), cache_path=cache)
    _, b = _tenants([rules_data[1]], judge)
    b.grade("q", "an answer", "anonymous")
    stance = gate.check(pay_rule, "anonymous").text
    plain = build_prompt(pay_rule, {"good": [], "bad": []}, "q", "an answer", "anonymous", stance)
    assert f'"key": "{prompt_key(plain)}"' in cache.read_text()


def test_many_threads_share_one_judge(rules_data, tmp_path):
    sent = []
    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent), cache_path=cache)
    graders = {
        role: Grader.from_rules({"assistant_role": role, "rules": rules_data}, judge)
        for role in (f"assistant number {n}" for n in range(8))
    }
    start, wrong = threading.Barrier(len(graders)), []

    def work(role, grader):
        start.wait()
        for n in range(25):
            grader.grade("q", f"answer {n} for {role}", "anonymous")

    threads = [threading.Thread(target=work, args=item) for item in graders.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for text in sent:
        answer = text.split("<assistant_response>")[1].split("</assistant_response>")[0]
        marker = answer.split(" for ", 1)[1]
        if _role(text) != marker:
            wrong.append((marker, _role(text)))
    assert len(sent) == 8 * 25 * 2
    assert wrong == []
    # Every line of the saved file is whole.
    lines = cache.read_text().splitlines()
    assert len(lines) == len(sent)
    assert all(line.startswith('{"key": "') and line.endswith("}") for line in lines)


def test_an_outage_seen_by_one_grader_does_not_stop_another(rules_data, capsys):
    state = {"down": True}

    def transport(payload):
        if state["down"]:
            raise httpx.ConnectError("connection refused")
        return _transport([])(payload)

    judge = ChatEndpointJudge(None, "m", None, transport=transport)
    first = grade_report(FIXTURE, rules_data, judge)
    assert first["stopped"] == "judge_unavailable"
    state["down"] = False
    second = grade_report(FIXTURE, rules_data, judge)
    assert second["stopped"] is None and second["overall"]["judge_unavailable"] == 0
    capsys.readouterr()


def test_a_view_can_be_copied_and_shows_what_the_judge_holds(rules_data):
    judge = ChatEndpointJudge(None, "the-model", None, transport=_transport([]))
    a, _ = _tenants(rules_data, judge)
    view = copy.copy(a.judge)
    assert (view.assistant_role, view.model, view.judge) == (ROLE_A, "the-model", judge)
    assert copy.deepcopy(a.judge).assistant_role == ROLE_A
    with pytest.raises(AttributeError):
        _ = view.no_such_name


def test_a_judge_can_still_be_copied_and_pickled(tmp_path, pay_rule):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    judge = ChatEndpointJudge(None, "m", None, transport=_transport([]), cache_path=cache)
    live = copy.deepcopy(judge)
    assert live.model == "m" and live._lock is not None
    replay = pickle.loads(pickle.dumps(ReplayJudge(cache, missing="review")))
    args = (pay_rule, {"good": [], "bad": []}, "q", "an answer", "anonymous", "stance")
    assert replay.grade(*args)["reason_code"] == "NO_SAVED_GRADE"
    assert replay.missing_lookups == 1
