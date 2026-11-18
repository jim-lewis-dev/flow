"""Causal projection properties independent of clocks, delivery and SQLite."""
from copy import deepcopy
from itertools import permutations
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from flowledger.core import DEFAULT_ANCHOR, Repository
from flowledger.peer_projection import project
from flowledger.sync import SyncState, _key


def change(table, key, before, after):
    return dict(table=table, key=list(key), before=before, after=after)


def event(name, *changes, parents=()):
    return dict(id=name, dataset='seed', parents=list(parents), changes=list(changes))


def decision(name, conflict, value, *parents):
    choice = next(c for c in conflict['choices'] if c['value'] == value)
    return dict(event(name, parents=parents), resolution={'conflict': conflict['id'], 'choice': choice['id']})


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

    def edit(self, event_id, parents=(), before=None, **updates):
        before = before or self.base
        return event(event_id, change('flows', self.key, before, dict(before, **updates)), parents=parents)

    def assert_orders(self, events):
        reference = project(self.initial, events)
        for order in permutations(events):
            self.assertEqual(reference, project(self.initial, order))
        return reference

    def assert_relations(self, rows):
        with self.repo.atomic():
            self.db._replace_rows(rows)
        self.assertIsNone(self.repo.conn.execute('PRAGMA foreign_key_check').fetchone())
        self.assertEqual(rows, self.db._rows())

    def test_disjoint_fields_commute_and_equal_values_agree(self):
        a = self.edit('a', notes='New note')
        b = self.edit('b', name='New title')
        same = self.edit('s', notes='New note')
        rows, conflicts, versions = self.assert_orders([a, b, same])
        self.assertEqual([], conflicts)
        self.assertEqual('New note', rows[self.identity]['row']['notes'])
        self.assertEqual('New title', rows[self.identity]['row']['name'])
        self.assertNotEqual('a', versions[self.identity]['notes'])
        self.assertNotIn('__row__', versions[self.identity])
        self.assert_relations(rows)

    def test_conflicting_choices_name_only_peers_that_proposed_each_value(self):
        a = dict(self.edit('a', notes='one'), origin='Laptop')
        b = dict(self.edit('b', notes='two'), origin='Phone')
        c = dict(self.edit('c', notes='one'), origin='Tablet')
        _, [conflict], _ = self.assert_orders([a, b, c])
        choices = {v['value']: v for v in conflict['choices']}
        self.assertEqual(['Laptop', 'Tablet'], choices['one']['origins'])
        self.assertEqual(['a', 'c'], choices['one']['events'])
        self.assertEqual(['Phone'], choices['two']['origins'])

    def test_prior_is_common_ancestor_not_earliest_seed_or_first_delivery(self):
        root = self.edit('root', notes='common')
        prior = root['changes'][0]['after']
        a = self.edit('a', parents=['root'], before=prior, notes='left')
        b = self.edit('b', parents=['root'], before=prior, notes='right')
        later = self.edit('a2', parents=['a'], before=a['changes'][0]['after'], notes='later')
        rows, [c], versions = self.assert_orders([root, a, b, later])
        self.assertEqual('common', rows[self.identity]['row']['notes'])
        self.assertEqual('common', c['base'])
        self.assertEqual({'later', 'right'}, {choice['value'] for choice in c['choices']})
        resolve = decision('resolve', c, 'later', 'a2', 'b')
        rows, conflicts, updated = project(self.initial, [resolve, b, later, a, root])
        self.assertFalse(conflicts)
        self.assertEqual('later', rows[self.identity]['row']['notes'])
        self.assertNotEqual(versions[self.identity]['notes'], updated[self.identity]['notes'])

    def test_concurrent_resolutions_agree_conflict_and_can_be_resolved_again(self):
        a, b, c = [self.edit(i, name=i) for i in 'abc']
        _, [first], _ = project(self.initial, [a, b])
        left = decision('r1', first, 'a', 'a', 'b')
        right = decision('r2', first, 'b', 'a', 'b')
        same = decision('rs', first, 'a', 'a', 'b')
        self.assertFalse(project(self.initial, [a, b, left, same])[1])
        rows, [again], _ = self.assert_orders([a, b, left, right])
        self.assertEqual('Baseline', again['base'])
        self.assertEqual('Baseline', rows[self.identity]['row']['name'])
        final = decision('rf', again, 'b', 'r1', 'r2')
        merged = [a, b, left, right, final]
        _, [unseen], _ = project(self.initial, merged + [c])
        self.assertEqual({'b', 'c'}, {choice['value'] for choice in unseen['choices']})
        finish = decision('last', unseen, 'c', 'c', 'rf')
        rows, conflicts, _ = project(self.initial, [finish, c, *merged])
        self.assertFalse(conflicts)
        self.assertEqual('c', rows[self.identity]['row']['name'])
        with self.assertRaisesRegex(ValueError, 'observed'):
            project(self.initial, [a, b, dict(left, parents=['a'])])
        with self.assertRaisesRegex(ValueError, 'selected value'):
            project(self.initial, [a, b, dict(left, resolution=dict(left['resolution'], choice='made-up'))])

    def test_quantity_and_unit_stay_together_and_resolving_derives_anchors(self):
        a = self.edit('a', amount='3')
        b = self.edit('b', units='hours')
        rows, [c], _ = self.assert_orders([a, b])
        self.assertEqual({'amount': '2', 'units': 'USD'}, c['base'])
        self.assertEqual(('2', 'USD'), tuple(rows[self.identity]['row'][key] for key in ('amount', 'units')))
        alive = dict(amount='2', units='hours')
        rows, conflicts, versions = project(self.initial, [a, b, decision('r', c, alive, 'a', 'b')])
        self.assertFalse(conflicts)
        self.assertEqual('r', versions[self.identity]['amount'])
        self.assertEqual('r', versions[self.identity]['units'])
        self.assert_relations(rows)
        self.assertIsNotNone(self.repo.get_account('A', 'hours'))

    def test_independent_endpoint_and_unit_create_missing_pair_then_accept_real_balance(self):
        a = self.edit('a', from_key='c')
        b = self.edit('b', units='kg')
        rows, conflicts, _ = self.assert_orders([a, b])
        self.assertFalse(conflicts)
        self.assert_relations(rows)
        key = ['c', 'kg']
        derived = rows[_key('account_balances', key)]['row']
        balance = event('balance', change('account_balances', key, derived, dict(derived, opening_amount='42')), parents=['a', 'b'])
        rows, conflicts, _ = project(self.initial, [balance, b, a])
        self.assertFalse(conflicts)
        self.assertEqual('42', rows[_key('account_balances', key)]['row']['opening_amount'])
        self.assert_relations(rows)

    def test_deleted_flow_and_tag_only_edit_can_keep_tags_or_remove_graph(self):
        key = self.key + ['first']
        tag = self.initial[_key('flow_tags', key)]['row']
        another = self.key + ['second']
        new_tag = dict(flow_id=self.key[0], tag_key='second', position=1)
        delete = event('d', change('flow_tags', key, tag, None), change('flows', self.key, self.base, None))
        add = event('a', change('flow_tags', another, None, new_tag))
        rows, [c], _ = self.assert_orders([delete, add])
        self.assertEqual(self.initial, rows)
        keep = next(option['value'] for option in c['choices'] if option['value'] is not None)
        rows, conflicts, _ = project(self.initial, [decision('k', c, keep, 'd', 'a'), delete, add])
        self.assertFalse(conflicts)
        self.assert_relations(rows)
        self.assertEqual(['First', 'second'], self.repo.get_flow(self.key[0])['tags'])
        rows, conflicts, _ = project(self.initial, [decision('x', c, None, 'd', 'a'), delete, add])
        self.assertFalse(conflicts)
        self.assert_relations(rows)
        self.assertFalse(any(record['table'] == 'flow_tags' for record in rows.values()))

    def test_ordered_tag_list_conflict_and_unrelated_name_merge(self):
        def add_tag(name, tag):
            key = self.key + [tag]
            return event(name, change('flow_tags', key, None, dict(flow_id=self.key[0], tag_key=tag, position=1)))
        a, b = add_tag('a', 'hot'), add_tag('b', 'cold')
        rename = self.edit('rename', name='other')
        rows, [c], versions = self.assert_orders([a, b, rename])
        self.assertEqual(['tags'], c['fields'])
        self.assertEqual({'first': 0}, c['base'])
        self.assertEqual('other', rows[self.identity]['row']['name'])
        self.assertIn(_key('flow_tags', self.key + ['hot']), versions)
        self.assert_relations(rows)

    def test_delete_or_keep_preserves_all_compatible_living_edits(self):
        left = self.edit('left', name='New name')
        right = self.edit('right', notes='New note')
        delete = event('gone', change('flows', self.key, self.base, None))
        rows, [c], _ = self.assert_orders([left, right, delete])
        self.assertEqual(self.base, rows[self.identity]['row'])
        self.assertEqual(2, len(c['choices']))
        keep = next(v for v in c['choices'] if v['value'] is not None)
        self.assertEqual(['left', 'right'], keep['events'])
        rows, conflicts, _ = project(self.initial, [decision('restore', c, keep['value'],
                                              'left', 'right', 'gone'), delete, left, right])
        self.assertFalse(conflicts)
        self.assertEqual(('New name', 'New note'), tuple(rows[self.identity]['row'][k] for k in ('name', 'notes')))
        self.assert_relations(rows)

    def test_independently_created_same_row_retains_shared_prior_value(self):
        fid = 'ebb8e61e-6798-4417-a8ae-f76969a45204'
        key = [fid]
        original = dict(self.base, flow_id=fid)
        born = change('flows', key, None, original)
        left, right = event('l', born), event('r', born)
        l1 = event('l1', change('flows', key, original, dict(original, units='kg')), parents=['l'])
        l2 = event('l2', change('flows', key, l1['changes'][0]['after'], dict(original, units='ton')), parents=['l1'])
        r1 = event('r1', change('flows', key, original, dict(original, amount='7')), parents=['r'])
        rows, [c], _ = project(self.initial, [l2, r1, left, l1, right])
        self.assertEqual({'amount': '2', 'units': 'USD'}, c['base'])
        self.assertEqual(original, rows[_key('flows', key)]['row'])
        self.assert_relations(rows)

    def test_absent_reference_anchor_does_not_overwrite_explicit_concurrent_balance(self):
        key = ['a', 'kg']
        default = dict(account_key='a', units='kg', opening_amount=None, opening_at=DEFAULT_ANCHOR)
        real = dict(default, opening_amount='15')
        a = event('a', change('account_balances', key, None, default))
        b = event('b', change('account_balances', key, None, real))
        rows, conflicts, _ = self.assert_orders([a, b])
        self.assertFalse(conflicts)
        self.assertEqual(real, rows[_key('account_balances', key)]['row'])

    def test_retains_deleted_support_used_by_another_live_flow(self):
        key = ['a', 'USD']
        identity = _key('account_balances', key)
        drop = event('drop', change('account_balances', key, self.initial[identity]['row'], None),
                     change('flows', self.key, self.base, None))
        fid = 'ebb8e61e-6798-4417-a8ae-f76969a45204'
        extra = event('add', change('flows', [fid], None, dict(self.base, flow_id=fid)))
        rows, conflicts, _ = self.assert_orders([drop, extra])
        self.assertFalse(conflicts)
        self.assertEqual(rows[identity], self.initial[identity])
        self.assert_relations(rows)
        self.assertIsNotNone(self.repo.get_flow(fid))
        self.assertIsNone(self.repo.get_flow(self.key[0]))

    def test_rejected_plain_overwrite_does_not_block_disjoint_edit(self):
        a, b = self.edit('a', name='left'), self.edit('b', name='right')
        note = self.edit('n', parents=['a', 'b'], notes='independent')
        rows, [c], _ = self.assert_orders([a, b, note])
        self.assertEqual('independent', rows[self.identity]['row']['notes'])
        self.assertEqual(['name'], c['fields'])
        with self.assertRaisesRegex(ValueError, 'existing peer conflict'):
            project(self.initial, [a, b, self.edit('x', parents=['a', 'b'], name='overwrite')])

    def test_duplicate_events_are_idempotent_but_changed_payload_rejected(self):
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

    def test_four_replicas_delete_recreate_and_resolve_without_partial_rows(self):
        # IDs may reappear after undo. Repeated competing decisions restoring
        # a row should never build a quantity or tag list out of a tombstone.
        rng = Random(19)
        for run in range(12):
            seed = self.initial if run % 2 else {
                k: v for k, v in self.initial.items() if v['table'] not in ('flows', 'flow_tags')}
            devices = [{} for _ in range(4)]
            for step in range(35):
                own = rng.choice(devices)
                if rng.random() < .45:
                    own.update(rng.choice(devices))
                rows, conflicts, _ = project(seed, own)
                parents = set(own) - {p for e in own.values() for p in e['parents']}
                name = str(step)
                if conflicts:
                    c = rng.choice(conflicts)
                    self.assertGreaterEqual(len(c['choices']), 2)
                    e = decision(name, c, rng.choice(c['choices'])['value'], *sorted(parents))
                else:
                    before = rows.get(self.identity, {}).get('row')
                    if before is not None and rng.random() < .25:
                        after = None
                    else:
                        after = dict(before or self.base)
                        field = rng.choice(['amount', 'units', 'notes', 'name'])
                        after[field] = str(rng.randrange(7))
                    e = event(name, change('flows', self.key, before, after), parents=sorted(parents))
                own[name] = e
            merged = {i: e for device in devices for i, e in device.items()}
            result = project(seed, merged)
            self.assertEqual(result, project(seed, reversed(list(merged.values()))))
            self.assert_relations(result[0])

    def test_sqlite_replay_of_a_stale_conflict_never_reverts_a_newer_resolution(self):
        from flowledger import peer_store as store

        store.share(self.repo, 'Water')
        peers = []
        for i in range(3):
            device = Repository(Path(self.repo.path).with_name('peer-%d.flow' % i))
            self.addCleanup(device.close)
            store.join(device, store.seed(self.repo))
            peers.append(device)
        # Three devices start with the same visible quantity.
        for device, amount in zip(peers, ('3', '4', '5')):
            device.save_flow(dict(self.flow, amount=amount))
        branches = [store.missing(device, []) for device in peers]
        store.receive(self.repo, branches[1] + branches[0])
        [first] = store.conflicts(self.repo)
        choose = next(c for c in first['choices'] if c['value']['amount'] == '4')
        store.resolve(self.repo, first['id'], choose['id'])
        self.assertEqual('4', self.repo.get_flow(self.key[0])['amount'])

        store.receive(self.repo, branches[2])
        [second] = store.conflicts(self.repo)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual('2', self.repo.get_flow(self.key[0])['amount'])
        self.assertEqual({'4', '5'}, {v['value']['amount'] for v in second['choices']})
        before_ids = store.inventory(self.repo)
        with self.assertRaisesRegex(ValueError, 'no longer'):
            store.resolve(self.repo, first['id'], choose['id'])
        self.assertEqual(before_ids, store.inventory(self.repo))
        self.assertEqual([second], store.conflicts(self.repo))
        final = next(v for v in second['choices'] if v['value']['amount'] == '5')
        store.resolve(self.repo, second['id'], final['id'])
        # A delayed device can relay every old intent and the new decisions.
        # Repeated copies of the two losing changes cannot repaint the ledger.
        old = branches[1] + branches[0]
        self.assertFalse(store.receive(self.repo, old))
        for device in peers:
            store.receive(device, store.missing(self.repo, store.inventory(device)))
            self.assertFalse(store.receive(device, old))
            self.assertEqual(self.repo.get_flow(self.key[0]), device.get_flow(self.key[0]))
            self.assertEqual(self.repo.sync.field_tokens('flows', self.key), device.sync.field_tokens('flows', self.key))
            self.assertFalse(store.conflicts(device))
        self.assertEqual('5', self.repo.get_flow(self.key[0])['amount'])

    def test_sqlite_field_revisions_report_aba_and_cross_unit_changes_only_where_affected(self):
        from flowledger import peer_store as store

        store.share(self.repo, 'Water')
        device = Repository(Path(self.repo.path).with_name('device.flow'))
        self.addCleanup(device.close)
        store.join(device, store.seed(self.repo))
        stale = self.repo.sync.field_tokens('flows', self.key)
        device.save_flow(dict(self.flow, units='kg', name='other'))
        device.save_flow(dict(self.flow, units='kg'))
        events = store.missing(device, [])
        with self.assertRaisesRegex(ValueError, 'Missing parent'):
            store.receive(self.repo, events[1:])
        self.assertEqual([], store.inventory(self.repo))
        self.assertEqual(self.flow, self.repo.get_flow(self.key[0]))
        store.receive(self.repo, list(reversed(events)))
        fresh = self.repo.sync.field_tokens('flows', self.key)
        for field in ('name', 'units', 'amount'):
            self.assertNotEqual(stale[field], fresh[field])
        for field in ('notes', 'tags', '__row__'):
            self.assertEqual(stale[field], fresh[field])
        current = self.repo.get_flow(self.key[0])
        self.assertEqual(self.flow['name'], current['name'])
        self.assertEqual(self.flow['amount'], current['amount'])
        self.assertEqual('kg', current['units'])
