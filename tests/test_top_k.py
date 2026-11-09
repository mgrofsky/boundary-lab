import numpy as np
import pytest

from redline import pipeline
from redline.judge import FakeJudge
from redline.router import KMeansRouter, Router, RouteResult

PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
REVIEW = {"verdict": "review", "severity": None, "reason_code": "JUDGE_ERROR", "reason_text": "?"}


class VectorEmbedder:
    """For tests. Returns the same vector for every text."""

    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=np.float32)

    def encode(self, texts):
        return np.vstack([self.vec for _ in texts])


def test_center_rank_orders_every_rule_by_similarity():
    centers = {"A": [1, 0, 0], "B": [0, 1, 0], "none": [0, 0, 1]}
    router = Router.from_centers(centers, threshold=0.1)
    router.embedder = VectorEmbedder([0.2, 0.9, 0.4])
    ranked = router.rank("p", "r", "verified")
    assert [c.rule_id for c in ranked] == ["B", "none", "A"]
    assert ranked[0].similarity > ranked[1].similarity > ranked[2].similarity
    assert ranked[0].cell == 1
    assert router.route("p", "r").rule_id == ranked[0].rule_id


def test_kmeans_rank_keeps_the_best_cluster_for_each_rule():
    centers = [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]]
    router = KMeansRouter.from_cells(centers, ["A", "A", "B"], [{}, {}, {}], threshold=0.1)
    router.embedder = VectorEmbedder([1, 0.05, 0])
    ranked = router.rank("p", "r")
    assert [c.rule_id for c in ranked] == ["A", "B"]
    assert ranked[0].cell == 0


class StubRouter:
    def __init__(self, ranked, threshold=0.0):
        self.ranked = [RouteResult(rule_id, sim, False, i) for i, (rule_id, sim) in enumerate(ranked)]
        self.threshold = threshold

    def route(self, prompt, response, user_type=None):
        return self.ranked[0]

    def rank(self, prompt, response, user_type=None):
        return list(self.ranked)


RECORD = {"id": "t-1", "user_type": "verified", "prompt": "pay?", "response": "Card 4111 saved."}


@pytest.fixture
def rules(acct_rule, pay_rule):
    return {"ACCT-01": acct_rule, "PAY-01": pay_rule}


def test_top_k_one_keeps_the_record_as_it_was(rules):
    router = StubRouter([("ACCT-01", 0.9), ("PAY-01", 0.8)])
    [one] = pipeline.run([RECORD], rules, router, FakeJudge(PASS), {})
    [default] = pipeline.run([RECORD], rules, router, FakeJudge(PASS), {}, top_k=1)
    assert one == default
    assert "rules_checked" not in one
    assert "outcomes_by_rule" not in one


def test_top_k_takes_the_worst_outcome(rules):
    router = StubRouter([("ACCT-01", 0.9), ("PAY-01", 0.8)])
    judge = FakeJudge([PASS, FAIL])
    [out] = pipeline.run([RECORD], rules, router, judge, {}, top_k=2)
    assert [c["rule"].rule_id for c in judge.calls] == ["ACCT-01", "PAY-01"]
    assert out["rules_checked"] == ["ACCT-01", "PAY-01"]
    assert out["outcomes_by_rule"] == {"ACCT-01": "pass", "PAY-01": "fail"}
    assert out["routed_rule"] == "PAY-01"
    assert out["similarity"] == pytest.approx(0.8)
    assert out["cell"] == 1
    assert (out["outcome"], out["reason_code"], out["severity"]) == ("fail", "CARD_ECHOED", 0.9)
    assert out["decided_by"] == "judge"


def test_review_beats_pass_and_a_tie_keeps_the_closer_rule(rules):
    router = StubRouter([("ACCT-01", 0.9), ("PAY-01", 0.8)])
    [out] = pipeline.run([RECORD], rules, router, FakeJudge([PASS, REVIEW]), {}, top_k=2)
    assert out["outcome"] == "review"
    assert out["routed_rule"] == "PAY-01"
    [out] = pipeline.run([RECORD], rules, router, FakeJudge([FAIL, FAIL]), {}, top_k=2)
    assert out["routed_rule"] == "ACCT-01"


def test_the_floor_drops_weak_matches_but_keeps_the_top_one(rules):
    router = StubRouter([("ACCT-01", 0.5), ("PAY-01", 0.4)])
    judge = FakeJudge(PASS)
    [out] = pipeline.run([RECORD], rules, router, judge, {}, top_k=2, floor=0.6)
    assert out["rules_checked"] == ["ACCT-01"]
    assert len(judge.calls) == 1
    [out] = pipeline.run([RECORD], rules, router, FakeJudge(PASS), {}, top_k=2, floor=0.4)
    assert out["rules_checked"] == ["ACCT-01", "PAY-01"]


def test_the_off_topic_label_is_never_judged(rules):
    router = StubRouter([("ACCT-01", 0.9), ("none", 0.85), ("PAY-01", 0.8)])
    judge = FakeJudge(PASS)
    [out] = pipeline.run([RECORD], rules, router, judge, {}, top_k=2)
    assert out["rules_checked"] == ["ACCT-01", "PAY-01"]


def test_an_off_topic_top_match_still_passes_without_the_judge(rules):
    router = StubRouter([("none", 0.9), ("PAY-01", 0.8)])
    judge = FakeJudge(FAIL)
    [out] = pipeline.run([RECORD], rules, router, judge, {}, top_k=3)
    assert (out["outcome"], out["decided_by"]) == ("pass", "router")
    assert out["rules_checked"] == []
    assert out["outcomes_by_rule"] == {}
    assert judge.calls == []


def test_judge_calls_counts_every_rule_checked(rules):
    router = StubRouter([("ACCT-01", 0.9), ("PAY-01", 0.8)])
    outputs = pipeline.run([RECORD, RECORD], rules, router, FakeJudge(PASS), {}, top_k=2)
    assert pipeline.judge_calls(outputs) == 4
    outputs = pipeline.run([RECORD, RECORD], rules, router, FakeJudge(PASS), {})
    assert pipeline.judge_calls(outputs) == 2


def test_top_k_must_be_at_least_one(rules):
    router = StubRouter([("ACCT-01", 0.9)])
    with pytest.raises(ValueError, match="top_k"):
        pipeline.run([RECORD], rules, router, FakeJudge(PASS), {}, top_k=0)
