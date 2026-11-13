"""The rules files shipped with the integration example must keep loading."""

from pathlib import Path

import pytest
import yaml

from redline import Grader
from redline.judge import FakeJudge
from redline.policy import load_policy, load_policy_data, validate_policy_data

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


@pytest.mark.parametrize(
    "name", ["rules-llm-endpoint-starter.yaml", "rules-aegisshield-outputs.yaml"]
)
def test_example_rules_pass_the_check_an_app_runs_on_save(name):
    """The app stores rules as fields, so the files must also be good as data."""
    data = yaml.safe_load((FOLDER / name).read_text())
    assert validate_policy_data(data) == []
    assert load_policy_data(data) == load_policy(FOLDER / name)
    judge = FakeJudge(PASS)
    user_type = load_policy_data(data).user_types[0]
    row = Grader.from_rules(data, judge).grade("a question", "a plain answer", user_type)
    assert row["outcome"] == "pass"


def test_the_outputs_rules_carry_the_notes_from_the_apps_team():
    text = (FOLDER / "rules-aegisshield-outputs.yaml").read_text()
    assert "switched off by default" in text
    assert "due to be rewritten" in text
