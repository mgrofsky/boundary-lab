"""Writing and removing the garak shim files. These tests use a fake package folder."""

import pytest

from redline import cli
from redline.garak import install

NAMES = ["detectors/redline.py", "probes/redline.py"]


@pytest.fixture
def package(tmp_path):
    """A folder laid out like the garak package, with one plugin of its own in each part."""
    root = tmp_path / "garak"
    for part in ("detectors", "probes"):
        (root / part).mkdir(parents=True)
        (root / part / "base.py").write_text("# garak's own file\n")
    return root


def _listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_each_shim_is_a_comment_a_docstring_and_one_import():
    assert list(install.SHIMS) == NAMES
    for content, target in zip(install.SHIMS.values(), ["detector import PolicyRules", "probe import PromptFile"]):
        code = [line for line in content.splitlines() if line and not line.startswith("#")]
        assert len(code) == 2
        assert code[0].startswith('"""') and code[0].endswith('"""')
        assert code[1] == f"from redline.garak.{target}  # noqa: F401"
        assert "redline garak-uninstall" in content


def test_install_writes_both_shims_and_says_where(package):
    code, lines = install.install(package)
    assert code == 0
    assert lines == [f"wrote {package / name}" for name in NAMES]
    for name, content in install.SHIMS.items():
        assert (package / name).read_text() == content


def test_dry_run_changes_nothing(package):
    before = _listing(package)
    code, lines = install.install(package, dry_run=True)
    assert code == 0
    assert lines == [f"would write {package / name}" for name in NAMES]
    assert _listing(package) == before


def test_install_twice_is_harmless(package):
    install.install(package)
    code, lines = install.install(package)
    assert code == 0
    assert all(line.startswith("already there") for line in lines)


def test_install_refuses_when_another_file_is_there(package):
    other = package / "probes" / "redline.py"
    other.write_text("# someone else's plugin\n")
    code, lines = install.install(package)
    assert code == 1
    assert "is not the Redline shim" in lines[0]
    assert other.read_text() == "# someone else's plugin\n"
    assert not (package / "detectors" / "redline.py").exists()


def test_install_refuses_a_folder_that_is_not_a_garak_package(tmp_path):
    code, lines = install.install(tmp_path / "nothing")
    assert code == 1
    assert "Nothing was changed" in lines[0]
    assert not (tmp_path / "nothing").exists()


def test_uninstall_leaves_the_folder_as_it_was(package):
    before = _listing(package)
    install.install(package)
    for part in ("detectors", "probes"):
        cache = package / part / "__pycache__"
        cache.mkdir()
        (cache / "redline.cpython-312.pyc").write_bytes(b"compiled")
        (cache / "base.cpython-312.pyc").write_bytes(b"garak's own")
    code, lines = install.uninstall(package)
    assert code == 0
    assert len(lines) == 4
    after = _listing(package)
    assert [p for p in after if "redline" in p] == []
    assert [p for p in after if "__pycache__" not in p] == before
    assert (package / "detectors" / "__pycache__" / "base.cpython-312.pyc").exists()


def test_uninstall_leaves_a_file_that_is_not_the_shim(package):
    install.install(package)
    changed = package / "detectors" / "redline.py"
    changed.write_text(changed.read_text() + "# edited\n")
    code, lines = install.uninstall(package)
    assert code == 1
    assert changed.exists()
    assert "left alone" in lines[0]
    assert not (package / "probes" / "redline.py").exists()


def test_uninstall_with_nothing_there_says_so(package):
    code, lines = install.uninstall(package)
    assert code == 0
    assert all(line.startswith("not there") for line in lines)


def test_uninstall_dry_run_changes_nothing(package):
    install.install(package)
    before = _listing(package)
    code, lines = install.uninstall(package, dry_run=True)
    assert code == 0
    assert all(line.startswith("would remove") for line in lines)
    assert _listing(package) == before


def test_commands_exit_2_when_garak_is_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(install, "find_garak", lambda: None)
    assert cli.main(["garak-install"]) == 2
    assert cli.main(["garak-uninstall", "--dry-run"]) == 2
    assert "garak is not installed" in capsys.readouterr().err


def test_commands_print_the_paths(monkeypatch, capsys, package):
    monkeypatch.setattr(install, "find_garak", lambda: package)
    assert cli.main(["garak-install", "--dry-run"]) == 0
    assert "would write" in capsys.readouterr().out
    assert cli.main(["garak-install"]) == 0
    assert str(package / "detectors" / "redline.py") in capsys.readouterr().out
    assert cli.main(["garak-uninstall"]) == 0
    assert "removed" in capsys.readouterr().out
    assert not (package / "detectors" / "redline.py").exists()


def test_find_garak_does_not_import_garak():
    import sys

    had = "garak" in sys.modules
    install.find_garak()
    assert ("garak" in sys.modules) == had
