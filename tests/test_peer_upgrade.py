"""Opening existing files preserves records and only enables current sharing."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from flowledger.core import Repository
from flowledger import peer_store as store


class PeerUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def old_file(self):
        path = self.root / 'existing.sqlite3'
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='Tank',
                to_account='Camp', amount='-0.125', units='L', notes='Local edit'))
            audit = repo.history()
            repo.conn.executescript('''
                CREATE TABLE sync_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE sync_outbox(sequence INTEGER PRIMARY KEY,payload TEXT);
                INSERT INTO sync_outbox VALUES(1,'preserved local intent');
                CREATE TRIGGER sync_guard_flows_update BEFORE UPDATE ON flows
                WHEN flow_sync_write_allowed()<>1
                BEGIN SELECT RAISE(ABORT,'Managed write required'); END;
            ''')
            repo.conn.executemany('INSERT INTO sync_meta VALUES(?,?)', [
                (key, json.dumps(value)) for key, value in {
                    'dataset': 'old-file', 'endpoint': 'https://old.invalid',
                    'token': 'old-token', 'certificate': 'old-cert',
                    'server': True, 'cursor': 19
                }.items()])
        return path, record, audit

    def test_old_settings_retire_without_changing_records_or_journals(self):
        path, record, audit = self.old_file()
        with closing(Repository(path)) as repo:
            self.assertEqual(repo.get_flow(record['flow_id']), record)
            self.assertEqual(repo.history(), audit)
            self.assertFalse(repo.sync.tracking)
            self.assertEqual(list(repo.conn.execute('SELECT * FROM sync_meta')), [])
            self.assertEqual(repo.conn.execute('SELECT payload FROM sync_outbox').fetchone()[0],
                             'preserved local intent')
            saved = repo.save_flow(dict(record, notes='Still editable'))
            identifier = store.share(repo, 'Water')
            self.assertTrue(repo.sync.tracking)
            with closing(Repository(self.root / 'joined.sqlite3')) as other:
                self.assertEqual(store.join(other, store.seed(repo)), identifier)
                self.assertEqual(other.get_flow(record['flow_id']), saved)
                self.assertFalse(store.inventory(other))

    def test_current_shared_history_identity_and_write_guards_survive_reopen(self):
        path = self.root / 'current.sqlite3'
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='A',
                to_account='B', amount='1', units='Wh'))
            identifier = store.share(repo, 'Power')
            original = store.seed(repo)
            repo.save_flow(dict(record, amount='2'))
            events = store.missing(repo, [])
            versions = repo.sync._versions()
        with closing(Repository(path)) as reopened:
            self.assertEqual(store.dataset(reopened), identifier)
            self.assertEqual(store.seed(reopened), original)
            self.assertEqual(store.missing(reopened, []), events)
            self.assertEqual(reopened.sync._versions(), versions)
            self.assertFalse(reopened.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sync_outbox'").fetchone())
        with closing(sqlite3.connect(path)) as external:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'flow_peer_write_allowed'):
                external.execute("UPDATE flows SET amount='9'")

    def test_restore_old_local_snapshot_rearms_capture_and_preserves_source(self):
        old, record, _ = self.old_file()
        original_bytes = old.read_bytes()
        with closing(Repository(self.root / 'local.sqlite3')) as repo:
            repo.restore(old)
            self.assertEqual(old.read_bytes(), original_bytes)
            self.assertEqual(repo.get_flow(record['flow_id']), record)
            self.assertFalse(repo.sync.tracking)
            repo.save_flow(dict(record, name='Restored'))
            store.share(repo, 'Water')
            repo.save_flow(dict(record, name='Shared'))
            self.assertEqual(len(store.inventory(repo)), 1)

    def test_retirement_failure_rolls_back_settings_and_guards(self):
        path, _, _ = self.old_file()
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TRIGGER reject_setting_delete BEFORE DELETE ON sync_meta "
                         "BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'blocked'):
            Repository(path)
        with closing(Repository(self.root / 'unchanged.sqlite3')) as local:
            saved = local.save_flow(dict(date='2026-11-01', from_account='Own',
                to_account='File', amount='7', units='kg'))
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'blocked'):
                local.restore(path)
            self.assertEqual(local.list_flows(), [saved])
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sync_meta').fetchone()[0], 6)
            self.assertTrue(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='sync_guard_flows_update'").fetchone())


if __name__ == '__main__':
    unittest.main()
