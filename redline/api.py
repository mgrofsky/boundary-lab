"""Grade answers from your own code with the same steps the run command uses."""

import json
from pathlib import Path

from redline import pipeline, policy
from redline.embed import Embedder
from redline.judge import ReplayJudge, select
from redline.router import KMeansRouter, Router, ShortCircuit


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _embedder(choice):
    """Return an embedder for a name, or the object itself when it already has encode."""
    if not isinstance(choice, str):
        return choice
    if choice == "minilm":
        return Embedder()
    raise ValueError(f"unknown embedder {choice!r}, expected minilm")


class Grader:
    """Load the rules and examples once, fit the router, then grade one answer at a time.

    judge is a judge object from redline.judge. None means the replay judge, which reads
    saved answers from cache. short_circuit is False, True for the default settings, or a
    ShortCircuit with your own settings. top_k above 1 judges up to that many of the closest
    rules and keeps the worst outcome. floor skips extra rules less similar than it.
    """

    def __init__(
        self,
        rules="policy/rules.yaml",
        train="data/train.jsonl",
        judge=None,
        router="center",
        k=12,
        short_circuit=False,
        seed=0,
        cache="data/judge_cache.jsonl",
        embedder="minilm",
        top_k=1,
        floor=None,
    ):
        loaded = policy.load_policy(rules)
        self.rules = loaded.rules
        self.user_types = loaded.user_types
        self.train = _read_jsonl(train)
        self.examples = select(self.train)
        self.judge = judge if judge is not None else ReplayJudge(cache)
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        self.top_k = top_k
        self.floor = floor
        if short_circuit is True:
            short_circuit = ShortCircuit()
        self.short_circuit = short_circuit or None
        self.embedder = _embedder(embedder)
        if router == "kmeans":
            self.router = KMeansRouter(
                self.embedder, k=k, seed=seed, short_circuit=self.short_circuit
            )
        elif router == "center":
            self.router = Router(self.embedder, short_circuit=self.short_circuit)
        else:
            raise ValueError(f"unknown router {router!r}, expected center or kmeans")
        self.router.fit(self.train)

    def check_user_type(self, user_type, record_id=None) -> None:
        """Raise ValueError when the rules file does not declare this user type."""
        if user_type in self.user_types:
            return
        where = f"record {record_id}: " if record_id is not None else ""
        raise ValueError(
            f"{where}user_type {user_type!r} is not declared in the rules file "
            f"(declared: {', '.join(self.user_types)})"
        )

    def run(self, records: list[dict]) -> list[dict]:
        """Grade records that carry id, user_type, prompt and response."""
        for record in records:
            self.check_user_type(record["user_type"], record.get("id"))
        return pipeline.run(
            records,
            self.rules,
            self.router,
            self.judge,
            self.examples,
            top_k=self.top_k,
            floor=self.floor,
        )

    def grade(self, prompt: str, response: str, user_type: str) -> dict:
        """Grade one answer and return its output record, without an id."""
        record = {"id": None, "user_type": user_type, "prompt": prompt, "response": response}
        [out] = self.run([record])
        del out["id"]
        return out
