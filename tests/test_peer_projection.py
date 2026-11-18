"""Latest-edit-wins projection and relational integrity."""
from copy import deepcopy
from itertools import permutations
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flowledger.core import DEFAULT_ANCHOR, Repository
from flowledger.peer_projection import project
from flowledger.sync import SyncState, _key


def change(table, key, before, after):
    return dict(table=table, key=list(key), before=before, after=after)


def event(name, *changes, parents=(), stamp=100):
    result = dict(id=name, dataset='seed', parents=list(parents), changes=list(changes))
    if stamp is not None:
        result['stamp'] = [stamp, 0] if isinstance(stamp, int) else stamp
    return result



class ProjectionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Repository(Path(directory.name) / 'base.flow')
        self.addCleanup(self.repo.close)
        self.flow = self.repo.save_flow({'date': '2026-01-02', 'from_account': 'A', 'to_account': 'B',
                                         'amount': '2', 'units': 'USD', 'name': 'Baseline', 'tags': ['First']})
        self.db = SyncState(self.repo)
        self.initial = self.db._rows()
        self.key = [self.flow['flow_id']]
        self.identity = _key('flows', self.key)
        self.base = self.initial[self.identity]['row']

    def edit(self, event_id, parents=(), before=None, stamp=100, **updates):
        before = self.base if before is None else before
        return event(event_id, change('flows', self.key, before, dict(before, **updates)),
                     parents=parents, stamp=stamp)

    def assert_orders(self, events):
        reference = project(self.initial, events)
        for order in permutations(events):
            self.assertEqual(reference, project(self.initial, order))
        self.assertEqual([], reference[1])
        return reference

    def assert_relations(self, rows):
        with self.repo.atomic():
            self.db._replace_rows(rows)
        self.assertIsNone(self.repo.conn.execute('PRAGMA foreign_key_check').fetchone())
        self.assertEqual(rows, self.db._rows())

    def test_actual_changed_fields_merge_without_repainting_newer_values(self):
        note = self.edit('note', stamp=300, notes='New note')
        title = self.edit('title', stamp=200, name='New title')
        stale = self.edit('stale', stamp=100, notes='Old note')
        rows, _, versions = self.assert_orders([title, note, stale])
        self.assertEqual(('New title', 'New note'),
                         tuple(rows[self.identity]['row'][key] for key in ('name', 'notes')))
        self.assertEqual('note', versions[self.identity]['notes'])
        self.assertEqual('title', versions[self.identity]['name'])
        self.assertNotIn('__row__', versions[self.identity])
        self.assert_relations(rows)

    def test_timestamp_then_counter_then_event_id_break_ties(self):
        a = self.edit('z', stamp=[100, 0], notes='first counter')
        b = self.edit('a', stamp=[100, 1], notes='next counter')
        c = self.edit('b', stamp=[100, 1], notes='event tie')
        rows, _, versions = self.assert_orders([a, b, c])
        self.assertEqual('event tie', rows[self.identity]['row']['notes'])
        self.assertEqual('b', versions[self.identity]['notes'])

    def test_quantity_and_unit_are_one_atomic_field(self):
        amount = self.edit('amount', stamp=100, amount='3')
        unit = self.edit('unit', stamp=200, units='hours')
        rows, _, versions = self.assert_orders([amount, unit])
        self.assertEqual(('2', 'hours'), tuple(rows[self.identity]['row'][key] for key in ('amount', 'units')))
        self.assertEqual('unit', versions[self.identity]['amount'])
        self.assertEqual('unit', versions[self.identity]['units'])
        self.assert_relations(rows)
        self.assertIsNotNone(self.repo.get_account('A', 'hours'))

    def test_coordinates_are_one_atomic_field(self):
        key = ['a']
        base = dict(account_key='a', x=1.0, y=2.0)
        create = event('create', change('account_positions', key, None, base))
        x = event('x', change('account_positions', key, base, dict(base, x=3.0)), parents=['create'], stamp=200)
        y = event('y', change('account_positions', key, base, dict(base, y=4.0)), parents=['create'], stamp=300)
        rows, _, versions = self.assert_orders([create, x, y])
        identity = _key('account_positions', key)
        self.assertEqual(dict(base, y=4.0), rows[identity]['row'])
        self.assertEqual(versions[identity]['x'], versions[identity]['y'])
        self.assert_relations(rows)

    def test_ordered_tags_are_atomic_and_unrelated_name_merges(self):
        def add_tag(name, tag, stamp):
            key = self.key + [tag]
            return event(name, change('flow_tags', key, None,
                         dict(flow_id=self.key[0], tag_key=tag, position=1)), stamp=stamp)
        a, b = add_tag('a', 'hot', 100), add_tag('b', 'cold', 200)
        rename = self.edit('rename', name='other', stamp=300)
        rows, _, versions = self.assert_orders([a, b, rename])
        links = {entity['key'][1] for entity in rows.values() if entity['table'] == 'flow_tags'}
        self.assertEqual({'first', 'cold'}, links)
        self.assertEqual('other', rows[self.identity]['row']['name'])
        self.assertEqual('b', versions[self.identity]['tags'])
        self.assertEqual('b', versions[_key('flow_tags', self.key + ['hot'])]['__row__'])
        self.assert_relations(rows)

    def test_older_incoming_edit_cannot_resurrect_a_deleted_row(self):
        old = self.edit('old', stamp=100, notes='stale note')
        delete = event('delete', change('flows', self.key, self.base, None), stamp=200)
        rows, _, versions = self.assert_orders([old, delete])
        self.assertNotIn(self.identity, rows)
        self.assertFalse(any(record['table'] == 'flow_tags' for record in rows.values()))
        self.assertEqual('delete', versions[self.identity]['__row__'])
        self.assert_relations(rows)

    def test_newer_note_resurrects_complete_row_and_preserves_older_field_winners(self):
        title = self.edit('title', stamp=100, name='New title')
        delete = event('delete', change('flows', self.key, self.base, None), stamp=200)
        note = self.edit('note', stamp=300, notes='New note')
        rows, _, versions = self.assert_orders([note, title, delete])
        self.assertEqual(dict(self.base, name='New title', notes='New note'), rows[self.identity]['row'])
        self.assertIn(_key('flow_tags', self.key + ['first']), rows)
        self.assertEqual('note', versions[self.identity]['__row__'])
        current = rows[self.identity]['row']
        date = self.edit('date', parents=['title', 'delete', 'note'], before=current,
                         stamp=400, date='2026-02-02 00:00:00')
        updated = project(self.initial, [delete, title, note, date])
        self.assertEqual('note', updated[2][self.identity]['__row__'])
        self.assert_relations(rows)

    def test_newer_tag_only_edit_resurrects_row_and_complete_tag_list(self):
        key = self.key + ['first']
        tag = self.initial[_key('flow_tags', key)]['row']
        another = self.key + ['second']
        new_tag = dict(flow_id=self.key[0], tag_key='second', position=1)
        delete = event('delete', change('flow_tags', key, tag, None),
                       change('flows', self.key, self.base, None), stamp=100)
        add = event('add', change('flow_tags', another, None, new_tag), stamp=200)
        rows, _, _ = self.assert_orders([delete, add])
        self.assertEqual(self.base, rows[self.identity]['row'])
        self.assert_relations(rows)
        self.assertEqual(['First', 'second'], self.repo.get_flow(self.key[0])['tags'])

    def test_noop_snapshot_is_not_a_liveness_edit(self):
        delete = event('delete', change('flows', self.key, self.base, None), stamp=100)
        noop = event('noop', change('flows', self.key, self.base, self.base), stamp=200)
        rows, _, _ = self.assert_orders([delete, noop])
        self.assertNotIn(self.identity, rows)

    def test_delete_and_create_in_same_event_do_not_mix_liveness(self):
        transaction = event('transaction', change('flows', self.key, self.base, None),
                            change('categories', ['new'], None, {'category_key': 'new', 'name': 'New'}))
        rows, _, _ = project(self.initial, [transaction])
        self.assertNotIn(self.identity, rows)
        self.assertIn(_key('categories', ['new']), rows)
        self.assert_relations(rows)

    def test_independent_endpoint_and_unit_derive_support_then_accept_real_balance(self):
        endpoint = self.edit('endpoint', from_key='c', stamp=100)
        unit = self.edit('unit', units='kg', stamp=200)
        rows, _, _ = self.assert_orders([endpoint, unit])
        self.assert_relations(rows)
        key = ['c', 'kg']
        derived = rows[_key('account_balances', key)]['row']
        balance = event('balance', change('account_balances', key, derived, dict(derived, opening_amount='42')),
                        parents=['endpoint', 'unit'], stamp=300)
        rows, conflicts, _ = project(self.initial, [balance, unit, endpoint])
        self.assertFalse(conflicts)
        self.assertEqual('42', rows[_key('account_balances', key)]['row']['opening_amount'])
        self.assert_relations(rows)

    def test_newer_automatic_anchor_does_not_overwrite_explicit_concurrent_balance(self):
        key = ['a', 'kg']
        default = dict(account_key='a', units='kg', opening_amount=None, opening_at=DEFAULT_ANCHOR)
        real = dict(default, opening_amount='15')
        auto = event('auto', change('account_balances', key, None, default), stamp=200)
        explicit = event('explicit', change('account_balances', key, None, real), stamp=100)
        rows, _, versions = self.assert_orders([auto, explicit])
        identity = _key('account_balances', key)
        self.assertEqual(real, rows[identity]['row'])
        self.assertEqual('explicit', versions[identity]['opening_amount'])
        self.assert_relations(rows)

    def test_deleted_support_is_retained_only_for_surviving_relations(self):
        key = ['a', 'USD']
        identity = _key('account_balances', key)
        drop = event('drop', change('account_balances', key, self.initial[identity]['row'], None),
                     change('flows', self.key, self.base, None), stamp=300)
        fid = 'ebb8e61e-6798-4417-a8ae-f76969a45204'
        extra = event('add', change('flows', [fid], None, dict(self.base, flow_id=fid)), stamp=100)
        rows, _, _ = self.assert_orders([drop, extra])
        self.assertEqual(rows[identity], self.initial[identity])
        self.assertIsNone(rows.get(self.identity))
        self.assert_relations(rows)

    def test_before_image_uses_causal_view_not_arrival_order(self):
        first = self.edit('first', stamp=100, name='First')
        second = self.edit('second', stamp=200, name='Second')
        before = dict(self.base, name='Second')
        note = self.edit('note', parents=['first', 'second'], before=before, stamp=300, notes='merged')
        rows, _, _ = self.assert_orders([note, second, first])
        self.assertEqual(dict(before, notes='merged'), rows[self.identity]['row'])
        invalid = self.edit('bad', parents=['first', 'second'], stamp=300, notes='wrong prior')
        with self.assertRaisesRegex(ValueError, 'wrong prior'):
            project(self.initial, [invalid, first, second])

    def test_revision_tokens_capture_aba_and_only_changed_atomic_groups(self):
        first = self.edit('first', stamp=100, units='kg', name='other')
        prior = first['changes'][0]['after']
        second = self.edit('second', parents=['first'], before=prior, stamp=200, name=self.base['name'])
        rows, _, versions = self.assert_orders([first, second])
        fields = versions[self.identity]
        self.assertEqual('second', fields['name'])
        self.assertEqual('first', fields['amount'])
        self.assertEqual('first', fields['units'])
        self.assertFalse({'notes', 'tags', '__row__'} & fields.keys())
        stale = self.edit('stale', stamp=50, name='losing')
        self.assertEqual((rows, [], versions), project(self.initial, [first, second, stale]))

    def test_tag_only_undo_is_guarded_across_flow_delete_and_resurrection(self):
        from flowledger import peer_store as store
        from flowledger.history import SessionHistory
        from flowledger.sync import SyncConflict

        store.share(self.repo, 'Water')
        history = SessionHistory(self.repo)
        self.addCleanup(history.close)
        with patch.object(store.time, 'time_ns', return_value=100_000_000):
            history.perform('Add tag', lambda: self.repo.save_flow(dict(self.flow, tags=['First', 'Second'])))
        peers = []
        for name in ('delete', 'revive'):
            peer = Repository(Path(self.repo.path).with_name(name + '.flow'))
            self.addCleanup(peer.close)
            store.join(peer, store.seed(self.repo))
            store.receive(peer, store.missing(self.repo, []))
            peers.append(peer)
        delete, revive = peers
        with patch.object(store.time, 'time_ns', return_value=200_000_000):
            delete.delete_flow(self.key[0])
        with patch.object(store.time, 'time_ns', return_value=300_000_000):
            revive.save_flow(dict(revive.get_flow(self.key[0]), notes='Resurrected'))
        store.receive(self.repo, store.missing(delete, store.inventory(self.repo)))
        store.receive(self.repo, store.missing(revive, store.inventory(self.repo)))
        self.assertEqual(['First', 'Second'], self.repo.get_flow(self.key[0])['tags'])
        before = store.inventory(self.repo)
        with self.assertRaisesRegex(SyncConflict, 'newer change'):
            history.undo()
        self.assertEqual(before, store.inventory(self.repo))
        self.assertEqual(['First', 'Second'], self.repo.get_flow(self.key[0])['tags'])

    def test_duplicate_events_are_idempotent_and_inputs_remain_immutable(self):
        a = self.edit('a', amount='3')
        b = self.edit('b', name='new')
        before = deepcopy([self.initial, a, b])
        self.assertEqual(project(self.initial, [a, b]), project(self.initial, [b, a, a, b]))
        self.assertEqual(before, [self.initial, a, b])
        with self.assertRaisesRegex(ValueError, 'reused'):
            project(self.initial, [a, dict(b, id='a')])
        with self.assertRaisesRegex(ValueError, 'Missing parent'):
            project(self.initial, [dict(a, parents=['lost'])])
        with self.assertRaisesRegex(ValueError, 'Cycle'):
            project(self.initial, [dict(a, parents=['b']), dict(b, parents=['a'])])

    def test_invalid_or_noncausal_stamps_are_rejected(self):
        a = self.edit('a', stamp=[100, 1], notes='first')
        for stamp in ([100, 0], [100, 1], [99, 9]):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, 'follow its parents'):
                project(self.initial, [a, self.edit('b', parents=['a'], before=a['changes'][0]['after'],
                                                     stamp=stamp, notes='second')])
        for stamp in ([0, 1], [-1, 0], [1, -1], [True, 0], [1, False], [1.0, 0],
                      [2**53, 0], [1, 2**53], [1], (1, 0)):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(ValueError, 'Invalid peer edit stamp'):
                project(self.initial, [self.edit('a', stamp=stamp, notes='first')])

    def test_unstamped_events_and_resolution_payloads_are_rejected(self):
        unstamped = self.edit('unstamped', stamp=None, notes='No timestamp')
        with self.assertRaisesRegex(ValueError, 'Invalid peer edit stamp'):
            project(self.initial, [unstamped])
        first = self.edit('first', notes='Stamped')
        child = self.edit('unstamped-child', stamp=None, parents=['first'],
                          before=first['changes'][0]['after'], notes='No timestamp')
        with self.assertRaisesRegex(ValueError, 'Invalid peer edit stamp'):
            project(self.initial, [first, child])
        resolved = dict(first, resolution={'conflict': 'old', 'choice': 'old'})
        with self.assertRaisesRegex(ValueError, 'Unsupported peer resolution event'):
            project(self.initial, [resolved])

    def test_four_replicas_delete_recreate_and_replay_without_partial_rows(self):
        rng = Random(19)
        for run in range(10):
            seed = self.initial if run % 2 else {
                k: v for k, v in self.initial.items() if v['table'] not in ('flows', 'flow_tags')}
            devices = [{} for _ in range(4)]
            for step in range(45):
                own = rng.choice(devices)
                if rng.random() < .45:
                    own.update(rng.choice(devices))
                rows, conflicts, _ = project(seed, own)
                self.assertFalse(conflicts)
                parents = set(own) - {p for e in own.values() for p in e['parents']}
                name = str(step)
                before = rows.get(self.identity, {}).get('row')
                if before is not None and rng.random() < .25:
                    after = None
                else:
                    after = dict(before or self.base)
                    field = rng.choice(['amount', 'units', 'notes', 'name'])
                    after[field] = str(rng.randrange(7))
                own[name] = event(name, change('flows', self.key, before, after),
                                  parents=sorted(parents), stamp=100 + step)
            merged = {i: e for device in devices for i, e in device.items()}
            result = project(seed, merged)
            self.assertEqual(result, project(seed, reversed(list(merged.values()))))
            self.assert_relations(result[0])
