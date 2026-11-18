"""Offline readable names preserve shared data and default shortcuts."""
from contextlib import closing
from hashlib import sha256
import fcntl
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository
from flowledger import peer_store, shared_files


class SharedFileNamesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root / 'data'),
                                     'XDG_CONFIG_HOME': str(self.root / 'config')})
        env.start()
        self.addCleanup(env.stop)
        self.data = self.root / 'data' / 'flowledger'
        self.data.mkdir(parents=True)
        self.settings = self.root / 'config' / 'flow' / 'peers'
        self.settings.mkdir(parents=True)
        self.catalog = self.settings / 'devices.sqlite3'
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute('CREATE TABLE files(dataset TEXT PRIMARY KEY,name TEXT,path TEXT UNIQUE,baseline TEXT)')

    def file(self, name='Money', *, original=False):
        dataset = str(uuid.uuid4())
        path = self.data / 'flow.sqlite3' if original else self.data / 'shared' / (sha256(dataset.encode()).hexdigest() + '.sqlite3')
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date='2026-11-18', from_account='Battery',
                to_account='Camp', amount='2.5', units='Wh', notes='Keep this exactly'))
            peer_store.share(repo, name, dataset_id=dataset)
            repo.save_flow(dict(record, amount='3.5'))
            baseline = peer_store.genesis(repo)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute('INSERT INTO files VALUES(?,?,?,?)', (dataset, name, str(path), baseline))
        return dataset, path, shared_files.shared_path(name)

    def registered(self, dataset):
        with closing(sqlite3.connect(self.catalog)) as db:
            return Path(db.execute('SELECT path FROM files WHERE dataset=?', (dataset,)).fetchone()[0])

    def test_names_are_readable_and_reject_unsafe_or_case_colliding_paths(self):
        self.assertEqual(shared_files.shared_path('Money'), self.data / 'shared/Money.sqlite3')
        self.assertEqual(shared_files.shared_path('Camp 2026.sqlite3').name, 'Camp 2026.sqlite3')
        for name in ('', '../Money', 'Money/June', 'Money\\June', '.hidden', 'Money\n',
                     ' Money', 'Money ', 'Money.', '\u00e9nergy', 'A:B', 'A*B'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                shared_files.shared_path(name)
        folder = self.data / 'shared'
        folder.mkdir()
        (folder / 'money.sqlite3').touch()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            shared_files.shared_path('Money')

    def test_rename_updates_catalog_and_default_without_changing_database_bytes(self):
        dataset, old, new = self.file()
        default = self.data / 'flow.sqlite3'
        alias = self.data / 'Money.sqlite3'
        default.symlink_to(old)
        alias.symlink_to(Path('shared') / old.name)
        original = old.read_bytes()
        inode = old.stat().st_ino
        self.assertEqual(shared_files.rename_shared_files(), [(old, new)])
        self.assertEqual(new.read_bytes(), original)
        self.assertEqual(new.stat().st_ino, inode)
        self.assertEqual(self.registered(dataset), new)
        self.assertEqual(default.resolve(), new)
        self.assertEqual(alias.resolve(), new)
        self.assertEqual(list(new.parent.iterdir()), [new])
        self.assertEqual(shared_files.rename_shared_files(), [])
        with closing(Repository(default)) as repo:
            record = repo.list_flows()[0]
            self.assertEqual(record['amount'], '3.5')
            repo.save_flow(dict(record, notes='Still shared'))
            self.assertEqual(len(peer_store.inventory(repo)), 2)

    def test_original_registered_file_is_not_moved(self):
        dataset, original, new = self.file(original=True)
        data = original.read_bytes()
        self.assertEqual(shared_files.rename_shared_files(), [])
        self.assertEqual(self.registered(dataset), original)
        self.assertEqual(original.read_bytes(), data)
        self.assertFalse(new.exists())

    def test_destination_collision_is_rejected_before_any_file_moves(self):
        first, a, an = self.file('First')
        second, b, bn = self.file('Second')
        bn.write_bytes(b'Unrelated file')
        with self.assertRaisesRegex(ValueError, 'already in use'):
            shared_files.rename_shared_files()
        self.assertTrue(a.is_file() and b.is_file())
        self.assertFalse(an.exists())
        self.assertEqual(bn.read_bytes(), b'Unrelated file')
        self.assertEqual(self.registered(first), a)
        self.assertEqual(self.registered(second), b)

    def test_same_shared_name_for_two_datasets_is_rejected(self):
        _, a, _ = self.file('Money')
        _, b, _ = self.file('Money')
        with self.assertRaisesRegex(ValueError, 'same local name'):
            shared_files.rename_shared_files()
        self.assertFalse(a.is_symlink() or b.is_symlink())

    def test_active_listener_and_database_sidecars_refuse_changes(self):
        _, old, new = self.file()
        with (self.settings / 'listener.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'Close all Flow windows'):
                shared_files.rename_shared_files()
        Path(str(old) + '-journal').touch()
        with self.assertRaisesRegex(ValueError, 'Close Flow cleanly'):
            shared_files.rename_shared_files()
        self.assertTrue(old.is_file())
        self.assertFalse(new.exists())

    def test_interrupted_hardlink_or_catalog_commit_is_recoverable(self):
        for phase in ('hardlink', 'old-link', 'catalog', 'default'):
            with self.subTest(phase=phase):
                dataset, old, new = self.file(phase)
                alias = self.data / (phase + '-default.sqlite3')
                alias.symlink_to(old)
                contents = old.read_bytes()
                os.link(old, new)
                if phase != 'hardlink':
                    shared_files._replace_link(old, new)
                if phase in ('catalog', 'default'):
                    with closing(sqlite3.connect(self.catalog)) as db, db:
                        db.execute('UPDATE files SET path=? WHERE dataset=?', (str(new), dataset))
                if phase == 'default':
                    shared_files._replace_link(alias, new)
                self.assertEqual(shared_files.rename_shared_files(), [(old, new)])
                self.assertEqual(new.read_bytes(), contents)
                self.assertEqual(self.registered(dataset), new)
                self.assertEqual(alias.resolve(), new)
                self.assertFalse(old.exists() or old.is_symlink())

    def test_catalog_failure_keeps_original_path_usable_and_retry_finishes(self):
        dataset, old, new = self.file()
        contents = old.read_bytes()
        default = self.data / 'flow.sqlite3'
        default.symlink_to(old)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute("CREATE TRIGGER reject_move BEFORE UPDATE ON files BEGIN SELECT RAISE(ABORT,'blocked'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'blocked'):
            shared_files.rename_shared_files()
        self.assertEqual(self.registered(dataset), old)
        self.assertEqual(old.read_bytes(), contents)
        self.assertEqual(default.read_bytes(), contents)
        with closing(sqlite3.connect(self.catalog)) as db, db:
            db.execute('DROP TRIGGER reject_move')
        self.assertEqual(shared_files.rename_shared_files(), [(old, new)])
        self.assertEqual(default.resolve(), new)


if __name__ == '__main__':
    unittest.main()
