"""Command line entry points for Dye."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .demo.scenarios import run_all
from .tracker import Tracker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boundary-dye", description="Find tool call arguments copied from untrusted text"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run the Northwind scenarios and explain each result")
    scenarios = sub.add_parser("scenarios", help="Print the scenario results")
    scenarios.add_argument("--json", action="store_true", help="Print JSON instead of lines")
    check = sub.add_parser("check", help="Check arguments against one or more text files")
    check.add_argument(
        "--source", action="append", required=True, metavar="LABEL=PATH",
        help="A file of untrusted text; repeat for more files",
    )
    check.add_argument(
        "--args", required=True, help="The proposed arguments as JSON, or @path to a JSON file"
    )
    check.add_argument("--min-length", type=int, default=4, help="Shortest value to track")
    return parser


def _load_args(spec: str) -> dict[str, Any]:
    text = Path(spec[1:]).read_text() if spec.startswith("@") else spec
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("--args must be a JSON object")
    return data


def cmd_check(ns: argparse.Namespace) -> int:
    tracker = Tracker(min_length=ns.min_length)
    for item in ns.source:
        label, sep, path = item.partition("=")
        if not sep or not label or not path:
            raise SystemExit(f"--source needs LABEL=PATH, got {item!r}")
        tracker.mark(label, Path(path).read_text())
    report = tracker.check(_load_args(ns.args))
    print(json.dumps(report.to_dict(), indent=2))
    return 1 if report.findings else 0


def cmd_scenarios(ns: argparse.Namespace) -> int:
    results = run_all()
    if ns.json:
        print(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            mark = "ok " if result.passed else "BAD"
            print(f"{mark} {result.name:<22} tainted={sorted(result.report.tainted)}")
    return 0 if all(result.passed for result in results) else 1


def cmd_demo(ns: argparse.Namespace) -> int:
    for result in run_all():
        scenario = result.scenario
        print(f"== {scenario.title}")
        for label, text in scenario.sources:
            print(f"   {label}: {text}")
        print(f"   model proposes {scenario.tool}({json.dumps(scenario.arguments)})")
        if result.report.findings:
            for finding in result.report.findings:
                print(
                    f"   tainted {finding.path} = {finding.value!r} came from "
                    f"{finding.source} ({finding.matcher} match)"
                )
        else:
            print("   no argument came from the marked text")
        print(f"   {scenario.note}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    commands = {"demo": cmd_demo, "scenarios": cmd_scenarios, "check": cmd_check}
    return commands[ns.command](ns)
