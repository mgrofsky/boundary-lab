"""A tiny shell loop for a chat model that plays a Linux shell, with Redline in front of it.

Run it from anywhere in a checkout that has the minilm extra installed:

    python docs/examples/guard_shell.py

The model's replies are replayed from examples/shell/holdout.jsonl, and the judge reads
saved grades, so no model, key or network is needed. One reply runs a second command
the visitor never typed. It is held back, and the loop prints the visitor's prompt
again in its place, as a shell does after a command that prints nothing.
"""

import re
from pathlib import Path

from redline import Grader, Guard

ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLE = ROOT / "examples" / "shell"

# What the visitor's terminal shows, and what the model wrote back. In a real app the
# reply would come from the model.
TURNS = [
    ("root@kestrel:~# id", "uid=0(root) gid=0(root) groups=0(root)\nroot@kestrel:~# "),
    ("root@kestrel:~# cd /var/www", "root@kestrel:/var/www# ls\nhtml\nroot@kestrel:/var/www# "),
    ("root@kestrel:/srv/app# pwd", "/srv/app\nroot@kestrel:/srv/app# "),
]


def build_guard(record=None) -> Guard:
    """The shell rules, examples and saved grades, checking every rule on each reply."""
    grader = Grader(
        rules=EXAMPLE / "rules.yaml",
        train=EXAMPLE / "train.jsonl",
        cache=EXAMPLE / "judge_cache.jsonl",
        top_k=5,
    )
    return Guard(grader, record=record)


# The shell prompt at the start of a message, such as root@kestrel:~# or deploy@kestrel:~$.
PROMPT = re.compile(r"^\S+@\S+?[#$] ")


def fallback(message: str) -> str:
    """The prompt the visitor already saw, printed again, or nothing when there was none.

    It is what a shell shows after a command that prints nothing, so it breaks no rule.
    """
    match = PROMPT.match(message)
    return match.group(0) if match else ""


def shell(guard: Guard, turns=TURNS, show=print) -> list:
    """Run each reply through the guard and print what the visitor would see."""
    decisions = []
    for number, (message, reply) in enumerate(turns, start=1):
        decision = guard.check(message, reply, "visitor", record_id=f"turn-{number}")
        decisions.append(decision)
        show(message)
        if decision.allow:
            show(reply)
        else:
            show(f"[held back: {decision.rule_id} {decision.reason_code}]")
            show(fallback(message))
    return decisions


if __name__ == "__main__":
    shell(build_guard())
