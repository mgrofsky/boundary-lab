import json
import shutil
from pathlib import Path

from redline import cli, report
from redline.embed import DEFAULT_MODEL


def test_embedding_name_reads_the_run_settings():
    assert report.embedding_name(None) == DEFAULT_MODEL
    assert report.embedding_name({"holdout": {"router": "center"}, "attack": None}) == DEFAULT_MODEL
    tfidf = {"embedder": "tfidf"}
    assert report.embedding_name({"holdout": tfidf, "attack": tfidf}) == "tfidf"
    mixed = {"holdout": tfidf, "attack": {"embedder": "minilm"}}
    assert report.embedding_name(mixed) == f"tfidf for holdout, {DEFAULT_MODEL} for attack"


def report_args(run_dir):
    return [
        "report",
        "--holdout", str(run_dir / "holdout_outputs.jsonl"),
        "--attack", str(run_dir / "attack_responses_outputs.jsonl"),
        "--out", str(run_dir),
    ]


def copy_saved_run(tmp_path, embedder=None):
    for name in ("holdout", "attack_responses"):
        shutil.copy(f"results/{name}_outputs.jsonl", tmp_path)
        meta = json.loads(Path(f"results/{name}_meta.json").read_text())
        if embedder:
            meta["embedder"] = embedder
        (tmp_path / f"{name}_meta.json").write_text(json.dumps(meta))
    return tmp_path


def test_report_names_the_tfidf_embedder(tmp_path):
    run_dir = copy_saved_run(tmp_path, "tfidf")
    assert cli.main(report_args(run_dir)) == 0
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["embedding_model"] == "tfidf"
    text = (run_dir / "report.md").read_text()
    assert "The router used `tfidf`" in text
    assert DEFAULT_MODEL not in text


def test_report_keeps_the_model_name_for_a_run_without_an_embedder_setting(tmp_path):
    run_dir = copy_saved_run(tmp_path)
    assert cli.main(report_args(run_dir)) == 0
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["embedding_model"] == DEFAULT_MODEL
