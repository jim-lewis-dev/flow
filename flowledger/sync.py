"""Optional, transactional synchronization of individual SQLite changes.

The public Flow records do not change. Accepted rows, outgoing operations and
field revisions live in sync_* tables in the same file. A coordinator orders
operations; clients retain their local edits until acknowledged or resolved.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
import uuid

from .history import _COLUMNS, _KEY_LENGTH

PROTOCOL = 1
MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_BATCH_OPERATIONS = 256
TABLES = dict(_COLUMNS)
REFERENCE_TABLES = {'accounts', 'categories', 'tags', 'account_balances'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _key(table, key):
    return table + ':' + _json(list(key))


def _identity(table, row):
    return tuple(row[c] for c in TABLES[table][:_KEY_LENGTH[table]])


def _fingerprint(rows):
    return hashlib.sha256(_json(rows).encode()).hexdigest()


class SyncConflict(ValueError):
    pass


class SyncState:
    def __init__(self, repo):
        self.repo = repo
        self.client = str(uuid.uuid4())
        self._muted = 0
        self._capturing = False
        self._prefix = 'flow_sync_' + uuid.uuid4().hex
        self._journal = self._prefix + '_journal'
        self._triggers = []
        repo.sync = self
        self._install_tables()
        conn = repo.conn
        conn.create_function('flow_sync_write_allowed', 0, lambda: int(self._muted > 0 or self._capturing))
        if self.enabled:
            self._install_guards()
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

    def _install_tables(self):
        self.repo.conn.executescript('''
        CREATE TABLE IF NOT EXISTS sync_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_base(identity TEXT PRIMARY KEY,row_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_versions(identity TEXT NOT NULL,column_name TEXT NOT NULL,token TEXT NOT NULL,PRIMARY KEY(identity,column_name));
        CREATE TABLE IF NOT EXISTS sync_base_versions(identity TEXT NOT NULL,column_name TEXT NOT NULL,token TEXT NOT NULL,PRIMARY KEY(identity,column_name));
        CREATE TABLE IF NOT EXISTS sync_remote_versions(identity TEXT NOT NULL,column_name TEXT NOT NULL,token TEXT NOT NULL,PRIMARY KEY(identity,column_name));
        CREATE TABLE IF NOT EXISTS sync_outbox(sequence INTEGER PRIMARY KEY,operation_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,conflict TEXT);
        CREATE TABLE IF NOT EXISTS sync_log(sequence INTEGER PRIMARY KEY,operation_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_receipts(operation_id TEXT PRIMARY KEY,payload TEXT NOT NULL,response TEXT NOT NULL);
        ''')

    def _install_guards(self):
        # A pre-sync application must not make untracked writes to this file.
        # Such an application does not register this SQLite function, so its
        # attempted write fails before modifying a record.
        for table in TABLES:
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                self.repo.conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS sync_guard_{table}_{operation.lower()} BEFORE {operation} ON {table} "
                    "WHEN flow_sync_write_allowed()<>1 BEGIN "
                    "SELECT RAISE(ABORT,'Open this shared file in current Flow to edit it.'); END")

    def _get(self, key, default=None):
        row = self.repo.conn.execute('SELECT value FROM sync_meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        encoded = _json(value)
        self.repo.conn.execute('INSERT INTO sync_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE value<>excluded.value', (key, encoded))

    @property
    def enabled(self):
        return bool(self._get('dataset'))

    @contextmanager
    def muted(self):
        self._muted += 1
        try:
            yield
        finally:
            self._muted -= 1

    def configuration(self):
        return {key: self._get(key, '') for key in ('dataset', 'endpoint', 'token', 'certificate')} | {'client': self.client}

    def status(self):
        conflicts = []
        for row in self.repo.conn.execute('SELECT operation_id,payload,conflict FROM sync_outbox WHERE conflict IS NOT NULL ORDER BY sequence'):
            changes = json.loads(row[1])['changes']
            base = self._base()
            for change in changes:
                change['remote'] = base.get(_key(change['table'], change['key']), {}).get('row')
            conflicts.append({'operation_id': row[0], 'message': row[2], 'changes': changes})
        return {'enabled': self.enabled, 'dataset': self._get('dataset', ''), 'endpoint': self._get('endpoint', ''),
                'pending': self.repo.conn.execute('SELECT count(*) FROM sync_outbox').fetchone()[0],
                'conflicts': conflicts, 'cursor': self._get('cursor', 0)}

    def _rows(self):
        result = {}
        for table, columns in TABLES.items():
            for values in self.repo.conn.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid"):
                row = dict(zip(columns, values))
                result[_key(table, _identity(table, row))] = {'table': table, 'key': list(_identity(table, row)), 'row': row}
        return result

    def _versions(self, base=False, remote=False):
        table = 'sync_remote_versions' if remote else ('sync_base_versions' if base else 'sync_versions')
        result = {}
        for identity, column, token in self.repo.conn.execute(f'SELECT identity,column_name,token FROM {table}'):
            result.setdefault(identity, {})[column] = token
        return result

    def _write_versions(self, versions, base=False, remote=False):
        table = 'sync_remote_versions' if remote else ('sync_base_versions' if base else 'sync_versions')
        old = {(identity, column): token for identity, column, token in self.repo.conn.execute(f'SELECT identity,column_name,token FROM {table}')}
        new = {(identity, column): token for identity, fields in versions.items() for column, token in fields.items()}
        self.repo.conn.executemany(f'DELETE FROM {table} WHERE identity=? AND column_name=?', [key for key in old if key not in new])
        self.repo.conn.executemany(f'INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(identity,column_name) DO UPDATE SET token=excluded.token', [(identity, column, token) for (identity, column), token in new.items() if old.get((identity, column)) != token])

    def _base(self):
        return {identity: json.loads(row) for identity, row in self.repo.conn.execute('SELECT identity,row_json FROM sync_base')}

    def _write_base(self, rows, versions):
        old = self._base()
        self.repo.conn.executemany('DELETE FROM sync_base WHERE identity=?', [(identity,) for identity in old if identity not in rows])
        self.repo.conn.executemany('INSERT INTO sync_base VALUES(?,?) ON CONFLICT(identity) DO UPDATE SET row_json=excluded.row_json', [(identity, _json(row)) for identity, row in rows.items() if old.get(identity) != row])
        self._write_versions(versions, base=True)

    def configure(self, *, dataset, client=None, endpoint='', token='', certificate='', seed=None, server=False):
        if not isinstance(dataset, str) or not dataset.strip():
            raise ValueError('A shared dataset identifier is required.')
        if self.enabled and self._get('dataset') != dataset:
            raise ValueError('This file already belongs to a different shared dataset.')
        if client:
            self.client = str(client)
        with self.muted(), self.repo.atomic():
            if not self.enabled:
                rows = self._rows()
                versions = {}
                cursor = 0
                if seed is not None:
                    self._validate_seed(seed, dataset)
                    seeded = seed['snapshot']
                    if 'order' in seed:
                        if not isinstance(seed['order'], list) or len(seed['order']) != len(seeded) or set(seed['order']) != set(seeded):
                            raise ValueError('Invalid synchronization row order.')
                        seeded = {identity: seeded[identity] for identity in seed['order']}
                    # Fresh databases contain only the standard category.
                    empty = all(value['table'] == 'categories' and value['key'] == ['uncategorized'] for value in rows.values())
                    if not empty and _fingerprint(rows) != _fingerprint(seeded):
                        raise ValueError('This file differs from the shared file. Join using a new empty file, or start sharing from this file.')
                    rows = seeded
                    versions = seed.get('versions', {})
                    cursor = seed.get('cursor', 0)
                    self._replace_rows(rows)
                self._write_base(rows, versions)
                self._write_versions(versions)
                self._set('cursor', cursor)
            self._set('dataset', dataset)
            self._set('server', bool(server))
            self._install_guards()
            for key, value in [('endpoint', endpoint), ('token', token), ('certificate', certificate)]:
                self._set(key, value)

    def _validate_seed(self, seed, dataset):
        if not isinstance(seed, dict) or seed.get('protocol') != PROTOCOL or seed.get('dataset') != dataset or not isinstance(seed.get('snapshot'), dict):
            raise ValueError('Incompatible synchronization seed.')
        for identity, entity in seed['snapshot'].items():
            self._validate_entity(entity)
            if identity != _key(entity['table'], entity['key']):
                raise ValueError('Invalid synchronization record identity.')
        self._validate_versions(seed.get('versions', {}))
        if type(seed.get('cursor', 0)) is not int or seed.get('cursor', 0) < 0:
            raise ValueError('Invalid synchronization cursor.')

    def export_seed(self):
        if not self.enabled:
            raise ValueError('Enable sharing before exporting a seed.')
        if self.status()['pending']:
            raise ValueError('Synchronize pending edits before creating a seed.')
        rows = self._rows()
        return {'protocol': PROTOCOL, 'dataset': self._get('dataset'), 'snapshot': rows, 'order': list(rows),
                'versions': self._versions(), 'cursor': self._get('cursor', 0)}

    def field_tokens(self, table, key, columns=None):
        identity = _key(table, key)
        current = {row[0]: row[1] for row in self.repo.conn.execute('SELECT column_name,token FROM sync_versions WHERE identity=?', (identity,))}
        if table == 'flows':
            tag_tokens = [(identity, field, token) for identity, fields in self._versions().items()
                          if identity.startswith('flow_tags:') and json.loads(identity.split(':', 1)[1])[0] == key[0]
                          for field, token in fields.items()]
            current['tags'] = hashlib.sha256(_json(sorted(tag_tokens)).encode()).hexdigest()
        selected = columns or ('__row__', *TABLES[table], *(('tags',) if table == 'flows' else ()))
        return {column: current.get(column, '') for column in selected}

    def field_token(self, table, key, column):
        return self.field_tokens(table, key, (column,))[column]

    def history_tokens(self, changes):
        result = {}
        for change in changes:
            columns = TABLES[change.table]
            if change.before is None or change.after is None:
                touched = ('__row__', *columns)
            else:
                touched = ('__row__', *(c for c, a, b in zip(columns, change.before, change.after) if a != b))
            identity = _key(change.table, change.key)
            current = self._versions(remote=True).get(identity, {})
            result[identity] = {column: current.get(column, '') for column in touched}
        return result

    def check_history_tokens(self, tokens):
        for identity, expected in (tokens or {}).items():
            actual = self._versions(remote=True).get(identity, {})
            if any(actual.get(column, '') != value for column, value in expected.items()):
                raise SyncConflict('Cannot undo or redo: a newer change affects this value.')

    def _start_capture(self):
        if self.enabled and self._get('server', False) and not self._muted:
            raise ValueError('This is a coordinator file. Join it through synchronization to edit a local copy.')
        if self.enabled and not self._muted:
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
        changes = []
        versions = self._versions()
        operation_id = str(uuid.uuid4())
        for identity, (table, key, old, new) in combined.items():
            if old == new:
                continue
            if old is not None and new is not None:
                changed = [column for column in TABLES[table] if old[column] != new[column]]
                before = {column: old[column] for column in changed}
                after = {column: new[column] for column in changed}
                expected_columns = ['__row__', *changed]
            else:
                before, after = old, new
                expected_columns = ['__row__', *TABLES[table]]
            expected = {column: versions.get(identity, {}).get(column, '') for column in expected_columns}
            change = {'table': table, 'key': list(key), 'before': before, 'after': after, 'expected': expected}
            changes.append(change)
            self._stamp(versions, change, operation_id)
        if changes:
            operation = {'id': operation_id, 'client': self.client, 'changes': changes}
            if len(_json(operation).encode()) > MAX_BATCH_BYTES:
                raise ValueError('This change is too large to synchronize at once; split the import into smaller batches.')
            self.repo.conn.execute('INSERT INTO sync_outbox(operation_id,payload) VALUES(?,?)', (operation_id, _json(operation)))
            self._write_versions(versions)

    @staticmethod
    def _stamp(versions, change, token):
        identity = _key(change['table'], change['key'])
        columns = TABLES[change['table']]
        fields = list(columns) + ['__row__'] if change['before'] is None or change['after'] is None else list(change['after'])
        current = versions.setdefault(identity, {})
        for field in fields:
            current[field] = token

    def prepare_request(self):
        if not self.enabled:
            raise ValueError('Synchronization is not configured for this file.')
        operations = []
        size = 0
        for payload, conflict in self.repo.conn.execute('SELECT payload,conflict FROM sync_outbox ORDER BY sequence'):
            if conflict:
                break
            if operations and (len(operations) >= MAX_BATCH_OPERATIONS or size + len(payload.encode()) > MAX_BATCH_BYTES):
                break
            operations.append(json.loads(payload))
            size += len(payload.encode())
        return {'protocol': PROTOCOL, 'dataset': self._get('dataset'), 'client': self.client,
                'cursor': self._get('cursor', 0), 'operations': operations}

    @staticmethod
    def _validate_versions(versions):
        if not isinstance(versions, dict) or any(not isinstance(identity, str) or not isinstance(fields, dict) or any(not isinstance(c, str) or not isinstance(v, str) for c, v in fields.items()) for identity, fields in versions.items()):
            raise ValueError('Invalid field revisions.')

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

    def _validate_operation(self, operation):
        if not isinstance(operation, dict) or not isinstance(operation.get('id'), str) or not isinstance(operation.get('client'), str) or not isinstance(operation.get('changes'), list):
            raise ValueError('Invalid synchronization operation.')
        uuid.UUID(operation['id'])
        seen = set()
        for change in operation['changes']:
            if not isinstance(change, dict) or change.get('table') not in TABLES:
                raise ValueError('Invalid synchronization change.')
            table = change['table']
            key = change.get('key')
            if not isinstance(key, list) or len(key) != _KEY_LENGTH[table] or not all(isinstance(v, str) for v in key):
                raise ValueError('Invalid synchronization key.')
            identity = _key(table, key)
            if identity in seen:
                raise ValueError('Repeated synchronization row.')
            seen.add(identity)
            before, after = change.get('before'), change.get('after')
            if before is None and after is None:
                raise ValueError('Empty synchronization change.')
            for row in (before, after):
                if row is not None and (not isinstance(row, dict) or not row or set(row) - set(TABLES[table])):
                    raise ValueError('Invalid synchronized columns.')
            if before is None or after is None:
                self._validate_entity({'table': table, 'key': key, 'row': before if before is not None else after})
                required = {'__row__', *TABLES[table]}
            else:
                if set(before) != set(after) or any(c in TABLES[table][:_KEY_LENGTH[table]] for c in before):
                    raise ValueError('Invalid updated columns.')
                required = {'__row__', *before}
            expected = change.get('expected')
            if not isinstance(expected, dict) or set(expected) != required or any(not isinstance(v, str) for v in expected.values()):
                raise ValueError('Invalid expected field revisions.')

    def _apply_memory(self, rows, versions, operation, *, check=True):
        applied = []
        for change in operation['changes']:
            table, key = change['table'], change['key']
            identity = _key(table, key)
            current = rows.get(identity, {}).get('row')
            before, after = change['before'], change['after']
            # Two flows may independently introduce the same vocabulary/anchor.
            if check and before is None and current is not None and table in REFERENCE_TABLES:
                if table != 'account_balances' or after.get('opening_amount') is None or current == after:
                    continue
            if check:
                current_versions = versions.get(identity, {})
                if any(current_versions.get(c, '') != token for c, token in change['expected'].items()):
                    raise SyncConflict('A newer change affects ' + table.replace('_', ' ') + ' ' + ', '.join(key) + '.')
                if before is None:
                    if current is not None:
                        raise SyncConflict('This record was already created on another device.')
                elif current is None or any(current.get(c) != value for c, value in before.items()):
                    raise SyncConflict('This record changed on another device.')
            if after is None:
                rows.pop(identity, None)
            else:
                updated = dict(current or {}) if before is not None else {}
                updated.update(after)
                # During optimistic conflict display, retain the original row.
                if set(updated) != set(TABLES[table]) and before is not None:
                    raise SyncConflict('This record was deleted on another device.')
                entity = {'table': table, 'key': key, 'row': updated}
                self._validate_entity(entity)
                rows[identity] = entity
            self._stamp(versions, change, operation['id'])
            applied.append(change)
        return {'id': operation['id'], 'client': operation['client'], 'changes': applied}

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

    def server_exchange(self, request):
        if not isinstance(request, dict) or request.get('protocol') != PROTOCOL or request.get('dataset') != self._get('dataset'):
            raise ValueError('Wrong shared file or synchronization protocol.')
        cursor = request.get('cursor')
        if type(cursor) is not int or cursor < 0 or cursor > self._get('cursor', 0):
            raise ValueError('Invalid synchronization cursor; reconnect using the current coordinator.')
        operations = request.get('operations')
        if not isinstance(operations, list) or len(operations) > 10000:
            raise ValueError('Invalid outgoing operations.')
        receipts, conflicts = [], []
        with self.muted(), self.repo.atomic():
            for operation in operations:
                self._validate_operation(operation)
                prior = self.repo.conn.execute('SELECT payload,response FROM sync_receipts WHERE operation_id=?', (operation['id'],)).fetchone()
                if prior:
                    if prior[0] != _json(operation):
                        raise ValueError('An operation identifier was reused with different contents.')
                    receipts.append(json.loads(prior[1]))
                    continue
                rows, versions = self._rows(), self._versions()
                try:
                    applied = self._apply_memory(rows, versions, operation)
                    with self.repo.atomic():
                        self._replace_rows(rows)
                except (SyncConflict, sqlite3.IntegrityError) as error:
                    conflicts.append({'operation_id': operation['id'], 'message': str(error)})
                    break
                sequence = self._get('cursor', 0) + 1
                self._set('cursor', sequence)
                self._write_versions(versions)
                self._write_base(rows, versions)
                self.repo.conn.execute('INSERT INTO sync_log(sequence,operation_id,payload) VALUES(?,?,?)', (sequence, operation['id'], _json(applied)))
                receipt = {'operation_id': operation['id'], 'sequence': sequence}
                self.repo.conn.execute('INSERT INTO sync_receipts VALUES(?,?,?)', (operation['id'], _json(operation), _json(receipt)))
                receipts.append(receipt)
            entries, size = [], 0
            for sequence, payload in self.repo.conn.execute('SELECT sequence,payload FROM sync_log WHERE sequence>? ORDER BY sequence LIMIT ?', (cursor, MAX_BATCH_OPERATIONS)):
                if entries and size + len(payload.encode()) > MAX_BATCH_BYTES:
                    break
                entries.append({'sequence': sequence, 'operation': json.loads(payload)})
                size += len(payload.encode())
            received_cursor = entries[-1]['sequence'] if entries else cursor
            more = received_cursor < self._get('cursor', 0)
        return {'protocol': PROTOCOL, 'dataset': self._get('dataset'), 'cursor': received_cursor, 'entries': entries,
                'receipts': [r for r in receipts if r['sequence'] <= received_cursor], 'conflicts': [] if more else conflicts, 'more': more}

    def accept_response(self, response):
        if not isinstance(response, dict) or response.get('protocol') != PROTOCOL or response.get('dataset') != self._get('dataset'):
            raise ValueError('Wrong shared file or synchronization protocol.')
        if not response.get('entries') and not response.get('receipts') and not response.get('conflicts') and response.get('cursor') == self._get('cursor', 0):
            return False
        before_rows = self._rows()
        with self.muted(), self.repo.atomic():
            rows, versions = self._base(), self._versions(base=True)
            cursor = self._get('cursor', 0)
            remote_versions = self._versions(remote=True)
            for entry in response.get('entries', []):
                if entry.get('sequence') != cursor + 1:
                    raise ValueError('Missing or reordered synchronization changes.')
                operation = entry['operation']
                self._validate_operation(operation)
                self._apply_memory(rows, versions, operation, check=False)
                local_ack = self.repo.conn.execute(
                    'SELECT 1 FROM sync_outbox WHERE operation_id=?', (operation['id'],)).fetchone() is not None
                if operation['client'] != self.client and not local_ack:
                    for change in operation['changes']:
                        self._stamp(remote_versions, change, operation['id'])
                cursor = entry['sequence']
                self.repo.conn.execute('DELETE FROM sync_outbox WHERE operation_id=?', (operation['id'],))
            if response.get('cursor') != cursor:
                raise ValueError('Incomplete synchronization response.')
            for receipt in response.get('receipts', []):
                if receipt.get('sequence', cursor + 1) > cursor:
                    raise ValueError('Acknowledgement is ahead of received changes.')
                self.repo.conn.execute('DELETE FROM sync_outbox WHERE operation_id=?', (receipt['operation_id'],))
            self._set('cursor', cursor)
            self._write_versions(remote_versions, remote=True)
            self._write_base(rows, versions)
            for conflict in response.get('conflicts', []):
                self.repo.conn.execute('UPDATE sync_outbox SET conflict=? WHERE operation_id=?', (conflict['message'], conflict['operation_id']))
            self._rebuild(rows, versions, before_rows)
        return before_rows != self._rows()

    def _rebuild(self, rows=None, versions=None, old_rows=None):
        rows = self._base() if rows is None else rows
        versions = self._versions(base=True) if versions is None else versions
        old_rows = self._rows() if old_rows is None else old_rows
        for sequence, payload, conflict in self.repo.conn.execute('SELECT sequence,payload,conflict FROM sync_outbox ORDER BY sequence').fetchall():
            operation = json.loads(payload)
            candidate, candidate_versions = json.loads(_json(rows)), json.loads(_json(versions))
            try:
                self._apply_memory(candidate, candidate_versions, operation)
            except SyncConflict as error:
                self.repo.conn.execute('UPDATE sync_outbox SET conflict=? WHERE sequence=?', (str(error), sequence))
                # Keep local intent visible; never discard it during a pull.
                for change in operation['changes']:
                    identity = _key(change['table'], change['key'])
                    if identity not in candidate and change['before'] is not None and identity in old_rows:
                        candidate[identity] = old_rows[identity]
                        if change['table'] == 'flows':
                            for child_id, child in old_rows.items():
                                if child['table'] == 'flow_tags' and child['key'][0] == change['key'][0]:
                                    candidate[child_id] = child
                self._apply_memory(candidate, candidate_versions, operation, check=False)
            rows, versions = candidate, candidate_versions
        try:
            self._replace_rows(rows)
        except SyncConflict:
            # An offline Undo may have removed a reference locally before this
            # device learns that another flow now uses it. The accepted base,
            # not just the already-undone local view, retains that reference.
            support = dict(self._base())
            support.update(old_rows)
            restored = set()
            for identity, entity in support.items():
                if identity not in rows and entity['table'] in REFERENCE_TABLES:
                    rows[identity] = entity
                    restored.add(identity)
            for sequence, payload in self.repo.conn.execute('SELECT sequence,payload FROM sync_outbox').fetchall():
                operation = json.loads(payload)
                if any(change['after'] is None and _key(change['table'], change['key']) in restored
                       for change in operation['changes']):
                    self.repo.conn.execute('UPDATE sync_outbox SET conflict=? WHERE sequence=?',
                                           ('Another record depends on this change.', sequence))
            self._replace_rows(rows)
        self._write_versions(versions)

    def resolve(self, operation_id, choice='remote'):
        if choice not in ('local', 'remote'):
            raise ValueError('Choose local or remote.')
        with self.muted(), self.repo.atomic():
            found = self.repo.conn.execute('SELECT sequence,payload FROM sync_outbox WHERE operation_id=? AND conflict IS NOT NULL', (operation_id,)).fetchone()
            if found is None:
                raise ValueError('This conflict is no longer pending.')
            rows, versions = self._base(), self._versions(base=True)
            # Earlier local operations still contribute to the expected state.
            for payload, in self.repo.conn.execute('SELECT payload FROM sync_outbox WHERE sequence<? ORDER BY sequence', (found[0],)):
                self._apply_memory(rows, versions, json.loads(payload))
            operation = json.loads(found[1])
            self.repo.conn.execute('DELETE FROM sync_outbox WHERE operation_id=?', (operation_id,))
            if choice == 'local':
                rewritten = []
                for change in operation['changes']:
                    identity = _key(change['table'], change['key'])
                    current = rows.get(identity, {}).get('row')
                    after = change['after']
                    if after is None:
                        if current is None:
                            continue
                        before = current
                    elif current is None:
                        if change['before'] is not None:
                            raise SyncConflict('This record was deleted; copy it as a new flow to keep it.')
                        before = None
                    elif change['before'] is None:
                        before = {c: current[c] for c in after if current[c] != after[c]}
                        after = {c: after[c] for c in before}
                        if not after:
                            continue
                    else:
                        before = {c: current[c] for c in after}
                    cols = ['__row__', *(TABLES[change['table']] if before is None or after is None else before)]
                    rewritten.append(dict(change, before=before, after=after, expected={c: versions.get(identity, {}).get(c, '') for c in cols}))
                if rewritten:
                    operation = dict(operation, id=str(uuid.uuid4()), client=self.client, changes=rewritten)
                    self._validate_operation(operation)
                    self.repo.conn.execute('INSERT INTO sync_outbox(sequence,operation_id,payload) VALUES(?,?,?)', (found[0], operation['id'], _json(operation)))
            self._rebuild()
