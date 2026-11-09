"""Mark untrusted text and find argument values that were lifted from it."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Any

_SPACES = re.compile(r"\s+")
_JOINERS = re.compile(r"(?<=\d)[ .,-](?=\d)")
_DIGIT_RUN = re.compile(r"\d+")
_SPELLED_DOT = re.compile(r"\s*[(\[]?\bdot\b[)\]]?\s*")
_SPELLED_AT = re.compile(r"\s*[(\[]?\bat\b[)\]]?\s*")
_SEPARATORS = re.compile(r"[\s,=:;/?&()\[\]{}<>\"'`]+")
MIN_TOKEN = 4

COMMON_IGNORE: tuple[str, ...] = (
    r"[a-z]{2}-[a-z]+-\d",
    r"\d{4}-\d{2}-\d{2}",
)
"""Opt in patterns for values that are never evidence: cloud regions such as us-east-2 and ISO dates."""


def normalize(text: str) -> str:
    """Lowercase the text and collapse runs of whitespace to one space."""
    return _SPACES.sub(" ", text.strip()).lower()


def spelled_out(text: str) -> str:
    """Turn "j dot doe at example dot com" into "j.doe@example.com"."""
    return _SPELLED_AT.sub("@", _SPELLED_DOT.sub(".", text))


def digit_runs(text: str) -> frozenset[str]:
    """Whole numbers in the text, with spaces, dots, commas and dashes between digits removed.

    "$420.00" gives {"42000"} and "4471 2201" gives {"44712201"}.
    """
    return frozenset(_DIGIT_RUN.findall(_JOINERS.sub("", text)))


def numeric_form(text: str) -> str | None:
    """The digits of a value made only of digits and joiners, else None."""
    joined = _JOINERS.sub("", text.strip())
    return joined if joined.isascii() and joined.isdigit() else None


def tokens(text: str) -> list[str]:
    """Pieces of a composite value worth checking on their own, such as the id inside an ARN.

    The text is split on runs of whitespace and , = : ; / ? & ( ) [ ] { } < > quotes and
    backticks. Dashes and dots do not split. A token is kept when it is at least four
    characters long and holds a digit or an @. A token equal to the whole text is dropped.
    """
    if not _SEPARATORS.search(text):
        return []
    whole = text.strip()
    return [
        piece
        for piece in _SEPARATORS.split(text)
        if len(piece) >= MIN_TOKEN
        and piece != whole
        and ("@" in piece or any(c.isdigit() for c in piece))
    ]


def contains_term(text: str, term: str) -> bool:
    """True when term appears in text with no letter or digit touching either end."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


@dataclass(frozen=True)
class Source:
    label: str
    text: str
    path: str = ""


@dataclass(frozen=True)
class Finding:
    path: str
    value: str
    source: str
    matcher: str
    source_path: str = ""


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...] = ()

    @property
    def copied(self) -> frozenset[str]:
        return frozenset(finding.path for finding in self.findings)

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "copied": sorted(self.copied),
            "findings": [asdict(finding) for finding in self.findings],
        }


def leaves(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar inside nested dicts and lists, with a path like items[0].sku."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from leaves(inner, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, inner in enumerate(value):
            yield from leaves(inner, f"{path}[{index}]")
    else:
        yield path, value


class Tracker:
    """Holds the untrusted text for one run and checks proposed arguments against it."""

    def __init__(self, ignore: Iterable[str] = ()) -> None:
        self._sources: list[Source] = []
        self._index: list[tuple[Source, str, str, frozenset[str]]] = []
        self._trusted: set[str] = set()
        self._ignore: list[str] = []
        self._ignore_compiled: list[re.Pattern[str]] = []
        self.ignore(*ignore)

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(self._sources)

    def mark(self, label: str, text: str, path: str = "") -> Source:
        """Record text that came from outside the app, such as a ticket body or a web page.

        path says where in a larger record the text sat, such as body. It is optional.
        """
        if not label:
            raise ValueError("label is required")
        source = Source(label, text, path)
        plain = normalize(text)
        self._sources.append(source)
        self._index.append((source, plain, spelled_out(plain), digit_runs(text)))
        return source

    def mark_data(self, label: str, data: Any) -> list[Source]:
        """Mark every scalar inside a dict or list as text, each with its own path.

        None, booleans and empty strings are skipped, as check() skips them.
        """
        marked = []
        for path, value in leaves(data):
            if value is None or isinstance(value, bool) or str(value) == "":
                continue
            marked.append(self.mark(label, str(value), path))
        return marked

    def trust(self, *values: Any) -> None:
        """Record values the app supplied itself, such as the signed in account. They are never reported."""
        self._trusted.update(normalize(str(value)) for value in values)

    def ignore(self, *patterns: str) -> None:
        """Skip values and tokens that match one of these regular expressions in full.

        Each pattern is matched against the normalised form, which is lowercase with single spaces.
        """
        for pattern in patterns:
            compiled = re.compile(pattern)
            self._ignore.append(pattern)
            self._ignore_compiled.append(compiled)

    def _skipped(self, text: str) -> bool:
        """True for an empty, trusted or ignored normalised value."""
        if not text or text in self._trusted:
            return True
        return any(pattern.fullmatch(text) for pattern in self._ignore_compiled)

    def check(self, arguments: dict[str, Any]) -> Report:
        """Report every argument value that appears in the marked text.

        The whole value is tested first. When it does not match, each token inside it is
        tested, and the first token that matches is reported with the matcher "token".
        """
        findings: list[Finding] = []
        for path, value in leaves(arguments):
            if value is None or isinstance(value, bool):
                continue
            raw = str(value)
            text = normalize(raw)
            if self._skipped(text):
                continue
            found = self._match(text)
            if found is not None:
                findings.append(Finding(path, raw, found[0].label, found[1], found[0].path))
                continue
            for token in tokens(raw):
                plain = normalize(token)
                if self._skipped(plain):
                    continue
                found = self._match(plain)
                if found is not None:
                    findings.append(Finding(path, token, found[0].label, "token", found[0].path))
                    break
        return Report(tuple(findings))

    def _match(self, text: str) -> tuple[Source, str] | None:
        """The first source holding the normalised text, and the matcher that found it."""
        number = numeric_form(text)
        for source, plain, spelled, runs in self._index:
            if contains_term(plain, text) or contains_term(spelled, text):
                return source, "text"
            if number is not None and number in runs:
                return source, "digits"
        return None
