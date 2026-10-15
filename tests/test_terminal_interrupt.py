"""Terminal interrupts exit cleanly without saving an unadded flow draft."""

import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from flowledger.core import Repository


PROJECT = Path(__file__).resolve().parents[1]


class TerminalInterruptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "flow.sqlite3"
        self.env = {**os.environ,
                    "XDG_DATA_HOME": str(self.root / "data"),
                    "XDG_CACHE_HOME": str(self.root / "cache"),
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def spawn(self, script):
        child = subprocess.Popen(
            [sys.executable, "-B", "-u", "-c", script, "--db", str(self.db)],
            cwd=PROJECT, env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        self.addCleanup(self.stop_child, child)
        return child

    @staticmethod
    def stop_child(child):
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        if child.stdout is not None:
            child.stdout.close()
        if child.stderr is not None:
            child.stderr.close()

    def wait_ready(self, child):
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=30):
                self.fail("Flow did not finish starting within 30 seconds")
            line = child.stdout.readline()
        if line != "READY\n":
            stdout, stderr = child.communicate(timeout=5)
            self.fail(f"Flow did not reach its test ready point: {line + stdout!r}; {stderr!r}")

    def seed_records(self):
        repo = Repository(self.db)
        try:
            repo.save_account("Tank", "100", units="liters", opening_at="2026-10-01")
            repo.save_flow(dict(date="2026-10-02 12:34:56", from_account="Tank",
                                to_account="Pump", amount="2.5", units="liters",
                                status="Complete", name="Saved transfer", notes="Keep this"))
            return "\n".join(repo.conn.iterdump())
        finally:
            repo.close()

    def assert_records_unchanged(self, before):
        repo = Repository(self.db)
        try:
            self.assertEqual("\n".join(repo.conn.iterdump()), before)
        finally:
            repo.close()

    @staticmethod
    def gui_script(*, close_window=False, block_startup=False, block_transaction=False):
        if block_transaction:
            ready = '''
        def interrupted_action():
            self.repo.save_flow(dict(date="2026-12-30", from_account="Temporary source",
                                     to_account="Temporary destination", amount="9",
                                     units="liters", name="Interrupted transfer"))
            print("READY", flush=True)
            time.sleep(30)
        self.history.perform("Interrupted transfer", interrupted_action)
'''
        else:
            ready = '''        print("READY", flush=True)
'''
            if close_window:
                ready += '''        self.after(100, self.request_close)
'''
            if block_startup:
                ready += '''        time.sleep(30)
'''
        return '''
import time
import flowledger.core as core
import flowledger.gui as gui
from flowledger.__main__ import main

class CheckedRepository(core.Repository):
    def close(self):
        super().close()
        print("CLOSED", flush=True)

class ReadyApp(gui.FlowApp):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # This is a complete but unadded draft, so interrupting must not create
        # a new record. No changes are made to the selected persisted flow.
        for field, value in dict(date="2026-12-30 15:30:45", from_account="Tank",
                                 to_account="Pump", amount="7", units="liters",
                                 name="Unadded draft").items():
            self.form_vars[field].set(value)
        self.notes_text.insert("end", "Do not add this draft")
        self.after(0, self.ready)
''' + ('''        self.update()
''' if block_startup else "") + '''
    def ready(self):
''' + ready + '''
core.Repository = CheckedRepository
gui.FlowApp = ReadyApp
raise SystemExit(main())
'''

    @unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
    def test_first_sigint_stops_idle_tk_promptly_without_saving_draft(self):
        before = self.seed_records()
        child = self.spawn(self.gui_script())
        self.wait_ready(child)
        started = time.monotonic()
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=1.5)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(stdout, "CLOSED\n")
        self.assertEqual(stderr, "")
        self.assert_records_unchanged(before)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
    def test_normal_window_close_still_closes_repository_without_saving_draft(self):
        before = self.seed_records()
        child = self.spawn(self.gui_script(close_window=True))
        self.wait_ready(child)
        stdout, stderr = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(stdout, "CLOSED\n")
        self.assertEqual(stderr, "")
        self.assert_records_unchanged(before)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
    def test_sigint_in_startup_tk_callback_cleans_partial_app_without_errors(self):
        before = self.seed_records()
        child = self.spawn(self.gui_script(block_startup=True))
        self.wait_ready(child)
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=1.5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(stdout, "CLOSED\n")
        self.assertEqual(stderr, "")
        self.assert_records_unchanged(before)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
    def test_sigint_interrupts_blocked_runtime_callback_and_rolls_back_action(self):
        before = self.seed_records()
        child = self.spawn(self.gui_script(block_transaction=True))
        self.wait_ready(child)
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=1.5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(stdout, "CLOSED\n")
        self.assertEqual(stderr, "")
        self.assert_records_unchanged(before)

    def test_sigint_during_gui_import_exits_cleanly_before_database_creation(self):
        child = self.spawn('''
import importlib.abc
import sys
import time
from flowledger.__main__ import main

class SlowGuiImport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "flowledger.gui":
            print("READY", flush=True)
            time.sleep(30)
        return None

sys.meta_path.insert(0, SlowGuiImport())
raise SystemExit(main())
''')
        self.wait_ready(child)
        child.send_signal(signal.SIGINT)
        stdout, stderr = child.communicate(timeout=1.5)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
