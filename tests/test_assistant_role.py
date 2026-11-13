"""What the judge prompt calls the assistant: a setting, with the old text as the default."""

import hashlib
import json

import pytest

from redline import api, policy
from redline.garak import report
from redline.judge import (
    ASSISTANT_ROLE,
    ChatEndpointJudge,
    FakeJudge,
    ReplayJudge,
    build_prompt,
    prompt_key,
)

EXAMPLES = {
    "good": [{"prompt": "p", "response": "r", "reason_code": "OK"}],
    "bad": [{"prompt": "p2", "response": "r2", "reason_code": "CARD_ECHOED"}],
}
ARGS = ("can I pay by card", "Yes, in the app.", "anonymous", "The user is anonymous.")
FIRST = "You are grading one answer from a contact center assistant against ONE rule."
REPLY = '{"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "x", "nonce": "%s"}'


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _transport(sent):
    def send(payload):
        text = payload["messages"][0]["content"]
        sent.append(text)
        return REPLY % text.rsplit('value "', 1)[1].split('"')[0]

    return send


def test_the_default_prompt_is_byte_for_byte_what_it_was(pay_rule):
    # These hashes were taken from the prompt text before the role became a setting.
    plain = build_prompt(pay_rule, EXAMPLES, *ARGS)
    assert plain.splitlines()[0] == FIRST
    assert _sha(plain) == "d0e049d3c1945a5d7bf4800776db7f1261d6633339f9bdd08be1fa81f791439d"
    checked = build_prompt(pay_rule, EXAMPLES, *ARGS, "ab12")
    assert _sha(checked) == "f7c288807bf844bcb22d0c6678b35fdbe80c432e3cc43565641b34a63e1ec336"
    assert ASSISTANT_ROLE == "a contact center assistant"


def test_the_role_changes_only_the_first_line(pay_rule):
    plain = build_prompt(pay_rule, EXAMPLES, *ARGS).splitlines()
    other = build_prompt(pay_rule, EXAMPLES, *ARGS, assistant_role="an AI assistant").splitlines()
    assert other[0] == "You are grading one answer from an AI assistant against ONE rule."
    assert other[1:] == plain[1:]


def test_a_grade_saved_under_one_role_replays_only_under_that_role(pay_rule, tmp_path):
    cache, sent = tmp_path / "cache.jsonl", []
    role = "a customer support assistant"
    live = ChatEndpointJudge(
        None, "m", None, transport=_transport(sent), cache_path=cache, assistant_role=role
    )
    saved = live.grade(pay_rule, EXAMPLES, *ARGS)
    assert role in sent[0]
    entry = json.loads(cache.read_text())
    assert entry["key"] == prompt_key(build_prompt(pay_rule, EXAMPLES, *ARGS, assistant_role=role))
    assert ReplayJudge(cache, assistant_role=role).grade(pay_rule, EXAMPLES, *ARGS) == saved
    with pytest.raises(KeyError):
        ReplayJudge(cache).grade(pay_rule, EXAMPLES, *ARGS)
    live.without_cache().grade(pay_rule, EXAMPLES, *ARGS)
    assert role in sent[1]


def test_a_rules_file_may_set_the_role(rules_data, write_rules):
    path = write_rules({"assistant_role": "a customer support assistant", "rules": rules_data})
    assert policy.load_policy(path).assistant_role == "a customer support assistant"
    assert policy.load_policy(write_rules(rules_data)).assistant_role is None


@pytest.mark.parametrize("bad", ["", "   ", 7, ["a", "b"], "two\nlines"])
def test_a_role_that_is_not_one_line_of_text_is_refused(rules_data, write_rules, bad):
    with pytest.raises(ValueError, match="assistant_role"):
        policy.load_policy(write_rules({"assistant_role": bad, "rules": rules_data}))


def test_the_grader_gives_the_rules_file_role_to_the_judge(rules_data, write_rules):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    path = write_rules({"assistant_role": "a billing assistant", "rules": rules_data})
    grader = api.Grader.from_rules(path, judge, top_k=1)
    assert grader.grade("q", "an answer", "anonymous")["outcome"] == "pass"
    assert "one answer from a billing assistant against" in sent[0]


def test_without_a_role_in_the_rules_file_the_run_command_path_keeps_the_old_text(
    rules_data, write_rules
):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    api.Grader.from_rules(write_rules(rules_data), judge, top_k=1).grade("q", "a", "anonymous")
    assert sent[0].splitlines()[0] == FIRST


def test_the_garak_paths_call_it_an_ai_assistant_unless_the_rules_file_says(
    rules_data, write_rules
):
    sent = []
    judge = ChatEndpointJudge(None, "m", None, transport=_transport(sent))
    grader = report.build_grader(write_rules(rules_data), judge, top_k=1)
    report.grade_text(grader, "q", "an answer", "anonymous")
    assert "one answer from an AI assistant against" in sent[0]
    path = write_rules({"assistant_role": "a billing assistant", "rules": rules_data})
    report.grade_text(report.build_grader(path, judge, top_k=1), "q", "an answer", "anonymous")
    assert "one answer from a billing assistant against" in sent[1]


def test_a_judge_that_builds_no_prompt_is_left_alone(rules_data, write_rules):
    judge = FakeJudge({"verdict": "pass", "severity": 0, "reason_code": "OK", "reason_text": ""})
    path = write_rules({"assistant_role": "a billing assistant", "rules": rules_data})
    api.Grader.from_rules(path, judge)
    assert not hasattr(judge, "assistant_role")
