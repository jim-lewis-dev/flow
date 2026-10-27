"""Session-only undo/redo for grouped repository changes.

Only changed rows are retained. Temporary SQLite triggers capture writes inside
``perform``; the capture follows savepoint rollbacks just like the records do.
Nothing is added to the persistent schema, and closing the session forgets the
undo stacks. The existing audit remains append-only, including undo/redo events.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import sqlite3
from typing import Callable, TypeVar
import uuid


_COLUMNS = {
    "accounts": ("account_key", "name"),
    "account_balances": ("account_key", "units", "opening_amount", "opening_at"),
    "categories": ("category_key", "name"),
    "tags": ("tag_key", "name"),
    "flows": ("flow_id", "date", "from_key", "to_key", "amount", "units",
              "status", "name", "notes", "category_key"),
    "flow_tags": ("flow_id", "tag_key", "position"),
}
_KEY_LENGTH = {table: 2 if table in ("flow_tags", "account_balances") else 1 for table in _COLUMNS}
_T = TypeVar("_T")


@dataclass(frozen=True)
class _Change:
    table: str
    key: tuple
    before: tuple | None
    after: tuple | None


@dataclass(frozen=True)
class _Action:
    label: str
    changes: tuple[_Change, ...]


@dataclass(frozen=True)
class _ExternalAction:
    label: str
    undo: Callable[[], object]
    redo: Callable[[], object]
    merge_key: object = None


class SessionHistory:
    """Undo atomic user actions on one Repository connection.

    Use ``perform(label, callback)`` for every editable database action. A batch
    import or a callback containing several writes becomes one undo step. Nested
    ``perform`` calls join their outer action. Failed actions and actions with no
    net record changes leave both stacks alone. New changes discard redo.

    Database restore is a session boundary: call ``clear()`` after a successful
    restore. Call ``close()`` before closing the repository. UI draft text and
    suggestion preferences outside SQLite are managed by the caller.
    """

    def __init__(self, repo):
        self.repo = repo
        self._undo: list[_Action | _ExternalAction] = []
        self._redo: list[_Action | _ExternalAction] = []
        self._capturing = False
        self._closed = False
        self._prefix = "flow_history_" + uuid.uuid4().hex
        self._journal = self._prefix + "_rows"
        self._active_function = self._prefix + "_active"
        self._row_function = self._prefix + "_row"
        self._triggers: list[str] = []
        conn = repo.conn
        conn.create_function(self._active_function, 0, lambda: int(self._capturing))
        conn.create_function(self._row_function, -1,
                             lambda *values: json.dumps(values, ensure_ascii=False))
        conn.execute(f"CREATE TEMP TABLE {self._journal} ("
                     "sequence INTEGER PRIMARY KEY, table_name TEXT NOT NULL, "
                     "before_json TEXT, after_json TEXT)")
        for table, columns in _COLUMNS.items():
            old = f"{self._row_function}({','.join('OLD.' + c for c in columns)})"
            new = f"{self._row_function}({','.join('NEW.' + c for c in columns)})"
            for operation, before, after in (("INSERT", "NULL", new),
                                              ("UPDATE", old, new),
                                              ("DELETE", old, "NULL")):
                name = f"{self._prefix}_{table}_{operation.lower()}"
                conn.execute(
                    f"CREATE TEMP TRIGGER {name} AFTER {operation} ON main.{table} "
                    f"WHEN {self._active_function}() BEGIN "
                    f"INSERT INTO {self._journal}(table_name,before_json,after_json) "
                    f"VALUES('{table}',{before},{after}); END")
                self._triggers.append(name)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    @property
    def undo_token(self):
        """Identify the latest action when ordering unfinished typing."""
        return self._undo[-1] if self._undo else None

    @property
    def undo_affects_records(self) -> bool:
        return bool(self._undo and isinstance(self._undo[-1], _Action))

    @property
    def redo_affects_records(self) -> bool:
        return bool(self._redo and isinstance(self._redo[-1], _Action))

    def _ensure_idle(self) -> None:
        if self._closed:
            raise RuntimeError("Undo history is closed.")
        if self._capturing or self.repo.conn.in_transaction:
            raise RuntimeError("Finish the current change before using undo history.")

    def _changes(self) -> tuple[_Change, ...]:
        changes = {}

        def record(table, before, after):
            row = before if before is not None else after
            key = row[:_KEY_LENGTH[table]]
            identity = table, key
            initial = changes[identity].before if identity in changes else before
            changes[identity] = _Change(table, key, initial, after)

        for table, before_json, after_json in self.repo.conn.execute(
                f"SELECT table_name,before_json,after_json FROM {self._journal} "
                "ORDER BY sequence"):
            before = tuple(json.loads(before_json)) if before_json is not None else None
            after = tuple(json.loads(after_json)) if after_json is not None else None
            if (before is not None and after is not None and
                    before[:_KEY_LENGTH[table]] != after[:_KEY_LENGTH[table]]):
                record(table, before, None)
                record(table, None, after)
            else:
                record(table, before, after)
        return tuple(change for change in changes.values() if change.before != change.after)

    def perform(self, label: str, callback: Callable[[], _T]) -> _T:
        if self._capturing:
            with self.repo.atomic():
                return callback()
        self._ensure_idle()
        try:
            self._capturing = True
            with self.repo.atomic():
                result = callback()
                changes = self._changes()
        finally:
            self._capturing = False
            self.repo.conn.execute(f"DELETE FROM {self._journal}")
        if changes:
            self._undo.append(_Action(label, changes))
            self._redo.clear()
        return result

    def perform_external(self, label: str, callback: Callable[[], bool],
                         undo: Callable[[], object], redo: Callable[[], object],
                         *, merge_key: object = None) -> bool:
        """Record one non-database change in the same undo order.

        ``callback`` returns whether anything changed. All three callbacks must
        leave their state untouched on failure.
        These actions cannot be nested inside a database action.
        A non-null merge_key groups consecutive changes, retaining the first
        undo callback and latest redo callback. An undone branch never merges.
        """
        self._ensure_idle()
        changed = callback()
        if changed:
            previous = self._undo[-1] if self._undo else None
            if (merge_key is not None and not self._redo and
                    isinstance(previous, _ExternalAction) and previous.merge_key == merge_key):
                self._undo[-1] = replace(previous, redo=redo)
            else:
                self._undo.append(_ExternalAction(label, undo, redo, merge_key))
            self._redo.clear()
        return changed

    def _read_row(self, change: _Change) -> tuple | None:
        columns = _COLUMNS[change.table]
        where = " AND ".join(column + "=?" for column in columns[:len(change.key)])
        row = self.repo.conn.execute(
            f"SELECT {','.join(columns)} FROM {change.table} WHERE {where}",
            change.key).fetchone()
        return tuple(row) if row is not None else None

    def _audit_records(self, changes):
        """Use the same public account/flow shapes as Repository's normal audit."""
        records = {}
        for change in changes:
            if change.table in ("flows", "flow_tags"):
                identity = "flow", change.key[0]
                if identity not in records:
                    records[identity] = self.repo.get_flow(change.key[0])
            elif change.table == "account_balances":
                identity = "account", change.key
                records[identity] = self.repo.get_account(*change.key)
        return records

    def _restore(self, action: _Action, *, undo: bool) -> None:
        with self.repo.atomic():
            # An unrecorded write must not be silently overwritten. Unrelated
            # records can change without invalidating this sparse undo step.
            for change in action.changes:
                expected = change.after if undo else change.before
                if self._read_row(change) != expected:
                    raise ValueError("Cannot undo or redo: these records changed outside this session action.")
            before = self._audit_records(action.changes)
            # References may move between accounts/categories in the same step.
            # Defer FK checks until every affected row is in its final state.
            self.repo.conn.execute("PRAGMA defer_foreign_keys = ON")
            for table in reversed(_COLUMNS):
                for change in action.changes:
                    target = change.before if undo else change.after
                    if change.table != table or target is not None:
                        continue
                    key_columns = _COLUMNS[table][:len(change.key)]
                    where = " AND ".join(column + "=?" for column in key_columns)
                    self.repo.conn.execute(f"DELETE FROM {table} WHERE {where}", change.key)
            for table, columns in _COLUMNS.items():
                for change in action.changes:
                    target = change.before if undo else change.after
                    if change.table != table or target is None:
                        continue
                    keys = columns[:len(change.key)]
                    updates = ",".join(f"{column}=excluded.{column}" for column in columns[len(keys):])
                    placeholders = ",".join("?" for _ in columns)
                    self.repo.conn.execute(
                        f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders}) "
                        f"ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}", target)
            if self.repo.conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise sqlite3.IntegrityError("Cannot undo or redo: another record depends on this change.")
            after = self._audit_records(action.changes)
            for (entity, key), old in before.items():
                new = after[entity, key]
                if old != new:
                    record = new or old
                    audit_key = f"{record['name']} [{record['units']}]" if entity == "account" else key
                    self.repo._audit(entity, audit_key, "undo" if undo else "redo", old, new)

    def undo(self) -> str | None:
        self._ensure_idle()
        if not self._undo:
            return None
        action = self._undo[-1]
        if isinstance(action, _ExternalAction):
            action.undo()
        else:
            self._restore(action, undo=True)
        self._undo.pop()
        self._redo.append(action)
        return action.label

    def redo(self) -> str | None:
        self._ensure_idle()
        if not self._redo:
            return None
        action = self._redo[-1]
        if isinstance(action, _ExternalAction):
            action.redo()
        else:
            self._restore(action, undo=False)
        self._redo.pop()
        self._undo.append(action)
        return action.label

    def clear(self) -> None:
        self._ensure_idle()
        self._undo.clear()
        self._redo.clear()
        self.repo.conn.execute(f"DELETE FROM {self._journal}")

    def close(self) -> None:
        if self._closed:
            return
        self._ensure_idle()
        self.clear()
        for name in self._triggers:
            self.repo.conn.execute(f"DROP TRIGGER IF EXISTS temp.{name}")
        self.repo.conn.execute(f"DROP TABLE IF EXISTS temp.{self._journal}")
        self.repo.conn.create_function(self._active_function, 0, None)
        self.repo.conn.create_function(self._row_function, -1, None)
        self._closed = True
