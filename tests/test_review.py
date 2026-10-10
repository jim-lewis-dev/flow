"""Independent acceptance examples checked against hand-calculated outcomes."""

from decimal import Decimal
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
        for name, opening in (("Checking", Decimal("1000")), ("AmEx", -Decimal("100")), ("Wallet", Decimal("50"))):
            self.repo.save_account(name, opening)

    def flow(self, source, target, amount, when, **fields):
        return self.repo.save_flow(dict(units="USD", from_account=source, to_account=target,
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
                         {"AmEx": -Decimal("100"), "Checking": Decimal("900"), "Wallet": Decimal("150")})
        self.assertEqual(self.balances("2026-10-02 13:00"),
                         {"AmEx": -Decimal("76.45"), "Checking": Decimal("840"), "Wallet": Decimal("150")})
        self.assertEqual(self.balances("2026-10-31"),
                         {"AmEx": -Decimal("76.45"), "Checking": Decimal("1340"), "Wallet": Decimal("150")})
        estimate = self.balances("2026-10-31", ("Complete", "Pending"))
        self.assertEqual(estimate, {"AmEx": -Decimal("76.45"), "Checking": Decimal("1340"), "Wallet": Decimal("142.75")})
        self.assertEqual(sum(estimate.values()), Decimal("1406.3"))
        forecast = self.balances("2026-10-31", ("Complete", "Pending", "Planned", "Void"))
        self.assertEqual(sum(forecast.values()), Decimal("1106.3"))


    def test_later_confirmed_snapshot_reconstructs_history_and_includes_unsettled_flow(self):
        self.example()
        for name, opening in (("Checking", Decimal("1340")), ("AmEx", -Decimal("76.45")), ("Wallet", Decimal("150"))):
            self.repo.save_account(name, opening, opening_at="2026-10-04")
        self.assertEqual(self.balances("2026-09-30 23:59:59"),
                         {"AmEx": -Decimal("100"), "Checking": Decimal("1000"), "Wallet": Decimal("50")})
        self.assertEqual(self.balances("2026-10-02 13:00"),
                         {"AmEx": -Decimal("76.45"), "Checking": Decimal("840"), "Wallet": Decimal("150")})
        self.assertEqual(sum(self.balances("2026-10-05", ("Complete", "Pending")).values()), Decimal("1406.3"))

    def test_graph_window_preserves_carry_and_same_time_flows_are_aggregated(self):
        self.example()
        account = self.repo.get_account("Checking")
        actual = ledger_timeline([account], self.repo.list_flows(),
                                 "2026-10-02 12:00", "2026-10-03 12:00")["Checking", "USD"]
        self.assertEqual(actual, [("2026-10-02 12:00:00", Decimal("900")),
                                  ("2026-10-02 13:00:00", Decimal("840")),
                                  ("2026-10-03 08:00:00", Decimal("1340")),
                                  ("2026-10-03 12:00:00", Decimal("1340"))])

    def test_imported_legitimate_repeat_is_explicit_and_future_changes_do_not_overwrite(self):
        row = dict(units="USD", date="2026-10-02", from_account="Wallet", to_account="Shop", amount="1.25")
        initial = preview_import(self.repo, [row, row])
        self.assertEqual(commit_import(self.repo, initial, include_similar=True),
                         {"imported": 2, "skipped": 0})
        self.assertEqual(self.balances("2026-10-03")["Wallet"], Decimal("47.5"))
        saved = self.repo.list_flows()[0]
        saved['amount'] = 999
        with self.assertRaises(ValueError):
            commit_import(self.repo, preview_import(self.repo, [saved]))
        self.assertEqual(self.balances("2026-10-03")["Wallet"], Decimal("47.5"))

    def test_removed_cent_representation_is_rejected_without_mutation(self):
        saved = self.flow("Wallet", "Shop", "12", "2026-10-02")
        proposed = dict(saved, amount_cents=2400)
        with self.assertRaises(ValueError):
            self.repo.save_flow(proposed)
        self.assertEqual(self.repo.get_flow(saved["flow_id"])['amount'], '12')


if __name__ == "__main__":
    unittest.main()
