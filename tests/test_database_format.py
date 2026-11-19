"""Current database files open unchanged; unsupported formats are rejected."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from flowledger.core import APPLICATION_ID, SCHEMA_VERSION, Repository, ledger_balance_at


class DatabaseFormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_new_accounts_have_only_anchor_fields_and_no_hidden_visibility(self):
        repo = Repository(self.root / "new.sqlite3")
        self.addCleanup(repo.close)
        saved = repo.save_account(" Cash ", "10", "2026-10-01")
        self.assertEqual(saved, {"name": "Cash", "units": "USD", "opening_amount": "10",
                                 "opening_at": "2026-10-01 00:00:00"})
        flow = repo.save_flow({"from_account": "Cash", "to_account": "Shop", "amount": "1", "units": "USD"})
        self.assertEqual([row["name"] for row in repo.list_accounts()], ["Cash", "Shop"])
        self.assertIsNone(repo.get_account("Shop")["opening_amount"])
        self.assertIsNone(ledger_balance_at(repo.get_account("Shop"), repo.list_flows(), flow["date"]))
        self.assertEqual(set(json.loads(repo.history()[-2]["after_json"])), set(saved))
        self.assertEqual({row[1] for row in repo.conn.execute("PRAGMA table_info(accounts)")},
                         {"account_key", "name"})
        self.assertEqual({row[1] for row in repo.conn.execute("PRAGMA table_info(account_balances)")},
                         {"account_key", "units", "opening_amount", "opening_at"})

    def test_unsupported_versions_cannot_open_or_replace_current_data(self):
        active = Repository(self.root / "active.sqlite3")
        self.addCleanup(active.close)
        active.save_flow(dict(from_account="A", to_account="B", amount=".25", units="kg"))
        active.save_map_positions({"A": (-0.5, 1.4)})
        before = list(active.conn.iterdump())
        for version in (*range(1, SCHEMA_VERSION), SCHEMA_VERSION + 1):
            with self.subTest(version=version):
                source = self.root / f"version-{version}.sqlite3"
                active.backup(source)
                with sqlite3.connect(source) as conn:
                    conn.execute(f"PRAGMA user_version={version}")
                original = source.read_bytes()
                files = set(self.root.iterdir())
                with self.assertRaisesRegex(ValueError, "Unsupported database version"):
                    Repository(source)
                with self.assertRaisesRegex(ValueError, "Unsupported database version"):
                    active.restore(source)
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual(list(active.conn.iterdump()), before)
                self.assertEqual(set(self.root.iterdir()), files)

    def test_empty_unsupported_file_is_not_initialized_as_a_new_file(self):
        path = self.root / "unsupported.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION - 1}")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported database version"):
            Repository(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_invalid_persisted_quantity_is_rejected_without_changes(self):
        path = self.root / "invalid.sqlite3"
        repo = Repository(path)
        saved = repo.save_flow(dict(from_account="Tank", to_account="Glass", amount=".125", units="liters"))
        repo.conn.execute("UPDATE flows SET amount='NaN' WHERE flow_id=?", (saved["flow_id"],))
        repo.close()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            Repository(path)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
