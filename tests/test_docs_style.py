import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BANNED = [
    "leverage",
    "utilize",
    "robust",
    "seamless",
    "seamlessly",
    "delve",
    "cutting-edge",
    "state-of-the-art",
    "comprehensive",
    "holistic",
    "paradigm",
    "synergy",
    "empower",
    "revolutionize",
    "game-changer",
    "streamline",
    "elevate",
    "unlock",
    "landscape",
    "it's important to note",
    "in today's",
    "furthermore",
    "moreover",
    "additionally",
]
DASHES = "\u2014\u2013"
TEXT_GLOBS = ["*.md", "*.py", "*.yaml", "*.yml", "*.jsonl", "*.toml", "*.txt"]
MAX_WORDS = 40


def walk(folder, globs):
    """Every file under folder matching globs, skipping .venv, .git and any dot folder."""
    for pattern in globs:
        for path in folder.rglob(pattern):
            parts = path.relative_to(ROOT).parts[:-1]
            if path.is_file() and not any(p.startswith(".") for p in parts):
                yield path


def prose_files():
    yield ROOT / "README.md"
    yield from walk(ROOT / "docs", ["*.md"])


def without_comments(text):
    # The README results markers are HTML comments, which start with "<!".
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def test_no_banned_words():
    files = [ROOT / "README.md"]
    for folder in ("docs", "policy", "guardrail_eval"):
        files += walk(ROOT / folder, ["*"])
    patterns = [re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE) for word in BANNED]
    found = []
    for path in files:
        text = path.read_text(errors="ignore")
        found += [f"{path.relative_to(ROOT)}: {p.pattern}" for p in patterns if p.search(text)]
    assert found == []


def test_no_em_or_en_dash():
    found = [
        str(path.relative_to(ROOT))
        for path in walk(ROOT, TEXT_GLOBS)
        if any(c in path.read_text(errors="ignore") for c in DASHES)
    ]
    assert found == []


def test_no_exclamation_marks_in_prose():
    found = [
        str(path.relative_to(ROOT))
        for path in prose_files()
        if "!" in without_comments(path.read_text())
    ]
    assert found == []


def readme_prose():
    lines, fenced = [], False
    for line in (ROOT / "README.md").read_text().splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced or line.lstrip().startswith("|"):
            continue
        lines.append(line)
    return without_comments("\n".join(lines))


def test_no_long_sentences_in_readme():
    sentences = re.split(r"\. |\? |\n\n", readme_prose())
    long = [s.strip() for s in sentences if len(s.split()) > MAX_WORDS]
    assert long == []
