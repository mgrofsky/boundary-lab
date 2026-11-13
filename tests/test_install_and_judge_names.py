"""The smaller install, the judge names an application imports, and the saved grades file."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx
import pytest

from redline import Grader, cli, embed
from redline import judge as judge_module
from redline.judge import ChatEndpointJudge, FakeJudge, ReplayJudge

ROOT = Path(__file__).parent.parent
PASS = {"verdict": "pass", "severity": 0.0, "reason_code": "OK", "reason_text": "ok"}
ARGS = ("can I pay by card", "Yes, in the app.", "anonymous", "The user is anonymous.")
NONE = {"good": [], "bad": []}
HINT = 'pip install "boundary-redline[minilm] @ git+https://github.com/mgrofsky/boundary-lab"'


@pytest.fixture
def no_minilm(monkeypatch):
    """Make sentence-transformers look as it does when it is not installed."""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)


def test_sentence_transformers_is_an_extra_and_stays_in_dev():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert not [d for d in project["dependencies"] if d.startswith("sentence-transformers")]
    extras = project["optional-dependencies"]
    assert [d for d in extras["minilm"] if d.startswith("sentence-transformers")]
    assert [d for d in extras["dev"] if d.startswith("sentence-transformers")]


def test_asking_for_minilm_without_it_gives_one_plain_error(no_minilm):
    with pytest.raises(embed.MissingExtra) as caught:
        embed.make("minilm")
    assert HINT in str(caught.value)
    assert "\n" not in str(caught.value)
    assert isinstance(caught.value, ImportError)


def test_tfidf_and_rules_only_mode_work_without_it(no_minilm, rules_data):
    judge = FakeJudge(PASS)
    row = Grader.from_rules(rules_data, judge).grade("q", "a plain answer", "anonymous")
    assert row["outcome"] == "pass"


def test_the_calibrated_mode_still_defaults_to_minilm_and_says_what_to_install(
    no_minilm, rules_data, write_rules, tmp_path, capsys
):
    data = tmp_path / "answers.jsonl"
    data.write_text(json.dumps({"id": "a", "user_type": "anonymous", "prompt": "q", "response": "r"}))
    train = tmp_path / "train.jsonl"
    train.write_text("")
    cache = tmp_path / "cache.jsonl"
    cache.write_text("")
    args = ["run", "--data", str(data), "--out", str(tmp_path / "out"), "--judge", "replay"]
    args += ["--rules", str(write_rules(rules_data)), "--train", str(train), "--cache", str(cache)]
    assert cli.main(args) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("redline: ") and HINT in err
    assert len(err.splitlines()) == 1


def test_garak_grade_with_a_train_file_says_what_to_install(
    no_minilm, rules_data, write_rules, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_live_judge", lambda *args, **kwargs: FakeJudge(PASS))
    train = tmp_path / "train.jsonl"
    train.write_text("")
    report = ROOT / "tests" / "fixtures" / "garak_report.jsonl"
    args = ["garak-grade", str(report), "--rules", str(write_rules(rules_data))]
    assert cli.main([*args, "--out", str(tmp_path / "out"), "--train", str(train)]) == 2
    err = capsys.readouterr().err.strip()
    assert err.startswith("redline: ") and HINT in err
    assert len(err.splitlines()) == 1
    assert not (tmp_path / "out").exists()


def test_importing_redline_loads_no_model_library():
    code = (
        "import sys, redline, redline.garak, redline.cli\n"
        "print([m for m in ('torch', 'sentence_transformers', 'garak') if m in sys.modules])"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_the_judge_names_are_importable_as_documented():
    assert set(judge_module.__all__) >= {"ChatEndpointJudge", "FakeJudge", "ReplayJudge"}


def test_the_judge_timeout_is_a_constructor_argument(monkeypatch, pay_rule):
    seen = []

    def post(url, **kwargs):
        seen.append(kwargs)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(judge_module, "_sleep", lambda seconds: None)
    ChatEndpointJudge("http://unused", "m", "k", tries=1).grade(pay_rule, NONE, *ARGS)
    ChatEndpointJudge("http://unused", "m", "k", tries=1, timeout=5).grade(pay_rule, NONE, *ARGS)
    assert [kwargs["timeout"] for kwargs in seen] == [60.0, 5]
    assert [kwargs["json"]["temperature"] for kwargs in seen] == [0, 0]


def _echo(payload):
    nonce = payload["messages"][0]["content"].rsplit('value "', 1)[1].split('"')[0]
    return json.dumps({**PASS, "nonce": nonce})


def test_a_new_saved_grade_line_carries_the_format_version(tmp_path, pay_rule):
    cache = tmp_path / "cache.jsonl"
    live = ChatEndpointJudge("http://unused", "m", "k", transport=_echo, cache_path=cache)
    answer = live.grade(pay_rule, NONE, *ARGS)
    [entry] = [json.loads(line) for line in cache.read_text().splitlines()]
    assert entry["version"] == judge_module.SAVED_GRADES_VERSION == 1
    assert sorted(entry) == ["answer", "key", "model", "rule_id", "version"]
    assert ReplayJudge(cache).grade(pay_rule, NONE, *ARGS) == answer


def test_lines_with_no_version_are_still_read(tmp_path, pay_rule):
    cache = tmp_path / "cache.jsonl"
    key = judge_module.prompt_key(judge_module.build_prompt(pay_rule, NONE, *ARGS))
    cache.write_text(json.dumps({"key": key, "model": "m", "rule_id": "PAY-01", "answer": PASS}) + "\n")
    assert ReplayJudge(cache).grade(pay_rule, NONE, *ARGS) == PASS
    live = ChatEndpointJudge("http://unused", "m", "k", transport=_echo, cache_path=cache)
    assert live.grade(pay_rule, NONE, *ARGS) == PASS


@pytest.mark.parametrize("make", [ReplayJudge, lambda path: ChatEndpointJudge(None, "m", None, cache_path=path, transport=_echo)])
def test_a_line_from_a_later_format_is_refused_with_its_line_number(tmp_path, make):
    cache = tmp_path / "cache.jsonl"
    cache.write_text("\n" + json.dumps({"version": 2, "key": "k", "answer": PASS}) + "\n")
    with pytest.raises(ValueError, match="cache.jsonl: line 2 is saved grades format version 2"):
        make(cache)


def test_the_pinned_saved_grades_have_no_version_and_still_load():
    path = ROOT / "data" / "judge_cache.jsonl"
    first = json.loads(path.read_text().splitlines()[0])
    assert "version" not in first
    assert ReplayJudge(path).answers
