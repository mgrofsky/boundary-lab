"""Route each answer, check it when needed, and record one final decision."""

from redline import gate
from redline.router import RouteResult

NO_EXAMPLES = {"good": [], "bad": []}
# With more than one rule checked, the highest rank here is the outcome kept.
WORST = {"pass": 0, "review": 1, "fail": 2}


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
    return routed, _judge_one(record, policy[routed.rule_id], judge, examples)


def _judge_one(record, rule, judge, examples) -> dict:
    stance = gate.check(rule, record["user_type"])
    graded = judge.grade(
        rule,
        examples.get(rule.rule_id, NO_EXAMPLES),
        record["prompt"],
        record["response"],
        record["user_type"],
        stance.text,
    )
    return {
        "outcome": graded["verdict"],
        "decided_by": "judge",
        "reason_code": graded["reason_code"],
        "reason_text": graded["reason_text"],
        "severity": graded["severity"],
    }


def _decide_many(record, policy, router, judge, examples, top_k, floor):
    """Judge the top_k closest rules and keep the worst outcome.

    The closest rule decides the path first. An abstain, an off-topic match or a shortcut
    ends there, as with one rule. Otherwise every rule in the top_k at or above the floor
    is judged, always including the closest one. The "none" label is never judged.
    Returns the route and decision for the rule whose outcome was taken, the rules
    checked and the outcome for each.
    """
    routed, decision = _decide(record, policy, router, judge, examples)
    if decision["decided_by"] == "router":
        checked = [] if routed.abstained or routed.rule_id == "none" else [routed.rule_id]
        return routed, decision, checked, {r: decision["outcome"] for r in checked}

    ranked = router.rank(record["prompt"], record["response"], record["user_type"])
    candidates = [c for c in ranked if c.rule_id not in ("none", routed.rule_id)]
    extra = [c for c in candidates[: top_k - 1] if floor is None or c.similarity >= floor]
    taken, outcomes = (routed, decision), {routed.rule_id: decision["outcome"]}
    for candidate in extra:
        result = _judge_one(record, policy[candidate.rule_id], judge, examples)
        outcomes[candidate.rule_id] = result["outcome"]
        # Candidates come closest first, so on a tie the closer rule stays.
        if WORST[result["outcome"]] > WORST[taken[1]["outcome"]]:
            taken = (candidate, result)
    return taken[0], taken[1], list(outcomes), outcomes


def run(records, policy, router, judge, examples, top_k=1, floor=None) -> list[dict]:
    """Grade each record and return one output record per input, with one final outcome.

    With top_k above 1, each record is judged against up to top_k rules and the output adds
    rules_checked and outcomes_by_rule. With top_k 1 the output is the same as before.
    """
    if top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")
    # Stub routers in tests have no name, and every real router before kmeans was "center".
    router_name = getattr(router, "name", "center")
    out = []
    for record in records:
        if top_k == 1:
            routed, decision = _decide(record, policy, router, judge, examples)
        else:
            routed, decision, checked, outcomes = _decide_many(
                record, policy, router, judge, examples, top_k, floor
            )
        row = {
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
        if top_k > 1:
            row["rules_checked"] = checked
            row["outcomes_by_rule"] = outcomes
        out.append(row)
    return out


def judge_calls(outputs: list[dict]) -> int:
    """Count the judge calls behind a list of output records."""
    total = 0
    for o in outputs:
        if "rules_checked" in o:
            total += 0 if o["short_circuit"] else len(o["rules_checked"])
        else:
            total += o["decided_by"] == "judge"
    return total
