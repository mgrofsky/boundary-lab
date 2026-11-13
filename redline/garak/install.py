"""Write and remove the small files that let garak find the Redline plugins.

garak 0.17.1 loads plugins only from inside its own package. Until it can load them
from elsewhere, each plugin needs a file in that package. The files written here hold
one import each and no logic. This module does not import garak.
"""

import importlib.util
import os
from pathlib import Path

_HEADER = """\
# Written by "redline garak-install". Remove it with "redline garak-uninstall".
# garak loads plugins only from inside its own package, so this file makes one
# Redline plugin visible to it. The code lives in the redline package.
"""
_DOCS = {
    "detectors/redline.py": '"""Check outputs against written policy rules with Redline."""\n\n',
    "probes/redline.py": (
        '"""Send the prompts in a file you supply, for Redline to check the answers."""\n\n'
    ),
}
_IMPORTS = {
    "detectors/redline.py": "from redline.garak.detector import PolicyRules  # noqa: F401\n",
    "probes/redline.py": "from redline.garak.probe import PromptFile  # noqa: F401\n",
}
_GUARD = "# garak imports every file here, so it must still start if Redline has been removed.\n"
SHIMS = {
    name: _HEADER + _DOCS[name] + _GUARD + "try:\n    " + _IMPORTS[name] + "except ImportError:\n    pass\n"
    for name in _DOCS
}
# The texts earlier versions wrote, oldest first. A file that holds one is ours as well,
# so install can bring it up to date and uninstall can remove it. When SHIMS changes,
# add the text it replaces here. The first entry is the first shim, a bare import.
EARLIER = {name: (_HEADER + _DOCS[name] + _IMPORTS[name],) for name in _DOCS}


def is_garak(package_dir: Path) -> bool:
    """True when the folder is an installed garak package, not a folder left behind."""
    return (Path(package_dir) / "__init__.py").is_file()


def find_garak() -> Path | None:
    """The folder of the garak package in this environment, or None. garak is not imported."""
    try:
        spec = importlib.util.find_spec("garak")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    return package_dir if is_garak(package_dir) else None


def _state(path: Path, name: str) -> str:
    """absent, current, earlier when it holds a shim an older Redline wrote, or other.

    Line endings are not compared, so a file a tool rewrote with other ones is still ours.
    """
    if not path.exists():
        return "absent"
    if not path.is_file():
        return "other"
    try:
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeDecodeError):
        return "other"
    if text == SHIMS[name]:
        return "current"
    return "earlier" if text in EARLIER[name] else "other"


def _states(package_dir: Path) -> dict[Path, str]:
    return {package_dir / name: _state(package_dir / name, name) for name in SHIMS}


def _can_write_in(folder: Path) -> bool:
    return folder.is_dir() and os.access(folder, os.W_OK | os.X_OK)


def _put_back(done: list[tuple[Path, bytes | None]]) -> None:
    """Undo this run: write back what each file held, or remove a file that was not there."""
    for path, before in reversed(done):
        try:
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
        except OSError:
            pass


def install(package_dir, dry_run: bool = False) -> tuple[int, list[str]]:
    """Write the shim files. Returns an exit code and the lines to print.

    A file already there that is not a Redline shim stops the install. So does a folder
    that cannot be written. Both are checked before anything is written. If a write
    still fails, what this run wrote is undone. In each case the code is 1.
    """
    package_dir = Path(package_dir)
    if not is_garak(package_dir):
        return 1, [f"{package_dir} is not an installed garak package. Nothing was changed."]
    states = _states(package_dir)
    blocked = [path for path, state in states.items() if state == "other"]
    if blocked:
        lines = [f"{path} exists and is not the Redline shim. Nothing was changed." for path in blocked]
        return 1, lines
    todo = {path: state for path, state in states.items() if state != "current"}
    stuck = [
        path
        for path, state in todo.items()
        if not _can_write_in(path.parent) or (state == "earlier" and not os.access(path, os.W_OK))
    ]
    if stuck:
        return 1, [f"{path} cannot be written. Nothing was changed." for path in stuck]
    lines, done = [], []
    for (path, state), content in zip(states.items(), SHIMS.values()):
        if state == "current":
            lines.append(f"already there: {path}")
        elif dry_run:
            lines.append(f"would {'write' if state == 'absent' else 'upgrade'} {path}")
        else:
            try:
                done.append((path, path.read_bytes() if state == "earlier" else None))
                path.write_bytes(content.encode("utf-8"))
            except OSError as exc:
                _put_back(done)
                return 1, [f"Could not write {path}: {exc.strerror}. Nothing was changed."]
            if state == "absent":
                lines.append(f"wrote {path}")
            else:
                lines.append(f"upgraded {path}, which held the shim an earlier Redline wrote")
    return 0, lines


def _compiled(path: Path) -> list[Path]:
    """The files Python compiled from a shim, which would otherwise stay behind."""
    return sorted((path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"))


def uninstall(package_dir, dry_run: bool = False) -> tuple[int, list[str]]:
    """Remove each shim file Redline wrote, now or in an earlier version, and its compiled copies.

    A file that differs is left alone and the exit code is 1. A folder that cannot be
    written is found before anything is removed. If a removal still fails, what this run
    removed is put back. In both cases the code is 1.
    """
    package_dir = Path(package_dir)
    code, lines, targets = 0, [], []
    for path, state in _states(package_dir).items():
        if state == "absent":
            lines.append(f"not there: {path}")
        elif state == "other":
            code = 1
            lines.append(f"{path} is not the Redline shim. It was left alone.")
        else:
            targets += [path, *_compiled(path)]
    stuck = [target for target in targets if not _can_write_in(target.parent)]
    if stuck:
        return 1, [f"{target} cannot be removed. Nothing was changed." for target in stuck]
    done = []
    for target in targets:
        if dry_run:
            lines.append(f"would remove {target}")
            continue
        try:
            done.append((target, target.read_bytes()))
            target.unlink()
        except OSError as exc:
            _put_back(done)
            return 1, [f"Could not remove {target}: {exc.strerror}. Nothing was changed."]
        lines.append(f"removed {target}")
    return code, lines
