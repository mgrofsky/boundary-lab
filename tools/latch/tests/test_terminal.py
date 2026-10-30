import os
import subprocess
import sys
from pathlib import Path

from boundary_latch.demo import terminal

SRC = str(Path(__file__).resolve().parent.parent / "src")

TITLES = [
    "Sam looks up an account Sam owns",
    "Sam asks for another customer's account",
    "The model tries to email an outside address",
    "A message goes out after a supervisor approves",
    "The refund amount changes after approval, using the same operation ID",
    "The same refund is submitted twice",
    "One run asks for more actions than the policy allows",
    "The refund goes through, but the reply is too slow",
    "An approval expires before the tool runs",
]


def run_demo(*args: str, cwd: str | None = None, env: dict | None = None, stdin: str = ""):
    return subprocess.run(
        [sys.executable, "-m", "boundary_latch", "demo", *args],
        capture_output=True,
        check=False,
        cwd=cwd,
        env=env or {**os.environ, "PYTHONPATH": SRC},
        input=stdin,
        text=True,
        timeout=60,
    )


def test_demo_plays_every_scenario():
    result = run_demo("--db", ":memory:")
    assert result.returncode == 0, result.stderr
    for text in ("ALREADY_RECORDED", "RUN_LIMIT", "UNCERTAIN", "example user",
                 "OPERATION_CONFLICT", "APPROVAL_EXPIRED"):
        assert text in result.stdout, text
    for title in TITLES:
        assert title in result.stdout, title


def test_each_step_prints_four_lines(capsys):
    assert terminal.main(["--db", ":memory:", "--scenario", "duplicate_refund"]) == 0
    lines = capsys.readouterr().out.splitlines()
    start = lines.index("who: Sam (support agent, example user)")
    assert lines[start + 1].startswith("asked for: ")
    assert lines[start + 2].startswith("result ALLOWED: ")
    assert lines[start + 3] == "change: nothing"
    assert lines[start + 4] == ""
    assert "change: A1 balance 100.00 -> 80.00" in lines
    assert any(line.startswith("result ALREADY_RECORDED: ") for line in lines)


def test_one_scenario_by_name(capsys):
    assert terminal.main(["--db", ":memory:", "--scenario", "bad_recipient"]) == 0
    out = capsys.readouterr().out
    assert "RECIPIENT_NOT_ALLOWED" in out
    assert "ALREADY_RECORDED" not in out


def test_unknown_scenario_is_refused(capsys):
    assert terminal.main(["--db", ":memory:", "--scenario", "nope"]) == 2
    assert "unknown scenario nope" in capsys.readouterr().err


def test_pause_waits_for_enter_between_scenarios():
    result = run_demo("--db", ":memory:", "--pause", stdin="\n" * 9)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("press Enter") == 8


def test_a_file_database_survives_a_second_run(tmp_path):
    db = str(tmp_path / "demo.sqlite")
    assert run_demo("--db", db).returncode == 0
    second = run_demo("--db", db)
    assert second.returncode == 0, second.stdout
    assert "9 of 9 scenarios matched" in second.stdout


def test_default_database_is_in_the_current_directory(tmp_path):
    result = run_demo("--scenario", "allowed_lookup", cwd=str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "latch-demo.sqlite").is_file()


def test_live_without_setup_says_what_it_needs():
    env = {k: v for k, v in os.environ.items() if not k.startswith("LATCH_")}
    env["PYTHONPATH"] = SRC
    result = run_demo("--db", ":memory:", "--live", env=env)
    assert result.returncode == 2
    assert "live mode needs boundary-latch[live] and LATCH_BASE_URL" in result.stderr
