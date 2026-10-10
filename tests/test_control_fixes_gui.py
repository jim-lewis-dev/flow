"""Deletion, starting-balance caret and all six ledger filters through Tk."""
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository, balance_at


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ControlFixGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "controls.sqlite3")
        self.repo.save_account("Warehouse", "10.5", "2026-10-01", units="kg")
        self.repo.save_account("Workshop", "0", "2026-10-01", units="kg")
        self.repo.save_account("Unused", "42", "2026-10-01", units="unused-unit")
        self.flows = []
        for index, units, status, category, tags in (
            (1, "kg", "Complete", "Inventory", ["Batch one", "Measured", "Trip"]),
            (2, "USD", "Pending", "Supplies", ["Trip"]),
            (3, "mW", "Complete", "Energy", ["Measured"]),
            (4, "MW", "Complete", "Energy", ["Measured"]),
            (5, "void-only", "Void", "Correction", ["Corrected"]),
        ):
            self.flows.append(self.repo.save_flow(dict(
                date=f"2026-10-0{index} 12:34:56", name=f"Flow {index}",
                from_account="Warehouse", to_account="Workshop", amount=".125",
                units=units, status=status, category=category, tags=tags,
                notes="First line\nSecond line\nExact saved note.")))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.dialog = patch("flowledger.gui.messagebox.showerror", side_effect=lambda *args, **kwargs: self.fail(str(args)))
        self.dialog.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        try:
            self.app._close_popups()
            for choice in self.app.ledger_filters.values():
                choice._hide_tooltip()
            self.app.history.close()
            self.app.destroy()
        finally:
            self.dialog.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def shortcut(self, sequence, widget=None):
        widget = widget or self.app
        widget.focus_force()
        self.pump()
        widget.event_generate(sequence)
        self.pump()

    def select(self, flow=None):
        self.app.ledger.selection_set((flow or self.flows[0])["flow_id"])
        self.pump()

    def select_filter(self, key, *values):
        self.app.ledger_filters[key].set_selected(values, notify=True)
        self.pump()

    def button(self, caption):
        from tkinter import ttk
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        return next(widget for widget in descendants(self.app)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == caption)

    def test_starting_balance_first_click_places_caret_at_end(self):
        self.app.show_view("accounts")
        self.app.accounts_tree.selection_set('["Warehouse", "kg"]')
        self.pump()
        entry = self.app.account_widgets["opening"]
        self.app.account_widgets["date"].focus_force()
        self.pump()
        entry.event_generate("<ButtonPress-1>", x=3, y=entry.winfo_height() // 2)
        entry.event_generate("<ButtonRelease-1>", x=3, y=entry.winfo_height() // 2)
        self.pump()
        self.assertIs(self.app.focus_get(), entry)
        self.assertEqual(entry.index("insert"), len(entry.get()))
        # Editing can subsequently move the caret; the focus default is one-time.
        entry.icursor(0)
        self.pump()
        self.assertEqual(entry.index("insert"), 0)

    def test_inline_starting_balance_caret_is_at_end_with_full_value_selected(self):
        self.app.show_view("accounts")
        self.pump()
        self.assertTrue(self.app.account_cell_editor.open('["Warehouse", "kg"]', "balance"))
        self.pump()
        entry = self.app.account_cell_editor.input
        self.assertEqual(entry.index("insert"), len(entry.get()))
        self.assertEqual((entry.index("sel.first"), entry.index("sel.last")), (0, len(entry.get())))

    def test_all_filter_captions_are_explicit_and_tooltips_show_full_selections(self):
        labels = {"From": "From", "To": "To", "Status": "Status",
                  "Category": "Categories", "Tag": "Tags", "Units": "Units"}
        self.assertEqual(list(self.app.ledger_filters), list(labels))
        for key, label in labels.items():
            with self.subTest(filter=key):
                choice = self.app.ledger_filters[key]
                self.assertEqual(choice.button.cget("text"), f"{label}: All ▾")
                self.assertEqual(choice.tooltip_text(), "All")
                values = ("First selected long value", "Second selected value", "Third selected value")
                choice.set_selected(values)
                choice._show_tooltip()
                self.pump()
                self.assertIsNotNone(choice._tooltip_popup)
                self.assertEqual(choice._tooltip_label.cget("text"), "\n".join(values))
                choice.set_selected((values[2],))
                self.pump()
                self.assertEqual(choice._tooltip_label.cget("text"), values[2])
                choice.set_selected(())
                self.pump()
                self.assertEqual(choice._tooltip_label.cget("text"), "All")
                choice._hide_tooltip()

    def test_hover_tooltip_is_cancelled_when_filter_opens_and_on_mouse_leave(self):
        choice = self.app.ledger_filters["Tag"]
        values = ("Batch one", "Measured", "Trip")
        choice.set_selected(values)
        choice.button.event_generate("<Enter>", x=5, y=5)
        self.app.after(500, self.app.quit)
        self.app.mainloop()
        self.assertEqual(choice._tooltip_label.cget("text"), "\n".join(values))
        choice.button.invoke()
        self.pump()
        self.assertIsNone(choice._tooltip_popup)
        self.assertIsNotNone(choice.popup)
        choice.close_popup()
        choice.button.event_generate("<Enter>", x=5, y=5)
        self.assertIsNotNone(choice._tooltip_after_id)
        choice.button.event_generate("<Leave>", x=5, y=5)
        self.assertIsNone(choice._tooltip_after_id)
        self.assertIsNone(choice._tooltip_popup)

    def test_units_filter_is_exact_case_sensitive_or_and_combines_with_other_filters(self):
        self.select_filter("Units", "mW")
        self.assertEqual(set(self.app.ledger_rows), {self.flows[2]["flow_id"]})
        self.select_filter("Units", "mW", "MW")
        self.assertEqual(set(self.app.ledger_rows), {flow["flow_id"] for flow in self.flows[2:4]})
        self.select_filter("Units", "kg", "USD")
        self.select_filter("From", "Warehouse")
        self.select_filter("To", "Workshop")
        self.select_filter("Status", "Complete", "Pending")
        self.select_filter("Category", "Inventory")
        self.select_filter("Tag", "Measured")
        self.assertEqual(set(self.app.ledger_rows), {self.flows[0]["flow_id"]})
        self.assertEqual(self.app.report_summary.get(), "Complete · 0.125 kg")

    def test_units_choices_follow_saved_flows_including_void_and_not_other_filters(self):
        expected = {flow["units"] for flow in self.flows}
        self.select_filter("Status", "Complete")
        self.select_filter("Units", "kg")
        self.app.form_vars["units"].set("unadded-unit")
        self.app.search_var.set("no matching flow")
        self.app.refresh_all()
        self.pump()
        self.assertEqual(set(self.app.ledger_filters["Units"].values), expected)
        self.assertIn("void-only", expected)
        self.assertNotIn("unused-unit", expected)
        self.assertNotIn("unadded-unit", expected)

    def test_units_checkbox_and_popup_search_undo_preserve_invalid_selected_draft(self):
        self.select()
        self.app.form_vars["amount"].set("unfinished")
        draft = self.app._flow_snapshot()
        saved = self.repo.list_flows()
        choice = self.app.ledger_filters["Units"]
        choice.button.invoke()
        self.pump()
        choice.checkbuttons["kg"].invoke()
        self.pump()
        choice.checkbuttons["USD"].invoke()
        self.pump()
        popup = choice.popup
        self.shortcut("<Control-z>", choice.checkbuttons["USD"])
        self.assertIs(choice.popup, popup)
        self.assertEqual(choice.get(), ("kg",))
        self.shortcut("<Control-y>", choice.checkbuttons["USD"])
        self.assertEqual(choice.get(), ("kg", "USD"))
        entry = choice.search_entry
        entry.focus_force()
        self.pump()
        for char in "mw":
            entry.insert("end", char)
        self.pump()
        self.assertEqual(set(choice.checkbuttons), {"mW", "MW"})
        self.shortcut("<Control-z>", entry)
        self.assertEqual(entry.get(), "")
        self.shortcut("<Control-y>", entry)
        self.assertEqual(entry.get(), "mw")
        self.assertEqual(choice.get(), ("kg", "USD"))
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual(self.repo.list_flows(), saved)

    def test_units_all_checkbox_clears_selection_and_can_be_undone(self):
        choice = self.app.ledger_filters["Units"]
        self.select_filter("Units", "kg", "USD")
        choice.button.invoke()
        self.pump()
        choice.all_button.invoke()
        self.pump()
        self.assertEqual(choice.get(), ())
        self.assertEqual(len(self.app.ledger_rows), len(self.flows))
        self.shortcut("<Control-z>", choice.all_button)
        self.assertEqual(choice.get(), ("kg", "USD"))
        self.assertFalse(choice._all_variable.get())
        self.shortcut("<Control-y>", choice.all_button)
        self.assertEqual(choice.get(), ())
        self.assertTrue(choice._all_variable.get())

    def test_delete_key_removes_exact_flow_and_undo_redo_refresh_all_dependent_views(self):
        flow = self.flows[0]
        self.select(flow)
        account = self.repo.get_account("Warehouse", "kg")
        self.assertEqual(balance_at(account, self.repo.list_flows(), "2026-10-10"), Decimal("10.375"))
        self.shortcut("<Delete>", self.app.ledger)
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))
        self.assertNotIn(flow["flow_id"], self.app.ledger_rows)
        self.assertNotIn("kg", self.app.ledger_filters["Units"].values)
        self.assertNotIn('["Warehouse", "kg"]', self.app.account_rows)
        self.assertNotIn(("Warehouse", "kg"), self.app.chart_account_pairs)
        self.assertEqual(balance_at(account, self.repo.list_flows(), "2026-10-10"), Decimal("10.5"))
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app._flow_snapshot(), self.app._clean_flow)
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertIn(flow["flow_id"], self.app.ledger_rows)
        self.assertIn("kg", self.app.ledger_filters["Units"].values)
        self.assertIn('["Warehouse", "kg"]', self.app.account_rows)
        self.assertIn(("Warehouse", "kg"), self.app.chart_account_pairs)
        self.assertEqual(balance_at(account, self.repo.list_flows(), "2026-10-10"), Decimal("10.375"))
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))
        self.assertEqual(len(self.repo.list_flows()), len(self.flows) - 1)

    def test_delete_button_discards_invalid_existing_draft_but_undo_restores_saved_record(self):
        flow = self.flows[0]
        self.select(flow)
        self.app.form_vars["amount"].set("invalid amount")
        self.button("Delete").invoke()
        self.pump()
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))
        self.assertEqual(self.app._flow_snapshot(), self.app._clean_flow)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.shortcut("<Control-y>")
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))

    def test_delete_with_no_selection_changes_neither_database_nor_history(self):
        before = "\n".join(self.repo.conn.iterdump())
        self.assertFalse(self.app.history.can_undo)
        self.shortcut("<Delete>", self.app.ledger)
        self.button("Delete").invoke()
        self.pump()
        self.assertEqual("\n".join(self.repo.conn.iterdump()), before)
        self.assertFalse(self.app.history.can_undo)

    def test_delete_in_top_field_inline_cell_and_notes_edits_text_without_removing_flow(self):
        flow = self.flows[0]
        self.select(flow)
        entry = self.app.form_widgets["name"].entry
        entry.focus_force()
        self.pump()
        entry.selection_clear()
        entry.icursor(0)
        self.shortcut("<Delete>", entry)
        self.assertEqual(entry.get(), "low 1")
        self.assertIsNotNone(self.repo.get_flow(flow["flow_id"]))
        entry.event_generate("<Return>")
        self.pump()
        self.assertTrue(self.app.ledger_editor.open(flow["flow_id"], "name"))
        self.pump()
        inline = self.app.ledger_editor.input
        inline.selection_clear()
        inline.icursor(0)
        self.shortcut("<Delete>", inline)
        self.assertEqual(inline.get(), "ow 1")
        self.assertIsNotNone(self.repo.get_flow(flow["flow_id"]))
        inline.event_generate("<Return>")
        self.pump()
        notes = self.app.notes_text
        notes.mark_set("insert", "1.0")
        self.shortcut("<Delete>", notes)
        self.assertTrue(notes.get("1.0", "end-1c").startswith("irst line"))
        self.assertIsNotNone(self.repo.get_flow(flow["flow_id"]))
        self.assertEqual(len(self.repo.list_flows()), len(self.flows))

    def test_void_keeps_saved_row_while_delete_removes_it_and_both_undo(self):
        flow = self.flows[0]
        self.select(flow)
        self.button("Void").invoke()
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["status"], "Void")
        self.assertIn(flow["flow_id"], self.app.ledger_rows)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.select(flow)
        self.shortcut("<Delete>", self.app.ledger)
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_delete_retains_unit_filter_selection_and_undo_returns_matching_row(self):
        flow = self.flows[0]
        self.select_filter("Units", "kg")
        self.select(flow)
        self.shortcut("<Delete>", self.app.ledger)
        self.assertEqual(self.app.ledger_filters["Units"].get(), ("kg",))
        self.assertEqual(self.app.ledger_rows, {})
        self.shortcut("<Control-z>")
        self.assertEqual(set(self.app.ledger_rows), {flow["flow_id"]})
        self.assertEqual(self.app.ledger_filters["Units"].get(), ("kg",))
        self.shortcut("<Control-y>")
        self.assertEqual(self.app.ledger_rows, {})
