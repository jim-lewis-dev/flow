"""Dataset lookup and installation preserve caller paths and existing files."""

from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from flowledger.__main__ import database_path, main


PROJECT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.work = self.root / "working directory"
        self.work.mkdir()
        self.data = self.root / "data" / "flowledger"
        self.env = {**os.environ, "HOME": str(self.root / "home"),
                    "XDG_DATA_HOME": str(self.data.parent),
                    "XDG_CACHE_HOME": str(self.root / "cache"),
                    "FLOW_PYTHON": sys.executable,
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def run_script(self, script, *args, env=None):
        return subprocess.run([str(script), *args], cwd=self.work,
                              env=env or self.env, capture_output=True,
                              text=True, timeout=20)

    def test_name_lookup_prefers_caller_file_then_default_and_preserves_suffix(self):
        with patch.dict(os.environ, self.env), patch("pathlib.Path.cwd", return_value=self.work):
            self.assertEqual(database_path("money"), self.data / "money.sqlite3")
            self.data.mkdir(parents=True)
            (self.data / "money.sqlite3").touch()
            self.assertEqual(database_path("money"), self.data / "money.sqlite3")
            local = self.work / "money.sqlite3"
            local.write_text("Local takes precedence even when invalid.")
            self.assertEqual(database_path("money"), local)
            self.assertEqual(database_path("money.sqlite3"), local)
            self.assertEqual(database_path("trip money"), self.data / "trip money.sqlite3")
            self.assertEqual(database_path(), self.data / "flow.sqlite3")
            self.assertEqual(database_path(demo=True), self.data / "demo.sqlite3")

    def test_explicit_paths_and_db_override_do_not_fall_back(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(database_path("./money"), Path("money.sqlite3"))
            self.assertEqual(database_path(str(self.work / "trip money")), self.work / "trip money.sqlite3")
            self.assertEqual(database_path(str(self.work / "money.sqlite")), self.work / "money.sqlite")
            self.assertEqual(database_path("~/money"), Path(self.env["HOME"]) / "money.sqlite3")
            self.assertEqual(database_path(db=Path("exact-name")), Path("exact-name"))
            for name in ("", " ", ".", ".."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    database_path(name)

    def test_invalid_local_database_reports_error_without_creating_fallback(self):
        invalid = self.work / "money.sqlite3"
        invalid.write_bytes(b"This is not SQLite.")
        stderr = io.StringIO()
        gui = types.ModuleType("flowledger.gui")
        gui.FlowApp = lambda *args, **kwargs: self.fail("Invalid data reached the GUI")
        with patch.dict(os.environ, self.env), patch.dict(sys.modules, {"flowledger.gui": gui}), \
                patch("pathlib.Path.cwd", return_value=self.work), \
                patch.object(sys, "argv", ["flow", "money"]), redirect_stderr(stderr):
            self.assertEqual(main(), 1)
        self.assertIn(str(invalid), stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(invalid.read_bytes(), b"This is not SQLite.")
        self.assertFalse((self.data / "money.sqlite3").exists())

    def test_launchers_find_project_without_changing_caller_directory(self):
        # Use the real launcher and repository with a headless stand-in for Tk.
        shadow = self.work / "flowledger"
        shadow.mkdir()
        (shadow / "__init__.py").write_text('raise RuntimeError("Wrong checkout")\n')
        support = self.root / "support"
        support.mkdir()
        (support / "sitecustomize.py").write_text('''
import sys
import types
module = types.ModuleType("flowledger.gui")
class App:
    def __init__(self, repo, **kwargs):
        print(repo.path)
    def after(self, *args): return "heartbeat"
    def after_cancel(self, *args): pass
    def mainloop(self): pass
    def winfo_exists(self): return False
    def destroy(self): pass
module.FlowApp = App
sys.modules["flowledger.gui"] = module
''')
        env = {**self.env, "PYTHONPATH": str(support) + os.pathsep + self.env.get("PYTHONPATH", "")}
        local = self.work / "money.sqlite3"
        local.touch()
        for launcher in ("flow", "Flow", "launch.sh"):
            with self.subTest(launcher=launcher):
                result = self.run_script(PROJECT / launcher, "money", env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(local))
        result = self.run_script(PROJECT / "flow", "new energy", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.data / "new energy.sqlite3"))
        self.assertTrue((self.data / "new energy.sqlite3").is_file())

    def test_installer_is_explicit_idempotent_and_preserves_conflicts(self):
        target = Path(self.env["HOME"]) / ".local" / "bin" / "flow"
        result = self.run_script(PROJECT / "install.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), PROJECT / "flow")
        result = self.run_script(PROJECT / "install.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already installed", result.stdout)
        result = self.run_script(target, "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: flow", result.stdout)
        target.unlink()
        target.write_text("Unrelated command")
        result = self.run_script(PROJECT / "install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(target.read_text(), "Unrelated command")

    def test_missing_dependencies_and_conflicting_arguments_change_nothing(self):
        result = self.run_script(PROJECT / "install.sh", env={**self.env, "FLOW_PYTHON": "/bin/false"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo apt install", result.stderr)
        self.assertFalse(Path(self.env["HOME"]).exists())
        for args in (("money", "--demo"), ("money", "--db", "other"), ("--demo", "--db", "other")):
            with self.subTest(args=args):
                result = self.run_script(PROJECT / "flow", *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Choose a dataset", result.stderr)
        self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
