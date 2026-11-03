"""Exercise the real HTTPS boundary and coordinator failure isolation."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger.sync import SyncState
from flowledger.sync_server import Coordinator, SnapshotMirror, initialize
from flowledger.sync_transport import exchange, fetch_bootstrap, parse_connection


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL creates the test certificate')
class SyncTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'money.sqlite3'
        with_repo = Repository(self.source)
        self.initial = with_repo.save_flow({
            'date': '2026-11-03', 'from_account': 'Battery', 'to_account': 'Lights',
            'amount': '25', 'units': 'Wh', 'name': 'Evening lights', 'notes': '',
        })
        with_repo.close()
        self.directory = self.root / 'coordinator'
        self.settings = initialize(self.source, self.directory, 'https://127.0.0.1:8765')

    @contextmanager
    def running(self):
        server = Coordinator(('127.0.0.1', 0), self.directory, self.settings)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        config = parse_connection(dict(self.settings, endpoint='https://127.0.0.1:' + str(server.server_port)))
        try:
            yield server, config
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def replica(self, config):
        repo = Repository(self.root / 'replica.sqlite3')
        self.addCleanup(repo.close)
        sync = SyncState(repo)
        sync.configure(**config, seed=fetch_bootstrap(config))
        return repo, sync

    def test_https_bootstrap_does_not_change_source(self):
        original = self.source.read_bytes()
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            self.assertEqual(repo.get_flow(self.initial['flow_id']), self.initial)
            self.assertEqual(sync.status()['pending'], 0)
        self.assertEqual(self.source.read_bytes(), original)

    def test_certificate_pin_checked_before_any_application_request(self):
        with self.running() as (server, config):
            config['certificate'] = '0' * 64
            with patch.object(server, 'process', wraps=server.process) as process:
                with self.assertRaisesRegex(ValueError, 'certificate changed'):
                    fetch_bootstrap(config)
                process.assert_not_called()

    def test_incorrect_access_key_cannot_read_or_modify(self):
        with self.running() as (server, config):
            config['token'] = 'a' * 43
            with patch.object(server, 'process', wraps=server.process) as process:
                with self.assertRaisesRegex(ValueError, 'connection key'):
                    fetch_bootstrap(config)
                process.assert_not_called()

    def test_wrong_dataset_rejected(self):
        with self.running() as (server, config):
            config['dataset'] = 'another-file'
            with self.assertRaisesRegex(ValueError, 'different Flow file'):
                fetch_bootstrap(config)

    def test_incomplete_tls_handshake_does_not_block_other_clients(self):
        with self.running() as (server, config):
            # A peer connected to TCP but never sends a TLS hello.
            stalled = socket.create_connection(('127.0.0.1', server.server_port), timeout=1)
            try:
                self.assertEqual(config['dataset'], fetch_bootstrap(config)['dataset'])
            finally:
                stalled.close()

    def test_router_snapshot_cannot_replace_live_coordinator_database(self):
        settings = dict(self.settings, mirror=str(self.directory / self.settings['database']))
        with self.assertRaisesRegex(ValueError, 'outside the coordinator directory'):
            Coordinator(('127.0.0.1', 0), self.directory, settings)

    def test_lost_acknowledgement_retries_once(self):
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            edited = dict(self.initial, amount='28')
            repo.save_flow(edited)
            request = sync.prepare_request()
            first = exchange(config, request)
            second = exchange(config, request)
            self.assertEqual(first, second)
            sync.accept_response(second)
            self.assertEqual(sync.status()['pending'], 0)
            self.assertEqual(repo.get_flow(edited['flow_id'])['amount'], '28')
            central = Repository(server.database)
            try:
                self.assertEqual(central.get_flow(edited['flow_id'])['amount'], '28')
                self.assertEqual(central.conn.execute('SELECT count(*) FROM sync_log').fetchone()[0], 1)
            finally:
                central.close()

    def test_invalid_operation_cannot_write_arbitrary_tables(self):
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            repo.save_flow(dict(self.initial, notes='Local note'))
            request = sync.prepare_request()
            request['operations'][0]['changes'][0]['table'] = 'audit'
            with self.assertRaisesRegex(ValueError, 'synchronization change'):
                exchange(config, request)
            central = Repository(server.database)
            try:
                self.assertEqual(central.get_flow(self.initial['flow_id'])['notes'], '')
            finally:
                central.close()
            self.assertEqual(sync.status()['pending'], 1)

    def test_unavailable_router_does_not_block_sync(self):
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            server.mirror.destination = self.root / 'unmounted-router' / 'money.sqlite3'
            with self.assertRaises(OSError):
                server.mirror._publish()
            repo.save_flow(dict(self.initial, notes='Survives router outage'))
            sync.accept_response(exchange(config, sync.prepare_request()))
            self.assertEqual(sync.status()['pending'], 0)
            central = Repository(server.database)
            try:
                self.assertEqual(central.get_flow(self.initial['flow_id'])['notes'], 'Survives router outage')
            finally:
                central.close()

    def test_idle_poll_does_not_request_another_router_snapshot(self):
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            self.assertFalse(server.mirror.changed.is_set())
            sync.accept_response(exchange(config, sync.prepare_request()))
            self.assertFalse(server.mirror.changed.is_set())
            repo.save_flow(dict(self.initial, amount='30'))
            sync.accept_response(exchange(config, sync.prepare_request()))
            self.assertTrue(server.mirror.changed.is_set())

    def test_router_snapshot_is_complete_and_replaceable(self):
        with self.running() as (server, config):
            repo, sync = self.replica(config)
            destination = self.root / 'router.sqlite3'
            mirror = SnapshotMirror(server.database, destination)
            mirror._publish()
            repo.save_flow(dict(self.initial, amount='32'))
            sync.accept_response(exchange(config, sync.prepare_request()))
            mirror._publish()
            snapshot = Repository(destination)
            try:
                self.assertEqual(snapshot.conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                self.assertEqual(snapshot.get_flow(self.initial['flow_id'])['amount'], '32')
            finally:
                snapshot.close()
            self.assertFalse(destination.with_name('.' + destination.name + '.sync-tmp').exists())


class ConnectionTextTests(unittest.TestCase):
    def test_requires_encrypted_endpoint_and_complete_pin(self):
        valid = dict(endpoint='https://dibbles.local:8765', dataset='money', token='a' * 43, certificate='b' * 64)
        self.assertEqual(parse_connection(json.dumps(valid)), valid)
        for endpoint in ['http://dibbles.local:8765', 'https://user:pass@dibbles.local',
                         'https://dibbles.local/files', 'https://dibbles.local?token=abc']:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                parse_connection(dict(valid, endpoint=endpoint))
        with self.assertRaises(ValueError):
            parse_connection(dict(valid, certificate='b' * 63))


if __name__ == '__main__':
    unittest.main()
