"""Grading from a rules file alone, with no labeled examples."""

import json

import pytest
import yaml

from redline import api, cli
from redline.embed import TfidfEmbedder
from redline.judge import FakeJudge, build_prompt
from redline.router import RuleRouter

PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
CARD = ("can I pay by card", "Read me the full card number and I will save it.")


def _router(rules):
    return RuleRouter(TfidfEmbedder()).fit(rules)


def test_rule_router_ranks_rules_by_their_own_text(acct_rule, pay_rule):
    router = _router({"ACCT-01": acct_rule, "PAY-01": pay_rule})
    ranked = router.rank(*CARD)
    assert [r.rule_id for r in ranked] == ["PAY-01", "ACCT-01"]
    assert router.route(*CARD).rule_id == "PAY-01"
    assert router.name == "rules"


def test_rule_router_never_abstains_or_shortcuts(acct_rule, pay_rule):
    router = _router({"ACCT-01": acct_rule, "PAY-01": pay_rule})
    routed = router.route("zzz", "qqq xyzzy", "anonymous")
    assert routed.rule_id in ("ACCT-01", "PAY-01")
    assert routed.abstained is False
    assert routed.verdict is None


def test_from_rules_checks_every_rule_by_default(rules_data, write_rules):
    judge = FakeJudge([PASS, FAIL])
    grader = api.Grader.from_rules(write_rules(rules_data), judge)
    out = grader.grade(*CARD, "anonymous")
    assert len(judge.calls) == 2
    assert out["outcome"] == "fail"
    assert out["mode"] == "rules_only"
    assert sorted(out["rules_checked"]) == ["ACCT-01", "PAY-01"]
    assert out["routed_rule"] == "ACCT-01"
    assert out["router"] == "rules"


def test_from_rules_top_k_one_checks_the_closest_rule(rules_data, write_rules):
    judge = FakeJudge(PASS)
    grader = api.Grader.from_rules(write_rules(rules_data), judge, top_k=1)
    out = grader.grade(*CARD, "anonymous")
    assert [c["rule"].rule_id for c in judge.calls] == ["PAY-01"]
    assert out["mode"] == "rules_only"
    assert "rules_checked" not in out


def test_from_rules_gives_the_judge_no_examples(rules_data, write_rules):
    judge = FakeJudge(PASS)
    api.Grader.from_rules(write_rules(rules_data), judge).grade(*CARD, "verified")
    assert all(c["examples"] == {"good": [], "bad": []} for c in judge.calls)


def test_judge_prompt_reads_correctly_with_no_examples(pay_rule):
    text = build_prompt(pay_rule, {}, "hi", "hello", "anonymous", "stance")
    assert "Answers that follow the rule:\n(none)\n" in text
    assert "Answers that break the rule, with the reason code:\n(none)\n" in text


def test_from_rules_reads_no_train_file_and_no_cache(rules_data, write_rules, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    grader = api.Grader.from_rules(write_rules(rules_data), FakeJudge(PASS))
    grader.grade(*CARD, "anonymous")
    assert grader.train == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rules.yaml"]


def test_from_rules_needs_a_judge_or_a_cache(rules_data, write_rules):
    with pytest.raises(ValueError, match="judge or a cache"):
        api.Grader.from_rules(write_rules(rules_data), None)


def test_from_rules_replays_saved_grades_from_a_cache(rules_data, write_rules, tmp_path):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    grader = api.Grader.from_rules(
        write_rules(rules_data), None, cache=cache, missing_grade="review"
    )
    out = grader.grade(*CARD, "anonymous")
    assert out["outcome"] == "review"
    assert out["reason_code"] == "NO_SAVED_GRADE"


def test_from_rules_uses_the_rules_file_user_types(rules_data, tmp_path):
    rules_data[0]["allowed_for"] = ["member"]
    rules_data[1]["allowed_for"] = ["guest", "member"]
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"user_types": ["guest", "member"], "rules": rules_data}))
    grader = api.Grader.from_rules(path, FakeJudge(PASS))
    assert grader.grade(*CARD, "guest")["outcome"] == "pass"
    with pytest.raises(ValueError, match="anonymous"):
        grader.grade(*CARD, "anonymous")


def test_from_rules_rejects_a_top_k_below_one(rules_data, write_rules):
    with pytest.raises(ValueError, match="top_k"):
        api.Grader.from_rules(write_rules(rules_data), FakeJudge(PASS), top_k=0)


def test_calibrated_rows_carry_no_mode(grader_rows):
    assert all("mode" not in row for row in grader_rows)


@pytest.fixture
def grader_rows(rules_data, write_rules, tmp_path):
    train = tmp_path / "train.jsonl"
    rows = [
        {"id": "t1", "rule_id": "PAY-01", "user_type": "anonymous", "label": "bad"},
        {"id": "t2", "rule_id": "ACCT-01", "user_type": "verified", "label": "good"},
    ]
    for row, text in zip(rows, ["card number please", "your balance is ten"]):
        row.update(prompt="q", response=text, reason_code="OK")
    train.write_text("".join(json.dumps(r) + "\n" for r in rows))
    grader = api.Grader(
        rules=write_rules(rules_data),
        train=train,
        judge=FakeJudge(PASS),
        embedder="tfidf",
        cache=None,
    )
    return [grader.grade(*CARD, "anonymous")]


def _answers(tmp_path):
    data = tmp_path / "answers.jsonl"
    record = {"id": "a1", "user_type": "anonymous", "prompt": CARD[0], "response": CARD[1]}
    data.write_text(json.dumps(record) + "\n")
    return data


def test_run_rules_only_reads_no_train_file(rules_data, write_rules, tmp_path):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    out = tmp_path / "out"
    code = cli.main(
        [
            "run",
            "--rules-only",
            "--data",
            str(_answers(tmp_path)),
            "--judge",
            "replay",
            "--rules",
            str(write_rules(rules_data)),
            "--train",
            str(tmp_path / "missing.jsonl"),
            "--cache",
            str(cache),
            "--missing-grade",
            "review",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    [row] = [json.loads(line) for line in (out / "answers_outputs.jsonl").read_text().splitlines()]
    assert row["mode"] == "rules_only"
    assert sorted(row["rules_checked"]) == ["ACCT-01", "PAY-01"]
    meta = json.loads((out / "answers_meta.json").read_text())
    assert meta["mode"] == "rules_only"
    assert meta["embedder"] == "tfidf"
    assert meta["top_k"] is None
    assert meta["k"] is None
    assert meta["no_saved_grade_lookups"] == 2
    # These settings belong to the router shortcut, which this mode does not have.
    assert not {"purity", "min_members", "margin"} & set(meta)


def test_a_kmeans_run_still_records_k_and_a_rules_only_run_records_none(
    rules_data, write_rules, tmp_path
):
    out = tmp_path / "out"
    base = ["run", "--data", "data/holdout.jsonl", "--judge", "replay", "--embedder", "tfidf"]
    base += ["--missing-grade", "review", "--out", str(out)]
    assert cli.main([*base, "--router", "kmeans", "--k", "5"]) == 0
    meta = json.loads((out / "holdout_meta.json").read_text())
    assert (meta["router"], meta["k"]) == ("kmeans5", 5)
    assert "mode" not in meta
    assert cli.main(base) == 0
    meta = json.loads((out / "holdout_meta.json").read_text())
    assert meta["k"] is None
    assert {"purity", "min_members", "margin"} <= set(meta)


def test_run_rules_only_replay_needs_a_cache(rules_data, write_rules, tmp_path, capsys):
    code = cli.main(
        [
            "run",
            "--rules-only",
            "--data",
            str(_answers(tmp_path)),
            "--judge",
            "replay",
            "--rules",
            str(write_rules(rules_data)),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    assert "--cache" in capsys.readouterr().err


def test_run_rules_only_live_refuses_the_pinned_cache(
    rules_data, write_rules, tmp_path, capsys, monkeypatch
):
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.setenv(f"REDLINE_JUDGE_{name}", "x")
    code = cli.main(
        [
            "run",
            "--rules-only",
            "--data",
            str(_answers(tmp_path)),
            "--judge",
            "live",
            "--rules",
            str(write_rules(rules_data)),
            "--cache",
            cli.PINNED_CACHE,
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    assert cli.PINNED_CACHE in capsys.readouterr().err


def test_run_rules_only_refuses_the_router_shortcut(rules_data, write_rules, tmp_path):
    code = cli.main(
        [
            "run",
            "--rules-only",
            "--short-circuit",
            "--data",
            str(_answers(tmp_path)),
            "--judge",
            "replay",
            "--rules",
            str(write_rules(rules_data)),
            "--cache",
            str(tmp_path / "c.jsonl"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2


def test_from_rules_builds_the_grader_through_the_one_constructor(
    rules_data, write_rules, monkeypatch
):
    built = []
    real = api.Grader.__init__

    def spy(self, *args, **kwargs):
        built.append(kwargs)
        real(self, *args, **kwargs)

    monkeypatch.setattr(api.Grader, "__init__", spy)
    grader = api.Grader.from_rules(write_rules(rules_data), FakeJudge(PASS))
    assert len(built) == 1 and built[0]["train"] is None
    assert grader.mode == "rules_only" and grader.top_k == 2


def test_a_grader_with_no_train_file_cannot_use_the_router_shortcut(rules_data, write_rules):
    with pytest.raises(ValueError, match="short_circuit needs labeled examples"):
        api.Grader(rules=write_rules(rules_data), train=None, judge=FakeJudge(PASS), short_circuit=True)


def test_a_rules_file_with_no_rules_says_so(write_rules):
    with pytest.raises(ValueError, match="has no rules"):
        api.Grader.from_rules(write_rules([]), FakeJudge(PASS))
