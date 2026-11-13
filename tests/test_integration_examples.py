"""The rules files shipped with the integration example must keep loading."""

from pathlib import Path

import pytest

from redline import Grader
from redline.judge import FakeJudge
from redline.policy import load_policy

FOLDER = Path(__file__).parent.parent / "docs" / "integrations" / "aegisshield"
PASS = {"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "ok"}


@pytest.mark.parametrize(
    ("name", "user_type", "rule_count"),
    [
        ("rules-llm-endpoint-starter.yaml", "anonymous", 7),
        ("rules-aegisshield-outputs.yaml", "analyst", 6),
    ],
)
def test_example_rules_load_and_grade(name, user_type, rule_count):
    path = FOLDER / name
    policy = load_policy(path)
    assert len(policy.rules) == rule_count
    assert user_type in policy.user_types
    assert policy.assistant_role
    judge = FakeJudge(PASS)
    row = Grader.from_rules(path, judge).grade("a question", "a plain answer", user_type)
    assert row["outcome"] == "pass"
    assert row["mode"] == "rules_only"
    assert len(judge.calls) == rule_count
