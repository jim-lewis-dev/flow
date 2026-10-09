"""Acceptance tests for financial invariants, timeline boundaries and recovery."""
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
import uuid

from flowledger.core import Repository, balance_at, format_money, normalize_date, normalize_flow_date, parse_money, timeline, ledger_balance_at, ledger_timeline
from flowledger.exchange import commit_import, export_flows, preview_import, read_import


class ValidationTests(unittest.TestCase):
    def test_partial_dates_and_24_hour_times_fill_only_missing_components(self):
        cases = {
            "2026": "2026-01-01 00:00:00",
            "2026-3": "2026-03-01 00:00:00",
            "2026-10-9": "2026-10-09 00:00:00",
            "2026-10-9 7": "2026-10-09 07:00:00",
            "2026-10-9 7:5": "2026-10-09 07:05:00",
            "2026-10-9 7:5:6": "2026-10-09 07:05:06",
            "2026-10-9T23:59:59": "2026-10-09 23:59:59",
            "2028-2-29 0": "2028-02-29 00:00:00",
            " 2026-1-1 0:0:0 ": "2026-01-01 00:00:00",
        }
        for entered, expected in cases.items():
            with self.subTest(entered=entered):
                self.assertEqual(normalize_date(entered), expected)
                stored = expected if " " in entered.strip() or "T" in entered else expected[:10]
                self.assertEqual(normalize_flow_date(entered), stored)

    def test_partial_date_defaults_do_not_accept_invalid_calendar_or_time_values(self):
        for value in ("", "26", "0000", "2026-0", "2026-13", "2026-1-0", "2026-2-29",
                      "2026-10-9 24", "2026-10-9 7:60", "2026-10-9 7:5:60",
                      "2026-10-9 -1", "2026-10-9 7 PM", "2026-10-9 7:",
                      "2026-10-9 7:5:", "2026-10 7", "2026-10-9 7:5:6Z",
                      "2026-10-9 7:5:6.1", "2026-10-9 7:5:6-05:00", None, 2026):
            with self.subTest(entered=value), self.assertRaises(ValueError):
                normalize_date(value)

    def test_money_is_exact_and_rejects_fractional_cents(self):
        self.assertEqual(parse_money("$1,234.56"), 123456)
        self.assertEqual(parse_money("-12.34", True), -1234)
        self.assertEqual(parse_money("12.340"), 1234)
        for text, cents in ((".50", 50), (".05", 5), (".5", 50), ("$.01", 1), ("+.50", 50), (".00", 0)):
            with self.subTest(text=text):
                self.assertEqual(parse_money(text), cents)
        self.assertEqual(parse_money("-.50", True), -50)
        self.assertEqual(parse_money("-$.05", True), -5)
        self.assertEqual(format_money(-123456), "-1,234.56")
        for value in ("0.001", ".001", ".", "-.", "-.50", "NaN", "inf", "1e3", "1,2", "-1", 1.25, True, "92233720368547758.08"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_money(value)

    def test_local_dates_preserve_times_and_reject_lossy_inputs(self):
        self.assertEqual(normalize_date("2026-10-02"), "2026-10-02 00:00:00")
        self.assertEqual(normalize_date("2026-10-02T13:45"), "2026-10-02 13:45:00")
        for value in ("2026-02-30", "2026-10-02 24:00", "2026-10-02T12:00Z", "2026-10-02T12:00:00.001", "10/02/2026"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_date(value)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.repo = Repository(self.path)
        self.repo.save_account("Checking", 100000)
        self.repo.save_account("AmEx", 0)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def flow(self, source="AmEx", destination="Joe's Diner", amount="12.00", **overrides):
        data = dict(date="2026-10-02 12:30", from_account=source, to_account=destination,
                    amount=amount, category="Dining", tags=[])
        data.update(overrides)
        return self.repo.save_flow(data)

    def balance(self, name, statuses=("Complete",)):
        return balance_at(self.repo.get_account(name), self.repo.list_flows(), "2026-10-31", statuses)

    def test_credit_purchase_payment_and_refund_conserve_resource(self):
        self.repo.save_account("Joe's Diner", 0)
        self.flow()
        self.assertEqual(self.balance("AmEx"), -1200)
        self.assertEqual(self.balance("Joe's Diner"), 1200)
        self.flow("Checking", "AmEx")
        self.assertEqual(self.balance("Checking"), 98800)
        self.assertEqual(self.balance("AmEx"), 0)
        self.flow("Joe's Diner", "AmEx", "5")
        self.assertEqual(self.balance("AmEx"), 500)
        self.assertEqual(self.balance("Joe's Diner"), 700)
        accounts = self.repo.list_accounts()
        self.assertEqual(sum(self.balance(row["name"]) for row in accounts), 100000)
        self.assertEqual(sum(self.balance(row["name"]) for row in accounts if row["name"] in ("Checking", "AmEx")), 99300)

    def test_pending_to_complete_updates_one_record_with_audit(self):
        pending = self.flow(amount="20", status="Pending")
        self.assertEqual(self.balance("AmEx"), 0)
        self.assertEqual(self.balance("AmEx", ("Complete", "Pending")), -2000)
        pending.update(status="Complete", amount_cents=2400)
        saved = self.repo.save_flow(pending)
        self.assertEqual(saved["flow_id"], pending["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(self.balance("AmEx", ("Complete", "Pending")), -2400)
        history = self.repo.history(pending["flow_id"])
        self.assertEqual(len(history), 2)
        self.assertEqual(json.loads(history[-1]["before_json"])["amount_cents"], 2000)
        self.repo.void_flow(pending["flow_id"])
        self.assertEqual(self.balance("AmEx", ("Complete", "Pending", "Planned", "Void")), 0)

    def test_names_are_identities_and_suggestions_learn_on_save(self):
        saved = self.flow(source=" amex ", destination=" Joe's Diner ", category=" Boat ", tags=["Trip", " trip ", "SUMMER", ""])
        self.assertEqual(saved["from_account"], "AmEx")
        self.assertEqual(saved["category"], "Boat")
        self.assertEqual(saved["tags"], ["Trip", "SUMMER"])
        repeated = self.flow(category="boat", tags=["summer"])
        self.assertEqual(repeated["category"], "Boat")
        self.assertEqual(repeated["tags"], ["SUMMER"])
        self.assertEqual(len(self.repo.list_accounts()), 3)
        self.assertIn("AmEx -> Joe's Diner", self.repo.list_names())

    def test_invalid_write_and_batch_rollback_leave_no_suggestions_or_audit(self):
        before_history = self.repo.history()
        with self.assertRaises(ValueError):
            with self.repo.atomic():
                self.flow(destination="New Shop", category="New Category", tags=["New Tag"])
                self.flow(source="checking", destination=" Checking ")
        self.assertEqual(self.repo.list_flows(), [])
        self.assertIsNone(self.repo.get_account("New Shop"))
        self.assertNotIn("New Category", self.repo.list_categories())
        self.assertNotIn("New Tag", self.repo.list_tags())
        self.assertEqual(self.repo.history(), before_history)

    def test_invalid_flow_fields_cannot_mutate_database(self):
        for changes in ({"amount":"0"}, {"amount_cents":1.2}, {"amount_cents":True},
                        {"status":"done"}, {"date":"tomorrow"}, {"tags":"trip"},
                        {"flow_id":"not-a-uuid"}, {"category":None},
                        {"scenario":"Baseline"}, {"time_specified":False}, {"invented":True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.flow(**changes)
        self.assertEqual(self.repo.list_flows(), [])
        self.assertEqual(len(self.repo.list_accounts()), 2)

    def test_dual_amount_fields_must_agree(self):
        consistent = self.flow(amount="12.00", amount_cents=1200)
        self.assertEqual(consistent["amount_cents"], 1200)
        before = self.repo.history()
        with self.assertRaisesRegex(ValueError, "disagree"):
            self.flow(amount="12.00", amount_cents=1300)
        self.assertEqual(self.repo.list_flows(), [consistent])
        self.assertEqual(self.repo.history(), before)

    def test_null_name_and_notes_rejected_without_suggestions_or_record_changes(self):
        saved = self.flow(name="Lunch", notes="Keep this note", tags=["Original"])
        before = list(self.repo.conn.iterdump())
        for field in ("name", "notes"):
            for editing in (False, True):
                with self.subTest(field=field, editing=editing):
                    changes = dict(date="2026-10-02", from_account="New Bank", to_account="New Shop",
                                   amount="23.45", category="New Category", tags=["New Tag"],
                                   **{field: "Before\x00after"})
                    if editing:
                        changes["flow_id"] = saved["flow_id"]
                    with self.assertRaisesRegex(ValueError, field.title() + " cannot contain a null character"):
                        self.repo.save_flow(changes)
                    self.assertEqual(preview_import(self.repo, [changes])[0]["state"], "Error")
                    self.assertEqual(list(self.repo.conn.iterdump()), before)

    def test_saved_multiline_unicode_name_and_notes_roundtrip_csv_and_json(self):
        saved = self.flow(name='Café 🚤\n"Lunch", crew', notes='\tFirst line: αβ\nSecond line: 旅行\r\nLast line.\n')
        for suffix in (".csv", ".json"):
            with self.subTest(suffix=suffix):
                exported = Path(self.temp.name) / ("flows" + suffix)
                self.assertEqual(export_flows(self.repo, exported), 1)
                rows = read_import(exported)
                self.assertEqual(rows[0]["name"], saved["name"])
                self.assertEqual(rows[0]["notes"], saved["notes"])
                imported = Repository(Path(self.temp.name) / ("roundtrip" + suffix + ".sqlite3"))
                try:
                    result = commit_import(imported, preview_import(imported, rows))
                    self.assertEqual(result, {"imported": 1, "skipped": 0})
                    self.assertEqual(imported.list_flows(), [saved])
                finally:
                    imported.close()


    def test_unknown_balance_remains_unknown(self):
        self.repo.save_account("Savings", None)
        self.flow("Checking", "Savings", "10")
        self.assertIsNone(self.balance("Savings"))
        values = timeline([self.repo.get_account("Savings")], self.repo.list_flows(), "2026-10-01", "2026-10-31")
        self.assertTrue(all(value is None for _, value in values["Savings"]))

    def test_backup_restore_preserves_records_and_creates_safety_copy(self):
        first = self.flow()
        backup = Path(self.temp.name) / "backup.sqlite3"
        self.repo.backup(backup)
        snapshot = self.repo.history()
        self.flow(amount="99")
        self.repo.restore(backup)
        self.assertEqual(self.repo.list_flows(), [first])
        self.assertEqual(self.repo.history(), snapshot)
        safety = list(Path(self.temp.name).glob("ledger.sqlite3.pre-restore-*.bak"))
        self.assertEqual(len(safety), 1)
        old = Repository(safety[0])
        self.assertEqual(len(old.list_flows()), 2)
        old.close()

    def test_restore_rejects_unknown_or_newer_schema_before_replacing(self):
        first = self.flow()
        wrong = Path(self.temp.name) / "wrong.sqlite3"
        with sqlite3.connect(wrong) as conn:
            conn.execute("CREATE TABLE unrelated(a)")
        with self.assertRaises(ValueError):
            self.repo.restore(wrong)
        newer = Path(self.temp.name) / "newer.sqlite3"
        self.repo.backup(newer)
        with sqlite3.connect(newer) as conn:
            conn.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            self.repo.restore(newer)
        self.assertEqual(self.repo.list_flows(), [first])
        self.assertEqual(list(Path(self.temp.name).glob("*.bak")), [])


class CalculationTests(unittest.TestCase):
    @staticmethod
    def record(when, amount, source="A", destination="B", status="Complete"):
        return {"date":when, "amount_cents":amount, "from_account":source,
                "to_account":destination, "status":status}

    def test_anchor_is_before_its_events_target_is_after_its_events(self):
        account = {"name":"A", "opening_at":"2026-10-02 12:00:00", "opening_cents":1000}
        flows = [self.record("2026-10-01 12:00:00", 100),
                 self.record("2026-10-02 11:00:00", 200),
                 self.record("2026-10-02 12:00:00", 300)]
        self.assertEqual(balance_at(account, flows, "2026-10-01"), 1300)
        self.assertEqual(balance_at(account, flows, "2026-10-01 12:00:00"), 1200)
        self.assertEqual(balance_at(account, flows, "2026-10-02 11:00:00"), 1000)
        self.assertEqual(balance_at(account, flows, "2026-10-02 12:00:00"), 700)

    def test_timeline_carries_prior_flows_combines_same_timestamp(self):
        account = {"name":"A", "opening_at":"2026-10-01", "opening_cents":1000}
        flows = [self.record("2026-10-01 12:00:00", 100),
                 self.record("2026-10-02 12:00:00", 200),
                 self.record("2026-10-02 12:00:00", 50, "B", "A")]
        series = timeline([account], flows, "2026-10-02", "2026-10-03")["A"]
        self.assertEqual(series, [("2026-10-02 00:00:00",900),
                                  ("2026-10-02 12:00:00",750),
                                  ("2026-10-03 00:00:00",750)])

    def test_random_timelines_agree_with_direct_balances_across_anchors(self):
        rng = random.Random(1026)
        accounts = [{"name":name, "opening_cents":rng.randrange(-1000,1000),
                     "opening_at":f"2026-10-0{day} 12:00:00"}
                    for name, day in (("A",1), ("B",3), ("C",6))]
        flows = []
        for _ in range(100):
            source, dest = rng.sample(["A", "B", "C"], 2)
            flows.append(self.record(f"2026-10-0{rng.randrange(1,8)} 12:00:00", rng.randrange(1,1000),
                                     source, dest, rng.choice(("Complete","Pending","Planned","Void"))))
        statuses = ("Complete", "Pending", "Planned")
        result = timeline(accounts, flows, "2026-10-01", "2026-10-08", statuses)
        for account in accounts:
            for when, balance in result[account["name"]]:
                self.assertEqual(balance, balance_at(account, flows, when, statuses))

    def test_confirmed_anchor_pending_and_overdue_plans_before_anchor(self):
        account = {"name":"A", "opening_at":"2026-10-03", "opening_cents":100000}
        flows = [self.record("2026-10-01 12:00:00", 2000, status="Pending"),
                 self.record("2026-10-02 12:00:00", 3000, status="Planned"),
                 self.record("2026-10-04 12:00:00", 4000)]
        pending = ("Complete", "Pending")
        forecast = ("Complete", "Pending", "Planned")
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-05"), 96000)
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-05", pending), 94000)
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-05", forecast), 91000)
        # The generic engine deliberately treats its seed as the complete assumed state.
        self.assertEqual(balance_at(account, flows, "2026-10-05", forecast), 96000)
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-01", forecast), 100000)
        for when, value in ledger_timeline([account], flows, "2026-10-01", "2026-10-05", forecast)["A"]:
            self.assertEqual(value, ledger_balance_at(account, flows, when, forecast))

    def test_old_pending_becoming_complete_requires_consistent_snapshot(self):
        account = {"name":"A", "opening_at":"2026-10-03", "opening_cents":100000}
        flows = [self.record("2026-10-01 12:00:00", 2000, status="Pending")]
        basis = ("Complete", "Pending")
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-04", basis), 98000)
        flows[0]["status"] = "Complete"
        # A completed pre-anchor event is assumed reflected in the confirmed opening.
        account["opening_cents"] = 98000
        self.assertEqual(ledger_balance_at(account, flows, "2026-10-04", basis), 98000)


if __name__ == "__main__":
    unittest.main()
