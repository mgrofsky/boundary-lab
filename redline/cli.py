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
        done = min(start + CHUNK, len(records))
        print(f"{label}: {done} of {len(records)}", file=sys.stderr)
    return out


# The saved grades the published results were made from. summary.json pins its hash.
PINNED_CACHE = "data/judge_cache.jsonl"


def is_pinned_cache(path) -> bool:
    """True when path is the saved judge cache in this folder or in the repo Redline came from."""
    if not path:
        return False
    here = Path(path).resolve()
    repo = Path(__file__).resolve().parent.parent / PINNED_CACHE
    return here in (Path(PINNED_CACHE).resolve(), repo.resolve())


class _Given(argparse.Action):
    """Store an option and note that it was given, so a default can differ by mode."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        namespace.given = {*getattr(namespace, "given", ()), self.dest}


def _rules_only_defaults(args) -> None:
    """With --rules-only, an option left out means no cache, tfidf and every rule."""
    given = getattr(args, "given", ())
    if "cache" not in given:
        args.cache = None
    if "embedder" not in given:
        args.embedder = "tfidf"
    if "top_k" not in given:
        args.top_k = None


def _live_judge(cache, save: bool = True, **retry):
    """The judge model named by the REDLINE_JUDGE settings. retry may set tries and backoff."""
    from redline.judge import ChatEndpointJudge

    load_env_file()
    base_url, model, api_key = _env("REDLINE_JUDGE")
    return ChatEndpointJudge(base_url, model, api_key, cache_path=cache, save=save, **retry)


def _rules_only_problem(args) -> str | None:
    """Say why these options cannot be used with --rules-only, or None when they can."""
    if args.short_circuit:
        return "--rules-only has no labeled examples, so --short-circuit cannot be used with it."
    if args.floor is not None:
        return (
            "--floor is not used with --rules-only. Use --top-k to limit how many rules "
            "the judge checks."
        )
    if args.judge == "replay" and args.cache is None:
        return "--rules-only with --judge replay needs --cache, a file of saved judge grades."
    if args.judge == "live" and is_pinned_cache(args.cache):
        return (
            f"--rules-only with a live judge must not write to {PINNED_CACHE}, the saved "
            "grades the published results were made from. Pass another file or leave out --cache."
        )
    return None


def _rules_only_grader(args, attempts: int = 1):
    """Build a Grader from the rules file alone. The train file is not read."""
    from redline import api

    judge = _live_judge(args.cache) if args.judge == "live" else None
    return api.Grader.from_rules(
        args.rules,
        judge,
        top_k=args.top_k,
        embedder=args.embedder,
        cache=args.cache,
        missing_grade=args.missing_grade,
        attempts=attempts,
    )


def _grader(args, attempts: int = 1):
    """Build the Grader that run and evade use, from their shared options."""
    from redline import api
    from redline.router import ShortCircuit

    judge = None
    if args.judge == "live":
        # Repeat runs may read the saved grades but must not add to them.
        judge = _live_judge(args.cache, save=not (attempts > 1 and is_pinned_cache(args.cache)))

    short_circuit = False
    if args.short_circuit:
        short_circuit = ShortCircuit(args.purity, args.min_members, args.margin)
    return api.Grader(
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
        attempts=attempts,
    )


def cmd_run(args) -> int:
    from redline import pipeline

    if args.attempts < 1:
        print("redline: --attempts must be at least 1", file=sys.stderr)
        return 2
    if args.attempts > 1 and args.judge != "live":
        print(
            "redline: --attempts needs --judge live. Replay has one saved grade per answer, "
            "so a repeat would only read the same grade again.",
            file=sys.stderr,
        )
        return 2
    if args.rules_only:
        _rules_only_defaults(args)
        problem = _rules_only_problem(args)
        if problem:
            print(f"redline: {problem}", file=sys.stderr)
            return 2
    records = read_jsonl(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.rules_only:
        grader = _rules_only_grader(args, args.attempts)
    else:
        grader = _grader(args, args.attempts)
    router = grader.router
    for record in records:
        try:
            grader.check_user_type(record["user_type"], record.get("id"))
        except ValueError as exc:
            raise SystemExit(f"redline: {exc}") from None
    if not args.rules_only:
        examples = json.dumps(grader.examples, indent=2)
        (out_dir / "judge_examples.json").write_text(examples + "\n")

    # Start timing after router setup and model loading, so this measures grading only.
    start = time.perf_counter()
    outputs = _in_chunks(records, grader.run, "graded")
    seconds = time.perf_counter() - start

    stem = Path(args.data).stem
    out_path = out_dir / f"{stem}_outputs.jsonl"
    write_jsonl(out_path, outputs)
    # Rules only mode has no router shortcut, so its settings file leaves these out.
    shortcut = {name: getattr(args, name) for name in ("purity", "min_members", "margin")}
    if args.rules_only:
        shortcut = {}
    meta = {
        "data": Path(args.data).as_posix(),
        "router": router.name,
        "embedder": grader.embedder_name,
        "k": args.k if args.router == "kmeans" and not args.rules_only else None,
        "short_circuit": args.short_circuit,
        **shortcut,
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
    if args.attempts > 1:
        meta["attempts"] = args.attempts
    if args.rules_only:
        meta["mode"] = grader.mode
    meta_path = out_dir / f"{stem}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {out_path} and {meta_path}", file=sys.stderr)
    return 0


def cmd_attack(args) -> int:
    from redline.target import Target, attempt_id_clashes, run_attacks

    if args.attempts < 1:
        print("redline: --attempts must be at least 1", file=sys.stderr)
        return 2
    load_env_file()
    base_url, model, api_key = _env("REDLINE_TARGET")
    system_prompt = Path(args.system).read_text()
    target = Target(base_url, model, api_key, system_prompt, temperature=args.temperature)
    prompts = read_jsonl(args.prompts)
    clashes = attempt_id_clashes(prompts, args.attempts)
    if clashes:
        print(
            "redline: with --attempts these prompt ids would be given to two answers: "
            f"{', '.join(clashes)}. Rename the prompts so no id ends in # and a number.",
            file=sys.stderr,
        )
        return 2
    responses = _in_chunks(
        prompts, lambda batch: run_attacks(batch, target, args.attempts), "answered"
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_path, responses)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


def cmd_evade(args) -> int:
    from redline import evade

    if args.rounds < 1:
        print("redline: --rounds must be at least 1", file=sys.stderr)
        return 2
    if args.judge == "replay" and args.missing_grade == "stop":
        print(
            "redline: evade in replay mode needs --missing-grade review. The saved cache has "
            "no grades for rewritten answers, so the run would stop at the first one.",
            file=sys.stderr,
        )
        return 2
    if args.cache is None:
        live = args.judge == "live"
        args.cache = str(Path(args.out) / "judge_cache.jsonl") if live else PINNED_CACHE
    # Checked after the default is filled in, so --out data cannot reach the pinned file.
    if args.judge == "live" and is_pinned_cache(args.cache):
        print(
            f"redline: evade with a live judge must not write to {PINNED_CACHE}, the saved "
            "grades the published results were made from. Pass --cache with another file, "
            "or an --out folder other than the one that holds it.",
            file=sys.stderr,
        )
        return 2
    attacker = None
    if args.attacker:
        load_env_file()
        base_url, model, api_key = _env("REDLINE_ATTACKER")
        attacker = evade.Attacker(base_url, model, api_key)
    records = read_jsonl(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    grader = _grader(args)
    for record in records:
        try:
            grader.check_user_type(record["user_type"], record.get("id"))
        except ValueError as exc:
            raise SystemExit(f"redline: {exc}") from None

    rows, summary, candidates = evade.run(
        records,
        grader,
        max_judge_calls=args.max_judge_calls,
        attacker=attacker,
        rounds=args.rounds,
    )
    summary = {
        "data": Path(args.data).as_posix(),
        "judge": args.judge,
        "router": grader.router.name,
        "embedder": grader.embedder_name,
        "top_k": args.top_k,
        "missing_grade": args.missing_grade,
        **summary,
    }
    written = [out_dir / "evade_outputs.jsonl", out_dir / "evade_summary.json"]
    write_jsonl(written[0], rows)
    written[1].write_text(json.dumps(summary, indent=2) + "\n")
    if attacker is not None:
        written.append(out_dir / "evade_candidates.jsonl")
        write_jsonl(written[2], candidates)
    if summary["stopped"]:
        print(f"redline: {summary['stopped']}", file=sys.stderr)
    print(f"wrote {', '.join(str(p) for p in written)}", file=sys.stderr)
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
    if args.evade_labels:
        from redline import evade

        summary["confirmed_adaptive"] = evade.confirmed(read_jsonl(args.evade_labels))
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


def _from_0_to_1(text: str) -> float:
    """Read an option that must be a number from 0 to 1."""
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"expected a number from 0 to 1, got {text!r}")
    return value


def _garak_dry_run(args) -> int:
    """Print what grading the report would cost. No judge is set up.

    It checks what a real run checks before its first judge call, apart from the
    judge's own settings and --out: the rules, the user type, the train file, the
    numbers, the report, and that the embedder can be built.
    """
    from redline import embed
    from redline.garak import report

    try:
        _, attempts, per_answer = report.plan(
            args.report, args.rules, args.top_k, args.train, args.user_type, args.max_judge_calls
        )
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"redline: {exc}") from None
    # Building an embedder loads no model. It only finds out whether it is installed.
    embed.make(args.embedder or report.default_embedder(args.train))
    estimate = report.estimate(attempts, per_answer)
    reasons = estimate["skipped_by_reason"]
    print(
        f"{Path(args.report).name}: {estimate['answers']} answers to grade, "
        f"{estimate['skipped']} skipped ({reasons['no_text']} with no text, "
        f"{reasons['blank']} blank, {reasons['wrong_language']} in another language), "
        f"at most {estimate['judge_calls']} judge calls"
    )
    return 0


def cmd_garak_grade(args) -> int:
    from redline.garak import report
    from redline.judge import MissingSavedGrade, ReplayJudge

    if args.dry_run:
        return _garak_dry_run(args)
    if args.out is None:
        print("redline: garak-grade needs --out, a folder for the three output files", file=sys.stderr)
        return 2
    if args.judge == "replay" and args.cache is None:
        print(
            "redline: --judge replay needs --cache, a file of saved judge grades", file=sys.stderr
        )
        return 2
    if args.judge == "live" and is_pinned_cache(args.cache):
        print(
            f"redline: garak-grade with a live judge must not write to {PINNED_CACHE}, the "
            "saved grades the published results were made from. Pass another file.",
            file=sys.stderr,
        )
        return 2
    try:
        if args.judge == "live":
            judge = _live_judge(args.cache, tries=report.JUDGE_TRIES, backoff=report.JUDGE_BACKOFF)
        else:
            judge = ReplayJudge(args.cache, missing=args.missing_grade)
        result = report.grade_report(
            args.report,
            args.rules,
            judge,
            out_dir=args.out,
            user_type=args.user_type,
            top_k=args.top_k,
            review_score=args.review_score,
            eval_threshold=args.eval_threshold,
            train=args.train,
            max_judge_calls=args.max_judge_calls,
            embedder=args.embedder,
        )
    except MissingSavedGrade as exc:
        # Only this case gets the hint. Any other KeyError is a fault and is not caught.
        raise SystemExit(
            f"redline: {exc.args[0]}. Pass --missing-grade review to record review and go on."
        ) from None
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"redline: {exc}") from None
    paths = [Path(args.out) / name for name in report.OUTPUT_FILES]
    print(f"wrote {', '.join(str(p) for p in paths)}", file=sys.stderr)
    untranslated = result["attempts_without_back_translation"]
    if untranslated:
        was = "attempt was" if untranslated == 1 else "attempts were"
        print(
            f"redline: {untranslated} {was} not graded. The prompt was not sent in English "
            "and the report holds no back translation of the outputs.",
            file=sys.stderr,
        )
    code = 0
    left = result["not_graded"]["count"]
    if left:
        # The cap is the one stop this command can ask for that leaves outputs behind.
        # Any other one ends in an exception before this line.
        were = "output was" if left == 1 else "outputs were"
        print(
            f"redline: warning: {left} {were} not graded because the run reached "
            "--max-judge-calls. They are not passes and not hits.",
            file=sys.stderr,
        )
        code = 1
    unavailable = result["overall"]["judge_unavailable"]
    if unavailable:
        were = "output was" if unavailable == 1 else "outputs were"
        print(
            f"redline: warning: {unavailable} {were} not evaluated because the judge model "
            "could not be reached. They are not passes and not hits. Run again when the "
            "judge answers.",
            file=sys.stderr,
        )
        code = 1
    return code


def _garak_shims(args, action: str) -> int:
    """Run install or uninstall from redline.garak.install on the garak in this environment."""
    from redline.garak import install

    package_dir = install.find_garak()
    if package_dir is None:
        print("redline: garak is not installed in this environment", file=sys.stderr)
        return 2
    try:
        code, lines = getattr(install, action)(package_dir, dry_run=args.dry_run)
    except OSError as exc:
        print(f"redline: garak-{action} could not finish: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return code


def cmd_garak_install(args) -> int:
    return _garak_shims(args, "install")


def cmd_garak_uninstall(args) -> int:
    return _garak_shims(args, "uninstall")


def _grading_options(
    parser, cache_default=PINNED_CACHE, cache_help="file of saved judge grades"
) -> None:
    """Options for the rules, router and judge, shared by run and evade.

    cache, embedder and top_k note when they are given, since --rules-only has its own
    defaults for them.
    """
    parser.add_argument("--train", default="data/train.jsonl", help="labeled setup examples")
    parser.add_argument("--cache", default=cache_default, action=_Given, help=cache_help)
    parser.add_argument(
        "--rules", default="policy/rules.yaml", help="YAML file containing your rules"
    )
    parser.add_argument(
        "--router",
        choices=["center", "kmeans"],
        default="center",
        help="match one center per rule or group similar examples with k-means",
    )
    parser.add_argument(
        "--k", type=int, default=12, help="number of groups for k-means (default: 12)"
    )
    parser.add_argument(
        "--short-circuit", action="store_true", help="let the router pass or fail some answers"
    )
    parser.add_argument(
        "--purity",
        type=float,
        default=1.0,
        help="k-means shortcut: share of labels that must agree (default: 1.0, or all)",
    )
    parser.add_argument(
        "--min-members",
        type=int,
        default=5,
        help="fewest examples needed in a shortcut group (default: 5)",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.10,
        help="center shortcut: required gap between good and bad match scores (default: 0.10)",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="random seed for repeatable k-means runs"
    )
    parser.add_argument(
        "--embedder",
        choices=["minilm", "tfidf"],
        default="minilm",
        action=_Given,
        help="minilm, the default model, or tfidf, which needs no download but routes worse. "
        "With --rules-only the default is tfidf",
    )
    parser.add_argument(
        "--missing-grade",
        choices=["stop", "review"],
        default="stop",
        help="replay: stop when no saved grade matches, or record review and go on (default: stop)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=1,
        action=_Given,
        help="judge up to this many of the closest rules and keep the worst outcome "
        "(default: 1, or every rule with --rules-only)",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=None,
        help="with --top-k, skip extra rules whose match score is below this (default: none)",
    )


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
    _grading_options(run)
    run.add_argument(
        "--rules-only",
        action="store_true",
        help="grade from the rules file alone: --train is not read, the judge gets no "
        "examples, and every rule is checked unless --top-k is set. This mode is unmeasured",
    )
    run.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="live only: judge each answer this many times, skipping saved grades after "
        "the first, and keep the worst outcome (default: 1)",
    )
    run.set_defaults(func=cmd_run)

    attack = sub.add_parser("attack", help="send attack prompts to the model under test")
    attack.add_argument("--prompts", required=True, help="attack prompts, one JSON record per line")
    attack.add_argument("--out", required=True, help="file to write the chatbot's answers to")
    attack.add_argument(
        "--system", default="policy/target_system_prompt.txt", help="instructions for the chatbot"
    )
    attack.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="send each prompt this many times; later answers get ids ending #2, #3 (default: 1)",
    )
    attack.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="sampling temperature for the chatbot; above 0 lets repeats differ (default: 0)",
    )
    attack.set_defaults(func=cmd_attack)

    evade = sub.add_parser(
        "evade",
        help="rewrite bad answers to see which slip past the checks",
        description=(
            "Grade each answer labeled bad as written and after each fixed rewrite. With "
            "--attacker, a chat model then rewords the answers still caught. In replay mode "
            "the rewritten answers have no saved grades, so pass --missing-grade review."
        ),
    )
    evade.add_argument("--data", required=True, help="labeled answers; only bad ones are used")
    evade.add_argument("--out", required=True, help="folder for the evade output files")
    evade.add_argument(
        "--judge",
        choices=["live", "replay"],
        default="replay",
        help="call a judge model (live) or read saved grades (replay, needs "
        "--missing-grade review) (default: replay)",
    )
    evade.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="with --attacker, most rewording rounds per answer (default: 5)",
    )
    evade.add_argument(
        "--attacker",
        action="store_true",
        help="also ask a chat model, set by REDLINE_ATTACKER_*, to reword the answers",
    )
    evade.add_argument(
        "--max-judge-calls",
        type=int,
        default=None,
        help="stop cleanly after this many judge calls and write what is done (default: none)",
    )
    _grading_options(
        evade,
        cache_default=None,
        cache_help="file of saved judge grades (default: with --judge live, judge_cache.jsonl "
        f"under --out, never {PINNED_CACHE}; with replay, {PINNED_CACHE})",
    )
    evade.set_defaults(func=cmd_evade)

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
    report.add_argument(
        "--evade-labels",
        default=None,
        help="evade candidates with a person's good or bad label; bad ones that passed "
        "count as missed",
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

    grade = sub.add_parser(
        "garak-grade",
        help="grade the outputs in a garak report against your rules",
        description=(
            "Read a garak report file and check each output against the rules. Without "
            "--train every rule is checked with no examples, a mode that is unmeasured."
        ),
    )
    grade.add_argument("report", help="a garak report file, one JSON entry per line")
    grade.add_argument("--rules", required=True, help="YAML file containing your rules")
    grade.add_argument(
        "--out", default=None, help="folder for the three output files, needed without --dry-run"
    )
    grade.add_argument(
        "--max-judge-calls",
        type=int,
        default=None,
        help="stop before the judge call that would pass this number (default: no limit)",
    )
    grade.add_argument(
        "--dry-run",
        action="store_true",
        help="print how many answers would be graded and the most judge calls that takes, "
        "then exit. No judge is called and its settings are not read",
    )
    grade.add_argument(
        "--user-type",
        default="anonymous",
        help="the user type every output was given to (default: anonymous)",
    )
    grade.add_argument(
        "--judge",
        choices=["live", "replay"],
        default="live",
        help="call a judge model (live) or read saved grades from --cache (default: live)",
    )
    grade.add_argument("--cache", default=None, help="file of saved judge grades (default: none)")
    grade.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="check this many of the closest rules (default: every rule, or 1 with --train)",
    )
    grade.add_argument(
        "--train",
        default=None,
        help="labeled examples; with them the calibrated mode is used instead of rules only",
    )
    grade.add_argument(
        "--review-score",
        type=_from_0_to_1,
        default=0.5,
        help="the score of an output sent to review, as given to the detector (default: 0.5)",
    )
    grade.add_argument(
        "--eval-threshold",
        type=_from_0_to_1,
        default=0.5,
        help="garak's eval threshold: a score below it passes (default: 0.5)",
    )
    grade.add_argument(
        "--embedder",
        choices=["minilm", "tfidf"],
        default=None,
        help="how text is matched to rules (default: tfidf, or minilm with --train)",
    )
    grade.add_argument(
        "--missing-grade",
        choices=["stop", "review"],
        default="stop",
        help="replay: stop when no saved grade matches, or record review and go on (default: stop)",
    )
    grade.set_defaults(func=cmd_garak_grade)

    for name, func, text in (
        ("garak-install", cmd_garak_install, "let the garak in this environment load Redline"),
        ("garak-uninstall", cmd_garak_uninstall, "remove the files garak-install wrote"),
    ):
        shim = sub.add_parser(
            name,
            help=text,
            description=(
                "garak loads plugins only from inside its own package. garak-install writes "
                "one small file each for the Redline detector and probe into it, and "
                "garak-uninstall removes them. A file that is not the Redline shim is "
                "never changed."
            ),
        )
        shim.add_argument("--dry-run", action="store_true", help="print what would be done")
        shim.set_defaults(func=func)
    return parser


def main(argv=None) -> int:
    from redline.embed import MissingExtra

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MissingExtra as exc:
        print(f"redline: {exc}", file=sys.stderr)
        return 2
