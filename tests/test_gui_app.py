"""Real Tk integration checks. Run with a display, including a local Xvfb display.

These tests exercise the application callbacks and inspect persisted records or
plotted values. They are skipped in the ordinary display-free test run.
"""

from datetime import datetime
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class GuiIntegrationTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "gui.sqlite3")
        self.repo.save_account("Checking", '1000')
        self.repo.save_account("AmEx", '0')
        self.callback_errors = []
        errors = self.callback_errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, exception_type, exception, traceback):
                errors.append(f"{exception_type.__name__}: {exception}")

        self.error_dialog = patch("flowledger.gui.messagebox.showerror",
                                  side_effect=lambda *args, **kwargs: self.fail(str(args)))
        self.error_dialog.start()
        self.app = CheckedApp(self.repo)
        self.app._now = lambda: "2026-10-02 15:00:00"
        self.pump()

    def tearDown(self):
        try:
            self.pump()
            for widget in [*self.app.form_widgets.values(), *self.app.ledger_filters.values()]:
                if hasattr(widget, "close_popup"):
                    widget.close_popup()
            # Let the widget's 120ms focus check finish before its Tcl command
            # disappears. Destroying successive Tk roots otherwise logs stale
            # timer warnings even though application callbacks all succeeded.
            self.app.focus_force()
            self.app.after(150, self.app.quit)
            self.app.mainloop()
            self.app.destroy()
        finally:
            self.error_dialog.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must not be swallowed")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must not be swallowed")

    def enter(self, **changes):
        for key, value in changes.items():
            self.app.form_vars[key].set(value)
        if self.app.editing_id:
            # Complete the edited field, just as Enter/Tab/blur does. Existing
            # records deliberately have no active Add button.
            self.app._field_enter("flow", next(reversed(changes)))
        else:
            self.app.save_button.invoke()
        self.pump()

    def select(self, flow):
        self.app.refresh_ledger()
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.ledger.focus(flow["flow_id"])
        self.pump()

    def seed_flow(self, **changes):
        data = dict(units="USD", date="2026-10-02 12:00", from_account="Checking", to_account="Shop",
                    amount="20.00", category="Dining", name="Lunch")
        data.update(changes)
        flow = self.repo.save_flow(data)
        self.app.refresh_all()
        self.pump()
        return flow

    def select_chart(self, *names):
        self.app.chart_accounts.selection_clear(0, "end")
        for index in range(self.app.chart_accounts.size()):
            if self.app.chart_account_pairs[index][0] in names:
                self.app.chart_accounts.selection_set(index)

    def remove_suggestion(self, field, value):
        """Click a rendered suggestion's X, preserving the active form."""
        combo = self.app.form_widgets[field]
        combo.entry.focus_force()
        self.pump()
        combo.open_popup(show_all=True)
        self.pump()
        index = combo.matches.index(value)
        combo.listbox.see(index)
        self.pump()
        _, y, _, height = combo.listbox.bbox(index)
        x = combo.remove_canvas.winfo_width() // 2
        combo.remove_canvas.event_generate("<ButtonPress-1>", x=x, y=y + height // 2)
        combo.remove_canvas.event_generate("<ButtonRelease-1>", x=x, y=y + height // 2)
        self.pump()

    def test_account_suggestion_x_preserves_new_draft_records_and_other_views(self):
        flow = self.seed_flow(tags=["Trip"], notes="Existing notes")
        self.select(flow)
        self.app.repeat_selected()
        self.app.form_vars["amount"].set("21.00")
        self.app.notes_text.insert("end", " and an unsaved edit")
        draft = self.app._flow_snapshot()
        clean = self.app._clean_flow
        database = "\n".join(self.repo.conn.iterdump())
        ledger = tuple(self.app.ledger.get_children())
        accounts = tuple((iid, self.app.accounts_tree.item(iid, "values"))
                         for iid in self.app.accounts_tree.get_children())
        chart_choices = self.app.chart_accounts.get(0, "end")
        self.remove_suggestion("from_account", "Checking")
        for field in ("from_account", "to_account"):
            self.assertNotIn("Checking", self.app.form_widgets[field].values)
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual(self.app._clean_flow, clean)
        self.assertIsNone(self.app.editing_id)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), database)
        self.assertEqual(tuple(self.app.ledger.get_children()), ledger)
        self.assertEqual(tuple((iid, self.app.accounts_tree.item(iid, "values"))
                               for iid in self.app.accounts_tree.get_children()), accounts)
        self.assertEqual(self.app.chart_accounts.get(0, "end"), chart_choices)
        self.assertIn("Checking · USD", chart_choices)

    def test_category_tag_and_name_x_leave_ledger_filters_and_records_intact(self):
        flow = self.seed_flow(tags=["Trip"])
        self.select(flow)
        database = "\n".join(self.repo.conn.iterdump())
        draft = self.app._flow_snapshot()
        for field, value in (("category", "Dining"), ("tags", "Trip"), ("name", "Lunch")):
            self.remove_suggestion(field, value)
            self.assertNotIn(value, self.app.form_widgets[field].values)
            self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), database)
        self.assertIn("Dining", self.app.ledger_filters["Category"].values)
        self.assertIn("Trip", self.app.ledger_filters["Tag"].values)
        self.app.ledger_filters["Category"].set_selected(("Dining",))
        self.app.ledger_filters["Tag"].set_selected(("Trip",))
        self.app.refresh_ledger()
        self.assertEqual(list(self.app.ledger_rows), [flow["flow_id"]])

    def test_removed_suggestions_survive_save_refresh_and_reloaded_preferences(self):
        from flowledger.suggestions import SuggestionPreferences
        flow = self.seed_flow(tags=["Trip"])
        self.select(flow)
        removed = (("from_account", "Checking"), ("category", "Dining"),
                   ("tags", "Trip"), ("name", "Lunch"))
        for field, value in removed:
            self.remove_suggestion(field, value)
        self.enter(amount="21.00")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), {**flow, 'amount': '21'})
        self.app.suggestion_preferences = SuggestionPreferences(self.repo.path)
        self.app.refresh_all()
        self.pump()
        for field, value in removed:
            self.assertNotIn(value, self.app.form_widgets[field].values)
        self.select(flow)
        self.pump()
        for field, value in removed:
            self.assertNotIn(value, self.app.form_widgets[field].values)
        self.assertEqual(self.app.form_vars["from_account"].get(), "Checking")

    def test_exact_enter_restores_account_without_saving_a_flow(self):
        self.seed_flow()
        self.remove_suggestion("to_account", "Checking")
        database = "\n".join(self.repo.conn.iterdump())
        combo = self.app.form_widgets["from_account"]
        combo.entry.focus_force()
        self.pump()
        combo.entry.delete(0, "end")
        combo.entry.insert(0, "Checking")
        combo.entry.event_generate("<Return>")
        self.pump()
        for field in ("from_account", "to_account"):
            self.assertIn("Checking", self.app.form_widgets[field].values)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), database)
        self.assertIsNone(combo.popup)

    def test_tag_enter_restores_only_last_csv_token(self):
        self.seed_flow(tags=["Trip", "Boat, trailer"])
        for value in ("Trip", "Boat, trailer"):
            self.remove_suggestion("tags", value)
        combo = self.app.form_widgets["tags"]
        combo.entry.focus_force()
        self.pump()
        combo.entry.delete(0, "end")
        combo.entry.insert(0, 'Trip, "Boat, trailer"')
        combo.entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(combo.get(), 'Trip, "Boat, trailer"')
        self.assertIn("Boat, trailer", combo.values)
        self.assertNotIn("Trip", combo.values)
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_failed_suggestion_removal_keeps_row_and_draft(self):
        self.seed_flow()
        combo = self.app.form_widgets["category"]
        self.app.form_vars["category"].set("Dining")
        draft = self.app._flow_snapshot()
        with patch.object(self.app.suggestion_preferences, "remove", side_effect=OSError("read only")):
            self.remove_suggestion("category", "Dining")
        self.assertIn("Dining", combo.values)
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertIn("Could not remove suggestion", self.app.form_feedback.get())

    def test_defaults_new_vocabulary_and_dropdown_filtering(self):
        self.assertEqual(self.app.form_vars["date"].get(), datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(self.app.form_vars["status"].get(), "Complete")
        self.enter(from_account="AmEx", to_account="Boat store", amount="12.34", category="Boat")
        rows = self.repo.list_flows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(set(rows[0]), {"flow_id", "date", "from_account", "to_account",
                                       "units", 'amount', "status", "name", "notes", "category", "tags"})
        self.assertEqual(rows[0]['amount'], '12.34')
        self.assertEqual(rows[0]["category"], "Boat")
        self.assertIn("Boat", self.app.form_widgets["category"].values)
        self.assertIn("Boat store", self.app.form_widgets["from_account"].values)
        widget = self.app.form_widgets["category"]
        widget.entry.focus_force()
        self.pump()  # Deliver FocusIn before simulating the user's next keystroke.
        self.assertIn("Boat", widget.matches)
        self.assertIn("Uncategorized", widget.matches)
        self.app.form_vars["category"].set("boa")
        widget._key_release(SimpleNamespace(keysym="a", state=0))
        self.pump()
        self.assertEqual(widget.matches, ["Boat"])
        widget._move(1)
        widget._accept()
        self.assertEqual(self.app.form_vars["category"].get(), "Boat")
        self.app.form_vars["category"].set("New Hobby")
        widget.open_popup()
        widget._accept()
        self.assertEqual(self.app.form_vars["category"].get(), "New Hobby")
        self.assertNotIn("New Hobby", self.repo.list_categories())
        self.assertEqual(len(self.repo.list_flows()), 1, "Enter in a field must not save a flow")

    def test_category_and_tag_filters_are_exact_not_text_mentions(self):
        wanted = self.seed_flow(category="Dining", tags=["Trip"], amount="12.00")
        self.seed_flow(category="Boat", name="Dining mentioned in a title", tags=["Trip"], amount="90.00")
        self.seed_flow(category="Dining", tags=["Trip two"], amount="30.00")
        self.app.ledger_filters["Category"].set_selected(("Dining",))
        self.app.ledger_filters["Tag"].set_selected(("Trip",))
        self.app.refresh_ledger()
        self.assertEqual(list(self.app.ledger_rows), [wanted["flow_id"]])
        self.assertEqual(self.app.report_summary.get(), 'Complete · 12.00 USD')

    def test_invalid_flow_stays_editable_and_does_not_create_vocabulary(self):
        self.enter(from_account="Checking", to_account="Brand new shop", amount="invalid", category="New hobby")
        self.assertEqual(self.repo.list_flows(), [])
        self.assertIsNone(self.repo.get_account("Brand new shop"))
        self.assertNotIn("New hobby", self.repo.list_categories())
        self.assertEqual(self.app.form_vars["amount"].get(), "invalid")
        self.assertEqual(self.app.form_feedback.get(), "Use numbers, +, -, *, / and parentheses only.")

    def test_multiselect_filters_use_any_value_within_each_field(self):
        dining = self.seed_flow(category="Dining", tags=["Trip"], amount="12")
        boat = self.seed_flow(category="Boat", tags=["Maintenance"], status="Pending", amount="25")
        self.seed_flow(category="Boat", tags=["Trip two"], status="Complete")
        self.seed_flow(category="Boat", tags=["Trip"], status="Planned")
        self.seed_flow(category="Other", tags=["Trip"], name="Dining Boat Trip", status="Complete")
        self.app.ledger_filters["Category"].set_selected(("Dining", "Boat"))
        self.app.ledger_filters["Status"].set_selected(("Complete", "Pending"))
        self.app.ledger_filters["Tag"].set_selected(("Trip", "Maintenance"))
        self.app.refresh_ledger()
        wanted = {dining["flow_id"], boat["flow_id"]}
        self.assertEqual(set(self.app.ledger_rows), wanted)
        self.assertEqual({flow["flow_id"] for flow in self.app.report_rows}, wanted)
        self.assertEqual([value.get() for value in self.app.metric_vars], ["1", "1", "2"])
        self.assertEqual(self.app.report_summary.get(), 'Complete · 12.00 USD')
        # Tags match exact elements, including a comma inside one quoted tag.
        quoted = self.seed_flow(category="Boat", tags=["Boat, trailer"], amount="8")
        self.app.ledger_filters["Tag"].set_selected(("Boat, trailer", "Maintenance"))
        self.app.refresh_ledger()
        self.assertEqual(set(self.app.ledger_rows), {boat["flow_id"], quoted["flow_id"]})

    def test_multiselect_combines_search_and_second_precision_date_bounds(self):
        wanted = self.seed_flow(date="2026-10-02 12:30:15", name="Repair", category="Boat", tags=["Trip"])
        self.seed_flow(date="2026-10-02 12:30:14", name="Repair", category="Boat", tags=["Trip"])
        self.seed_flow(date="2026-10-02 12:30:16", name="Repair", category="Boat", tags=["Trip"])
        self.seed_flow(date="2026-10-02 12:30:15", name="Lunch", category="Dining", tags=["Trip"])
        self.app.ledger_filters["Category"].set_selected(("Boat", "Dining"))
        self.app.ledger_filters["Tag"].set_selected(("Trip",))
        self.app.filter_start.set("2026-10-02 12:30:15")
        self.app.filter_end.set("2026-10-02 12:30:15")
        self.app.search_var.set("Repair")
        self.app.refresh_ledger()
        self.assertEqual(set(self.app.ledger_rows), {wanted["flow_id"]})
        for check_filter in self.app.ledger_filters.values():
            check_filter.set_selected(())
        self.app.filter_start.set("")
        self.app.filter_end.set("")
        self.app.search_var.set("")
        self.app.refresh_ledger()
        self.assertEqual(len(self.app.ledger_rows), 4)

    def test_filter_refresh_preserves_active_edit_and_filters_survive_autosave(self):
        row = self.seed_flow(category="Dining", tags=["Trip"], notes="Stored notes")
        other = self.seed_flow(category="Boat", status="Pending", tags=["Repair"])
        self.select(row)
        self.app.notes_text.focus_force()
        self.pump()
        self.app.form_vars["amount"].set("23.45")
        self.app.notes_text.insert("end", "\nStill editing")
        draft = self.app._flow_snapshot()
        clean = self.app._clean_flow
        chosen = {"Status": ("Pending",), "Category": ("Boat",), "Tag": ("Repair",)}
        for label, values in chosen.items():
            self.app.ledger_filters[label].set_selected(values, notify=True)
        self.app.refresh_all()
        self.pump()
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual(self.app._clean_flow, clean)
        self.assertEqual(self.app.editing_id, row["flow_id"])
        self.assertEqual(set(self.app.ledger_rows), {other["flow_id"]})
        self.app.ledger.focus_force()
        self.pump()
        self.assertEqual(self.repo.get_flow(row["flow_id"])['amount'], '23.45')
        self.assertEqual(self.repo.get_flow(row["flow_id"])["notes"], "Stored notes\nStill editing")
        self.assertEqual(self.app.editing_id, row["flow_id"])
        self.assertEqual(self.app._flow_snapshot(), self.app._clean_flow)
        self.assertEqual(self.app.form_vars["amount"].get(), "23.45")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Stored notes\nStill editing")
        self.assertEqual(set(self.app.ledger_rows), {other["flow_id"]})
        for label, values in chosen.items():
            self.assertEqual(self.app.ledger_filters[label].get(), values)

    def test_pending_update_keeps_identity_and_ledger_counts_follow_status(self):
        row = self.seed_flow(status="Pending")
        self.assertEqual([v.get() for v in self.app.metric_vars], ["0", "1", "1"])
        self.select(row)
        self.enter(status="Complete", amount="24.00", date="2026-10-02 13:14:15")
        updated = self.repo.get_flow(row["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(updated["status"], "Complete")
        self.assertEqual(updated["date"], "2026-10-02 13:14:15")
        self.assertEqual(updated['amount'], '24')
        self.assertEqual([v.get() for v in self.app.metric_vars], ["1", "0", "1"])

    def test_large_exact_amount_and_comma_tag_survive_edit(self):
        row = self.seed_flow(amount="90071992547409.93", tags=["Boat, trailer", 'A "quoted" tag'])
        self.select(row)
        self.enter(name="Exact amount preserved")
        updated = self.repo.get_flow(row["flow_id"])
        self.assertEqual(updated["name"], "Exact amount preserved")
        self.assertEqual(updated['amount'], '90071992547409.93')
        self.assertEqual(updated["tags"], ["Boat, trailer", 'A "quoted" tag'])

    def test_saving_while_search_or_status_filter_hides_new_row_is_safe(self):
        self.app.search_var.set("cannot-match-this-record")
        self.enter(from_account="Checking", to_account="Shop", amount="7.25")
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(len(self.app.ledger.get_children()), 0)
        self.app.search_var.set("")
        pending = self.seed_flow(status="Pending", name="Awaiting confirmation")
        self.app.ledger_filters["Status"].set_selected(("Pending",))
        self.app.refresh_ledger()
        self.enter(from_account="Checking", to_account="Shop", amount="8.25")
        self.assertEqual(len(self.repo.list_flows()), 3)
        self.assertEqual(self.app.ledger_filters["Status"].get(), ("Pending",))
        self.assertEqual(self.app.ledger.get_children(), (pending["flow_id"],))

    def test_unknown_opening_remains_unknown_in_explicit_combined_graph(self):
        self.app.new_account()
        self.app.account_vars["name"].set("Savings")
        self.app.account_vars["opening"].set("")
        self.app.save_account()
        self.pump()
        self.assertIsNone(self.repo.get_account("Savings")['opening_amount'])
        self.seed_flow(to_account="Savings")
        self.select_chart("Checking", "Savings")
        self.app.chart_combined.set(True)
        self.app.draw_chart()
        self.pump()
        self.assertTrue(any("Combined balance is unknown" in text.get_text() for text in self.app.axes.texts))

    def test_confirmed_and_pending_graphs_use_saved_flow_values(self):
        self.seed_flow()
        self.seed_flow(amount="15", status="Pending", name="Pending lunch")
        self.seed_flow(amount="200", status="Planned", date="2026-10-10", name="Rent")
        self.seed_flow(amount="300", status="Canceled", name="Voided flow")
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("2026-10-31")
        self.select_chart("Checking")
        for basis, expected in (("Confirmed", 980.0), ("Including Pending", 965.0), ("Including Planned", 765.0)):
            self.app.chart_basis.set(basis)
            self.app.draw_chart()
            self.pump()
            line = next(line for line in self.app.axes.lines if line.get_label() == "Checking")
            self.assertEqual(line.get_ydata()[-1], expected)

    def test_ledger_counts_filter_records_without_ownership_or_double_counting(self):
        self.seed_flow(from_account="Checking", to_account="AmEx", amount="40")
        self.seed_flow(from_account="AmEx", to_account="Shop", status="Pending", amount="15")
        self.seed_flow(status="Planned", amount="8")
        self.seed_flow(status="Canceled", amount="5")
        self.assertEqual([v.get() for v in self.app.metric_vars], ["1", "1", "4"])
        self.assertEqual(self.app.report_summary.get(), 'Complete · 40.00 USD')
        self.app.ledger_filters["Status"].set_selected(("Pending",))
        self.app.refresh_ledger()
        self.assertEqual([v.get() for v in self.app.metric_vars], ["0", "1", "1"])
        self.assertEqual(self.app.report_summary.get(), 'Complete · 0 flows')

    def test_account_gui_and_graph_do_not_infer_ownership(self):
        self.seed_flow()
        self.assertFalse(hasattr(self.app, "account_mine"))
        self.assertFalse(hasattr(self.app, "account_archived"))
        self.assertEqual(set(self.repo.get_account("Checking")), {"name", "units", 'opening_amount', "opening_at"})
        self.assertEqual(len(self.app.chart_accounts.curselection()), 1)
        selected = self.app.chart_accounts.get(self.app.chart_accounts.curselection()[0])
        self.assertEqual(selected, self.app.chart_accounts.get(0))
        self.app.chart_accounts.selection_clear(0, "end")
        self.app.refresh_all()
        self.assertEqual(self.app.chart_accounts.curselection(), ())

    def test_blank_and_explicit_times_survive_edit_and_hover(self):
        row = self.seed_flow(date="2026-10-02")
        self.select(row)
        self.assertIn("2026-10-02", self.app.flow_tooltip(row["flow_id"]))
        self.enter(name="Date only edited")
        self.assertEqual(self.repo.get_flow(row["flow_id"])["date"], "2026-10-02")
        self.select(self.repo.get_flow(row["flow_id"]))
        self.enter(date="2026-10-02 00:00")
        saved = self.repo.get_flow(row["flow_id"])
        self.assertEqual(saved["date"], "2026-10-02 00:00:00")
        self.assertNotIn("time_specified", saved)
        self.assertNotIn("scenario", saved)
        self.assertIn("00:00:00", self.app.flow_tooltip(row["flow_id"]))
        self.app.new_flow(focus=False)
        self.select(saved)
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-02 00:00:00")
        self.app.repeat_selected()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-02 00:00:00")
        self.app.save_button.invoke()
        self.pump()
        self.assertEqual([flow["date"] for flow in self.repo.list_flows()],
                         ["2026-10-02 00:00:00", "2026-10-02 00:00:00"])

    def test_repeat_copies_every_field_and_add_is_one_undoable_action(self):
        original = self.seed_flow(date="2026-10-02 01:02:03", status="Planned",
                                  from_account="AmEx", to_account="Boat store",
                                  amount="1234.56", category="Boat", name="Parts",
                                  notes="First line\nSecond line", tags=["Trip", "Boat, trailer"])
        self.select(original)
        self.assertIn("disabled", self.app.save_button.state())
        self.app.repeat_selected()
        self.pump()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-02 01:02:03")
        self.assertEqual(self.app.form_vars["status"].get(), "Planned")
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertNotIn("disabled", self.app.save_button.state())
        self.app.save_button.invoke()
        self.pump()
        copies = [flow for flow in self.repo.list_flows() if flow["flow_id"] != original["flow_id"]]
        self.assertEqual(len(copies), 1)
        repeated = copies[0]
        self.assertEqual({key: value for key, value in repeated.items() if key != "flow_id"},
                         {key: value for key, value in original.items() if key != "flow_id"})
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.form_vars["amount"].get(), "")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.list_flows(), [original])
        self.app.redo()
        self.pump()
        self.assertEqual(self.repo.get_flow(repeated["flow_id"]), repeated)
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)

    def test_selecting_original_discards_repeat_draft_without_prompt(self):
        original = self.seed_flow()
        self.select(original)
        self.app.repeat_selected()
        self.app.form_vars["amount"].set("999.99")
        self.app.notes_text.insert("end", "Unfinished draft")
        with patch("flowledger.gui.messagebox.askyesnocancel",
                   side_effect=AssertionError("Row navigation must not prompt")):
            self.select(original)
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertEqual(self.app.form_vars["amount"].get(), "20")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "")
        self.assertEqual(self.repo.list_flows(), [original])

    def test_suggestion_x_can_be_undone_and_redone_without_changing_records(self):
        original = self.seed_flow(category="Boat")
        self.select(original)
        self.remove_suggestion("category", "Boat")
        self.assertNotIn("Boat", self.app.form_widgets["category"].values)
        self.app.undo()
        self.pump()
        self.assertIn("Boat", self.app.form_widgets["category"].values)
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.app.redo()
        self.pump()
        self.assertNotIn("Boat", self.app.form_widgets["category"].values)
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.assertEqual(self.app.editing_id, original["flow_id"])

    def test_filter_dialog_undo_does_not_undo_an_unrelated_saved_record(self):
        import tkinter as tk
        from tkinter import ttk
        self.enter(from_account="Checking", to_account="Shop", amount="20.00")
        original = self.repo.list_flows()[0]
        self.select(original)
        self.app.edit_date_filters()
        self.pump()
        dialog = next(widget for widget in self.app.winfo_children() if isinstance(widget, tk.Toplevel))
        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)
        entry = next(widget for widget in descendants(dialog) if isinstance(widget, ttk.Entry))
        entry.focus_force()
        self.pump()
        entry.insert(0, "14:35")
        entry.event_generate("<Control-z>")
        self.pump()
        self.assertEqual(self.repo.list_flows(), [original])
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertTrue(self.app.history.can_undo)
        self.assertFalse(self.app.history.can_redo)
        self.assertTrue(dialog.winfo_exists())
        dialog.destroy()

    def test_empty_redo_keeps_the_current_inline_edit(self):
        original = self.seed_flow()
        self.select(original)
        self.assertTrue(self.app.ledger_editor.open(original["flow_id"], 'amount'))
        self.pump()
        editor = self.app.ledger_editor.input
        editor.delete(0, "end")
        editor.insert(0, "invalid")
        self.assertFalse(self.app.history.can_redo)
        editor.event_generate("<Control-y>")
        self.pump()
        self.assertIs(self.app.ledger_editor.input, editor)
        self.assertEqual(editor.get(), "invalid")
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        editor.event_generate("<Escape>")
        self.pump()

    def test_redo_after_fresh_typing_does_not_replace_existing_unsaved_field(self):
        original = self.seed_flow()
        self.select(original)
        self.enter(amount="21.00")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.assertTrue(self.app.history.can_redo)
        editor = self.app.form_widgets["amount"]
        editor.focus_force()
        self.pump()
        editor.delete(0, "end")
        editor.insert(0, "22.00")
        editor.event_generate("<Control-z>")
        self.pump()
        self.assertEqual(editor.get(), "20")
        editor.delete(0, "end")
        editor.insert(0, "23.00")
        # A stale typing-redo must not fall through to the saved 21.00 edit.
        # Repeating Ctrl+Y must also leave this unfinished field alone.
        for _ in range(2):
            editor.event_generate("<Control-y>")
            self.pump()
            self.assertEqual(editor.get(), "23.00")
            self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
            self.assertEqual(self.app.editing_id, original["flow_id"])
        editor.event_generate("<Escape>")
        self.pump()

    def test_redo_after_fresh_typing_does_not_add_an_undone_flow(self):
        self.enter(from_account="Checking", to_account="Shop", amount="21.00")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.list_flows(), [])
        self.assertTrue(self.app.history.can_redo)
        editor = self.app.form_widgets["amount"]
        editor.focus_force()
        self.pump()
        editor.insert(0, "22.00")
        editor.event_generate("<Control-z>")
        self.pump()
        self.assertEqual(editor.get(), "")
        editor.insert(0, "23.00")
        for _ in range(2):
            editor.event_generate("<Control-y>")
            self.pump()
            self.assertEqual(editor.get(), "23.00")
            self.assertEqual(self.repo.list_flows(), [])
            self.assertIsNone(self.app.editing_id)

    def test_add_and_new_reestablish_typing_undo_when_date_keeps_focus(self):
        editor = self.app.form_widgets["date"]
        editor.focus_force()
        self.pump()
        self.enter(from_account="Checking", to_account="Shop", amount="21.00")
        saved = self.repo.list_flows()[0]
        for reset in (False, True):
            with self.subTest(after_new=reset):
                if reset:
                    editor.event_generate("<Control-n>")
                    self.pump()
                self.assertIs(self.app.focus_get(), editor)
                before = editor.get()
                editor.delete(0, "end")
                editor.insert(0, "2026-11-05")
                editor.event_generate("<Control-z>")
                self.pump()
                self.assertEqual(editor.get(), before)
                self.assertEqual(self.repo.list_flows(), [saved])
                self.assertTrue(self.app.history.can_redo)
                self.assertEqual(self.app.history.redo_label, "Type flow")

    def test_desktop_layout_uses_full_width_and_all_columns(self):
        for height in (1200, 1080):
            self.app.geometry(f"1920x{height}+0+0")
            self.app.show_view("flows")
            self.pump()
            self.assertEqual(len(self.app.ledger["columns"]), 10)
            self.assertEqual(self.app.ledger.xview(), (0.0, 1.0))
            self.assertGreater(self.app.ledger.winfo_width(), 1800)
            self.assertGreater(self.app.ledger.winfo_height(), height*.5)
            self.assertLess(max(c.winfo_y() for c in self.app.flow_cells), 5)
            self.app.show_view("accounts")
            self.pump()
            self.assertGreater(self.app.accounts_tree.winfo_width(), 1800)
            self.assertEqual(self.app.accounts_tree.xview(), (0.0, 1.0))
            self.assertLess(max(c.winfo_y() for c in self.app.account_cells), 5)
        self.app.show_view("flows")
        self.app.geometry("1280x800+0+0")
        self.pump()
        self.assertEqual(self.app.ledger.xview(), (0.0, 1.0))
        self.assertGreater(self.app.flow_cells[-1].winfo_y(), 40)
        for cell in self.app.flow_cells:
            self.assertLessEqual(cell.winfo_rootx() + cell.winfo_width(), 1280)


    def test_single_datetime_field_sets_an_explicit_time(self):
        self.assertNotIn("time", self.app.form_vars)
        self.assertNotIn("time", self.app.account_vars)
        self.assertFalse(hasattr(self.app, "time_button"))
        self.enter(date="2026-10-9 7:5", from_account="Checking", to_account="Shop", amount="3")
        row = self.repo.list_flows()[0]
        self.assertNotIn("time_specified", row)
        self.assertEqual(row["date"], "2026-10-09 07:05:00")

    def test_date_dialog_accepts_timed_start_with_same_date_end(self):
        import tkinter as tk
        from tkinter import ttk
        self.app.filter_start.set("2026-10-2 12")
        self.app.filter_end.set("2026-10-2")
        self.app.edit_date_filters()
        self.pump()
        dialog = next(w for w in self.app.winfo_children() if isinstance(w, tk.Toplevel))
        def apply(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and child.cget("text") == "Apply":
                    child.invoke()
                    return True
                if apply(child):
                    return True
        self.assertTrue(apply(dialog))
        self.pump()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.app.filter_end.get(), "2026-10-02")


    def test_graph_date_only_end_includes_afternoon_flows(self):
        self.seed_flow(date="2026-10-02 14:00", amount="25.00")
        self.select_chart("Checking")
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("2026-10-02")
        self.app.chart_basis.set("Confirmed")
        self.app.draw_chart()
        self.pump()
        line = next(line for line in self.app.axes.lines if line.get_label() == "Checking")
        self.assertEqual(line.get_ydata()[-1], 975.0)

    def test_full_datetime_in_date_field_replaces_previous_time(self):
        row = self.seed_flow(date="2026-10-02 08:09:10")
        for entered, expected in (("2026-10-03 17:18:19", "2026-10-03 17:18:19"),
                                  ("2026-10-04T01:02:03", "2026-10-04 01:02:03")):
            self.select(self.repo.get_flow(row["flow_id"]))
            self.enter(date=entered)
            self.assertEqual(self.repo.get_flow(row["flow_id"])["date"], expected)
            self.assertEqual(len(self.repo.list_flows()), 1)

    def test_datetime_field_focusout_and_enter_keep_the_whole_timestamp(self):
        row = self.seed_flow(date="2026-10-02 08:09:10")
        self.select(row)
        date_widget = self.app.form_widgets["date"]
        date_widget.focus_force()
        self.pump()
        self.app.form_vars["date"].set("2026-10-3T17:18:19")
        self.app.notes_text.focus_set()
        self.pump()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-03 17:18:19")
        self.assertEqual(self.repo.get_flow(row["flow_id"])["date"], "2026-10-03 17:18:19")
        date_widget.focus_force()
        self.pump()
        self.app.form_vars["date"].set("2026-10-4 9")
        date_widget.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(row["flow_id"])["date"], "2026-10-04 09:00:00")

    def test_graph_map_and_resize_fit_scaled_canvas_and_date_labels(self):
        original_scaling = float(self.app.tk.call("tk", "scaling"))
        try:
            self.app.tk.call("tk", "scaling", 1.65)
            self.seed_flow(date="2026-10-02 12:30:15", amount="3")
            self.select_chart("Checking")
            self.app.chart_start.set("2026-10-01")
            self.app.chart_end.set("2026-10-31")
            self.app.geometry("1920x1200+0+0")
            self.app.show_view("charts")
            for geometry in ("1920x1200+0+0", "1280x800+0+0"):
                self.app.geometry(geometry)
                self.pump()
                self.app.draw_chart()
                self.pump()
                canvas = self.app.canvas.get_tk_widget()
                bounds = self.app.figure.bbox
                self.assertAlmostEqual(bounds.width, canvas.winfo_width(), delta=1)
                self.assertAlmostEqual(bounds.height, canvas.winfo_height(), delta=1)
                renderer = self.app.canvas.get_renderer()
                for label in self.app.axes.get_xticklabels():
                    if not label.get_visible() or not label.get_text():
                        continue
                    box = label.get_window_extent(renderer)
                    self.assertGreaterEqual(box.x0, bounds.x0 - 1)
                    self.assertGreaterEqual(box.y0, bounds.y0 - 1)
                    self.assertLessEqual(box.x1, bounds.x1 + 1)
                    self.assertLessEqual(box.y1, bounds.y1 + 1)
        finally:
            self.app.tk.call("tk", "scaling", original_scaling)

    def test_second_boundaries_in_ledger_and_graph_filters(self):
        date_only = self.seed_flow(date="2026-10-02", amount="2")
        first = self.seed_flow(date="2026-10-02 12:30:15", amount="3")
        later = self.seed_flow(date="2026-10-02 12:30:16", amount="7")
        self.app.filter_start.set("2026-10-02 12:30:15")
        self.app.filter_end.set("2026-10-02 12:30:15")
        self.app.refresh_ledger()
        self.assertEqual(set(self.app.ledger_rows), {first["flow_id"]})
        self.app.filter_start.set("2026-10-02")
        self.app.filter_end.set("2026-10-02")
        self.app.refresh_ledger()
        self.assertEqual(set(self.app.ledger_rows), {date_only["flow_id"], first["flow_id"], later["flow_id"]})
        self.app.filter_start.set("2026-10-2")
        self.app.filter_end.set("2026-10-2")
        self.app.refresh_ledger()
        self.assertEqual(set(self.app.ledger_rows), {date_only["flow_id"], first["flow_id"], later["flow_id"]})
        self.app.chart_start.set("2026-10-01")
        self.app.chart_basis.set("Confirmed")
        self.select_chart("Checking")
        for ending, balance in (("12:30:14", 998.0), ("12:30:15", 995.0), ("12:30:16", 988.0)):
            self.app.chart_end.set("2026-10-02 " + ending)
            self.app.draw_chart()
            self.pump()
            line = next(line for line in self.app.axes.lines if line.get_label() == "Checking")
            self.assertEqual(line.get_ydata()[-1], balance)

    def test_ledger_search_accepts_displayed_decimal_amounts(self):
        row = self.seed_flow(amount="1000.50")
        self.seed_flow(amount="2.00", name="Another amount")
        for query in ("1000.50", "1,000.50"):
            self.app.search_var.set(query)
            self.pump()
            self.assertEqual(list(self.app.ledger_rows), [row["flow_id"]])


    def test_closing_notes_dialog_does_not_reopen_previous_dropdown(self):
        import tkinter as tk
        from tkinter import ttk
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        for button in (self.app.notes_button,):
            combo = self.app.form_widgets["category"]
            combo.entry.focus_force()
            self.pump()
            combo.open_popup(show_all=True)
            self.assertIsNotNone(combo.popup)
            button.invoke()
            self.pump()
            self.assertIsNone(combo.popup)
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, tk.Toplevel))
            close_label = "Close"
            next(w for w in children(dialog) if isinstance(w, ttk.Button) and w.cget("text") == close_label).invoke()
            self.app.after(180, self.app.quit)
            self.app.mainloop()
            self.pump()
            self.assertIsNone(combo.popup)



if __name__ == "__main__":
    unittest.main()
