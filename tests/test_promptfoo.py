"""The promptfoo files stay in step with the data and the rig."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import yaml

from redline import policy
from redline.judge import FakeJudge
from redline.router import Router

ROOT = Path(__file__).resolve().parent.parent
PF = ROOT / "promptfoo"
ATTACKS = ROOT / "data" / "attacks.jsonl"
RULES = policy.load(ROOT / "policy" / "rules.yaml")


def load(name):
    """Import a script from the promptfoo folder, which is not a package."""
    spec = importlib.util.spec_from_file_location(name, PF / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generate_tests = load("generate_tests")
judge_assert = load("judge_assert")
replay_provider = load("replay_provider")


class FixedEmbedder:
    """Returns the same vector for any text, so routing is predictable without a model."""

    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=np.float32)

    def encode(self, texts):
        return np.vstack([self.vec for _ in texts])


def rig_with(judge, vec=(1.0, 0.0)):
    router = Router.from_centers({"PAY-01": [1.0, 0.0], "none": [0.0, 1.0]}, threshold=0.9)
    router.embedder = FixedEmbedder(vec)
    return judge_assert.Rig(RULES, router, judge, {})


def test_every_config_file_points_at_files_that_exist():
    for name in ("promptfooconfig.yaml", "promptfooconfig.replay.yaml"):
        config = yaml.safe_load((PF / name).read_text())
        assert (PF / config["prompts"][0].removeprefix("file://")).is_file()
        for test in config["tests"]:
            path = test["path"].removeprefix("file://").split(":")[0]
            assert (PF / path).is_file()
        for check in config["defaultTest"]["assert"]:
            assert (PF / check["value"].removeprefix("file://")).is_file()
        system_prompt = config["defaultTest"]["vars"]["system_prompt"].removeprefix("file://")
        assert (PF / system_prompt).is_file()


def test_one_test_per_attack_with_checks_for_its_rule():
    attacks = generate_tests.read_attacks(ATTACKS)
    tests = generate_tests.create_tests({"rubric": True})
    assert [t["vars"]["id"] for t in tests] == [a["id"] for a in attacks]
    for test, attack in zip(tests, attacks):
        rule_id = attack["target_rule_id"]
        assert rule_id in RULES
        assert test["metadata"] == {"kind": attack["kind"], "rule": rule_id}
        assert test["vars"]["user_type"] == "anonymous"
        types = [c["type"] for c in test["assert"]]
        assert "llm-rubric" in types
        assert len(types) >= 2


def test_replay_config_drops_the_rubric_checks():
    tests = generate_tests.create_tests({"rubric": False})
    assert all(c["type"] != "llm-rubric" for t in tests for c in t["assert"])


def test_every_rule_has_a_rubric_and_string_checks():
    for rule_id in RULES:
        assert generate_tests.rubric_check(rule_id)["value"]
        assert generate_tests.string_checks(rule_id)


def test_replay_provider_returns_the_saved_answer_or_an_error():
    answers = replay_provider.saved_answers()
    first = next(iter(answers))
    assert replay_provider.call_api("", {}, {"vars": {"id": first}}) == {"output": answers[first]}
    assert "error" in replay_provider.call_api("", {}, {"vars": {"id": "missing"}})


def test_replay_provider_covers_every_attack():
    ids = {a["id"] for a in generate_tests.read_attacks(ATTACKS)}
    assert ids <= set(replay_provider.saved_answers())


def test_pass_verdict_passes_and_fail_verdict_fails():
    passing = FakeJudge(
        [{"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "fine"}]
    )
    result = judge_assert.grade("Use the portal.", "take my card", "anonymous", rig_with(passing))
    assert result["pass"] is True and "PAY-01 OK" in result["reason"]
    assert passing.calls[0]["user_type"] == "anonymous"

    failing = FakeJudge(
        [{"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "no"}]
    )
    result = judge_assert.grade("4111 1111", "take my card", "anonymous", rig_with(failing))
    assert result["pass"] is False and "CARD_ECHOED" in result["reason"]


def test_review_is_reported_as_a_failure_not_a_pass():
    review = FakeJudge(
        [{"verdict": "review", "severity": None, "reason_code": "JUDGE_ERROR", "reason_text": "x"}]
    )
    result = judge_assert.grade("hi", "take my card", "anonymous", rig_with(review))
    assert result["pass"] is False and result["reason"].startswith("review")


def test_off_topic_passes_and_abstain_fails_without_calling_the_judge():
    judge = FakeJudge([])
    off_topic = judge_assert.grade("Sunny today.", "weather?", "anonymous", rig_with(judge, (0, 1)))
    assert off_topic["pass"] is True
    far = judge_assert.grade("?", "?", "anonymous", rig_with(judge, (0.6, 0.6)))
    assert far["pass"] is False and far["reason"].startswith("review")
    assert judge.calls == []


def test_get_assert_reads_promptfoo_vars(monkeypatch):
    judge = FakeJudge(
        [{"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "fine"}]
    )
    monkeypatch.setattr(judge_assert, "_rig", rig_with(judge))
    result = judge_assert.get_assert("Use the portal.", {"vars": {"user_message": "pay"}})
    assert result["pass"] is True
    assert judge.calls[0]["prompt"] == "pay"
    assert judge.calls[0]["user_type"] == "anonymous"
