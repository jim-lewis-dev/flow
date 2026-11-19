"""Exact resource quantities and independent unit balances."""
from decimal import Decimal, localcontext
import sqlite3
import unittest

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
                value = parse_quantity(text)
                self.assertEqual(quantity_text(value), canonical)
                self.assertEqual(parse_quantity(format_quantity(value), True), value)
        self.assertEqual(format_quantity("1234567.0005"), "1,234,567.0005")

    def test_quantity_parser_rejects_ambiguous_or_lossy_inputs(self):
        for value in (True, 1.25, None, {}, "", "$12.00", "1e4", "NaN", "Infinity", "1,2", ".", "1."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_quantity(value)
        with self.assertRaisesRegex(ValueError, "Quantity cannot be negative"):
            parse_quantity("-1", allow_negative=False)
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

    def test_displayed_account_pairs_derive_from_flows_including_canceled(self):
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


if __name__ == "__main__":
    unittest.main()
