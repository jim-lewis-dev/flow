"""A developer's saved peers must never be loaded by the GUI test suite."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gui_test_support import IsolatedGuiTestCase


@unittest.skipUnless(os.environ.get('DISPLAY'), 'A graphical DISPLAY is required')
class GuiIsolationTests(unittest.TestCase):
    def test_saved_catalog_stays_outside_the_complete_test_lifecycle(self):
        from flowledger.core import Repository
        from flowledger.gui import FlowApp

        phases = []

        class Probe(IsolatedGuiTestCase):
            def record_phase(self, phase):
                phases.append((phase, os.environ['XDG_CONFIG_HOME'], os.environ['XDG_DATA_HOME']))

            def setUp(self):
                self.record_phase('setup')
                self.addCleanup(self.record_phase, 'cleanup')
                root = Path(os.environ['XDG_DATA_HOME'])
                root.mkdir(parents=True)
                self.repo = Repository(root / 'temporary.sqlite3')
                self.addCleanup(self.repo.close)
                self.app = FlowApp(self.repo)
                self.addCleanup(self.app.destroy)

            def test_app(self):
                self.record_phase('test')
                self.app.update()
                self.assertIsNone(self.app.sync_controller.peer.worker.thread)
                self.assertFalse((Path(os.environ['XDG_CONFIG_HOME']) / 'flow/peers/devices.sqlite3').exists())

            def tearDown(self):
                self.record_phase('teardown')

        with TemporaryDirectory() as directory:
            outside = Path(directory)
            catalog = outside / 'settings/flow/peers/devices.sqlite3'
            catalog.parent.mkdir(parents=True)
            catalog.write_bytes(b'Existing device catalog: do not open or modify')
            original = {
                'XDG_CONFIG_HOME': str(outside / 'settings'),
                'XDG_DATA_HOME': str(outside / 'data')
            }
            with patch.dict(os.environ, original):
                result = unittest.TestResult()
                Probe('test_app').run(result)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual([phase for phase, _, _ in phases], ['setup', 'test', 'teardown', 'cleanup'])
                locations = {(settings, data) for _, settings, data in phases}
                self.assertEqual(len(locations), 1)
                settings, data = locations.pop()
                self.assertNotEqual(settings, original['XDG_CONFIG_HOME'])
                self.assertNotEqual(data, original['XDG_DATA_HOME'])
                self.assertFalse(Path(settings).parent.exists())
                for key, value in original.items():
                    self.assertEqual(os.environ[key], value)
                self.assertEqual(catalog.read_bytes(), b'Existing device catalog: do not open or modify')
