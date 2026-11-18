"""Opening existing files preserves records and only enables current sharing."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository
from flowledger import peer_store as store
from flowledger.peer_legacy import project as legacy_project
from flowledger.sync import TABLES, _json, _key


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

    def old_shared_file(self, *, resolved=True):
        """Create the exact protocol-2 seed and immutable event representation."""
        path = self.root / 'old-shared.sqlite3'
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-01', from_account='A',
                to_account='B', amount='1', units='Wh', name='Original'))
            identifier = store.share(repo, 'Power')
            original = dict(store.seed(repo), protocol=2)
            before = original['snapshot'][_key('flows', [record['flow_id']])]['row']
            def event(index, name):
                return dict(id=str(uuid.UUID(int=index)), dataset=identifier, parents=[],
                            changes=[dict(table='flows', key=[record['flow_id']],
                                          before=before, after=dict(before, name=name))])
            events = [event(1, 'Left'), event(2, 'Right')]
            if resolved:
                _, conflicts, _ = legacy_project(original['snapshot'], events)
                conflict, = conflicts
                choice = next(choice for choice in conflict['choices'] if choice['value'] == 'Left')
                events.append(dict(id=str(uuid.UUID(int=3)), dataset=identifier,
                    parents=[e['id'] for e in events], changes=[],
                    resolution=dict(conflict=conflict['id'], choice=choice['id'])))
                current, _, _ = legacy_project(original['snapshot'], events)
                prior = current[_key('flows', [record['flow_id']])]['row']
                events.append(dict(id=str(uuid.UUID(int=4)), dataset=identifier,
                    parents=[events[-1]['id']], changes=[dict(table='flows', key=[record['flow_id']],
                    before=prior, after=dict(prior, notes='Queued offline edit'))]))
            rows, conflicts, versions = legacy_project(original['snapshot'], events)
            with repo.sync.muted(), repo.atomic():
                repo.conn.execute('DROP TRIGGER peer_guard_events_insert')
                repo.conn.executemany('INSERT INTO peer_events(id,payload) VALUES(?,?)',
                                     [(e['id'], _json(e)) for e in events])
                heads = {e['id'] for e in events} - {p for e in events for p in e['parents']}
                repo.conn.executemany('INSERT INTO peer_heads VALUES(?)', [(key,) for key in heads])
                repo.sync._replace_rows(rows)
                repo.sync._write_versions(versions)
                repo.sync._set('peer_conflicts', conflicts)
                immutable = repo.conn.execute("SELECT sql FROM sqlite_master WHERE name='peer_immutable_peer_seed_update'").fetchone()[0]
                repo.conn.execute('DROP TRIGGER peer_immutable_peer_seed_update')
                repo.conn.execute('UPDATE peer_seed SET payload=?', (_json(original),))
                repo.conn.execute(immutable)
                repo.conn.execute("DELETE FROM sync_meta WHERE key IN ('peer_protocol','peer_clock')")
                for table in TABLES:
                    for operation in ('insert', 'update', 'delete'):
                        name = f'peer_guard_{table}_{operation}'
                        sql = repo.conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
                        repo.conn.execute(f'DROP TRIGGER {name}')
                        repo.conn.execute(sql.replace('<>3', '<>1'))
            expected = repo.get_flow(record['flow_id'])
            audit = repo.history()
        return path, expected, original, events, audit

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

    def test_protocol_two_upgrade_preserves_history_choice_identity_and_offline_edits(self):
        path, record, original, events, audit = self.old_shared_file()
        with closing(Repository(path)) as repo:
            self.assertEqual(repo.get_flow(record['flow_id']), record)
            self.assertEqual(record['name'], 'Left')
            self.assertEqual(record['notes'], 'Queued offline edit')
            self.assertEqual(repo.history(), audit)
            self.assertEqual(store.dataset(repo), original['dataset'])
            self.assertEqual(store.genesis(repo), store._digest(original['snapshot'], original['order']))
            self.assertEqual(repo.conn.execute('SELECT payload FROM peer_seed').fetchone()[0], _json(original))
            self.assertEqual(store.seed(repo), dict(original, protocol=3))
            self.assertEqual(store.missing(repo, []), events)
            self.assertEqual(store._meta(repo, 'peer_clock'), [0, 3])
            self.assertFalse(repo.conn.execute("SELECT 1 FROM sync_meta WHERE key='peer_conflicts'").fetchone())
            with closing(Repository(self.root / 'upgraded-join.sqlite3')) as other:
                store.join(other, store.seed(repo))
                store.receive(other, events[::-1])
                self.assertEqual(other.get_flow(record['flow_id']), record)
                self.assertEqual({e['id']: e for e in store.missing(other, [])},
                                 {e['id']: e for e in events})
                with patch('flowledger.peer_store.time.time_ns', return_value=1_000_000):
                    other.save_flow(dict(record, amount='2'))
                newest = store.missing(other, [])[-1]
                self.assertEqual(newest['stamp'], [1, 0])
                self.assertEqual(newest['parents'], [events[-1]['id']])
                store.receive(repo, [newest])
            self.assertEqual(repo.get_flow(record['flow_id'])['amount'], '2')
        with closing(Repository(path)) as reopened:
            self.assertEqual(store.missing(reopened, [])[:len(events)], events)
            self.assertEqual(reopened.conn.execute('SELECT payload FROM peer_seed').fetchone()[0], _json(original))

    def test_protocol_two_unresolved_edits_choose_deterministically_on_upgrade(self):
        path, record, _, events, _ = self.old_shared_file(resolved=False)
        self.assertEqual(record['name'], 'Original')
        with closing(Repository(path)) as repo:
            self.assertEqual(repo.get_flow(record['flow_id'])['name'], 'Right')
            self.assertEqual(store.missing(repo, []), events)
            self.assertEqual(store._meta(repo, 'peer_clock'), [0, 1])
            repo.save_flow(dict(repo.get_flow(record['flow_id']), name='Followup'))
            self.assertEqual(repo.get_flow(record['flow_id'])['name'], 'Followup')

    def test_upgrade_guards_block_old_app_capturing_and_muted_writes(self):
        path, record, _, events, _ = self.old_shared_file()
        with closing(Repository(path)) as repo:
            self.assertEqual(store._meta(repo, 'peer_protocol'), 3)
        with closing(sqlite3.connect(path)) as old:
            old.create_function('flow_peer_write_allowed', 0, lambda: 1)
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'Update Flow'):
                old.execute("UPDATE flows SET name='Lost old app edit'")
            with self.assertRaisesRegex(sqlite3.OperationalError, 'flow_peer_event_write_allowed'):
                old.execute('INSERT INTO peer_events(id,payload) VALUES(?,?)',
                            (str(uuid.uuid4()), _json(events[0])))
        with closing(Repository(path)) as repo:
            self.assertEqual(repo.get_flow(record['flow_id']), record)
            self.assertEqual(store.missing(repo, []), events)
            with self.assertRaisesRegex(ValueError, 'Update Flow'):
                store.join(repo, dict(store.seed(repo), protocol=2))

    def test_protocol_upgrade_failure_rolls_back_rows_clock_guards_and_metadata(self):
        path, record, original, events, _ = self.old_shared_file(resolved=False)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TRIGGER reject_peer_upgrade BEFORE INSERT ON sync_meta "
                         "WHEN NEW.key='peer_protocol' BEGIN SELECT RAISE(ABORT,'blocked upgrade'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'blocked upgrade'):
            Repository(path)
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('SELECT name FROM flows').fetchone()[0], record['name'])
            self.assertEqual(conn.execute('SELECT payload FROM peer_seed').fetchone()[0], _json(original))
            self.assertEqual([json.loads(row[0]) for row in conn.execute('SELECT payload FROM peer_events ORDER BY sequence')], events)
            self.assertEqual(conn.execute("SELECT count(*) FROM sync_meta WHERE key IN ('peer_protocol','peer_clock')").fetchone()[0], 0)
            self.assertIn('<>1', conn.execute("SELECT sql FROM sqlite_master WHERE name='peer_guard_flows_update'").fetchone()[0])
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='peer_guard_events_insert'").fetchone())
            self.assertTrue(json.loads(conn.execute("SELECT value FROM sync_meta WHERE key='peer_conflicts'").fetchone()[0]))


if __name__ == '__main__':
    unittest.main()
