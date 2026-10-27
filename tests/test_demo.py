"""End-to-end example arithmetic in independent units."""
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from flowledger.core import Repository, ledger_balance_at
from flowledger.demo import seed_demo
from flowledger.exchange import export_flows, read_import, preview_import, commit_import


class DemoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "demo.sqlite3")
        seed_demo(self.repo)

    def tearDown(self):
        self.repo.close()
        self.directory.cleanup()

    def total(self, at, statuses=("Complete",)):
        flows = self.repo.list_flows()
        return sum(ledger_balance_at(a, flows, at, statuses) for a in self.repo.list_accounts() if a["name"] in ("Checking", "Savings", "Cash", "AmEx"))

    def test_demo_confirmed_and_pending_totals(self):
        # $7,960 opening - $152.75 purchases + $5 refund. Card payment is neutral.
        self.assertEqual(self.total("2026-10-02 23:59:59"), Decimal("7812.25"))
        self.assertEqual(self.total("2026-10-02 23:59:59", statuses=("Complete", "Pending")), Decimal("7714.85"))

    def test_planned_flows_are_plain_records_selected_by_status(self):
        self.assertEqual(self.total("2026-12-31 23:59:59", ("Complete", "Pending", "Planned")), Decimal("14909.85"))
        self.assertEqual(len(self.repo.list_flows()), 20)
        self.assertTrue(all("scenario" not in row for row in self.repo.list_flows()))

    def test_materials_have_independent_balances_and_status_bases(self):
        warehouse = self.repo.get_account("Warehouse", "kg")
        workshop = self.repo.get_account("Workshop", "kg")
        flows = self.repo.list_flows()
        at = "2026-10-10 23:59:59"
        for statuses, expected in [
            (("Complete",), ("19.875", "0.125")),
            (("Complete", "Pending"), ("18.375", "1.625")),
            (("Complete", "Pending", "Planned"), ("16.125", "3.875")),
        ]:
            self.assertEqual(ledger_balance_at(warehouse, flows, at, statuses), Decimal(expected[0]))
            self.assertEqual(ledger_balance_at(workshop, flows, at, statuses), Decimal(expected[1]))
        self.assertEqual(self.total("2026-10-02 23:59:59"), Decimal("7812.25"))

    def test_seed_never_mixes_into_existing_database(self):
        before = (self.repo.list_flows(), self.repo.history())
        self.assertFalse(seed_demo(self.repo))
        self.assertEqual(before, (self.repo.list_flows(), self.repo.history()))

    def test_export_reimport_is_safe_and_restore_preserves_flows(self):
        path = Path(self.directory.name) / "flows.json"
        export_flows(self.repo, path)
        result = commit_import(self.repo, preview_import(self.repo, read_import(path)))
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 20)
        snapshot = Path(self.directory.name) / "snapshot.sqlite3"
        self.repo.backup(snapshot)
        first = self.repo.list_flows()[0]
        self.repo.void_flow(first["flow_id"])
        self.repo.restore(snapshot)
        self.assertEqual(self.total("2026-10-02 23:59:59"), Decimal("7812.25"))
        self.assertEqual(len(self.repo.list_flows()), 20)

    def test_camp_example_keeps_resources_independent_and_refuses_existing_file(self):
        from examples.camp import create_camp

        path = Path(self.directory.name) / "camp.sqlite3"
        create_camp(path)
        camp = Repository(path)
        try:
            flows = camp.list_flows()
            self.assertEqual(len(flows), 60)
            for statuses, energy, water in (
                    (("Complete",), "560", "22"),
                    (("Complete", "Pending"), "400", "14"),
                    (("Complete", "Pending", "Planned"), "20", "6")):
                for name, units, expected in (("Battery", "Wh", energy), ("Fresh tank", "L", water)):
                    self.assertEqual(ledger_balance_at(camp.get_account(name, units), flows,
                                                       "2026-10-03 23:59:59", statuses), Decimal(expected))
            self.assertEqual(ledger_balance_at(camp.get_account("Cooking", "Wh"), flows,
                                               "2026-10-03 23:59:59", statuses), Decimal("0"))
            self.assertEqual(ledger_balance_at(camp.get_account("Cooking", "L"), flows,
                                               "2026-10-03 23:59:59", statuses), Decimal("4.5"))
            self.assertEqual(len(camp.load_map_positions()), 11)
        finally:
            camp.close()
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            create_camp(path)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
