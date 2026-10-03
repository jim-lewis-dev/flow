"""End-to-end example arithmetic with independently computed dollar results."""
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
        self.assertEqual(self.total("2026-10-02 23:59:59"), 781225)
        self.assertEqual(self.total("2026-10-02 23:59:59", statuses=("Complete", "Pending")), 771485)

    def test_planned_flows_are_plain_records_selected_by_status(self):
        self.assertEqual(self.total("2026-12-31 23:59:59", ("Complete", "Pending", "Planned")), 1490985)
        self.assertEqual(len(self.repo.list_flows()), 17)
        self.assertTrue(all("scenario" not in row for row in self.repo.list_flows()))

    def test_seed_never_mixes_into_existing_database(self):
        before = (self.repo.list_flows(), self.repo.history())
        self.assertFalse(seed_demo(self.repo))
        self.assertEqual(before, (self.repo.list_flows(), self.repo.history()))

    def test_export_reimport_is_safe_and_restore_preserves_flows(self):
        path = Path(self.directory.name) / "flows.json"
        export_flows(self.repo, path)
        result = commit_import(self.repo, preview_import(self.repo, read_import(path)))
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 17)
        snapshot = Path(self.directory.name) / "snapshot.sqlite3"
        self.repo.backup(snapshot)
        first = self.repo.list_flows()[0]
        self.repo.void_flow(first["flow_id"])
        self.repo.restore(snapshot)
        self.assertEqual(self.total("2026-10-02 23:59:59"), 781225)
        self.assertEqual(len(self.repo.list_flows()), 17)


if __name__ == "__main__":
    unittest.main()
