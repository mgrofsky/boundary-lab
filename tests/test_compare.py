import json

from test_report import load_summary

from redline import report


def write_run(folder, router, short_circuit, count, right):
    """A run folder with the published summary, short circuit numbers and two meta files."""
    folder.mkdir()
    summary = load_summary()
    sc = {"count": count, "share": count / 56, "right": right, "wrong_ids": []}
    summary["short_circuit"] = {"holdout": sc, "attack": dict(sc, share=count / 80)}
    (folder / "summary.json").write_text(json.dumps(summary))
    for name, calls in (("holdout", 40), ("attack_responses", 60)):
        meta = {"router": router, "short_circuit": short_circuit, "judge_calls": calls}
        meta["seconds"] = 1.5
        (folder / f"{name}_meta.json").write_text(json.dumps(meta))
    return folder


def two_runs(tmp_path):
    return [
        write_run(tmp_path / "center", "center", False, 0, 0),
        write_run(tmp_path / "kmeans12_sc", "kmeans12", True, 4, 4),
    ]


def test_build_comparison_gives_one_row_per_folder(tmp_path):
    rows = report.build_comparison(two_runs(tmp_path))
    assert [row["label"] for row in rows] == [
        "center, short circuit off",
        "kmeans12, short circuit on",
    ]
    for row in rows:
        assert all(key in row for _, key in report.COMPARISON_COLUMNS)
    assert rows[1]["judge_calls"] == 100
    assert rows[1]["seconds"] == 3.0


def test_render_comparison_has_header_and_one_line_per_folder(tmp_path):
    text = report.render_comparison(report.build_comparison(two_runs(tmp_path)))
    table = [line for line in text.splitlines() if line.startswith("|")]
    assert table[0].startswith("| Run | Router accuracy holdout |")
    assert len(table) == 2 + 2
    assert "4 of 4" in table[3]
    assert "| none |" in table[2]


def test_readme_table_ignores_the_new_keys():
    summary = load_summary()
    before = report.render_readme_table(summary)
    summary["short_circuit"] = {"holdout": report.NO_SHORT_CIRCUIT, "attack": {"count": 1}}
    summary["run"] = {"holdout": {"router": "kmeans12"}, "attack": None}
    assert report.render_readme_table(summary) == before
