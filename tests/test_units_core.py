"""Exact resource quantities, independent unit balances and legacy USD recovery."""
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import (Repository, decimal_sum, format_quantity, ledger_balance_at,
                             ledger_timeline, parse_quantity, quantity_text)
from flowledger.history import SessionHistory


class QuantityTests(unittest.TestCase):
    def test_plain_exact_quantities_have_no_currency_precision_limit(self):
        examples = {".125": "0.125", "+.000000000000000000000000000001": "0.000000000000000000000000000001",
                    "1,234,567.000500": "1234567.0005", "00012.3400": "12.34", "-.050": "-0.05",
                    "-0.000": "0", "1234567890123456789012345678901234567890.0123456789":
                    "1234567890123456789012345678901234567890.0123456789"}
        for text, canonical in examples.items():
            with self.subTest(text=text):
                value = parse_quantity(text, allow_negative=True)
                self.assertEqual(quantity_text(value), canonical)
                self.assertEqual(parse_quantity(format_quantity(value), True), value)
        self.assertEqual(format_quantity("1234567.0005"), "1,234,567.0005")

    def test_quantity_parser_rejects_ambiguous_or_lossy_inputs(self):
        for value in (True, 1.25, None, {}, "", "$12.00", "1e4", "NaN", "Infinity", "1,2", ".", "1.", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_quantity(value)
        for value in (Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_quantity(value)

    def test_addition_is_exact_under_an_unhelpful_decimal_context(self):
        giant = "1234567890123456789012345678901234567890"
        tiny = "0.000000000000000000000000000001"
        with localcontext() as context:
            context.prec = 3
            result = decimal_sum([giant, tiny, "0.125"])
            self.assertEqual(quantity_text(result), giant + ".125000000000000000000000000001")
            self.assertEqual(quantity_text(decimal_sum([result, Decimal(giant).copy_negate()])),
                             "0.125000000000000000000000000001")
            self.assertEqual(decimal_sum([]), Decimal(0))


class UnitRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")
        self.history = SessionHistory(self.repo)
        self.addCleanup(self.repo.close)
        self.addCleanup(self.history.close)

    def flow(self, **changes):
        return self.repo.save_flow({"date": "2026-10-02", "from_account": "Store", "to_account": "Home",
                                    "amount": ".125", "units": "kg", **changes})

    def test_units_are_required_and_invalid_writes_are_atomic(self):
        before = list(self.repo.conn.iterdump())
        for units in (None, "", "   ", 1, "a\x00b"):
            with self.subTest(units=units), self.assertRaises(ValueError):
                self.flow(units=units)
            self.assertEqual(list(self.repo.conn.iterdump()), before)
        with self.assertRaises(ValueError):
            self.repo.save_flow({"from_account": "Store", "to_account": "Home", "amount": "1"})
        self.assertEqual(list(self.repo.conn.iterdump()), before)

    def test_saved_quantities_are_canonical_strings_and_only_units_is_added(self):
        saved = self.flow(amount="000.12500", units=" kg ")
        self.assertEqual(saved["amount"], "0.125")
        self.assertEqual(saved["units"], "kg")
        self.assertEqual(set(saved), {"flow_id", "date", "from_account", "to_account", "amount", "units",
                                      "status", "name", "notes", "category", "tags"})
        raw = self.repo.conn.execute("SELECT typeof(amount),amount,units FROM flows").fetchone()
        self.assertEqual(tuple(raw), ("text", "0.125", "kg"))

    def test_accounts_hold_separate_starting_balances_without_unit_case_folding(self):
        usd = self.repo.save_account(" Cash ", "1,234.50", "2026-10-01", "USD")
        kg = self.repo.save_account("cash", ".125", "2026-10-03 7", "kg")
        self.assertEqual(usd, {"name": "Cash", "units": "USD", "opening_amount": "1234.5",
                               "opening_at": "2026-10-01 00:00:00"})
        self.assertEqual(kg["name"], "Cash")
        self.assertEqual(self.repo.get_account("CASH", "USD"), usd)
        self.assertEqual(self.repo.get_account("Cash", "kg"), kg)
        self.assertIsNone(self.repo.get_account("Cash", "KG"))
        self.repo.save_account("Cash", "2", units="mW")
        self.repo.save_account("Cash", "3", units="MW")
        self.assertEqual(self.repo.get_account("Cash", "mW")["opening_amount"], "2")
        self.assertEqual(self.repo.get_account("Cash", "MW")["opening_amount"], "3")
        self.assertEqual(self.repo.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
        self.assertEqual(self.repo.list_units(), ["MW", "USD", "kg", "mW"])

    def test_displayed_account_pairs_derive_from_flows_including_void(self):
        self.repo.save_account("Store", "100", units="USD")
        self.repo.save_account("Store", "5", units="kg")
        saved = self.flow(status="Canceled")
        self.assertEqual([(a["name"], a["units"]) for a in self.repo.list_accounts(referenced_only=True)],
                         [("Home", "kg"), ("Store", "kg")])
        self.repo.conn.execute("DELETE FROM flows WHERE flow_id=?", (saved["flow_id"],))
        self.assertEqual(self.repo.list_accounts(referenced_only=True), [])
        self.assertEqual(self.repo.get_account("Store", "kg")["opening_amount"], "5")
        self.assertEqual(self.repo.get_account("Store", "USD")["opening_amount"], "100")

    def test_unknown_balance_in_one_unit_does_not_borrow_another_unit_anchor(self):
        self.repo.save_account("Store", "10", units="USD")
        saved = self.flow()
        self.assertIsNone(self.repo.get_account("Store", "kg")["opening_amount"])
        self.assertIsNone(ledger_balance_at(self.repo.get_account("Store", "kg"), [saved], "2026-10-03"))
        self.assertEqual(ledger_balance_at(self.repo.get_account("Store", "USD"), [saved], "2026-10-03"), 10)

    def test_flows_cannot_reference_a_missing_matching_unit_balance(self):
        self.flow()
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.conn.execute("UPDATE flows SET units='USD'")
        self.assertEqual(self.repo.list_flows()[0]["units"], "kg")

    def test_unit_edit_undo_restores_record_and_new_endpoint_anchors(self):
        saved = self.history.perform("Add flow", self.flow)
        before = list(self.repo.conn.iterdump())
        changed = self.history.perform("Edit unit", lambda: self.repo.save_flow({**saved, "units": "liters"}))
        self.assertEqual(changed["flow_id"], saved["flow_id"])
        self.assertEqual(self.repo.list_units(), ["kg", "liters"])
        self.history.undo()
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.assertIsNone(self.repo.get_account("Store", "liters"))
        self.assertEqual(self.repo.list_units(), ["kg"])
        self.history.redo()
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), changed)
        self.assertIsNone(self.repo.conn.execute("PRAGMA foreign_key_check").fetchone())
        self.assertGreater(len(list(self.repo.conn.iterdump())), len(before))

    def test_anchor_edit_undo_is_scoped_to_its_account_unit_pair(self):
        self.repo.save_account("Store", "100", units="USD")
        original = self.repo.save_account("Store", "1.125", units="kg")
        changed = self.history.perform("Edit starting balance", lambda: self.repo.save_account("Store", "2.375", units="kg"))
        self.history.undo()
        self.assertEqual(self.repo.get_account("Store", "kg"), original)
        self.assertEqual(self.repo.get_account("Store", "USD")["opening_amount"], "100")
        self.history.redo()
        self.assertEqual(self.repo.get_account("Store", "kg"), changed)

    def test_same_named_account_graphs_and_pending_effects_are_separate_by_unit(self):
        usd = self.repo.save_account("Store", "100", "2026-10-03", "USD")
        kg = self.repo.save_account("Store", "1.125", "2026-10-03", "kg")
        flows = [self.flow(date="2026-10-01", amount="20", units="USD", status="Pending"),
                 self.flow(date="2026-10-02", amount=".125", units="kg", status="Planned"),
                 self.flow(date="2026-10-04", amount="5", units="USD"),
                 self.flow(date="2026-10-04", amount=".25", units="kg")]
        statuses = ("Complete", "Pending", "Planned")
        self.assertEqual(ledger_balance_at(usd, flows, "2026-10-05", statuses), 75)
        self.assertEqual(ledger_balance_at(kg, flows, "2026-10-05", statuses), Decimal(".75"))
        graph = ledger_timeline([usd, kg], flows, "2026-10-01", "2026-10-05", statuses)
        self.assertEqual(set(graph), {("Store", "USD"), ("Store", "kg")})
        for account in (usd, kg):
            for at, balance in graph[account["name"], account["units"]]:
                self.assertEqual(balance, ledger_balance_at(account, flows, at, statuses))

    def test_resource_balances_remain_exact_beyond_ambient_decimal_precision(self):
        giant = "1234567890123456789012345678901234567890"
        tiny = "0.000000000000000000000000000001"
        self.repo.save_account("Store", giant, units="kg")
        self.flow(amount=tiny)
        with localcontext() as context:
            context.prec = 2
            balance = ledger_balance_at(self.repo.get_account("Store", "kg"), self.repo.list_flows(), "2026-10-03")
            self.assertEqual(quantity_text(balance), "1234567890123456789012345678901234567889.999999999999999999999999999999")


V4_SCHEMA = """
PRAGMA application_id=1179406167;
PRAGMA user_version=4;
CREATE TABLE accounts(account_key TEXT PRIMARY KEY,name TEXT NOT NULL,opening_cents INTEGER,opening_at TEXT NOT NULL);
CREATE TABLE categories(category_key TEXT PRIMARY KEY,name TEXT NOT NULL);
CREATE TABLE tags(tag_key TEXT PRIMARY KEY,name TEXT NOT NULL);
CREATE TABLE flows(flow_id TEXT PRIMARY KEY,date TEXT NOT NULL,from_key TEXT NOT NULL REFERENCES accounts(account_key),
    to_key TEXT NOT NULL REFERENCES accounts(account_key),amount_cents INTEGER NOT NULL,status TEXT NOT NULL,name TEXT NOT NULL,
    notes TEXT NOT NULL,category_key TEXT NOT NULL REFERENCES categories(category_key));
CREATE TABLE flow_tags(flow_id TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,tag_key TEXT NOT NULL REFERENCES tags(tag_key),
    position INTEGER NOT NULL,PRIMARY KEY(flow_id,tag_key));
CREATE TABLE audit(audit_id INTEGER PRIMARY KEY,changed_at TEXT NOT NULL,entity TEXT NOT NULL,entity_key TEXT NOT NULL,
    action TEXT NOT NULL,before_json TEXT,after_json TEXT);
CREATE INDEX flows_date ON flows(datetime(date),flow_id);
CREATE INDEX flows_from ON flows(from_key,date);
CREATE INDEX flows_to ON flows(to_key,date);
"""


class UnitMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def old_database(self):
        path = self.root / "old.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.executescript(V4_SCHEMA)
            conn.execute("INSERT INTO accounts VALUES('cash','Cash',-9223372036854775807,'2026-10-01 00:00:00')")
            conn.execute("INSERT INTO accounts VALUES('shop','Shop',NULL,'2026-10-02 03:04:05')")
            conn.execute("INSERT INTO categories VALUES('uncategorized','Uncategorized')")
            conn.execute("INSERT INTO tags VALUES('family','Family')")
            conn.execute("INSERT INTO flows VALUES('00000000-0000-0000-0000-000000000001','2026-10-02','cash','shop',"
                         "9223372036854775807,'Complete','Supply','Keep café notes','uncategorized')")
            conn.execute("INSERT INTO flow_tags VALUES('00000000-0000-0000-0000-000000000001','family',0)")
            conn.execute("INSERT INTO audit VALUES(1,'2026-10-02','flow','old','create',NULL,?)",
                         (json.dumps({"amount_cents": 9223372036854775807}),))
        return path

    @staticmethod
    def snapshot(path):
        with sqlite3.connect(path) as conn:
            return list(conn.iterdump())

    def test_v4_migration_is_exact_preserves_identity_and_historical_audit(self):
        path = self.old_database()
        original = self.snapshot(path)
        repo = Repository(path)
        saved = repo.list_flows()[0]
        self.assertEqual(saved["flow_id"], "00000000-0000-0000-0000-000000000001")
        self.assertEqual((saved["amount"], saved["units"]), ("92233720368547758.07", "USD"))
        self.assertEqual((saved["date"], saved["notes"], saved["tags"]), ("2026-10-02", "Keep café notes", ["Family"]))
        self.assertEqual(repo.get_account("Cash")["opening_amount"], "-92233720368547758.07")
        self.assertIsNone(repo.get_account("Shop")["opening_amount"])
        self.assertEqual(json.loads(repo.history()[0]["after_json"]), {"amount_cents": 9223372036854775807})
        self.assertEqual(repo.conn.execute("PRAGMA user_version").fetchone()[0], 6)
        self.assertIsNone(repo.conn.execute("PRAGMA foreign_key_check").fetchone())
        self.assertEqual(self.snapshot(repo.migration_backup_path), original)
        backup = repo.migration_backup_path
        repo.close()
        reopened = Repository(path)
        self.addCleanup(reopened.close)
        self.assertIsNone(reopened.migration_backup_path)
        self.assertEqual(reopened.list_flows(), [saved])
        self.assertEqual(list(self.root.glob("*.pre-v6-*.bak")), [Path(backup)])

    def test_failed_v4_migration_rolls_back_all_data_and_retains_original_backup(self):
        path = self.old_database()
        original = self.snapshot(path)
        validate = Repository._validate_database
        def fail_at_v5(conn, **kwargs):
            if conn.execute("PRAGMA user_version").fetchone()[0] == 6:
                raise ValueError("deliberate upgrade failure")
            validate(conn, **kwargs)
        with patch.object(Repository, "_validate_database", side_effect=fail_at_v5):
            with self.assertRaisesRegex(ValueError, "deliberate"):
                Repository(path)
        self.assertEqual(self.snapshot(path), original)
        backups = list(self.root.glob("*.pre-v6-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.snapshot(backups[0]), original)

    def test_restore_old_backup_migrates_private_copy_and_preserves_source(self):
        source = self.old_database()
        source_hash = hashlib.sha256(source.read_bytes()).digest()
        active = self.root / "active.sqlite3"
        repo = Repository(active)
        self.addCleanup(repo.close)
        old_anchor = repo.save_account("Tank", ".125", units="liters")
        repo.restore(source)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), source_hash)
        self.assertEqual(repo.list_flows()[0]["units"], "USD")
        self.assertEqual(repo.list_units(), ["USD"])
        safety = list(self.root.glob("active.sqlite3.pre-restore-*.bak"))
        self.assertEqual(len(safety), 1)
        previous = Repository(safety[0])
        self.addCleanup(previous.close)
        self.assertEqual(previous.list_accounts(), [old_anchor])

    def test_reopening_rejects_invalid_persisted_decimal_without_changing_it(self):
        path = self.root / "invalid.sqlite3"
        repo = Repository(path)
        flow = repo.save_flow({"from_account": "Tank", "to_account": "Glass", "amount": ".125", "units": "liters"})
        repo.conn.execute("UPDATE flows SET amount='NaN' WHERE flow_id=?", (flow["flow_id"],))
        repo.close()
        before = self.snapshot(path)
        with self.assertRaises(ValueError):
            Repository(path)
        self.assertEqual(self.snapshot(path), before)


if __name__ == "__main__":
    unittest.main()
