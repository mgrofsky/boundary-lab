"""Run each answer through route, gate stance and judge, and record one outcome."""

from redline import gate
from redline.router import RouteResult

NO_EXAMPLES = {"good": [], "bad": []}


def _decide(record, policy, router, judge, examples) -> tuple[RouteResult, dict]:
    routed = router.route(record["prompt"], record["response"], record["user_type"])
    if routed.abstained:
        return routed, {
            "outcome": "review",
            "decided_by": "router",
            "reason_code": "ROUTER_ABSTAIN",
            "reason_text": "The answer is not close to any rule, so a person should look at it.",
            "severity": None,
        }
    if routed.rule_id == "none":
        return routed, {
            "outcome": "pass",
            "decided_by": "router",
            "reason_code": "OK",
            "reason_text": "The answer is off topic, so no rule applies.",
            "severity": None,
        }
    if routed.verdict:
        return routed, {
            "outcome": routed.verdict,
            "decided_by": "router",
            "reason_code": "SHORT_CIRCUIT",
            "reason_text": routed.note,
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
    return routed, {
        "outcome": graded["verdict"],
        "decided_by": "judge",
        "reason_code": graded["reason_code"],
        "reason_text": graded["reason_text"],
        "severity": graded["severity"],
    }


def run(records, policy, router, judge, examples) -> list[dict]:
    """Grade each record and return one output record per input, with one final outcome."""
    # Stub routers in tests have no name, and every real router before kmeans was "center".
    router_name = getattr(router, "name", "center")
    out = []
    for record in records:
        routed, decision = _decide(record, policy, router, judge, examples)
        out.append(
            {
                "id": record["id"],
                "user_type": record["user_type"],
                "prompt": record["prompt"],
                "response": record["response"],
                "routed_rule": routed.rule_id,
                "similarity": float(routed.similarity),
                "router": router_name,
                "cell": routed.cell,
                "short_circuit": bool(routed.verdict),
                "outcome": decision["outcome"],
                "decided_by": decision["decided_by"],
                "reason_code": decision["reason_code"],
                "reason_text": decision["reason_text"],
                "severity": decision["severity"],
            }
        )
    return out
