"""Read command line options and run the requested Redline command."""

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
    from redline import api, pipeline
    from redline.judge import ChatEndpointJudge
    from redline.router import ShortCircuit

    records = read_jsonl(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    judge = None
    if args.judge == "live":
        load_env_file()
        base_url, model, api_key = _env("REDLINE_JUDGE")
        judge = ChatEndpointJudge(base_url, model, api_key, cache_path=args.cache)

    short_circuit = False
    if args.short_circuit:
        short_circuit = ShortCircuit(args.purity, args.min_members, args.margin)
    grader = api.Grader(
        rules=args.rules,
        train=args.train,
        judge=judge,
        router=args.router,
        k=args.k,
        short_circuit=short_circuit,
        seed=args.seed,
        cache=args.cache,
        embedder=args.embedder,
        top_k=args.top_k,
        floor=args.floor,
        missing_grade=args.missing_grade,
    )
    router = grader.router
    for record in records:
        try:
            grader.check_user_type(record["user_type"], record.get("id"))
        except ValueError as exc:
            raise SystemExit(f"redline: {exc}") from None
    (out_dir / "judge_examples.json").write_text(json.dumps(grader.examples, indent=2) + "\n")

    # Start timing after router setup and model loading, so this measures grading only.
    start = time.perf_counter()
    outputs = _in_chunks(records, grader.run, "graded")
    seconds = time.perf_counter() - start

    stem = Path(args.data).stem
    out_path = out_dir / f"{stem}_outputs.jsonl"
    write_jsonl(out_path, outputs)
    meta = {
        "data": Path(args.data).as_posix(),
        "router": router.name,
        "embedder": grader.embedder_name,
        "k": args.k if args.router == "kmeans" else None,
        "short_circuit": args.short_circuit,
        "purity": args.purity,
        "min_members": args.min_members,
        "margin": args.margin,
        "records": len(outputs),
        "top_k": args.top_k,
        "floor": args.floor,
        "judge_calls": pipeline.judge_calls(outputs),
        "short_circuits": sum(1 for o in outputs if o["short_circuit"]),
        "missing_grade": args.missing_grade,
        # Records whose kept outcome had no saved grade, and every lookup that had none.
        "no_saved_grade_records": sum(1 for o in outputs if o["reason_code"] == "NO_SAVED_GRADE"),
        "no_saved_grade_lookups": getattr(grader.judge, "missing_lookups", 0),
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
        report.embedding_name(run_meta),
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


def cmd_gate(args) -> int:
    from redline import thresholds

    path = Path(args.summary)
    if not path.is_file():
        raise SystemExit(f"redline: no summary at {path}, run redline report first")
    try:
        lines = thresholds.check(
            json.loads(path.read_text()),
            min_recall=args.min_recall,
            max_false_alarm=args.max_false_alarm,
            min_kappa=args.min_kappa,
            attack=args.attack,
            use_interval=args.use_interval,
        )
    except thresholds.NoCounts as exc:
        print(f"redline: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 1 if lines else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redline",
        description=(
            "Test whether a chatbot follows its rules, and measure how well the test works."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="grade a file of answers")
    run.add_argument("--data", required=True, help="answers to grade, one JSON record per line")
    run.add_argument(
        "--judge",
        required=True,
        choices=["live", "replay"],
        help="call a judge model (live) or read saved grades (replay)",
    )
    run.add_argument(
        "--out", default="results/", help="folder for the output files (default: results/)"
    )
    run.add_argument("--train", default="data/train.jsonl", help="labeled setup examples")
    run.add_argument("--cache", default="data/judge_cache.jsonl", help="file of saved judge grades")
    run.add_argument("--rules", default="policy/rules.yaml", help="YAML file containing your rules")
    run.add_argument(
        "--router",
        choices=["center", "kmeans"],
        default="center",
        help="match one center per rule or group similar examples with k-means",
    )
    run.add_argument("--k", type=int, default=12, help="number of groups for k-means (default: 12)")
    run.add_argument(
        "--short-circuit", action="store_true", help="let the router pass or fail some answers"
    )
    run.add_argument(
        "--purity",
        type=float,
        default=1.0,
        help="k-means shortcut: share of labels that must agree (default: 1.0, or all)",
    )
    run.add_argument(
        "--min-members",
        type=int,
        default=5,
        help="fewest examples needed in a shortcut group (default: 5)",
    )
    run.add_argument(
        "--margin",
        type=float,
        default=0.10,
        help="center shortcut: required gap between good and bad match scores (default: 0.10)",
    )
    run.add_argument("--seed", type=int, default=0, help="random seed for repeatable k-means runs")
    run.add_argument(
        "--embedder",
        choices=["minilm", "tfidf"],
        default="minilm",
        help="minilm, the default model, or tfidf, which needs no download but routes worse",
    )
    run.add_argument(
        "--missing-grade",
        choices=["stop", "review"],
        default="stop",
        help="replay: stop when no saved grade matches, or record review and go on (default: stop)",
    )
    run.add_argument(
        "--top-k",
        type=int,
        default=1,
        help="judge up to this many of the closest rules and keep the worst outcome (default: 1)",
    )
    run.add_argument(
        "--floor",
        type=float,
        default=None,
        help="with --top-k, skip extra rules whose match score is below this (default: none)",
    )
    run.set_defaults(func=cmd_run)

    attack = sub.add_parser("attack", help="send attack prompts to the model under test")
    attack.add_argument("--prompts", required=True, help="attack prompts, one JSON record per line")
    attack.add_argument("--out", required=True, help="file to write the chatbot's answers to")
    attack.add_argument(
        "--system", default="policy/target_system_prompt.txt", help="instructions for the chatbot"
    )
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
    report.add_argument(
        "--holdout-labels", default="data/holdout.jsonl", help="hand labels for the holdout answers"
    )
    report.add_argument(
        "--attack-labels",
        default="data/runs/attack_labels.jsonl",
        help="hand labels for the attack answers",
    )
    report.add_argument("--attacks", default="data/attacks.jsonl", help="original attack prompts")
    report.add_argument(
        "--train", default="data/train.jsonl", help="setup examples to count in the report"
    )
    report.add_argument(
        "--cache",
        default="data/judge_cache.jsonl",
        help="saved judge grades to identify in the report",
    )
    report.set_defaults(func=cmd_report)

    compare = sub.add_parser("compare", help="compare several runs in one table")
    compare.add_argument(
        "--runs", nargs="+", required=True, help="run folders with summaries and settings files"
    )
    compare.add_argument(
        "--out", default="results/routers/comparison.md", help="path for the comparison table"
    )
    compare.set_defaults(func=cmd_compare)

    readme = sub.add_parser("readme", help="update the README results table")
    readme.add_argument("--check", action="store_true", help="exit 1 if the table is out of date")
    readme.add_argument(
        "--summary", default="results/summary.json", help="the summary the table is built from"
    )
    readme.add_argument(
        "--readme", default="redline/README.md", help="README file containing the results markers"
    )
    readme.set_defaults(func=cmd_readme)

    gate = sub.add_parser("gate", help="exit 1 when the summary breaks a limit, for CI")
    gate.add_argument("--summary", default="results/summary.json", help="the summary to check")
    gate.add_argument(
        "--min-recall", type=float, help="lowest share of rule breaks caught that passes"
    )
    gate.add_argument(
        "--max-false-alarm",
        type=float,
        help="highest share of good answers marked fail that passes",
    )
    gate.add_argument(
        "--min-kappa", type=float, help="lowest agreement with the hand labels that passes"
    )
    gate.add_argument(
        "--attack",
        action="store_true",
        help="check the attack run's numbers instead of the holdout's",
    )
    gate.add_argument(
        "--use-interval",
        action="store_true",
        help="test recall on the low end of its 95%% interval and false alarms on the high end",
    )
    gate.set_defaults(func=cmd_gate)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
