"""Canceled flows and display precision preserve the saved resource values."""
from decimal import Decimal, Inexact, localcontext
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

    def test_unsupported_export_status_is_rejected_without_changes(self):
        repo = Repository(":memory:")
        self.addCleanup(repo.close)
        row = dict(date="2026-10-02 01:02:03", from_account="A", to_account="B",
                   amount=".125123456", units="kg", status="Void", name="Old file",
                   category="Material", notes="First\nsecond", tags=["Boat", "Work"])
        before = list(repo.conn.iterdump())
        preview = preview_import(repo, [row])
        self.assertEqual(preview[0]["state"], "Error")
        self.assertIn("status must be", preview[0]["message"])
        with self.assertRaisesRegex(ValueError, "status must be"):
            commit_import(repo, preview)
        self.assertEqual(list(repo.conn.iterdump()), before)
