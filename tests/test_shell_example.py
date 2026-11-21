"""The second example: rules for a chat model that plays a Linux shell."""

import hashlib
import importlib.util
import ipaddress
import json
import re
from pathlib import Path

import pytest
import yaml

from redline import cli, policy
from redline.api import read_train
from redline.judge import read_saved_grades

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "shell"
RULE_IDS = ["TURN-01", "ROLE-01", "SYS-01", "FMT-01", "STATE-01"]
# The ranges set aside for documentation. No other address may appear in the example.
DOC_RANGES = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]
ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def rows(name):
    return [json.loads(line) for line in (EXAMPLE / name).read_text().splitlines() if line]


def test_the_rules_load_and_have_no_problems():
    data = yaml.safe_load((EXAMPLE / "rules.yaml").read_text())
    assert policy.validate_policy_data(data) == []
    loaded = policy.load_policy(EXAMPLE / "rules.yaml")
    assert list(loaded.rules) == RULE_IDS
    assert loaded.user_types == ["visitor"]
    assert loaded.assistant_role == "a chat model that plays a Linux shell"


@pytest.mark.parametrize("name, per_label", [("train.jsonl", 8), ("holdout.jsonl", 5)])
def test_each_data_file_has_the_right_shape_and_labels(name, per_label):
    rules = policy.load(EXAMPLE / "rules.yaml")
    records = read_train(EXAMPLE / name, rules)
    assert len({r["id"] for r in records}) == len(records)
    for r in records:
        assert r["user_type"] == "visitor", r["id"]
        assert r["label"] in ("good", "bad"), r["id"]
        assert isinstance(r["prompt"], str) and r["prompt"], r["id"]
        assert isinstance(r["response"], str), r["id"]
        if r["label"] == "good":
            assert r["reason_code"] == "OK", r["id"]
        else:
            assert r["reason_code"] in rules[r["rule_id"]].reason_codes, r["id"]
            assert r["reason_code"] != "OK", r["id"]
    for rule_id in RULE_IDS:
        for label in ("good", "bad"):
            count = sum(1 for r in records if r["rule_id"] == rule_id and r["label"] == label)
            assert count == per_label, (rule_id, label)


def test_holdout_and_train_share_no_answer():
    train = {(r["prompt"], r["response"]) for r in rows("train.jsonl")}
    assert not train & {(r["prompt"], r["response"]) for r in rows("holdout.jsonl")}


def test_only_documentation_addresses_appear():
    for path in EXAMPLE.rglob("*"):
        if path.is_file() and path.suffix in (".jsonl", ".txt", ".yaml", ".md", ".json"):
            for found in ADDRESS.findall(path.read_text()):
                address = ipaddress.ip_address(found)
                if address.is_loopback or address.is_unspecified:
                    continue
                assert any(address in net for net in DOC_RANGES), (path.name, found)


def test_every_saved_grade_reads_and_names_one_model():
    saved = read_saved_grades(EXAMPLE / "judge_cache.jsonl")
    assert {entry["model"] for entry in saved} == {"us.anthropic.claude-haiku-5-5"}
    assert {entry["rule_id"] for entry in saved} == set(RULE_IDS)


def same_rows(saved, fresh):
    assert len(saved) == len(fresh)
    for old, new in zip(saved, fresh, strict=True):
        assert old["id"] == new["id"]
        for key in ("routed_rule", "outcome", "decided_by", "reason_code", "reason_text"):
            assert old[key] == new[key], (old["id"], key)


@pytest.mark.slow
@pytest.mark.parametrize("folder, more", [("results", ["--top-k", "5"]), ("results/top1", [])])
def test_a_replay_gives_the_saved_results(tmp_path, folder, more):
    args = [
        "run",
        "--rules", str(EXAMPLE / "rules.yaml"),
        "--train", str(EXAMPLE / "train.jsonl"),
        "--cache", str(EXAMPLE / "judge_cache.jsonl"),
        "--data", str(EXAMPLE / "holdout.jsonl"),
        "--judge", "replay",
        "--out", str(tmp_path),
        *more,
    ]  # fmt: skip
    assert cli.main(args) == 0
    fresh = cli.read_jsonl(tmp_path / "holdout_outputs.jsonl")
    same_rows(cli.read_jsonl(EXAMPLE / folder / "holdout_outputs.jsonl"), fresh)
    summary = json.loads((EXAMPLE / folder / "summary.json").read_text())
    cache = (EXAMPLE / "judge_cache.jsonl").read_bytes()
    assert summary["judge_cache_sha256"] == hashlib.sha256(cache).hexdigest()


def test_the_saved_summary_passes_the_gate_it_is_documented_with():
    summary = EXAMPLE / "results" / "summary.json"
    args = ["gate", "--summary", str(summary), "--min-recall", "0.8", "--max-false-alarm", "0.1"]
    assert cli.main(args) == 0


@pytest.mark.slow
def test_the_shell_example_holds_back_the_second_round_and_shows_the_rest(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "guard_shell", ROOT / "docs" / "examples" / "guard_shell.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    lines = []
    record = tmp_path / "guard.jsonl"
    decisions = example.shell(example.build_guard(record=record), show=lines.append)
    assert [d.action for d in decisions] == ["show", "hold", "show"]
    assert decisions[1].rule_id == "TURN-01"
    assert decisions[1].reason_code == "EXTRA_ROUND"
    assert "root@kestrel:/var/www# ls\nhtml\nroot@kestrel:/var/www# " not in lines
    assert lines[lines.index("[held back: TURN-01 EXTRA_ROUND]") + 1] == "root@kestrel:~# "
    logged = [json.loads(line) for line in record.read_text().splitlines()]
    assert [line["id"] for line in logged] == ["turn-1", "turn-2", "turn-3"]


@pytest.mark.parametrize(
    "message, expected",
    [
        ("root@kestrel:~# cd /var/www", "root@kestrel:~# "),
        ("deploy@kestrel:/tmp$ ls", "deploy@kestrel:/tmp$ "),
        ("uname -a", ""),
    ],
)
def test_the_fallback_is_the_prompt_the_visitor_already_saw(message, expected):
    spec = importlib.util.spec_from_file_location(
        "guard_shell", ROOT / "docs" / "examples" / "guard_shell.py"
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    assert example.fallback(message) == expected
