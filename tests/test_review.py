"""Independent acceptance examples checked against hand-calculated outcomes."""

import tempfile
from pathlib import Path
import unittest

from flowledger.core import Repository, ledger_balance_at, ledger_timeline
from flowledger.exchange import commit_import, preview_import


class IndependentReviewTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Repository(Path(self.directory.name) / "review.sqlite3")
        self.addCleanup(self.repo.close)
        for name, opening in (("Checking", 100000), ("AmEx", -10000), ("Wallet", 5000)):
            self.repo.save_account(name, opening)

    def flow(self, source, target, amount, when, **fields):
        return self.repo.save_flow(dict(from_account=source, to_account=target,
                                       amount=amount, date=when, **fields))

    def example(self):
        self.flow("Checking", "Wallet", "100", "2026-10-01")
        self.flow("AmEx", "Shop", "36.45", "2026-10-02 13:00")
        self.flow("Checking", "AmEx", "60", "2026-10-02 13:00")
        self.flow("Employer", "Checking", "500", "2026-10-03 08:00")
        self.flow("Wallet", "Shop", "7.25", "2026-10-03 09:00", status="Pending")
        rent = self.flow("Checking", "Landlord", "300", "2026-10-10", status="Planned")
        self.flow("Checking", "Shop", "998", "2026-10-03", status="Void")
        return rent

    def balances(self, at, statuses=("Complete",)):
        flows = self.repo.list_flows()
        return {account["name"]: ledger_balance_at(account, flows, at, statuses)
                for account in self.repo.list_accounts() if account["name"] in ("Checking", "AmEx", "Wallet")}

    def test_purchase_transfer_income_pending_and_void_have_exact_independent_answers(self):
        self.example()
        self.assertEqual(self.balances("2026-10-01"),
                         {"AmEx": -10000, "Checking": 90000, "Wallet": 15000})
        self.assertEqual(self.balances("2026-10-02 13:00"),
                         {"AmEx": -7645, "Checking": 84000, "Wallet": 15000})
        self.assertEqual(self.balances("2026-10-31"),
                         {"AmEx": -7645, "Checking": 134000, "Wallet": 15000})
        estimate = self.balances("2026-10-31", ("Complete", "Pending"))
        self.assertEqual(estimate, {"AmEx": -7645, "Checking": 134000, "Wallet": 14275})
        self.assertEqual(sum(estimate.values()), 140630)
        forecast = self.balances("2026-10-31", ("Complete", "Pending", "Planned", "Void"))
        self.assertEqual(sum(forecast.values()), 110630)


    def test_later_confirmed_snapshot_reconstructs_history_and_includes_unsettled_flow(self):
        self.example()
        for name, opening in (("Checking", 134000), ("AmEx", -7645), ("Wallet", 15000)):
            self.repo.save_account(name, opening, opening_at="2026-10-04")
        self.assertEqual(self.balances("2026-09-30 23:59:59"),
                         {"AmEx": -10000, "Checking": 100000, "Wallet": 5000})
        self.assertEqual(self.balances("2026-10-02 13:00"),
                         {"AmEx": -7645, "Checking": 84000, "Wallet": 15000})
        self.assertEqual(sum(self.balances("2026-10-05", ("Complete", "Pending")).values()), 140630)

    def test_graph_window_preserves_carry_and_same_time_flows_are_aggregated(self):
        self.example()
        account = self.repo.get_account("Checking")
        actual = ledger_timeline([account], self.repo.list_flows(),
                                 "2026-10-02 12:00", "2026-10-03 12:00")["Checking"]
        self.assertEqual(actual, [("2026-10-02 12:00:00", 90000),
                                  ("2026-10-02 13:00:00", 84000),
                                  ("2026-10-03 08:00:00", 134000),
                                  ("2026-10-03 12:00:00", 134000)])

    def test_imported_legitimate_repeat_is_explicit_and_future_changes_do_not_overwrite(self):
        row = dict(date="2026-10-02", from_account="Wallet", to_account="Shop", amount="1.25")
        initial = preview_import(self.repo, [row, row])
        self.assertEqual(commit_import(self.repo, initial, include_similar=True),
                         {"imported": 2, "skipped": 0})
        self.assertEqual(self.balances("2026-10-03")["Wallet"], 4750)
        saved = self.repo.list_flows()[0]
        saved["amount_cents"] = 999
        with self.assertRaises(ValueError):
            commit_import(self.repo, preview_import(self.repo, [saved]))
        self.assertEqual(self.balances("2026-10-03")["Wallet"], 4750)

    def test_disagreeing_money_representations_are_rejected_without_mutation(self):
        saved = self.flow("Wallet", "Shop", "12", "2026-10-02")
        proposed = dict(saved, amount="24.00")
        with self.assertRaises(ValueError):
            self.repo.save_flow(proposed)
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["amount_cents"], 1200)


if __name__ == "__main__":
    unittest.main()
