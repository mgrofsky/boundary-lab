"""Rules passed as data, and the check that lists every problem without raising."""

from pathlib import Path

import pytest
import yaml

from redline import Grader, policy
from redline.judge import FakeJudge

ROOT = Path(__file__).parent.parent
PASS = {"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "ok"}
SHIPPED = [
    "policy/rules.yaml",
    "docs/integrations/aegisshield/rules-llm-endpoint-starter.yaml",
    "docs/integrations/aegisshield/rules-aegisshield-outputs.yaml",
]


def _codes(data):
    return [(p["where"], p["code"]) for p in policy.validate_policy_data(data)]


def test_data_loads_to_the_same_policy_as_the_file(rules_data, write_rules):
    assert policy.load_policy_data(rules_data) == policy.load_policy(write_rules(rules_data))


def test_a_mapping_carries_user_types_and_the_assistant_role(rules_data):
    rules_data[0]["allowed_for"] = ["agent"]
    rules_data[1]["allowed_for"] = ["guest", "agent"]
    data = {"rules": rules_data, "user_types": ["guest", "agent"], "assistant_role": "a bot"}
    loaded = policy.load_policy_data(data)
    assert loaded.user_types == ["guest", "agent"]
    assert loaded.assistant_role == "a bot"
    assert list(loaded.rules) == ["ACCT-01", "PAY-01"]


def test_load_policy_data_does_not_change_the_data_it_is_given(rules_data):
    before = [dict(r) for r in rules_data]
    policy.load_policy_data(rules_data)
    assert rules_data == before


def test_an_error_names_the_source_and_the_rule(rules_data):
    del rules_data[1]["reason_codes"]
    with pytest.raises(ValueError, match="^tenant 7: PAY-01: missing field reason_codes"):
        policy.load_policy_data(rules_data, source="tenant 7")
    with pytest.raises(ValueError, match="^<data>: "):
        policy.load_policy_data(rules_data)


def test_load_policy_names_the_file_in_an_error(rules_data, write_rules):
    rules_data[0]["allowed_for"] = ["admin"]
    path = write_rules(rules_data)
    with pytest.raises(ValueError, match="rules.yaml: .*ACCT-01.*unknown user type.*admin"):
        policy.load_policy(path)


@pytest.mark.parametrize("path", SHIPPED)
def test_the_shipped_rules_files_have_no_problems(path):
    data = yaml.safe_load((ROOT / path).read_text())
    assert policy.validate_policy_data(data) == []


def test_good_data_has_no_problems(rules_data):
    assert policy.validate_policy_data(rules_data) == []
    assert policy.validate_policy_data({"rules": rules_data, "user_types": ["verified"]}) != []


def test_every_problem_is_reported_in_one_pass(rules_data):
    rules_data[0]["name"] = ""
    rules_data[0]["allowed_for"] = ["admin"]
    rules_data[1]["rule_id"] = "PAY 01"
    rules_data[1]["reason_codes"] = []
    rules_data.append(dict(rules_data[0], name="again"))
    assert _codes(rules_data) == [
        ("rules[0].name", "empty"),
        ("rules[0].allowed_for", "unknown_user_type"),
        ("rules[1].rule_id", "bad_characters"),
        ("rules[1].reason_codes", "empty"),
        ("rules[2].rule_id", "duplicate"),
        ("rules[2].allowed_for", "unknown_user_type"),
    ]


def test_each_problem_has_where_a_code_and_a_message(rules_data):
    rules_data[0]["rule"] = 7
    [problem] = policy.validate_policy_data(rules_data)
    assert sorted(problem) == ["code", "message", "where"]
    assert problem["code"] in policy.PROBLEM_CODES
    assert "ACCT-01" in problem["message"]
    assert "rule" in problem["message"]


@pytest.mark.parametrize(
    ("change", "where", "code"),
    [
        ({"rule_id": 7}, "rules[0].rule_id", "wrong_type"),
        ({"rule_id": ["a"]}, "rules[0].rule_id", "wrong_type"),
        ({"rule_id": ""}, "rules[0].rule_id", "missing"),
        ({"rule_id": "A/1"}, "rules[0].rule_id", "bad_characters"),
        ({"name": None}, "rules[0].name", "wrong_type"),
        ({"rule": "  "}, "rules[0].rule", "empty"),
        ({"plain_summary": ["x"]}, "rules[0].plain_summary", "wrong_type"),
        ({"allowed_for": "verified"}, "rules[0].allowed_for", "wrong_type"),
        ({"allowed_for": [3]}, "rules[0].allowed_for[0]", "wrong_type"),
        ({"reason_codes": "OK"}, "rules[0].reason_codes", "wrong_type"),
        ({"reason_codes": None}, "rules[0].reason_codes", "empty"),
        ({"reason_codes": ["OK", "NOT OK"]}, "rules[0].reason_codes[1]", "bad_characters"),
        ({"reason_codes": ["OK", ""]}, "rules[0].reason_codes[1]", "empty"),
        ({"reason_codes": [{"a": 1}]}, "rules[0].reason_codes[0]", "wrong_type"),
    ],
)
def test_one_bad_field_gives_one_problem(rules_data, change, where, code):
    rules_data[0].update(change)
    assert _codes(rules_data) == [(where, code)]
    with pytest.raises((TypeError, ValueError)):
        policy.load_policy_data(rules_data)


def test_a_missing_field_is_named_once_per_field(rules_data):
    del rules_data[0]["name"], rules_data[0]["rule"]
    assert _codes(rules_data) == [("rules[0].name", "missing"), ("rules[0].rule", "missing")]


def test_an_empty_allowed_for_means_no_one_and_is_not_a_problem(rules_data):
    rules_data[0]["allowed_for"] = []
    rules_data[1]["allowed_for"] = None
    assert policy.validate_policy_data(rules_data) == []
    assert policy.load_policy_data(rules_data).rules["PAY-01"].allowed_for == []


@pytest.mark.parametrize(
    ("data", "where", "code"),
    [
        (None, "rules", "wrong_type"),
        ("rules", "rules", "wrong_type"),
        ({"user_types": ["a"]}, "rules", "wrong_type"),
        ({"rules": {"a": 1}}, "rules", "wrong_type"),
        (["not a rule"], "rules[0]", "wrong_type"),
        ({"rules": [], "user_types": []}, "user_types", "empty"),
        ({"rules": [], "user_types": "guest"}, "user_types", "wrong_type"),
        ({"rules": [], "user_types": ["a", "a"]}, "user_types[1]", "duplicate"),
        ({"rules": [], "user_types": ["a", 3]}, "user_types[1]", "wrong_type"),
        ({"rules": [], "user_types": ["a", {}]}, "user_types[1]", "wrong_type"),
        ({"rules": [], "user_types": ["a", " "]}, "user_types[1]", "empty"),
        ({"rules": [], "assistant_role": "two\nlines"}, "assistant_role", "not_one_line"),
        ({"rules": [], "assistant_role": 4}, "assistant_role", "wrong_type"),
        ({"rules": [], "assistant_role": ""}, "assistant_role", "empty"),
    ],
)
def test_problems_outside_the_rules(data, where, code):
    assert _codes(data) == [(where, code)]
    with pytest.raises((TypeError, ValueError)):
        policy.load_policy_data(data)


def test_the_old_error_types_stay(rules_data):
    with pytest.raises(TypeError, match="expected a list of rules"):
        policy.load_policy_data({"nothing": 1})
    with pytest.raises(TypeError, match="rule 1 is not a mapping"):
        policy.load_policy_data(["x"])
    with pytest.raises(ValueError, match="rule 1 has no rule_id"):
        policy.load_policy_data([{"name": "x"}])


def test_allowed_for_is_not_checked_when_user_types_is_bad(rules_data):
    data = {"rules": rules_data, "user_types": "guest"}
    assert _codes(data) == [("user_types", "wrong_type")]


def test_validate_reads_no_file_and_calls_no_judge(rules_data, monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("read a file"))
    assert policy.validate_policy_data(rules_data) == []


def test_from_rules_takes_a_path_a_policy_or_data(rules_data, write_rules):
    path = write_rules(rules_data)
    for rules in (path, str(path), policy.load_policy(path), rules_data, {"rules": rules_data}):
        judge = FakeJudge(PASS)
        row = Grader.from_rules(rules, judge).grade("q", "a plain answer", "anonymous")
        assert row["outcome"] == "pass"
        assert len(judge.calls) == 2


def test_from_rules_with_data_that_has_no_rules_says_so():
    with pytest.raises(ValueError, match="<data>: the rules file has no rules"):
        Grader.from_rules([], FakeJudge(PASS))
