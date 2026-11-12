from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from importlib.resources import as_file, files
from pathlib import Path

from .demo.terminal import DEFAULT_DB
from .demo.terminal import add_arguments as add_demo_arguments
from .errors import ConfigError, StorageError
from .events import format_line, to_event
from .policy import LABELS, load_policy
from .store import read_audit

POLL_SECONDS = 1


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
    print(f"policy ok, {len(policy.tools)} tools, policy hash {policy.digest[:12]}")
    mode = policy.flow.all_three
    if mode != "allow":
        counts = ", ".join(
            f"{label} {sum(label in (tp.labels or ()) for tp in policy.tools.values())}"
            for label in LABELS
        )
        print(f'flow rule: all_three = "{mode}"; tools with each label: {counts}')
    return 0


def scenarios(as_json: bool) -> int:
    from .demo.scenarios import render_text, run_all, to_json

    results = run_all()
    print(to_json(results) if as_json else render_text(results))
    return 0 if all(scenario.passed for scenario in results) else 1


def audit(path: str, org: str | None, last: int | None, follow: bool, as_json: bool) -> int:
    if not Path(path).is_file():
        print(f"no Latch database at {path}", file=sys.stderr)
        return 1

    def show(rows: list[dict]) -> int:
        for row in rows:
            event = to_event(row)
            print(json.dumps(event) if as_json else format_line(event), flush=True)
        return rows[-1]["id"] if rows else 0

    try:
        seen = show(read_audit(path, org, last=last))
        while follow:
            time.sleep(POLL_SECONDS)
            seen = show(read_audit(path, org, after_id=seen)) or seen
    except KeyboardInterrupt:
        return 0
    except StorageError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def init(directory: str) -> int:
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        print("the destination folder must be empty", file=sys.stderr)
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


def serve(port: int, db: str, live: bool) -> int:
    try:
        from .demo import server

        server.serve(port=port, db=db, live=live)
    except ImportError:
        print("the browser demo needs boundary-latch[demo]", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="boundary-latch", description="Check AI tool calls before they run."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="check a policy file")
    check_cmd.add_argument("policy", help="path to a TOML policy file")
    demo_cmd = commands.add_parser("demo", help="play the Northwind scenarios in the terminal")
    add_demo_arguments(demo_cmd)
    serve_cmd = commands.add_parser("serve", help="run the browser demo on 127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    serve_cmd.add_argument(
        "--db", default=DEFAULT_DB, help=f"SQLite file for the demo (default ./{DEFAULT_DB})"
    )
    serve_cmd.add_argument("--live", action="store_true", help="let a live model take turns")
    scenarios_cmd = commands.add_parser("scenarios", help="run the built-in scenarios")
    scenarios_cmd.add_argument("--json", action="store_true", help="print JSON")
    audit_cmd = commands.add_parser("audit", help="print the audit rows from a Latch database")
    audit_cmd.add_argument("path", help="path to the SQLite file")
    audit_cmd.add_argument("--org", default=None, help="only rows for this organization")
    audit_cmd.add_argument("--last", type=int, default=None, metavar="N",
                           help="only the newest N rows")
    audit_cmd.add_argument("--follow", action="store_true",
                           help="keep printing new rows until interrupted")
    audit_cmd.add_argument("--json", action="store_true", help="print one JSON object per line")
    mcp_cmd = commands.add_parser(
        "mcp", help="check every tool call between an MCP client and an MCP server",
        usage="boundary-latch mcp --policy policy.toml [options] -- <server command> [args...]",
    )
    from .mcp import add_arguments as add_mcp_arguments

    add_mcp_arguments(mcp_cmd)
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
    if args.command == "audit":
        return audit(args.path, args.org, args.last, args.follow, args.json)
    if args.command == "mcp":
        from .mcp import run as run_mcp

        return run_mcp(args)
    if args.command == "serve":
        return serve(args.port, args.db, args.live)
    from .demo.terminal import run as run_demo

    return run_demo(args)
