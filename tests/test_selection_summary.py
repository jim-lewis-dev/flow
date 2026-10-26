"""Selected row totals describe records exactly, separately for each resource."""
from copy import deepcopy
from decimal import localcontext
import unittest

from flowledger.selection_summary import format_selection_summary


def flow(**changes):
    return {"date": "2026-10-01", "from_account": "A", "to_account": "B", "amount": "1",
            "units": "USD", "status": "Complete", "category": "Supplies", **changes}


class SelectionSummaryTests(unittest.TestCase):
    def test_mixed_statuses_signed_and_zero_rows_have_hand_calculated_totals(self):
        records = [flow(amount="10.10"),
                   flow(date="2026-10-02", to_account="C", amount="-2.05", status="Pending"),
                   flow(amount="0", units="kg", status="Canceled", category="Inspection"),
                   flow(date="2026-10-03 4:5:6", from_account="D", amount="1.25", units="kg",
                        status="Planned")]
        original = deepcopy(records)
        self.assertEqual(format_selection_summary(records), """4 selected flows
Selected record totals · all selected statuses
Dates: 2026-10-01 → 2026-10-03 04:05:06
Statuses: Complete 1 · Pending 1 · Planned 1 · Canceled 1
Total quantities: 1.25 kg · 8.05 USD

From
  A: 0 kg · 8.05 USD
  D: 1.25 kg

To
  B: 1.25 kg · 10.1 USD
  C: -2.05 USD

Categories
  Inspection: 0 kg
  Supplies: 1.25 kg · 8.05 USD""")
        self.assertEqual(records, original)

    def test_canceled_quantities_count_without_reversing_negative_endpoints(self):
        text = format_selection_summary([flow(amount="-3", status="Canceled")])
        self.assertIn("all selected statuses", text)
        self.assertIn("Statuses: Canceled 1", text)
        self.assertIn("Total quantities: -3 USD", text)
        self.assertIn("From\n  A: -3 USD\n\nTo\n  B: -3 USD", text)
        self.assertIn("Categories\n  Supplies: -3 USD", text)

    def test_arbitrary_precision_sums_survive_low_decimal_context(self):
        records = [flow(amount="123456789012345678901234567890.12345678901234567890123456789"),
                   flow(amount="-123456789012345678901234567890"),
                   flow(amount="-.00000000000000000000000000001")]
        with localcontext() as context:
            context.prec = 2
            text = format_selection_summary(records)
        exact = "0.12345678901234567890123456788 USD"
        self.assertIn("Total quantities: " + exact, text)
        self.assertIn("From\n  A: " + exact, text)
        self.assertIn("To\n  B: " + exact, text)
        self.assertIn("Categories\n  Supplies: " + exact, text)

    def test_unit_case_stays_distinct_and_label_order_does_not_depend_on_selection_order(self):
        records = [flow(from_account="zebra", to_account="Delta", category="zinc", units="usd", amount="2"),
                   flow(from_account="Alpha", to_account="beta", category="Copper", units="USD", amount="3"),
                   flow(from_account="Alpha", to_account="beta", category="Copper", units="usd", amount="4")]
        text = format_selection_summary(records)
        self.assertEqual(text, format_selection_summary(reversed(records)))
        self.assertIn("Total quantities: 3 USD · 6 usd", text)
        self.assertIn("From\n  Alpha: 3 USD · 4 usd\n  zebra: 2 usd", text)
        self.assertIn("To\n  beta: 3 USD · 4 usd\n  Delta: 2 usd", text)
        self.assertIn("Categories\n  Copper: 3 USD · 4 usd\n  zinc: 2 usd", text)

    def test_empty_and_single_timestamp_selections_stay_compact(self):
        self.assertEqual(format_selection_summary([]), "")
        text = format_selection_summary([flow(date="2026-10-1 4", amount="0")])
        self.assertTrue(text.startswith("1 selected flow\n"))
        self.assertIn("Dates: 2026-10-01 04:00:00\n", text)
        self.assertNotIn(" → ", text)


if __name__ == "__main__":
    unittest.main()
