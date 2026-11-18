"""Replay boundaries and resource values across independent local databases."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger import peer_store as store


class SyncReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repositories = []
        self.first = self.open('first')
        self.record = self.first.save_flow(dict(date='2026-11-01', from_account='A',
            to_account='B', amount='1', units='L', name='Original'))
        store.share(self.first, 'Water')
        self.a, self.b = self.join('a'), self.join('b')

    def tearDown(self):
        for repository in self.repositories:
            repository.close()

    def open(self, name):
        repo = Repository(Path(self.temporary.name) / (name + '.sqlite3'))
        self.repositories.append(repo)
        return repo

    def join(self, name):
        repo = self.open(name)
        store.join(repo, store.seed(self.first))
        return repo

    def exchange(self, repo):
        return store.receive(self.first, store.missing(repo, store.inventory(self.first)))

    def pull(self, repo):
        return store.receive(repo, store.missing(self.first, store.inventory(repo)))

    def edit_at(self, repo, milliseconds, **changes):
        with patch('flowledger.peer_store.time.time_ns', return_value=milliseconds * 1_000_000):
            return repo.save_flow(dict(self.record, **changes))

    def send(self, source, target):
        return store.receive(target, store.missing(source, store.inventory(target)))

    def assert_converged(self, peers, expected):
        for peer in peers:
            self.send(self.first, peer)
            self.assertEqual(expected, peer.get_flow(self.record['flow_id']))
            self.assertEqual(set(store.inventory(self.first)), set(store.inventory(peer)))

    def test_large_offline_chain_and_stale_peer_replay_in_pages(self):
        count = store.BATCH_EVENTS + 7
        for value in range(count):
            self.a.save_flow(dict(self.record, amount=str(value)))
        self.assertEqual(store.BATCH_EVENTS, len(store.missing(self.a, [])))
        self.exchange(self.a)
        self.assertEqual(7, len(store.missing(self.a, store.inventory(self.first))))
        self.exchange(self.a)
        self.assertEqual([], store.missing(self.a, store.inventory(self.first)))
        self.assertTrue(self.pull(self.b))
        self.assertEqual(store.BATCH_EVENTS, len(store.inventory(self.b)))
        self.assertTrue(self.pull(self.b))
        self.assertFalse(self.pull(self.b))
        self.assertEqual(str(count - 1), self.b.get_flow(self.record['flow_id'])['amount'])

    def test_four_peers_delayed_older_edit_keeps_its_creation_time_through_relay(self):
        c = self.join('c')
        start = 1_800_000_000_000
        self.edit_at(self.a, start, name='Older offline name')
        self.edit_at(c, start + 1000, notes='Independent notes')
        self.edit_at(self.b, start + 2000, name='Latest name')
        older, = store.missing(self.a, [])
        newer, = store.missing(self.b, [])
        self.assertEqual([start, 0], older['stamp'])
        self.assertEqual([start + 2000, 0], newer['stamp'])
        self.send(self.b, self.first)

        # Receipt and relay much later must not turn an old edit into a new one.
        with patch('flowledger.peer_store.time.time_ns', return_value=(start + 9000) * 1_000_000):
            self.send(self.a, c)
            self.send(c, self.first)
        relayed = {event['id']: event for event in store.missing(self.first, [])}
        self.assertEqual(older, relayed[older['id']])
        self.assertEqual(newer, relayed[newer['id']])
        expected = dict(self.record, name='Latest name', notes='Independent notes')
        self.assertEqual(expected, self.first.get_flow(self.record['flow_id']))
        self.assert_converged((self.a, self.b, c), expected)
        changes = self.first.conn.total_changes
        for peer in (self.a, self.b, c):
            self.assertFalse(self.send(peer, self.first))
        self.assertEqual(changes, self.first.conn.total_changes)

    def test_four_peers_delayed_newer_field_wins_after_a_later_unrelated_edit(self):
        c = self.join('c')
        start = 1_800_000_000_000
        self.edit_at(self.a, start, name='Earlier name')
        self.edit_at(self.b, start + 2000, name='Delayed newer name')
        self.edit_at(c, start + 3000, notes='Still newer notes')
        self.send(self.a, self.first)
        self.send(c, self.first)
        self.assertEqual('Earlier name', self.first.get_flow(self.record['flow_id'])['name'])

        self.send(self.b, c)
        self.send(c, self.first)
        expected = dict(self.record, name='Delayed newer name', notes='Still newer notes')
        self.assertEqual(expected, self.first.get_flow(self.record['flow_id']))
        self.assert_converged((self.a, self.b, c), expected)

    def test_four_peers_delete_ignores_older_edit_and_newer_edit_restores_record(self):
        c = self.join('c')
        start = 1_800_000_000_000
        self.edit_at(self.b, start, name='Older offline name', notes='Earlier saved notes')
        with patch('flowledger.peer_store.time.time_ns', return_value=(start + 1000) * 1_000_000):
            self.a.delete_flow(self.record['flow_id'])
        self.edit_at(c, start + 2000, name='Newer offline name', amount='3', units='kg')
        self.send(self.a, self.first)
        self.assertIsNone(self.first.get_flow(self.record['flow_id']))
        self.send(self.b, self.first)
        self.assertIsNone(self.first.get_flow(self.record['flow_id']))

        self.send(c, self.first)
        expected = dict(self.record, name='Newer offline name', amount='3', units='kg',
                        notes='Earlier saved notes')
        self.assertEqual(expected, self.first.get_flow(self.record['flow_id']))
        self.assert_converged((self.a, self.b, c), expected)

    def test_idle_pull_makes_no_writes_or_insertion_order_changes(self):
        self.a.save_flow(dict(self.record, flow_id=None))
        self.exchange(self.a)
        rows_before = [tuple(row) for row in self.a.conn.execute('SELECT rowid,flow_id FROM flows ORDER BY rowid')]
        changed_before = self.a.conn.total_changes
        self.exchange(self.a)
        self.assertEqual(changed_before, self.a.conn.total_changes)
        self.pull(self.b)
        self.b.save_flow(dict(self.record, notes='Remote'))
        self.exchange(self.b)
        self.pull(self.a)
        self.assertEqual(rows_before, [tuple(row) for row in self.a.conn.execute('SELECT rowid,flow_id FROM flows ORDER BY rowid')])

    def test_zero_negative_and_self_flows_preserve_exact_resource_values(self):
        records = []
        for amount, destination in [('0', 'B'), ('-0.125', 'B'), ('123456789123456789.000001', 'A')]:
            records.append(self.a.save_flow(dict(self.record, flow_id=None, amount=amount, to_account=destination, units='kg')))
        self.exchange(self.a)
        self.pull(self.b)
        for record in records:
            self.assertEqual(record, self.b.get_flow(record['flow_id']))
            self.assertEqual(record, self.first.get_flow(record['flow_id']))


if __name__ == '__main__':
    unittest.main()
