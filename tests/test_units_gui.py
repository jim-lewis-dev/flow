"""Resource units across entry, inline edits, account anchors and graph history."""
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class UnitsGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "units.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01", units="USD")
        self.repo.save_account("Checking", "1.5", "2026-10-01", units="kg")
        self.repo.save_account("Warehouse", "2", "2026-10-01", units="kg")
        self.errors = []
        errors = self.errors
        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")
        self.dialog = patch("flowledger.gui.messagebox.showerror", side_effect=lambda *a, **k: self.fail(str(a)))
        self.dialog.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        try:
            self.app._close_popups()
            self.app.after(150, self.app.quit)
            self.app.mainloop()
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

    def seed(self, **changes):
        values = dict(date="2026-10-02 12:34:56", from_account="Checking", to_account="Warehouse",
                      amount=".125", units="kg", name="Transfer", category="Inventory", tags=["Batch"], notes="Measured")
        values.update(changes)
        flow = self.repo.save_flow(values)
        self.app.refresh_all()
        self.pump()
        return flow

    def select(self, flow):
        self.app.ledger.selection_set(flow["flow_id"])
        self.pump()

    def entry(self, key):
        widget = self.app.form_widgets[key]
        return widget.entry if hasattr(widget, "entry") else widget

    def replace(self, entry, value):
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, value)
        self.pump()

    def graph(self, pairs, combined=False):
        self.app.show_view("charts")
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("2026-10-03")
        self.app.chart_accounts.selection_clear(0, "end")
        for index, pair in enumerate(self.app.chart_account_pairs):
            if pair in pairs:
                self.app.chart_accounts.selection_set(index)
        self.app.chart_combined.set(combined)
        self.app.draw_chart()
        self.pump()
        return {(line.get_label(), self.app.chart_line_units[line]): rows for line, rows in self.app.chart_series.items()}

    def test_default_column_and_form_order_matches_requested_fields(self):
        columns = ("date", "name", "from_account", "to_account", "amount", "units", "status", "category", "tags", "notes")
        self.assertEqual(tuple(self.app.ledger["columns"]), columns)
        self.assertEqual(tuple(self.app.ledger["displaycolumns"]), ("#all",))
        self.assertEqual(tuple(self.app.form_widgets), columns[:-1])
        self.assertTrue(self.app.notes_text.winfo_exists())
        self.assertEqual(self.app.form_vars["units"].get(), "USD")
        self.assertEqual(self.app.account_vars["units"].get(), "USD")

    def test_missing_units_prevents_add_without_creating_partial_records(self):
        for key, value in {"from_account": "Checking", "to_account": "Warehouse", "amount": ".125", "units": ""}.items():
            self.app.form_vars[key].set(value)
        before = self.repo.list_accounts()
        self.assertFalse(self.app.save_flow())
        self.assertIn("unit", self.app.form_feedback.get().casefold())
        self.assertEqual(self.repo.list_flows(), [])
        self.assertEqual(self.repo.list_accounts(), before)
        self.assertEqual(self.app.form_vars["amount"].get(), ".125")

    def test_add_preserves_fraction_and_new_unit_is_available_next_time(self):
        for key, value in {"from_account": "Tank", "to_account": "Bottle", "amount": ".000125", "units": "litres", "name": "Sample"}.items():
            self.app.form_vars[key].set(value)
        self.assertTrue(self.app.save_flow())
        saved, = self.repo.list_flows()
        self.assertEqual((saved["amount"], saved["units"]), ("0.000125", "litres"))
        self.assertIn("litres", self.app.form_widgets["units"].values)
        self.assertEqual(self.app.ledger.set(saved["flow_id"], "amount"), "0.0001")
        self.assertEqual(self.app.ledger.set(saved["flow_id"], "units"), "litres")
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.form_vars["units"].get(), "USD")
        self.app.undo()
        self.assertEqual(self.repo.list_flows(), [])
        self.app.redo()
        self.assertEqual(self.repo.list_flows(), [saved])

    def test_units_inline_edit_updates_shared_form_and_undo_restores_balances(self):
        flow = self.seed()
        self.select(flow)
        self.assertTrue(self.app.ledger_editor.open(flow["flow_id"], "units"))
        self.replace(self.app.ledger_editor.input, "USD")
        self.assertEqual(self.app.form_vars["units"].get(), "USD")
        self.app.ledger_editor.input.event_generate("<Return>")
        self.pump()
        saved = self.repo.get_flow(flow["flow_id"])
        self.assertEqual(saved, {**flow, "units": "USD"})
        self.assertEqual(self.app.ledger.set(flow["flow_id"], "units"), "USD")
        self.app.undo()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.app.redo()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), saved)
        self.assertEqual(self.repo.get_account("Checking", "kg")["opening_amount"], "1.5")

    def test_blank_units_on_existing_record_stay_correctable_without_partial_save(self):
        flow = self.seed()
        self.select(flow)
        self.replace(self.entry("units"), "")
        self.entry("units").event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertEqual(self.app.form_vars["units"].get(), "")
        self.assertIn("unit", self.app.form_feedback.get().casefold())
        self.replace(self.entry("units"), "kg")
        self.entry("units").event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_repeat_copies_units_fraction_timestamp_and_all_metadata(self):
        flow = self.seed()
        self.select(flow)
        self.app.repeat_selected()
        self.pump()
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.form_vars["units"].get(), "kg")
        self.assertEqual(self.app.form_vars["amount"].get(), "0.125")
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-02 12:34:56")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Measured")
        self.assertTrue(self.app.save_flow())
        rows = self.repo.list_flows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([{k: v for k, v in row.items() if k != "flow_id"} for row in rows],
                         [{k: v for k, v in flow.items() if k != "flow_id"}] * 2)


    def test_each_account_unit_has_its_own_inline_starting_balance(self):
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.app.show_view("accounts")
        key = self.app._account_key(self.repo.get_account("Checking", "kg"))
        usd_key = self.app._account_key(self.repo.get_account("Checking", "USD"))
        self.assertNotEqual(key, usd_key)
        self.assertTrue(self.app.accounts_tree.exists(key))
        self.assertTrue(self.app.accounts_tree.exists(usd_key))
        self.app.accounts_tree.selection_set(key)
        self.pump()
        self.assertEqual(self.app.account_vars["units"].get(), "kg")
        self.assertTrue(self.app.account_cell_editor.open(key, "balance"))
        self.replace(self.app.account_cell_editor.input, "1.750125")
        self.app.account_cell_editor.input.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_account("Checking", "kg")["opening_amount"], "1.750125")
        self.assertEqual(self.repo.get_account("Checking", "USD")["opening_amount"], "100")
        self.app.undo()
        self.assertEqual(self.repo.get_account("Checking", "kg")["opening_amount"], "1.5")
        self.app.redo()
        self.assertEqual(self.repo.get_account("Checking", "kg")["opening_amount"], "1.750125")

    def test_mixed_units_are_separate_plots_and_combining_never_adds_units(self):
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        pairs = (("Checking", "USD"), ("Checking", "kg"), ("Warehouse", "kg"))
        rows = self.graph(pairs)
        self.assertEqual(set(self.app.chart_axes), {"USD", "kg"})
        self.assertEqual(rows["Checking", "USD"][-1][1], Decimal("90"))
        self.assertEqual(rows["Checking", "kg"][-1][1], Decimal("1.375"))
        self.assertEqual(rows["Warehouse", "kg"][-1][1], Decimal("2.125"))
        rows = self.graph(pairs, combined=True)
        self.assertEqual(set(rows), {("Combined", "USD"), ("Combined", "kg")})
        self.assertEqual(rows["Combined", "USD"][-1][1], Decimal("90"))
        self.assertEqual(rows["Combined", "kg"][-1][1], Decimal("3.5"))

    def test_unknown_anchor_affects_its_unit_only_and_warning_names_pair(self):
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.repo.save_account("Checking", None, "2026-10-01", units="kg")
        self.app.refresh_all()
        rows = self.graph((("Checking", "USD"), ("Checking", "kg")), combined=True)
        self.assertEqual(set(rows), {("Combined", "USD")})
        self.assertEqual(rows["Combined", "USD"][-1][1], Decimal("90"))
        self.assertIn("Checking · kg", self.app.chart_message.get())
        self.assertTrue(any("Combined balance is unknown" in text.get_text()
                            for text in self.app.chart_axes["kg"].texts))

    def test_graph_history_restores_both_unit_selections_axes_and_visibility(self):
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.graph((("Checking", "USD"), ("Checking", "kg")))
        self.app.view_history.remember("graph")
        before = self.app._graph_state()
        kg_line = next(line for line in self.app.chart_series if self.app.chart_line_units[line] == "kg")
        legend = next(key for key, line in self.app.chart_line_map.items() if line is kg_line)
        self.app.toggle_chart_line(SimpleNamespace(artist=legend))
        self.app.chart_axes["kg"].set_ylim(-5, 5)
        self.pump()
        self.app.undo()
        self.app.undo()
        self.pump()
        self.assertEqual(self.app._graph_state(), before)
        self.app.redo()
        self.app.redo()
        self.pump()
        self.assertFalse(next(line for line in self.app.chart_series if self.app.chart_line_units[line] == "kg").get_visible())
        self.assertEqual(self.app.chart_axes["kg"].get_ylim(), (-5, 5))
        self.assertEqual(self.app._graph_controls()[0], (("Checking", "USD"), ("Checking", "kg")))

    def test_confirmed_and_pending_balances_are_calculated_for_each_unit(self):
        self.seed()
        self.seed(amount=".25", status="Pending", name="Awaiting transfer")
        self.seed(units="USD", to_account="Shop", amount="10")
        self.seed(units="USD", to_account="Shop", amount="25", status="Pending")
        self.app.show_view("accounts")
        for units, confirmed, pending in (("USD", "90.00", "65.00"), ("kg", "1.3750", "1.1250")):
            with self.subTest(units=units):
                key = self.app._account_key(self.repo.get_account("Checking", units))
                self.assertEqual(self.app.accounts_tree.set(key, "confirmed"), confirmed)
                self.assertEqual(self.app.accounts_tree.set(key, "pending"), pending)
        self.assertEqual(self.app.account_summary.get(), "3 accounts · 4 balances")

    def test_add_starting_balance_for_an_existing_name_keeps_other_units(self):
        self.app.show_view("accounts")
        for key, value in {"name": "Checking", "units": "seconds", "opening": "180.125", "date": "2026-10-03 8"}.items():
            self.app.account_vars[key].set(value)
        self.assertTrue(self.app.save_account())
        saved = self.repo.get_account("Checking", "seconds")
        self.assertEqual(saved, dict(name="Checking", units="seconds", opening_amount="180.125", opening_at="2026-10-03 08:00:00"))
        self.assertEqual(self.repo.get_account("Checking", "USD")["opening_amount"], "100")
        self.assertEqual(self.repo.get_account("Checking", "kg")["opening_amount"], "1.5")
        self.assertNotIn("seconds", self.app.form_widgets["units"].values)
        self.assertEqual(self.app.accounts_tree.get_children(), ())
        self.app.undo()
        self.assertIsNone(self.repo.get_account("Checking", "seconds"))
        self.assertIsNotNone(self.repo.get_account("Checking", "USD"))
        self.app.redo()
        self.assertEqual(self.repo.get_account("Checking", "seconds"), saved)
        self.assertNotIn("seconds", self.app.form_widgets["units"].values)
        self.seed(units="seconds", date="2026-10-04", amount="10")
        self.assertIn("seconds", self.app.form_widgets["units"].values)

    def test_category_report_keeps_same_category_and_status_separate_by_units(self):
        from tkinter import ttk
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.app.show_report()
        self.pump()
        def trees(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Treeview):
                    yield child
                yield from trees(child)
        report = next(tree for tree in trees(self.app) if tuple(tree["columns"]) == ("category", "status", "amount", "units", "count"))
        rows = [tuple(report.item(key, "values")) for key in report.get_children()]
        self.assertEqual(set(rows), {("Inventory", "Complete", "10.00", "USD", "1"), ("Inventory", "Complete", "0.1250", "kg", "1")})
        report.winfo_toplevel().destroy()

    def test_category_report_headings_sort_exact_totals_and_counts_like_ledger(self):
        from tkinter import ttk
        for category, amounts in (("A", ["1"] * 10), ("B", ["1"] * 2),
                                  ("C", ["-2.00001"]), ("Z", ["-2.00002"])):
            for amount in amounts:
                self.repo.save_flow(dict(date="2026-10-01", from_account="Warehouse",
                    to_account="Checking", amount=amount, units="kg", category=category))
        self.app.refresh_all()
        before = list(self.repo.conn.iterdump())
        self.app.show_report()
        self.pump()
        dialog = next(w for w in self.app.winfo_children()
                      if w.winfo_class() == "Toplevel" and w.title().startswith("Category totals"))
        tree = next(w for w in dialog.winfo_children() if isinstance(w, ttk.Treeview))
        original = tree.get_children()
        tree.selection_set(original[:2])
        tree.focus(original[0])
        def sort(column):
            command = tree.heading(column, "command")
            self.assertTrue(command)
            tree.tk.call(command)
            self.pump()
            return [tree.set(row, "category") for row in tree.get_children()]
        self.assertEqual(sort("amount"), ["Z", "C", "B", "A"])
        self.assertEqual(sort("amount"), ["A", "B", "C", "Z"])
        self.assertEqual(sort("count"), ["C", "Z", "B", "A"])
        self.assertEqual(sort("count"), ["A", "B", "C", "Z"])
        self.assertEqual(sort("category"), ["A", "B", "C", "Z"])
        self.assertEqual(sort("category"), ["Z", "C", "B", "A"])
        for column in ("status", "units"):
            sort(column)
            sort(column)
        self.assertEqual(set(tree.selection()), set(original[:2]))
        self.assertEqual(tree.focus(), original[0])
        self.assertEqual(list(self.repo.conn.iterdump()), before)
        dialog.destroy()

    def test_other_table_popups_have_sortable_headings_without_changing_records(self):
        from tkinter import ttk
        self.seed()
        before = list(self.repo.conn.iterdump())
        records = [dict(date="2026-10-04", name=f"Import {index}", from_account="Checking",
                        to_account="Store", units="kg", amount=amount)
                   for index, amount in enumerate(["10", "2", "-3"] + ["1"] * 8 + ["bad"])]
        with patch("flowledger.gui.filedialog.askopenfilename", return_value="preview.json"), \
                patch("flowledger.gui.read_import", return_value=records):
            self.app.import_records()
        self.pump()
        dialog = self.app.grab_current()
        tree = next(w for w in dialog.winfo_children() if isinstance(w, ttk.Treeview))
        tree.tk.call(tree.heading("row", "command"))
        self.assertEqual([int(tree.set(row, "row")) for row in tree.get_children()], list(range(1, 13)))
        tree.tk.call(tree.heading("amount", "command"))
        self.assertEqual(tree.set(tree.get_children()[0], "amount"), "-3.0000")
        for column in tree["columns"]:
            self.assertTrue(tree.heading(column, "command"))
            tree.tk.call(tree.heading(column, "command"))
            tree.tk.call(tree.heading(column, "command"))
        dialog.destroy()
        self.app.show_history(all_records=True)
        self.pump()
        dialog = next(w for w in self.app.winfo_children()
                      if w.winfo_class() == "Toplevel" and w.title() == "Change history")
        tree = next(w for w in dialog.winfo_children() if isinstance(w, ttk.Treeview))
        original = set(tree.get_children())
        for column in tree["columns"]:
            command = tree.heading(column, "command")
            self.assertTrue(command)
            tree.tk.call(command)
            tree.tk.call(command)
        self.assertEqual(set(tree.get_children()), original)
        self.assertEqual(list(self.repo.conn.iterdump()), before)
        dialog.destroy()

    def test_stacked_graph_hover_reports_the_selected_unit_exactly(self):
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.dates import date2num
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.graph((("Checking", "USD"), ("Checking", "kg")))
        self.app.canvas.draw()
        axes = self.app.chart_axes["kg"]
        x, y = axes.transData.transform((date2num(datetime(2026, 10, 3)), 1.375))
        event = MouseEvent("motion_notify_event", self.app.canvas, x, y)
        self.app.canvas.callbacks.process("motion_notify_event", event)
        self.pump()
        tooltip = self.app.chart_tooltips[self.app.chart_axes["kg"]]
        self.assertTrue(tooltip.get_visible())
        self.assertIn("Checking: 1.375 kg", tooltip.get_text())
        self.assertNotIn("USD", tooltip.get_text())
        self.assertFalse(self.app.chart_tooltips[self.app.chart_axes["USD"]].get_visible())

    def test_complete_volume_lists_units_separately(self):
        self.seed()
        self.seed(units="USD", to_account="Shop", amount="10")
        self.assertEqual(self.app.report_summary.get(), "Complete · 10.00 USD · 0.1250 kg")
        self.assertNotIn("10.125", self.app.report_summary.get())

    def test_graph_reports_float_overflow_and_underflow_without_changing_records(self):
        cases = (("large", "1" + "0" * 400, ".125"),
                 ("tiny", "0", "." + "0" * 400 + "1"))
        for units, opening, amount in cases:
            with self.subTest(units=units):
                self.repo.save_account("Checking", opening, "2026-10-01", units=units)
                flow = self.seed(amount=amount, units=units, name=units)
                before = "\n".join(self.repo.conn.iterdump())
                rows = self.graph((("Checking", units),))
                self.assertEqual(rows, {})
                self.assertEqual(self.app.chart_message.get(), f"Outside graph display range: Checking · {units}.")
                self.assertTrue(self.app.chart_message_label.winfo_ismapped())
                self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
                self.assertEqual("\n".join(self.repo.conn.iterdump()), before)

    def test_graph_accepts_exact_zero_starting_balance(self):
        self.repo.save_account("Checking", "0", "2026-10-01", units="kg")
        self.seed()
        rows = self.graph((("Checking", "kg"),))
        self.assertEqual(rows["Checking", "kg"][0][1], Decimal(0))
        self.assertEqual(rows["Checking", "kg"][-1][1], Decimal("-.125"))
        self.assertEqual(self.app.chart_message.get(), "")


if __name__ == "__main__":
    unittest.main()
