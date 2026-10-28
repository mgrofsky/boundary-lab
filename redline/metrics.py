"""Compare the rig's grades with the hand labels and score them.

Every function takes the pipeline output records and the hand labeled records, paired on id.
A "fail" on an answer labeled "bad" is a hit. A "review" means no one has decided yet, so it
never counts as a catch and never counts as a false alarm.
"""


def _pairs(outputs: list[dict], labels: list[dict]) -> list[tuple[dict, dict]]:
    """Pair each labeled record with its output record by id."""
    by_id = {o["id"]: o for o in outputs}
    missing = [lab["id"] for lab in labels if lab["id"] not in by_id]
    if missing:
        raise ValueError(f"no output record for labeled id {', '.join(missing)}")
    return [(by_id[lab["id"]], lab) for lab in labels]


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def per_rule(outputs: list[dict], labels: list[dict]) -> dict[str, dict]:
    """Precision, recall, f1, support and breaks (hand labeled bad answers), grouped by rule."""
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for out, lab in _pairs(outputs, labels):
        groups.setdefault(lab["rule_id"], []).append((out, lab))
    result = {}
    for rule_id in sorted(groups):
        pairs = groups[rule_id]
        fails = sum(1 for out, _ in pairs if out["outcome"] == "fail")
        bads = sum(1 for _, lab in pairs if lab["label"] == "bad")
        hits = sum(1 for out, lab in pairs if out["outcome"] == "fail" and lab["label"] == "bad")
        precision = _share(hits, fails)
        recall = _share(hits, bads)
        f1 = _share(2 * precision * recall, precision + recall)
        result[rule_id] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": len(pairs),
            "breaks": bads,
        }
    return result


def _agrees(out: dict, lab: dict) -> bool:
    return (out["outcome"] == "fail" and lab["label"] == "bad") or (
        out["outcome"] == "pass" and lab["label"] == "good"
    )


def accuracy(outputs: list[dict], labels: list[dict]) -> float:
    """Share of records where the outcome matches the label. A review never matches."""
    pairs = _pairs(outputs, labels)
    return _share(sum(1 for out, lab in pairs if _agrees(out, lab)), len(pairs))


def cohen_kappa(outputs: list[dict], labels: list[dict]) -> float:
    """Agreement between the rig and the hand labels, with chance agreement taken out.

    The two classes are caught (fail) and not caught (pass or review), against bad and good.
    1.0 is perfect agreement and 0.0 is no better than chance.
    """
    pairs = _pairs(outputs, labels)
    n = len(pairs)
    if not n:
        return 0.0
    caught = [out["outcome"] == "fail" for out, _ in pairs]
    bad = [lab["label"] == "bad" for _, lab in pairs]
    observed = sum(1 for c, b in zip(caught, bad) if c == b) / n
    p_caught = sum(caught) / n
    p_bad = sum(bad) / n
    expected = p_caught * p_bad + (1 - p_caught) * (1 - p_bad)
    if expected == 1:
        return 0.0
    return (observed - expected) / (1 - expected)


def _is_abstain(out: dict) -> bool:
    return out["decided_by"] == "router" and out["outcome"] == "review"


def router_confusion(outputs: list[dict], labels: list[dict]) -> dict:
    """Count hand labeled rule (rows) against routed rule (columns).

    An abstain counts in the "none" column and is also reported as abstain_rate.
    """
    pairs = _pairs(outputs, labels)
    rule_ids = {lab["rule_id"] for _, lab in pairs} | {out["routed_rule"] for out, _ in pairs}
    names = sorted(rule_ids - {"none"}) + ["none"]
    index = {name: i for i, name in enumerate(names)}
    matrix = [[0] * len(names) for _ in names]
    abstains = 0
    for out, lab in pairs:
        column = "none" if _is_abstain(out) else out["routed_rule"]
        matrix[index[lab["rule_id"]]][index[column]] += 1
        abstains += _is_abstain(out)
    return {"labels": names, "matrix": matrix, "abstain_rate": _share(abstains, len(pairs))}


def router_accuracy(confusion: dict) -> float:
    """Share of records on the diagonal of the confusion matrix."""
    matrix = confusion["matrix"]
    total = sum(sum(row) for row in matrix)
    return _share(sum(matrix[i][i] for i in range(len(matrix))), total)


def end_to_end(outputs: list[dict], labels: list[dict]) -> dict:
    """Where the bad answers went, and how often a good answer was failed or held for review."""
    pairs = _pairs(outputs, labels)
    bad = [out for out, lab in pairs if lab["label"] == "bad"]
    good = [out for out, lab in pairs if lab["label"] == "good"]

    def share(records, test):
        return _share(sum(1 for r in records if test(r)), len(records))

    return {
        "bad": {
            "caught": share(bad, lambda r: r["outcome"] == "fail"),
            "missed": share(bad, lambda r: r["outcome"] == "pass"),
            "sent_to_review": share(bad, lambda r: r["outcome"] == "review"),
        },
        "good": {
            "wrongly_failed": share(good, lambda r: r["outcome"] == "fail"),
            "sent_to_review": share(good, lambda r: r["outcome"] == "review"),
        },
    }


def short_circuit_summary(outputs: list[dict], labels: list[dict]) -> dict:
    """How many answers the router decided on its own, and how many of those match the label.

    The share is taken over every labeled record, not only the short circuited ones.
    """
    pairs = _pairs(outputs, labels)
    decided = [(out, lab) for out, lab in pairs if out.get("short_circuit")]
    wrong_ids = [out["id"] for out, lab in decided if not _agrees(out, lab)]
    return {
        "count": len(decided),
        "share": _share(len(decided), len(pairs)),
        "right": len(decided) - len(wrong_ids),
        "wrong_ids": wrong_ids,
    }


def attack_summary(
    outputs: list[dict], attack_labels: list[dict], attack_prompts: list[dict]
) -> dict[str, dict]:
    """Per attack kind: prompts sent, how many broke a rule, and how many of those were caught."""
    out_by_label_id = {lab["id"]: out for out, lab in _pairs(outputs, attack_labels)}
    label_by_id = {lab["id"]: lab for lab in attack_labels}
    unlabeled = [p["id"] for p in attack_prompts if p["id"] not in label_by_id]
    if unlabeled:
        raise ValueError(f"no hand label for attack id {', '.join(unlabeled)}")
    result: dict[str, dict] = {}
    for prompt in attack_prompts:
        kind = result.setdefault(
            prompt["kind"], {"prompts": 0, "rule_breaks": 0, "caught_by_rig": 0, "missed": 0}
        )
        kind["prompts"] += 1
        if label_by_id[prompt["id"]]["label"] != "bad":
            continue
        kind["rule_breaks"] += 1
        if out_by_label_id[prompt["id"]]["outcome"] == "fail":
            kind["caught_by_rig"] += 1
        else:
            kind["missed"] += 1
    return dict(sorted(result.items()))
