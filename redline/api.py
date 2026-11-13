"""Grade answers from your own code with the same steps the run command uses."""

import json
import os
from pathlib import Path

from redline import embed, pipeline, policy
from redline.judge import MISSING_GRADE, ReplayJudge, RoleView, select, takes_role
from redline.router import KMeansRouter, Router, RuleRouter, ShortCircuit

# What every row of a labeled examples file holds.
TRAIN_FIELDS = ("id", "rule_id", "user_type", "label", "prompt", "response", "reason_code")
# What rules may be given as: the path of a rules file, a loaded Policy, or rules data.
Rules = str | os.PathLike | policy.Policy | list | dict


def read_train(path: str | os.PathLike, rules: dict | None = None) -> list[dict]:
    """Read a labeled examples file and return its rows in order.

    path is a file with one JSON object per line. Blank lines are skipped. rules is
    the loaded rules keyed by rule_id. With it, every row must name one of those
    rules, or "none" for an answer no rule applies to.

    A line that is not a JSON object, that lacks one of TRAIN_FIELDS, or that names a
    rule the rules do not have raises ValueError with the file and the line number.
    So does a file with no rows, since the calibrated mode has nothing to learn from.
    A file that cannot be read raises OSError.
    """
    rows = []
    for number, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"{path}: line {number} is not valid JSON") from None
        if not isinstance(row, dict):
            # A bad line is bad content, like a missing field, so it is a ValueError too.
            raise ValueError(f"{path}: line {number} is not a JSON object")  # noqa: TRY004
        missing = [field for field in TRAIN_FIELDS if field not in row]
        if missing:
            raise ValueError(f"{path}: line {number} has no {', '.join(missing)}")
        known = rules is None or row["rule_id"] in rules or policy.reserved(row["rule_id"])
        if not known:
            raise ValueError(
                f"{path}: line {number}: rule_id {policy.show(row['rule_id'])} is not a "
                "rule in the rules"
            )
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: has no examples, and the calibrated mode needs some")
    return rows


def check_rules(loaded: policy.Policy, source: str) -> None:
    """Raise ValueError when a Grader cannot be built from these rules.

    Data and files were checked when they were loaded. A Policy built by hand was not.
    """
    if not loaded.rules:
        raise ValueError(f"{source}: {policy.NO_RULES}")
    for rule_id in loaded.rules:
        if policy.reserved(rule_id):
            raise ValueError(
                f"{source}: rule_id {policy.show(rule_id)} is reserved. Redline uses the "
                "word none to mean that no rule applies"
            )


def check_user_type(user_types: list[str], user_type: str, record_id: object = None) -> None:
    """Raise ValueError when user_type is not one of user_types.

    user_types is the list the rules declare. record_id, when given, is the id of the
    record the user type came from, and the message starts with it.
    """
    if user_type in user_types:
        return
    where = f"record {record_id}: " if record_id is not None else ""
    raise ValueError(
        f"{where}user_type {user_type!r} is not declared in the rules file "
        f"(declared: {', '.join(user_types)})"
    )


def _embedder(choice):
    """Return an embedder for a name, or the object itself when one is passed in."""
    if not isinstance(choice, str):
        return choice
    return embed.make(choice)


def read_rules(rules: Rules) -> tuple[policy.Policy, str]:
    """Return the Policy for a path, a loaded Policy or rules data, and a name for errors."""
    if isinstance(rules, policy.Policy):
        return rules, "<policy>"
    if isinstance(rules, (list, dict)):
        return policy.load_policy_data(rules), "<data>"
    return policy.load_policy(rules), str(rules)


def estimate_calls(
    n_answers: int, rules: Rules, top_k: int | None = None, attempts: int = 1
) -> int:
    """The largest number of judge calls a rules only run can make.

    n_answers is how many answers will be graded. rules is anything Grader.from_rules
    takes: a path, a Policy or rules data. top_k None checks every rule for each
    answer, and top_k N checks the N closest, or every rule when there are fewer.
    attempts is how many times each answer is judged, as for Grader.

    Returns n_answers times the rules checked per answer times attempts. A run can
    make fewer calls: a saved grade answers without one, and so does a blank answer in
    a garak report. No judge is called. A negative n_answers, or a top_k or attempts
    below 1, raises ValueError. So do rules that do not load.
    """
    if n_answers < 0:
        raise ValueError(f"n_answers must be 0 or more, got {n_answers}")
    if top_k is not None and top_k < 1:
        raise ValueError(f"top_k must be at least 1, got {top_k}")
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    loaded, source = read_rules(rules)
    check_rules(loaded, source)
    count = len(loaded.rules)
    return n_answers * (count if top_k is None else min(top_k, count)) * attempts


class Grader:
    """Load the rules and examples once, fit the router, then grade one answer at a time.

    rules is the path of a rules file, a Policy from redline.policy, or the rules as
    data: what the file holds once parsed. See redline.policy.load_policy_data.

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

    The judge prompt calls the system being graded "a contact center assistant". A rules
    file can change that with a top level assistant_role, and assistant_role here is used
    when the file sets none. The role belongs to the Grader. It is passed to the judge
    with each call and is never written onto the judge, so one judge object can serve
    any number of Graders, in any order and from several threads. A judge whose grade
    does not take assistant_role is called without it. Grader.judge is then the judge
    seen under this Grader's role, see redline.judge.RoleView.
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
        assistant_role=None,
    ):
        loaded, source = read_rules(rules)
        check_rules(loaded, source)
        self.rules = loaded.rules
        self.user_types = loaded.user_types
        # With no labeled examples the rules' own text is all there is to route on.
        self.mode = "rules_only" if train is None else None
        self.train = [] if train is None else read_train(train, self.rules)
        self.examples = select(self.train)
        if self.mode and top_k is None:
            top_k = len(self.rules)
        role = loaded.assistant_role or assistant_role
        self._set_judge(judge, cache, missing_grade, top_k, floor, attempts, role)
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

    def _set_judge(self, judge, cache, missing_grade, top_k, floor, attempts, role) -> None:
        if missing_grade not in MISSING_GRADE:
            raise ValueError(f"missing_grade must be stop or review, got {missing_grade!r}")
        self.judge = judge if judge is not None else ReplayJudge(cache, missing=missing_grade)
        # None leaves the judge's own text, which is the one the saved grades were made with.
        # The caller's judge is never written to. It may be serving other Graders.
        if role is not None and takes_role(self.judge):
            self.judge = RoleView(self.judge, role)
        self.assistant_role = getattr(self.judge, "assistant_role", role)
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
        assistant_role=None,
    ) -> "Grader":
        """Grade from the rules alone, with no labeled examples. This mode is unmeasured.

        rules is a path, a Policy or rules data, as for Grader.

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
            assistant_role=assistant_role,
        )

    def check_user_type(self, user_type, record_id=None) -> None:
        """Raise ValueError when the rules file does not declare this user type."""
        check_user_type(self.user_types, user_type, record_id)

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
