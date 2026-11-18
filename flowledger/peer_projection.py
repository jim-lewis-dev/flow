"""Deterministic latest-edit-wins projection of immutable peer events.

A stamp orders actual edits to each logical field, never entire row snapshots.
Quantity/unit, map coordinates and each ordered tag list are atomic groups.
Historical unstamped events are validated with their original causal semantics
before their writes are translated into these registers.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .core import DEFAULT_ANCHOR
from .history import _COLUMNS, _KEY_LENGTH
from .peer_legacy import _Projection as _LegacyProjection, _digest, _get, _groups, _ordered, _put
from .sync import _key


@dataclass
class _Write:
    index: int
    before: dict | None
    after: dict | None


class _Record:
    def __init__(self, projection, table, key, seed):
        self.p = projection
        self.table = table
        self.key = key
        self.identity = _key(table, key)
        self.seed = seed
        self.groups = _groups(table)
        self.writes = {}
        self.written = 0
        self.living = 0
        self.structural = 0
        self.field_masks = defaultdict(int)
        self.tag_keys = set((seed or {}).get('tags', {}))

    def add(self, index, before, after, *, whole=False, groups=None):
        bit = 1 << index
        self.writes[index] = _Write(index, before, after)
        self.written |= bit
        if whole or before is None or after is None:
            self.structural |= bit
        # Deletion changes liveness only. Keeping earlier field values lets a
        # newer edit restore a complete record without borrowing null fields
        # from the tombstone or repainting unrelated, newer edits.
        if after is not None:
            self.living |= bit
            for group in self.groups:
                touched = (group in groups if groups is not None else
                           whole or before is None or _get(before, group) != _get(after, group))
                if touched:
                    self.field_masks[group] |= bit
        if self.table == 'flows':
            self.tag_keys.update((before or {}).get('tags', {}))
            self.tag_keys.update((after or {}).get('tags', {}))

    def heads(self, mask):
        result = []
        while mask:
            index = mask.bit_length() - 1
            result.append(self.writes[index])
            mask &= ~((1 << index) | self.p.past[index])
        return result

    def winner(self, mask, *, strong=False):
        heads = self.heads(mask)
        if strong and self.table == 'account_balances':
            # An automatically created reference anchor must not erase an
            # explicit opening balance independently entered by another peer.
            explicit = [w for w in heads if not (
                w.before is None and w.after is not None
                and w.after['opening_amount'] is None
                and w.after['opening_at'] == DEFAULT_ANCHOR)]
            heads = explicit or heads
        return max(heads, key=lambda w: self.p.order[w.index], default=None)

    def content(self, mask):
        """Build the latest complete living values, including before deletes."""
        live = self.winner(mask & self.living)
        if live is None and self.seed is None:
            return None
        row = dict(live.after if live else self.seed)
        for group in self.groups:
            winner = self.winner(mask & self.field_masks[group], strong=True)
            _put(row, group, _get(winner.after if winner else self.seed, group))
        return row

    def view(self, mask):
        last = self.winner(mask & self.written)
        if last is not None and last.after is None:
            return None
        return self.content(mask)

    def versions(self, mask):
        fields = {}
        structure = self.winner(mask & self.structural)
        if structure is not None:
            # A regular edit does not invalidate every unrelated undo field.
            # The first real edit after the newest delete does change the row
            # incarnation; subsequent ordinary edits retain that row token.
            if structure.after is None:
                revivals = [w for w in self.writes.values()
                            if mask & (1 << w.index) and w.after is not None
                            and self.p.order[w.index] > self.p.order[structure.index]]
                if revivals:
                    structure = min(revivals, key=lambda w: self.p.order[w.index])
            token = self.p.names[structure.index]
            fields.update(dict.fromkeys(('__row__', *_COLUMNS[self.table][:_KEY_LENGTH[self.table]]), token))
        for group in self.groups:
            winner = self.winner(mask & self.field_masks[group], strong=True)
            if winner is not None:
                fields.update(dict.fromkeys(group, self.p.names[winner.index]))
        return fields


class _Projection:
    def __init__(self, initial):
        self.records = {}
        self.names = []
        self.past = []
        self.index = {}
        self.order = []
        self.legacy = set()
        tags = defaultdict(dict)
        for entity in initial.values():
            if entity['table'] == 'flow_tags':
                flow, tag = entity['key']
                tags[flow][tag] = entity['row']['position']
        for entity in initial.values():
            table, key = entity['table'], entity['key']
            if table != 'flow_tags':
                row = dict(entity['row'])
                if table == 'flows':
                    row['tags'] = tags[key[0]]
                self.records[_key(table, key)] = _Record(self, table, key, row)

    def record(self, table, key):
        identity = _key(table, key)
        if identity not in self.records:
            self.records[identity] = _Record(self, table, key, None)
        return self.records[identity]

    def register(self, event):
        index = len(self.names)
        parents = [self.index[key] for key in event['parents']]
        past = 0
        for parent in parents:
            past |= self.past[parent] | (1 << parent)
        if 'stamp' in event:
            stamp = event['stamp']
            if (not isinstance(stamp, list) or len(stamp) != 2
                    or any(type(n) is not int for n in stamp)
                    or stamp[0] <= 0 or stamp[1] < 0
                    or any(n > 2**53 - 1 for n in stamp)):
                raise ValueError('Invalid peer edit stamp.')
            if any(tuple(stamp) <= self.order[parent][:2] for parent in parents):
                raise ValueError('Peer edit stamp must follow its parents.')
            if 'resolution' in event:
                raise ValueError('Stamped peer events cannot contain a legacy resolution.')
        else:
            if any(parent not in self.legacy for parent in parents):
                raise ValueError('Legacy peer events cannot follow stamped edits.')
            stamp = (0, 1 + max((self.order[parent][1] for parent in parents), default=0))
            self.legacy.add(index)
        self.index[event['id']] = index
        self.names.append(event['id'])
        self.past.append(past)
        self.order.append((*stamp, event['id']))
        return index, past

    def add(self, record, index, before, after, **kwargs):
        record.add(index, before, after, **kwargs)

    def ingest(self, event, historical=None):
        index, past = self.register(event)
        if historical is not None:
            for old, write, groups, whole in historical:
                record = self.record(old.table, old.key)
                self.add(record, index, write.before, write.after, groups=groups, whole=whole)
            return
        by_record = defaultdict(list)
        for change in event['changes']:
            table, key = change['table'], change['key']
            if table == 'flow_tags':
                table, key = 'flows', key[:1]
            by_record[self.record(table, key)].append(change)
        for record, changes in by_record.items():
            before = record.view(past)
            main = next((c for c in changes if c['table'] != 'flow_tags'), None)
            public_before = {k: v for k, v in before.items() if k != 'tags'} if before is not None else None
            derived = False
            if main and main['before'] != public_before and record.table in ('accounts', 'account_balances', 'categories', 'tags'):
                actual = self.render(past)[0].get(record.identity, {}).get('row')
                if actual == main['before']:
                    before = public_before = actual
                    derived = True
            if main and main['before'] != public_before:
                raise ValueError('Peer change has the wrong prior row: ' + record.identity)
            after = main['after'] if main else public_before
            after = dict(after) if after is not None else None
            if record.table == 'flows':
                tags = dict((before or {}).get('tags', {}))
                for change in changes:
                    if change['table'] != 'flow_tags':
                        continue
                    flow, tag = change['key']
                    old = {'flow_id': flow, 'tag_key': tag, 'position': tags[tag]} if tag in tags else None
                    if change['before'] != old:
                        raise ValueError('Peer change has the wrong prior flow tags.')
                    if change['after'] is None:
                        tags.pop(tag, None)
                    else:
                        tags[tag] = change['after']['position']
                if after is None:
                    if any(c['after'] is not None for c in changes if c['table'] == 'flow_tags'):
                        raise ValueError('Peer tags require an existing flow.')
                else:
                    after['tags'] = tags
            if before != after:
                self.add(record, index, before, after, whole=derived)

    def render(self, mask):
        rows, versions = {}, {}

        def emit(table, key, row):
            rows[_key(table, key)] = {'table': table, 'key': list(key), 'row': row}

        for record in self.records.values():
            row = record.view(mask)
            fields = record.versions(mask)
            if fields:
                versions[record.identity] = fields
            if record.table == 'flows':
                token = fields.get('tags')
                row_token = fields.get('__row__')
                if row_token and row_token != token:
                    # Tag-only history also belongs to the owning flow's row
                    # incarnation. A delete/revival must invalidate an old
                    # tag undo even when the ordered tag list is unchanged.
                    token = _digest([token or '', row_token])
                for tag in sorted(record.tag_keys):
                    key = [record.key[0], tag]
                    if token:
                        versions[_key('flow_tags', key)] = dict.fromkeys(('__row__', *_COLUMNS['flow_tags']), token)
                if row is not None:
                    for tag, position in sorted(row['tags'].items(), key=lambda p: (p[1], p[0])):
                        emit('flow_tags', [record.key[0], tag], {'flow_id': record.key[0], 'tag_key': tag, 'position': position})
                    row = {k: v for k, v in row.items() if k != 'tags'}
            if row is not None:
                emit(record.table, record.key, row)

        # Rebuild only supports required by surviving relations. A retained
        # account or category never revives its deleted dependent flows.
        def require(table, key, default):
            identity = _key(table, key)
            if identity in rows:
                return
            record = self.records.get(identity)
            fallback = record.content(mask) if record else None
            emit(table, key, fallback if fallback is not None else default)

        for entity in list(rows.values()):
            row = entity['row']
            if entity['table'] == 'flows':
                for key in (row['from_key'], row['to_key']):
                    unit = row['units']
                    require('account_balances', [key, unit], {'account_key': key, 'units': unit, 'opening_amount': None, 'opening_at': DEFAULT_ANCHOR})
                key = row['category_key']
                require('categories', [key], {'category_key': key, 'name': key})
            elif entity['table'] == 'flow_tags':
                key = row['tag_key']
                require('tags', [key], {'tag_key': key, 'name': key})
        for entity in list(rows.values()):
            if entity['table'] in ('account_positions', 'account_balances'):
                key = entity['key'][0]
                require('accounts', [key], {'account_key': key, 'name': key})
        return rows, [], versions


def project(initial_rows, events):
    """Return (physical rows, empty decision list, field revision tokens).

    Any delivery order of a complete parent-closed graph produces the same
    result. Neither the snapshot nor immutable event payloads are modified.
    """
    ordered = list(_ordered(events))
    historical = None
    translated = {}
    # Old authors saw the original causal projection, including unresolved
    # common values. Validate against that view before translating writes;
    # validating old before-images against today's winners would lose valid
    # old offline branches and recorded manual decisions.
    for event in ordered:
        if 'stamp' in event:
            continue
        if historical is None:
            historical = _LegacyProjection(initial_rows)
        if any(parent not in historical.index for parent in event['parents']):
            raise ValueError('Legacy peer events cannot follow stamped edits.')
        historical.ingest(event)
        index = historical.index[event['id']]
        bit = 1 << index
        if 'resolution' in event:
            candidates = historical.records.values()
        else:
            identities = {
                _key('flows', change['key'][:1]) if change['table'] == 'flow_tags'
                else _key(change['table'], change['key'])
                for change in event['changes']}
            candidates = (historical.records[identity] for identity in identities)
        translated[event['id']] = [
            (record, record.writes[index],
             {group for group, mask in record.field_masks.items() if mask & bit},
             bool(record.structural & bit))
            for record in candidates if index in record.writes]
    projection = _Projection(initial_rows)
    for event in ordered:
        projection.ingest(event, translated.get(event['id']))
    return projection.render((1 << len(projection.names)) - 1)
