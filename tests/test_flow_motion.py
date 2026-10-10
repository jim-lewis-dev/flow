"""Event chronology, time boundaries, and exact quantity display scaling."""
from copy import deepcopy
from decimal import Decimal
import unittest

from flowledger.flow_motion import grouped_events, pulse_radius, select_events


def flow(flow_id: str, when: str, amount: str = "1", units: str = "USD",
         status: str = "Complete") -> dict:
    return dict(flow_id=flow_id, date=when, from_account="Checking",
                to_account="Store", amount=amount, units=units, status=status,
                name="Purchase", category="Shopping", tags=["trip"], notes="one note")


class EventSelectionTests(unittest.TestCase):
    def test_inclusive_day_boundary_explicit_second_and_stable_ties(self):
        records = [flow("next", "2026-10-03"), flow("end", "2026-10-02 23:59:59"),
                   flow("tie-A", "2026-10-02"), flow("tie-B", "2026-10-02 00:00:00"),
                   flow("before", "2026-10-01 23:59:59"),
                   flow("early", "2026-10-02 01:00:00")]
        before = deepcopy(records)
        day = select_events(records, "2026-10-2", "2026-10-2", "USD", ("Complete",))
        self.assertEqual([row["flow_id"] for row in day],
                         ["tie-A", "tie-B", "early", "end"])
        self.assertIs(day[0], records[2])
        exact = select_events(records, "2026-10-2 0:0", "2026-10-2 1:0:0", "USD", "Complete")
        self.assertEqual([row["flow_id"] for row in exact], ["tie-A", "tie-B", "early"])
        self.assertEqual(records, before)

    def test_status_and_unit_are_exact_and_canceled_always_excluded(self):
        records = [flow("complete", "2026-10-02"), flow("pending", "2026-10-02", status="Pending"),
                   flow("planned", "2026-10-02", status="Planned"),
                   flow("canceled", "2026-10-02", status="Canceled"),
                   flow("lower", "2026-10-02", units="usd"),
                   flow("yen", "2026-10-02", units="JPY")]
        selected = select_events(records, "2026-10-02", "2026-10-02", "USD",
                                 (s for s in ("Complete", "Pending", "Planned", "Canceled")))
        self.assertEqual([row["flow_id"] for row in selected], ["complete", "pending", "planned"])
        self.assertEqual(select_events(records, "2026-10-02", "2026-10-02", "USD", ("Canceled",)), [])
        self.assertEqual([row["flow_id"] for row in select_events(records, "2026-10-02", "2026-10-02", "usd", "Complete")],
                         ["lower"])

    def test_bad_range_status_and_dates_raise(self):
        record = flow("one", "2026-10-02")
        for start, end in (("2026-10-03", "2026-10-02"),
                           ("2026-10-02 23:59:59", "2026-10-02 00:00:00"),
                           ("2026-02-30", "2026-10-02"), ("2026-10-02", "2026-13-01")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                select_events([record], start, end, "USD", ("Complete",))
        with self.assertRaisesRegex(ValueError, "status"):
            select_events([record], "2026-10-02", "2026-10-02", "USD", ["Void"])

    def test_grouped_midnight_and_explicit_midnight_are_one_instant(self):
        records = [flow("later", "2026-10-02 00:00:01"),
                   flow("date", "2026-10-02"), flow("time", "2026-10-02 00:00")]
        original = deepcopy(records)
        self.assertEqual([(when, [row["flow_id"] for row in rows])
                          for when, rows in grouped_events(records)],
                         [("2026-10-02 00:00:00", ["date", "time"]),
                          ("2026-10-02 00:00:01", ["later"])])
        self.assertEqual(records, original)


class PulseTests(unittest.TestCase):
    def test_scaled_fraction_and_huge_exact_amounts_are_bounded(self):
        self.assertEqual(pulse_radius("10", "10"), 17)
        self.assertTrue(5 < pulse_radius("1", "10") < 17)
        self.assertTrue(5 < pulse_radius(".000000001", ".000001") < 17)
        self.assertEqual(pulse_radius(Decimal("1e-10000"), Decimal("1e-1000")), 5)
        # Decimal values can be vastly larger than a binary float can hold.
        huge = Decimal("1e100000")
        small = Decimal("1e1000")
        self.assertTrue(5 <= pulse_radius(small, huge) < 17)
        self.assertEqual(pulse_radius(huge, huge), 17)

    def test_invalid_or_nonpositive_quantities_rejected(self):
        for amount, maximum in (("0", "10"), ("1", "0"), ("-1", "10"),
                                ("x", "10"), ("1", "NaN")):
            with self.subTest(amount=amount, maximum=maximum), self.assertRaises(ValueError):
                pulse_radius(amount, maximum)


if __name__ == "__main__":
    unittest.main()
