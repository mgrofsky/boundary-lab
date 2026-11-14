import subprocess
import sys

import boundary_latch

GOOD = """
[tools.lookup_account]
require_permission = "accounts:read"

[tools.send_message]
require_permission = "messages:send"
"""


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "boundary_latch", *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


def test_check_accepts_a_valid_policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(GOOD)
    result = run_cli("check", str(path))
    assert result.returncode == 0
    assert result.stdout.startswith("policy ok, 2 tools, policy hash ")
    digest = boundary_latch.load_policy(path).digest
    assert result.stdout.strip().endswith(digest[:12])


def test_check_rejects_a_bad_policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('[tools.lookup_account]\nrequire_permission = "accounts:read"\nfoo = 1\n')
    result = run_cli("check", str(path))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("policy rejected: ")


def test_check_rejects_bad_toml(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text("[tools.lookup_account\n")
    result = run_cli("check", str(path))
    assert result.returncode == 1
    assert result.stderr.startswith("policy rejected: ")


def test_serve_passes_its_flags(monkeypatch):
    from boundary_latch import cli
    from boundary_latch.demo import server

    seen = {}
    monkeypatch.setattr(server, "serve", lambda **kwargs: seen.update(kwargs))
    assert cli.main(["serve", "--port", "8800", "--db", "x.sqlite", "--live"]) == 0
    assert seen == {"port": 8800, "db": "x.sqlite", "live": True}
    assert cli.main(["serve"]) == 0
    assert seen == {"port": 8765, "db": "latch-demo.sqlite", "live": False}


def test_serve_reports_a_config_error(monkeypatch, capsys):
    from boundary_latch import cli

    monkeypatch.delenv("LATCH_BASE_URL", raising=False)
    monkeypatch.delenv("LATCH_MODEL", raising=False)
    assert cli.main(["serve", "--live"]) == 2
    assert "live mode needs LATCH_BASE_URL and LATCH_MODEL" in capsys.readouterr().err


def test_public_names():
    for name in (
        "Latch",
        "SQLiteStore",
        "ToolSpec",
        "UserContext",
        "CallInfo",
        "Decision",
        "PreparedAction",
        "ExecutionResult",
        "OperationStatus",
        "Code",
        "explain",
        "LatchError",
        "ConfigError",
        "StorageError",
        "ToolFailed",
        "load_policy",
        "parse_policy",
        "__version__",
    ):
        assert hasattr(boundary_latch, name), name
    assert set(boundary_latch.__all__) >= {"Latch", "load_policy", "ToolFailed"}


FLOW = """
[flow]
all_three = "deny"

[tools.get_issue]
require_permission = "use"
labels = ["untrusted"]

[tools.read_and_send]
require_permission = "use"
labels = ["sensitive", "external"]

[tools.create_pull_request]
require_permission = "use"
{labels}
"""


def test_check_prints_the_flow_mode_and_how_many_tools_carry_each_label(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(FLOW.format(labels='labels = ["external"]'))
    result = run_cli("check", str(path))
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("policy ok, 3 tools, policy hash ")
    assert lines[1] == 'flow rule: all_three = "deny"; tools with each label: untrusted 1, sensitive 1, external 2'


def test_check_says_nothing_about_flow_when_the_rule_is_off(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(GOOD)
    result = run_cli("check", str(path))
    assert "flow rule" not in result.stdout


def test_check_rejects_a_tool_without_labels_when_the_rule_is_on(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text(FLOW.format(labels=""))
    result = run_cli("check", str(path))
    assert result.returncode == 1
    assert "create_pull_request" in result.stderr
    assert "needs labels" in result.stderr
    path.write_text(FLOW.format(labels="labels = []"))
    assert run_cli("check", str(path)).returncode == 0
