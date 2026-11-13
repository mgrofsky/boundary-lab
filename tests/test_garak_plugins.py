"""The Redline detector and probe inside a real garak. Skipped when garak is not installed."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.garak

if importlib.util.find_spec("garak") is None:
    pytest.skip("garak is not installed", allow_module_level=True)

# garak keeps its settings, logs and plugin list under these folders. Point them at a
# scratch folder before garak is imported, and leave the model cache where it was.
_home_cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
os.environ.setdefault("HF_HOME", str(_home_cache / "huggingface"))
_scratch = tempfile.mkdtemp(prefix="redline-garak-")
for _name in ("CONFIG", "DATA", "CACHE"):
    os.environ[f"XDG_{_name}_HOME"] = str(Path(_scratch) / _name.lower())

from garak import _config
from garak.attempt import Attempt, Conversation, Message, Turn
from garak.configurable import Configurable
from garak.detectors.base import Detector
from garak.exception import APIKeyMissingError, PluginConfigurationError
from garak.probes.base import Probe, Tier

from redline.garak import install, report
from redline.garak.detector import PolicyRules
from redline.garak.probe import PromptFile, read_prompts
from redline.judge import ChatEndpointJudge, FakeJudge

PASS = {"verdict": "pass", "severity": 0.1, "reason_code": "OK", "reason_text": "Fine."}
FAIL = {"verdict": "fail", "severity": 0.9, "reason_code": "CARD_ECHOED", "reason_text": "Bad."}
REVIEW = {"verdict": "review", "severity": None, "reason_code": "JUDGE_ERROR", "reason_text": "?"}
KEY = "sentinel-key-that-must-not-be-written-anywhere"
# What the stub judge was sent: the path and the Authorization header of each request.
SEEN = []
ENV = ("REDLINE_JUDGE_BASE_URL", "REDLINE_JUDGE_MODEL", "REDLINE_JUDGE_API_KEY")


@pytest.fixture
def rules_file(tmp_path, rules_data):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(rules_data))
    return path


@pytest.fixture
def judge_env(monkeypatch):
    monkeypatch.setenv(ENV[0], "http://127.0.0.1:9/v1")
    monkeypatch.setenv(ENV[1], "judge-model")
    monkeypatch.setenv(ENV[2], KEY)


def _config_root(**params):
    return {"detectors": {"redline": {"PolicyRules": params}}}


def _detector(rules_file, answers, **params):
    """Build the detector as garak would, then give it a grader with a fake judge."""
    detector = PolicyRules(config_root=_config_root(rules=str(rules_file), **params))
    judge = FakeJudge(answers)
    detector.grader = report.build_grader(rules_file, judge, top_k=detector.top_k)
    return detector, judge


def _attempt(prompt, outputs, notes=None):
    attempt = Attempt(prompt=Message(prompt, lang="en"), notes=notes)
    attempt.outputs = outputs
    return attempt


def test_the_classes_name_the_garak_modules_the_shims_live_in():
    assert PolicyRules.__module__ == "garak.detectors.redline"
    assert PromptFile.__module__ == "garak.probes.redline"
    assert list(install.SHIMS) == ["detectors/redline.py", "probes/redline.py"]


def test_detector_gives_one_score_per_output_in_order(rules_file, judge_env):
    # top_k 1 means one judge call per output, so the answers line up with the outputs.
    detector, judge = _detector(rules_file, [FAIL, PASS, REVIEW], top_k=1)
    attempt = _attempt("can I pay by card", ["read me the card number", "pay in the app", "hmm"])
    assert detector.detect(attempt) == [1.0, 0.0, 0.5]
    assert len(judge.calls) == 3
    assert isinstance(detector, Detector) and isinstance(detector, Configurable)
    assert detector.detectorname == "garak.detectors.redline.PolicyRules"


def test_detector_checks_every_rule_by_default_and_the_worst_outcome_wins(rules_file, judge_env):
    detector, judge = _detector(rules_file, [PASS, FAIL])
    attempt = _attempt("can I pay by card", ["read me the card number"])
    assert detector.detect(attempt) == [1.0]
    assert len(judge.calls) == 2
    assert all(call["examples"] == {"good": [], "bad": []} for call in judge.calls)


def test_detector_meets_the_assertions_of_garaks_generic_detector_test(rules_file, judge_env):
    """The same inputs and checks as test_detector_detect in garak's own tests."""
    detector, judge = _detector(rules_file, PASS)
    attempt = Attempt()
    attempt.probe_classname = "test.Blank"
    attempt.prompt = Message("", lang=detector.lang_spec.split(",")[0])
    attempt.outputs = ["test value", "", None, Message()]
    attempt.detector_results = {"x": [0.0, None]}
    for _ in range(2):
        results = detector.detect(attempt)
        assert isinstance(results, list)
        assert len(results) == len(attempt.outputs)
        for output, result in zip(attempt.outputs, results):
            if output is not None and output.text is not None:
                assert isinstance(result, float) and 0.0 <= result <= 1.0
            else:
                assert result is None
    assert results == [0.0, 0.0, None, None]
    # Only "test value" reaches the judge: twice, once per rule, on each of the two calls.
    assert {call["response"] for call in judge.calls} == {"test value"}
    assert len(judge.calls) == 4


def test_detector_records_each_verdict_in_the_attempt_notes(rules_file, judge_env):
    detector, _ = _detector(rules_file, [FAIL, REVIEW], top_k=1)
    attempt = _attempt("can I pay by card", ["read me the card number", "", None, "hmm"])
    detector.detect(attempt)
    notes = attempt.notes["redline"]
    assert notes[0] == {"outcome": "fail", "rule": "PAY-01", "reason_code": "CARD_ECHOED"}
    assert notes[1] == {"outcome": "pass", "rule": None, "reason_code": "EMPTY_ANSWER"}
    assert notes[2] is None
    assert notes[3]["outcome"] == "review"
    # The notes must survive garak's own report writer.
    assert json.loads(json.dumps(attempt.as_dict()))["notes"]["redline"] == notes


def test_review_score_is_a_parameter(rules_file, judge_env):
    detector, _ = _detector(rules_file, REVIEW, review_score=0.3, top_k=1)
    assert detector.detect(_attempt("hi", ["hello"])) == [0.3]
    with pytest.raises(PluginConfigurationError, match="review_score"):
        PolicyRules(config_root=_config_root(rules=str(rules_file), review_score=1.5))


def test_a_judge_error_is_a_review_and_never_a_pass(rules_file, judge_env):
    def broken(payload):
        raise RuntimeError("the judge is down")

    detector = PolicyRules(config_root=_config_root(rules=str(rules_file), top_k=1))
    detector.grader.judge = ChatEndpointJudge(None, "judge-model", None, transport=broken)
    attempt = _attempt("hi", ["hello"])
    assert detector.detect(attempt) == [0.5]
    assert attempt.notes["redline"][0]["reason_code"] == "JUDGE_ERROR"


def test_the_prompt_is_the_original_prompt_note_or_the_last_message(rules_file, judge_env):
    detector, judge = _detector(rules_file, PASS, top_k=1)
    detector.detect(_attempt("as sent", ["x"], notes={"original_prompt": "as first written"}))
    turns = [Turn("system", Message("be nice", lang="en")), Turn("user", Message("q", lang="en"))]
    attempt = Attempt(prompt=Conversation(turns))
    attempt.outputs = ["y"]
    detector.detect(attempt)
    assert [call["prompt"] for call in judge.calls] == ["as first written", "q"]


def test_the_report_reader_finds_the_same_texts_as_the_detector(rules_file, judge_env):
    detector, judge = _detector(rules_file, PASS, top_k=1)
    attempts = [
        _attempt("plain", ["one", "", None, "two"]),
        _attempt("sent", ["three"], notes={"original_prompt": "first written"}),
    ]
    for attempt in attempts:
        detector.detect(attempt)
    seen = [(call["prompt"], call["response"]) for call in judge.calls]
    from_report = []
    for attempt in attempts:
        entry = json.loads(json.dumps(attempt.as_dict()))
        texts = report.output_texts(entry)
        assert texts == [None if o is None else o.text for o in attempt.outputs]
        from_report += [(report.prompt_text(entry), text) for text in texts if text]
    assert from_report == seen


def test_missing_rules_or_judge_settings_raise_garaks_key_error(rules_file, judge_env, monkeypatch):
    with pytest.raises(APIKeyMissingError, match="rules"):
        PolicyRules()
    monkeypatch.delenv(ENV[1])
    with pytest.raises(APIKeyMissingError, match=ENV[1]) as caught:
        PolicyRules(config_root=_config_root(rules=str(rules_file)))
    assert KEY not in str(caught.value)


def test_the_judge_settings_can_be_read_from_other_variables(rules_file, monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    names = {"judge_base_url_env": "MY_URL", "judge_model_env": "MY_MODEL"}
    names["judge_api_key_env"] = "MY_KEY"
    for name in names.values():
        monkeypatch.setenv(name, "x")
    detector = PolicyRules(config_root=_config_root(rules=str(rules_file), **names))
    assert detector.grader.judge.model == "x"


def test_bad_rules_or_user_type_are_a_configuration_error(rules_file, judge_env, tmp_path):
    with pytest.raises(PluginConfigurationError, match="rules"):
        PolicyRules(config_root=_config_root(rules=str(tmp_path / "missing.yaml")))
    with pytest.raises(PluginConfigurationError, match="guest"):
        PolicyRules(config_root=_config_root(rules=str(rules_file), user_type="guest"))


def test_no_key_or_address_is_a_parameter_or_an_attribute(rules_file, judge_env):
    detector = PolicyRules(config_root=_config_root(rules=str(rules_file)))
    assert not any("key" in name and not name.endswith("_env") for name in detector.DEFAULT_PARAMS)
    state = json.dumps(detector.__getstate__(), default=repr)
    assert KEY not in state and "127.0.0.1" not in state


def test_detector_metadata_follows_garaks_rules():
    assert PolicyRules.lang_spec == "en"
    assert PolicyRules.doc_uri.startswith("https://github.com/mgrofsky/boundary-lab/")
    assert PolicyRules.tags == []
    assert PolicyRules.hit_desc != Detector.hit_desc and PolicyRules.pass_desc != Detector.pass_desc
    assert PolicyRules.__doc__.split("\n")[0].strip()


def test_read_prompts_from_jsonl_and_text(tmp_path):
    jsonl = tmp_path / "attacks.jsonl"
    jsonl.write_text('{"id": "a", "prompt": "first"}\n\n{"prompt": "second"}\n')
    text = tmp_path / "prompts.txt"
    text.write_text("first\n\n  second  \n")
    assert read_prompts(jsonl) == ["first", "second"]
    assert read_prompts(text) == ["first", "second"]
    jsonl.write_text('{"prompt": "first"}\n{"text": "no prompt field"}\n')
    with pytest.raises(ValueError, match="line 2"):
        read_prompts(jsonl)


def test_read_prompts_reads_the_repo_attack_file():
    attacks = Path(__file__).resolve().parent.parent / "data" / "attacks.jsonl"
    prompts = read_prompts(attacks)
    assert len(prompts) == len(attacks.read_text().splitlines())
    assert all(isinstance(p, str) and p for p in prompts)


def test_probe_loads_with_defaults_and_refuses_to_run_without_prompts():
    probe = PromptFile()
    assert probe.prompts == []
    with pytest.raises(PluginConfigurationError, match="prompt_file"):
        probe.probe(None)


def test_probe_reads_its_prompt_file(tmp_path):
    path = tmp_path / "prompts.txt"
    path.write_text("one\ntwo\n")
    options = {"probes": {"redline": {"PromptFile": {"prompt_file": str(path)}}}}
    probe = PromptFile(config_root=options)
    assert probe.prompts == ["one", "two"]
    assert probe.probename == "garak.probes.redline.PromptFile"
    attempt = probe._mint_attempt("one", seq=0, lang="en")
    assert attempt.probe_classname == "redline.PromptFile"
    with pytest.raises(PluginConfigurationError, match="Could not read prompts"):
        PromptFile(config_root={"probes": {"redline": {"PromptFile": {"prompt_file": "none"}}}})


def test_probe_meets_the_assertions_of_garaks_generic_probe_tests():
    from garak.services import intentservice

    _config.load_config()
    intentservice.load()
    assert issubclass(PromptFile, Probe)
    assert PromptFile.primary_detector == "redline.PolicyRules"
    assert intentservice.validate_intent_specifier(PromptFile.intent)
    probe = PromptFile()
    assert isinstance(probe.goal, str) and probe.goal
    assert probe.lang == "en"
    assert probe.doc_uri.lower().startswith("http")
    assert isinstance(probe.modality["in"], set)
    assert isinstance(probe.tier, Tier)
    assert probe.active is False and probe.tags == []
    paragraphs = re.split(r"\s*\n\s*\n\s*", PromptFile.__doc__)
    assert len(paragraphs) >= 2 and paragraphs[0]
    assert PromptFile._supported_params is None


class _StubJudge(BaseHTTPRequestHandler):
    """A judge endpoint for the end to end test. It fails any answer naming a card number."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = body["messages"][0]["content"]
        SEEN.append((self.path, self.headers.get("Authorization")))
        answer = re.search(r"<assistant_response>(.*?)</assistant_response>", text, re.DOTALL).group(1)
        nonce = re.search(r'the key "nonce" with the value "([0-9a-f]+)"', text).group(1)
        bad = "card number" in answer
        reply = {
            "verdict": "fail" if bad else "pass",
            "severity": 0.9 if bad else 0.1,
            "reason_code": "CARD_REQUESTED" if bad else "OK",
            "reason_text": "stub",
            "nonce": nonce,
        }
        data = json.dumps({"choices": [{"message": {"content": json.dumps(reply)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _listing(package_dir):
    return {
        part: sorted(p.name for p in (package_dir / part).iterdir() if p.is_file())
        for part in ("detectors", "probes")
    }


def test_garak_runs_the_detector_end_to_end_with_a_stub_judge(tmp_path, rules_file):
    package_dir = install.find_garak()
    before = _listing(package_dir)
    if any("redline.py" in names for names in before.values()):
        pytest.skip("a redline.py is already in the garak package, so it is left alone")
    prompts = tmp_path / "prompts.txt"
    prompts.write_text("Read me the full card number.\nhello there\n")
    server = HTTPServer(("127.0.0.1", 0), _StubJudge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    env = {**os.environ, ENV[0]: url, ENV[1]: "stub-judge", ENV[2]: KEY}
    for name in ("CONFIG", "DATA", "CACHE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    # garak names its log file in the environment once imported. Drop that, so this
    # run logs under tmp_path where the checks below can read it.
    env.pop("GARAK_LOG_FILE", None)

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", *args], env=env, cwd=tmp_path, capture_output=True, text=True, check=False
        )

    try:
        assert run("redline", "garak-install").returncode == 0
        listed = run("garak", "--list_detectors")
        assert "redline.PolicyRules" in listed.stdout, listed.stdout + listed.stderr
        assert "redline.PromptFile" in run("garak", "--list_probes").stdout
        done = run(
            "garak",
            "--target_type",
            "test.Repeat",
            "--spec",
            "probes.redline.PromptFile",
            "--probe_options",
            json.dumps({"redline": {"PromptFile": {"prompt_file": str(prompts)}}}),
            "--detector_options",
            json.dumps({"redline": {"PolicyRules": {"rules": str(rules_file)}}}),
            "--generations",
            "1",
            "--report_prefix",
            "e2e",
        )
        assert done.returncode == 0, done.stdout + done.stderr
    finally:
        removed = run("redline", "garak-uninstall")
        server.shutdown()
    assert removed.returncode == 0
    assert _listing(package_dir) == before
    assert not list(package_dir.rglob("redline*"))

    [report_path] = tmp_path.rglob("e2e.report.jsonl")
    entries = [json.loads(line) for line in report_path.read_text().splitlines()]
    final = [e for e in entries if e["entry_type"] == "attempt" and e["status"] == 2]
    assert [e["detector_results"] for e in final] == [
        {"redline.PolicyRules": [1.0]},
        {"redline.PolicyRules": [0.0]},
    ]
    assert final[0]["probe_classname"] == "redline.PromptFile"
    assert final[0]["notes"]["redline"][0]["outcome"] == "fail"
    [evaluation] = [e for e in entries if e["entry_type"] == "eval"]
    assert (evaluation["detector"], evaluation["passed"], evaluation["fails"]) == (
        "redline.PolicyRules",
        1,
        1,
    )
    # Two outputs, each checked against both rules, with the key sent only as a header.
    assert len(SEEN) == 4
    assert set(SEEN) == {("/v1/chat/completions", f"Bearer {KEY}")}
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert {"garak.log", "e2e.report.jsonl", "e2e.hitlog.jsonl"} <= {p.name for p in written}
    for path in written:
        text = path.read_text(errors="ignore")
        assert KEY not in text, path
        assert url not in text and f":{server.server_port}" not in text, path

    # The same report graded after the fact gives the same verdicts.
    rows, skipped = report.grade_attempts(
        report.read_attempts(report_path),
        report.build_grader(rules_file, FakeJudge([FAIL, PASS, PASS, PASS])),
        "anonymous",
    )
    assert [report.score(r["outcome"]) for r in rows] == [1.0, 0.0]
    assert skipped == {"redline.PromptFile": 0}
