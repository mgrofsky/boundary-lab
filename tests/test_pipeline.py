from redline import gate, pipeline
from redline.judge import FakeJudge
from redline.router import RouteResult


class StubRouter:
    def __init__(self, result):
        self.result = result

    def route(self, prompt, response):
        return self.result


def record(user_type):
    return {"id": "t-1", "user_type": user_type, "prompt": "my balance?", "response": "It is $40."}


FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "DATA_DISCLOSED", "reason_text": "x"}


def test_anonymous_on_acct_is_failed_by_judge_with_stance(acct_rule):
    judge = FakeJudge({**FAIL, "reason_code": "NOT_ALLOWED_FOR_USER"})
    router = StubRouter(RouteResult("ACCT-01", 0.9, False))
    [out] = pipeline.run([record("anonymous")], {"ACCT-01": acct_rule}, router, judge, {})
    assert out["outcome"] == "fail"
    assert out["decided_by"] == "judge"
    assert out["reason_code"] == "NOT_ALLOWED_FOR_USER"
    assert judge.calls[0]["stance"] == gate.check(acct_rule, "anonymous").text


def test_verified_on_acct_is_decided_by_judge(acct_rule):
    judge = FakeJudge(FAIL)
    router = StubRouter(RouteResult("ACCT-01", 0.9, False))
    [out] = pipeline.run([record("verified")], {"ACCT-01": acct_rule}, router, judge, {})
    assert out["outcome"] == "fail"
    assert out["decided_by"] == "judge"
    assert len(judge.calls) == 1


def test_router_abstain_gives_review(acct_rule):
    judge = FakeJudge(FAIL)
    router = StubRouter(RouteResult("none", 0.1, True))
    [out] = pipeline.run([record("verified")], {"ACCT-01": acct_rule}, router, judge, {})
    assert out["outcome"] == "review"
    assert out["decided_by"] == "router"
    assert judge.calls == []
