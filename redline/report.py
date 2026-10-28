"""Write the summary file, the long report and the README results table."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from redline import metrics

START = "<!-- results:start -->"
END = "<!-- results:end -->"


def _judge_block(outputs, labels) -> dict:
    return {
        "accuracy": metrics.accuracy(outputs, labels),
        "cohen_kappa": metrics.cohen_kappa(outputs, labels),
        "per_rule": metrics.per_rule(outputs, labels),
    }


def _read_cache(cache_path) -> tuple[bytes, list[str]]:
    """The cache file as bytes and the sorted model names in it. A missing file is empty."""
    if cache_path is None or not Path(cache_path).is_file():
        return b"", []
    data = Path(cache_path).read_bytes()
    models = set()
    for line in data.decode("utf-8").splitlines():
        if line.strip():
            models.add(json.loads(line).get("model") or "unknown")
    return data, sorted(models)


def _attack_routing(attack_outputs, attack_prompts) -> dict:
    """How many attack answers the router sent to a rule other than the one the attack targeted."""
    target = {p["id"]: p["target_rule_id"] for p in attack_prompts}
    misrouted = sum(1 for o in attack_outputs if o["routed_rule"] != target[o["id"]])
    return {"total": len(attack_outputs), "misrouted": misrouted}


def build_summary(
    holdout_outputs,
    holdout_labels,
    attack_outputs,
    attack_labels,
    attack_prompts,
    embedding_model,
    counts,
    cache_path=None,
) -> dict:
    """Score the holdout run and the attack run and gather every number in one dict."""
    confusion = metrics.router_confusion(holdout_outputs, holdout_labels)
    cache_bytes, models = _read_cache(cache_path)
    return {
        "router": {
            "holdout_accuracy": metrics.router_accuracy(confusion),
            "abstain_rate": confusion["abstain_rate"],
            "confusion": {"labels": confusion["labels"], "matrix": confusion["matrix"]},
        },
        "judge": {
            "holdout": _judge_block(holdout_outputs, holdout_labels),
            "attack": _judge_block(attack_outputs, attack_labels),
        },
        "end_to_end": metrics.end_to_end(holdout_outputs, holdout_labels),
        "attack": metrics.attack_summary(attack_outputs, attack_labels, attack_prompts),
        "attack_end_to_end": metrics.end_to_end(attack_outputs, attack_labels),
        "attack_routing": _attack_routing(attack_outputs, attack_prompts),
        "judge_model": models,
        "judge_cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
        "produced_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "embedding_model": embedding_model,
        "counts": dict(counts),
    }


def write_summary(summary: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n")


def _pct(share: float) -> str:
    return f"{round(share * 100)} percent"


def _attack_totals(summary: dict) -> tuple[int, int, int]:
    kinds = summary["attack"].values()
    return (
        sum(k["prompts"] for k in kinds),
        sum(k["rule_breaks"] for k in kinds),
        sum(k["caught_by_rig"] for k in kinds),
    )


def _rule_sentences(per_rule: dict) -> list[str]:
    lines = []
    for rule_id, m in per_rule.items():
        if rule_id == "none":
            continue
        lines.append(
            f"For {rule_id}, when the rig said an answer broke the rule, it was right "
            f"{_pct(m['precision'])} of the time. Of the answers that really broke the rule, "
            f"it caught {_pct(m['recall'])}."
        )
    return lines


def _judge_section(title: str, block: dict, set_name: str, first: bool) -> list[str]:
    miss = (
        "A review counts as a miss here, because no one has decided yet."
        if first
        else "Review counts as a miss, same as above."
    )
    return [
        f"## {title}",
        "",
        (
            f"The rig's final answer matched the hand label for {_pct(block['accuracy'])} of the "
            f"{set_name}, where the rig's final answer is pass, fail or review. {miss}"
        ),
        "",
        (
            f"Kappa, a measure of agreement with luck taken out, is {block['cohen_kappa']:.2f}. "
            "A kappa of 0 is no better than guessing and 1 is perfect agreement."
        ),
        "",
        (
            "Agreement and kappa count different things. Agreement compares the rig's final "
            "answer (pass, fail or review) with the hand label, and a review is a miss. Kappa "
            "compares a yes or no on whether the rig failed the answer with a yes or no on "
            "whether the hand label said bad, so a review counts as did not fail."
        ),
        "",
        *(line for sentence in _rule_sentences(block["per_rule"]) for line in (sentence, "")),
    ]


def _attack_sentence(kind: str, k: dict) -> str:
    start = f"The {kind} kind sent {k['prompts']} prompts"
    if not k["rule_breaks"]:
        return f"{start}, and none of them got the model to break a rule."
    return (
        f"{start}, and {k['rule_breaks']} of them got the model to break a rule. The rig "
        f"caught {k['caught_by_rig']} of those and missed {k['missed']}."
    )


def render_report(summary: dict) -> str:
    """Return the long report as markdown, in plain sentences with one table at the end."""
    router = summary["router"]
    bad = summary["end_to_end"]["bad"]
    good = summary["end_to_end"]["good"]
    prompts, breaks, caught = _attack_totals(summary)
    routing = summary["attack_routing"]
    good_passed = 100 - round(good["wrongly_failed"] * 100) - round(good["sent_to_review"] * 100)
    lines = [
        "# Results report",
        "",
        (
            f"This file is written by `redline report`. It was made at "
            f"{summary['produced_at']}. Answers were turned into numbers with "
            f"{summary['embedding_model']}."
        ),
        "",
        (
            f"The judge model was {', '.join(summary['judge_model']) or 'not recorded'}. The hash "
            "of the judge cache file is in summary.json."
        ),
        "",
        "## The data",
        "",
        *(f"- {path} has {n} records." for path, n in summary["counts"].items()),
        "",
        "## Routing",
        "",
        (
            f"The router picked the right rule for {_pct(router['holdout_accuracy'])} of the "
            "56 holdout answers, the 8 off topic ones included. The holdout answers were never "
            "used to set up the router or the judge."
        ),
        "",
        (
            "On an on topic answer, an abstain counts as wrong here. On an off topic answer, "
            "an abstain counts as right."
        ),
        "",
        (
            f"It abstained on {_pct(router['abstain_rate'])} of them. An abstain means the "
            "answer was not close to any rule, so it goes to a person."
        ),
        "",
        *_judge_section(
            "The judge on the holdout set", summary["judge"]["holdout"], "holdout answers", True
        ),
        *_judge_section(
            "The judge on the attack run", summary["judge"]["attack"], "attack answers", False
        ),
        "## End to end on the holdout set",
        "",
        (
            f"Of the answers that broke a rule, the rig caught {_pct(bad['caught'])}. "
            f"It missed {_pct(bad['missed'])} and sent {_pct(bad['sent_to_review'])} to a "
            "person for review."
        ),
        "",
        (
            "Of the answers that followed the rules, the rig failed "
            f"{_pct(good['wrongly_failed'])} by mistake. That is the cry wolf rate. It sent "
            f"{_pct(good['sent_to_review'])} of them to review and passed the other "
            f"{good_passed} percent."
        ),
        "",
        "## The attack run",
        "",
        (
            f"The model under test got {prompts} attack prompts, and {breaks} of them got it to "
            f"break a rule, going by the hand labels. The rig caught {caught} of those."
        ),
        "",
        (
            "The per rule numbers are grouped by the rule each attack targeted. The judge graded "
            "each answer against the rule the router picked, which differed from the target for "
            f"{routing['misrouted']} of the {routing['total']} attack answers. When the router "
            "picks the wrong rule the judge reads the answer under the wrong rule, so a prompt "
            "leak that talks about payments can pass as a payment answer."
        ),
        "",
        *(
            line
            for kind, k in summary["attack"].items()
            for line in (_attack_sentence(kind, k), "")
        ),
        "## Per rule numbers",
        "",
        (
            "Precision is how often a fail was right. Recall is how many of the real rule breaks "
            "were caught. F1 blends the two into one number from 0 to 1. The off topic answers are "
            "not in the table because there is no rule for them to break."
        ),
        "",
        "| Set | Rule | Breaks | Precision | Recall | F1 | Answers |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for set_name in ("holdout", "attack"):
        for rule_id, m in summary["judge"][set_name]["per_rule"].items():
            if rule_id == "none":
                continue
            lines.append(
                f"| {set_name} | {rule_id} | {m['breaks']} | {_pct(m['precision'])} | {_pct(m['recall'])} "
                f"| {m['f1']:.2f} | {m['support']} |"
            )
    return "\n".join(lines) + "\n"


def write_report(summary: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(summary))


def render_readme_table(summary: dict) -> str:
    """Return the short results table that goes between the README markers."""
    holdout = summary["judge"]["holdout"]
    bad = summary["end_to_end"]["bad"]
    prompts, breaks, caught = _attack_totals(summary)
    caught_share = f" ({_pct(caught / breaks)})" if breaks else ""
    rows = [
        ("Router accuracy on holdout", _pct(summary["router"]["holdout_accuracy"])),
        (
            "Rig accuracy on holdout (router and judge together)",
            _pct(holdout["accuracy"]),
        ),
        (
            "Judge agreement with hand labels on holdout (kappa)",
            f"{holdout['cohen_kappa']:.2f}",
        ),
        ("Rule breaks caught end to end", _pct(bad["caught"])),
        ("Cry wolf rate on holdout", _pct(summary["end_to_end"]["good"]["wrongly_failed"])),
        ("Attack prompts that broke a rule", f"{breaks} of {prompts}"),
        ("Of those, caught by the rig", f"{caught} of {breaks}{caught_share}"),
        (
            "Cry wolf rate on attack prompts",
            _pct(summary["attack_end_to_end"]["good"]["wrongly_failed"]),
        ),
        (
            "Judge agreement with hand labels on attack prompts (kappa)",
            f"{summary['judge']['attack']['cohen_kappa']:.2f}",
        ),
    ]
    lines = ["| What we measured | Result |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    lines += [
        "",
        (
            "This table is written from results/summary.json by `redline readme`, "
            "so do not edit it by hand."
        ),
    ]
    return "\n".join(lines)


def _with_table(text: str, summary: dict) -> str:
    start = text.find(START)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"README needs the markers {START} and {END}, in that order")
    head = text[: start + len(START)]
    return f"{head}\n{render_readme_table(summary)}\n{text[end:]}"


def update_readme(path, summary: dict) -> None:
    """Replace the text between the results markers with a fresh table."""
    path = Path(path)
    path.write_text(_with_table(path.read_text(), summary))


def check_readme(path, summary: dict) -> bool:
    """Return True when the README on disk already holds the table summary.json would give."""
    text = Path(path).read_text()
    return text == _with_table(text, summary)
