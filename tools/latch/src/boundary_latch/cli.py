from __future__ import annotations

import argparse
import sys

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
    commands.add_parser("scenarios", help="run the built in scenarios")
    commands.add_parser("init", help="copy the starter project")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return check(args.policy)
    return not_available(args.command)
