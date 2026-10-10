"""Deletion removes saved rows while preserving exact session restoration."""
from decimal import Decimal
import json
import sqlite3
import unittest
from unittest.mock import patch

from flowledger.core import Repository, ledger_balance_at
from flowledger.history import SessionHistory


class DeleteFlowTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")
        self.history = SessionHistory(self.repo)

    def tearDown(self):
        self.history.close()
        self.repo.close()

    def flow(self, **changes):
        return self.repo.save_flow({
            "date": "2026-10-02 13:14:15", "name": "Materials",
            "from_account": "Warehouse", "to_account": "Workshop",
            "amount": ".125000000000000000000001", "units": "kg",
            "status": "Complete", "category": "Supplies",
            "tags": ["Project B", "Priority", "Indoor"],
            "notes": "Keep all the details\nincluding this second line.", **changes})

    def delete(self, record):
        return self.history.perform("Delete Flow", lambda: self.repo.delete_flow(record["flow_id"]))

    def snapshot(self):
        return {table: [tuple(row) for row in self.repo.conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ("accounts", "account_balances", "categories", "tags", "flows", "flow_tags", "audit")}

    def test_delete_leaves_other_records_and_retains_full_before_audit(self):
        target = self.flow()
        remaining = self.flow(name="Other materials", amount="8", tags=["Other"])
        before_events = self.repo.history()
        self.assertEqual(self.repo.delete_flow(target["flow_id"]), target)
        self.assertEqual(self.repo.list_flows(), [remaining])
        events = self.repo.history()
        self.assertEqual(events[:-1], before_events)
        self.assertEqual(events[-1]["entity_key"], target["flow_id"])
        self.assertEqual(events[-1]["action"], "delete")
        self.assertEqual(json.loads(events[-1]["before_json"]), target)
        self.assertIsNone(events[-1]["after_json"])
        self.assertIsNone(self.repo.conn.execute("PRAGMA foreign_key_check").fetchone())
        self.assertFalse(self.repo.conn.in_transaction)

    def test_delete_last_reference_hides_pair_but_preserves_its_starting_balance(self):
        warehouse = self.repo.save_account("Warehouse", "12.345678901234567890123456789", units="kg")
        workshop = self.repo.save_account("Workshop", "-1.25", units="kg")
        record = self.flow()
        self.assertEqual({(row["name"], row["units"]) for row in self.repo.list_accounts(referenced_only=True)},
                         {("Warehouse", "kg"), ("Workshop", "kg")})
        self.delete(record)
        self.assertEqual(self.repo.list_accounts(referenced_only=True), [])
        self.assertEqual(self.repo.get_account("Warehouse", "kg"), warehouse)
        self.assertEqual(self.repo.get_account("Workshop", "kg"), workshop)
        self.history.undo()
        self.assertEqual(self.repo.get_flow(record["flow_id"]), record)
        self.assertEqual(len(self.repo.list_accounts(referenced_only=True)), 2)
        self.history.redo()
        self.assertEqual(self.repo.list_accounts(referenced_only=True), [])
        self.assertEqual(self.repo.get_account("Warehouse", "kg"), warehouse)

    def test_delete_undo_redo_updates_only_matching_unit_balances(self):
        for units, quantity in (("USD", "1000"), ("kg", "1"), ("mW", "7"), ("MW", "9")):
            self.repo.save_account("Warehouse", quantity, units=units)
        usd = self.flow(units="USD", amount="12.34")
        kg = self.flow(amount="0.125")
        mw = self.flow(units="mW", amount="2")
        big_mw = self.flow(units="MW", amount="3")

        def balances():
            return {units: ledger_balance_at(self.repo.get_account("Warehouse", units), self.repo.list_flows(),
                                            "2026-10-03") for units in ("USD", "kg", "mW", "MW")}

        before = {"USD": Decimal("987.66"), "kg": Decimal(".875"), "mW": Decimal("5"), "MW": Decimal("6")}
        after = {**before, "kg": Decimal("1")}
        self.assertEqual(balances(), before)
        self.delete(kg)
        self.assertEqual(balances(), after)
        self.history.undo()
        self.assertEqual(balances(), before)
        self.assertEqual(self.repo.get_flow(kg["flow_id"]), kg)
        self.history.redo()
        self.assertEqual(balances(), after)
        for record in (usd, mw, big_mw):
            self.assertEqual(self.repo.get_flow(record["flow_id"]), record)

    def test_repeated_undo_redo_preserves_exact_record_tags_and_identity(self):
        record = self.flow()
        self.delete(record)
        for _ in range(3):
            self.assertEqual(self.history.undo(), "Delete Flow")
            self.assertEqual(self.repo.get_flow(record["flow_id"]), record)
            self.assertEqual(self.history.redo(), "Delete Flow")
            self.assertIsNone(self.repo.get_flow(record["flow_id"]))
        self.assertEqual([event["action"] for event in self.repo.history(record["flow_id"])],
                         ["create", "delete", "undo", "redo", "undo", "redo", "undo", "redo"])

    def test_unknown_flow_changes_neither_database_nor_redo(self):
        record = self.flow()
        self.delete(record)
        self.history.undo()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Unknown flow"):
            self.history.perform("Delete Flow", lambda: self.repo.delete_flow("missing"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.history.can_undo)
        self.assertTrue(self.history.can_redo)
        self.history.redo()
        self.assertIsNone(self.repo.get_flow(record["flow_id"]))

    def test_failed_audit_rolls_back_deletion_and_cascaded_tags(self):
        record = self.flow()
        before = self.snapshot()
        with patch.object(self.repo, "_audit", side_effect=sqlite3.OperationalError("audit write failed")):
            with self.assertRaisesRegex(sqlite3.OperationalError, "audit write failed"):
                self.delete(record)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.history.can_undo)
        self.assertFalse(self.history.can_redo)
        self.assertFalse(self.repo.conn.in_transaction)

    def test_deleting_void_record_is_distinct_from_voiding(self):
        record = self.flow()
        voided = self.repo.void_flow(record["flow_id"])
        self.assertEqual(voided["status"], "Canceled")
        self.assertEqual(self.repo.list_flows(), [voided])
        self.delete(voided)
        self.assertEqual(self.repo.list_flows(), [])
        self.history.undo()
        self.assertEqual(self.repo.list_flows(), [voided])


if __name__ == "__main__":
    unittest.main()
