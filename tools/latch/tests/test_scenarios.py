import json
import os
import subprocess
import sys
from pathlib import Path

from boundary_latch.demo.scenarios import FakeClock, render_text, run_all, to_json

NAMES = [
    "allowed_lookup",
    "other_customer",
    "bad_recipient",
    "message_with_approval",
    "changed_after_approval",
    "duplicate_refund",
    "exhausted_limit",
    "timeout_after_refund",
    "expired_approval",
]

SRC = str(Path(__file__).resolve().parent.parent / "src")


def by_name():
    return {scenario.name: scenario for scenario in run_all()}


def test_nine_scenarios_match_their_expected_codes():
    scenarios = run_all()
    assert [s.name for s in scenarios] == NAMES
    for scenario in scenarios:
        assert scenario.observed == scenario.expected, scenario.name
        assert scenario.passed


def test_scenario_details():
    found = by_name()
    assert found["allowed_lookup"].expected == ["ALLOWED", "EXECUTED"]
    assert "A1 balance 100.00" in found["allowed_lookup"].steps[-1].state_change
    assert found["message_with_approval"].steps[-1].state_change == "outbox 1"
    assert found["changed_after_approval"].expected == [
        "NEEDS_APPROVAL",
        "APPROVED",
        "OPERATION_CONFLICT",
        "EXECUTED",
    ]
    duplicate = found["duplicate_refund"]
    assert duplicate.expected == ["ALLOWED", "EXECUTED", "ALREADY_RECORDED"]
    assert duplicate.steps[1].state_change == "A1 balance 100.00 -> 80.00"
    assert duplicate.steps[2].state_change == "nothing"
    assert found["exhausted_limit"].expected == ["ALLOWED", "RUN_LIMIT"]
    timeout = found["timeout_after_refund"]
    assert timeout.expected == ["ALLOWED", "UNCERTAIN", "UNCERTAIN"]
    assert "backend refunds 1" in timeout.steps[-1].state_change
    assert found["expired_approval"].expected[-1] == "APPROVAL_EXPIRED"


def test_actors_are_labeled_sample_identities():
    for scenario in run_all():
        for step in scenario.steps:
            assert "sample identity" in step.actor


def test_a_shared_clock_still_passes():
    clock = FakeClock()
    assert all(s.passed for s in run_all(clock=clock))
    assert clock.now > FakeClock().now


def test_to_json_round_trips():
    data = json.loads(to_json(run_all()))
    assert [item["name"] for item in data] == NAMES
    assert all(item["observed"] == item["expected"] for item in data)
    assert data[0]["steps"][0]["actor"].startswith("Sam")


def test_render_text_shows_every_step():
    text = render_text(run_all())
    assert "ALREADY_RECORDED" in text
    assert "OPERATION_CONFLICT" in text
    assert "RUN_LIMIT" in text
    assert "APPROVAL_EXPIRED" in text
    assert "UNCERTAIN" in text
    assert "sample identity" in text
    assert "9 of 9 scenarios matched" in text


def test_scenarios_command_exits_zero():
    env = {**os.environ, "PYTHONPATH": SRC}
    result = subprocess.run(
        [sys.executable, "-m", "boundary_latch", "scenarios", "--json"],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 9
