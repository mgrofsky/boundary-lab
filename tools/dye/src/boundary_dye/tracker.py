"""Mark untrusted text and find argument values that were lifted from it."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_MIN_LENGTH = 4

_SPACES = re.compile(r"\s+")
_JOINERS = re.compile(r"(?<=\d)[ .,-](?=\d)")
_DIGIT_RUN = re.compile(r"\d+")


def normalize(text: str) -> str:
    """Lowercase the text and collapse runs of whitespace to one space."""
    return _SPACES.sub(" ", text.strip()).lower()


def digit_runs(text: str) -> tuple[str, ...]:
    """Digit runs with the spaces, dots, commas and dashes between digits removed.

    "$420.00" gives ("42000",) and "4471 2201" gives ("44712201",).
    """
    return tuple(_DIGIT_RUN.findall(_JOINERS.sub("", text)))


def numeric_form(text: str) -> str | None:
    """The digits of a value made only of digits and joiners, else None."""
    joined = _JOINERS.sub("", text.strip())
    return joined if joined.isascii() and joined.isdigit() else None


@dataclass(frozen=True)
class Source:
    label: str
    text: str


@dataclass(frozen=True)
class Finding:
    path: str
    value: str
    source: str
    matcher: str


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...] = ()

    @property
    def tainted(self) -> frozenset[str]:
        return frozenset(finding.path for finding in self.findings)

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "tainted": sorted(self.tainted),
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

    def __init__(self, min_length: int = DEFAULT_MIN_LENGTH):
        if min_length < 1:
            raise ValueError("min_length must be at least 1")
        self.min_length = min_length
        self._sources: list[Source] = []
        self._index: list[tuple[Source, str, tuple[str, ...]]] = []

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(self._sources)

    def mark(self, label: str, text: str) -> Source:
        """Record text that came from outside the app, such as a ticket body or a web page."""
        if not label:
            raise ValueError("label is required")
        source = Source(label, text)
        self._sources.append(source)
        self._index.append((source, normalize(text), digit_runs(text)))
        return source

    def check(self, arguments: dict[str, Any]) -> Report:
        """Report every argument value that appears in the marked text."""
        findings: list[Finding] = []
        for path, value in leaves(arguments):
            if value is None or isinstance(value, bool):
                continue
            text = normalize(str(value))
            if len(text) < self.min_length:
                continue
            number = numeric_form(text)
            for source, source_text, runs in self._index:
                if text in source_text:
                    findings.append(Finding(path, str(value), source.label, "text"))
                    break
                if number is not None and any(number in run for run in runs):
                    findings.append(Finding(path, str(value), source.label, "digits"))
                    break
        return Report(tuple(findings))
