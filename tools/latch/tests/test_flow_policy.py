"""The [flow] table and tool labels in the policy."""

import pytest

from boundary_latch.errors import ConfigError
from boundary_latch.policy import parse_policy

READ = """
[tools.get_issue]
require_permission = "mcp:use"
approve_permission = "mcp:approve"
{labels}
"""


def flow(mode: str, labels: str = 'labels = ["untrusted"]') -> str:
    return f'[flow]\nall_three = "{mode}"\n' + READ.format(labels=labels)


def test_the_rule_is_off_by_default_and_labels_are_unset():
    policy = parse_policy(READ.format(labels=""))
    assert policy.flow.all_three == "allow"
    assert policy.tools["get_issue"].labels is None


@pytest.mark.parametrize("mode", ["allow", "approve", "deny"])
def test_each_mode_parses(mode):
    policy = parse_policy(flow(mode))
    assert policy.flow.all_three == mode
    assert policy.tools["get_issue"].labels == ["untrusted"]


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ConfigError):
        parse_policy(flow("warn"))


def test_an_unknown_label_is_rejected():
    with pytest.raises(ConfigError):
        parse_policy(flow("deny", 'labels = ["secret"]'))


def test_a_repeated_label_is_rejected():
    with pytest.raises(ConfigError, match="distinct"):
        parse_policy(flow("deny", 'labels = ["untrusted", "untrusted"]'))


@pytest.mark.parametrize("mode", ["approve", "deny"])
def test_with_the_rule_on_a_tool_without_labels_is_rejected_by_name(mode):
    with pytest.raises(ConfigError, match="get_issue"):
        parse_policy(flow(mode, ""))


def test_with_the_rule_on_an_empty_label_list_is_fine():
    policy = parse_policy(flow("deny", "labels = []"))
    assert policy.tools["get_issue"].labels == []


def test_with_the_rule_off_labels_may_be_missing():
    assert parse_policy(flow("allow", "")).tools["get_issue"].labels is None


def test_approve_mode_needs_approve_permission_on_a_labelled_tool():
    text = '[flow]\nall_three = "approve"\n[tools.t]\nrequire_permission = "a"\nlabels = ["sensitive"]\n'
    with pytest.raises(ConfigError, match="approve_permission"):
        parse_policy(text)


def test_approve_mode_does_not_need_approve_permission_on_an_unlabelled_tool():
    text = '[flow]\nall_three = "approve"\n[tools.t]\nrequire_permission = "a"\nlabels = []\n'
    assert parse_policy(text).tools["t"].labels == []


def test_deny_mode_does_not_need_approve_permission():
    text = '[flow]\nall_three = "deny"\n[tools.t]\nrequire_permission = "a"\nlabels = ["external"]\n'
    assert parse_policy(text).flow.all_three == "deny"


def test_the_digest_covers_the_mode_and_the_labels():
    digests = {
        parse_policy(READ.format(labels="")).digest,
        parse_policy(flow("deny", "labels = []")).digest,
        parse_policy(flow("deny")).digest,
        parse_policy(flow("approve")).digest,
        parse_policy(flow("allow")).digest,
        parse_policy(flow("allow", "labels = []")).digest,
    }
    assert len(digests) == 6


def test_the_digest_of_a_policy_without_flow_keys_did_not_change():
    # The digest a policy had before the flow rule existed, so stored operations still match.
    policy = parse_policy('[tools.t]\nrequire_permission = "a"\n')
    assert policy.digest == "608900452d5896795723488bfc755591134bc7afb2e4f99a9332a3ee0aa92704"
