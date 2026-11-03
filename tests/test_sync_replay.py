"""Replay boundaries and resource values across independent local databases."""
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository
from flowledger.sync import SyncState, MAX_BATCH_OPERATIONS


class SyncReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repositories = []
        self.server = self.open('server')
        self.record = self.server.save_flow(dict(date='2026-11-01', from_account='A',
            to_account='B', amount='1', units='L', name='Original'))
        self.authority = SyncState(self.server)
        self.authority.configure(dataset='water', server=True)
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
        SyncState(repo).configure(dataset='water', seed=self.authority.export_seed())
        return repo

    def exchange(self, repo):
        response = self.authority.server_exchange(repo.sync.prepare_request())
        repo.sync.accept_response(response)
        return response

    def test_large_offline_chain_and_stale_peer_replay_in_pages(self):
        count = MAX_BATCH_OPERATIONS + 7
        for value in range(count):
            self.a.save_flow(dict(self.record, amount=str(value)))
        self.assertEqual(MAX_BATCH_OPERATIONS, len(self.a.sync.prepare_request()['operations']))
        self.exchange(self.a)
        self.assertEqual(7, self.a.sync.status()['pending'])
        self.exchange(self.a)
        self.assertEqual(0, self.a.sync.status()['pending'])
        response = self.exchange(self.b)
        self.assertTrue(response['more'])
        self.assertEqual(MAX_BATCH_OPERATIONS, self.b.sync.status()['cursor'])
        response = self.exchange(self.b)
        self.assertFalse(response['more'])
        self.assertEqual(str(count - 1), self.b.get_flow(self.record['flow_id'])['amount'])
        self.assertFalse(self.b.sync.status()['conflicts'])

    def test_idle_pull_makes_no_writes_or_insertion_order_changes(self):
        self.a.save_flow(dict(self.record, flow_id=None))
        self.exchange(self.a)
        rows_before = [tuple(row) for row in self.a.conn.execute('SELECT rowid,flow_id FROM flows ORDER BY rowid')]
        changed_before = self.a.conn.total_changes
        self.exchange(self.a)
        self.assertEqual(changed_before, self.a.conn.total_changes)
        self.b.save_flow(dict(self.record, notes='Remote'))
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual(rows_before, [tuple(row) for row in self.a.conn.execute('SELECT rowid,flow_id FROM flows ORDER BY rowid')])

    def test_zero_negative_and_self_flows_preserve_exact_resource_values(self):
        records = []
        for amount, destination in [('0', 'B'), ('-0.125', 'B'), ('123456789123456789.000001', 'A')]:
            records.append(self.a.save_flow(dict(self.record, flow_id=None, amount=amount, to_account=destination, units='kg')))
        self.exchange(self.a)
        self.exchange(self.b)
        for record in records:
            self.assertEqual(record, self.b.get_flow(record['flow_id']))
            self.assertEqual(record, self.server.get_flow(record['flow_id']))


if __name__ == '__main__':
    unittest.main()
