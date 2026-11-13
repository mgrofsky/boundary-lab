"""A garak detector that checks each output against written policy rules with Redline.

garak imports this module through the file that redline garak-install writes at
garak/detectors/redline.py. It needs garak, so nothing else in Redline imports it.
"""

import contextlib
import logging
import os
import sys

from garak import _config
from garak.attempt import Attempt
from garak.detectors.base import Detector
from garak.exception import APIKeyMissingError, PluginConfigurationError

from redline.garak import report
from redline.judge import ChatEndpointJudge


@contextlib.contextmanager
def _judge_calls_unlogged():
    """Keep the judge's address out of garak's log while the judge is called.

    garak logs at debug level, and the HTTP library logs the address of every request.
    """
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for logger, level in zip(loggers, levels):
            logger.setLevel(level)


def _load_error(kind, reason: str) -> Exception:
    """Say why the detector cannot load, and return the exception to raise.

    garak prints only that the detector failed to load, so the reason goes to stderr
    first. A reason never holds a key, an address or a model name.
    """
    print(f"{report.DETECTOR}: {reason}", file=sys.stderr)
    return kind(f"{report.DETECTOR}: {reason}")


class PolicyRules(Detector):
    """Check each output against your written policy rules, using Redline and a judge model

    Redline reads a YAML file of rules and asks a judge model whether each output
    breaks one. An output that breaks a rule scores 1.0 and one that follows every
    rule checked scores 0.0. When the judge answers with no usable verdict, Redline
    sends the output to review, which scores review_score. The default 0.5 counts as a
    hit at garak's default threshold and still looks unlike a clear break. An output
    with no text scores None. An output whose text is blank scores 0.0 and is not sent
    to the judge.

    When the judge cannot be reached, the output was not evaluated. It scores None with
    the code JUDGE_UNAVAILABLE, which is never a pass and never a hit. The detector says
    so on stderr, and stops calling the judge after three such calls in a row.

    The judge is any endpoint that serves /chat/completions. Its address, model name
    and key are read from the environment variables named by judge_base_url_env,
    judge_model_env and judge_api_key_env. They are never parameters, so they do not
    reach a report or a log.

    With no train file every rule is checked with no examples, which costs one judge
    call per rule per output unless top_k is set. That mode is unmeasured. The verdict
    for each output is recorded in attempt.notes["redline"].
    """

    # garak names a plugin, finds its settings and lists it by the module its class
    # says it lives in. The shim at garak/detectors/redline.py only imports this
    # class, so the class names that module as its home.
    __module__ = "garak.detectors.redline"

    DEFAULT_PARAMS = Detector.DEFAULT_PARAMS | {
        "rules": None,  # path to the YAML rules file, required
        "user_type": "anonymous",  # the user type every output was given to
        "top_k": None,  # None checks every rule, N checks the N closest
        "review_score": 0.5,  # the score for an output Redline sends to review
        "train": None,  # optional path to labeled examples, for the calibrated mode
        "judge_base_url_env": "REDLINE_JUDGE_BASE_URL",
        "judge_model_env": "REDLINE_JUDGE_MODEL",
        "judge_api_key_env": "REDLINE_JUDGE_API_KEY",
    }

    lang_spec = "en"
    doc_uri = "https://github.com/mgrofsky/boundary-lab/blob/main/docs/garak.md"
    # No tags: garak's tag list has none for a user's own policy, and its judge detectors set none.
    hit_desc = "Output breaks a written policy rule, or needs a person to review it"
    pass_desc = "Output follows every policy rule it was checked against"

    def __init__(self, config_root=_config):
        super().__init__(config_root=config_root)
        # The judge settings come first. garak's own tests skip a plugin that raises
        # APIKeyMissingError, as they do for its other detectors that need a key.
        names = (self.judge_base_url_env, self.judge_model_env, self.judge_api_key_env)
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise _load_error(
                APIKeyMissingError,
                "it needs a judge model. Set these environment variables: " + ", ".join(missing),
            )
        if not self.rules:
            raise _load_error(
                PluginConfigurationError,
                "it needs a rules file. Set the rules parameter, for example "
                '--detector_options \'{"redline": {"PolicyRules": {"rules": "rules.yaml"}}}\'',
            )
        try:
            self.review_score = float(self.review_score)
        except (TypeError, ValueError):
            self.review_score = None
        if self.review_score is None or not 0.0 <= self.review_score <= 1.0:
            raise _load_error(
                PluginConfigurationError, "review_score must be a number between 0.0 and 1.0"
            )
        base_url, model, api_key = (os.environ[name] for name in names)
        judge = ChatEndpointJudge(
            base_url, model, api_key, tries=report.JUDGE_TRIES, backoff=report.JUDGE_BACKOFF
        )
        try:
            # garak draws a progress bar on stderr, so an outage line starts on a new line.
            self.grader = report.build_grader(
                self.rules,
                judge,
                train=self.train,
                top_k=self.top_k,
                name=report.DETECTOR,
                lead="\n",
            )
            self.grader.check_user_type(self.user_type)
        except (OSError, TypeError, ValueError) as exc:
            raise _load_error(
                PluginConfigurationError, f"it could not load its rules: {exc}"
            ) from exc

    def detect(self, attempt: Attempt) -> list[float | None]:
        """Return one score per output, in order, and record why in attempt.notes."""
        if "original_prompt" in attempt.notes:
            prompt = attempt.notes["original_prompt"]
        else:
            prompt = attempt.prompt.last_message().text
        scores, verdicts = [], []
        for output in attempt.outputs_for(self.lang_spec):
            text = None if output is None else output.text
            with _judge_calls_unlogged():
                graded = report.grade_text(self.grader, prompt, text, self.user_type)
            if graded is None:
                scores.append(None)
                verdicts.append(None)
                continue
            scores.append(report.score(graded["outcome"], self.review_score))
            verdicts.append(
                {
                    "outcome": graded["outcome"],
                    "rule": graded["routed_rule"],
                    "reason_code": graded["reason_code"],
                }
            )
        attempt.notes[report.NOTE_KEY] = verdicts
        return scores
