"""Exact graph quantities stay separate by account and unit."""
from decimal import Decimal, localcontext
import unittest

from flowledger.graphing import graph_timeline


START = "2026-10-01 00:00:00"
END = "2026-10-03 00:00:00"


def account(name="Checking", balance="100", anchor=START, units="USD"):
    return dict(name=name, units=units, opening_amount=balance, opening_at=anchor)


def flow(when=START, amount="25", source="Checking", target="Shop",
         status="Complete", units="USD"):
    return dict(date=when, amount=str(amount), units=units, from_account=source,
                to_account=target, status=status)


def points(*rows):
    return [(when, None if value is None else Decimal(str(value))) for when, value in rows]


class MultiUnitGraphTests(unittest.TestCase):
    def test_same_account_keeps_units_and_starting_balances_separate(self):
        accounts = [account(), account(balance="400", units="JPY"),
                    account(balance="1.23456789", units="kg")]
        flows = [flow(amount="12.34"), flow(amount="150", units="JPY"),
                 flow(amount=".00000001", units="kg")]
        result = graph_timeline(accounts, flows, START, END)
        self.assertEqual(set(result), {("Checking", "USD"), ("Checking", "JPY"), ("Checking", "kg")})
        self.assertEqual(result[("Checking", "USD")], points((START, "100"), (START, "87.66"), (END, "87.66")))
        self.assertEqual(result[("Checking", "JPY")], points((START, "400"), (START, "250"), (END, "250")))
        self.assertEqual(result[("Checking", "kg")],
                         points((START, "1.23456789"), (START, "1.23456788"), (END, "1.23456788")))

    def test_combined_groups_by_units_without_conversion(self):
        accounts = [account(), account("Cash", "5"),
                    account(balance="400", units="JPY"), account("Cash", "50", units="JPY")]
        flows = [flow(target="Cash"), flow(amount="150", units="JPY")]
        result = graph_timeline(accounts, flows, START, END, combined=True)
        self.assertEqual(result, {
            ("Combined", "USD"): points((START, "105"), (START, "105"), (END, "105")),
            ("Combined", "JPY"): points((START, "450"), (START, "300"), (END, "300"))})

    def test_unit_matching_is_case_sensitive_but_trims_outer_spaces(self):
        accounts = [account(units=" kg "), account(balance="1000", units="KG")]
        result = graph_timeline(accounts, [flow(amount=".001", units=" kg ")], START, END)
        self.assertEqual(result[("Checking", "kg")], points((START, "100"), (START, "99.999"), (END, "99.999")))
        self.assertEqual(result[("Checking", "KG")], points((START, "1000"), (START, "1000"), (END, "1000")))

    def test_unselected_unit_event_does_not_add_start_point_or_later_time(self):
        flows = [flow(units="JPY"), flow("2026-10-02", units="JPY")]
        self.assertEqual(graph_timeline([account()], flows, START, END),
                         {("Checking", "USD"): points((START, "100"), (END, "100"))})

    def test_unit_events_share_the_same_time_axis(self):
        second = "2026-10-02 12:00:00"
        accounts = [account(), account(balance="10", units="litres")]
        flows = [flow(amount="5"), flow(second, ".25", units="litres")]
        result = graph_timeline(accounts, flows, START, END)
        expected_times = [START, START, second, END]
        self.assertEqual([when for when, _ in result[("Checking", "USD")]], expected_times)
        self.assertEqual([when for when, _ in result[("Checking", "litres")]], expected_times)
        self.assertEqual(result[("Checking", "litres")],
                         points((START, "10"), (START, "10"), (second, "9.75"), (END, "9.75")))

    def test_missing_anchor_only_makes_its_own_combined_unit_unknown(self):
        accounts = [account(), account("Cash", None), account(balance="10", units="kg")]
        result = graph_timeline(accounts, [flow(amount=".5", units="kg")], START, END, combined=True)
        self.assertEqual(result[("Combined", "USD")], points((START, None), (START, None), (END, None)))
        self.assertEqual(result[("Combined", "kg")], points((START, "10"), (START, "9.5"), (END, "9.5")))

    def test_each_unit_can_have_its_own_anchor_date(self):
        accounts = [account(balance="75", anchor=END), account(balance="200", anchor="2026-09-01", units="JPY")]
        flows = [flow(), flow("2026-09-15", "10", units="JPY"), flow(amount="5", units="JPY")]
        result = graph_timeline(accounts, flows, START, END)
        self.assertEqual(result[("Checking", "USD")], points((START, "100"), (START, "75"), (END, "75")))
        self.assertEqual(result[("Checking", "JPY")], points((START, "190"), (START, "185"), (END, "185")))

    def test_exact_sum_and_pre_start_recovery_under_small_decimal_context(self):
        accounts = [account(balance="1000000000000000000000000000000.0000000000000000000001", units="kg"),
                    account("Cash", "0.0000000000000000000002", units="kg")]
        flows = [flow(amount=".0000000000000000000001", units="kg")]
        with localcontext() as context:
            context.prec = 3
            result = graph_timeline(accounts, flows, START, END, combined=True)
        self.assertEqual(result[("Combined", "kg")],
                         points((START, "1000000000000000000000000000000.0000000000000000000003"),
                                (START, "1000000000000000000000000000000.0000000000000000000002"),
                                (END, "1000000000000000000000000000000.0000000000000000000002")))


if __name__ == "__main__":
    unittest.main()
