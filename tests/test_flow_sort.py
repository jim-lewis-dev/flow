"""Flow placement follows visible contents and exact quantities, never UUIDs."""
from decimal import Decimal
import unittest

from flowledger.core import FLOW_SORT_FIELDS, Repository, STATUSES, flow_sort_key


class FlowSortTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")
        self.addCleanup(self.repo.close)

    @staticmethod
    def record(**changes):
        return {"date": "2026-10-02", "from_account": "Source", "to_account": "Target",
                "amount": "1", "units": "USD", "status": "Complete", "category": "General",
                "name": "Transfer", "notes": "", "tags": [], **changes}

    def test_each_visible_key_breaks_ties_before_ids_and_insertion_order(self):
        cases = (("date", "2026-10-02 9:4:5", "2026-10-02 9:4:6"),
                 ("from_account", "a Source", "Z Source"),
                 ("to_account", "b Target", "Y Target"),
                 ("amount", "2", "10"), ("units", "MW", "mW"),
                 ("status", "Complete", "Canceled"),
                 ("category", "alpha", "Zebra"), ("name", "alpha", "Zebra"))
        self.assertEqual(FLOW_SORT_FIELDS, tuple(field for field, _, _ in cases))
        for position, (field, low, high) in enumerate(cases):
            with self.subTest(field=field):
                repo = Repository(":memory:")
                try:
                    # Both insertion order and UUID order oppose the intended order.
                    later = repo.save_flow(self.record(**{field: high},
                        flow_id="00000000-0000-0000-0000-000000000001"))
                    earlier = repo.save_flow(self.record(**{field: low},
                        flow_id="ffffffff-ffff-ffff-ffff-ffffffffffff"))
                    early_key, late_key = flow_sort_key(earlier), flow_sort_key(later)
                    self.assertEqual(early_key[:position], late_key[:position])
                    self.assertLess(early_key[position], late_key[position])
                    self.assertEqual(repo.list_flows(), [earlier, later])
                finally:
                    repo.close()

    def test_earlier_keys_dominate_later_fields(self):
        early = self.record(date="2026-10-01 23:59:59", from_account="Z", to_account="Y",
                            amount="999", units="z", status="Canceled", category="Z", name="Z")
        late = self.record(date="2026-10-02", from_account="A", to_account="B",
                           amount="-999", units="A", status="Complete", category="A", name="A")
        self.assertLess(flow_sort_key(early), flow_sort_key(late))
        early.update(date=late["date"], from_account="A", to_account="Z")
        late.update(from_account="B", to_account="A")
        self.assertLess(flow_sort_key(early), flow_sort_key(late))

    def test_arbitrary_precision_signed_numeric_order_is_not_lexical_or_float(self):
        big = "9" * 400
        smaller_big = "1" + "0" * 399
        close_low = "1." + "0" * 399 + "1"
        close_high = "1." + "0" * 399 + "2"
        expected = ["-" + big, "-10", "-2", "0", close_low, close_high, "2", "10", smaller_big, big]
        saved = []
        for index, amount in enumerate(reversed(expected), 1):
            saved.append(self.repo.save_flow(self.record(amount=amount,
                flow_id=f"00000000-0000-0000-0000-{index:012x}")))
        self.assertEqual([record["amount"] for record in self.repo.list_flows()], expected)
        for record in saved:
            self.assertIsInstance(flow_sort_key(record)[3], Decimal)
        self.assertEqual(flow_sort_key(self.record(amount="-.000")),
                         flow_sort_key(self.record(amount="0.000")))

    def test_date_only_is_midnight_and_seconds_define_order(self):
        times = ["2026-10-02 00:00:01", "2026-10-01 23:59:59", "2026-10-02"]
        records = [self.repo.save_flow(self.record(date=value)) for value in times]
        self.assertEqual(self.repo.list_flows(), [records[1], records[2], records[0]])
        self.assertEqual(flow_sort_key(self.record(date="2026-10-2")),
                         flow_sort_key(self.record(date="2026-10-02T0:0:0")))

    def test_text_is_casefolded_but_units_and_status_order_are_preserved(self):
        record = self.record(from_account="Straße", to_account="Target", category="General", name="Trip")
        self.assertEqual(flow_sort_key(record), flow_sort_key({**record,
            "from_account": "STRASSE", "to_account": "target", "category": "GENERAL", "name": "TRIP"}))
        self.assertNotEqual(flow_sort_key({**record, "units": "MW"}),
                            flow_sort_key({**record, "units": "mW"}))
        records = [self.repo.save_flow(self.record(status=status)) for status in reversed(STATUSES)]
        self.assertEqual([flow["status"] for flow in self.repo.list_flows()], list(STATUSES))

    def test_identical_keys_keep_insertion_order_and_edits_do_not_reposition_ties(self):
        first = self.repo.save_flow(self.record(flow_id="ffffffff-ffff-ffff-ffff-ffffffffffff", notes="First"))
        second = self.repo.save_flow(self.record(flow_id="00000000-0000-0000-0000-000000000001", notes="Second"))
        self.assertEqual(flow_sort_key(first), flow_sort_key(second))
        self.assertEqual(self.repo.list_flows(), [first, second])
        first = self.repo.save_flow({**first, "notes": "Updated", "tags": ["Changed"]})
        self.assertEqual(self.repo.list_flows(), [first, second])

    def test_changing_only_ids_cannot_change_content_order(self):
        lower = self.repo.save_flow(self.record(amount="2",
            flow_id="ffffffff-ffff-ffff-ffff-ffffffffffff"))
        higher = self.repo.save_flow(self.record(amount="10",
            flow_id="00000000-0000-0000-0000-000000000001"))
        self.repo.conn.execute("UPDATE flows SET flow_id=? WHERE flow_id=?",
                              ("00000000-0000-0000-0000-000000000002", lower["flow_id"]))
        self.repo.conn.execute("UPDATE flows SET flow_id=? WHERE flow_id=?",
                              ("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", higher["flow_id"]))
        self.assertEqual([flow["amount"] for flow in self.repo.list_flows()], ["2", "10"])
        self.assertEqual(flow_sort_key(lower), flow_sort_key({**lower, "flow_id": higher["flow_id"]}))

    def test_name_suggestions_retain_earliest_spelling_without_uuid_order(self):
        self.repo.save_flow(self.record(name="Trip", flow_id="ffffffff-ffff-ffff-ffff-ffffffffffff"))
        self.repo.save_flow(self.record(name="TRIP", flow_id="00000000-0000-0000-0000-000000000001"))
        self.assertEqual(self.repo.list_names(), ["Trip"])
        self.repo.save_flow(self.record(name="tRiP", date="2026-10-01"))
        self.assertEqual(self.repo.list_names(), ["tRiP"])


if __name__ == "__main__":
    unittest.main()
