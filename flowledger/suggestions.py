"""Remember removed entry suggestions without changing ledger records.

Preferences live beside the database in ``<database>.suggestions.json``. Only
the entry vocabularies are filtered; accounts, flows, and SQLite remain
untouched. In-memory databases use in-memory preferences.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Iterable


KINDS = ("account", "category", "tag", "name", "unit")


def _key(value: str, kind: str = "") -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("Suggestion must be nonempty text without null characters.")
    # Unit symbols are case sensitive: mW and MW are different resources.
    return value.strip() if kind == "unit" else value.strip().casefold()


class SuggestionPreferences:
    """A persistent set of removed suggestions for one database.

    ``remove`` and ``restore`` write successfully before changing memory. A
    damaged or unreadable preferences file is reported through ``load_error``;
    the application can still display all suggestions, but cannot overwrite
    that file through this instance.
    """

    def __init__(self, database_path: str | Path):
        self.path = (None if str(database_path) == ":memory:" else
                     Path(str(Path(database_path).expanduser().resolve()) +
                          ".suggestions.json"))
        self.load_error: str | None = None
        self._removed: dict[str, set[str]] = {kind: set() for kind in KINDS}
        if self.path is None:
            return
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except (OSError, UnicodeError) as error:
            self.load_error = f"Cannot read suggestion preferences: {error}"
            return
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or set(data) - set(KINDS):
                raise ValueError("Expected account, category, tag, name, and unit lists.")
            removed = {kind: set() for kind in KINDS}
            for kind, values in data.items():
                if not isinstance(values, list):
                    raise ValueError(f"{kind} must contain a list of suggestions.")
                removed[kind] = {_key(value, kind) for value in values}
            self._removed = removed
        except (ValueError, TypeError) as error:
            self.load_error = f"Invalid suggestion preferences: {error}"

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError("Unknown suggestion kind: " + str(kind))

    def visible(self, kind: str, values: Iterable[str]) -> list[str]:
        """Filter a vocabulary, preserving its display names and ordering."""
        self._check_kind(kind)
        return [value for value in values if _key(value, kind) not in self._removed[kind]]

    def remove(self, kind: str, value: str) -> bool:
        """Remove a suggestion; return whether the preference changed."""
        return self._change(kind, value, remove=True)

    def restore(self, kind: str, value: str) -> bool:
        """Restore an explicitly entered suggestion; return whether it changed."""
        return self._change(kind, value, remove=False)

    def _change(self, kind: str, value: str, *, remove: bool) -> bool:
        self._check_kind(kind)
        key = _key(value, kind)
        if self.load_error:
            raise ValueError(self.load_error)
        if (key in self._removed[kind]) == remove:
            return False
        updated = {group: set(values) for group, values in self._removed.items()}
        if remove:
            updated[kind].add(key)
        else:
            updated[kind].discard(key)
        self._write(updated)
        self._removed = updated
        return True

    def _write(self, removed: dict[str, set[str]]) -> None:
        if self.path is None:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({kind: sorted(removed[kind]) for kind in KINDS},
                          stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
