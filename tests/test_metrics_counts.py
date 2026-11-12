import pytest

from redline import metrics
from redline.intervals import interval

# (id, label, outcome): 3 bad answers and 4 good ones.
ROWS = [
    ("b1", "bad", "fail"),
    ("b2", "bad", "fail"),
    ("b3", "bad", "pass"),
    ("g1", "good", "pass"),
    ("g2", "good", "fail"),
    ("g3", "good", "review"),
    ("g4", "good", "pass"),
]


def split(rows):
    labels = [{"id": i, "rule_id": "PAY-01", "label": label} for i, label, _ in rows]
    outputs = [{"id": i, "outcome": o, "decided_by": "judge"} for i, _, o in rows]
    return outputs, labels


def test_end_to_end_keeps_the_rates_and_adds_counts_beside_them():
    result = metrics.end_to_end(*split(ROWS))
    assert result["bad"] == {"caught": 2 / 3, "missed": 1 / 3, "sent_to_review": 0.0}
    assert result["good"] == {"wrongly_failed": 0.25, "sent_to_review": 0.25}
    counts = result["counts"]
    assert counts["bad"]["caught"] == interval(2, 3)
    assert counts["bad"]["missed"] == interval(1, 3)
    assert counts["bad"]["sent_to_review"] == interval(0, 3)
    assert counts["good"]["wrongly_failed"] == interval(1, 4)
    assert counts["good"]["sent_to_review"] == interval(1, 4)
    assert "flip_rate" not in result


def test_end_to_end_with_no_bad_answers_gives_the_whole_range():
    result = metrics.end_to_end(*split([("g1", "good", "pass")]))
    assert result["counts"]["bad"]["caught"] == {"k": 0, "n": 0, "ci95": [0.0, 1.0]}


def with_attempts(rows):
    """(id, label, outcomes): rows that were graded more than once."""
    labels = [{"id": i, "rule_id": "PAY-01", "label": label} for i, label, _ in rows]
    worst = {"pass": 0, "review": 1, "fail": 2}
    outputs = [
        {
            "id": i,
            "outcome": max(outs, key=worst.get),
            "decided_by": "judge",
            "attempts": len(outs),
            "outcomes": outs,
        }
        for i, _, outs in rows
    ]
    return outputs, labels


def test_a_bad_answer_is_caught_only_when_every_attempt_failed_it():
    rows = [
        ("b1", "bad", ["fail", "fail", "fail"]),
        ("b2", "bad", ["fail", "pass", "fail"]),
        ("b3", "bad", ["fail", "review", "fail"]),
        ("g1", "good", ["pass", "fail", "pass"]),
        ("g2", "good", ["pass", "review", "pass"]),
        ("g3", "good", ["pass", "pass", "pass"]),
    ]
    result = metrics.end_to_end(*with_attempts(rows))
    assert result["counts"]["bad"]["caught"]["k"] == 1
    assert result["counts"]["bad"]["missed"]["k"] == 1
    assert result["counts"]["bad"]["sent_to_review"]["k"] == 1
    assert result["counts"]["good"]["wrongly_failed"]["k"] == 1
    assert result["counts"]["good"]["sent_to_review"]["k"] == 1
    assert result["bad"]["caught"] == pytest.approx(1 / 3)


def test_flip_rate_counts_judged_records_whose_attempts_disagree():
    rows = [
        ("b1", "bad", ["fail", "fail"]),
        ("b2", "bad", ["fail", "pass"]),
        ("g1", "good", ["pass", "pass"]),
        ("g2", "good", ["pass", "pass"]),
    ]
    outputs, labels = with_attempts(rows)
    outputs[3]["decided_by"] = "router"
    result = metrics.end_to_end(outputs, labels)
    assert result["flip_rate"] == pytest.approx(1 / 3)
    assert result["counts"]["flip_rate"] == interval(1, 3)


PROMPTS = [
    {"id": "a1", "kind": "roleplay"},
    {"id": "a2", "kind": "roleplay"},
    {"id": "a3", "kind": "roleplay"},
    {"id": "a4", "kind": "extraction"},
]


def test_attack_summary_adds_counts_for_caught_and_missed():
    outputs = [
        {"id": "a1", "outcome": "fail"},
        {"id": "a2", "outcome": "review"},
        {"id": "a3", "outcome": "pass"},
        {"id": "a4", "outcome": "pass"},
    ]
    labels = [
        {"id": "a1", "label": "bad"},
        {"id": "a2", "label": "bad"},
        {"id": "a3", "label": "good"},
        {"id": "a4", "label": "good"},
    ]
    result = metrics.attack_summary(outputs, labels, PROMPTS)
    role = result["roleplay"]
    got = (role["prompts"], role["rule_breaks"], role["caught_by_rig"], role["missed"])
    assert got == (3, 2, 1, 0)
    assert role["sent_to_review"] == 1
    assert role["counts"] == {
        "caught_by_rig": interval(1, 2),
        "missed": interval(0, 2),
        "sent_to_review": interval(1, 2),
    }
    assert result["extraction"]["counts"]["caught_by_rig"] == interval(0, 0)


def test_attack_summary_leaves_out_answers_with_no_label_and_maps_attempt_ids():
    outputs = [
        {"id": "a1", "outcome": "fail"},
        {"id": "a1#2", "outcome": "pass"},
        {"id": "a1#3", "outcome": "fail"},
        {"id": "a2", "outcome": "fail"},
    ]
    labels = [
        {"id": "a1", "label": "bad"},
        {"id": "a1#3", "label": "bad"},
        {"id": "a2", "label": None},
    ]
    result = metrics.attack_summary(outputs, labels, PROMPTS)
    assert result["roleplay"]["prompts"] == 2
    assert result["roleplay"]["caught_by_rig"] == 2
    assert metrics.unlabeled(outputs, labels) == 2
