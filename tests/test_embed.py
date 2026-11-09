import json

import numpy as np
import pytest

from redline import api, cli, embed
from redline.judge import FakeJudge

TEXTS = ["what is my balance", "pay with my card", "nice weather today"]


def test_tfidf_gives_unit_rows_after_fit():
    embedder = embed.TfidfEmbedder().fit(TEXTS)
    vectors = embedder.encode(TEXTS + ["balance card"])
    assert vectors.dtype == np.float32
    assert vectors.shape[0] == 4
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert embedder.name == "tfidf"


def test_tfidf_must_be_fit_first():
    with pytest.raises(RuntimeError, match="fit"):
        embed.TfidfEmbedder().encode(TEXTS)


def test_make_picks_the_embedder_by_name():
    assert isinstance(embed.make("minilm"), embed.Embedder)
    assert embed.make("minilm").name == "minilm"
    assert isinstance(embed.make("tfidf"), embed.TfidfEmbedder)
    with pytest.raises(ValueError, match="minilm, tfidf"):
        embed.make("other")


def no_model(*args, **kwargs):
    raise AssertionError("the tfidf embedder must not load the model")


def test_a_tfidf_run_loads_no_model_and_records_the_embedder(monkeypatch, tmp_path):
    monkeypatch.setattr(embed.Embedder, "encode", no_model)
    record = {"id": "h-1", "user_type": "verified", "prompt": "whats my balance",
              "response": "Your balance is $84.12."}
    data = tmp_path / "holdout.jsonl"
    data.write_text(json.dumps(record) + "\n")
    pass_answer = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "x"}

    class FakeJudgeGrader(api.Grader):
        def __init__(self, **kwargs):
            super().__init__(**{**kwargs, "judge": FakeJudge(pass_answer)})

    monkeypatch.setattr(api, "Grader", FakeJudgeGrader)
    out = tmp_path / "out"
    args = ["run", "--data", str(data), "--judge", "replay", "--embedder", "tfidf"]
    assert cli.main(args + ["--out", str(out)]) == 0
    meta = json.loads((out / "holdout_meta.json").read_text())
    assert meta["embedder"] == "tfidf"
    [row] = cli.read_jsonl(out / "holdout_outputs.jsonl")
    assert row["id"] == "h-1"


def test_the_grader_fits_tfidf_on_the_training_texts(monkeypatch):
    monkeypatch.setattr(embed.Embedder, "encode", no_model)
    grader = api.Grader(judge=FakeJudge({}), embedder="tfidf")
    assert grader.embedder_name == "tfidf"
    assert grader.router.rank("whats my balance", "It is $84.12.", "verified")[0].rule_id


def test_grader_missing_grade_review_keeps_going(monkeypatch, tmp_path):
    monkeypatch.setattr(embed.Embedder, "encode", no_model)
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    grader = api.Grader(embedder="tfidf", cache=cache, missing_grade="review")
    out = grader.grade("can i pay with my card", "Read me the full card number please.", "verified")
    assert out["decided_by"] == "judge"
    assert (out["outcome"], out["reason_code"]) == ("review", "NO_SAVED_GRADE")
    with pytest.raises(ValueError, match="stop or review"):
        api.Grader(embedder="tfidf", cache=cache, missing_grade="skip")


def test_run_with_missing_grade_review_counts_them_in_meta(tmp_path):
    record = {"id": "h-1", "user_type": "verified", "prompt": "can i pay with my card",
              "response": "Read me the full card number please."}
    data = tmp_path / "holdout.jsonl"
    data.write_text(json.dumps(record) + "\n")
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    out = tmp_path / "out"
    args = ["run", "--data", str(data), "--judge", "replay", "--embedder", "tfidf",
            "--cache", str(cache), "--missing-grade", "review", "--out", str(out)]
    assert cli.main(args) == 0
    [row] = cli.read_jsonl(out / "holdout_outputs.jsonl")
    meta = json.loads((out / "holdout_meta.json").read_text())
    assert (row["decided_by"], row["reason_code"]) == ("judge", "NO_SAVED_GRADE")
    assert meta["no_saved_grade_records"] == 1
    assert meta["no_saved_grade_lookups"] == 1
    assert "no_saved_grade" not in meta
    assert meta["missing_grade"] == "review"


def test_top_k_counts_a_missing_grade_hidden_behind_a_fail(monkeypatch, tmp_path):
    from redline import gate
    from redline.judge import build_prompt, prompt_key

    monkeypatch.setattr(embed.Embedder, "encode", no_model)
    record = {"id": "h-1", "user_type": "verified", "prompt": "can i pay with my card",
              "response": "Read me the full card number please."}
    probe = api.Grader(judge=FakeJudge({}), embedder="tfidf")
    top = probe.router.rank(record["prompt"], record["response"], "verified")[0].rule_id
    rule = probe.rules[top]
    key = prompt_key(build_prompt(
        rule, probe.examples[top], record["prompt"], record["response"], "verified",
        gate.check(rule, "verified").text,
    ))
    fail = {"verdict": "fail", "severity": 0.9, "reason_code": "OK", "reason_text": "x"}
    cache = tmp_path / "cache.jsonl"
    cache.write_text(json.dumps({"key": key, "model": "m", "rule_id": top, "answer": fail}) + "\n")
    data = tmp_path / "holdout.jsonl"
    data.write_text(json.dumps(record) + "\n")
    out = tmp_path / "out"
    args = ["run", "--data", str(data), "--judge", "replay", "--embedder", "tfidf",
            "--cache", str(cache), "--missing-grade", "review", "--top-k", "2",
            "--floor", "-1", "--out", str(out)]
    assert cli.main(args) == 0
    [row] = cli.read_jsonl(out / "holdout_outputs.jsonl")
    assert (row["outcome"], row["routed_rule"]) == ("fail", top)
    assert sorted(row["outcomes_by_rule"].values()) == ["fail", "review"]
    meta = json.loads((out / "holdout_meta.json").read_text())
    assert meta["no_saved_grade_records"] == 0
    assert meta["no_saved_grade_lookups"] == 1
