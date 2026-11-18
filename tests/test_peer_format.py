"""Opening existing files preserves records and only enables current sharing."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from flowledger.core import Repository
from flowledger import peer_store as store
from flowledger.sync import _json


class PeerFormatTests(unittest.TestCase):
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

    def unsupported_file(self, *, seed_protocol=2):
        path = self.root / ('unsupported-' + str(seed_protocol) + '.sqlite3')
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='A',
                to_account='B', amount='1', units='Wh', name='Original'))
            store.share(repo, 'Old test')
            record = repo.save_flow(dict(record, notes='Keep the stored value'))
            original = dict(store.seed(repo), protocol=seed_protocol)
            event, = store.missing(repo, [])
            del event['stamp']
        with closing(sqlite3.connect(path)) as raw, raw:
            for table, value in [('peer_seed', original), ('peer_events', event)]:
                name = 'peer_immutable_' + table + '_update'
                sql = raw.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
                raw.execute('DROP TRIGGER ' + name)
                raw.execute('UPDATE ' + table + ' SET payload=?', (_json(value),))
                raw.execute(sql)
        return path, record, original, event

    def test_unsupported_files_remain_readable_but_cannot_edit_share_or_relay(self):
        for protocol in (2, 3):
            with self.subTest(protocol=protocol):
                path, record, original, event = self.unsupported_file(seed_protocol=protocol)
                with closing(Repository(path)) as repo:
                    self.assertEqual(repo.get_flow(record['flow_id']), record)
                    audit = repo.history()
                    actions = [lambda: repo.save_flow(dict(record, name='Must not save')),
                               lambda: store.share(repo, 'Must not advertise'),
                               lambda: store.seed(repo), lambda: store.inventory(repo),
                               lambda: store.missing(repo, []), lambda: store.receive(repo, [])]
                    for action in actions:
                        with self.assertRaisesRegex(ValueError, 'unsupported sharing history'):
                            action()
                    self.assertEqual(repo.get_flow(record['flow_id']), record)
                    self.assertEqual(repo.history(), audit)
                    self.assertEqual(repo.conn.execute('SELECT payload FROM peer_seed').fetchone()[0], _json(original))
                    self.assertEqual(repo.conn.execute('SELECT payload FROM peer_events').fetchone()[0], _json(event))
                    self.assertFalse(repo.conn.in_transaction)

    def test_external_commit_rechecks_previously_valid_format(self):
        path = self.root / 'external.sqlite3'
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='A',
                to_account='B', amount='1', units='Wh'))
            shared = store.share(repo, 'Current')
            store.require_current(repo)
            # Simulate an older client appending an unstamped event. The
            # connection is still open, so its cached format must be invalidated.
            with closing(sqlite3.connect(path)) as other, other:
                other.create_function('flow_peer_event_write_allowed', 1, lambda payload: 1)
                event = dict(id=str(uuid.uuid4()), dataset=shared, parents=[], changes=[])
                other.execute('INSERT INTO peer_events(id,payload) VALUES(?,?)', (event['id'], _json(event)))
            with self.assertRaisesRegex(ValueError, 'unsupported sharing history'):
                repo.save_flow(dict(record, name='Must not save'))
            self.assertEqual(repo.get_flow(record['flow_id']), record)

    def test_local_file_can_be_shared_without_changing_records_or_ids(self):
        with closing(Repository(self.root / 'ordinary.sqlite3')) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='Water',
                to_account='Camp', amount='2.5', units='L', tags=['camp']))
            repo.save_account('Water', units='L', opening_amount='10', opening_at='2026-11-01')
            repo.save_map_positions({'Water': (12.5, 37.5)})
            audit = repo.history()
            positions = repo.load_map_positions()
            accounts = repo.list_accounts()
            identifier = store.share(repo, 'Real file')
            with closing(Repository(self.root / 'new-join.sqlite3')) as other:
                self.assertEqual(store.join(other, store.seed(repo)), identifier)
                self.assertEqual(other.get_flow(record['flow_id']), record)
                self.assertEqual(other.list_accounts(), accounts)
                self.assertEqual(other.load_map_positions(), positions)
            self.assertEqual(repo.history(), audit)
            self.assertEqual(repo.get_flow(record['flow_id']), record)
            self.assertEqual(store.inventory(repo), [])


if __name__ == '__main__':
    unittest.main()
