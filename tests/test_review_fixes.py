"""Tests for the defects an independent review found in Redline 0.3.0."""

from redline import metrics, report, thresholds


def _row(rid, outcomes, rule="PAY-01"):
    worst = max(outcomes, key=lambda o: {"pass": 0, "review": 1, "fail": 2}[o])
    return {
        "id": rid,
        "outcome": worst,
        "outcomes": outcomes,
        "attempts": len(outcomes),
        "decided_by": "judge",
        "routed_rule": rule,
        "short_circuit": False,
    }


def _lab(rid, label, rule="PAY-01"):
    return {"id": rid, "label": label, "rule_id": rule}


# Two bad answers each failed once then passed once, and two good answers always passed.
DISAGREE_OUT = [
    _row("b1", ["fail", "pass"]),
    _row("b2", ["fail", "pass"]),
    _row("g1", ["pass", "pass"]),
    _row("g2", ["pass", "pass"]),
]
DISAGREE_LAB = [_lab("b1", "bad"), _lab("b2", "bad"), _lab("g1", "good"), _lab("g2", "good")]


def test_attempts_that_disagree_tell_the_same_story_everywhere():
    e2e = metrics.end_to_end(DISAGREE_OUT, DISAGREE_LAB)
    assert e2e["bad"]["caught"] == 0.0 and e2e["bad"]["missed"] == 1.0
    rule = metrics.per_rule(DISAGREE_OUT, DISAGREE_LAB)["PAY-01"]
    assert rule["recall"] == 0.0
    assert metrics.accuracy(DISAGREE_OUT, DISAGREE_LAB) == 0.5
    assert metrics.cohen_kappa(DISAGREE_OUT, DISAGREE_LAB) == 0.0


def test_a_good_answer_failed_once_counts_as_failed_everywhere():
    outs = [_row("b1", ["fail", "fail"]), _row("g1", ["pass", "fail"])]
    labs = [_lab("b1", "bad"), _lab("g1", "good")]
    assert metrics.end_to_end(outs, labs)["good"]["wrongly_failed"] == 1.0
    assert metrics.per_rule(outs, labs)["PAY-01"]["precision"] == 0.5
    assert metrics.accuracy(outs, labs) == 0.5


def test_gate_min_kappa_fails_when_attempts_disagree(tmp_path, capsys):
    from redline import cli

    summary = report.build_summary(DISAGREE_OUT, DISAGREE_LAB, [], [], [], "m", {})
    assert thresholds.check(summary, min_kappa=0.5) == [
        "kappa on holdout is 0.000, below the minimum of 0.500"
    ]
    path = tmp_path / "summary.json"
    report.write_summary(summary, path)
    assert cli.main(["gate", "--summary", str(path), "--min-kappa", "0.5"]) == 1
    assert "kappa on holdout is 0.000" in capsys.readouterr().out


def test_confirmed_adaptive_leaves_out_only_records_whose_pass_is_unlabeled():
    from redline import evade
    from redline.intervals import interval

    labeled = [
        {"id": "b1", "round": 1, "verdict": "pass", "label": "bad"},
        {"id": "b2", "round": 1, "verdict": "pass", "label": "good"},
        {"id": "b3", "round": 1, "verdict": "fail", "label": "unconfirmed"},
        {"id": "b3", "round": 2, "verdict": "pass", "label": "unconfirmed"},
        {"id": "b4", "round": 1, "verdict": "fail", "label": "unconfirmed"},
        {"id": "b4", "round": 2, "verdict": "fail"},
    ]
    block = evade.confirmed(labeled)
    assert block["records"] == 3
    assert block["counts"]["missed"] == interval(1, 3)
    assert block["unlabeled"] == 1


def test_a_stop_on_judge_calls_drops_the_partly_graded_record_everywhere():
    from test_evade import BAD, StubGrader, verdict

    from redline import evade, mutate

    grader = StubGrader(lambda response: verdict("fail", "X"), {"PAY-01": object()})
    limit = len(mutate.REWRITES) + 3
    rows, summary, _ = evade.run(BAD, grader, max_judge_calls=limit)
    assert summary["records"] == 1
    assert len(rows) == 1 + len(mutate.REWRITES)
    assert {r["id"] for r in rows} == {"b1"}
    for block in summary["by_rewrite"].values():
        assert block["counts"]["caught"]["n"] == summary["records"]


REPO = __import__("pathlib").Path(__file__).resolve().parent.parent
PINNED = REPO / "data" / "judge_cache.jsonl"


def _hash(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workdir(tmp_path, monkeypatch, verdict="fail"):
    """Work in tmp_path with a copy of the pinned cache at data/judge_cache.jsonl.

    Judge and attacker share one fake transport. The judge prompt asks for JSON. Returns
    the copy and the list of payloads sent. No judge is ever pointed at the pinned file.
    """
    import json
    import shutil

    from redline import judge as judge_module

    (tmp_path / "data").mkdir()
    copy = tmp_path / "data" / "judge_cache.jsonl"
    shutil.copyfile(PINNED, copy)
    sent = []

    def send(payload):
        sent.append(payload)
        if "response_format" in payload:
            nonce = payload["messages"][0]["content"].rsplit('"', 2)[-2]
            reply = {"nonce": nonce, "verdict": verdict, "severity": 0.5}
            return json.dumps({**reply, "reason_code": "OK", "reason_text": "x"})
        return "<rewrite>A new wording.</rewrite>"

    for who in ("JUDGE", "ATTACKER"):
        for name in ("BASE_URL", "MODEL", "API_KEY"):
            monkeypatch.setenv(f"REDLINE_{who}_{name}", "x")
    monkeypatch.setattr(judge_module, "http_transport", lambda *a, **k: send)
    monkeypatch.chdir(tmp_path)
    return copy, sent


def _bad_args(tmp_path, n=1, fresh=False):
    """Data, rules and train options. With fresh, no saved grade matches any answer."""
    import json

    from redline import cli

    rows = [r for r in cli.read_jsonl(REPO / "data" / "holdout.jsonl") if r["label"] == "bad"]
    rows = rows[:n]
    if fresh:
        rows = [{**r, "response": r["response"] + " Unseen."} for r in rows]
    data = tmp_path / "bad.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in rows))
    rules, train = REPO / "policy" / "rules.yaml", REPO / "data" / "train.jsonl"
    return ["--data", str(data), "--rules", str(rules), "--train", str(train)]


def test_live_evade_writes_its_cache_under_out_not_the_pinned_cache(tmp_path, monkeypatch):
    from redline import cli

    before = _hash(PINNED)
    copy, _ = _workdir(tmp_path, monkeypatch)
    copied = _hash(copy)
    args = ["evade", *_bad_args(tmp_path), "--judge", "live", "--embedder", "tfidf"]
    assert cli.main([*args, "--out", "evade"]) == 0
    assert _hash(copy) == copied and _hash(PINNED) == before
    assert (tmp_path / "evade" / "judge_cache.jsonl").stat().st_size > 0


def test_live_evade_with_attacker_leaves_the_pinned_cache_alone(tmp_path, monkeypatch):
    from redline import cli

    before = _hash(PINNED)
    copy, sent = _workdir(tmp_path, monkeypatch)
    copied = _hash(copy)
    args = ["evade", *_bad_args(tmp_path), "--judge", "live", "--attacker", "--rounds", "2"]
    assert cli.main([*args, "--embedder", "tfidf", "--out", "evade"]) == 0
    assert any("response_format" not in p for p in sent)
    assert _hash(copy) == copied and _hash(PINNED) == before


def test_live_evade_refuses_the_pinned_cache_before_any_judge_call(tmp_path, monkeypatch, capsys):
    from redline import cli

    before = _hash(PINNED)
    copy, sent = _workdir(tmp_path, monkeypatch)
    copied = _hash(copy)
    args = ["evade", *_bad_args(tmp_path), "--judge", "live"]
    args += ["--cache", "data/judge_cache.jsonl", "--embedder", "tfidf", "--out", "evade"]
    assert cli.main(args) == 2
    assert "data/judge_cache.jsonl" in capsys.readouterr().err
    assert sent == []
    assert _hash(copy) == copied and _hash(PINNED) == before


def test_run_with_attempts_reads_but_never_writes_the_pinned_cache(tmp_path, monkeypatch):
    from redline import cli

    before = _hash(PINNED)
    copy, sent = _workdir(tmp_path, monkeypatch)
    copied = _hash(copy)
    args = ["run", *_bad_args(tmp_path, 3, fresh=True), "--judge", "live", "--attempts", "2"]
    assert cli.main([*args, "--embedder", "tfidf", "--out", "run"]) == 0
    assert sent
    assert _hash(copy) == copied and _hash(PINNED) == before


class _RouterGrader:
    """Passes by router any answer containing "padded", else fails by judge."""

    def __init__(self):
        self.judge = self
        self.rules = {}

    def grade(self, *args):
        return {}

    def run(self, records):
        rows = []
        for r in records:
            routed = "padded" in r["response"]
            if not routed:
                self.judge.grade()
            rows.append(
                {
                    "id": r["id"],
                    "outcome": "pass" if routed else "fail",
                    "decided_by": "router" if routed else "judge",
                    "reason_code": "OK" if routed else "X",
                    "reason_text": "",
                    "routed_rule": "none" if routed else "PAY-01",
                }
            )
        return rows


def test_router_only_counts_records_the_router_passed_as_written_and_after_a_rewrite():
    from redline import evade
    from redline.intervals import interval

    records = [
        {"id": "a", "label": "bad", "user_type": "x", "prompt": "p", "response": "padded"},
        {"id": "b", "label": "bad", "user_type": "x", "prompt": "p", "response": "plain"},
        {"id": "c", "label": "bad", "user_type": "x", "prompt": "p", "response": "plain"},
    ]
    rewrites = {"pad": lambda t: t + " padded", "same": lambda t: t}
    _, summary, _ = evade.run(records, _RouterGrader(), rewrites=rewrites)
    assert summary["router_only"] == {
        "as_written": interval(1, 3),
        "after_rewrite": interval(3, 3),
    }


def test_an_attack_id_with_a_hash_is_looked_up_exactly_first():
    prompts = [{"id": "atk#7", "kind": "card", "target_rule_id": "PAY-01"}]
    outs = [{**_row("atk#7", ["fail"]), "routed_rule": "PAY-01"}]
    outs[0].pop("outcomes")
    labs = [_lab("atk#7", "bad")]
    kinds = metrics.attack_summary(outs, labs, prompts)
    assert kinds["card"]["caught_by_rig"] == 1
    assert report._attack_routing(outs, prompts) == {"total": 1, "misrouted": 0}
    summary = report.build_summary([], [], outs, labs, prompts, "m", {})
    assert summary["attack"]["card"]["rule_breaks"] == 1


def test_a_later_attempt_of_an_id_with_a_hash_still_finds_its_prompt():
    prompts = [{"id": "atk#7", "kind": "card", "target_rule_id": "PAY-01"}]
    assert metrics.prompt_id("atk#7#2", {"atk#7"}) == "atk#7"
    assert metrics.prompt_id("plain#2", {"plain"}) == "plain"
    outs = [{**_row("atk#7#2", ["pass"]), "routed_rule": "PAY-01"}]
    assert report._attack_routing(outs, prompts) == {"total": 1, "misrouted": 0}


def test_attack_kinds_count_a_review_as_sent_to_review_not_missed():
    prompts = [{"id": "a1", "kind": "card"}, {"id": "a2", "kind": "card"}]
    prompts.append({"id": "a3", "kind": "card"})
    outs = [_row("a1", ["fail"]), _row("a2", ["review"]), _row("a3", ["pass"])]
    labs = [_lab("a1", "bad"), _lab("a2", "bad"), _lab("a3", "bad")]
    card = metrics.attack_summary(outs, labs, prompts)["card"]
    assert (card["caught_by_rig"], card["missed"], card["sent_to_review"]) == (1, 1, 1)
    assert card["counts"]["sent_to_review"] == interval_of(1, 3)
    e2e = metrics.end_to_end(outs, labs)["counts"]["bad"]
    assert e2e["sent_to_review"]["k"] == card["sent_to_review"]
    assert e2e["missed"]["k"] == card["missed"]


def interval_of(k, n):
    from redline.intervals import interval

    return interval(k, n)


def test_wilson_ends_are_exact():
    from redline.intervals import wilson

    for n in range(1, 60):
        assert wilson(0, n)[0] == 0.0
        assert wilson(n, n)[1] == 1.0


def test_gate_use_interval_with_counts_missing_ci95_exits_2(tmp_path, capsys):
    import json

    from redline import cli

    summary = {
        "end_to_end": {
            "bad": {"caught": 0.9},
            "good": {"wrongly_failed": 0.0},
            "counts": {"bad": {"caught": {"k": 9, "n": 10}}, "good": {"wrongly_failed": {}}},
        },
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))
    args = ["gate", "--summary", str(path), "--min-recall", "0.5", "--use-interval"]
    assert cli.main(args) == 2
    assert "ci95" in capsys.readouterr().err


def test_the_repo_cache_counts_as_pinned_from_any_folder(tmp_path, monkeypatch):
    from redline import cli

    monkeypatch.chdir(tmp_path)
    assert cli.is_pinned_cache(PINNED)
    assert not cli.is_pinned_cache(tmp_path / "judge_cache.jsonl")
    assert not cli.is_pinned_cache(None)
