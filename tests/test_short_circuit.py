import numpy as np

from redline.embed import text
from redline.router import KMeansRouter, Router, ShortCircuit


class FixedEmbedder:
    """Returns a fixed unit vector for each known text."""

    def __init__(self, vectors):
        self.vectors = vectors

    def encode(self, texts):
        return np.vstack([self.vectors[t] for t in texts])


def fake_records(rows):
    """Turn (rule_id, user_type, label, vector) rows into records and a matching embedder."""
    records, vectors = [], {}
    for i, (rule_id, user_type, label, vec) in enumerate(rows):
        record = {
            "rule_id": rule_id,
            "user_type": user_type,
            "label": label,
            "prompt": f"prompt {i}",
            "response": "answer",
        }
        records.append(record)
        vec = np.asarray(vec, dtype=np.float32)
        vectors[text(record["prompt"], record["response"])] = vec / np.linalg.norm(vec)
    return records, FixedEmbedder(vectors)


GOOD = [[1, 0.1, 2], [1, -0.1, 2], [1.1, 0, 2]]
BAD = [[0.1, 1, 2], [-0.1, 1, 2], [0, 1.1, 2]]
NEAR_GOOD = np.array([1, 0, 2], dtype=np.float32)


def center_router(**settings):
    rows = [("ACCT-01", "anonymous", "good", v) for v in GOOD]
    rows += [("ACCT-01", "anonymous", "bad", v) for v in BAD]
    records, embedder = fake_records(rows)
    return Router(embedder, ShortCircuit(**settings)).fit(records)


def test_center_near_good_answers_passes():
    router = center_router(min_members=3)
    result = router.route_vector(NEAR_GOOD, "anonymous")
    assert router.name == "center"
    assert result.rule_id == "ACCT-01"
    assert result.verdict == "pass"
    assert "good" in result.note


def test_center_too_few_members_gives_no_verdict():
    result = center_router(min_members=7).route_vector(NEAR_GOOD, "anonymous")
    assert result.verdict is None


def test_center_margin_too_wide_gives_no_verdict():
    result = center_router(min_members=3, margin=0.9).route_vector(NEAR_GOOD, "anonymous")
    assert result.verdict is None


def test_center_needs_a_user_type_with_sub_centers():
    router = center_router(min_members=3)
    assert router.route_vector(NEAR_GOOD).verdict is None
    assert router.route_vector(NEAR_GOOD, "verified").verdict is None


def cell_router(counts, rule_id="ACCT-01", **settings):
    """One cluster on the first axis and one off topic cluster on the second."""
    return KMeansRouter.from_cells(
        centers=[[1, 0], [0, 1]],
        rule_ids=[rule_id, "none"],
        counts=[counts, {}],
        threshold=0.8,
        short_circuit=ShortCircuit(**settings),
    )


ON_CELL = np.array([1, 0.05], dtype=np.float32)


def test_kmeans_pure_cell_fails_the_answer():
    result = cell_router({("anonymous", "bad"): 5}).route_vector(ON_CELL, "anonymous")
    assert result.cell == 0
    assert result.verdict == "fail"
    assert "all labeled bad" in result.note


def test_kmeans_cell_with_four_members_gives_no_verdict():
    result = cell_router({("anonymous", "bad"): 4}).route_vector(ON_CELL, "anonymous")
    assert result.verdict is None


def test_kmeans_mixed_cell_needs_purity_low_enough():
    counts = {("anonymous", "bad"): 4, ("anonymous", "good"): 1}
    assert cell_router(counts).route_vector(ON_CELL, "anonymous").verdict is None
    result = cell_router(counts, purity=0.8).route_vector(ON_CELL, "anonymous")
    assert result.verdict == "fail"


def test_kmeans_off_topic_cell_gives_no_verdict():
    result = cell_router({("anonymous", "bad"): 5}, rule_id="none").route_vector(
        ON_CELL, "anonymous"
    )
    assert result.rule_id == "none"
    assert result.verdict is None


def test_kmeans_abstain_wins_over_short_circuit():
    router = cell_router({("anonymous", "bad"): 5})
    result = router.route_vector(np.array([1, 1], dtype=np.float32), "anonymous")
    assert result.abstained
    assert result.verdict is None


def test_kmeans_fit_names_each_cluster_by_majority_rule():
    rows = []
    for angle, rules in [(0, "AAAB"), (120, "BBBB"), (240, "CCCC")]:
        for j, rule_id in enumerate(rules):
            a = np.radians(angle + (j - 1.5) * 4)
            rows.append((rule_id, "anonymous", "bad", [np.cos(a), np.sin(a)]))
    records, embedder = fake_records(rows)
    router = KMeansRouter(embedder, k=3).fit(records)
    assert router.name == "kmeans3"
    assert sorted(router.rule_ids) == ["A", "B", "C"]
    assert 0 < router.threshold < 1
    assert router.route_vector(np.array([1, 0.01])).rule_id == "A"
