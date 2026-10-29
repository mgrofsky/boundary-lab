"""`boundary-latch demo`: play the Northwind scenarios in the terminal."""

from __future__ import annotations

import argparse
import os
import sys

from .scenarios import BUILDERS, render_scenario, render_summary, run_one

DEFAULT_DB = "latch-demo.sqlite"
LIVE_MISSING = "live mode needs boundary-latch[live] and LATCH_BASE_URL"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db", default=DEFAULT_DB, help=f"SQLite file for the demo (default ./{DEFAULT_DB})"
    )
    parser.add_argument("--scenario", default=None, help="play one scenario by name")
    parser.add_argument("--pause", action="store_true", help="wait for Enter between scenarios")
    parser.add_argument("--live", action="store_true", help="talk to a live model instead")


def run_live_mode(args: argparse.Namespace) -> int:
    try:
        import httpx  # noqa: F401 the live extra

        from ..live import run_live
    except ImportError:
        run_live = None
    if run_live is None or not os.environ.get("LATCH_BASE_URL"):
        print(LIVE_MISSING, file=sys.stderr)
        return 2
    return run_live(args)


def run(args: argparse.Namespace) -> int:
    if args.live:
        return run_live_mode(args)
    if args.scenario is None:
        names = list(BUILDERS)
    elif args.scenario in BUILDERS:
        names = [args.scenario]
    else:
        print(f"unknown scenario {args.scenario}, pick one of: {', '.join(BUILDERS)}",
              file=sys.stderr)
        return 2
    played = []
    for number, name in enumerate(names, 1):
        if args.pause and number > 1:
            input("press Enter for the next scenario ")
        scenario = run_one(name, db_path=args.db)
        played.append(scenario)
        print(render_scenario(number, scenario), flush=True)
    print(render_summary(played))
    return 0 if all(scenario.passed for scenario in played) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="boundary-latch demo", description="Play the Northwind scenarios in the terminal."
    )
    add_arguments(parser)
    return run(parser.parse_args(argv))
