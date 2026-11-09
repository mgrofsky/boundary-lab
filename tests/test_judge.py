import json
import re

import httpx
import pytest

from redline import judge as judge_module
from redline.judge import ChatEndpointJudge, ReplayJudge, build_prompt, parse_answer, prompt_key

NONCE_LINE = re.compile(r'include the key "nonce" with the value "([0-9a-f]+)"')


def nonce_of(payload):
    """The check value the live judge put in the prompt it sent."""
    return NONCE_LINE.search(payload["messages"][0]["content"]).group(1)


def echo(raw):
    """A transport that answers raw, with the prompt's check value added to the JSON."""

    def send(payload):
        return raw.replace('{"verdict"', f'{{"nonce": "{nonce_of(payload)}", "verdict"', 1)

    return send


def grade_with(raw, rule):
    judge = ChatEndpointJudge("http://unused", "m", "k", transport=echo(raw))
    args = ("what is my balance", "It is $40.", "verified", "The user is verified.")
    return judge.grade(rule, {"good": [], "bad": []}, *args)


def test_clean_json_parses(acct_rule):
    raw = json.dumps(
        {"verdict": "fail", "severity": 0.9, "reason_code": "DATA_DISCLOSED", "reason_text": "x"}
    )
    result = grade_with(raw, acct_rule)
    assert result["verdict"] == "fail"
    assert result["severity"] == 0.9
    assert result["reason_code"] == "DATA_DISCLOSED"


def test_code_fences_are_stripped(acct_rule):
    raw = '```json\n{"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "x"}\n```'
    result = grade_with(raw, acct_rule)
    assert result["verdict"] == "pass"
    assert result["reason_code"] == "OK"


def test_bad_json_gives_review(acct_rule):
    result = grade_with("this is not json {", acct_rule)
    assert result["verdict"] == "review"
    assert result["reason_code"] == "JUDGE_ERROR"


def test_unknown_verdict_gives_judge_error(acct_rule):
    raw = json.dumps({"verdict": "maybe", "severity": 0.5, "reason_code": "OK"})
    result = grade_with(raw, acct_rule)
    assert result["verdict"] == "review"
    assert result["reason_code"] == "JUDGE_ERROR"


def test_unknown_reason_code_becomes_other(acct_rule):
    raw = json.dumps({"verdict": "fail", "severity": 0.7, "reason_code": "XYZ"})
    assert grade_with(raw, acct_rule)["reason_code"] == "OTHER"


GOOD = json.dumps(
    {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "fine"}
)
ARGS = ("what is my balance", "It is $40.", "verified", "The user is verified.")


def test_cached_answer_is_reused(acct_rule, tmp_path):
    calls = []

    def transport(payload):
        calls.append(payload)
        return echo(GOOD)(payload)

    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge("http://unused", "m", "k", transport=transport, cache_path=cache)
    first = judge.grade(acct_rule, {"good": [], "bad": []}, *ARGS)
    second = judge.grade(acct_rule, {"good": [], "bad": []}, *ARGS)
    assert len(calls) == 1
    assert second == first
    fresh = ChatEndpointJudge("http://unused", "m", "k", transport=transport, cache_path=cache)
    assert fresh.grade(acct_rule, {"good": [], "bad": []}, *ARGS) == first
    assert len(calls) == 1


def test_errors_stay_out_of_cache(acct_rule, tmp_path):
    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge(
        "http://unused", "m", "k", transport=lambda payload: "junk", cache_path=cache
    )
    result = judge.grade(acct_rule, {"good": [], "bad": []}, *ARGS)
    assert result["reason_code"] == "JUDGE_ERROR"
    assert not cache.exists() or cache.read_text().strip() == ""


def _response(status, body=None):
    request = httpx.Request("POST", "http://unused/chat/completions")
    return httpx.Response(status, json=body or {}, request=request)


def test_retry_on_429(monkeypatch):
    waits = []
    monkeypatch.setattr(judge_module, "_sleep", waits.append)
    ok = {"choices": [{"message": {"content": GOOD}}]}
    replies = [_response(429), _response(429), _response(200, ok)]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: replies.pop(0))
    send = judge_module.http_transport("http://unused", "k")
    assert json.loads(send({}))["verdict"] == "pass"
    assert waits == [2, 4]
    assert replies == []


def test_other_status_is_not_retried(monkeypatch):
    monkeypatch.setattr(judge_module, "_sleep", lambda s: None)
    calls = []

    def post(*a, **k):
        calls.append(1)
        return _response(401)

    monkeypatch.setattr(httpx, "post", post)
    send = judge_module.http_transport("http://unused", "k")
    with pytest.raises(httpx.HTTPStatusError):
        send({})
    assert len(calls) == 1


def test_think_block_is_dropped(acct_rule):
    result = grade_with("<think>let me see\n{not json}</think>\n" + GOOD, acct_rule)
    assert result["verdict"] == "pass"


RULE_ARGS = ({"good": [], "bad": []}, *ARGS)


def test_the_nonce_adds_one_sentence_and_leaves_the_cache_key_alone(acct_rule):
    plain = build_prompt(acct_rule, *RULE_ARGS)
    checked = build_prompt(acct_rule, *RULE_ARGS, nonce="ab12")
    assert build_prompt(acct_rule, *RULE_ARGS, nonce=None) == plain
    assert checked.startswith(plain)
    extra = checked[len(plain):].strip()
    assert extra == 'Also include the key "nonce" with the value "ab12" in the JSON object.'


def test_parse_answer_checks_the_nonce_only_when_given(acct_rule):
    good = json.loads(GOOD)
    assert parse_answer(GOOD, acct_rule)["verdict"] == "pass"
    assert parse_answer(json.dumps({**good, "nonce": "ab12"}), acct_rule, "ab12")["verdict"] == "pass"
    for raw in (GOOD, json.dumps({**good, "nonce": "zz99"})):
        result = parse_answer(raw, acct_rule, "ab12")
        assert result["verdict"] == "review"
        assert result["reason_code"] == "JUDGE_NONCE"
        assert result["reason_text"] == "The judge reply did not carry the check value."


def test_a_reply_with_the_nonce_is_graded_and_cached_under_the_plain_key(acct_rule, tmp_path):
    sent = []

    def transport(payload):
        sent.append(payload)
        return echo(GOOD)(payload)

    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge("http://unused", "m", "k", transport=transport, cache_path=cache)
    result = judge.grade(acct_rule, *RULE_ARGS)
    assert result["verdict"] == "pass"
    [entry] = [json.loads(line) for line in cache.read_text().splitlines()]
    assert entry["key"] == prompt_key(build_prompt(acct_rule, *RULE_ARGS))
    assert nonce_of(sent[0]) in sent[0]["messages"][0]["content"]


def test_each_call_gets_a_new_nonce(acct_rule):
    sent = []

    def transport(payload):
        sent.append(nonce_of(payload))
        return echo(GOOD)(payload)

    judge = ChatEndpointJudge("http://unused", "m", "k", transport=transport)
    judge.grade(acct_rule, *RULE_ARGS)
    judge.grade(acct_rule, *RULE_ARGS)
    assert len(set(sent)) == 2


def test_a_reply_without_the_nonce_is_review_and_not_cached(acct_rule, tmp_path):
    cache = tmp_path / "cache.jsonl"
    judge = ChatEndpointJudge(
        "http://unused", "m", "k", transport=lambda payload: GOOD, cache_path=cache
    )
    result = judge.grade(acct_rule, *RULE_ARGS)
    assert (result["verdict"], result["reason_code"]) == ("review", "JUDGE_NONCE")
    assert not cache.exists() or cache.read_text().strip() == ""


def test_a_saved_answer_is_used_without_any_nonce_check(acct_rule, tmp_path):
    cache = tmp_path / "cache.jsonl"
    answer = {**json.loads(GOOD), "raw": GOOD}
    key = prompt_key(build_prompt(acct_rule, *RULE_ARGS))
    cache.write_text(json.dumps({"key": key, "model": "m", "rule_id": "ACCT-01", "answer": answer}) + "\n")

    def transport(payload):
        raise AssertionError("a saved answer must not call the judge")

    live = ChatEndpointJudge("http://unused", "m", "k", transport=transport, cache_path=cache)
    assert live.grade(acct_rule, *RULE_ARGS)["verdict"] == "pass"
    assert ReplayJudge(cache).grade(acct_rule, *RULE_ARGS)["verdict"] == "pass"
