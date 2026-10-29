import pytest

from boundary_latch.errors import ConfigError
from boundary_latch.policy import load_policy, parse_policy

VALID = """
[limits]
max_actions_per_run = 5

[tools.send_message]
require_permission = "messages:send"
approve_permission = "messages:approve"
recipient_field = "to"
allowed_recipients = ["help@northwind.example"]
approval.mode = "always"
"""

REFUND = """
[tools.refund]
require_permission = "refunds:create"
approve_permission = "refunds:approve"
approval.mode = "over_amount"
approval.amount_field = "amount_minor"
approval.threshold_minor = {threshold}
"""


def test_valid_policy_parses():
    policy = parse_policy(VALID)
    tool = policy.tools["send_message"]
    assert tool.require_permission == "messages:send"
    assert tool.allowed_recipients == ["help@northwind.example"]
    assert tool.approval.mode == "always"
    assert policy.limits.max_actions_per_run == 5
    assert policy.limits.run_lifetime_seconds == 900


def test_unknown_key_is_rejected():
    with pytest.raises(ConfigError):
        parse_policy('[tools.send_message]\nrequire_permission = "messages:send"\nfoo = 1\n')


def test_over_amount_without_threshold_is_rejected():
    text = (
        "[tools.refund]\n"
        'require_permission = "refunds:create"\n'
        'approve_permission = "refunds:approve"\n'
        'approval.mode = "over_amount"\n'
        'approval.amount_field = "amount_minor"\n'
    )
    with pytest.raises(ConfigError):
        parse_policy(text)


def test_recipient_field_without_list_is_rejected():
    text = '[tools.send_message]\nrequire_permission = "messages:send"\nrecipient_field = "to"\n'
    with pytest.raises(ConfigError):
        parse_policy(text)


def test_approval_without_approve_permission_is_rejected():
    text = '[tools.send_message]\nrequire_permission = "messages:send"\napproval.mode = "always"\n'
    with pytest.raises(ConfigError):
        parse_policy(text)


def test_bad_toml_is_rejected():
    with pytest.raises(ConfigError):
        parse_policy("[tools.send_message\n")


def test_digest_tracks_contents():
    low = parse_policy(REFUND.format(threshold=5000))
    high = parse_policy(REFUND.format(threshold=6000))
    assert len(low.digest) == 64
    assert all(c in "0123456789abcdef" for c in low.digest)
    assert low.digest != high.digest
    assert low.digest == parse_policy(REFUND.format(threshold=5000)).digest


def test_load_policy_reads_a_file(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(VALID)
    assert load_policy(path).digest == parse_policy(VALID).digest
