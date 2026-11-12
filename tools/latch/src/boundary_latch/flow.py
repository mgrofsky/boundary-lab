"""The flow rule: when a run would read untrusted input, read private data and act outside.

Every function here is pure. The engine and the store call them with stored facts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .policy import LABELS

FULL = frozenset(LABELS)

# How the reason names a label the run already holds, and one this call adds.
HELD = {"untrusted": "untrusted input", "sensitive": "private data"}
ADDS = {"untrusted": "reads untrusted input", "sensitive": "reads private data",
        "external": "acts outside"}


def applies(mode: str, held: Iterable[str], call: Iterable[str]) -> bool:
    """True when the rule gates a call with these labels in a run holding those labels."""
    held, call = frozenset(held), frozenset(call)
    if mode == "allow" or not call:
        return False
    if held >= FULL:
        return "external" in call
    return held | call >= FULL


def call_labels(tool_labels: Iterable[str] | None, copied: bool) -> frozenset[str]:
    """The labels a call carries: its tool's, plus untrusted when it has a copied value.

    A tool with no labels carries none, so the rule never gates it.
    """
    labels = frozenset(tool_labels or ())
    if labels and copied:
        labels |= {"untrusted"}
    return labels


def source(entry: Mapping[str, Any]) -> str:
    """Name what set a label, from stored facts only."""
    if entry.get("copied"):
        return f"copied value in {entry['tool']}"
    if "tool" in entry:
        return str(entry["tool"])
    return "marked by the app"


def and_join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def reason(held: Mapping[str, Mapping[str, Any]], call: Iterable[str], tool: str,
           tool_labels: Iterable[str] | None) -> str:
    """One sentence that names what set each label the run holds and what this call adds.

    The labels go in a fixed order, so the same facts always give the same sentence.
    """
    call = frozenset(call)
    own = frozenset(tool_labels or ())
    reads = [f"{HELD[label]} ({source(held[label])})" for label in LABELS
             if label in held and label in HELD]
    done = []
    if reads:
        done.append("read " + and_join(reads))
    if "external" in held:
        done.append(f"acted outside ({source(held['external'])})")
    parts = []
    if done:
        parts.append("This run " + and_join(done) + ".")
    adds = {"external"} if frozenset(held) >= FULL else call - frozenset(held)
    words = []
    for label in LABELS:
        if label in adds:
            copied_only = label == "untrusted" and label not in own
            words.append("uses a copied value" if copied_only else ADDS[label])
    if words:
        parts.append(f"This call {and_join(words)} ({tool}).")
    return " ".join(parts)
