import json
from pathlib import Path

from guardrail_eval import policy

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RULES = policy.load(ROOT / "policy" / "rules.yaml")


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


TRAIN = read(DATA / "train.jsonl")
HOLDOUT = read(DATA / "holdout.jsonl")
ATTACKS = read(DATA / "attacks.jsonl")


def labeled_files():
    """Train and holdout, plus the attack labels once the attack run has been labeled."""
    files = [DATA / "train.jsonl", DATA / "holdout.jsonl"]
    attack_labels = DATA / "runs" / "attack_labels.jsonl"
    if attack_labels.is_file():
        files.append(attack_labels)
    return files


def test_no_response_in_both_train_and_holdout():
    shared = {r["response"] for r in TRAIN} & {r["response"] for r in HOLDOUT}
    assert shared == set()


def test_every_rule_has_good_and_bad_train_records():
    for rule_id in RULES:
        labels = {r["label"] for r in TRAIN if r["rule_id"] == rule_id}
        assert {"good", "bad"} <= labels, rule_id


def test_every_rule_id_is_known():
    known = set(RULES) | {"none"}
    for path in labeled_files():
        unknown = {r["rule_id"] for r in read(path)} - known
        assert unknown == set(), f"{path.name}: {sorted(unknown)}"


def test_every_attack_targets_a_known_rule():
    unknown = {a["target_rule_id"] for a in ATTACKS} - set(RULES)
    assert unknown == set()
