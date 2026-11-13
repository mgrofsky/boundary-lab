"""Writing and removing the garak shim files. These tests use a fake package folder."""

import builtins
import os
import runpy
import types
from pathlib import Path

import pytest

from redline import cli
from redline.garak import install

NAMES = ["detectors/redline.py", "probes/redline.py"]
# What Redline 0.4.0 wrote, kept here in full so a change to install.py cannot move it.
SHIM_0_4_0 = {
    "detectors/redline.py": '''\
# Written by "redline garak-install". Remove it with "redline garak-uninstall".
# garak loads plugins only from inside its own package, so this file makes one
# Redline plugin visible to it. The code lives in the redline package.
"""Check outputs against written policy rules with Redline."""

from redline.garak.detector import PolicyRules  # noqa: F401
''',
    "probes/redline.py": '''\
# Written by "redline garak-install". Remove it with "redline garak-uninstall".
# garak loads plugins only from inside its own package, so this file makes one
# Redline plugin visible to it. The code lives in the redline package.
"""Send the prompts in a file you supply, for Redline to check the answers."""

from redline.garak.probe import PromptFile  # noqa: F401
''',
}
needs_file_modes = pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="a read only folder stops nothing for this user",
)


@pytest.fixture
def package(tmp_path):
    """A folder laid out like the garak package, with one plugin of its own in each part."""
    root = tmp_path / "garak"
    for part in ("detectors", "probes"):
        (root / part).mkdir(parents=True)
        (root / part / "base.py").write_text("# garak's own file\n")
    (root / "__init__.py").write_text("")
    return root


@pytest.fixture
def read_only():
    """Make folders read only for one test, and writable again whatever happens."""
    locked = []

    def lock(folder):
        locked.append((folder, folder.stat().st_mode))
        folder.chmod(0o555)

    yield lock
    for folder, mode in locked:
        folder.chmod(mode)


def _listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _contents(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _write_old(package):
    for name, text in SHIM_0_4_0.items():
        (package / name).write_text(text)


def test_each_shim_is_one_guarded_import():
    assert list(install.SHIMS) == NAMES
    targets = ["detector import PolicyRules", "probe import PromptFile"]
    for content, target in zip(install.SHIMS.values(), targets):
        lines = content.splitlines()
        code = [line for line in lines if line and not line.startswith("#")]
        assert code[0].startswith('"""') and code[0].endswith('"""')
        assert code[1:] == [
            "try:",
            f"    from redline.garak.{target}  # noqa: F401",
            "except ImportError:",
            "    pass",
        ]
        # One comment line above the guard says why it is there.
        assert lines[lines.index("try:") - 1].startswith("# ")
        assert not lines[lines.index("try:") - 2]
        assert "redline garak-uninstall" in content


def test_a_shim_does_not_raise_when_redline_cannot_be_imported(monkeypatch, package):
    real = builtins.__import__

    def gone(name, *args, **kwargs):
        if name.split(".")[0] == "redline":
            raise ModuleNotFoundError("No module named 'redline'")
        return real(name, *args, **kwargs)

    install.install(package)
    monkeypatch.setattr(builtins, "__import__", gone)
    for name in install.SHIMS:
        scope = runpy.run_path(str(package / name))
        assert "PolicyRules" not in scope and "PromptFile" not in scope


def test_the_text_0_4_0_wrote_is_known_as_an_earlier_shim():
    for name, text in SHIM_0_4_0.items():
        assert text in install.EARLIER[name]
        assert text != install.SHIMS[name]


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


def test_install_upgrades_an_older_shim_in_place_and_says_so(package):
    _write_old(package)
    code, lines = install.install(package, dry_run=True)
    assert code == 0 and all(line.startswith("would upgrade") for line in lines)
    assert (package / NAMES[0]).read_text() == SHIM_0_4_0[NAMES[0]]
    code, lines = install.install(package)
    assert code == 0
    assert all(line.startswith(f"upgraded {package}") for line in lines) and len(lines) == 2
    for name, content in install.SHIMS.items():
        assert (package / name).read_text() == content


def test_uninstall_removes_an_older_shim(package):
    before = _listing(package)
    _write_old(package)
    code, lines = install.uninstall(package)
    assert code == 0 and all(line.startswith("removed") for line in lines)
    assert _listing(package) == before


@pytest.mark.parametrize("ending", ["\r\n", "\r"])
def test_a_shim_with_other_line_endings_is_still_ours(package, ending):
    before = _listing(package)
    current, old = package / NAMES[0], package / NAMES[1]
    current.write_bytes(install.SHIMS[NAMES[0]].replace("\n", ending).encode())
    old.write_bytes(SHIM_0_4_0[NAMES[1]].replace("\n", ending).encode())
    code, lines = install.install(package)
    assert code == 0
    assert lines[0].startswith("already there") and lines[1].startswith("upgraded")
    assert old.read_text() == install.SHIMS[NAMES[1]]
    assert install.uninstall(package)[0] == 0
    assert _listing(package) == before


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


def test_a_leftover_folder_with_no_init_file_is_not_garak(package, monkeypatch):
    (package / "__init__.py").unlink()
    before = _listing(package)
    code, lines = install.install(package)
    assert code == 1 and "is not an installed garak package" in lines[0]
    assert _listing(package) == before

    spec = types.SimpleNamespace(submodule_search_locations=[str(package)])
    monkeypatch.setattr(install.importlib.util, "find_spec", lambda name: spec)
    assert install.find_garak() is None
    (package / "__init__.py").write_text("")
    assert install.find_garak() == package


@needs_file_modes
def test_install_writes_nothing_when_one_folder_is_read_only(package, read_only):
    before = _contents(package)
    read_only(package / "probes")
    code, lines = install.install(package)
    assert code == 1
    assert lines == [f"{package / 'probes' / 'redline.py'} cannot be written. Nothing was changed."]
    assert _contents(package) == before


@needs_file_modes
def test_uninstall_removes_nothing_when_one_folder_is_read_only(package, read_only):
    install.install(package)
    before = _contents(package)
    read_only(package / "probes")
    code, lines = install.uninstall(package)
    assert code == 1
    assert len(lines) == 1 and lines[0].endswith("cannot be removed. Nothing was changed.")
    assert _contents(package) == before


def _fail_on(monkeypatch, method, name):
    """Make one Path method raise for the file with this name, as a full disk would."""
    real = getattr(Path, method)

    def broken(self, *args, **kwargs):
        if str(self).endswith(name):
            raise OSError(28, "No space left on device")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, method, broken)


def test_a_write_that_fails_part_way_leaves_nothing_behind(package, monkeypatch):
    before = _contents(package)
    _fail_on(monkeypatch, "write_bytes", NAMES[1])
    code, lines = install.install(package)
    assert code == 1
    assert len(lines) == 1 and "No space left on device" in lines[0]
    assert _contents(package) == before


def test_an_upgrade_that_fails_part_way_puts_the_older_shim_back(package, monkeypatch):
    _write_old(package)
    before = _contents(package)
    _fail_on(monkeypatch, "write_bytes", NAMES[1])
    code, lines = install.install(package)
    assert code == 1 and len(lines) == 1
    assert _contents(package) == before


def test_a_removal_that_fails_part_way_puts_the_files_back(package, monkeypatch):
    install.install(package)
    cache = package / "detectors" / "__pycache__"
    cache.mkdir()
    (cache / "redline.cpython-312.pyc").write_bytes(b"compiled")
    before = _contents(package)
    _fail_on(monkeypatch, "unlink", NAMES[1])
    code, lines = install.uninstall(package)
    assert code == 1
    assert len(lines) == 1 and lines[0].startswith("Could not remove")
    assert _contents(package) == before


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


@pytest.mark.parametrize("command", ["garak-install", "garak-uninstall"])
def test_commands_report_a_file_error_as_one_line(monkeypatch, capsys, package, command):
    def broken(package_dir, dry_run=False):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(install, "find_garak", lambda: package)
    monkeypatch.setattr(install, command.split("-")[1], broken)
    assert cli.main([command]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1 and captured.err.startswith("redline: ")
    assert "Permission denied" in captured.err


def test_find_garak_does_not_import_garak():
    import sys

    had = "garak" in sys.modules
    install.find_garak()
    assert ("garak" in sys.modules) == had
