"""Signed and zero flows preserve identity and exact resource accounting."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository, decimal_sum, ledger_balance_at
from flowledger.exchange import commit_import, export_flows, preview_import, read_import
from flowledger.graphing import graph_timeline
from flowledger.history import SessionHistory
from flowledger.selection_summary import format_selection_summary


class SignedQuantityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "flows.sqlite3"
        self.repo = Repository(self.path)
        self.addCleanup(lambda: self.repo.close())

    def flow(self, **changes):
        return self.repo.save_flow({"date": "2026-10-03", "from_account": "A", "to_account": "B",
                                    "amount": "1", "units": "USD", "name": "Transfer",
                                    "notes": "", "tags": [], **changes})

    def test_signed_edit_zero_comment_and_undo_preserve_fields_and_identity(self):
        history = SessionHistory(self.repo)
        self.addCleanup(history.close)
        original = self.flow(amount="12.34", notes="Correction", category="Supplies", tags=["Project"])
        negative = history.perform("Edit Flow", lambda: self.repo.save_flow({**original, "amount": "-12.3400"}))
        self.assertEqual(negative, {**original, "amount": "-12.34"})
        comment = history.perform("Edit Flow", lambda: self.repo.save_flow({**negative, "amount": "-.000"}))
        self.assertEqual(comment, {**original, "amount": "0"})
        history.undo()
        self.assertEqual(self.repo.get_flow(original["flow_id"]), negative)
        history.redo()
        self.assertEqual(self.repo.get_flow(original["flow_id"]), comment)
        self.assertEqual(self.repo.conn.execute("SELECT amount FROM flows").fetchone()[0], "0")

    def test_same_account_flows_keep_balances_flat_and_undo_preserves_records(self):
        account = self.repo.save_account("A", "123.456", "2026-10-03", "kg")
        history = SessionHistory(self.repo)
        self.addCleanup(history.close)
        for amount, status, date in (("5", "Complete", "2026-10-01"),
                                     ("-2", "Pending", "2026-10-03"),
                                     ("0", "Planned", "2026-10-04")):
            with self.subTest(amount=amount, status=status):
                saved = history.perform("Add Flow", lambda: self.flow(
                    to_account=" a ", amount=amount, status=status, date=date,
                    units="kg", category="Inspection", tags=["Check"], notes="Same account"))
                self.assertEqual((saved["from_account"], saved["to_account"]), ("A", "A"))
                history.undo()
                self.assertIsNone(self.repo.get_flow(saved["flow_id"]))
                history.redo()
                self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        flows = self.repo.list_flows()
        statuses = ("Complete", "Pending", "Planned")
        for when in ("2026-09-30", "2026-10-03", "2026-10-05"):
            self.assertEqual(ledger_balance_at(account, flows, when, statuses), Decimal("123.456"))
        for combined in (False, True):
            points = graph_timeline([account], flows, "2026-10-01", "2026-10-05",
                                    statuses, combined=combined)
            self.assertTrue(all(value == Decimal("123.456") for rows in points.values()
                                for _, value in rows))
        summary = format_selection_summary(flows)
        self.assertIn("From\n  A: 3 kg", summary)
        self.assertIn("To\n  A: 3 kg", summary)
        self.assertIn("Categories\n  Inspection: 3 kg", summary)

    def test_signed_flows_conserve_each_unit_with_confirmed_and_forecast_bases(self):
        for name, usd, kg in (("A", "100", "5"), ("B", "0", "2")):
            self.repo.save_account(name, usd, "2026-10-03", "USD")
            self.repo.save_account(name, kg, "2026-10-03", "kg")
        self.flow(date="2026-10-01", amount="-10", status="Pending")
        self.flow(date="2026-10-02", amount="-.125", units="kg", status="Planned")
        self.flow(amount="-4")
        self.flow(date="2026-10-04", amount=".5", units="kg")
        self.flow(date="2026-10-04", amount="0", notes="Inventory inspected")
        self.flow(date="2026-10-04", amount="-999", status="Canceled")
        flows = self.repo.list_flows()
        bases = (("Complete",), ("Complete", "Pending"), ("Complete", "Pending", "Planned", "Canceled"))
        expected = ((Decimal("104"), Decimal("4.5")),
                    (Decimal("114"), Decimal("4.5")),
                    (Decimal("114"), Decimal("4.625")))
        for statuses, (usd, kg) in zip(bases, expected):
            with self.subTest(statuses=statuses):
                for unit, value, total in (("USD", usd, Decimal("100")), ("kg", kg, Decimal("7"))):
                    accounts = [self.repo.get_account(name, unit) for name in ("A", "B")]
                    balances = [ledger_balance_at(account, flows, "2026-10-05", statuses) for account in accounts]
                    self.assertEqual(balances[0], value)
                    self.assertEqual(decimal_sum(balances), total)
                    points = graph_timeline(accounts, flows, "2026-10-01", "2026-10-05", statuses)
                    for account in accounts:
                        for when, balance in points[account["name"], unit]:
                            # The graph prepends a BEFORE value only at a start event.
                            if when != "2026-10-01 00:00:00":
                                self.assertEqual(balance, ledger_balance_at(account, flows, when, statuses))

    def test_graph_start_signed_and_zero_events_keep_both_sides_and_combined_totals(self):
        accounts = [self.repo.save_account("A", "10", units="kg"),
                    self.repo.save_account("B", "2", units="kg")]
        flows = [self.flow(date="2026-10-01", amount="-3", units="kg"),
                 self.flow(date="2026-10-01", amount="0", units="kg", notes="Opening check"),
                 self.flow(date="2026-10-02", amount="2", units="kg")]
        original = deepcopy((accounts, flows))
        points = graph_timeline(accounts, flows, "2026-10-01", "2026-10-03")
        self.assertEqual([value for _, value in points["A", "kg"]], [10, 13, 11, 11])
        self.assertEqual([value for _, value in points["B", "kg"]], [2, -1, 1, 1])
        combined = graph_timeline(accounts, flows, "2026-10-01", "2026-10-03", combined=True)
        self.assertEqual([value for _, value in combined["Combined", "kg"]], [12, 12, 12, 12])
        self.assertEqual((accounts, flows), original)

    def test_reopen_backup_restore_and_portable_exports_accept_signed_and_zero_records(self):
        self.flow(amount="-12345678901234567890.123456789", units="kg", notes="Return")
        self.flow(amount="0", units="kg", to_account="A", notes="Inspection", name="Comment")
        self.repo.save_account("A", "-1", units="kg")
        original = self.repo.list_flows()
        schema = self.repo.conn.execute("PRAGMA user_version").fetchone()[0]
        self.repo.close()
        self.repo = Repository(self.path)
        self.assertEqual(self.repo.list_flows(), original)
        self.assertIsNone(self.repo.migration_backup_path)
        backup = Path(self.directory.name) / "snapshot.sqlite3"
        self.repo.backup(backup)
        self.repo.save_flow({**original[0], "amount": "7"})
        self.repo.restore(backup)
        self.assertEqual(self.repo.list_flows(), original)
        self.assertEqual(self.repo.get_account("A", "kg")["opening_amount"], "-1")
        self.assertEqual(self.repo.conn.execute("PRAGMA user_version").fetchone()[0], schema)
        for extension in ("csv", "json"):
            with self.subTest(extension=extension):
                path = Path(self.directory.name) / f"flows.{extension}"
                self.assertEqual(export_flows(self.repo, path), 2)
                imported = Repository(":memory:")
                try:
                    preview = preview_import(imported, read_import(path))
                    self.assertEqual([row["state"] for row in preview], ["New", "New"])
                    self.assertEqual(commit_import(imported, preview), {"imported": 2, "skipped": 0})
                    self.assertEqual(imported.list_flows(), original)
                finally:
                    imported.close()

    def test_import_identity_keeps_sign_and_endpoints_while_normalizing_decimal_text(self):
        negative = self.flow(amount="-3", name="Adjustment")
        zero = self.flow(amount="0", name="Inspection")
        rows = [{**negative, "amount": "-03.000"}, {**zero, "amount": "-.000"},
                {**negative, "amount": "3"},
                {key: value for key, value in {**negative, "from_account": "B", "to_account": "A", "amount": "3"}.items()
                 if key != "flow_id"}]
        preview = preview_import(self.repo, rows)
        self.assertEqual([item["state"] for item in preview], ["Duplicate", "Duplicate", "Conflict", "New"])
        self.assertEqual(preview[0]["data"], negative)
        self.assertEqual(preview[1]["data"], zero)


if __name__ == "__main__":
    unittest.main()
