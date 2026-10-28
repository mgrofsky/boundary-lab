import json

import httpx
import pytest

from redline import judge as judge_module
from redline.judge import ChatEndpointJudge


def grade_with(raw, rule):
    judge = ChatEndpointJudge("http://unused", "m", "k", transport=lambda payload: raw)
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
        return GOOD

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
