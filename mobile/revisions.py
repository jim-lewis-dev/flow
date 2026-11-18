"""Bind the phone's editable values to the peer revisions actually displayed."""
from contextlib import contextmanager

from flowledger.peer_store import dataset
from flowledger.sync import _key


FLOW_FIELDS = {'date': 'date', 'name': 'name', 'from_account': 'from_key',
               'to_account': 'to_key', 'amount': 'amount', 'units': 'units',
               'status': 'status', 'category': 'category_key', 'tags': 'tags',
               'notes': 'notes'}
ANCHOR_FIELDS = {name: name for name in ('opening_at', 'opening_amount')}


@contextmanager
def read_snapshot(repo):
    # A worker may be writing through another connection. A form must never
    # associate an old amount with a revision read from a newer transaction.
    repo.conn.execute('SAVEPOINT mobile_display_snapshot')
    try:
        yield
    finally:
        repo.conn.execute('RELEASE SAVEPOINT mobile_display_snapshot')


def _stamp(shared, identity, fields, tokens):
    # Blank revisions refer to the original snapshot. Sparse form keeps a
    # large, newly shared file small and binds each edit to its file and row.
    return {'dataset': shared, 'row': identity, '__row__': tokens.get('__row__', ''),
            **{field: tokens[col] for field, col in fields.items() if col in tokens}}


def decorate(repo, flows, anchors):
    shared = dataset(repo)
    if not shared:
        return
    all_tokens = {}
    for identity, column, token in repo.conn.execute('SELECT identity,column_name,token FROM sync_versions'):
        all_tokens.setdefault(identity, {})[column] = token
    for rows, table, fields in ((flows, 'flows', FLOW_FIELDS), (anchors, 'account_balances', ANCHOR_FIELDS)):
        for row in rows:
            key = [row['flow_id']] if table == 'flows' else [row['name'].casefold(), row['units']]
            identity = _key(table, key)
            row['edit_revision'] = _stamp(shared, identity, fields, all_tokens.get(identity, {}))


def check(repo, table, key, expected, changed):
    shared = dataset(repo)
    if not shared:
        if expected is not None:
            raise ValueError('The active file changed. Reopen the record before editing it.')
        return
    identity = _key(table, key)
    fields = FLOW_FIELDS if table == 'flows' else ANCHOR_FIELDS
    current = dict(repo.conn.execute('SELECT column_name,token FROM sync_versions WHERE identity=?', (identity,)))
    stamp = _stamp(shared, identity, fields, current)
    if not isinstance(expected, dict) or any(expected.get(k) != stamp[k] for k in ('dataset', 'row', '__row__')):
        raise ValueError('This record changed or was reopened. Reopen it before editing it.')
    for field in changed:
        if field not in fields or expected.get(field, '') != stamp.get(field, ''):
            raise ValueError(field.replace('_', ' ').title() + ' changed on another device. Reopen the record before editing it.')
