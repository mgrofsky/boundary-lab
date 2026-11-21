"""Check one answer before a chatbot shows it, and say whether to show it.

Guard sits on top of a Grader. An app calls it with the prompt and the answer its model
just wrote, before the user sees anything. It returns a Decision: show the answer, hold
it back, or show something else. It checks a whole answer, so an app that streams tokens
must buffer the answer first.
"""

import asyncio
import json
import math
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from redline import judge as judge_module
from redline import pipeline

# What a Decision tells the app to do.
#   show: show the answer as given.
#   regenerated: the answer was held back, and a new one passed. Show the new one.
#   replace: the answer was held back. Show the replacement text.
#   hold: the answer was held back and there is nothing to show in its place.
ACTIONS = ("show", "regenerated", "replace", "hold")
# What to do with a review, and with an answer the judge gave no usable grade for.
CHOICES = ("hold", "show")
# Why a fallback decided instead of a grade.
#   deadline: the check ran out of time.
#   judge_error: the judge gave no usable grade: an outage, a reply it could not read,
#     a reply without the check value, or no saved grade.
#   error: grading raised an exception.
#   too_long: the prompt or the answer is longer than max_chars, so neither was sent
#     to the judge.
FALLBACKS = ("deadline", "judge_error", "error", "too_long")
GUARD_CODES = {
    "deadline": "GUARD_DEADLINE",
    "error": "GUARD_ERROR",
    "too_long": "GUARD_TOO_LONG",
}
DEADLINE_SECONDS = 10.0
MAX_CHARS = 20_000
# How many new answers regenerate may be asked for, by default and at most.
RETRIES = 1
MAX_RETRIES = 3
# An exception's text is cut to this length before it is kept.
ERROR_CHARS = 200


@dataclass(frozen=True)
class Decision:
    """What to do with one answer, and why.

    allow is True only when the answer as given may be shown. text is what to show:
    the answer, a new answer that passed, the replacement, or None for nothing. The
    outcome, rule and reason describe the answer as given. fallback is None when a
    grade decided, or one of FALLBACKS. outcomes has one outcome per answer checked,
    the answer as given first. judge_calls counts the calls of grades that finished.
    """

    allow: bool
    action: str
    text: str | None
    outcome: str
    rule_id: str | None
    reason_code: str
    reason_text: str
    decided_by: str
    fallback: str | None
    seconds: float
    judge_calls: int
    outcomes: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["outcomes"] = list(self.outcomes)
        return out


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:ERROR_CHARS]


class _Checked:
    """One answer and what grading it gave."""

    def __init__(self, response, row=None, fallback=None, error=None):
        self.response = response
        self.row = row
        self.fallback = fallback
        self.error = error


class _State:
    """What the worker has done so far. The caller reads it when the worker ends or time is up."""

    def __init__(self):
        self.lock = threading.Lock()
        self.checked: list[_Checked] = []
        self.pending: str | None = None
        self.error: str | None = None
        self.worker: threading.Thread | None = None
        self.finished = False


class Guard:
    """Grade an answer before it is shown and say what to do with it.

    grader is a redline.Grader, built once and reused. Every option has a default that
    holds the answer back when in doubt.

    on_review says what a review does: "hold" (the default) or "show". on_error says
    what happens when no grade arrives in time, the judge gives no usable grade, or
    grading raises: "hold" (the default, fail closed) or "show" (fail open). The
    Decision records the fallback either way. on_error applies only to the answer as
    given. Once that answer is held back, nothing unchecked is shown.

    deadline_seconds is the time limit on the whole check, a new answer included.
    The call returns by then whatever the judge does. The live judge's own requests
    stop at it too, see redline.judge.deadline.

    replacement is the text shown in place of a held back answer. None shows nothing.
    replacements maps a rule_id to its own text, used when that rule held the answer.

    regenerate, when given, is called as regenerate(prompt, held) after a grade holds
    the answer back. held is a dict with response, rule_id, outcome, reason_code and
    reason_text. It returns a new answer, which is graded the same way, or None to give
    up. It is called at most retries times per check (default 1, at most 3). It is
    not called after a fallback, since a judge that just failed would likely fail again.

    max_chars holds back, with no judge call, an answer when it or its prompt is longer
    than that many characters. None sends every answer to the judge.

    record is the path of a JSON lines file. Each check appends one line per answer
    checked: the same fields as a line of holdout_outputs.jsonl, plus a "guard" object
    with the action taken. None writes nothing. record_text False (the default) writes
    the prompt, the response and the judge's reason_text as null, since held back
    answers are the ones most likely to hold the data the rules protect.
    """

    def __init__(
        self,
        grader,
        on_review="hold",
        on_error="hold",
        deadline_seconds=DEADLINE_SECONDS,
        replacement=None,
        replacements=None,
        regenerate=None,
        retries=RETRIES,
        max_chars=MAX_CHARS,
        record=None,
        record_text=False,
    ):
        for name, value in (("on_review", on_review), ("on_error", on_error)):
            if value not in CHOICES:
                raise ValueError(f"{name} must be hold or show, got {value!r}")
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
            or not math.isfinite(deadline_seconds)
            or deadline_seconds <= 0
        ):
            raise ValueError(f"deadline_seconds must be a number above 0, got {deadline_seconds!r}")
        if isinstance(retries, bool) or not isinstance(retries, int):
            raise TypeError(f"retries must be a whole number, got {retries!r}")
        if not 0 <= retries <= MAX_RETRIES:
            raise ValueError(f"retries must be from 0 to {MAX_RETRIES}, got {retries}")
        if regenerate is not None and not callable(regenerate):
            raise ValueError("regenerate must be a function or None")
        if replacement is not None and not isinstance(replacement, str):
            raise ValueError("replacement must be text or None")
        replacements = dict(replacements or {})
        for rule_id, text in replacements.items():
            if rule_id not in grader.rules:
                raise ValueError(f"replacements names {rule_id!r}, which is not a rule")
            if not isinstance(text, str):
                raise TypeError(f"replacements[{rule_id!r}] must be text")
        if max_chars is not None and (
            isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1
        ):
            raise ValueError(f"max_chars must be a whole number above 0 or None, got {max_chars!r}")
        self.grader = grader
        self.on_review = on_review
        self.on_error = on_error
        self.deadline_seconds = float(deadline_seconds)
        self.replacement = replacement
        self.replacements = replacements
        self.regenerate = regenerate
        self.retries = retries
        self.max_chars = max_chars
        self.record_text = record_text
        self.record = Path(record) if record is not None else None
        self._lock = threading.Lock()
        if self.record is not None:
            # Fail now, not on the first answer, when the file cannot be written.
            with self.record.open("a"):
                pass

    @property
    def max_judge_calls(self) -> int:
        """The most judge calls one check can make.

        One grade checks top_k rules, or every rule when there are fewer, attempts
        times. A check grades the answer, and with regenerate up to retries new ones.
        """
        per_grade = min(self.grader.top_k, len(self.grader.rules)) * self.grader.attempts
        answers = 1 + (self.retries if self.regenerate is not None else 0)
        return per_grade * answers

    def check(self, prompt: str, response: str, user_type: str, record_id=None) -> "Decision":
        """Grade one answer and return the Decision. It waits at most deadline_seconds.

        record_id is written to the record as id, so the lines of one conversation can
        be found together.
        A prompt or response that is not text raises TypeError, and a user type the
        rules do not declare raises ValueError. A judge that fails does not raise.
        """
        start, end, state, stop = self._start(prompt, response, user_type)
        try:
            state.worker.join(max(0.0, end - time.monotonic()))
        finally:
            # Whatever happened here, the worker must not start anything new.
            stop.set()
        return self._finish(start, state, prompt, response, user_type, record_id)

    async def acheck(
        self, prompt: str, response: str, user_type: str, record_id=None
    ) -> "Decision":
        """The same check, awaitable. The event loop is free while the judge works.

        The judge makes blocking calls, so the grading runs in a thread of its own,
        started at once. The deadline counts from the call, as for check.
        """
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def wake() -> None:
            try:
                loop.call_soon_threadsafe(done.set)
            except RuntimeError:
                # The loop has closed. Nobody is waiting any more.
                pass

        start, end, state, stop = self._start(prompt, response, user_type, wake)
        try:
            await asyncio.wait_for(done.wait(), max(0.0, end - time.monotonic()))
        except TimeoutError:
            pass
        finally:
            stop.set()
        return self._finish(start, state, prompt, response, user_type, record_id)

    def _start(self, prompt, response, user_type, wake=None):
        """Check the arguments and start the worker. Returns start, end, state and stop."""
        for name, value in (("prompt", prompt), ("response", response)):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be text, got {type(value).__name__}")
        self.grader.check_user_type(user_type)
        start = time.monotonic()
        end = start + self.deadline_seconds
        state = _State()
        stop = threading.Event()
        state.worker = threading.Thread(
            target=self._work,
            args=(state, stop, end, prompt, response, user_type, wake),
            name="redline-guard",
            daemon=True,
        )
        state.worker.start()
        return start, end, state, stop

    def _finish(self, start, state, prompt, response, user_type, record_id) -> "Decision":
        """Decide from what the worker did by now, and write the record."""
        with state.lock:
            checked = list(state.checked)
            finished = state.finished
            pending = state.pending
            error = state.error
        seconds = time.monotonic() - start
        if not finished:
            # The step that was running when time ran out: grading the answer as given,
            # grading a new one, or asking for one, which has no answer yet.
            checked.append(_Checked(pending if checked else response, fallback="deadline"))
        elif not checked:
            checked.append(_Checked(response, fallback="error", error=error))
        decision = self._decide(checked, seconds, error)
        if self.record is not None:
            self._write(decision, checked, prompt, user_type, record_id)
        return decision

    def _work(self, state, stop, end, prompt, response, user_type, wake) -> None:
        try:
            text = response
            for number in range(1 + self.retries):
                if stop.is_set():
                    return
                with state.lock:
                    state.pending = text
                checked = self._grade(prompt, text, user_type, end)
                with state.lock:
                    state.checked.append(checked)
                    state.pending = None
                if self._shown(checked, first=number == 0) or checked.fallback:
                    return
                if self.regenerate is None or number == self.retries or stop.is_set():
                    return
                text = self.regenerate(prompt, _held(checked))
                if not isinstance(text, str):
                    return
        except BaseException as exc:  # noqa: BLE001 (the caller decides from what was done)
            with state.lock:
                state.error = _error_text(exc)
        finally:
            with state.lock:
                state.finished = True
            if wake is not None:
                wake()

    def _grade(self, prompt, response, user_type, end) -> _Checked:
        if self.max_chars is not None and max(len(prompt), len(response)) > self.max_chars:
            return _Checked(response, fallback="too_long")
        try:
            with judge_module.deadline(end - time.monotonic()):
                row = self.grader.grade(prompt, response, user_type)
        except Exception as exc:  # noqa: BLE001 (a failed grade is a fallback, never a pass)
            return _Checked(response, fallback="error", error=_error_text(exc))
        if row.get("outcome") not in pipeline.WORST:
            return _Checked(response, row, "judge_error", "unknown outcome")
        # The judge only says pass or fail. A review it decided means no usable grade.
        if row["outcome"] == "review" and row.get("decided_by") == "judge":
            return _Checked(response, row, "judge_error")
        return _Checked(response, row)

    def _shown(self, checked: _Checked, first: bool) -> bool:
        """True when this answer may be shown."""
        if checked.fallback == "too_long":
            return False
        if checked.fallback:
            return first and self.on_error == "show"
        outcome = checked.row["outcome"]
        if outcome == "review":
            return self.on_review == "show"
        return outcome == "pass"

    def _decide(self, checked: list[_Checked], seconds: float, error: str | None) -> Decision:
        first, last = checked[0], checked[-1]
        shown = self._shown(last, first=len(checked) == 1)
        if shown and len(checked) == 1:
            action, text = "show", first.response
        elif shown:
            action, text = "regenerated", last.response
        else:
            text = self._replacement(first)
            action = "hold" if text is None else "replace"
        why = _why(first)
        fallback = next((c.fallback for c in reversed(checked) if c.fallback), None)
        errors = [c.error for c in checked if c.error] + ([error] if error else [])
        return Decision(
            allow=action == "show",
            action=action,
            text=text,
            outcome=why["outcome"],
            rule_id=why["rule_id"],
            reason_code=why["reason_code"],
            reason_text=why["reason_text"],
            decided_by=why["decided_by"],
            fallback=fallback,
            seconds=round(seconds, 4),
            judge_calls=sum(pipeline.judge_calls([c.row]) for c in checked if c.row),
            outcomes=tuple(_why(c)["outcome"] for c in checked),
            error=errors[0] if errors else None,
        )

    def _replacement(self, checked: _Checked) -> str | None:
        rule_id = checked.row.get("routed_rule") if checked.row else None
        return self.replacements.get(rule_id, self.replacement)

    def _write(self, decision, checked, prompt, user_type, record_id) -> None:
        at = datetime.now(UTC).isoformat(timespec="seconds")
        router = getattr(self.grader.router, "name", "center")
        lines, last = [], len(checked) - 1
        for number, item in enumerate(checked):
            row = dict(item.row) if item.row else _blank_row(item, router)
            row.pop("id", None)
            row = {"id": record_id, "user_type": user_type, **row}
            row["user_type"] = user_type
            row["prompt"] = prompt
            row["response"] = item.response
            if not self.record_text:
                row["prompt"] = row["response"] = None
                if row["decided_by"] == "judge":
                    row["reason_text"] = None
            row["guard"] = {
                "try": number,
                "shown": last == number and decision.action in ("show", "regenerated"),
                "fallback": item.fallback,
                "action": decision.action,
                "allow": decision.allow,
                "seconds": decision.seconds,
                "at": at,
            }
            lines.append(json.dumps(row) + "\n")
        with self._lock, self.record.open("a") as f:
            f.write("".join(lines))


def _blank_row(checked: _Checked, router: str) -> dict:
    """An output row for an answer that got no grade, in the shape run writes."""
    why = _why(checked)
    return {
        "prompt": None,
        "response": None,
        "routed_rule": None,
        "similarity": None,
        "router": router,
        "cell": None,
        "short_circuit": False,
        "outcome": why["outcome"],
        "decided_by": why["decided_by"],
        "reason_code": why["reason_code"],
        "reason_text": why["reason_text"],
        "severity": None,
    }


REASONS = {
    "deadline": "No grade arrived before the deadline.",
    "error": "Grading raised an error, so there is no grade.",
    "too_long": "The prompt or the answer is longer than the limit, so it was not judged.",
}


def _why(checked: _Checked) -> dict:
    """The outcome, rule and reason for one checked answer."""
    if checked.row is not None and checked.row.get("outcome") in pipeline.WORST:
        row = checked.row
        return {
            "outcome": row["outcome"],
            "rule_id": row.get("routed_rule"),
            "reason_code": row["reason_code"],
            "reason_text": row["reason_text"],
            "decided_by": row["decided_by"],
        }
    if checked.row is not None:
        # A judge of your own gave a verdict Redline does not know. It is not a pass.
        return {
            "outcome": "review",
            "rule_id": checked.row.get("routed_rule"),
            "reason_code": "JUDGE_ERROR",
            "reason_text": "The judge gave a verdict that is not pass or fail.",
            "decided_by": "judge",
        }
    return {
        "outcome": "review",
        "rule_id": None,
        "reason_code": GUARD_CODES[checked.fallback],
        "reason_text": REASONS[checked.fallback],
        "decided_by": "guard",
    }


def _held(checked: _Checked) -> dict:
    why = _why(checked)
    return {
        "response": checked.response,
        "rule_id": why["rule_id"],
        "outcome": why["outcome"],
        "reason_code": why["reason_code"],
        "reason_text": why["reason_text"],
    }
