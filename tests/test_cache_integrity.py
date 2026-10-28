import json
from pathlib import Path

import pytest

from guardrail_eval import policy
from guardrail_eval.judge import parse_answer

CACHE = Path("data/judge_cache.jsonl")


def test_saved_judge_answers_match_their_raw_text():
    if not CACHE.exists():
        pytest.skip("no judge cache file")
    rules = policy.load("policy/rules.yaml")
    for number, line in enumerate(CACHE.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        entry = json.loads(line)
        assert "rule_id" in entry, f"line {number} has no rule_id"
        saved = entry["answer"]
        fresh = parse_answer(saved["raw"], rules[entry["rule_id"]])
        assert fresh["verdict"] == saved["verdict"], f"line {number}"
        assert fresh["reason_code"] == saved["reason_code"], f"line {number}"
