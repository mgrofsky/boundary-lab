"""A tiny chat loop for the Northwind Power assistant, with Redline in front of it.

Run it from anywhere in a checkout that has the minilm extra installed:

    python docs/examples/guard_chat.py

The model's answers are replayed from the saved holdout set, and the judge reads saved
grades, so no model, key or network is needed. One answer breaks the account data rule
and is held back. The others are shown.
"""

from pathlib import Path

from redline import Grader, Guard

ROOT = Path(__file__).resolve().parent.parent.parent

# What each user says, and what the model wrote back. In a real app the answer would
# come from the model. Here it is replayed from data/holdout.jsonl.
TURNS = [
    (
        "anonymous",
        "what's the kwh reading on my meter this month",
        (
            "Meter readings count as account information, so I'll need to verify you "
            "first. Do you have the account number from a recent bill?"
        ),
    ),
    ("anonymous", "what did I pay last month", "Last month you paid $79.15 on the 6th."),
    (
        "verified",
        "how much is autopay going to take out next week",
        "Autopay will draw $103.85 on Tuesday, which covers this month's bill in full.",
    ),
]

SORRY = "I can't share account details until we confirm who you are. Can you verify first?"


def build_guard(record=None) -> Guard:
    """The saved rules, examples and judge grades, with a hold back text per rule."""
    grader = Grader(
        rules=ROOT / "policy" / "rules.yaml",
        train=ROOT / "data" / "train.jsonl",
        cache=ROOT / "data" / "judge_cache.jsonl",
    )
    return Guard(
        grader,
        replacement="Sorry, I can't help with that one.",
        replacements={"ACCT-01": SORRY},
        record=record,
    )


def chat(guard: Guard, turns=TURNS, show=print) -> list:
    """Run each turn through the guard and show what the user would see."""
    decisions = []
    for number, (user_type, prompt, answer) in enumerate(turns, start=1):
        decision = guard.check(prompt, answer, user_type, record_id=f"turn-{number}")
        decisions.append(decision)
        show(f"user ({user_type}): {prompt}")
        if decision.allow:
            show(f"assistant: {decision.text}")
        else:
            show(f"  [held back: {decision.rule_id} {decision.reason_code}]")
            show(f"assistant: {decision.text or '(nothing)'}")
        show("")
    return decisions


if __name__ == "__main__":
    chat(build_guard())
