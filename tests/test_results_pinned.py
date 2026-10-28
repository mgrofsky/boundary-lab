import hashlib
import json
from pathlib import Path

import pytest

from guardrail_eval import cli, metrics, report

TOLERANCE = 0.001


def close(published, fresh, where):
    """Walk the published numbers and check each one against the fresh value."""
    if isinstance(published, dict):
        for key, value in published.items():
            close(value, fresh[key], f"{where}.{key}")
    else:
        assert fresh == pytest.approx(published, abs=TOLERANCE), where


@pytest.mark.slow
def test_published_numbers_match_a_replay_of_the_saved_judge_answers(tmp_path):
    cli.main(["run", "--data", "data/holdout.jsonl", "--judge", "replay", "--out", str(tmp_path)])
    outputs = cli.read_jsonl(tmp_path / "holdout_outputs.jsonl")
    labels = cli.read_jsonl("data/holdout.jsonl")
    published = json.loads(Path("results/summary.json").read_text())

    confusion = metrics.router_confusion(outputs, labels)
    router = {
        "holdout_accuracy": metrics.router_accuracy(confusion),
        "abstain_rate": confusion["abstain_rate"],
    }
    judge = {
        "accuracy": metrics.accuracy(outputs, labels),
        "cohen_kappa": metrics.cohen_kappa(outputs, labels),
        "per_rule": metrics.per_rule(outputs, labels),
    }

    close({k: published["router"][k] for k in router}, router, "router")
    close(published["judge"]["holdout"], judge, "judge.holdout")


def same_records(tracked, fresh):
    """Compare output records field by field. Floats get a tolerance, since the embedding
    model gives slightly different similarity scores on different machines."""
    assert len(tracked) == len(fresh)
    for old, new in zip(tracked, fresh):
        assert old.keys() == new.keys(), old.get("id")
        for field, value in old.items():
            if isinstance(value, float):
                assert new[field] == pytest.approx(value, abs=TOLERANCE), f"{old['id']}.{field}"
            else:
                assert new[field] == value, f"{old['id']}.{field}"


# Output records carry no timestamp or timing field, so every field is compared.
@pytest.mark.slow
def test_published_attack_numbers_and_outputs_match_a_replay(tmp_path):
    published = json.loads(Path("results/summary.json").read_text())
    cache = Path("data/judge_cache.jsonl")
    assert published["judge_cache_sha256"] == hashlib.sha256(cache.read_bytes()).hexdigest()

    holdout_dir = tmp_path / "holdout"
    attack_dir = tmp_path / "attack"
    cli.main(["run", "--data", "data/holdout.jsonl", "--judge", "replay", "--out", str(holdout_dir)])
    cli.main(
        ["run", "--data", "data/runs/attack_responses.jsonl", "--judge", "replay", "--out", str(attack_dir)]
    )
    holdout_out = cli.read_jsonl(holdout_dir / "holdout_outputs.jsonl")
    attack_out = cli.read_jsonl(attack_dir / "attack_responses_outputs.jsonl")
    attack_labels = cli.read_jsonl("data/runs/attack_labels.jsonl")
    attacks = cli.read_jsonl("data/attacks.jsonl")

    same_records(cli.read_jsonl("results/holdout_outputs.jsonl"), holdout_out)
    same_records(cli.read_jsonl("results/attack_responses_outputs.jsonl"), attack_out)

    judge = {
        "accuracy": metrics.accuracy(attack_out, attack_labels),
        "cohen_kappa": metrics.cohen_kappa(attack_out, attack_labels),
        "per_rule": metrics.per_rule(attack_out, attack_labels),
    }
    close(published["judge"]["attack"], judge, "judge.attack")
    close(published["attack"], metrics.attack_summary(attack_out, attack_labels, attacks), "attack")
    close(
        published["attack_end_to_end"],
        metrics.end_to_end(attack_out, attack_labels),
        "attack_end_to_end",
    )
    close(published["attack_routing"], report._attack_routing(attack_out, attacks), "attack_routing")
