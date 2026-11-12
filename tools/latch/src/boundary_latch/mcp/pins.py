"""The first definition seen for each tool, kept in memory or in a JSON file."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import ConfigError


class Pins:
    """name -> {"hash", "first_seen", "definition"}.

    The definition is kept so a change found in a later run can show and serve
    the old one until a person accepts the new one.
    """

    def __init__(self, path: str | Path | None = None, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path is not None else None
        self.clock = clock
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path is not None and self.path.exists():
            self.entries = self._load(self.path)

    @staticmethod
    def _load(path: Path) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ConfigError(f"pin file {path} could not be read: {exc}") from exc
        ok = isinstance(data, dict) and all(
            isinstance(entry, dict) and isinstance(entry.get("hash"), str)
            for entry in data.values()
        )
        if not ok:
            raise ConfigError(f"pin file {path} is not a map of tool name to pin")
        return data

    def get(self, name: str) -> dict[str, Any] | None:
        return self.entries.get(name)

    def pin(self, name: str, hash_: str, definition: dict[str, Any]) -> None:
        """Pin a definition. The first seen time stays when a new one is accepted."""
        old = self.entries.get(name)
        entry = {"hash": hash_, "first_seen": old["first_seen"] if old else self.clock(),
                 "definition": definition}
        if old:
            entry["accepted_at"] = self.clock()
        self.entries[name] = entry
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        body = json.dumps(self.entries, indent=2, sort_keys=True)
        folder = self.path.parent
        fd, temp = tempfile.mkstemp(prefix=".pins-", dir=folder)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
