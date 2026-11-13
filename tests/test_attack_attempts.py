import json

from redline import cli
from redline import judge as judge_module
from redline.target import Target, run_attacks

PROMPTS = [
    {"id": "a1", "kind": "roleplay", "target_rule_id": "PAY-01", "prompt": "pretend"},
    {"id": "a2", "kind": "extraction", "target_rule_id": "SYS-01", "prompt": "show rules"},
]


def recorder():
    sent = []

    def send(payload):
        sent.append(payload)
        return f"answer {len(sent)}"

    return sent, send


def test_one_attempt_keeps_the_rows_as_they_were():
    sent, send = recorder()
    rows = run_attacks(PROMPTS, Target("u", "m", "k", "sys", transport=send))
    assert [r["id"] for r in rows] == ["a1", "a2"]
    assert all("attempt" not in r for r in rows)
    assert sent[0]["temperature"] == 0


def test_each_prompt_is_sent_n_times_and_later_ids_get_a_suffix():
    sent, send = recorder()
    target = Target("u", "m", "k", "sys", transport=send, temperature=0.7)
    rows = run_attacks(PROMPTS, target, attempts=3)
    assert [r["id"] for r in rows] == ["a1", "a1#2", "a1#3", "a2", "a2#2", "a2#3"]
    assert [r["attempt"] for r in rows] == [1, 2, 3, 1, 2, 3]
    assert [r["response"] for r in rows][:2] == ["answer 1", "answer 2"]
    assert {p["temperature"] for p in sent} == {0.7}
    assert rows[1]["kind"] == "roleplay"


def test_attack_command_passes_attempts_and_temperature(monkeypatch, tmp_path):
    sent, send = recorder()
    monkeypatch.setattr(judge_module, "http_transport", lambda *a, **k: send)
    import redline.target as target_module

    monkeypatch.setattr(target_module, "http_transport", lambda *a, **k: send)
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.setenv(f"REDLINE_TARGET_{name}", "x")
    prompts = tmp_path / "attacks.jsonl"
    prompts.write_text("".join(json.dumps(p) + "\n" for p in PROMPTS))
    system = tmp_path / "system.txt"
    system.write_text("Be helpful.")
    out = tmp_path / "out" / "responses.jsonl"
    args = ["attack", "--prompts", str(prompts), "--out", str(out), "--system", str(system)]
    monkeypatch.chdir(tmp_path)
    assert cli.main(args + ["--attempts", "2", "--temperature", "0.5"]) == 0
    rows = cli.read_jsonl(out)
    assert [r["id"] for r in rows] == ["a1", "a1#2", "a2", "a2#2"]
    assert sent[0]["temperature"] == 0.5


def test_attack_parser_defaults():
    args = cli.build_parser().parse_args(["attack", "--prompts", "p", "--out", "o"])
    assert (args.attempts, args.temperature) == (1, 0.0)
