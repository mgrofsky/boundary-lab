"""Run each answer through route, gate stance and judge, and record one outcome."""

from guardrail_eval import gate

NO_EXAMPLES = {"good": [], "bad": []}


def _decide(record, policy, router, judge, examples) -> tuple[str, float, dict]:
    routed = router.route(record["prompt"], record["response"])
    if routed.abstained:
        return routed.rule_id, routed.similarity, {
            "outcome": "review",
            "decided_by": "router",
            "reason_code": "ROUTER_ABSTAIN",
            "reason_text": "The answer is not close to any rule, so a person should look at it.",
            "severity": None,
        }
    if routed.rule_id == "none":
        return routed.rule_id, routed.similarity, {
            "outcome": "pass",
            "decided_by": "router",
            "reason_code": "OK",
            "reason_text": "The answer is off topic, so no rule applies.",
            "severity": None,
        }
    rule = policy[routed.rule_id]
    stance = gate.check(rule, record["user_type"])
    graded = judge.grade(
        rule,
        examples.get(rule.rule_id, NO_EXAMPLES),
        record["prompt"],
        record["response"],
        record["user_type"],
        stance.text,
    )
    return routed.rule_id, routed.similarity, {
        "outcome": graded["verdict"],
        "decided_by": "judge",
        "reason_code": graded["reason_code"],
        "reason_text": graded["reason_text"],
        "severity": graded["severity"],
    }


def run(records, policy, router, judge, examples) -> list[dict]:
    """Grade each record and return one output record per input, with one final outcome."""
    out = []
    for record in records:
        routed_rule, similarity, decision = _decide(record, policy, router, judge, examples)
        out.append(
            {
                "id": record["id"],
                "user_type": record["user_type"],
                "prompt": record["prompt"],
                "response": record["response"],
                "routed_rule": routed_rule,
                "similarity": float(similarity),
                "outcome": decision["outcome"],
                "decided_by": decision["decided_by"],
                "reason_code": decision["reason_code"],
                "reason_text": decision["reason_text"],
                "severity": decision["severity"],
            }
        )
    return out
