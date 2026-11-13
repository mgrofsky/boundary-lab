"""No tracked file tells a reader to install Redline by name from the public index.

Another project owns the name redline there. This package is named boundary-redline
and is installed from a checkout or from the repository.
"""

import re
import subprocess
import tomllib
from pathlib import Path

from redline import embed

ROOT = Path(__file__).parent.parent
SOURCE = "git+https://github.com/mgrofsky/boundary-lab"
# A pip install whose target is this package's name, with or without extras.
NAMED = re.compile(r"pip3? install\s+(?:-\S+\s+)*[\"'`]?(?:boundary-)?redline\b(\[[^\]]*\])?(?P<rest>.*)")
# The name with extras, as a requirement, anywhere on a line.
EXTRAS = re.compile(r"(?:boundary-)?redline\[[a-z,]+\](?P<rest>.*)")


def _tracked():
    """Every tracked text file outside tools, which holds the lab's other packages."""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    for name in filter(None, listed):
        path = ROOT / name
        # This file holds bad lines on purpose, to prove the check finds them.
        if name.startswith("tools/") or path == Path(__file__) or not path.is_file():
            continue
        try:
            yield name, path.read_text()
        except UnicodeDecodeError:
            continue


def bare_install_lines(name: str, text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in (NAMED.search(line), EXTRAS.search(line)):
            if match and not match["rest"].lstrip("\"'` ").startswith(f"@ {SOURCE}"):
                found.append(f"{name}:{number}: {line.strip()}")
                break
    return found


def test_the_check_finds_a_bare_install_line():
    bad = [
        "pip install redline",
        'pip install "redline[minilm]"',
        "pip install -U boundary-redline",
        'install `"redline[minilm]"` by name',
        'pip install "boundary-redline[minilm]"',
        "pip install boundary-redline[minilm] @ git+https://example.com/other",
    ]
    good = [
        'pip install -e ".[minilm]"',
        'pip install ".[minilm]"',
        "pip install .",
        f'pip install "boundary-redline[minilm] @ {SOURCE}"',
        f'pip install "boundary-redline @ {SOURCE}"',
        "pip install redlines",
        "redline garak-install",
    ]
    for line in bad:
        assert bare_install_lines("x", line), line
    for line in good:
        assert not bare_install_lines("x", line), line


def test_no_tracked_file_has_a_bare_install_line():
    found = [line for name, text in _tracked() for line in bare_install_lines(name, text)]
    assert found == []


def test_the_distribution_is_named_like_the_labs_other_packages():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["name"] == "boundary-redline"
    assert project["scripts"] == {"redline": "redline.cli:main"}


def test_the_missing_extra_message_gives_the_checkout_and_the_repository_form(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    try:
        embed.make("minilm")
    except embed.MissingExtra as exc:
        message = str(exc)
    assert 'pip install -e ".[minilm]"' in message
    assert f'pip install "boundary-redline[minilm] @ {SOURCE}"' in message
    assert "\n" not in message
    assert bare_install_lines("message", message) == []


def test_the_readme_says_once_that_the_package_is_not_on_the_public_index():
    text = (ROOT / "redline" / "README.md").read_text()
    assert text.count("not on the public Python package index") == 1
