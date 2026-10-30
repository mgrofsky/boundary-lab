"""Command line entry point for redline."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

CHUNK = 10


def load_env_file(path=".env") -> None:
    """Read KEY=VALUE lines from a .env file into the environment.

    Values already set in the environment win. Blank lines and lines starting with # are skipped.
    """
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(prefix: str) -> tuple[str, str, str]:
    names = [f"{prefix}_BASE_URL", f"{prefix}_MODEL", f"{prefix}_API_KEY"]
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SystemExit(f"redline: set {', '.join(missing)} in the environment or .env")
    return tuple(os.environ[n] for n in names)


def read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_jsonl(path, records) -> None:
    with Path(path).open("w") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)


def _in_chunks(records, fn, label):
    """Call fn on every 10 records and print one progress line after each batch."""
    out = []
    for start in range(0, len(records), CHUNK):
        out.extend(fn(records[start : start + CHUNK]))
        print(f"{label}: {len(out)} of {len(records)}", file=sys.stderr)
    return out


def cmd_run(args) -> int:
    from redline import pipeline, policy
    from redline.embed import Embedder
    from redline.judge import ChatEndpointJudge, ReplayJudge, select
    from redline.router import KMeansRouter, Router, ShortCircuit

    rules = policy.load(args.rules)
    train = read_jsonl(args.train)
    records = read_jsonl(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    examples = select(train)
    (out_dir / "judge_examples.json").write_text(json.dumps(examples, indent=2) + "\n")

    if args.judge == "live":
        load_env_file()
        base_url, model, api_key = _env("REDLINE_JUDGE")
        judge = ChatEndpointJudge(base_url, model, api_key, cache_path=args.cache)
    else:
        judge = ReplayJudge(args.cache)

    short_circuit = None
    if args.short_circuit:
        short_circuit = ShortCircuit(args.purity, args.min_members, args.margin)
    if args.router == "kmeans":
        router = KMeansRouter(Embedder(), k=args.k, seed=args.seed, short_circuit=short_circuit)
    else:
        router = Router(Embedder(), short_circuit=short_circuit)
    router.fit(train)

    # The clock starts after fit, so the embedding model is already loaded and the
    # seconds show grading time only.
    start = time.perf_counter()
    outputs = _in_chunks(
        records, lambda batch: pipeline.run(batch, rules, router, judge, examples), "graded"
    )
    seconds = time.perf_counter() - start

    stem = Path(args.data).stem
    out_path = out_dir / f"{stem}_outputs.jsonl"
    write_jsonl(out_path, outputs)
    meta = {
        "data": Path(args.data).as_posix(),
        "router": router.name,
        "k": args.k if args.router == "kmeans" else None,
        "short_circuit": args.short_circuit,
        "purity": args.purity,
        "min_members": args.min_members,
        "margin": args.margin,
        "records": len(outputs),
        "judge_calls": sum(1 for o in outputs if o["decided_by"] == "judge"),
        "short_circuits": sum(1 for o in outputs if o["short_circuit"]),
        "seconds": round(seconds, 2),
    }
    meta_path = out_dir / f"{stem}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {out_path} and {meta_path}", file=sys.stderr)
    return 0


def cmd_attack(args) -> int:
    from redline.target import Target, run_attacks

    load_env_file()
    base_url, model, api_key = _env("REDLINE_TARGET")
    system_prompt = Path(args.system).read_text()
    target = Target(base_url, model, api_key, system_prompt)
    prompts = read_jsonl(args.prompts)
    responses = _in_chunks(prompts, lambda batch: run_attacks(batch, target), "answered")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_path, responses)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


def cmd_report(args) -> int:
    from redline import report
    from redline.embed import DEFAULT_MODEL

    files = {
        "train": args.train,
        "holdout": args.holdout_labels,
        "attacks": args.attacks,
        "attack_labels": args.attack_labels,
        "holdout_outputs": args.holdout,
        "attack_outputs": args.attack,
    }
    data = {name: read_jsonl(path) for name, path in files.items()}
    counts = {Path(path).as_posix(): len(data[name]) for name, path in files.items()}
    # `redline run` writes <data stem>_meta.json next to <data stem>_outputs.jsonl.
    metas = {}
    for name, path in (("holdout", args.holdout), ("attack", args.attack)):
        path = Path(path)
        meta_path = path.with_name(path.stem.removesuffix("_outputs") + "_meta.json")
        metas[name] = json.loads(meta_path.read_text()) if meta_path.is_file() else None
    run_meta = metas if any(metas.values()) else None
    summary = report.build_summary(
        data["holdout_outputs"],
        data["holdout"],
        data["attack_outputs"],
        data["attack_labels"],
        data["attacks"],
        DEFAULT_MODEL,
        counts,
        args.cache,
        run_meta,
    )
    out_dir = Path(args.out)
    report.write_summary(summary, out_dir / "summary.json")
    report.write_report(summary, out_dir / "report.md")
    print(f"wrote {out_dir / 'summary.json'} and {out_dir / 'report.md'}", file=sys.stderr)
    return 0


def cmd_compare(args) -> int:
    from redline import report

    rows = report.build_comparison([Path(f) for f in args.runs])
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.render_comparison(rows))
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(rows, indent=2) + "\n")
    print(f"wrote {out_path} and {json_path}", file=sys.stderr)
    return 0


def cmd_readme(args) -> int:
    from redline import report

    summary = json.loads(Path(args.summary).read_text())
    if args.check:
        if not report.check_readme(args.readme, summary):
            print("README results table is out of date")
            return 1
        return 0
    report.update_readme(args.readme, summary)
    print(f"updated {args.readme}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redline",
        description=(
            "Test whether a chatbot follows its rules, and measure how well the test works."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="grade a file of answers")
    run.add_argument("--data", required=True, help="the answers to grade, one JSON record a line")
    run.add_argument("--judge", required=True, choices=["live", "replay"])
    run.add_argument(
        "--out", default="results/", help="folder for the output files (default: results/)"
    )
    run.add_argument("--train", default="data/train.jsonl")
    run.add_argument("--cache", default="data/judge_cache.jsonl")
    run.add_argument("--rules", default="policy/rules.yaml")
    run.add_argument("--router", choices=["center", "kmeans"], default="center")
    run.add_argument("--k", type=int, default=12, help="clusters for the kmeans router")
    run.add_argument(
        "--short-circuit", action="store_true", help="let the router decide clear answers"
    )
    run.add_argument("--purity", type=float, default=1.0, help="kmeans share of one label")
    run.add_argument("--min-members", type=int, default=5, help="fewest labeled answers needed")
    run.add_argument("--margin", type=float, default=0.10, help="center gap between good and bad")
    run.add_argument("--seed", type=int, default=0, help="kmeans random seed")
    run.set_defaults(func=cmd_run)

    attack = sub.add_parser("attack", help="send attack prompts to the model under test")
    attack.add_argument("--prompts", required=True)
    attack.add_argument("--out", required=True)
    attack.add_argument("--system", default="policy/target_system_prompt.txt")
    attack.set_defaults(func=cmd_attack)

    report = sub.add_parser("report", help="write the summary and the report")
    report.add_argument(
        "--holdout", default="results/holdout_outputs.jsonl", help="graded holdout answers"
    )
    report.add_argument(
        "--attack",
        default="results/attack_responses_outputs.jsonl",
        help="graded attack responses",
    )
    report.add_argument("--out", default="results/", help="folder for summary.json and report.md")
    report.add_argument("--holdout-labels", default="data/holdout.jsonl")
    report.add_argument("--attack-labels", default="data/runs/attack_labels.jsonl")
    report.add_argument("--attacks", default="data/attacks.jsonl")
    report.add_argument("--train", default="data/train.jsonl")
    report.add_argument("--cache", default="data/judge_cache.jsonl")
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("compare", help="write one table over several run folders")
    compare.add_argument("--runs", nargs="+", required=True, help="folders with summary and meta")
    compare.add_argument("--out", default="results/routers/comparison.md")
    compare.set_defaults(func=cmd_compare)

    readme = sub.add_parser("readme", help="update the README results table")
    readme.add_argument("--check", action="store_true", help="exit 1 if the table is out of date")
    readme.add_argument(
        "--summary", default="results/summary.json", help="the summary the table is built from"
    )
    readme.add_argument("--readme", default="redline/README.md")
    readme.set_defaults(func=cmd_readme)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
