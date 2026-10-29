from __future__ import annotations

import argparse
import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path

from .errors import ConfigError
from .policy import load_policy


def check(path: str) -> int:
    try:
        policy = load_policy(path)
    except ConfigError as exc:
        message = str(exc).removeprefix("policy rejected: ")
        print(f"policy rejected: {message}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"policy rejected: could not read {path}: {exc.strerror}", file=sys.stderr)
        return 1
    print(f"policy ok, {len(policy.tools)} tools, digest {policy.digest[:12]}")
    return 0


def scenarios(as_json: bool) -> int:
    from .demo.scenarios import render_text, run_all, to_json

    results = run_all()
    print(to_json(results) if as_json else render_text(results))
    return 0 if all(scenario.passed for scenario in results) else 1


def init(directory: str) -> int:
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        print("refusing to write into a nonempty directory", file=sys.stderr)
        return 1
    with as_file(files("boundary_latch") / "starter") as starter:
        shutil.copytree(
            starter, target, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in sorted(p.relative_to(target).as_posix() for p in target.rglob("*")
                       if p.is_file()):
        print(name)
    return 0


def not_available(name: str) -> int:
    print(f"boundary-latch {name} is not available yet")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boundary-latch", description="Check AI tool calls before they run."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="validate a policy file")
    check_cmd.add_argument("policy", help="path to a policy TOML file")
    commands.add_parser("demo", help="play the Northwind scenarios in the terminal")
    commands.add_parser("serve", help="run the browser demo on 127.0.0.1")
    scenarios_cmd = commands.add_parser("scenarios", help="run the built in scenarios")
    scenarios_cmd.add_argument("--json", action="store_true", help="print JSON")
    init_cmd = commands.add_parser("init", help="copy the starter project")
    init_cmd.add_argument("directory", help="where to write the starter project")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return check(args.policy)
    if args.command == "scenarios":
        return scenarios(args.json)
    if args.command == "init":
        return init(args.directory)
    return not_available(args.command)
