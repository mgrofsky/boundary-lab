import pytest

from redline import policy


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


def test_load_policy_defaults_to_the_two_user_types(rules_data, write_rules):
    loaded = policy.load_policy(write_rules(rules_data))
    assert loaded.user_types == ["anonymous", "verified"]
    assert set(loaded.rules) == {"ACCT-01", "PAY-01"}


def test_user_types_come_from_the_rules_file(rules_data, write_rules):
    rules_data[0]["allowed_for"] = ["member", "staff"]
    rules_data[1]["allowed_for"] = ["guest"]
    path = write_rules({"user_types": ["guest", "member", "staff"], "rules": rules_data})
    loaded = policy.load_policy(path)
    assert loaded.user_types == ["guest", "member", "staff"]
    assert loaded.rules["ACCT-01"].allowed_for == ["member", "staff"]
    assert policy.load(path) == loaded.rules


def test_allowed_for_is_checked_against_the_declared_user_types(rules_data, write_rules):
    path = write_rules({"user_types": ["guest", "member"], "rules": rules_data})
    with pytest.raises(ValueError, match="ACCT-01: unknown user type in allowed_for: verified"):
        policy.load_policy(path)


@pytest.mark.parametrize("bad", [[], "guest", ["guest", "guest"], ["guest", ""], [3]])
def test_a_bad_user_types_list_raises(rules_data, write_rules, bad):
    with pytest.raises(ValueError, match="user_types"):
        policy.load_policy(write_rules({"user_types": bad, "rules": rules_data}))
