"""SQLite journal and transaction boundary for local-first Flow files.

Only the original records and immutable edits travel between devices. The
materialized ledger, field revisions and unresolved decisions are derived
from those edits. No device address, credential or elected writer lives here.
"""
from __future__ import annotations

import hashlib
import json
import socket
import uuid

from .core import normalize_date, normalize_flow_date, quantity_text
from .peer_projection import project
from .sync import SyncState, TABLES, _json, _key

PROTOCOL = 2
BATCH_EVENTS = 128
BATCH_BYTES = 2 * 1024 * 1024
SEED_BYTES = 56 * 1024 * 1024


def _meta(repo, key):
    if not repo.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
        return ''
    row = repo.conn.execute('SELECT value FROM sync_meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else ''


def dataset(repo):
    return _meta(repo, 'peer_dataset')


def genesis(repo):
    return _meta(repo, 'peer_genesis')


def _uuid(value):
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError('Invalid peer identifier.')
    if str(uuid.UUID(value)) != value:
        raise ValueError('Invalid peer identifier.')
    return value


def _name(value):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 200 or any(ord(c) < 32 for c in value):
        raise ValueError('Choose a short, nonempty display name.')
    return value.strip()


def _entity(entity):
    if not isinstance(entity, dict) or set(entity) != {'table', 'key', 'row'} or not isinstance(entity['table'], str):
        raise ValueError('Invalid peer record.')
    SyncState._validate_entity(entity)
    table, row = entity['table'], entity['row']
    if table == 'flows':
        _uuid(row['flow_id'])
        if normalize_flow_date(row['date']) != row['date'] or quantity_text(row['amount']) != row['amount']:
            raise ValueError('Invalid flow date or quantity.')
    if table == 'account_balances':
        amount = row['opening_amount']
        if normalize_date(row['opening_at']) != row['opening_at'] or (amount is not None and quantity_text(amount) != amount):
            raise ValueError('Invalid opening balance.')
    if table == 'flow_tags':
        _uuid(row['flow_id'])
    for column in ('from_key', 'to_key', 'category_key', 'account_key', 'tag_key'):
        if column in row and (not row[column] or row[column].strip().casefold() != row[column]):
            raise ValueError('Invalid peer reference key.')
    if 'units' in row and row['units'].strip() != row['units']:
        raise ValueError('Invalid units.')
    if table in ('accounts', 'categories', 'tags') and row['name'].strip() != row['name']:
        raise ValueError('Invalid display name.')
    if table == 'flow_tags' and row['position'] >= 2**63:
        raise ValueError('Invalid tag position.')


def _digest(rows, order):
    return hashlib.sha256(_json([rows, order]).encode()).hexdigest()


def _validate_seed(value):
    required = {'protocol', 'dataset', 'name', 'snapshot', 'order'}
    if not isinstance(value, dict) or set(value) != required or type(value['protocol']) is not int or value['protocol'] != PROTOCOL:
        raise ValueError('Invalid peer file snapshot.')
    _uuid(value['dataset'])
    _name(value['name'])
    rows, order = value['snapshot'], value['order']
    if not isinstance(rows, dict) or not isinstance(order, list) or any(not isinstance(k, str) for k in order):
        raise ValueError('Invalid peer snapshot rows.')
    if len(order) != len(rows) or set(order) != rows.keys():
        raise ValueError('Invalid peer snapshot row order.')
    for identity, entity in rows.items():
        _entity(entity)
        if identity != _key(entity['table'], entity['key']):
            raise ValueError('Invalid peer snapshot record identity.')
    # Use the same ASCII wire representation as the network envelope.
    if len(json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)) > SEED_BYTES:
        raise ValueError('This starting snapshot is too large to share in one transfer.')


def _initialize(repo, value):
    sync = repo.sync
    rows, order = value['snapshot'], value['order']
    # DDL is part of the same savepoint as the initial rows and identity.
    repo.conn.execute('CREATE TABLE peer_events(sequence INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL)')
    repo.conn.execute('CREATE TABLE peer_heads(id TEXT PRIMARY KEY REFERENCES peer_events(id))')
    repo.conn.execute('CREATE TABLE peer_seed(singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload TEXT NOT NULL)')
    repo.conn.execute('INSERT INTO peer_seed VALUES(1,?)', (_json(value),))
    for table in TABLES:
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            repo.conn.execute(f'CREATE TRIGGER peer_guard_{table}_{operation.lower()} BEFORE {operation} ON {table} '
                              "WHEN flow_peer_write_allowed()<>1 BEGIN "
                              "SELECT RAISE(ABORT,'Open this shared file in current Flow to edit it.'); END")
    for table in ('peer_seed', 'peer_events'):
        for operation in ('UPDATE', 'DELETE'):
            repo.conn.execute(f'CREATE TRIGGER peer_immutable_{table}_{operation.lower()} BEFORE {operation} ON {table} '
                              "BEGIN SELECT RAISE(ABORT,'Peer history is immutable.'); END")
    # An older Flow with coordinator support knows sync_meta but does not
    # understand peer sharing. Refuse its attempt to configure a second writer.
    for operation in ('INSERT', 'UPDATE'):
        repo.conn.execute(f'CREATE TRIGGER peer_refuse_coordinator_{operation.lower()} BEFORE {operation} ON sync_meta '
                          "WHEN NEW.key='dataset' BEGIN SELECT RAISE(ABORT,'This file uses peer sharing.'); END")
    sync._set('peer_dataset', value['dataset'])
    sync._set('peer_genesis', _digest(rows, order))
    sync._set('peer_conflicts', [])
    sync._replace_rows({k: rows[k] for k in order})


def _setup(repo):
    if repo.sync is None and repo.conn.in_transaction:
        raise ValueError('Finish the current transaction before enabling sharing.')
    sync = repo.sync or SyncState(repo)
    if sync.enabled:
        raise ValueError('This file is linked to a coordinator. Migrate it before sharing with peers.')
    return sync


def share(repo, name, *, dataset_id=None):
    name = _name(name)
    if dataset_id is not None:
        _uuid(dataset_id)
    sync = _setup(repo)
    with sync.muted(), repo.atomic():
        if sync.enabled:
            raise ValueError('This file is linked to a coordinator.')
        current = dataset(repo)
        if current:
            if dataset_id and current != dataset_id:
                raise ValueError('This file already has a different dataset identifier.')
            return current
        rows = sync._rows()
        value = {'protocol': PROTOCOL, 'dataset': dataset_id or str(uuid.uuid4()), 'name': name,
                 'snapshot': rows, 'order': list(rows)}
        _validate_seed(value)
        _initialize(repo, value)
        return value['dataset']


def join(repo, value):
    _validate_seed(value)
    sync = _setup(repo)
    with sync.muted(), repo.atomic():
        if sync.enabled:
            raise ValueError('This file is linked to a coordinator.')
        current = dataset(repo)
        if current:
            if current != value['dataset'] or genesis(repo) != _digest(value['snapshot'], value['order']):
                raise ValueError('This file belongs to a different dataset or starting snapshot.')
            return current
        rows = sync._rows()
        # Only the untouched factory default qualifies as empty.
        default = {'table': 'categories', 'key': ['uncategorized'],
                   'row': {'category_key': 'uncategorized', 'name': 'Uncategorized'}}
        if rows != {_key('categories', default['key']): default} and rows != value['snapshot']:
            raise ValueError('This file differs from the shared file. Join using a new empty file.')
        _initialize(repo, value)
        return value['dataset']


def seed(repo):
    if not dataset(repo):
        raise ValueError('This file is not shared with peers.')
    return json.loads(repo.conn.execute('SELECT payload FROM peer_seed WHERE singleton=1').fetchone()[0])


def inventory(repo):
    if not dataset(repo):
        return []
    return [r[0] for r in repo.conn.execute('SELECT id FROM peer_events ORDER BY sequence')]


def missing(repo, known_ids):
    if not dataset(repo):
        raise ValueError('This file is not shared with peers.')
    if not isinstance(known_ids, (list, tuple, set, frozenset)):
        raise ValueError('Invalid peer inventory.')
    known = {_uuid(k) for k in known_ids}
    batch, size = [], 2
    for key, payload in repo.conn.execute('SELECT id,payload FROM peer_events ORDER BY sequence'):
        if key in known:
            continue
        event = json.loads(payload)
        size += _event_bytes(event) + 1
        if batch and size > BATCH_BYTES:
            break
        batch.append(event)
        if len(batch) == BATCH_EVENTS:
            break
    return batch


def _event_bytes(event):
    return len(json.dumps(event, ensure_ascii=True, separators=(',', ':'), allow_nan=False))


def _validate_event(value, shared):
    required = {'id', 'dataset', 'parents', 'changes'}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - (required | {'origin', 'resolution'}):
        raise ValueError('Invalid peer event.')
    _uuid(value['id'])
    if value['dataset'] != shared:
        raise ValueError('Peer event belongs to a different dataset.')
    if 'origin' in value:
        _name(value['origin'])
    parents = value['parents']
    if not isinstance(parents, list) or len({_uuid(p) for p in parents}) != len(parents) or value['id'] in parents:
        raise ValueError('Invalid peer event parents.')
    changes = value['changes']
    if not isinstance(changes, list):
        raise ValueError('Invalid peer changes.')
    if 'resolution' in value:
        decision = value['resolution']
        if changes or not isinstance(decision, dict) or set(decision) != {'conflict', 'choice'} or not all(
                isinstance(v, str) and len(v) == 64 and all(c in '0123456789abcdef' for c in v) for v in decision.values()):
            raise ValueError('Invalid peer conflict decision.')
    elif not changes:
        raise ValueError('Empty peer event.')
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {'table', 'key', 'before', 'after'} or change['before'] == change['after']:
            raise ValueError('Invalid peer record change.')
        for row in (change['before'], change['after']):
            if row is not None:
                _entity({'table': change['table'], 'key': change['key'], 'row': row})
        identity = _key(change['table'], change['key'])
        if identity in seen:
            raise ValueError('Repeated peer record change.')
        seen.add(identity)


def _append(repo, event):
    repo.conn.execute('INSERT INTO peer_events(id,payload) VALUES(?,?)', (event['id'], _json(event)))
    repo.conn.executemany('DELETE FROM peer_heads WHERE id=?', [(p,) for p in event['parents']])
    repo.conn.execute('INSERT INTO peer_heads VALUES(?)', (event['id'],))


def _materialize(repo, *, remote):
    sync = repo.sync
    base = seed(repo)
    events = (json.loads(r[0]) for r in repo.conn.execute('SELECT payload FROM peer_events ORDER BY sequence'))
    rows, decisions, versions = project({k: base['snapshot'][k] for k in base['order']}, events)
    if remote:
        previous = sync._versions()
        observed = sync._versions(remote=True)
        for identity in previous.keys() | versions.keys():
            old, new = previous.get(identity, {}), versions.get(identity, {})
            for field in old.keys() | new.keys():
                if old.get(field) != new.get(field):
                    observed.setdefault(identity, {})[field] = new.get(field, '')
        sync._write_versions(observed, remote=True)
    with sync.muted():
        sync._replace_rows(rows)
    sync._write_versions(versions)
    sync._set('peer_conflicts', decisions)


def _local(repo, changes, decision=None):
    event = {'id': str(uuid.uuid4()), 'dataset': dataset(repo), 'changes': changes,
             'origin': _name(getattr(repo, 'peer_origin', '') or socket.gethostname() or 'Flow device'),
             'parents': [r[0] for r in repo.conn.execute('SELECT id FROM peer_heads ORDER BY id')]}
    if decision is not None:
        event['resolution'] = decision
    if _event_bytes(event) + 3 > BATCH_BYTES:
        raise ValueError('This change is too large to synchronize at once; split it into smaller edits.')
    _validate_event(event, event['dataset'])
    _append(repo, event)
    _materialize(repo, remote=decision is not None)


def capture(repo, combined):
    changes = [{'table': table, 'key': list(key), 'before': old, 'after': new}
               for table, key, old, new in combined.values() if old != new]
    if changes:
        _local(repo, changes)


def receive(repo, events):
    shared = dataset(repo)
    if not shared:
        raise ValueError('This file is not shared with peers.')
    if not isinstance(events, list) or len(events) > BATCH_EVENTS or _event_bytes(events) > BATCH_BYTES:
        raise ValueError('Invalid or oversized peer event batch.')
    sync = repo.sync or SyncState(repo)
    added = False
    with sync.muted(), repo.atomic():
        pending = {}
        for event in events:
            _validate_event(event, shared)
            prev = repo.conn.execute('SELECT payload FROM peer_events WHERE id=?', (event['id'],)).fetchone()
            if prev:
                if prev[0] != _json(event):
                    raise ValueError('A peer event identifier was reused with different contents.')
                continue
            if event['id'] in pending and pending[event['id']] != event:
                raise ValueError('A peer event identifier was reused with different contents.')
            pending[event['id']] = event
        # Accept a complete batch in any order. A missing ancestor causes
        # the entire batch to roll back, so a later complete retry is safe.
        while pending:
            ready = [key for key, event in pending.items() if not any(p in pending for p in event['parents'])]
            if not ready:
                raise ValueError('Cycle in peer event history.')
            for key in ready:
                event = pending.pop(key)
                for parent in event['parents']:
                    if not repo.conn.execute('SELECT 1 FROM peer_events WHERE id=?', (parent,)).fetchone():
                        raise ValueError('Missing parent peer event; request its history first.')
                _append(repo, event)
                added = True
        if added:
            _materialize(repo, remote=True)
    return added


def conflicts(repo):
    return _meta(repo, 'peer_conflicts') or []


def resolve(repo, conflict_id, choice_id):
    if not dataset(repo):
        raise ValueError('This file is not shared with peers.')
    sync = repo.sync or SyncState(repo)
    with sync.muted(), repo.atomic():
        conflict = next((c for c in conflicts(repo) if c['id'] == conflict_id), None)
        if not conflict or not any(c['id'] == choice_id for c in conflict['choices']):
            raise ValueError('This conflict or choice is no longer available. Refresh the conflict list.')
        _local(repo, [], {'conflict': conflict_id, 'choice': choice_id})
    return True
