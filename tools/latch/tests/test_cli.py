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
    assert result.stdout.startswith("policy ok, 2 tools, digest ")
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
