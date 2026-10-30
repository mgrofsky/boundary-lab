"""Write the summary file, full report, and README results table."""

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
    """Read the cache and its model names. Return empty values if the file is missing."""
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
    run_meta=None,
) -> dict:
    """Score both runs and collect the results in one dict.

    run_meta holds the "holdout" and "attack" meta files written by `redline run`, or is None.
    """
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
        "short_circuit": {
            "holdout": metrics.short_circuit_summary(holdout_outputs, holdout_labels),
            "attack": metrics.short_circuit_summary(attack_outputs, attack_labels),
        },
        "run": run_meta,
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
            f"For {rule_id}, when Redline said an answer broke the rule, it was right "
            f"{_pct(m['precision'])} of the time. Of the answers that really broke the rule, "
            f"it caught {_pct(m['recall'])}."
        )
    return lines


def _judge_section(title: str, block: dict, set_name: str, first: bool) -> list[str]:
    miss = (
        "Review counts as a miss because the answer still needs a decision."
        if first
        else "Review counts as a miss here too."
    )
    return [
        f"## {title}",
        "",
        (
            f"Redline's final decision matched the hand label for {_pct(block['accuracy'])} of the "
            f"{set_name}. Each decision is pass, fail, or review. {miss}"
        ),
        "",
        (
            f"Kappa is {block['cohen_kappa']:.2f}. It measures agreement after accounting for "
            "agreement expected by chance. A score of 0 matches that chance level; "
            "1 means complete agreement."
        ),
        "",
        (
            "Accuracy and kappa handle review differently. Accuracy counts a review as a miss. "
            "Kappa compares fail against pass or review, and checks that against the bad or "
            "good hand label. Both scores include the router's decisions."
        ),
        "",
        *(line for sentence in _rule_sentences(block["per_rule"]) for line in (sentence, "")),
    ]


def _attack_sentence(kind: str, k: dict) -> str:
    names = {
        "card_elicitation": "getting card details into chat",
        "prompt_extraction": "getting the chatbot's instructions",
        "roleplay_injection": "using roleplay to override the rules",
        "verification_bypass": "skipping identity checks",
    }
    start = f"The group for {names.get(kind, kind)} had {k['prompts']} prompts"
    if not k["rule_breaks"]:
        return f"{start}, and none of them got the model to break a rule."
    return (
        f"{start}, and {k['rule_breaks']} of them got the model to break a rule. Redline "
        f"caught {k['caught_by_rig']} of those and missed {k['missed']}."
    )


def _router_sentence(meta: dict) -> str:
    if meta.get("k"):
        start = f"This run used k-means with {meta['k']} clusters."
    else:
        start = "This run used center matching."
    if not meta["short_circuit"]:
        return f"{start} The shortcut was off. Off-topic matches still passed without the judge."
    if meta.get("k"):
        return (
            f"{start} The shortcut needed at least {meta['min_members']} examples for the user's "
            f"type in the matched cluster, with at least {_pct(meta['purity'])} sharing one label."
        )
    return (
        f"{start} The shortcut needed at least {meta['min_members']} good and "
        f"{meta['min_members']} bad examples for the matched rule and user type. "
        f"The winning match needed a score gap of at least {meta['margin']:.2f} "
        "and had to meet the router's cutoff."
    )


def _short_circuit_sentences(set_name: str, sc: dict, meta: dict | None) -> list[str]:
    if sc["count"]:
        line = (
            f"On the {set_name}, the router used the shortcut for {sc['count']} answers, "
            f"or {_pct(sc['share'])} of the total. {sc['right']} matched the hand label."
        )
        if sc["wrong_ids"]:
            line += f" The wrong ones were {', '.join(sc['wrong_ids'])}."
    else:
        line = f"On the {set_name}, the router did not use the shortcut."
    if meta:
        line += (
            f" {meta['judge_calls']} answers went to the judge, including saved-grade lookups. "
            f"Grading took {meta['seconds']} seconds."
        )
    return [line, ""]


def _short_circuit_section(summary: dict) -> list[str]:
    """Describe the router and shortcut. Skip summaries that don't have these results."""
    if "short_circuit" not in summary:
        return []
    run = summary.get("run") or {}
    meta = run.get("holdout") or run.get("attack")
    lines = ["## Routing and the shortcut", ""]
    if meta:
        lines += [_router_sentence(meta), ""]
    else:
        lines += ["No run settings files were found, so the router settings aren't recorded.", ""]
    lines += [
        (
            "The `--short-circuit` option lets the router pass or fail some answers using the "
            "labeled examples, without asking the judge. Center matching averages examples "
            "for each rule and compares separate good and bad groups. K-means groups similar "
            "examples into clusters, then counts labels in the matched cluster for the user's type."
        ),
        "",
    ]
    for key, set_name in (("holdout", "holdout set"), ("attack", "attack run")):
        lines += _short_circuit_sentences(set_name, summary["short_circuit"][key], run.get(key))
    lines += [
        (
            "These times cover grading after router setup. The saved settings don't say which "
            "grades came from live calls or the cache. The judge count includes both, so it "
            "isn't a count of network requests or a measure of live-model speed."
        ),
        "",
    ]
    return lines


def render_report(summary: dict) -> str:
    """Return the full report as Markdown, with one table at the end."""
    router = summary["router"]
    bad = summary["end_to_end"]["bad"]
    good = summary["end_to_end"]["good"]
    prompts, breaks, caught = _attack_totals(summary)
    routing = summary["attack_routing"]
    holdout_rules = summary["judge"]["holdout"]["per_rule"]
    holdout_count = sum(rule["support"] for rule in holdout_rules.values())
    off_topic_count = holdout_rules.get("none", {}).get("support", 0)
    good_passed = 100 - round(good["wrongly_failed"] * 100) - round(good["sent_to_review"] * 100)
    lines = [
        "# Results report",
        "",
        (
            f"This report uses the results saved at {summary['produced_at']}. "
            f"The router used `{summary['embedding_model']}` to turn text into numbers "
            "it could compare. `redline report` writes this file."
        ),
        "",
        (
            f"The judge model was recorded as {', '.join(summary['judge_model']) or 'unknown'}. "
            "These names come from the cache file. They haven't been checked separately. "
            "The cache file's hash, a fingerprint of its contents, is in `summary.json`."
        ),
        "",
        "## The data",
        "",
        (
            "The holdout set contains answers kept back for checking the tool. The attack run "
            "contains the chatbot's answers to prompts that tried to make it break a rule. "
            "Both sets use labels written by hand."
        ),
        "",
        *(f"- {path} has {n} records." for path, n in summary["counts"].items()),
        "",
        "## Routing",
        "",
        (
            f"The router picked the right rule for {_pct(router['holdout_accuracy'])} of the "
            f"{holdout_count} holdout answers, including {off_topic_count} off-topic answers. "
            "Keep these answers separate from the examples used to set up the router and judge. "
            "The report doesn't check that separation for you."
        ),
        "",
        (
            "If the router can't find a close enough match, it marks the answer for review. "
            "That counts as a wrong match for an answer about a rule, and a correct match "
            "for an off-topic answer."
        ),
        "",
        (
            f"The router marked {_pct(router['abstain_rate'])} of the answers for review. "
            "Redline records that result; it doesn't send it to a person."
        ),
        "",
        *_short_circuit_section(summary),
        *_judge_section(
            "Final decisions on the holdout set",
            summary["judge"]["holdout"],
            "holdout answers",
            True,
        ),
        *_judge_section(
            "Final decisions on the attack run", summary["judge"]["attack"], "attack answers", False
        ),
        "## Violations, false alarms, and reviews on the holdout set",
        "",
        (
            f"Of the answers that broke a rule, Redline caught {_pct(bad['caught'])}. "
            f"It passed {_pct(bad['missed'])} by mistake and marked "
            f"{_pct(bad['sent_to_review'])} for review."
        ),
        "",
        (
            "Of the answers that followed the rules, Redline failed "
            f"{_pct(good['wrongly_failed'])} by mistake. That's the false alarm rate. It marked "
            f"{_pct(good['sent_to_review'])} of them for review and passed the other "
            f"{good_passed} percent."
        ),
        "",
        "## The attack run",
        "",
        (
            f"The model under test got {prompts} attack prompts, and {breaks} of them got it to "
            f"break a rule, going by the hand labels. Redline caught {caught} of those."
        ),
        "",
        (
            "The results are grouped by the rule each attack targeted. The router "
            "picked a different rule or no rule for "
            f"{routing['misrouted']} of the {routing['total']} attack answers. When the judge is "
            "used, it checks the rule the router picked. A wrong match can hide a violation, "
            "such as leaked instructions in an answer about payments."
        ),
        "",
        *(
            line
            for kind, k in summary["attack"].items()
            for line in (_attack_sentence(kind, k), "")
        ),
        "## Results for each rule",
        "",
        (
            "Precision is how often a fail was right. Recall is how many of the real rule breaks "
            "were caught. F1 combines the two into one score from 0 to 1. Off-topic answers are "
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
            "Overall accuracy on holdout (router and judge)",
            _pct(holdout["accuracy"]),
        ),
        (
            "Agreement with hand labels on holdout (kappa)",
            f"{holdout['cohen_kappa']:.2f}",
        ),
        ("Rule breaks caught on holdout", _pct(bad["caught"])),
        ("False alarm rate on holdout", _pct(summary["end_to_end"]["good"]["wrongly_failed"])),
        ("Attack prompts that broke a rule", f"{breaks} of {prompts}"),
        ("Of those, caught by Redline", f"{caught} of {breaks}{caught_share}"),
        (
            "False alarm rate on attack answers",
            _pct(summary["attack_end_to_end"]["good"]["wrongly_failed"]),
        ),
        (
            "Agreement with hand labels on attack answers (kappa)",
            f"{summary['judge']['attack']['cohen_kappa']:.2f}",
        ),
    ]
    lines = ["| What we measured | Result |", "| --- | --- |"]
    lines += [f"| {name} | {value} |" for name, value in rows]
    lines += [
        "",
        (
            "Run `redline readme` to update this table from `results/summary.json`. "
            "Don't edit the table by hand."
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


NO_SHORT_CIRCUIT = {"count": 0, "share": 0.0, "right": 0, "wrong_ids": []}

# (header, row key) for each column of the comparison table, after the run label.
COMPARISON_COLUMNS = [
    ("Router accuracy holdout", "router_accuracy_holdout"),
    ("Overall accuracy holdout", "rig_accuracy_holdout"),
    ("Kappa holdout", "kappa_holdout"),
    ("Breaks caught holdout", "breaks_caught_holdout"),
    ("False alarms holdout", "cry_wolf_holdout"),
    ("Attack caught (x of y)", "attack_caught"),
    ("False alarms attack", "cry_wolf_attack"),
    ("Kappa attack", "kappa_attack"),
    ("Shortcut share holdout", "short_circuit_share_holdout"),
    ("Shortcut correct holdout", "short_circuit_right_holdout"),
    ("Shortcut share attack", "short_circuit_share_attack"),
    ("Shortcut correct attack", "short_circuit_right_attack"),
    ("Answers sent to judge (holdout + attack)", "judge_calls"),
    ("Seconds (holdout + attack)", "seconds"),
]


def _comparison_row(folder: Path) -> dict:
    summary = json.loads((folder / "summary.json").read_text())
    holdout_meta = json.loads((folder / "holdout_meta.json").read_text())
    attack_meta = json.loads((folder / "attack_responses_meta.json").read_text())
    _, breaks, caught = _attack_totals(summary)
    short_circuit = summary.get("short_circuit") or {}
    sc_holdout = short_circuit.get("holdout", NO_SHORT_CIRCUIT)
    sc_attack = short_circuit.get("attack", NO_SHORT_CIRCUIT)
    on_off = "on" if holdout_meta["short_circuit"] else "off"
    return {
        "folder": folder.as_posix(),
        "label": f"{holdout_meta['router']}, short circuit {on_off}",
        "router_accuracy_holdout": summary["router"]["holdout_accuracy"],
        "rig_accuracy_holdout": summary["judge"]["holdout"]["accuracy"],
        "kappa_holdout": summary["judge"]["holdout"]["cohen_kappa"],
        "breaks_caught_holdout": summary["end_to_end"]["bad"]["caught"],
        "cry_wolf_holdout": summary["end_to_end"]["good"]["wrongly_failed"],
        "attack_caught": {"caught": caught, "breaks": breaks},
        "cry_wolf_attack": summary["attack_end_to_end"]["good"]["wrongly_failed"],
        "kappa_attack": summary["judge"]["attack"]["cohen_kappa"],
        "short_circuit_share_holdout": sc_holdout["share"],
        "short_circuit_right_holdout": {"right": sc_holdout["right"], "count": sc_holdout["count"]},
        "short_circuit_share_attack": sc_attack["share"],
        "short_circuit_right_attack": {"right": sc_attack["right"], "count": sc_attack["count"]},
        "judge_calls": holdout_meta["judge_calls"] + attack_meta["judge_calls"],
        "seconds": round(holdout_meta["seconds"] + attack_meta["seconds"], 2),
    }


def build_comparison(folders) -> list[dict]:
    """One row of numbers per run folder, each folder holding summary.json and two meta files."""
    return [_comparison_row(Path(folder)) for folder in folders]


def _cell(key: str, value) -> str:
    if key == "attack_caught":
        return f"{value['caught']} of {value['breaks']}"
    if key.startswith("short_circuit_right"):
        return f"{value['right']} of {value['count']}" if value["count"] else "none"
    if key.startswith("kappa"):
        return f"{value:.2f}"
    if key in ("judge_calls", "seconds"):
        return str(value)
    return _pct(value)


def render_comparison(rows: list[dict]) -> str:
    """Return a Markdown table comparing several runs."""
    headers = ["Run"] + [header for header, _ in COMPARISON_COLUMNS]
    lines = [
        "# Router comparison",
        "",
        (
            "`redline compare` writes this file. Each row is one run folder, scored on "
            "the holdout set and the attack run. Holdout answers are kept back for checking "
            "the tool. Attack answers come from prompts that tried to make the chatbot break a rule."
        ),
        "",
        (
            "Center matching averages the examples for each rule. K-means groups similar examples into "
            "clusters. With the shortcut on, the router can pass or fail some answers using "
            "the labeled examples, without asking the judge."
        ),
        "",
        (
            "Start with breaks caught and false alarms. A false alarm means a good answer "
            "was marked fail. Shortcut correct shows how many shortcut decisions matched the hand labels. "
            "None means the shortcut made no decisions."
        ),
        "",
        (
            "Overall accuracy counts review as a miss. Kappa measures agreement after "
            "accounting for chance, with pass and review grouped together. Both scores include "
            "the router's decisions."
        ),
        "",
        (
            "The judge count includes cached grades and replay lookups as well as network requests. "
            "Seconds excludes router setup. These files don't record live calls separately, "
            "so the times don't show whether one router makes live models faster."
        ),
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = [row["label"]] + [_cell(key, row[key]) for _, key in COMPARISON_COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
