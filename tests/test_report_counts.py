from redline import metrics, report
from redline.intervals import interval

PROMPTS = [
    {"id": "a1", "kind": "roleplay_injection", "target_rule_id": "PAY-01"},
    {"id": "a2", "kind": "roleplay_injection", "target_rule_id": "PAY-01"},
]


def out(record_id, outcome, routed="PAY-01"):
    return {
        "id": record_id,
        "outcome": outcome,
        "routed_rule": routed,
        "decided_by": "judge",
        "short_circuit": False,
    }


def test_rate_text_gives_count_share_and_interval():
    assert report.rate_text(interval(22, 24)) == "22 of 24 (92%, 95% interval 74% to 98%)"
    assert report.rate_text(interval(0, 0)) == "0 of 0 (no answers)"


def small_summary():
    holdout = [out("h1", "fail"), out("h2", "pass"), out("h3", "pass")]
    holdout_labels = [
        {"id": "h1", "rule_id": "PAY-01", "label": "bad"},
        {"id": "h2", "rule_id": "PAY-01", "label": "bad"},
        {"id": "h3", "rule_id": "PAY-01", "label": "good"},
    ]
    attack = [out("a1", "fail"), out("a1#2", "pass"), out("a2", "pass", routed="SYS-01")]
    attack_labels = [
        {"id": "a1", "rule_id": "PAY-01", "label": "bad"},
        {"id": "a2", "rule_id": "PAY-01", "label": "good"},
    ]
    return report.build_summary(
        holdout, holdout_labels, attack, attack_labels, PROMPTS, "tfidf", {}, None, None
    )


def test_summary_leaves_unlabeled_attack_answers_out_and_counts_them():
    summary = small_summary()
    assert summary["attack_unlabeled"] == 1
    assert summary["attack_routing"] == {"total": 2, "misrouted": 1}
    assert summary["attack"]["roleplay_injection"]["prompts"] == 2
    assert summary["attack_end_to_end"]["counts"]["bad"]["caught"] == interval(1, 1)


def test_report_prints_each_end_to_end_rate_with_count_and_interval():
    text = report.render_report(small_summary())
    assert "Rule breaks caught: 1 of 2 (50%, 95% interval 9% to 91%)" in text
    assert "Good answers wrongly failed: 0 of 1" in text
    assert "1 attack answer had no hand label and was left out of every rate." in text


def test_readme_table_gives_counts_and_intervals():
    table = report.render_readme_table(small_summary())
    assert "| Rule breaks caught on holdout | 1 of 2 (50%, 95% interval 9% to 91%) |" in table
    assert "| Of those, caught by Redline | 1 of 1 (100%, 95% interval 21% to 100%) |" in table
    assert "| Attack answers with no hand label, left out | 1 |" in table


def test_readme_table_without_counts_keeps_the_old_form():
    summary = small_summary()
    for block in ("end_to_end", "attack_end_to_end"):
        del summary[block]["counts"]
    del summary["attack_unlabeled"]
    table = report.render_readme_table(summary)
    assert "| Rule breaks caught on holdout | 50 percent |" in table


def test_report_prints_the_flip_rate_when_rows_were_graded_more_than_once():
    holdout = [dict(out("h1", "fail"), attempts=2, outcomes=["fail", "pass"])]
    labels = [{"id": "h1", "rule_id": "PAY-01", "label": "bad"}]
    summary = report.build_summary(holdout, labels, [], [], [], "tfidf", {}, None, None)
    assert summary["end_to_end"]["counts"]["flip_rate"] == interval(1, 1)
    text = report.render_report(summary)
    assert "Judged answers whose attempts disagreed: 1 of 1" in text
    assert metrics.end_to_end(holdout, labels)["counts"]["bad"]["missed"] == interval(1, 1)
