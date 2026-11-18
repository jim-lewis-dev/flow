"""An existing paired ledger keeps exchanging edits after its local rename."""
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from discovery_test_support import isolate_discovery
from flowledger.core import Repository
from flowledger import peer_store
from flowledger.peer_network import PeerNode
from flowledger.shared_files import rename_shared_files, shared_path


@unittest.skipUnless(shutil.which('openssl'), 'Desktop device identities require OpenSSL')
class SharedFileSyncTests(unittest.TestCase):
    def test_renamed_copy_and_default_shortcut_keep_identity_and_two_way_sync(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, XDG_DATA_HOME=str(root / 'data'),
                                 XDG_CONFIG_HOME=str(root / 'config'))
        environment.start()
        self.addCleanup(environment.stop)
        isolate_discovery(self)
        owner_config = root / 'owner-device'
        joined_config = root / 'config/flow/peers'

        def start(config, name, port=0):
            node = PeerNode(config, name=name, host='127.0.0.1', port=port,
                            discovery=False, interval=3600).start()
            self.addCleanup(node.stop)
            return node

        owner = start(owner_config, 'Owner')
        joined = start(joined_config, 'Joined')
        source = root / 'original.sqlite3'
        with closing(Repository(source)) as repo:
            record = repo.save_flow(dict(date='2026-11-18', name='Meter reading',
                from_account='Tank', to_account='Garden', amount='1.25', units='L'))
        dataset = owner.share(source, 'Money')
        owner.nearby_open()
        joined.nearby_open()
        owner_id = owner.status()['peer_id']
        joined.nearby_lookup(owner.status()['address'])
        joined.nearby_request(owner_id)
        request = owner.nearby_poll()['request']
        owner.nearby_approve(request['id'])
        joined.nearby_poll()
        owner.nearby_close()
        joined.nearby_close()

        data = root / 'data/flowledger'
        old = data / 'shared' / (sha256(dataset.encode()).hexdigest() + '.sqlite3')
        joined.join(dataset, owner_id, old)
        default = data / 'flow.sqlite3'
        default.symlink_to(old)
        with closing(Repository(default)) as repo:
            repo.save_flow(dict(record, notes='Saved before renaming'))
        self.assertEqual(joined.sync_once()['errors'], [])
        owner_state, joined_state = owner.status(), joined.status()
        owner_trust, joined_trust = owner._peer_snapshot(), joined._peer_snapshot()
        owner.stop()
        joined.stop()
        before = old.read_bytes()
        original_before = source.read_bytes()

        new = shared_path('Money')
        self.assertEqual(rename_shared_files(), [(old, new)])
        self.assertFalse(old.exists())
        self.assertTrue(default.is_symlink())
        self.assertEqual(default.resolve(), new)
        self.assertEqual(new.read_bytes(), before)
        self.assertEqual(source.read_bytes(), original_before)
        self.assertEqual(rename_shared_files(), [])

        owner = start(owner_config, 'Owner', owner_state['port'])
        joined = start(joined_config, 'Joined', joined_state['port'])
        self.assertEqual(owner.status()['peer_id'], owner_id)
        self.assertEqual(joined.status()['peer_id'], joined_state['peer_id'])
        self.assertEqual(owner._peer_snapshot(), owner_trust)
        self.assertEqual(joined._peer_snapshot(), joined_trust)
        self.assertEqual(joined.local_files(), [dict(dataset=dataset, name='Money', path=str(new))])
        with closing(Repository(default)) as repo:
            self.assertEqual(repo.path, str(new))
            self.assertEqual(peer_store.dataset(repo), dataset)
            row = repo.get_flow(record['flow_id'])
            self.assertEqual(row['notes'], 'Saved before renaming')
            repo.save_flow(dict(row, name='Changed through default shortcut'))
        self.assertEqual(joined.sync_once()['errors'], [])
        with closing(Repository(source)) as repo:
            row = repo.get_flow(record['flow_id'])
            self.assertEqual(row['name'], 'Changed through default shortcut')
            repo.save_flow(dict(row, amount='2.5'))
        self.assertEqual(owner.sync_once()['errors'], [])
        with closing(Repository(default)) as repo:
            row = repo.get_flow(record['flow_id'])
            self.assertEqual(row['amount'], '2.5')
            self.assertEqual(row['name'], 'Changed through default shortcut')
            self.assertEqual(row['notes'], 'Saved before renaming')
            self.assertEqual(len(repo.list_flows()), 1)
            self.assertEqual(peer_store.dataset(repo), dataset)
        self.assertFalse(old.exists())


if __name__ == '__main__':
    unittest.main()
