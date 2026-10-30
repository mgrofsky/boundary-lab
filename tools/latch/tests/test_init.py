import os
import subprocess
import sys
from pathlib import Path

from boundary_latch.cli import main

SRC = Path(__file__).resolve().parent.parent / "src"
FILES = ["README.md", "pyproject.toml", "policy.toml", "app.py", "tests/test_app.py"]


def test_init_writes_the_five_files(tmp_path, capsys):
    target = tmp_path / "x"
    assert main(["init", str(target)]) == 0
    for name in FILES:
        assert (target / name).is_file()
    out = capsys.readouterr().out
    for name in FILES:
        assert name in out
    assert not list(target.rglob("__pycache__"))


def test_init_into_an_empty_directory(tmp_path):
    target = tmp_path / "empty"
    target.mkdir()
    assert main(["init", str(target)]) == 0
    assert (target / "app.py").is_file()


def test_init_refuses_a_nonempty_directory(tmp_path, capsys):
    target = tmp_path / "x"
    assert main(["init", str(target)]) == 0
    capsys.readouterr()
    before = (target / "app.py").read_text()
    assert main(["init", str(target)]) == 1
    assert "the destination folder must be empty" in capsys.readouterr().err
    assert (target / "app.py").read_text() == before


def test_starter_tests_pass(tmp_path):
    target = tmp_path / "x"
    assert main(["init", str(target)]) == 0
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        check=False,
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
