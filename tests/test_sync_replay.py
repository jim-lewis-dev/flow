"""Replay boundaries and resource values across independent local databases."""
from pathlib import Path
import tempfile
import unittest

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
        self.assertFalse(store.conflicts(self.b))

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
