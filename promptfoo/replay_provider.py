"""A promptfoo provider that replays the chatbot answers saved from the original attack run.

It reads results/attack_responses_outputs.jsonl and returns the saved answer for the
test's id. It doesn't call a chatbot. This lets you repeat the checks on the same
answers while working on them.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAVED = ROOT / "results" / "attack_responses_outputs.jsonl"

_answers = None


def saved_answers(path=SAVED) -> dict[str, str]:
    global _answers
    if _answers is None:
        _answers = {}
        for line in Path(path).read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                _answers[record["id"]] = record["response"]
    return _answers


def call_api(prompt, options, context) -> dict:
    attack_id = context["vars"].get("id")
    answers = saved_answers()
    if attack_id not in answers:
        return {"error": f"no saved answer for attack {attack_id!r}"}
    return {"output": answers[attack_id]}
