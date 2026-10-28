"""Run the guardrail-eval rig as a promptfoo python assertion.

promptfoo calls get_assert(output, context) once per answer. The rig routes the answer
to a rule, adds the stance for the user type, and asks the judge for a verdict. A pass
verdict passes, a fail verdict fails, and a review is reported as a failure with the
reason, since a non decision should not show up green.

The judge is picked with the GE_JUDGE environment variable:
  live    call the model named by GE_JUDGE_BASE_URL, GE_JUDGE_MODEL and GE_JUDGE_API_KEY
  replay  use the saved answers in data/judge_cache.jsonl, no model and no network
The default is live. A .env file in the repository root is read for the live settings.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardrail_eval import gate, policy
from guardrail_eval.cli import _env, load_env_file, read_jsonl
from guardrail_eval.embed import Embedder
from guardrail_eval.judge import ChatEndpointJudge, ReplayJudge, select
from guardrail_eval.router import Router

RULES_PATH = ROOT / "policy" / "rules.yaml"
TRAIN_PATH = ROOT / "data" / "train.jsonl"
CACHE_PATH = ROOT / "data" / "judge_cache.jsonl"

_rig = None


class Rig:
    """The three parts of the pipeline, built once per process."""

    def __init__(self, rules, router, judge, examples):
        self.rules = rules
        self.router = router
        self.judge = judge
        self.examples = examples


def build_rig() -> Rig:
    rules = policy.load(RULES_PATH)
    train = read_jsonl(TRAIN_PATH)
    examples = select(train)
    router = Router(Embedder()).fit(train)
    if os.environ.get("GE_JUDGE", "live") == "replay":
        judge = ReplayJudge(CACHE_PATH)
    else:
        load_env_file(str(ROOT / ".env"))
        base_url, model, api_key = _env("GE_JUDGE")
        judge = ChatEndpointJudge(base_url, model, api_key)
    return Rig(rules, router, judge, examples)


def grade(output: str, user_message: str, user_type: str, rig: Rig) -> dict:
    """Route, gate and judge one answer. Returns a promptfoo GradingResult dict."""
    routed = rig.router.route(user_message, output)
    if routed.abstained:
        return {
            "pass": False,
            "score": 0.0,
            "reason": f"review: not close to any rule (similarity {routed.similarity:.2f})",
        }
    if routed.rule_id == "none":
        return {"pass": True, "score": 1.0, "reason": "off topic, no rule applies"}
    rule = rig.rules[routed.rule_id]
    stance = gate.check(rule, user_type)
    verdict = rig.judge.grade(
        rule,
        rig.examples.get(rule.rule_id, {"good": [], "bad": []}),
        user_message,
        output,
        user_type,
        stance.text,
    )
    label = f"{routed.rule_id} {verdict['reason_code']}: {verdict['reason_text']}"
    if verdict["verdict"] == "pass":
        return {"pass": True, "score": 1.0, "reason": label}
    if verdict["verdict"] == "fail":
        return {"pass": False, "score": 0.0, "reason": label}
    return {"pass": False, "score": 0.0, "reason": f"review: {label}"}


def get_assert(output: str, context) -> dict:
    global _rig
    if _rig is None:
        _rig = build_rig()
    variables = context["vars"]
    return grade(output, variables["user_message"], variables.get("user_type", "anonymous"), _rig)
