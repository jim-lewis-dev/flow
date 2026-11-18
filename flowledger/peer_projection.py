"""Project immutable peer events onto the ordinary Flow tables.

Each logical field is a multi-value register. Its revisions form a causal
antichain: a later revision only replaces revisions it has observed. No wall
clock, device ordering, arrival order or elected writer selects a value.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import heapq
import json

from .core import DEFAULT_ANCHOR
from .history import _COLUMNS, _KEY_LENGTH
from .sync import _key


def _wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_wire(value).encode()).hexdigest()


def _groups(table):
    fields = list(_COLUMNS[table][_KEY_LENGTH[table]:])
    groups = []
    # The magnitude has no meaning without its unit. A point is one position.
    for group in (('amount', 'units'), ('x', 'y')):
        if all(field in fields for field in group):
            groups.append(group)
            fields = [f for f in fields if f not in group]
    if table == 'flows':
        fields.append('tags')
    return groups + [(field,) for field in fields]


def _get(row, group):
    if row is None or group == ('__row__',):
        return row
    if len(group) == 1:
        return row[group[0]]
    return {field: row[field] for field in group}


def _put(row, group, value):
    if len(group) == 1:
        row[group[0]] = value
    else:
        row.update(value)


def _ordered(events):
    indexed = {}
    for event in events.values() if isinstance(events, dict) else events:
        key = event['id']
        if key in indexed and indexed[key] != event:
            raise ValueError('A peer event identifier was reused.')
        indexed[key] = event
    remaining = {}
    children = defaultdict(list)
    for key, event in indexed.items():
        parents = set(event['parents'])
        if any(parent not in indexed for parent in parents):
            raise ValueError('Missing parent peer event.')
        remaining[key] = len(parents)
        for parent in parents:
            children[parent].append(key)
    ready = [key for key, pending in remaining.items() if not pending]
    heapq.heapify(ready)
    emitted = 0
    while ready:
        key = heapq.heappop(ready)
        yield indexed[key]
        emitted += 1
        for child in children[key]:
            remaining[child] -= 1
            if not remaining[child]:
                heapq.heappush(ready, child)
    if emitted != len(indexed):
        raise ValueError('Cycle in peer event history.')


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
        self.structural = 0
        self.field_masks = defaultdict(int)
        self.tag_keys = set((seed or {}).get('tags', {}))

    def add(self, index, before, after, *, whole=False, chosen=None):
        bit = 1 << index
        whole = whole or before is None or after is None
        self.writes[index] = _Write(index, before, after)
        self.written |= bit
        if whole:
            self.structural |= bit
        for group in self.groups:
            if whole or group == chosen or _get(before, group) != _get(after, group):
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

    def token(self, heads):
        ids = sorted(self.p.names[w.index] for w in heads)
        return ids[0] if len(ids) == 1 else _digest(ids) if ids else ''

    def common(self, heads):
        common = self.p.past[heads[0].index]
        for w in heads[1:]:
            common &= self.p.past[w.index]
        return common

    def strong(self, heads):
        # save_flow may independently create an empty anchor in the same
        # account/unit that another peer initialized with an opening balance.
        # An automatically supplied default does not overwrite that balance.
        if self.table != 'account_balances':
            return heads
        def placeholder(w):
            return (w.before is None and w.after is not None
                    and w.after['opening_amount'] is None
                    and w.after['opening_at'] == DEFAULT_ANCHOR)
        return [w for w in heads if not placeholder(w)] or heads

    def conflict(self, group, base, heads, alternatives=None):
        name = {'__row__': 'record', 'tags': 'tags', 'from_key': 'from account',
                'to_key': 'to account', 'category_key': 'category'}
        alternatives = alternatives or [(w, _get(w.after, group)) for w in heads]
        values = {_wire(value): value for w, value in alternatives}
        revision = self.token(heads)
        conflict_id = _digest([self.identity, group, revision])
        fields = list(_COLUMNS[self.table]) if group == ('__row__',) else list(group)
        if group == ('__row__',):
            fields.insert(0, '__row__')
            if self.table == 'flows':
                fields.append('tags')
        choices = []
        for encoded, value in sorted(values.items()):
            if group == ('__row__',):
                label = 'Delete record' if value is None else 'Keep ' + (_wire(value))
            elif group == ('tags',):
                label = ', '.join(k for k, _ in sorted(value.items(), key=lambda p: (p[1], p[0]))) or '(no tags)'
            elif group == ('amount', 'units'):
                label = value['amount'] + ' ' + value['units']
            else:
                label = str(value) if isinstance(value, str) and value else encoded
            sources = [w.index for w, v in alternatives if _wire(v) == encoded]
            choices.append({'id': _digest([conflict_id, value]), 'label': label, 'value': value,
                            'events': sorted(self.p.names[i] for i in sources),
                            'origins': sorted({self.p.origins[i] for i in sources if self.p.origins[i]})})
        title = self.table.replace('_', ' ') + ' ' + ', '.join(self.key) + ': ' + ' / '.join(name.get(f, f.replace('_', ' ')) for f in group)
        return {'id': conflict_id, 'identity': self.identity, 'table': self.table,
                'key': self.key, 'fields': fields, 'group': list(group), 'title': title,
                'base': base, 'choices': choices}

    def field(self, group, mask):
        heads = self.strong(self.heads(mask & self.field_masks[group]))
        if not heads:
            return _get(self.seed, group), None
        if len(heads) == 1:
            return _get(heads[0].after, group), None
        values = {_wire(_get(w.after, group)) for w in heads}
        if len(values) == 1:
            return _get(heads[0].after, group), None
        # Repeated competing resolutions can have another unresolved register
        # as their common past. Walk it without using the Python call stack.
        step = heads
        while True:
            common = self.common(step)
            observed = self.strong(self.heads(common & self.field_masks[group]))
            if not observed:
                base = _get(self.seed, group)
                if self.seed is None:
                    # Independent but identical creations can be the common
                    # starting value even when they have no shared event ID.
                    created = [w for i, w in self.writes.items() if mask & (1 << i)
                               and w.before is None and w.after is not None]
                    prior = {_wire(_get(w.after, group)) for w in created}
                    if len(prior) == 1:
                        base = _get(created[0].after, group)
                    elif all(w.before is not None for w in step) and len({_wire(_get(w.before, group)) for w in step}) == 1:
                        base = _get(step[0].before, group)
                break
            if len(observed) == 1 or len({_wire(_get(w.after, group)) for w in observed}) == 1:
                base = _get(observed[0].after, group)
                break
            step = observed
        if base is None and group != ('opening_amount',):
            # Two independent decisions can restore exactly the same entire
            # row. Its values are a valid common starting point for a later
            # edit, even if the decisions' shared *events* include a delete.
            starts = self.strong(self.heads(mask & self.structural))
            if starts and all(w.after is not None for w in starts):
                matches = {_wire(_get(w.after, group)) for w in starts}
                if len(matches) == 1:
                    base = _get(starts[0].after, group)
        return base, self.conflict(group, base, heads)

    def view(self, mask):
        heads = self.heads(mask & self.written)
        if not heads:
            return self.seed, []
        if all(w.after is None for w in heads):
            return None, []
        structural = self.strong(self.heads(mask & self.structural))
        # Concurrent creations of different complete records have no shared
        # row onto which field-by-field edits could safely be rendered.
        different_creations = (len(structural) > 1 and
                               len({_wire(w.after) for w in structural}) > 1)
        if any(w.after is None for w in heads) or different_creations:
            live = self.strong(heads)
            if all(w.after is None for w in live):
                return None, []
            if len({_wire(w.after) for w in live}) > 1:
                base, _ = self.view(self.common(live))
                alternatives = None
                keep = [w for w in live if w.after is not None]
                if len(keep) < len(live) and len(keep) > 1:
                    # Deleting a record races with its *merged* living
                    # branch. Preserve disjoint notes, amount, tags, etc.
                    surviving = 0
                    for w in keep:
                        surviving |= self.p.past[w.index] | (1 << w.index)
                    combined, nested = self.view(surviving)
                    if combined is not None and not any(c['group'] == ['__row__'] for c in nested):
                        alternatives = []
                        for w in live:
                            value = None
                            if w.after is not None:
                                value = dict(combined)
                                for c in nested:
                                    g = tuple(c['group'])
                                    _put(value, g, _get(w.after, g))
                            alternatives.append((w, value))
                return base, [self.conflict(('__row__',), base, live, alternatives)]
        row = dict(next(w.after for w in heads if w.after is not None))
        conflicts = []
        for group in self.groups:
            value, conflict = self.field(group, mask)
            if value is None and group != ('opening_amount',):
                # No complete common row exists across these generations.
                # Expose whole valid alternatives rather than constructing a
                # flow with absent units, an invalid point, or orphan tags.
                rivals = self.strong(self.heads(mask & self.field_masks[group]))
                base, _ = self.view(self.common(rivals))
                return base, [self.conflict(('__row__',), base, rivals)]
            _put(row, group, value)
            if conflict:
                conflicts.append(conflict)
        return row, conflicts

    def versions(self, mask, conflicts):
        fields = {}
        structural = self.heads(mask & self.structural)
        if any(c['group'] == ['__row__'] for c in conflicts):
            structural = self.heads(mask & self.written)
        if structural:
            token = self.token(structural)
            fields.update(dict.fromkeys(('__row__', *_COLUMNS[self.table][:_KEY_LENGTH[self.table]]), token))
        for group in self.groups:
            heads = self.heads(mask & self.field_masks[group])
            if heads:
                fields.update(dict.fromkeys(group, self.token(heads)))
        return fields


class _Projection:
    def __init__(self, initial):
        self.records = {}
        self.names = []
        self.origins = []
        self.past = []
        self.index = {}
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

    def ingest(self, event):
        index = len(self.names)
        past = 0
        for key in event['parents']:
            parent = self.index[key]
            past |= self.past[parent] | (1 << parent)
        self.index[event['id']] = index
        self.names.append(event['id'])
        self.origins.append(event.get('origin', ''))
        self.past.append(past)
        if 'resolution' in event:
            self.resolve(index, past, event['resolution'])
            return
        by_record = defaultdict(list)
        for change in event['changes']:
            table, key = change['table'], change['key']
            if table == 'flow_tags':
                table, key = 'flows', key[:1]
            by_record[self.record(table, key)].append(change)
        for r, changes in by_record.items():
            before, conflicts = r.view(past)
            main = next((c for c in changes if c['table'] != 'flow_tags'), None)
            public_before = {k: v for k, v in before.items() if k != 'tags'} if before is not None else None
            # Concurrent independent endpoint/unit edits can materialize a
            # default support row. Validate edits to it against the same view
            # the user saw, then record its first explicit value.
            derived = False
            if main and main['before'] != public_before and r.table in ('accounts', 'account_balances', 'categories', 'tags'):
                actual = self.render(past)[0].get(r.identity, {}).get('row')
                if actual == main['before']:
                    before = actual
                    public_before = actual
                    derived = True
            if main and main['before'] != public_before:
                raise ValueError('Peer change has the wrong prior row: ' + r.identity)
            after = main['after'] if main else public_before
            after = dict(after) if after is not None else None
            if r.table == 'flows':
                tags = dict((before or {}).get('tags', {}))
                for c in changes:
                    if c['table'] != 'flow_tags':
                        continue
                    flow, tag = c['key']
                    old = {'flow_id': flow, 'tag_key': tag, 'position': tags[tag]} if tag in tags else None
                    if c['before'] != old:
                        raise ValueError('Peer change has the wrong prior flow tags.')
                    if c['after'] is None:
                        tags.pop(tag, None)
                    else:
                        tags[tag] = c['after']['position']
                if after is None:
                    if any(c['after'] is not None for c in changes if c['table'] == 'flow_tags'):
                        raise ValueError('Peer tags require an existing flow.')
                else:
                    after['tags'] = tags
            for conflict in conflicts:
                group = tuple(conflict['group'])
                if group == ('__row__',) or after is None or _get(before, group) != _get(after, group):
                    raise ValueError('Choose a value for the existing peer conflict first.')
            if before != after:
                r.add(index, before, after, whole=derived)

    def resolve(self, index, past, decision):
        for r in self.records.values():
            before, conflicts = r.view(past)
            for c in conflicts:
                if c['id'] != decision['conflict']:
                    continue
                choice = next((v for v in c['choices'] if v['id'] == decision['choice']), None)
                if choice is None:
                    raise ValueError('The selected value was not in the observed peer conflict.')
                group = tuple(c['group'])
                if group == ('__row__',):
                    r.add(index, before, choice['value'], whole=True)
                else:
                    after = dict(before)
                    _put(after, group, choice['value'])
                    r.add(index, before, after, chosen=group)
                return
        raise ValueError('The peer resolution does not identify an observed conflict.')

    def render(self, mask):
        rows, conflicts, versions = {}, [], {}
        def emit(table, key, row):
            rows[_key(table, key)] = {'table': table, 'key': list(key), 'row': row}
        for r in self.records.values():
            row, unresolved = r.view(mask)
            conflicts.extend(unresolved)
            fields = r.versions(mask, unresolved)
            if fields:
                versions[r.identity] = fields
            if r.table == 'flows':
                token = fields.get('tags')
                for tag in sorted(r.tag_keys):
                    key = [r.key[0], tag]
                    if token:
                        versions[_key('flow_tags', key)] = dict.fromkeys(('__row__', *_COLUMNS['flow_tags']), token)
                if row is not None:
                    for tag, position in sorted(row['tags'].items(), key=lambda p: (p[1], p[0])):
                        emit('flow_tags', [r.key[0], tag], {'flow_id': r.key[0], 'tag_key': tag, 'position': position})
                    row = {k: v for k, v in row.items() if k != 'tags'}
            if row is not None:
                emit(r.table, r.key, row)
        # Rebuild only support that a surviving relation requires. A removed
        # support row can remain visible until its last concurrent user goes
        # away; never resurrect a deleted flow or keep an orphan tag link.
        def require(table, key, default):
            identity = _key(table, key)
            if identity in rows:
                return
            record = self.records.get(identity)
            fallback = None
            if record:
                heads = record.heads(mask & record.written)
                if heads and all(w.after is None for w in heads):
                    prior = {_wire(w.before) for w in heads}
                    if len(prior) == 1:
                        fallback = heads[0].before
            emit(table, key, fallback or default)
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
        return rows, sorted(conflicts, key=lambda c: c['id']), versions


def project(initial_rows, events):
    """Return (physical rows, unresolved decisions, field revision tokens).

    Input may be any ordering of a complete parent-closed event graph. The
    initial snapshot and event payloads are never modified.
    """
    projection = _Projection(initial_rows)
    for event in _ordered(events):
        projection.ingest(event)
    return projection.render((1 << len(projection.names)) - 1)
