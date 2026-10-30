import hashlib
import json
from pathlib import Path

from redline import report


def load_summary():
    """The published summary, with the keys a fresh run adds filled in if it predates them."""
    summary = json.loads(Path("results/summary.json").read_text())
    summary.setdefault("judge_model", ["test-model"])
    summary.setdefault("attack_routing", {"total": 80, "misrouted": 21})
    summary.setdefault("attack_end_to_end", summary["end_to_end"])
    return summary


def test_report_says_what_each_number_counts():
    summary = load_summary()
    text = report.render_report(summary)
    assert "The judge model was" in text
    assert "cache file's hash, a fingerprint of its contents, is in `summary.json`" in text
    assert "including 8 off-topic answers" in text
    assert "Accuracy and kappa handle review differently" in text
    assert "grouped by the rule each attack targeted" in text
    routing = summary["attack_routing"]
    assert f"{routing['misrouted']} of the {routing['total']} attack answers" in text
    assert "| Set | Rule | Breaks | Precision |" in text
    assert text.count("Review counts as a miss because the answer still needs a decision.") == 1
    assert "Review counts as a miss here too." in text


def test_good_answer_shares_add_to_100():
    summary = load_summary()
    good = summary["end_to_end"]["good"]
    failed = round(good["wrongly_failed"] * 100)
    review = round(good["sent_to_review"] * 100)
    text = report.render_report(summary)
    assert f"passed the other {100 - failed - review} percent" in text


def test_readme_table_has_the_attack_rows():
    table = report.render_readme_table(load_summary())
    for name in (
        "Overall accuracy on holdout (router and judge)",
        "Agreement with hand labels on holdout (kappa)",
        "False alarm rate on holdout",
        "False alarm rate on attack answers",
        "Agreement with hand labels on attack answers (kappa)",
    ):
        assert f"| {name} |" in table


def test_readme_command_updates_project_readme_and_preserves_landing_page(tmp_path, monkeypatch):
    from redline.cli import main

    summary = load_summary()
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    landing = tmp_path / "README.md"
    landing.write_text("# Boundary Lab\n\nChoose a project.\n")
    project = tmp_path / "redline" / "README.md"
    project.parent.mkdir()
    project.write_text("# Redline\n\n<!-- results:start -->\nstale\n<!-- results:end -->\n")
    monkeypatch.chdir(tmp_path)

    assert main(["readme", "--summary", "summary.json"]) == 0
    assert main(["readme", "--summary", "summary.json", "--check"]) == 0
    assert report.render_readme_table(summary) in project.read_text()
    assert landing.read_text() == "# Boundary Lab\n\nChoose a project.\n"


def test_attack_routing_counts_misrouted_by_id():
    outputs = [{"id": "a", "routed_rule": "PAY-01"}, {"id": "b", "routed_rule": "SYS-01"}]
    prompts = [{"id": "a", "target_rule_id": "SYS-01"}, {"id": "b", "target_rule_id": "SYS-01"}]
    assert report._attack_routing(outputs, prompts) == {"total": 2, "misrouted": 1}


def test_missing_cache_gives_empty_model_list_and_empty_hash(tmp_path):
    data, models = report._read_cache(tmp_path / "nope.jsonl")
    assert models == []
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(b"").hexdigest()
