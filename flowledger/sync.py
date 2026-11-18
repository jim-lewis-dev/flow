"""Transactional change capture and field revisions for shared Flow files.

Immutable peer events are the synchronization history. These helpers capture
local writes, validate record snapshots and protect Undo from newer edits.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import uuid

from .history import _COLUMNS, _KEY_LENGTH

TABLES = dict(_COLUMNS)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _key(table, key):
    return table + ':' + _json(list(key))


def _identity(table, row):
    return tuple(row[c] for c in TABLES[table][:_KEY_LENGTH[table]])


def retire_settings(conn):
    """Deactivate obsolete sync settings without altering ledger records."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
        return
    # Retire old connection settings without replaying queues or replacing
    # records. Existing journal tables stay untouched for data preservation.
    keys = ('dataset', 'endpoint', 'token', 'certificate', 'server', 'cursor')
    settings = [row[0] for row in conn.execute(
        "SELECT key FROM sync_meta WHERE key IN ('dataset','endpoint','token','certificate','server','cursor')")]
    old_guards = {f'sync_guard_{table}_{op}' for table in TABLES
                  for op in ('insert', 'update', 'delete')}
    guards = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
              if row[0] in old_guards]
    if not settings and not guards:
        return
    conn.execute('SAVEPOINT retire_sync_settings')
    try:
        for name in guards:
            conn.execute(f'DROP TRIGGER "{name}"')
        conn.executemany('DELETE FROM sync_meta WHERE key=?', [(key,) for key in keys])
        if not conn.execute("SELECT 1 FROM sync_meta WHERE key='peer_dataset'").fetchone():
            for table in ('sync_versions', 'sync_remote_versions'):
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    conn.execute(f'DELETE FROM {table}')
        conn.execute('RELEASE SAVEPOINT retire_sync_settings')
    except BaseException:
        conn.execute('ROLLBACK TO SAVEPOINT retire_sync_settings')
        conn.execute('RELEASE SAVEPOINT retire_sync_settings')
        raise


class SyncConflict(ValueError):
    pass


class SyncState:
    def __init__(self, repo):
        self.repo = repo
        self._muted = 0
        self._capturing = False
        self._peer_event_payload = None
        self._peer_format_version = None
        self._prefix = 'flow_sync_' + uuid.uuid4().hex
        self._journal = self._prefix + '_journal'
        self._triggers = []
        self._install_tables()
        conn = repo.conn
        # Persistent guards prevent clients without event capture from writing.
        conn.create_function('flow_peer_write_allowed', 0, lambda: 3 if self._muted > 0 or self._capturing else 0)
        conn.create_function('flow_peer_event_write_allowed', 1,
                             lambda payload: int(self._peer_event_payload is not None and payload == self._peer_event_payload))
        conn.create_function(self._prefix + '_active', 0, lambda: int(self._capturing and not self._muted))
        conn.create_function(self._prefix + '_row', -1, lambda *v: _json(v))
        conn.execute(f'CREATE TEMP TABLE {self._journal}(sequence INTEGER PRIMARY KEY,table_name TEXT,before_json TEXT,after_json TEXT)')
        for table, columns in TABLES.items():
            old = self._prefix + '_row(' + ','.join('OLD.' + c for c in columns) + ')'
            new = self._prefix + '_row(' + ','.join('NEW.' + c for c in columns) + ')'
            for op, before, after in [('INSERT', 'NULL', new), ('UPDATE', old, new), ('DELETE', old, 'NULL')]:
                name = self._prefix + '_' + table + '_' + op.lower()
                conn.execute(f"CREATE TEMP TRIGGER {name} AFTER {op} ON main.{table} WHEN {self._prefix}_active() BEGIN INSERT INTO {self._journal}(table_name,before_json,after_json) VALUES('{table}',{before},{after}); END")
                self._triggers.append(name)
        repo.sync = self

    def _install_tables(self):
        self.repo.conn.executescript('''
        CREATE TABLE IF NOT EXISTS sync_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_versions(identity TEXT NOT NULL,column_name TEXT NOT NULL,token TEXT NOT NULL,PRIMARY KEY(identity,column_name));
        CREATE TABLE IF NOT EXISTS sync_remote_versions(identity TEXT NOT NULL,column_name TEXT NOT NULL,token TEXT NOT NULL,PRIMARY KEY(identity,column_name));
        ''')
        retire_settings(self.repo.conn)

    def close(self):
        """Release connection-local capture before replacing a local database."""
        for name in self._triggers:
            self.repo.conn.execute(f'DROP TRIGGER IF EXISTS temp.{name}')
        self.repo.conn.execute(f'DROP TABLE IF EXISTS temp.{self._journal}')

    def _get(self, key, default=None):
        row = self.repo.conn.execute('SELECT value FROM sync_meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        encoded = _json(value)
        self.repo.conn.execute('INSERT INTO sync_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value<>excluded.value', (key, encoded))

    @property
    def tracking(self):
        return bool(self._get('peer_dataset'))

    @contextmanager
    def muted(self):
        self._muted += 1
        try:
            yield
        finally:
            self._muted -= 1

    def _rows(self):
        result = {}
        for table, columns in TABLES.items():
            for values in self.repo.conn.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid"):
                row = dict(zip(columns, values))
                result[_key(table, _identity(table, row))] = {'table': table, 'key': list(_identity(table, row)), 'row': row}
        return result

    def _versions(self, remote=False):
        table = 'sync_remote_versions' if remote else 'sync_versions'
        result = {}
        for identity, column, token in self.repo.conn.execute(f'SELECT identity,column_name,token FROM {table}'):
            result.setdefault(identity, {})[column] = token
        return result

    def _write_versions(self, versions, remote=False):
        table = 'sync_remote_versions' if remote else 'sync_versions'
        old = {(identity, column): token for identity, column, token in self.repo.conn.execute(f'SELECT identity,column_name,token FROM {table}')}
        new = {(identity, column): token for identity, fields in versions.items() for column, token in fields.items()}
        self.repo.conn.executemany(f'DELETE FROM {table} WHERE identity=? AND column_name=?', [key for key in old if key not in new])
        self.repo.conn.executemany(f'INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(identity,column_name) DO UPDATE SET token=excluded.token', [(identity, column, token) for (identity, column), token in new.items() if old.get((identity, column)) != token])

    def field_tokens(self, table, key, columns=None):
        identity = _key(table, key)
        current = {row[0]: row[1] for row in self.repo.conn.execute('SELECT column_name,token FROM sync_versions WHERE identity=?', (identity,))}
        selected = columns or ('__row__', *TABLES[table], *(('tags',) if table == 'flows' else ()))
        return {column: current.get(column, '') for column in selected}

    def field_token(self, table, key, column):
        return self.field_tokens(table, key, (column,))[column]

    def history_tokens(self, changes):
        result = {}
        revisions = self._versions(remote=True)
        for change in changes:
            columns = TABLES[change.table]
            if change.before is None or change.after is None:
                touched = ('__row__', *columns)
            else:
                touched = ('__row__', *(c for c, a, b in zip(columns, change.before, change.after) if a != b))
            identity = _key(change.table, change.key)
            current = revisions.get(identity, {})
            result[identity] = {column: current.get(column, '') for column in touched}
        return result

    def check_history_tokens(self, tokens):
        revisions = self._versions(remote=True)
        for identity, expected in (tokens or {}).items():
            actual = revisions.get(identity, {})
            if any(actual.get(column, '') != value for column, value in expected.items()):
                raise SyncConflict('Cannot undo or redo: a newer change affects this value.')

    def _start_capture(self):
        if self.tracking and not self._muted:
            from .peer_store import require_current
            require_current(self.repo)
            self.repo.conn.execute(f'DELETE FROM {self._journal}')
            self._capturing = True

    def _stop_capture(self):
        self._capturing = False

    def _finish_capture(self):
        if not self._capturing or self._muted:
            return
        combined = {}
        for table, before, after in self.repo.conn.execute(f'SELECT table_name,before_json,after_json FROM {self._journal} ORDER BY sequence'):
            columns = TABLES[table]
            old = dict(zip(columns, json.loads(before))) if before else None
            new = dict(zip(columns, json.loads(after))) if after else None
            if old is not None and new is not None and _identity(table, old) != _identity(table, new):
                pairs = [(old, None), (None, new)]
            else:
                pairs = [(old, new)]
            for old, new in pairs:
                key = _identity(table, old if old is not None else new)
                identity = _key(table, key)
                first = combined[identity][2] if identity in combined else old
                combined[identity] = (table, key, first, new)
        from .peer_store import capture
        capture(self.repo, combined)

    @staticmethod
    def _validate_entity(entity):
        if not isinstance(entity, dict) or entity.get('table') not in TABLES:
            raise ValueError('Invalid synchronization table.')
        table = entity['table']
        key = entity.get('key')
        row = entity.get('row')
        if not isinstance(key, list) or len(key) != _KEY_LENGTH[table] or not all(isinstance(v, str) for v in key):
            raise ValueError('Invalid synchronization key.')
        if not isinstance(row, dict) or set(row) != set(TABLES[table]) or list(_identity(table, row)) != key:
            raise ValueError('Invalid synchronization row.')
        for column, value in row.items():
            if column in ('x', 'y'):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Invalid account position.')
            elif column == 'position':
                if type(value) is not int or value < 0:
                    raise ValueError('Invalid tag position.')
            elif value is None and column == 'opening_amount':
                pass
            elif not isinstance(value, str) or '\x00' in value:
                raise ValueError('Invalid synchronized field.')
        from .core import normalize_flow_date, normalize_date, quantity_text, STATUSES
        if table == 'flows':
            uuid.UUID(row['flow_id'])
            normalize_flow_date(row['date'])
            quantity_text(row['amount'])
            if row['status'] not in STATUSES or not row['units'].strip():
                raise ValueError('Invalid flow status or units.')
        elif table == 'account_balances':
            normalize_date(row['opening_at'])
            if row['opening_amount'] is not None:
                quantity_text(row['opening_amount'])
            if not row['units'].strip():
                raise ValueError('Units required.')
        elif table in ('accounts', 'categories', 'tags'):
            if not row['name'].strip() or row['name'].strip().casefold() != key[0]:
                raise ValueError('Invalid named record.')

    def _replace_rows(self, rows):
        """Reconcile only changed rows; preserve rowids and equal-sort ordering."""
        conn = self.repo.conn
        current = self._rows()
        if current == rows:
            return
        conn.execute('PRAGMA defer_foreign_keys = ON')
        for table in reversed(TABLES):
            key_columns = TABLES[table][:_KEY_LENGTH[table]]
            where = ' AND '.join(column + '=?' for column in key_columns)
            for identity, entity in current.items():
                if entity['table'] == table and identity not in rows:
                    conn.execute(f'DELETE FROM {table} WHERE {where}', entity['key'])
        for table, columns in TABLES.items():
            key_columns = columns[:_KEY_LENGTH[table]]
            where = ' AND '.join(column + '=?' for column in key_columns)
            for identity, entity in rows.items():
                if entity['table'] != table:
                    continue
                old = current.get(identity)
                if old is None:
                    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(entity['row'][c] for c in columns))
                elif old != entity:
                    changed = [c for c in columns if old['row'][c] != entity['row'][c]]
                    if changed:
                        conn.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in changed)} WHERE {where}", tuple(entity['row'][c] for c in changed) + tuple(entity['key']))
        if conn.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise SyncConflict('Another record depends on this change.')

