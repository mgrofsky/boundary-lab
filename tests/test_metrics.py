import pytest

from redline import metrics

# The six row fixture from the plan: (id, rule_id, label, outcome).
ROWS = [
    ("1", "ACCT-01", "bad", "fail"),
    ("2", "ACCT-01", "bad", "pass"),
    ("3", "ACCT-01", "good", "pass"),
    ("4", "PAY-01", "good", "fail"),
    ("5", "PAY-01", "bad", "fail"),
    ("6", "PAY-01", "good", "pass"),
]


def split(rows):
    labels = [{"id": i, "rule_id": rule_id, "label": label} for i, rule_id, label, _ in rows]
    outputs = [{"id": i, "outcome": outcome} for i, _, _, outcome in rows]
    return outputs, labels


def test_per_rule_precision_and_recall():
    result = metrics.per_rule(*split(ROWS))
    assert result["ACCT-01"]["precision"] == pytest.approx(1.0)
    assert result["ACCT-01"]["recall"] == pytest.approx(0.5)
    assert result["PAY-01"]["precision"] == pytest.approx(0.5)
    assert result["PAY-01"]["recall"] == pytest.approx(1.0)
    assert result["ACCT-01"]["support"] == 3


def test_per_rule_breaks_counts_hand_labeled_bad_answers():
    result = metrics.per_rule(*split(ROWS))
    assert result["ACCT-01"]["breaks"] == 2
    assert result["PAY-01"]["breaks"] == 1


def test_accuracy_is_four_of_six():
    assert metrics.accuracy(*split(ROWS)) == pytest.approx(4 / 6)


def test_kappa_is_one_when_every_outcome_matches():
    matching = [
        (i, rule_id, label, "fail" if label == "bad" else "pass") for i, rule_id, label, _ in ROWS
    ]
    assert metrics.cohen_kappa(*split(matching)) == pytest.approx(1.0)


def short_circuited(rows, flags):
    outputs, labels = split(rows)
    for out, flag in zip(outputs, flags):
        out["short_circuit"] = flag
    return outputs, labels


def test_short_circuit_summary_with_none_short_circuited():
    result = metrics.short_circuit_summary(*short_circuited(ROWS, [False] * 6))
    assert result == {"count": 0, "share": 0.0, "right": 0, "wrong_ids": []}


def test_short_circuit_summary_counts_two_right_and_one_wrong():
    flags = [True, False, True, True, False, False]
    result = metrics.short_circuit_summary(*short_circuited(ROWS, flags))
    assert result["count"] == 3
    assert result["right"] == 2
    assert result["wrong_ids"] == ["4"]


def test_short_circuit_share_uses_every_record():
    flags = [True, True, False, False, False, False]
    result = metrics.short_circuit_summary(*short_circuited(ROWS, flags))
    assert result["share"] == pytest.approx(2 / 6)
