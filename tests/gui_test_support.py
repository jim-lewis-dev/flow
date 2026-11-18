"""Keep GUI tests away from the user's device catalog and saved files."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class IsolatedGuiTestCase(unittest.TestCase):
    def run(self, result=None):
        # Cover construction, callbacks, tearDown and addCleanup, including
        # subprocesses. A real saved device catalog must never resume here.
        with TemporaryDirectory(prefix='flow-gui-test-') as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                'XDG_CONFIG_HOME': str(root / 'settings'),
                'XDG_DATA_HOME': str(root / 'data')
            }):
                return super().run(result)
