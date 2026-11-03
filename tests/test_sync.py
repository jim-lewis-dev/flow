"""Distributed edits, retry/restart, conflicts and guarded session undo."""
import copy
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository
from flowledger.history import SessionHistory
from flowledger.sync import SyncState, SyncConflict


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repos = []
        self.server = self.repo('server')
        self.record = self.server.save_flow(dict(date='2026-11-01', from_account='A', to_account='B', amount='2', units='USD', name='Initial'))
        self.server_sync = SyncState(self.server)
        self.server_sync.configure(dataset='money', server=True)
        self.a, self.b = self.client('a'), self.client('b')

    def tearDown(self):
        for repo in self.repos:
            try:
                repo.close()
            except Exception:
                pass

    def repo(self, name):
        repo = Repository(Path(self.temp.name) / (name + '.sqlite3'))
        self.repos.append(repo)
        return repo

    def client(self, name):
        repo = self.repo(name)
        SyncState(repo).configure(dataset='money', seed=self.server_sync.export_seed())
        return repo

    def exchange(self, repo):
        response = self.server_sync.server_exchange(repo.sync.prepare_request())
        repo.sync.accept_response(response)
        return response

    def edit(self, repo, **fields):
        record = repo.get_flow(self.record['flow_id'])
        record.update(fields)
        return repo.save_flow(record)

    def test_offline_restart_and_lost_ack(self):
        self.edit(self.a, notes='offline')
        request = self.a.sync.prepare_request()
        first = self.server_sync.server_exchange(request)
        second = self.server_sync.server_exchange(request)
        self.assertEqual(first, second)
        path = self.a.path
        old_client = self.a.sync.client
        self.a.close()
        self.a = Repository(path)
        self.repos.append(self.a)
        self.assertNotEqual(old_client, self.a.sync.client)
        self.assertEqual(1, self.a.sync.status()['pending'])
        self.exchange(self.a)
        self.assertEqual(0, self.a.sync.status()['pending'])
        self.exchange(self.b)
        self.assertEqual('offline', self.b.get_flow(self.record['flow_id'])['notes'])
        self.assertEqual(1, self.server.conn.execute('SELECT count(*) FROM sync_log').fetchone()[0])

    def test_unrelated_fields_and_same_field_conflict(self):
        self.edit(self.a, name='phone')
        self.edit(self.b, notes='desktop')
        self.exchange(self.a)
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual(('phone', 'desktop'), tuple(self.a.get_flow(self.record['flow_id'])[f] for f in ('name', 'notes')))
        self.edit(self.a, amount='3')
        self.edit(self.b, amount='4')
        self.exchange(self.a)
        self.exchange(self.b)
        self.assertEqual('4', self.b.get_flow(self.record['flow_id'])['amount'])
        conflict = self.b.sync.status()['conflicts'][0]
        self.assertEqual('3', conflict['changes'][0]['remote']['amount'])
        self.b.sync.resolve(conflict['operation_id'], 'remote')
        self.assertEqual('3', self.b.get_flow(self.record['flow_id'])['amount'])

    def test_local_resolution_and_dependent_edits(self):
        self.edit(self.a, amount='3')
        self.edit(self.b, amount='4')
        self.edit(self.b, notes='keep this')
        self.exchange(self.a)
        self.exchange(self.b)
        self.b.sync.resolve(self.b.sync.status()['conflicts'][0]['operation_id'], 'local')
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual(('4', 'keep this'), tuple(self.a.get_flow(self.record['flow_id'])[f] for f in ('amount', 'notes')))

    def test_undo_preserves_unrelated_remote_fields_and_blocks_aba(self):
        history = SessionHistory(self.a)
        history.perform('name', lambda: self.edit(self.a, name='local'))
        self.exchange(self.a)
        self.exchange(self.b)
        self.edit(self.b, notes='remote')
        self.exchange(self.b)
        self.exchange(self.a)
        history.undo()
        self.assertEqual('remote', self.a.get_flow(self.record['flow_id'])['notes'])
        history.redo()
        self.exchange(self.a)
        self.exchange(self.b)
        self.edit(self.b, name='other')
        self.edit(self.b, name='local')
        self.exchange(self.b)
        self.exchange(self.a)
        with self.assertRaisesRegex(SyncConflict, 'newer change'):
            history.undo()
        history.close()

    def test_references_and_positions_are_atomic(self):
        for repo, name in ((self.a, 'one'), (self.b, 'two')):
            repo.save_flow(dict(date='2026-11-02', from_account='New', to_account='B', amount='0', units='kg', name=name, tags=['shared']))
        self.exchange(self.a)
        self.exchange(self.b)
        self.assertFalse(self.b.sync.status()['conflicts'])
        self.exchange(self.a)
        self.assertEqual(3, len(self.a.list_flows()))
        self.a.save_map_positions({'A': (1.5, -2)})
        self.exchange(self.a)
        self.exchange(self.b)
        self.assertEqual({'A': (1.5, -2)}, self.b.load_map_positions())
        self.a.delete_flow(self.record['flow_id'])
        self.exchange(self.a)
        self.exchange(self.b)
        self.assertIsNone(self.b.get_flow(self.record['flow_id']))

    def test_rejected_operation_is_atomic_and_wrong_seed_denied(self):
        self.edit(self.a, amount='3')
        request = self.a.sync.prepare_request()
        bad = copy.deepcopy(request)
        bad['operations'][0]['changes'][0]['after']['amount'] = 'NaN'
        with self.assertRaises(ValueError):
            self.server_sync.server_exchange(bad)
        self.assertEqual('2', self.server.get_flow(self.record['flow_id'])['amount'])
        other = self.repo('other')
        other.save_account('different', opening_amount='2')
        with self.assertRaisesRegex(ValueError, 'differs'):
            SyncState(other).configure(dataset='money', seed=self.server_sync.export_seed())
        self.assertIsNotNone(other.get_account('different'))

    def test_rollback_does_not_enqueue(self):
        with self.assertRaises(RuntimeError):
            with self.a.atomic():
                self.edit(self.a, name='rolled back')
                raise RuntimeError()
        self.assertEqual(0, self.a.sync.status()['pending'])
        self.assertEqual('Initial', self.a.get_flow(self.record['flow_id'])['name'])

    def test_reused_operation_id_rejected(self):
        self.edit(self.a, name='one')
        request = self.a.sync.prepare_request()
        self.server_sync.server_exchange(request)
        request['operations'][0]['changes'][0]['after']['name'] = 'two'
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.server_sync.server_exchange(request)

    def test_multiple_own_undos_redos_across_acknowledgements(self):
        history = SessionHistory(self.a)
        history.perform('first', lambda: self.edit(self.a, name='first'))
        history.perform('second', lambda: self.edit(self.a, name='second'))
        history.undo()
        self.exchange(self.a)
        history.undo()
        self.assertEqual('Initial', self.a.get_flow(self.record['flow_id'])['name'])
        history.redo()
        self.exchange(self.a)
        history.redo()
        self.assertEqual('second', self.a.get_flow(self.record['flow_id'])['name'])
        self.exchange(self.a)
        history.close()

    def test_undo_remote_aba_detected_while_local_edit_pending(self):
        history = SessionHistory(self.a)
        history.perform('local', lambda: self.edit(self.a, name='local'))
        self.edit(self.b, name='remote')
        self.edit(self.b, name='Initial')
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual('local', self.a.get_flow(self.record['flow_id'])['name'])
        with self.assertRaises(SyncConflict):
            history.undo()
        history.close()

    def test_remote_delete_preserves_local_conflict_and_can_choose_remote(self):
        self.edit(self.a, notes='unsent')
        self.b.delete_flow(self.record['flow_id'])
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual('unsent', self.a.get_flow(self.record['flow_id'])['notes'])
        conflict = self.a.sync.status()['conflicts'][0]
        self.a.sync.resolve(conflict['operation_id'], 'remote')
        self.assertIsNone(self.a.get_flow(self.record['flow_id']))

    def test_idle_pull_preserves_rowids_and_makes_no_writes(self):
        before = [tuple(row) for row in self.a.conn.execute('SELECT rowid,* FROM flows')]
        changes = self.a.conn.total_changes
        self.exchange(self.a)
        self.assertEqual(changes, self.a.conn.total_changes)
        self.assertEqual(before, [tuple(row) for row in self.a.conn.execute('SELECT rowid,* FROM flows')])

    def test_undo_create_does_not_delete_remote_dependent_flow(self):
        history = SessionHistory(self.a)
        history.perform('new', lambda: self.a.save_flow(dict(date='2026-11-02', from_account='New', to_account='B', amount='1', units='USD')))
        self.exchange(self.a)
        self.exchange(self.b)
        self.b.save_flow(dict(date='2026-11-03', from_account='New', to_account='A', amount='2', units='USD'))
        self.exchange(self.b)
        self.exchange(self.a)
        with self.assertRaises(Exception):
            history.undo()
        self.assertEqual(3, len(self.a.list_flows()))
        history.close()

    def test_resolving_one_conflict_does_not_overwrite_other_conflict(self):
        self.edit(self.a, amount='3', notes='a')
        self.edit(self.b, amount='4')
        self.edit(self.b, notes='b')
        self.exchange(self.a)
        self.exchange(self.b)
        self.b.sync.resolve(self.b.sync.status()['conflicts'][0]['operation_id'], 'local')
        self.assertEqual(1, len(self.b.sync.status()['conflicts']))
        self.assertIn('notes', self.b.sync.status()['conflicts'][0]['changes'][0]['after'])

    def test_old_application_cannot_make_untracked_writes(self):
        import sqlite3
        with sqlite3.connect(self.a.path) as old:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'flow_sync_write_allowed'):
                old.execute("UPDATE flows SET notes='untracked'")
        self.assertEqual('', self.a.get_flow(self.record['flow_id'])['notes'])
        self.edit(self.a, notes='tracked')
        self.assertEqual(1, self.a.sync.status()['pending'])

    def test_deleted_conflicting_flow_retains_tags_for_copy(self):
        self.edit(self.a, tags=['keep'])
        self.exchange(self.a)
        self.exchange(self.b)
        self.edit(self.a, notes='local')
        self.b.delete_flow(self.record['flow_id'])
        self.exchange(self.b)
        self.exchange(self.a)
        self.assertEqual(['keep'], self.a.get_flow(self.record['flow_id'])['tags'])
        self.assertEqual('local', self.a.get_flow(self.record['flow_id'])['notes'])

    def test_undo_add_cannot_cascade_away_new_remote_tags(self):
        history = SessionHistory(self.a)
        created = history.perform('add', lambda: self.a.save_flow(dict(date='2026-11-02', from_account='A', to_account='B', amount='1', units='USD')))
        self.exchange(self.a)
        self.exchange(self.b)
        created['tags'] = ['remote']
        self.b.save_flow(created)
        self.exchange(self.b)
        self.exchange(self.a)
        with self.assertRaisesRegex(ValueError, 'depends'):
            history.undo()
        self.assertEqual(['remote'], self.a.get_flow(created['flow_id'])['tags'])
        history.close()

    def test_map_undo_blocks_remote_change(self):
        history = SessionHistory(self.a)
        history.perform('move', lambda: self.a.save_map_positions({'A': (1, 2)}))
        self.exchange(self.a)
        self.exchange(self.b)
        self.b.save_map_positions({'A': (2, 3)})
        self.exchange(self.b)
        self.exchange(self.a)
        with self.assertRaisesRegex(ValueError, 'newer change'):
            history.undo()
        self.assertEqual({'A': (2, 3)}, self.a.load_map_positions())
        history.close()

    def test_restoring_synced_source_preserves_standalone_target(self):
        local = self.repo('standalone')
        original = local.save_flow(dict(date='2026-11-01', from_account='Local', to_account='Only', amount='8', units='kg'))
        snapshot = Path(self.temp.name) / 'shared-backup.sqlite3'
        self.a.backup(snapshot)
        with self.assertRaisesRegex(ValueError, 'Open it separately or join'):
            local.restore(snapshot)
        self.assertEqual([original], local.list_flows())
        self.assertIsNone(local.sync)
        self.assertIsNone(local.conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_meta'").fetchone())
        self.assertEqual([], list(Path(self.temp.name).glob('standalone.sqlite3.pre-restore-*')))
        original['notes'] = 'Still editable'
        self.assertEqual('Still editable', local.save_flow(original)['notes'])

    def test_previous_session_ack_does_not_block_current_session_undo(self):
        self.edit(self.a, name='previous session')
        path = self.a.path
        old_client = self.a.sync.client
        self.a.close()
        self.a = Repository(path)
        self.repos.append(self.a)
        self.assertNotEqual(old_client, self.a.sync.client)
        history = SessionHistory(self.a)
        history.perform('current edit', lambda: self.edit(self.a, name='current session'))
        self.exchange(self.a)
        history.undo()
        self.assertEqual('previous session', self.a.get_flow(self.record['flow_id'])['name'])
        self.exchange(self.a)
        self.exchange(self.b)
        self.assertEqual('previous session', self.b.get_flow(self.record['flow_id'])['name'])
        history.close()

    def test_offline_undo_receives_new_remote_dependent_flow_as_conflict(self):
        history = SessionHistory(self.a)
        added = history.perform('add', lambda: self.a.save_flow(dict(date='2026-11-02', from_account='New', to_account='B', amount='1', units='kg')))
        self.exchange(self.a)
        self.exchange(self.b)
        remote = self.b.save_flow(dict(date='2026-11-03', from_account='New', to_account='B', amount='2', units='kg'))
        self.exchange(self.b)
        history.undo()  # This device has not yet received the dependent flow.
        self.assertIsNone(self.a.get_account('New', 'kg'))
        self.exchange(self.a)
        self.assertIsNotNone(self.a.get_flow(remote['flow_id']))
        self.assertIsNotNone(self.a.get_account('New', 'kg'))
        conflicts = self.a.sync.status()['conflicts']
        self.assertEqual(1, len(conflicts))
        self.a.sync.resolve(conflicts[0]['operation_id'], 'remote')
        self.exchange(self.a)
        self.assertIsNotNone(self.a.get_flow(added['flow_id']))
        self.assertIsNotNone(self.a.get_flow(remote['flow_id']))
        self.assertEqual(0, self.a.sync.status()['pending'])
        self.assertEqual([], self.a.conn.execute('PRAGMA foreign_key_check').fetchall())
        history.close()
