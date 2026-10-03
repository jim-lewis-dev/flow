"""Graph boundaries checked against exact hand-calculated balances."""
from copy import deepcopy
import unittest

from flowledger.core import ledger_timeline
from flowledger.graphing import graph_timeline, normalize_graph_date


START = "2026-10-01 00:00:00"
END = "2026-10-03 00:00:00"


def account(name="Checking", balance=10000, anchor=START):
    return dict(name=name, opening_cents=balance, opening_at=anchor)


def flow(when=START, cents=2500, source="Checking", target="Shop", status="Complete"):
    return dict(date=when, amount_cents=cents, from_account=source,
                to_account=target, status=status)


class GraphDateTests(unittest.TestCase):
    def test_unpadded_dates_remain_date_only(self):
        for text in ("2026-1-2", "2026-01-2", " 2026-1-02 "):
            with self.subTest(text=text):
                self.assertEqual(normalize_graph_date(text), "2026-01-02")

    def test_time_to_seconds_and_iso_separator(self):
        self.assertEqual(normalize_graph_date("2026-1-2 3:4"), "2026-01-02 03:04:00")
        self.assertEqual(normalize_graph_date("2026-1-2T3:4:5"), "2026-01-02 03:04:05")
        self.assertEqual(normalize_graph_date("2026-10-01 0:0:0"), START)
        self.assertEqual(normalize_graph_date("2024-2-29"), "2024-02-29")

    def test_invalid_input_is_named_in_useful_error(self):
        for text in ("2026-2-29", "2026-13-1", "2026-1-0", "2026-1-2 24:0",
                     "2026-1-2 3:60", "2026-1-2 3:4:60", "2026-1-2 3:4:5.1",
                     "2026-1-2T3:4Z", "2026-1-2T3:4+00:00", "10/1/2026", "", None):
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                normalize_graph_date(text)
            self.assertIn(repr(text), str(caught.exception))
            self.assertIn("YYYY-MM-DD", str(caught.exception))


class GraphTimelineTests(unittest.TestCase):
    def test_start_flow_preserves_anchor_at_exact_midnight_and_date_only(self):
        for when in (START, "2026-10-01"):
            with self.subTest(when=when):
                series = graph_timeline([account()], [flow(when)], "2026-10-1", "2026-10-3")
                self.assertEqual(series["Checking"], [(START, 10000), (START, 7500), (END, 7500)])

    def test_previous_flows_carry_into_pre_start_balance(self):
        series = graph_timeline([account(anchor="2026-09-01")],
                                [flow("2026-09-20", 1000), flow()], START, END)
        self.assertEqual(series["Checking"], [(START, 9000), (START, 6500), (END, 6500)])

    def test_later_anchor_reconstructs_both_sides(self):
        series = graph_timeline([account(balance=7500, anchor=END)], [flow()], START, END)
        self.assertEqual(series["Checking"], [(START, 10000), (START, 7500), (END, 7500)])

    def test_no_eligible_start_flow_has_no_extra_point(self):
        later = "2026-10-02 12:00:00"
        flows = [flow(START, status="Void"), flow(later), flow(START, source="Other", target="Shop")]
        result = graph_timeline([account()], flows, START, END)
        self.assertEqual(result["Checking"], [(START, 10000), (later, 7500), (END, 7500)])

    def test_pending_basis_includes_only_requested_effects(self):
        flows = [flow(START, 2000), flow(START, 500, status="Pending"), flow(START, 100, status="Planned")]
        self.assertEqual(graph_timeline([account()], flows, START, END)["Checking"][0:2],
                         [(START, 10000), (START, 8000)])
        self.assertEqual(graph_timeline([account()], flows, START, END,
                                       ("Complete", "Pending"))["Checking"][0:2],
                         [(START, 10000), (START, 7500)])

    def test_pending_before_and_at_start_with_later_confirmed_anchor(self):
        flows = [flow("2026-09-30", 500, status="Pending"), flow(START, 1000, status="Pending")]
        points = graph_timeline([account(anchor=END)], flows, START, END,
                                ("Complete", "Pending"))["Checking"]
        self.assertEqual(points, [(START, 9500), (START, 8500), (END, 8500)])

    def test_only_excluded_start_flow_does_not_prepend(self):
        self.assertEqual(graph_timeline([account()], [flow(status="Pending")], START, END)["Checking"],
                         [(START, 10000), (END, 10000)])

    def test_same_instant_flows_are_aggregated_once(self):
        flows = [flow(cents=2000), flow(cents=1000), flow(cents=500, source="Shop", target="Checking")]
        self.assertEqual(graph_timeline([account()], flows, START, END)["Checking"],
                         [(START, 10000), (START, 7500), (END, 7500)])

    def test_canceling_start_flows_keep_pre_and_post_points(self):
        flows = [flow(), flow(source="Shop", target="Checking")]
        self.assertEqual(graph_timeline([account()], flows, START, END)["Checking"],
                         [(START, 10000), (START, 10000), (END, 10000)])

    def test_accounts_match_case_insensitively_and_series_stay_aligned(self):
        accounts = [account("CHECKING"), account("Cash", 500)]
        result = graph_timeline(accounts, [flow(source=" checking ")], START, END)
        self.assertEqual(result["CHECKING"][0:2], [(START, 10000), (START, 7500)])
        self.assertEqual(result["Cash"], [(START, 500), (START, 500), (END, 500)])

    def test_combined_transfers_conserve_every_aligned_value(self):
        accounts = [account(), account("Cash", 500)]
        flows = [flow(target="Cash"), flow("2026-10-02", 400, "Cash", "Checking")]
        result = graph_timeline(accounts, flows, START, END, combined=True)
        self.assertEqual(result, {"Combined": [(START, 10500), (START, 10500),
                                               ("2026-10-02 00:00:00", 10500), (END, 10500)]})

    def test_combined_preserves_preflow_sum_and_external_change(self):
        accounts = [account(), account("Cash", 500)]
        result = graph_timeline(accounts, [flow()], START, END, combined=True)
        self.assertEqual(result["Combined"], [(START, 10500), (START, 8000), (END, 8000)])

    def test_unknown_opening_remains_unknown_individually_and_combined(self):
        accounts = [account(), account("Shop", None)]
        result = graph_timeline(accounts, [flow()], START, END)
        self.assertEqual(result["Shop"], [(START, None), (START, None), (END, None)])
        self.assertEqual(graph_timeline(accounts, [flow()], START, END, combined=True),
                         {"Combined": [(START, None), (START, None), (END, None)]})

    def test_single_instant_window_keeps_both_values(self):
        self.assertEqual(graph_timeline([account()], [flow()], START, START)["Checking"],
                         [(START, 10000), (START, 7500)])

    def test_whole_second_start_preserved_without_artificial_earlier_time(self):
        when = "2026-10-01 12:34:56"
        result = graph_timeline([account()], [flow(when)], when, END)
        self.assertEqual(result["Checking"], [(when, 10000), (when, 7500), (END, 7500)])

    def test_exact_large_cents_and_no_input_or_ledger_mutation(self):
        accounts = [account(balance=2**60)]
        flows = [flow(cents=123)]
        original = deepcopy((accounts, flows))
        ledger_before = ledger_timeline(accounts, flows, START, END)
        result = graph_timeline(accounts, flows, START, END, combined=True)
        self.assertEqual(result["Combined"], [(START, 2**60), (START, 2**60 - 123), (END, 2**60 - 123)])
        self.assertEqual((accounts, flows), original)
        self.assertEqual(ledger_timeline(accounts, flows, START, END), ledger_before)

    def test_status_generator_and_invalid_status_or_range(self):
        self.assertEqual(graph_timeline([account()], [flow()], START, END,
                                       (status for status in ["Complete"]))["Checking"][0:2],
                         [(START, 10000), (START, 7500)])
        self.assertEqual(graph_timeline([account()], [flow()], START, END, "Complete")["Checking"][0:2],
                         [(START, 10000), (START, 7500)])
        with self.assertRaisesRegex(ValueError, "status"):
            graph_timeline([account()], [flow()], START, END, ("Done",))
        with self.assertRaisesRegex(ValueError, "start"):
            graph_timeline([account()], [flow()], END, START)

    def test_empty_accounts(self):
        self.assertEqual(graph_timeline([], [flow()], START, END), {})
        self.assertEqual(graph_timeline([], [flow()], START, END, combined=True), {})


if __name__ == "__main__":
    unittest.main()
