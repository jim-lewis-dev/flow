"""Prove that cutover never publishes two authorities or drops pending work."""
from contextlib import closing
import fcntl
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flowledger import peer_store
from flowledger.core import Repository
from flowledger.peer_migrate import migrate, proof
from flowledger.sync import SyncState
from flowledger.sync_server import initialize, load_settings


class MigrationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source, self.service = self.root / 'local.sqlite3', self.root / 'service'
        self.dest = self.root / 'peer.sqlite3'
        repo = Repository(self.source)
        try:
            self.row = repo.save_flow(dict(date='2026-11-01', from_account='Battery',
                                      to_account='Lamp', amount='20', units='Wh', notes='seed'))
        finally:
            repo.close()
        self.settings = initialize(self.source, self.service, 'https://127.0.0.1:8765')
        self.server = self.service / self.settings['database']
        with closing(Repository(self.server)) as server, closing(Repository(self.source)) as local:
            SyncState(local).configure(dataset=self.settings['dataset'],seed=server.sync.export_seed())
        self.receipt = proof(self.source)

    def convert(self, *receipts):
        return migrate(self.service, self.dest, 'energy', receipts, all_closed=True)

    def test_cutover_verifies_sources_and_preserves_records(self):
        old = self.server.read_bytes()
        with closing(sqlite3.connect(self.server)) as db:
            audit = db.execute('SELECT * FROM audit ORDER BY audit_id').fetchall()
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.convert(self.receipt, self.receipt + 'x')
        with (self.service / 'service.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, 'Stop'):
                self.convert(self.receipt)
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.convert(self.receipt), self.dest)
        self.assertEqual(self.server.read_bytes(),old)
        with self.assertRaisesRegex(ValueError,'retired'):
            load_settings(self.service)
        with closing(Repository(self.dest)) as repo:
            self.assertEqual(peer_store.dataset(repo), self.settings['dataset'])
            self.assertEqual(self.row, repo.get_flow(self.row['flow_id']))
            self.assertEqual(audit,[tuple(r) for r in repo.conn.execute('SELECT * FROM audit ORDER BY audit_id')])
            self.assertFalse(repo.sync.enabled)
        with self.assertRaisesRegex(ValueError, 'peer dataset'):
            initialize(self.dest,self.root / 'wrong','https://127.0.0.1:8765')

    def test_pending_clients_and_publication_failure(self):
        with closing(Repository(self.source)) as local:
            local.save_flow(dict(self.row, amount='25'))
            with self.assertRaisesRegex(ValueError, 'Pending'):
                proof(self.source)
            with closing(Repository(self.server)) as server:
                local.sync.accept_response(server.sync.server_exchange(local.sync.prepare_request()))
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.convert(self.receipt)
        current = proof(self.source)
        with patch('flowledger.peer_migrate.os.link', side_effect=OSError('destination unavailable')):
            with self.assertRaisesRegex(OSError,'destination unavailable'):
                self.convert(current)
        self.assertTrue((self.service / 'coordinator.json').exists())
        self.assertFalse(self.dest.exists())
        self.dest.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'new file'):
            self.convert(current)
        self.assertEqual(self.dest.read_bytes(),b'keep')
