"""Endpoint filtering, session history, and compact desktop layout through Tk."""
import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class AccountFilterTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "filters.sqlite3")
        self.repo.save_account("Unused", '1000')
        self.flows = {}
        for day, source, destination, status, name, category, tag, amount in (
            (1, "Checking", "Diner", "Complete", "Lunch", "Dining", "Trip", "12"),
            (2, "AmEx", "Shop", "Pending", "Groceries", "Groceries", "Work", "25"),
            (3, "Shop", "Checking", "Complete", "Refund", "Refund", "Trip", "5"),
            (4, "Checking Reserve", "Diner", "Canceled", "Correction", "Other", "Work", "4"),
            (5, "Cash", "Shop", "Planned", "Supplies", "Groceries", "Trip", "8"),
        ):
            self.flows[name] = self.repo.save_flow(dict(units="USD", date=f"2026-10-0{day} 12:34:56",
                from_account=source, to_account=destination, amount=amount, status=status,
                name=name, category=category, tags=[tag], notes="Checking mentioned in notes"))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1920x1200+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        self.app.history.close()
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def choose(self, key, *values):
        self.app.ledger_filters[key].set_selected(values, notify=True)
        self.pump()

    def names(self):
        return {flow["name"] for flow in self.app.ledger_rows.values()}

    def shortcut(self, key, widget=None):
        widget = widget or self.app
        widget.focus_force()
        self.pump()
        widget.event_generate("<Control-" + key + ">")
        self.pump()

    def test_from_is_exact_and_directional(self):
        self.choose("From", "Checking")
        self.assertEqual(self.names(), {"Lunch"})
        self.choose("From", "Shop")
        self.assertEqual(self.names(), {"Refund"})

    def test_to_is_exact_and_directional(self):
        self.choose("To", "Checking")
        self.assertEqual(self.names(), {"Refund"})
        self.choose("To", "Shop")
        self.assertEqual(self.names(), {"Groceries", "Supplies"})

    def test_endpoint_names_ignore_case(self):
        self.choose("From", "CHECKING")
        self.choose("To", "diner")
        self.assertEqual(self.names(), {"Lunch"})

    def test_multiple_endpoints_use_or_within_and_between(self):
        self.choose("From", "Checking", "AmEx")
        self.choose("To", "Diner", "Shop")
        self.assertEqual(self.names(), {"Lunch", "Groceries"})
        self.choose("To", "Shop")
        self.assertEqual(self.names(), {"Groceries"})
        self.choose("From")
        self.assertEqual(self.names(), {"Groceries", "Supplies"})
        self.choose("To")
        self.assertEqual(len(self.names()), 5)

    def test_endpoints_combine_with_all_other_restrictions_and_totals(self):
        self.choose("From", "Checking", "AmEx")
        self.choose("To", "Diner", "Shop")
        self.choose("Status", "Complete", "Pending")
        self.choose("Category", "Dining", "Groceries")
        self.choose("Tag", "Trip")
        self.app.filter_start.set("2026-10-1 12:34:56")
        self.app.filter_end.set("2026-10-1 12:34:56")
        self.app.search_var.set("lunch")
        self.app.refresh_ledger()
        self.assertEqual(self.names(), {"Lunch"})
        self.assertEqual([value.get() for value in self.app.metric_vars], ["1", "0", "1"])
        self.assertEqual(self.app.report_summary.get(), 'Complete · 12.00 USD')

    def test_choices_use_saved_endpoints_independent_of_other_filters(self):
        expected_from = {"Checking", "AmEx", "Shop", "Checking Reserve", "Cash"}
        expected_to = {"Diner", "Shop", "Checking"}
        self.choose("Status", "Complete")
        self.choose("Category", "Dining")
        self.app.search_var.set("nonexistent")
        self.app.form_vars["from_account"].set("Unadded account")
        self.app.refresh_all()
        self.assertEqual(set(self.app.ledger_filters["From"].values), expected_from)
        self.assertEqual(set(self.app.ledger_filters["To"].values), expected_to)
        self.assertEqual(self.names(), set())

    def test_correcting_last_endpoint_reference_prunes_filter_and_undo_restores_records(self):
        flow = self.flows["Lunch"]
        self.app.ledger.selection_set(flow["flow_id"])
        self.pump()
        self.choose("From", "Checking")
        self.app.form_vars["from_account"].set("AmEx")
        self.app._field_enter("flow", "from_account")
        self.pump()
        choice = self.app.ledger_filters["From"]
        self.assertNotIn("Checking", choice.values)
        self.assertEqual(choice.get(), ())
        self.assertNotIn("Checking", choice._available_values())
        self.assertEqual(len(self.names()), 5)
        self.shortcut("z")
        self.assertIn("Checking", choice.values)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertEqual(choice.get(), ())
        self.assertEqual(len(self.names()), 5)
        self.shortcut("y")
        self.assertNotIn("Checking", choice.values)
        self.assertEqual(choice.get(), ())
        self.assertEqual(len(self.names()), 5)

    def test_each_endpoint_popup_supports_multiselect_undo_and_search(self):
        database = "\n".join(self.repo.conn.iterdump())
        for key, first, second in (("From", "Checking", "AmEx"), ("To", "Diner", "Shop")):
            with self.subTest(filter=key):
                choice = self.app.ledger_filters[key]
                choice.open_popup()
                self.pump()
                choice.checkbuttons[first].invoke()
                choice.checkbuttons[second].invoke()
                popup = choice.popup
                self.shortcut("z", choice.checkbuttons[second])
                self.assertIs(choice.popup, popup)
                self.assertEqual(choice.get(), (first,))
                self.assertFalse(choice._choice_variables[second].get())
                self.shortcut("y", choice.checkbuttons[second])
                self.assertEqual(choice.get(), (first, second))
                entry = choice.search_entry
                entry.focus_force()
                self.pump()
                entry.insert(0, second[:2])
                self.assertNotIn(first, choice.checkbuttons)
                self.assertEqual(choice.get(), (first, second))
                self.shortcut("z", entry)
                self.assertEqual(entry.get(), "")
                self.shortcut("y", entry)
                self.assertEqual(entry.get(), second[:2])
                choice.close_popup()
        self.assertEqual("\n".join(self.repo.conn.iterdump()), database)

    def test_filter_history_preserves_unfinished_record_fields(self):
        flow = self.flows["Lunch"]
        self.app.ledger.selection_set(flow["flow_id"])
        self.pump()
        self.app.form_vars["amount"].set("unfinished")
        draft = self.app._flow_snapshot()
        self.choose("From", "AmEx")
        self.choose("To", "Shop")
        self.shortcut("z")
        self.shortcut("z")
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertEqual(self.app.editing_id, flow["flow_id"])

    def test_add_updates_choices_resets_form_and_retains_endpoint_filters(self):
        self.choose("From", "AmEx")
        for key, value in dict(units="USD", from_account="Checking", to_account="New Shop", amount=".50",
                               name="New purchase").items():
            self.app.form_vars[key].set(value)
        self.app.save_button.invoke()
        self.pump()
        self.assertEqual(len(self.repo.list_flows()), 6)
        self.assertIn("New Shop", self.app.ledger_filters["To"].values)
        self.assertEqual(self.app.ledger_filters["From"].get(), ("AmEx",))
        self.assertEqual(self.names(), {"Groceries"})
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app._flow_snapshot(), self.app._clean_flow)

    def test_search_and_six_filter_buttons_fit_supported_window_sizes(self):
        from tkinter import ttk
        primary = self.app.search_entry.master
        dates = next(widget for widget in primary.winfo_children()
                     if isinstance(widget, ttk.Button) and widget.cget("text") == "Dates…")
        for width, height in ((1920, 1200), (1920, 1080), (1280, 800), (1120, 700)):
            with self.subTest(width=width, height=height):
                self.app.geometry(f"{width}x{height}+0+0")
                self.pump()
                search = self.app.search_entry
                self.assertGreaterEqual(search.winfo_width(), 100)
                self.assertLessEqual(search.winfo_width(), 240)
                controls = [search, *(choice.button for choice in self.app.ledger_filters.values()),
                            dates, self.app.reset_filters_button]
                right = primary.winfo_rootx() + primary.winfo_width()
                for previous, widget in zip(controls, controls[1:]):
                    self.assertGreaterEqual(widget.winfo_rootx(), previous.winfo_rootx() + previous.winfo_width())
                    center = widget.winfo_rooty() + widget.winfo_height() / 2
                    baseline = controls[1].winfo_rooty() + controls[1].winfo_height() / 2
                    self.assertLessEqual(abs(center - baseline), 1)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right)
                self.assertEqual(self.app.ledger.xview(), (0.0, 1.0))
