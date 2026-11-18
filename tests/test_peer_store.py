"""SQLite behavior across peer edits, crash/restart boundaries and old clients."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository
from flowledger.history import SessionHistory
from flowledger import peer_store as store
from flowledger.sync import SyncConflict, _key


class PeerStoreTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.a, self.b = self.repo('a'), self.repo('b')
        self.row = self.a.save_flow({'date': '2026-10-01', 'amount': '3.0025', 'units': 'kWh',
                                     'from_account': 'Battery', 'to_account': 'Lamp',
                                     'name': 'Night', 'tags': ['solar']})
        self.dataset = store.share(self.a, 'Camp')
        self.seed = store.seed(self.a)
        store.join(self.b, self.seed)

    def repo(self, name):
        result = Repository(self.root / (name + '.sqlite3'))
        self.addCleanup(result.close)
        return result

    def row_at(self, repo):
        return repo.get_flow(self.row['flow_id'])

    def edit(self, repo, **values):
        return repo.save_flow(dict(self.row_at(repo), **values))

    def send(self, source, target):
        return store.receive(target, store.missing(source, store.inventory(target)))

    def test_atomic_capture_restart_retry_and_immutable_original(self):
        with self.a.atomic():
            self.edit(self.a, name='first')
            self.edit(self.a, amount='7.125')
            try:
                with self.a.atomic():
                    self.edit(self.a, notes='must roll back')
                    raise ValueError
            except ValueError:
                pass
        original = store.missing(self.a, [])
        self.assertEqual(len(original), 1)
        self.assertEqual(len(original[0]['changes']), 1)
        self.assertEqual(store.seed(self.a), self.seed)
        with closing(Repository(self.a.path)) as reopened:
            self.assertEqual(store.seed(reopened), self.seed)
            self.assertEqual(store.inventory(reopened), [original[0]['id']])
            self.assertTrue(self.send(reopened, self.b))
        self.assertEqual(self.row_at(self.a), self.row_at(self.b))
        self.assertFalse(store.receive(self.b, original))
        with self.assertRaisesRegex(ValueError, 'reused'):
            store.receive(self.b, [dict(original[0], origin='another origin')])
        with self.assertRaises(sqlite3.IntegrityError):
            self.a.conn.execute("UPDATE peer_events SET payload='{}'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.a.conn.execute('DELETE FROM peer_seed')

    def test_complete_reversed_batch_accepted_incomplete_or_invalid_batch_rejected(self):
        self.edit(self.a, name='before')
        self.edit(self.a, name='after')
        first, second = store.missing(self.a, [])
        self.assertEqual(second['parents'], [first['id']])
        with self.assertRaisesRegex(ValueError, 'Missing parent'):
            store.receive(self.b, [second])
        wrong = deepcopy(second)
        wrong['changes'][0]['before']['notes'] = 'not seen'
        with self.assertRaisesRegex(ValueError, 'wrong prior'):
            store.receive(self.b, [first, wrong])
        self.assertEqual(store.inventory(self.b), [])
        self.assertEqual(self.row_at(self.b), self.row)
        self.assertTrue(store.receive(self.b, [second, first]))
        self.assertEqual(store.inventory(self.b), [first['id'], second['id']])
        self.assertEqual(self.row_at(self.b)['name'], 'after')

    def test_causal_heads_latest_edit_and_followup_are_durable(self):
        with patch('flowledger.peer_store.time.time_ns', return_value=1000_000_000):
            self.edit(self.a, name='sun')
        with patch('flowledger.peer_store.time.time_ns', return_value=2000_000_000):
            self.edit(self.b, name='moon')
        self.send(self.a, self.b)
        self.send(self.b, self.a)
        self.assertFalse(store._meta(self.b, 'peer_conflicts'))
        self.assertEqual(self.row_at(self.a)['name'], 'moon')
        self.edit(self.a, notes='independent', name='final')
        journal = store.missing(self.a, [])
        self.assertEqual(set(journal[-1]['parents']), {e['id'] for e in journal[:2]})
        self.assertGreater(journal[-1]['stamp'], max(e['stamp'] for e in journal[:2]))
        self.send(self.a, self.b)
        self.assertFalse(store._meta(self.b, 'peer_conflicts'))
        self.assertEqual((self.row_at(self.b)['name'], self.row_at(self.b)['notes']), ('final', 'independent'))
        with closing(Repository(self.b.path)) as reopen:
            self.assertEqual(self.row_at(reopen), self.row_at(self.a))
            self.assertFalse(store._meta(reopen, 'peer_conflicts'))

    def test_undo_field_aba_other_fields_and_redo(self):
        h = SessionHistory(self.a)
        self.addCleanup(h.close)
        h.perform('Name', lambda: self.edit(self.a, name='local'))
        self.send(self.a, self.b)
        self.edit(self.b, notes='remote')
        self.send(self.b, self.a)
        h.undo()
        self.assertEqual(self.row_at(self.a)['notes'], 'remote')
        h.redo()
        self.send(self.a, self.b)
        old = self.a.sync.field_tokens('flows', [self.row['flow_id']])
        self.edit(self.b, name='away', tags=[])
        self.edit(self.b, name='local', tags=['solar'])
        self.send(self.b, self.a)
        new = self.a.sync.field_tokens('flows', [self.row['flow_id']])
        self.assertNotEqual(old['name'], new['name'])
        self.assertNotEqual(old['tags'], new['tags'])
        self.assertEqual(old['notes'], new['notes'])
        with self.assertRaisesRegex(SyncConflict, 'newer change'):
            h.undo()

    def test_history_started_before_external_sharing_checks_remote_aba(self):
        local = self.repo('private-first')
        local.save_flow(self.row)
        history = SessionHistory(local)
        self.addCleanup(history.close)
        original = self.edit(local, name='first')
        history.perform('Rename privately', lambda: self.edit(local, name='after'))
        with closing(Repository(local.path)) as worker:
            store.share(worker, 'Started later')
            other = self.repo('joined-later')
            store.join(other, store.seed(worker))
            self.edit(other, name='away')
            self.edit(other, name='after')
            self.send(other, worker)
        with self.assertRaisesRegex(SyncConflict, 'newer change'):
            history.undo()
        self.assertNotEqual(self.row_at(local)['name'], original['name'])

    def test_event_capture_guard_and_private_backup_restore(self):
        self.assertTrue(self.a.sync.tracking)
        with closing(sqlite3.connect(self.a.path)) as old:
            old.create_function('flow_sync_write_allowed', 0, lambda: 1)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'flow_peer_write_allowed'):
                old.execute("UPDATE flows SET name='lost'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'peer sharing'):
                old.execute("""INSERT INTO sync_meta VALUES('dataset','"old"')""")
        private = self.repo('private')
        backup = self.root / 'backup.sqlite3'
        self.a.backup(backup)
        with self.assertRaisesRegex(ValueError, 'synchronized backup'):
            private.restore(backup)
        with self.assertRaisesRegex(ValueError, 'synchronized file'):
            self.a.restore(private.path)

    def test_failed_join_no_overwrite_and_seed_validation(self):
        c = self.repo('c')
        c.save_account('Different', units='W')
        with self.assertRaisesRegex(ValueError, 'differs'):
            store.join(c, self.seed)
        self.assertEqual(store.dataset(c), '')
        self.assertEqual(c.list_accounts()[0]['name'], 'Different')
        empty = self.repo('empty')
        malformed = deepcopy(self.seed)
        del malformed['snapshot'][_key('accounts', ['battery'])]
        malformed['order'] = list(malformed['snapshot'])
        with self.assertRaisesRegex((ValueError, sqlite3.IntegrityError), 'depends|constraint'):
            store.join(empty, malformed)
        self.assertEqual(store.dataset(empty), '')
        self.assertFalse(empty.conn.in_transaction)
        self.assertFalse(empty.list_flows())
        self.assertEqual(store.join(empty, self.seed), self.dataset)
        variants = [dict(self.seed, dataset=str(uuid.uuid4())), dict(self.seed, order=self.seed['order'][::-1])]
        for modified in variants:
            with self.assertRaisesRegex(ValueError, 'different'):
                store.join(empty, modified)
        self.assertEqual(self.row_at(empty), self.row)

    def test_external_activation_and_failed_capture_rolls_back(self):
        open_editor = self.repo('other')
        self.assertIsNone(open_editor.sync)
        with closing(Repository(open_editor.path)) as worker:
            store.join(worker, self.seed)
        self.edit(open_editor, name='armed')
        self.assertEqual(len(store.inventory(open_editor)), 1)
        old = self.row_at(open_editor)
        clock = store._meta(open_editor, 'peer_clock')
        with patch('flowledger.peer_store._materialize', side_effect=ValueError('rollback')):
            with self.assertRaisesRegex(ValueError, 'rollback'):
                self.edit(open_editor, name='failed')
        self.assertEqual(self.row_at(open_editor), old)
        self.assertEqual(len(store.inventory(open_editor)), 1)
        self.assertEqual(store._meta(open_editor, 'peer_clock'), clock)
        self.assertFalse(open_editor.conn.in_transaction)
        self.edit(open_editor, amount='0')

    def test_clock_rollback_same_millisecond_receive_restart_and_relay(self):
        with patch('flowledger.peer_store.time.time_ns', return_value=3000_000_000):
            self.edit(self.a, name='first')
            self.edit(self.a, name='second')
        with patch('flowledger.peer_store.time.time_ns', return_value=1000_000_000):
            self.edit(self.a, name='third')
        events = store.missing(self.a, [])
        self.assertEqual([e['stamp'] for e in events], [[3000, 0], [3000, 1], [3000, 2]])
        self.send(self.a, self.b)
        self.assertEqual(store.missing(self.b, []), events)
        with closing(Repository(self.b.path)) as reopened:
            with patch('flowledger.peer_store.time.time_ns', return_value=1000_000_000):
                self.edit(reopened, notes='after observing')
            newest = store.missing(reopened, [])[-1]
            self.assertEqual(newest['stamp'], [3000, 3])
            self.assertEqual(newest['parents'], [events[-1]['id']])

    def test_timestamp_validation_batch_rollback_and_append_guard(self):
        with patch('flowledger.peer_store.time.time_ns', return_value=1000_000_000):
            self.edit(self.a, name='first')
            self.edit(self.a, name='second')
        first, second = store.missing(self.a, [])
        invalid = [None, [], [1], [1, 2, 3], [True, 0], [1, False], [1.0, 0],
                   [0, 0], [-1, 0], [1, -1], [store.MAX_STAMP + 1, 0], [1, store.MAX_STAMP + 1]]
        for stamp in invalid:
            with self.subTest(stamp=stamp):
                with self.assertRaisesRegex(ValueError, 'timestamp'):
                    store.receive(self.b, [dict(first, stamp=stamp)])
        with self.assertRaisesRegex(ValueError, 'later than every parent'):
            store.receive(self.b, [first, dict(second, stamp=first['stamp'])])
        self.assertEqual(store.inventory(self.b), [])
        self.assertEqual(store._meta(self.b, 'peer_clock'), [0, 0])
        self.assertEqual(self.row_at(self.b), self.row)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'Update Flow'):
            self.b.conn.execute('INSERT INTO peer_events(id,payload) VALUES(?,?)',
                                (first['id'], store._json(first)))
        self.assertTrue(store.receive(self.b, [second, first]))
        unstamped_child = deepcopy(second)
        unstamped_child['id'] = str(uuid.uuid4())
        unstamped_child['parents'] = [second['id']]
        del unstamped_child['stamp']
        with self.assertRaisesRegex(ValueError, 'unsupported sharing history'):
            store.receive(self.b, [unstamped_child])

    def test_timestamp_tie_breaks_by_event_uuid_on_every_device(self):
        low, high = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))
        with patch('flowledger.peer_store.time.time_ns', return_value=1000_000_000):
            self.edit(self.a, name='lower id')
            self.edit(self.b, name='higher id')
        left, = store.missing(self.a, [])
        right, = store.missing(self.b, [])
        left, right = dict(left, id=low), dict(right, id=high)
        for name, events in [('left-first', [left, right]), ('right-first', [right, left])]:
            repo = self.repo(name)
            store.join(repo, self.seed)
            store.receive(repo, events)
            self.assertEqual(self.row_at(repo)['name'], 'higher id')

    def test_delta_bounds_duplicate_values_and_wrong_dataset(self):
        for amount in ('4', '5', '6'):
            self.edit(self.a, amount=amount)
        with patch.object(store, 'BATCH_EVENTS', 2):
            events = store.missing(self.a, [])
            self.assertEqual(len(events), 2)
            self.assertTrue(store.receive(self.b, events))
            self.assertEqual(len(store.missing(self.a, store.inventory(self.b))), 1)
        bad = dict(store.missing(self.a, [])[0], dataset=str(uuid.uuid4()))
        with self.assertRaisesRegex(ValueError, 'different dataset'):
            store.receive(self.b, [bad])
        self.assertTrue(self.send(self.a, self.b))
        with self.assertRaisesRegex(ValueError, 'Invalid peer identifier'):
            store.missing(self.b, [True])


if __name__ == '__main__':
    unittest.main()
