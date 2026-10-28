from redline import gate


def test_anonymous_on_acct_is_not_allowed(acct_rule):
    stance = gate.check(acct_rule, "anonymous")
    assert stance.allowed is False
    assert stance.reason_code == "NOT_ALLOWED_FOR_USER"


def test_verified_on_acct_is_allowed(acct_rule):
    stance = gate.check(acct_rule, "verified")
    assert stance.allowed is True
    assert stance.reason_code == "OK"
