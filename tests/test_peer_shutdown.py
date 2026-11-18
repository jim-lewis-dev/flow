"""Stopped background workers do not write errors into retired device settings."""
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from flowledger import peer_network, peer_wire


class PeerShutdownTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.node = peer_network.PeerNode(directory.name, interval=.2)
        self.stop = threading.Event()

    def test_monitor_error_after_stop_does_not_write_status(self):
        def fail():
            self.stop.set()
            raise sqlite3.OperationalError('database is locked')
        with patch.object(self.node, '_acquire', side_effect=fail), \
                patch.object(self.node, '_error') as save:
            self.node._run(self.stop)
        save.assert_not_called()

    def test_running_monitor_still_reports_errors(self):
        with patch.object(self.node, '_acquire', side_effect=OSError('listener unavailable')), \
                patch.object(self.node, '_error', side_effect=lambda _: self.stop.set()) as save:
            self.node._run(self.stop)
        save.assert_called_once_with('listener unavailable')

    def test_discovery_error_after_stop_does_not_log_or_write_status(self):
        def fail(*args, **kwargs):
            self.stop.set()
            raise sqlite3.OperationalError('disk I/O error')
        with patch.object(peer_wire, 'discover', side_effect=fail), \
                patch.object(self.node, '_discovery_status') as save, \
                self.assertNoLogs(peer_network.__name__, level='WARNING'):
            self.node._beacons(self.stop, peer_wire.PORT)
        save.assert_not_called()

    def test_running_discovery_still_reports_errors(self):
        with patch.object(peer_wire, 'discover', side_effect=PermissionError('network blocked')), \
                patch.object(self.node, '_discovery_status', side_effect=lambda _: self.stop.set()) as save, \
                self.assertLogs(peer_network.__name__, level='WARNING') as logs:
            self.node._beacons(self.stop, peer_wire.PORT)
        save.assert_called_once_with('UDP discovery on port 47622: network blocked')
        self.assertIn('UDP discovery: network blocked', logs.output[0])
