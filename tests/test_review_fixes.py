"""Tests for the defects an independent review found in Redline 0.3.0."""

from redline import metrics, report, thresholds


def _row(rid, outcomes, rule="PAY-01"):
    worst = max(outcomes, key=lambda o: {"pass": 0, "review": 1, "fail": 2}[o])
    return {
        "id": rid,
        "outcome": worst,
        "outcomes": outcomes,
        "attempts": len(outcomes),
        "decided_by": "judge",
        "routed_rule": rule,
        "short_circuit": False,
    }


def _lab(rid, label, rule="PAY-01"):
    return {"id": rid, "label": label, "rule_id": rule}


# Two bad answers each failed once then passed once, and two good answers always passed.
DISAGREE_OUT = [
    _row("b1", ["fail", "pass"]),
    _row("b2", ["fail", "pass"]),
    _row("g1", ["pass", "pass"]),
    _row("g2", ["pass", "pass"]),
]
DISAGREE_LAB = [_lab("b1", "bad"), _lab("b2", "bad"), _lab("g1", "good"), _lab("g2", "good")]


def test_attempts_that_disagree_tell_the_same_story_everywhere():
    e2e = metrics.end_to_end(DISAGREE_OUT, DISAGREE_LAB)
    assert e2e["bad"]["caught"] == 0.0 and e2e["bad"]["missed"] == 1.0
    rule = metrics.per_rule(DISAGREE_OUT, DISAGREE_LAB)["PAY-01"]
    assert rule["recall"] == 0.0
    assert metrics.accuracy(DISAGREE_OUT, DISAGREE_LAB) == 0.5
    assert metrics.cohen_kappa(DISAGREE_OUT, DISAGREE_LAB) == 0.0


def test_a_good_answer_failed_once_counts_as_failed_everywhere():
    outs = [_row("b1", ["fail", "fail"]), _row("g1", ["pass", "fail"])]
    labs = [_lab("b1", "bad"), _lab("g1", "good")]
    assert metrics.end_to_end(outs, labs)["good"]["wrongly_failed"] == 1.0
    assert metrics.per_rule(outs, labs)["PAY-01"]["precision"] == 0.5
    assert metrics.accuracy(outs, labs) == 0.5


def test_gate_min_kappa_fails_when_attempts_disagree():
    summary = report.build_summary(DISAGREE_OUT, DISAGREE_LAB, [], [], [], "m", {})
    assert thresholds.check(summary, min_kappa=0.5) == [
        "kappa on holdout is 0.000, below the minimum of 0.500"
    ]
