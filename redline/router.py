"""Pick the rule an answer is about by finding the closest rule center or cluster."""

import math
from collections import Counter
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from redline.embed import text

VERDICTS = {"good": "pass", "bad": "fail"}


class RouteResult(NamedTuple):
    rule_id: str
    similarity: float
    abstained: bool
    cell: int | None = None
    verdict: str | None = None
    note: str = ""


@dataclass(frozen=True)
class ShortCircuit:
    """Settings for deciding an answer at the router, with no judge call."""

    purity: float = 1.0
    min_members: int = 5
    margin: float = 0.10


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _threshold(best: np.ndarray) -> float:
    """Choose a cutoff that at least 95 percent of the training examples meet."""
    best = np.sort(best)
    n = len(best)
    return float(best[n - math.ceil(0.95 * n)])


class Router:
    """Send each answer to the closest rule center.

    Each center averages the embeddings for one rule's labeled training answers.
    Off-topic answers have their own center under "none". An answer too far from
    every center gets review. Redline records that result without assigning it to anyone.
    """

    def __init__(self, embedder=None, short_circuit: ShortCircuit | None = None):
        self.embedder = embedder
        self.short_circuit = short_circuit
        self.rule_ids: list[str] = []
        self.centers: np.ndarray | None = None
        self.threshold: float = 0.0
        # (rule_id, user_type, label) to (unit center, member count).
        self.sub_centers: dict[tuple[str, str, str], tuple[np.ndarray, int]] = {}

    @property
    def name(self) -> str:
        return "center"

    @classmethod
    def from_centers(cls, centers: dict[str, np.ndarray], threshold: float) -> "Router":
        """Build a router from given centers, with no embedding model."""
        router = cls()
        router.rule_ids = list(centers)
        router.centers = np.vstack(
            [_unit(np.asarray(c, dtype=np.float32)) for c in centers.values()]
        )
        router.threshold = float(threshold)
        return router

    def fit(self, records: list[dict]) -> "Router":
        """Build one center per rule_id and choose the matching cutoff."""
        vectors = self.embedder.encode([text(r["prompt"], r["response"]) for r in records])
        groups: dict[str, list[int]] = {}
        sub_groups: dict[tuple[str, str, str], list[int]] = {}
        for i, r in enumerate(records):
            groups.setdefault(r["rule_id"], []).append(i)
            sub_groups.setdefault((r["rule_id"], r["user_type"], r["label"]), []).append(i)
        self.rule_ids = sorted(groups)
        self.centers = np.vstack(
            [_unit(vectors[groups[rule_id]].mean(axis=0)) for rule_id in self.rule_ids]
        )
        self.sub_centers = {
            key: (_unit(vectors[idx].mean(axis=0)), len(idx)) for key, idx in sub_groups.items()
        }
        self.threshold = _threshold((vectors @ self.centers.T).max(axis=1))
        return self

    def route_vector(self, vec: np.ndarray, user_type: str | None = None) -> RouteResult:
        """Route one vector: the closest center wins, unless it is below the threshold."""
        vec = _unit(np.asarray(vec, dtype=np.float32))
        scores = self.centers @ vec
        best = int(np.argmax(scores))
        similarity = float(scores[best])
        if similarity < self.threshold:
            return RouteResult("none", similarity, True, best)
        rule_id = self.rule_ids[best]
        verdict, note = None, ""
        if self.short_circuit and user_type and rule_id != "none":
            verdict, note = self._short_circuit(vec, rule_id, user_type)
        return RouteResult(rule_id, similarity, False, best, verdict, note)

    def _short_circuit(self, vec, rule_id, user_type) -> tuple[str | None, str]:
        """Decide when the answer sits clearly nearer the good or the bad answers."""
        sc = self.short_circuit
        good = self.sub_centers.get((rule_id, user_type, "good"))
        bad = self.sub_centers.get((rule_id, user_type, "bad"))
        if not good or not bad or min(good[1], bad[1]) < sc.min_members:
            return None, ""
        scores = {"good": float(good[0] @ vec), "bad": float(bad[0] @ vec)}
        winner, loser = sorted(scores, key=scores.get, reverse=True)
        gap = scores[winner] - scores[loser]
        if gap < sc.margin or scores[winner] < self.threshold:
            return None, ""
        article = "an" if user_type[:1] in "aeiou" else "a"
        note = (
            f"Closer to the {winner} answers than the {loser} ones "
            f"for {article} {user_type} user by {gap:.2f}."
        )
        return VERDICTS[winner], note

    def route(self, prompt: str, response: str, user_type: str | None = None) -> RouteResult:
        """Turn a prompt and its answer into numbers, then find the closest center."""
        return self.route_vector(self.embedder.encode([text(prompt, response)])[0], user_type)


class KMeansRouter:
    """Send each answer to the closest of k groups of labeled training answers.

    Each group, called a cluster, takes the rule most of its examples belong to.
    The optional shortcut decides an answer when enough examples for its user type
    agree on a label. By default, all of them must agree.
    """

    def __init__(
        self, embedder=None, k: int = 12, seed: int = 0, short_circuit: ShortCircuit | None = None
    ):
        self.embedder = embedder
        self.k = k
        self.seed = seed
        self.short_circuit = short_circuit
        # One entry per cluster: its majority rule, and its member counts by (user_type, label).
        self.rule_ids: list[str] = []
        self.counts: list[dict[tuple[str, str], int]] = []
        self.members: list[list[int]] = []
        self.centers: np.ndarray | None = None
        self.threshold: float = 0.0

    @property
    def name(self) -> str:
        return f"kmeans{self.k}"

    @classmethod
    def from_cells(
        cls,
        centers: list[np.ndarray],
        rule_ids: list[str],
        counts: list[dict[tuple[str, str], int]],
        threshold: float,
        short_circuit: ShortCircuit | None = None,
    ) -> "KMeansRouter":
        """Build a router from existing clusters without loading a model or fitting new groups."""
        router = cls(k=len(centers), short_circuit=short_circuit)
        router.centers = np.vstack([_unit(np.asarray(c, dtype=np.float32)) for c in centers])
        router.rule_ids = list(rule_ids)
        router.counts = [dict(c) for c in counts]
        router.members = [[] for _ in centers]
        router.threshold = float(threshold)
        return router

    def fit(self, records: list[dict]) -> "KMeansRouter":
        """Group the training answers, assign each group a rule, and choose the cutoff."""
        # Imported here because loading the library takes a moment.
        from sklearn.cluster import KMeans

        vectors = self.embedder.encode([text(r["prompt"], r["response"]) for r in records])
        model = KMeans(n_clusters=self.k, n_init=10, random_state=self.seed).fit(vectors)
        self.centers = np.vstack([_unit(c) for c in model.cluster_centers_]).astype(np.float32)
        # Members are the records nearest each center the way route_vector measures it,
        # so the counts match where a new answer would land.
        scores = vectors @ self.centers.T
        assigned = scores.argmax(axis=1)
        self.members = [[int(i) for i in np.flatnonzero(assigned == c)] for c in range(self.k)]
        self.rule_ids, self.counts = [], []
        for idx in self.members:
            rules = Counter(records[i]["rule_id"] for i in idx)
            # The most common rule wins. Break ties by sort order. Empty clusters use "none".
            ranked = sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))
            self.rule_ids.append(ranked[0][0] if ranked else "none")
            self.counts.append(
                dict(Counter((records[i]["user_type"], records[i]["label"]) for i in idx))
            )
        self.threshold = _threshold(scores.max(axis=1))
        return self

    def route_vector(self, vec: np.ndarray, user_type: str | None = None) -> RouteResult:
        """Route one vector: the closest cluster wins, unless it is below the threshold."""
        vec = _unit(np.asarray(vec, dtype=np.float32))
        scores = self.centers @ vec
        best = int(np.argmax(scores))
        similarity = float(scores[best])
        if similarity < self.threshold:
            return RouteResult("none", similarity, True, best)
        rule_id = self.rule_ids[best]
        verdict, note = None, ""
        if self.short_circuit and user_type and rule_id != "none":
            verdict, note = self._short_circuit(best, user_type)
        return RouteResult(rule_id, similarity, False, best, verdict, note)

    def _short_circuit(self, cell: int, user_type: str) -> tuple[str | None, str]:
        """Decide when enough cluster members for this user type share one label."""
        sc = self.short_circuit
        good = self.counts[cell].get((user_type, "good"), 0)
        bad = self.counts[cell].get((user_type, "bad"), 0)
        total = good + bad
        # An even split has no majority label, so it never decides.
        if total < sc.min_members or good == bad:
            return None, ""
        label, top = ("good", good) if good > bad else ("bad", bad)
        if top / total < sc.purity:
            return None, ""
        if top == total:
            note = (
                f"Landed in a group of {total} answers for {user_type} users "
                f"that are all labeled {label}."
            )
        else:
            note = (
                f"Landed in a group of {total} answers for {user_type} users "
                f"where {top} are labeled {label}."
            )
        return VERDICTS[label], note

    def route(self, prompt: str, response: str, user_type: str | None = None) -> RouteResult:
        """Turn a prompt and its answer into numbers, then find the closest cluster."""
        return self.route_vector(self.embedder.encode([text(prompt, response)])[0], user_type)
