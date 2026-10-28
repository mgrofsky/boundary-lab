import pytest

from guardrail_eval import policy


def test_load_finds_acct_rule(rules_data, write_rules):
    rules = policy.load(write_rules(rules_data))
    assert "ACCT-01" in rules
    assert rules["ACCT-01"].allowed_for == ["verified"]


def test_missing_reason_codes_raises(rules_data, write_rules):
    del rules_data[0]["reason_codes"]
    with pytest.raises(ValueError, match="ACCT-01"):
        policy.load(write_rules(rules_data))


def test_duplicate_rule_id_raises(rules_data, write_rules):
    rules_data[1]["rule_id"] = "ACCT-01"
    with pytest.raises(ValueError, match="ACCT-01"):
        policy.load(write_rules(rules_data))
