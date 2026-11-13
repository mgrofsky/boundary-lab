"""garak-grade must write what Redline 0.4.0 wrote for the same input.

The files under fixtures/garak_grade_0_4_0 were written by the 0.4.0 code, one folder
per case. The graded and eval files must match byte for byte. The summary must match
once the keys added since are taken out.
"""

import json
from pathlib import Path

import httpx
import pytest
import yaml
from conftest import RULES

from redline import cli
from redline.judge import ChatEndpointJudge, FakeJudge

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "garak_report.jsonl"
OLD = HERE / "fixtures" / "garak_grade_0_4_0"
PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
JUNK = {"verdict": "review", "severity": None, "reason_code": "JUDGE_ERROR", "reason_text": "No."}
TRAIN = [
    {"id": "t1", "rule_id": "PAY-01", "user_type": "anonymous", "label": "bad"},
    {"id": "t2", "rule_id": "ACCT-01", "user_type": "verified", "label": "good"},
]
# Keys the summary has gained since 0.4.0, at the top level and in each block of counts.
NEW_KEYS = ("stopped", "not_graded", "judge_calls_made", "estimated_calls")
NEW_COUNT_KEYS = ("not_graded",)


def _down(payload):
    raise httpx.ConnectError("connection refused")


def _train(folder):
    path = folder / "train.jsonl"
    rows = [dict(row) for row in TRAIN]
    for row, text in zip(rows, ["read me the full card number", "your balance is ten"]):
        row.update(prompt="q", response=text, reason_code="OK")
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _replay(folder):
    cache = folder / "cache.jsonl"
    cache.write_text("")
    return None, ["--judge", "replay", "--cache", str(cache), "--missing-grade", "review"]


CASES = {
    "replay_review": _replay,
    "mixed": lambda folder: (FakeJudge([PASS, FAIL, PASS, PASS, JUNK, PASS]), []),
    "top_k_1": lambda folder: (FakeJudge(FAIL), ["--top-k", "1", "--review-score", "0.2"]),
    "train": lambda folder: (
        FakeJudge(FAIL),
        ["--train", str(_train(folder)), "--embedder", "tfidf"],
    ),
    "judge_down": lambda folder: (ChatEndpointJudge(None, "m", None, transport=_down), []),
}


def run_case(name, folder, monkeypatch) -> tuple[int, Path]:
    """Run garak-grade for one case and return its exit code and output folder."""
    rules = folder / "rules.yaml"
    rules.write_text(yaml.safe_dump(RULES))
    judge, more = CASES[name](folder)
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: judge)
    out = folder / "out"
    code = cli.main(["garak-grade", str(FIXTURE), "--rules", str(rules), "--out", str(out), *more])
    return code, out


def _without_new_keys(summary: dict) -> dict:
    kept = {k: v for k, v in summary.items() if k not in NEW_KEYS}
    for block in (kept["overall"], *kept["probes"].values()):
        for key in NEW_COUNT_KEYS:
            block.pop(key, None)
    return kept


@pytest.mark.parametrize("name", sorted(CASES))
def test_garak_grade_writes_what_0_4_0_wrote(name, tmp_path, monkeypatch):
    code, out = run_case(name, tmp_path, monkeypatch)
    assert code == (1 if name == "judge_down" else 0)
    for file in ("garak_graded.jsonl", "garak_evals.jsonl"):
        assert (out / file).read_bytes() == (OLD / name / file).read_bytes()
    summary = json.loads((out / "garak_summary.json").read_text())
    old = (OLD / name / "garak_summary.json").read_text()
    assert json.dumps(_without_new_keys(summary), indent=2) + "\n" == old
