"""Pick the rule an answer is about by finding the closest rule center."""

import math
from typing import NamedTuple

import numpy as np

from redline.embed import text


class RouteResult(NamedTuple):
    rule_id: str
    similarity: float
    abstained: bool


def _unit(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


class Router:
    """Sends each answer to the rule whose center it is closest to.

    A center is the average of the labeled train answers for one rule, plus one
    center for off topic answers under "none". When an answer is far from every
    center, the router abstains and the answer goes to a person.
    """

    def __init__(self, embedder=None):
        self.embedder = embedder
        self.rule_ids: list[str] = []
        self.centers: np.ndarray | None = None
        self.threshold: float = 0.0

    @classmethod
    def from_centers(cls, centers: dict[str, np.ndarray], threshold: float) -> "Router":
        """Build a router from given centers, with no embedding model."""
        router = cls()
        router.rule_ids = list(centers)
        router.centers = np.vstack([_unit(np.asarray(c, dtype=np.float32)) for c in centers.values()])
        router.threshold = float(threshold)
        return router

    def fit(self, records: list[dict]) -> "Router":
        """Build one center per rule_id in the records and pick the threshold."""
        vectors = self.embedder.encode([text(r["prompt"], r["response"]) for r in records])
        groups: dict[str, list[int]] = {}
        for i, r in enumerate(records):
            groups.setdefault(r["rule_id"], []).append(i)
        self.rule_ids = sorted(groups)
        self.centers = np.vstack(
            [_unit(vectors[groups[rule_id]].mean(axis=0)) for rule_id in self.rule_ids]
        )
        best = np.sort((vectors @ self.centers.T).max(axis=1))
        # The lowest score still kept when 95 percent of train records sit at or above it.
        n = len(best)
        self.threshold = float(best[n - math.ceil(0.95 * n)])
        return self

    def route_vector(self, vec: np.ndarray) -> RouteResult:
        """Route one vector: the closest center wins, unless it is below the threshold."""
        scores = self.centers @ _unit(np.asarray(vec, dtype=np.float32))
        best = int(np.argmax(scores))
        similarity = float(scores[best])
        if similarity < self.threshold:
            return RouteResult("none", similarity, True)
        return RouteResult(self.rule_ids[best], similarity, False)

    def route(self, prompt: str, response: str) -> RouteResult:
        """Embed a prompt and its answer, then route the vector."""
        return self.route_vector(self.embedder.encode([text(prompt, response)])[0])
