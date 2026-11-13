"""Check an install of Redline with no extras. CI runs this, and so can you.

Install Redline into a fresh environment with no extras, change to a folder outside
the repository, and run this file with that environment's Python. It exits 0 when
rules only mode works there and the model libraries are absent.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "fixtures" / "garak_report.jsonl"
RULES = HERE.parent / "docs" / "integrations" / "aegisshield" / "rules-llm-endpoint-starter.yaml"
PASS = {"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "ok"}
# The package is not on the public index, so the hint names the repository.
HINT = 'pip install "boundary-redline[minilm] @ git+https://github.com/mgrofsky/boundary-lab"'


def redline(*args) -> subprocess.CompletedProcess:
    command = [sys.executable, "-m", "redline", *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main() -> int:
    from importlib import metadata

    import redline as package
    from redline import Grader, embed
    from redline.garak import grade_report
    from redline.judge import FakeJudge

    assert HERE.parent not in Path(package.__file__).resolve().parents, "not the installed copy"
    # The installed package is boundary-redline. The import name and the command are redline.
    assert metadata.version("boundary-redline")
    owners = metadata.packages_distributions().get("redline")
    assert owners == ["boundary-redline"], owners
    for name in ("torch", "sentence_transformers", "garak"):
        assert importlib.util.find_spec(name) is None, f"{name} is installed"

    judge = FakeJudge(PASS)
    row = Grader.from_rules(RULES, judge).grade("a question", "a plain answer", "anonymous")
    assert row["outcome"] == "pass" and len(judge.calls) == 7, row

    summary = grade_report(REPORT, RULES, FakeJudge(PASS))
    assert summary["overall"]["graded"] == 4 and summary["stopped"] is None, summary

    dry = redline("garak-grade", str(REPORT), "--rules", str(RULES), "--dry-run")
    assert dry.returncode == 0 and "at most 21 judge calls" in dry.stdout, dry

    try:
        embed.make("minilm")
    except embed.MissingExtra as exc:
        assert HINT in str(exc), exc
    else:
        raise AssertionError("the minilm embedder was built with no extra installed")

    with tempfile.TemporaryDirectory() as folder:
        data, cache, out = (Path(folder) / name for name in ("answers.jsonl", "cache.jsonl", "out"))
        record = {"id": "a1", "user_type": "anonymous", "prompt": "hello", "response": "hi there"}
        data.write_text(json.dumps(record) + "\n")
        cache.write_text("")
        shared = ("run", "--data", str(data), "--out", str(out), "--rules", str(RULES))
        replay = ("--judge", "replay", "--cache", str(cache), "--missing-grade", "review")
        ran = redline(*shared, "--rules-only", *replay)
        assert ran.returncode == 0, ran
        [graded] = [json.loads(line) for line in (out / "answers_outputs.jsonl").read_text().splitlines()]
        assert graded["mode"] == "rules_only" and graded["outcome"] == "review", graded
        # The calibrated mode defaults to minilm, so here it stops with one plain line.
        calibrated = redline(*shared, "--train", str(cache), *replay)
        assert calibrated.returncode == 2, calibrated
        assert calibrated.stderr.strip().count("\n") == 0, calibrated
        assert HINT in calibrated.stderr, calibrated

    print("plain install ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
