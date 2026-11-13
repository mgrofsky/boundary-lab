"""Two cases an automated review of the 0.3.0 pull request found."""

import hashlib
from pathlib import Path

from redline import cli
from redline.target import attempt_id_clashes, run_attacks

PINNED = Path(__file__).parent.parent / "data" / "judge_cache.jsonl"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_live_evade_with_out_set_to_the_data_folder_is_refused(monkeypatch, capsys):
    """Without --cache the default is a file under --out, which must not be the pinned one."""
    monkeypatch.chdir(PINNED.parent.parent)
    before = sha(PINNED)
    code = cli.main(["evade", "--data", "data/holdout.jsonl", "--judge", "live", "--out", "data"])
    assert code == 2
    assert "must not write" in capsys.readouterr().err
    assert sha(PINNED) == before


def test_attempt_ids_that_clash_with_prompt_ids_are_found():
    prompts = [{"id": "a"}, {"id": "a#2"}, {"id": "b"}]
    assert attempt_id_clashes(prompts, 1) == []
    assert attempt_id_clashes(prompts, 2) == ["a#2"]
    assert attempt_id_clashes([{"id": "a"}, {"id": "b"}], 3) == []


def test_attack_with_clashing_ids_stops_before_any_prompt_is_sent(tmp_path, monkeypatch, capsys):
    prompts = tmp_path / "prompts.jsonl"
    prompts.write_text(
        '{"id": "a", "kind": "k", "target_rule_id": "SYS-01", "prompt": "one"}\n'
        '{"id": "a#2", "kind": "k", "target_rule_id": "SYS-01", "prompt": "two"}\n'
    )
    system = tmp_path / "system.txt"
    system.write_text("You are a test.")
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.setenv(f"REDLINE_TARGET_{name}", "http://unused" if name == "BASE_URL" else "x")
    sent = []
    monkeypatch.setattr("redline.target.Target.answer", lambda self, prompt: sent.append(prompt))
    out = tmp_path / "out.jsonl"
    code = cli.main(
        [
            "attack",
            "--prompts",
            str(prompts),
            "--system",
            str(system),
            "--out",
            str(out),
            "--attempts",
            "2",
        ]
    )
    assert code == 2
    assert "a#2" in capsys.readouterr().err
    assert sent == []
    assert not out.exists()


def test_attempt_ids_stay_distinct_without_a_clash():
    class Echo:
        def answer(self, prompt):
            return prompt

    prompts = [{"id": "a", "kind": "k", "target_rule_id": "r", "prompt": "p"}]
    rows = run_attacks(prompts, Echo(), attempts=3)
    assert [r["id"] for r in rows] == ["a", "a#2", "a#3"]
