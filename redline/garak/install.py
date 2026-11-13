"""Write and remove the small files that let garak find the Redline plugins.

garak 0.17.1 loads plugins only from inside its own package. Until it can load them
from elsewhere, each plugin needs a file in that package. The files written here hold
one import each and no logic. This module does not import garak.
"""

import importlib.util
from pathlib import Path

_HEADER = """\
# Written by "redline garak-install". Remove it with "redline garak-uninstall".
# garak loads plugins only from inside its own package, so this file makes one
# Redline plugin visible to it. The code lives in the redline package.
"""
SHIMS = {
    "detectors/redline.py": _HEADER
    + '"""Check outputs against written policy rules with Redline."""\n\n'
    + "from redline.garak.detector import PolicyRules  # noqa: F401\n",
    "probes/redline.py": _HEADER
    + '"""Send the prompts in a file you supply, for Redline to check the answers."""\n\n'
    + "from redline.garak.probe import PromptFile  # noqa: F401\n",
}


def find_garak() -> Path | None:
    """The folder of the garak package in this environment, or None. garak is not imported."""
    try:
        spec = importlib.util.find_spec("garak")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def _state(path: Path, content: str) -> str:
    """absent, ours when the file is byte for byte the shim, or other."""
    if not path.exists():
        return "absent"
    if path.is_file() and path.read_bytes() == content.encode("utf-8"):
        return "ours"
    return "other"


def _states(package_dir: Path) -> dict[Path, str]:
    return {
        package_dir / name: _state(package_dir / name, content) for name, content in SHIMS.items()
    }


def install(package_dir, dry_run: bool = False) -> tuple[int, list[str]]:
    """Write the shim files. Returns an exit code and the lines to print.

    A file already there that is not the shim stops the install, and nothing is changed.
    """
    package_dir = Path(package_dir)
    states = _states(package_dir)
    blocked = [path for path, state in states.items() if state == "other"]
    if blocked:
        lines = [f"{path} exists and is not the Redline shim. Nothing was changed." for path in blocked]
        return 1, lines
    missing = [path for path in states if not path.parent.is_dir()]
    if missing:
        return 1, [f"{path.parent} is not a folder. Nothing was changed." for path in missing]
    lines = []
    for (path, state), content in zip(states.items(), SHIMS.values()):
        if state == "ours":
            lines.append(f"already there: {path}")
        elif dry_run:
            lines.append(f"would write {path}")
        else:
            path.write_bytes(content.encode("utf-8"))
            lines.append(f"wrote {path}")
    return 0, lines


def _compiled(path: Path) -> list[Path]:
    """The files Python compiled from a shim, which would otherwise stay behind."""
    return sorted((path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"))


def uninstall(package_dir, dry_run: bool = False) -> tuple[int, list[str]]:
    """Remove each shim file that is byte for byte the shim, and what Python compiled from it.

    A file that differs is left alone and the exit code is 1.
    """
    package_dir = Path(package_dir)
    code, lines = 0, []
    for path, state in _states(package_dir).items():
        if state == "absent":
            lines.append(f"not there: {path}")
        elif state == "other":
            code = 1
            lines.append(f"{path} is not the Redline shim. It was left alone.")
        elif dry_run:
            lines.append(f"would remove {path}")
        else:
            for target in [path, *_compiled(path)]:
                target.unlink()
                lines.append(f"removed {target}")
    return code, lines
