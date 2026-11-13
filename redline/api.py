"""Grade answers from your own code with the same steps the run command uses."""

import json
from pathlib import Path

from redline import embed, pipeline, policy
from redline.judge import MISSING_GRADE, ReplayJudge, select
from redline.router import KMeansRouter, Router, RuleRouter, ShortCircuit


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _embedder(choice):
    """Return an embedder for a name, or the object itself when one is passed in."""
    if not isinstance(choice, str):
        return choice
    return embed.make(choice)


class Grader:
    """Load the rules and examples once, fit the router, then grade one answer at a time.

    judge is a judge object from redline.judge. None means the replay judge, which reads
    saved answers from cache. short_circuit is False, True for the default settings, or a
    ShortCircuit with your own settings. embedder is "minilm", the default model, "tfidf",
    which needs no download but routes worse, or your own object with encode. top_k above 1 judges up to that many of the closest
    rules and keeps the worst outcome. floor skips extra rules less similar than it.
    missing_grade says what the replay judge does with no saved grade: "stop" raises
    KeyError, "review" records review with the code NO_SAVED_GRADE. attempts above 1
    judges each answer that reaches the judge that many times. It needs a judge with
    without_cache, such as ChatEndpointJudge, since later attempts must skip saved grades.

    train None grades from the rules file alone. See from_rules, which is the plain way
    to ask for that mode.
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
        missing_grade="stop",
        attempts=1,
    ):
        loaded = policy.load_policy(rules)
        self.rules = loaded.rules
        if not self.rules:
            raise ValueError(f"{rules}: the rules file has no rules")
        self.user_types = loaded.user_types
        # With no labeled examples the rules' own text is all there is to route on.
        self.mode = "rules_only" if train is None else None
        self.train = [] if train is None else _read_jsonl(train)
        self.examples = select(self.train)
        if self.mode and top_k is None:
            top_k = len(self.rules)
        self._set_judge(judge, cache, missing_grade, top_k, floor, attempts)
        if short_circuit is True:
            short_circuit = ShortCircuit()
        self.short_circuit = short_circuit or None
        if self.mode and self.short_circuit:
            raise ValueError("short_circuit needs labeled examples, so it needs a train file")
        self.embedder = _embedder(embedder)
        self.embedder_name = getattr(self.embedder, "name", type(self.embedder).__name__)
        if self.mode:
            self.router = RuleRouter(self.embedder).fit(self.rules)
            return
        if hasattr(self.embedder, "fit"):
            self.embedder.fit([embed.text(r["prompt"], r["response"]) for r in self.train])
        if router == "kmeans":
            self.router = KMeansRouter(
                self.embedder, k=k, seed=seed, short_circuit=self.short_circuit
            )
        elif router == "center":
            self.router = Router(self.embedder, short_circuit=self.short_circuit)
        else:
            raise ValueError(f"unknown router {router!r}, expected center or kmeans")
        self.router.fit(self.train)

    def _set_judge(self, judge, cache, missing_grade, top_k, floor, attempts) -> None:
        if missing_grade not in MISSING_GRADE:
            raise ValueError(f"missing_grade must be stop or review, got {missing_grade!r}")
        self.judge = judge if judge is not None else ReplayJudge(cache, missing=missing_grade)
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        self.top_k = top_k
        self.floor = floor
        if attempts < 1:
            raise ValueError(f"attempts must be at least 1, got {attempts}")
        self.attempts = attempts
        self.again = None
        if attempts > 1:
            if not hasattr(self.judge, "without_cache"):
                raise ValueError("attempts above 1 need a live judge, not saved grades")
            self.again = self.judge.without_cache()

    @classmethod
    def from_rules(
        cls,
        rules,
        judge,
        top_k=None,
        embedder="tfidf",
        cache=None,
        missing_grade="stop",
        attempts=1,
    ) -> "Grader":
        """Grade from a rules file alone, with no labeled examples. This mode is unmeasured.

        The router ranks the rules by how close each rule's text is to the answer, and the
        judge gets no examples. top_k None checks every rule, which costs one judge call
        per rule per answer. top_k N checks the N closest. The worst outcome wins. judge
        None reads saved grades from cache. cache None means no saved grades are read or
        written by Redline. Every output row carries mode "rules_only".
        """
        if judge is None and cache is None:
            raise ValueError("rules only mode needs a judge or a cache of saved grades")
        return cls(
            rules=rules,
            train=None,
            judge=judge,
            cache=cache,
            embedder=embedder,
            top_k=top_k,
            missing_grade=missing_grade,
            attempts=attempts,
        )

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
        rows = pipeline.run(
            records,
            self.rules,
            self.router,
            self.judge,
            self.examples,
            top_k=self.top_k,
            floor=self.floor,
            attempts=self.attempts,
            again=self.again,
        )
        if self.mode:
            for row in rows:
                row["mode"] = self.mode
        return rows

    def grade(self, prompt: str, response: str, user_type: str) -> dict:
        """Grade one answer and return its output record, without an id."""
        record = {"id": None, "user_type": user_type, "prompt": prompt, "response": response}
        [out] = self.run([record])
        del out["id"]
        return out
