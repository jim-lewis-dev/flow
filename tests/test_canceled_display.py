"""Status migration and display precision preserve the saved resource values."""
from decimal import Decimal, Inexact, localcontext
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository, STATUSES, ledger_balance_at
from flowledger.exchange import preview_import, commit_import
from flowledger.graphing import graph_timeline
from flowledger.units import decimal_places, format_display, UNIT_DECIMAL_PLACES


class CanceledDisplayTests(unittest.TestCase):
    def test_decimal_settings_format_without_changing_exact_values(self):
        self.assertEqual(decimal_places("USD"), 2)
        self.assertEqual(decimal_places("kg"), 4)
        for value, unit, displayed in [
            ("12", "USD", "12.00"), ("1234.56789", "USD", "1,234.57"),
            (".125", "kg", "0.1250"), (".123456789", "kg", "0.1235"),
            ("-.00001", "USD", "0.00"), ("-12.5", "USD", "-12.50"),
        ]:
            self.assertEqual(format_display(value, unit), displayed)
        exact = Decimal("123456789012345678901234567890.125")
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            self.assertEqual(format_display(exact, "kg"), "123,456,789,012,345,678,901,234,567,890.1250")
        self.assertEqual(exact, Decimal("123456789012345678901234567890.125"))
        with patch.dict(UNIT_DECIMAL_PLACES, {"mW": 6}):
            self.assertEqual(format_display(".125", "mW"), "0.125000")
            self.assertEqual(format_display(".125", "MW"), "0.1250")

    def test_canceled_never_affects_any_balance_or_graph_basis(self):
        repo = Repository(":memory:")
        self.addCleanup(repo.close)
        account = repo.save_account("A", "10", "2026-10-01", units="kg")
        saved = repo.save_flow(dict(date="2026-10-02", from_account="A", to_account="B",
                                   amount=".125123456", units="kg", status="Canceled"))
        self.assertEqual(STATUSES, ("Complete", "Pending", "Planned", "Canceled"))
        self.assertEqual(ledger_balance_at(account, [saved], "2026-10-03", STATUSES), 10)
        rows = graph_timeline([account], [saved], "2026-10-01", "2026-10-03", STATUSES)
        self.assertTrue(all(value == 10 for _, value in rows["A", "kg"]))
        changed = repo.save_flow({**saved, "status": "Complete"})
        self.assertEqual(ledger_balance_at(account, [changed], "2026-10-03"), Decimal("9.874876544"))

    def test_old_export_status_maps_to_canceled_without_changing_other_fields(self):
        repo = Repository(":memory:")
        self.addCleanup(repo.close)
        row = dict(date="2026-10-02 01:02:03", from_account="A", to_account="B",
                   amount=".125123456", units="kg", status="Void", name="Old file",
                   category="Material", notes="First\nsecond", tags=["Boat", "Work"])
        preview = preview_import(repo, [row])
        self.assertEqual(preview[0]["state"], "New")
        commit_import(repo, preview)
        saved, = repo.list_flows()
        self.assertEqual(saved["status"], "Canceled")
        for field in ("date", "units", "name", "category", "notes", "tags"):
            self.assertEqual(saved[field], row[field])
        self.assertEqual(saved["amount"], "0.125123456")

    def make_v5(self, path):
        schema = Path("flowledger/schema.sql").read_text().replace("user_version = 6", "user_version = 5").replace("'Canceled'", "'Void'")
        with sqlite3.connect(path) as conn:
            conn.executescript(schema)
            conn.executemany("INSERT INTO accounts VALUES(?,?)", [("a","A"),("b","B")])
            conn.executemany("INSERT INTO account_balances VALUES(?,?,?,?)",
                [("a","kg","10.123456789","2026-10-01 01:02:03"),("b","kg",None,"2026-10-01")])
            conn.execute("INSERT INTO flows VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("00000000-0000-0000-0000-000000000001","2026-10-02 03:04:05","a","b",
                 "0.125123456","kg","Void","Saved row","Notes\ncafé","uncategorized"))
            conn.execute("INSERT INTO tags VALUES('boat','Boat')")
            conn.execute("INSERT INTO flow_tags VALUES('00000000-0000-0000-0000-000000000001','boat',0)")
            conn.execute("INSERT INTO audit VALUES(1,'2026-10-02','flow','old','create',NULL,?)",
                         (json.dumps({"status":"Void","amount":"0.125123456"}),))

    def test_v5_status_upgrade_preserves_all_other_values_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"old.sqlite3"
            self.make_v5(path)
            with sqlite3.connect(path) as conn:
                before = list(conn.iterdump())
                audit = conn.execute("SELECT * FROM audit").fetchall()
            repo = Repository(path)
            saved, = repo.list_flows()
            self.assertEqual(saved["status"], "Canceled")
            self.assertEqual(saved["flow_id"], "00000000-0000-0000-0000-000000000001")
            self.assertEqual((saved["amount"],saved["units"],saved["date"],saved["notes"],saved["tags"]),
                ("0.125123456","kg","2026-10-02 03:04:05","Notes\ncafé",["Boat"]))
            self.assertEqual(repo.get_account("A","kg")["opening_amount"], "10.123456789")
            self.assertEqual([tuple(row) for row in repo.conn.execute("SELECT * FROM audit")], audit)
            self.assertEqual(repo.conn.execute("PRAGMA user_version").fetchone()[0],6)
            self.assertFalse(repo.conn.execute("PRAGMA foreign_key_check").fetchall())
            self.assertIsNone(repo.migration_backup_path)
            repo.close()
            repo = Repository(path)
            self.assertIsNone(repo.migration_backup_path)
            self.assertEqual(repo.list_flows(),[saved])
            repo.close()
            self.assertEqual(list(Path(directory).iterdir()),[path])

    def test_failed_status_upgrade_rolls_back_records_schema_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"old.sqlite3"
            self.make_v5(path)
            with sqlite3.connect(path) as conn:
                before=list(conn.iterdump())
            validate=Repository._validate_database
            def fail(conn, **kwargs):
                if conn.execute("PRAGMA user_version").fetchone()[0]==6:
                    raise ValueError("Migration test failure")
                return validate(conn,**kwargs)
            with patch.object(Repository,"_validate_database",side_effect=fail):
                with self.assertRaisesRegex(ValueError,"Migration test failure"):
                    Repository(path)
            with sqlite3.connect(path) as conn:
                self.assertEqual(list(conn.iterdump()),before)
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],5)
            self.assertEqual(list(Path(directory).iterdir()),[path])

    def test_current_database_without_void_records_upgrades_without_extra_files(self):
        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/"old.sqlite3"
                self.make_v5(path)
                with sqlite3.connect(path) as conn:
                    conn.execute("UPDATE flows SET status='Complete'")
                    if empty:
                        conn.execute("DELETE FROM flow_tags")
                        conn.execute("DELETE FROM flows")
                    before=conn.execute("SELECT * FROM flows").fetchall()
                repo=Repository(path)
                self.assertEqual([tuple(row) for row in repo.conn.execute("SELECT * FROM flows")],before)
                self.assertIsNone(repo.migration_backup_path)
                self.assertEqual(repo.conn.execute("PRAGMA user_version").fetchone()[0],6)
                repo.close()
                self.assertEqual(list(Path(directory).iterdir()),[path])
