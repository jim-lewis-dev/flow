"""Real Tk checks for shared fields and automatic completed-field persistence.

Callback failures and unexpected confirmation dialogs are test failures.
"""

from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class LedgerInlineEditingTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "inline.sqlite3")
        self.repo.save_account("Checking", '1000')
        self.repo.save_account("AmEx", '0')
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.dialog_patches = [
            patch("flowledger.gui.messagebox.showerror", side_effect=self.unexpected_dialog),
            patch("flowledger.gui.messagebox.askyesnocancel", side_effect=self.unexpected_dialog),
        ]
        for item in self.dialog_patches:
            item.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.click_time = 1000
        self.pump()

    def unexpected_dialog(self, *args, **kwargs):
        self.fail(f"Unexpected dialog: {args!r}")

    def tearDown(self):
        try:
            self.app.ledger_editor.close()
            self.app._close_notes_editor()
            self.app._close_popups()
            self.settle()
            self.app.destroy()
        finally:
            for item in reversed(self.dialog_patches):
                item.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def settle(self):
        self.app.after(180, self.app.quit)
        self.app.mainloop()
        self.pump()

    def seed(self, **changes):
        data = dict(units="USD", date="2026-10-02", from_account="Checking", to_account="Shop",
                    amount="20.00", status="Complete", name="Purchase",
                    category="Dining", tags=["Original"], notes="Original notes")
        data.update(changes)
        flow = self.repo.save_flow(data)
        self.app.refresh_all()
        self.pump()
        return flow

    def click_cell(self, flow, column="name", twice=False, pump_between=True):
        tree = self.app.ledger
        fid = flow["flow_id"]
        tree.see(fid)
        self.pump()
        bounds = tree.bbox(fid, column)
        self.assertTrue(bounds, "A clicked cell must be visible")
        x, y, width, height = bounds
        x += min(25, max(1, width // 2))
        y += height // 2
        self.click_time += 1000
        for repeat in range(2 if twice else 1):
            when = self.click_time + repeat * 150
            tree.event_generate("<ButtonPress-1>", x=x, y=y, time=when)
            tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=when + 40)
            if pump_between:
                self.pump()
        if not pump_between:
            self.pump()

    def open_cell(self, flow, column):
        if self.app.editing_id != flow["flow_id"]:
            self.click_cell(flow)
        editor = self.app.ledger_editor
        if editor.flow_id != flow["flow_id"] or editor.field != column or editor.input is None:
            self.assertTrue(editor.open(flow["flow_id"], column))
        self.pump()
        self.assertEqual(self.app.ledger_editor.flow_id, flow["flow_id"])
        self.assertEqual(self.app.ledger_editor.field, column)
        self.assertIsNotNone(self.app.ledger_editor.input)
        return self.app.ledger_editor.input

    def replace_cell(self, flow, column, text):
        entry = self.open_cell(flow, column)
        if column == "notes":
            entry.delete("1.0", "end")
            entry.insert("1.0", text)
        elif column == "status" and hasattr(entry, "set"):
            entry.set(text)
            entry.event_generate("<<ComboboxSelected>>")
        else:
            entry.delete(0, "end")
            entry.insert(0, text)
        self.pump()
        return entry

    def finish_cell(self):
        entry = self.app.ledger_editor.input
        self.assertIsNotNone(entry)
        entry.focus_force()
        self.pump()
        entry.event_generate("<Control-Return>" if self.app.ledger_editor.field == "notes" else "<Return>")
        self.settle()

    def assert_closed(self):
        self.assertIsNone(self.app.ledger_editor.widget)
        self.assertIsNone(self.app.ledger_editor.input)
        for field, widget in self.app.form_widgets.items():
            if hasattr(widget, "popup"):
                self.assertIsNone(widget.popup, f"{field} suggestions must be closed")

    def assert_new_form(self):
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertEqual(self.app.form_title.cget("text"), "New Flow")
        self.assertEqual(self.app.form_vars["date"].get(), datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(self.app.form_vars["status"].get(), "Complete")
        self.assertEqual(self.app.form_vars["category"].get(), "Uncategorized")
        for field in ("from_account", "to_account", "amount", "name", "tags"):
            self.assertEqual(self.app.form_vars[field].get(), "", field)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "")
        self.assert_closed()

    def test_double_click_selects_the_clicked_row_and_opens_the_correct_cell(self):
        first = self.seed(name="First")
        second = self.seed(name="Second", to_account="Marina")
        self.click_cell(first)
        self.click_cell(second, "to_account", twice=True)
        self.assertEqual(self.app.editing_id, second["flow_id"])
        self.assertEqual(self.app.ledger.selection(), (second["flow_id"],))
        self.assertEqual(self.app.ledger_editor.field, "to_account")
        self.assertEqual(self.app.ledger_editor.flow_id, second["flow_id"])
        self.assertEqual(self.app.ledger_editor.input.get(), "Marina")
        self.assertEqual(self.repo.get_flow(first["flow_id"]), first)
        self.assertEqual(self.repo.get_flow(second["flow_id"]), second)

    def test_every_field_shares_top_form_and_completion_saves_the_existing_record(self):
        flow = self.seed(date="2026-10-02 13:45:12")
        changes = [("date", "2026-10-07", "date", "date", "2026-10-07"),
                   ("from_account", "AmEx", "from_account", "from_account", "AmEx"),
                   ("to_account", "Marina", "to_account", "to_account", "Marina"),
                   ('amount', "38.20", "amount", 'amount', '38.2'),
                   ("status", "Pending", "status", "status", "Pending"),
                   ("name", "Boat parts", "name", "name", "Boat parts corrected"),
                   ("category", "Boat", "category", "category", "Boat"),
                   ("tags", 'Fall, "Trailer, repair"', "tags", "tags", ["Fall", "Trailer, repair"])]
        for column, value, top_field, stored_field, expected in changes:
            with self.subTest(field=column):
                entry = self.replace_cell(flow, column, value)
                self.assertEqual(self.app.form_vars[top_field].get(), value)
                other_value = value + " corrected" if top_field == "name" else value
                self.app.form_vars[top_field].set(other_value)
                if column != "status":
                    self.assertEqual(entry.get(), other_value)
                    self.finish_cell()
                else:
                    self.assertFalse(entry.winfo_exists())
                self.assertEqual(self.repo.get_flow(flow["flow_id"])[stored_field], expected)
                self.assertEqual(self.app.editing_id, flow["flow_id"])
                self.assertEqual(len(self.repo.list_flows()), 1)
        notes = "  Receipt note\n\nSecond line  \n"
        self.replace_cell(flow, "notes", notes)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), notes)
        self.finish_cell()
        saved = self.repo.get_flow(flow["flow_id"])
        self.assertEqual(saved, {"units": "USD", **flow, "date": "2026-10-07",
                                "from_account": "AmEx", "to_account": "Marina",
                                'amount': '38.2', "status": "Pending",
                                "name": "Boat parts corrected", "category": "Boat",
                                "tags": ["Fall", "Trailer, repair"], "notes": notes})
        self.assertEqual(self.app.ledger.selection(), (flow["flow_id"],))
        self.assert_closed()

    def test_notes_cell_compact_and_expanded_views_share_content_immediately(self):
        flow = self.seed(notes="Initial\nSecond")
        cell = self.open_cell(flow, "notes")
        cell.insert("end-1c", "\nCell addition")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Initial\nSecond\nCell addition")
        self.app.edit_notes()
        expanded = self.app.expanded_notes_text
        self.assertEqual(expanded.get("1.0", "end-1c"), "Initial\nSecond\nCell addition")
        expanded.insert("end-1c", "\nExpanded addition")
        # No event pump: copied buffers would lose edits on immediate completion.
        self.assertEqual(cell.get("1.0", "end-1c"),
                         "Initial\nSecond\nCell addition\nExpanded addition")
        self.app.notes_text.insert("end-1c", "\nTop addition")
        expected = "Initial\nSecond\nCell addition\nExpanded addition\nTop addition"
        self.assertEqual(cell.get("1.0", "end-1c"), expected)
        self.assertEqual(expanded.get("1.0", "end-1c"), expected)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.app._close_notes_editor()
        self.settle()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], expected)
        self.assertFalse(cell.winfo_exists())
        self.assertFalse(expanded.winfo_exists())
        self.assertEqual(self.app.editing_id, flow["flow_id"])

    def test_date_only_edits_reset_time_and_full_timestamp_keeps_seconds(self):
        flow = self.seed(date="2026-10-02 13:45:12")
        self.replace_cell(flow, "date", "2026-10-09")
        self.app.ledger_editor.close()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-09")
        self.assertTrue(self.app.save_flow())
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["date"], "2026-10-09")
        self.replace_cell(flow, "date", "2026-10-10 08:09:10")
        self.assertTrue(self.app.save_flow())
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["date"], "2026-10-10 08:09:10")
        no_time = self.seed(name="Date only")
        self.replace_cell(no_time, "date", "2026-10-11")
        self.assertTrue(self.app.save_flow())
        self.assertEqual(self.repo.get_flow(no_time["flow_id"])["date"], "2026-10-11")

    def test_new_finishes_cell_then_clears_editor_and_row_focus(self):
        flow = self.seed()
        entry = self.replace_cell(flow, "name", "Keep this")
        self.app.new_flow_button.invoke()
        self.settle()
        self.assertFalse(entry.winfo_exists())
        self.assert_new_form()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["name"], "Keep this")
        self.assertEqual(self.app.ledger.set(flow["flow_id"], "name"), "Keep this")
        self.click_cell(flow)
        self.assertEqual(self.app.form_vars["name"].get(), "Keep this")
        self.assert_closed()

    def test_dirty_row_change_saves_to_correct_flow_without_prompt(self):
        first = self.seed(name="First")
        second = self.seed(name="Second")
        self.replace_cell(first, "name", "First saved")
        self.click_cell(second)
        self.assertEqual(self.app.editing_id, second["flow_id"])
        self.assertEqual(self.app.form_vars["name"].get(), "Second")
        self.assertEqual(self.repo.get_flow(first["flow_id"])["name"], "First saved")
        self.assertEqual(self.app.ledger.set(first["flow_id"], "name"), "First saved")
        self.assert_closed()
        self.replace_cell(second, "name", "Second saved")
        self.click_cell(first)
        self.settle()
        self.assertEqual(self.repo.get_flow(second["flow_id"])["name"], "Second saved")
        self.assertEqual(self.app.editing_id, first["flow_id"])
        self.assertEqual(self.app.form_vars["name"].get(), "First saved")
        self.assertEqual(len(self.repo.list_flows()), 2)
        self.assert_closed()

    def test_refresh_sort_and_filter_finish_cell_without_losing_values(self):
        first = self.seed(name="First")
        self.seed(name="Second")
        for index, operation in enumerate((self.app.refresh_all,
                             lambda: self.app.sort_ledger("name"),
                             lambda: self.app.search_var.set("matches no flow"))):
            with self.subTest(operation=operation):
                self.app.search_var.set("")
                value = f"Saved edit {index}"
                entry = self.replace_cell(first, "name", value)
                before = self.app._flow_snapshot()
                operation()
                self.pump()
                self.assertFalse(entry.winfo_exists())
                self.assert_closed()
                self.assertEqual(self.app._flow_snapshot(), before)
                self.assertEqual(self.app.editing_id, first["flow_id"])
                self.assertEqual(self.repo.get_flow(first["flow_id"])["name"], value)
        self.app.search_var.set("")
        self.pump()
        self.assertEqual(self.app.ledger.set(first["flow_id"], "name"), value)

    def test_repeat_can_be_discarded_by_selecting_original_then_f2_edits_original(self):
        original = self.seed(date="2026-10-02 12:34:56", status="Planned")
        self.click_cell(original)
        snapshot = self.app._flow_snapshot()
        self.app.repeat_selected()
        self.pump()
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertEqual(self.app.ledger.focus(), "")
        self.assertEqual(self.app._flow_snapshot(), snapshot)
        self.app.form_vars["name"].set("Discarded copy")
        self.click_cell(original)
        self.app.ledger.event_generate("<F2>")
        self.pump()
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertEqual(self.app.ledger_editor.flow_id, original["flow_id"])
        self.assertEqual(self.app.ledger_editor.field, "date")
        self.assertEqual(self.repo.list_flows(), [original])

    def test_repeat_add_creates_copy_then_double_click_edits_original(self):
        original = self.seed(date="2026-10-02 12:34:56", status="Pending")
        self.click_cell(original)
        self.app.repeat_selected()
        self.app.form_vars["name"].set("Repeated purchase")
        self.app.save_button.invoke()
        self.settle()
        self.assert_new_form()
        self.click_cell(original, "name", twice=True, pump_between=False)
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertEqual(self.app.ledger_editor.flow_id, original["flow_id"])
        self.assertEqual(self.app.ledger_editor.field, "name")
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        copies = [flow for flow in self.repo.list_flows() if flow["flow_id"] != original["flow_id"]]
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0], {**original, "flow_id": copies[0]["flow_id"], "name": "Repeated purchase"})

    def test_repeat_invalid_add_retains_draft_until_selecting_original(self):
        original = self.seed()
        self.click_cell(original)
        self.app.repeat_selected()
        self.app.form_vars["amount"].set("invalid")
        self.pump()
        draft = self.app._flow_snapshot()
        self.assertFalse(self.app.save_flow())
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertEqual(self.repo.list_flows(), [original])
        self.assert_closed()
        self.click_cell(original)
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertEqual(self.app.form_vars["amount"].get(), "20")
        self.assertEqual(self.repo.list_flows(), [original])

    def test_invalid_cell_values_remain_correctable_without_partial_database_changes(self):
        flow = self.seed()
        for column, invalid, correct, stored_field, stored in (
                ('amount', "invalid", "12.34", 'amount', '12.34'),
                ("date", "2026-02-30", "2026-10-07", "date", "2026-10-07")):
            with self.subTest(field=column):
                original = self.repo.get_flow(flow["flow_id"])
                self.replace_cell(flow, column, invalid)
                before = self.app._flow_snapshot()
                self.assertFalse(self.app.save_flow())
                self.pump()
                self.assertEqual(self.repo.get_flow(flow["flow_id"]), original)
                self.assertEqual(self.app.editing_id, flow["flow_id"])
                self.assertEqual(self.app._flow_snapshot(), before)
                self.assertTrue(self.app.form_feedback.get())
                self.replace_cell(flow, column, correct)
                self.assertTrue(self.app.save_flow())
                self.assertEqual(self.repo.get_flow(flow["flow_id"])[stored_field], stored)
                self.assertEqual(self.app.editing_id, flow["flow_id"])
                self.assertEqual(len(self.repo.list_flows()), 1)

    def test_status_popdown_focus_and_keyboard_selection_keep_the_cell_usable(self):
        flow = self.seed(status="Complete")
        combo = self.open_cell(flow, "status")
        combo.focus_force()
        self.pump()
        # Ttk creates this popdown/listbox entirely in Tcl, so focus_get()
        # cannot resolve it to a tkinter Widget. Post the actual arrow button.
        x, y = combo.winfo_width() - 8, combo.winfo_height() // 2
        combo.event_generate("<ButtonPress-1>", x=x, y=y)
        combo.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.pump()
        focused = str(self.app.tk.call("focus"))
        self.assertIn("popdown", focused)
        self.assertEqual(self.app.ledger_editor.field, "status")
        self.app.tk.call("event", "generate", focused, "<Down>")
        self.app.tk.call("event", "generate", focused, "<Return>")
        self.pump()
        self.assertFalse(combo.winfo_exists())
        self.assert_closed()
        self.assertEqual(self.app.form_vars["status"].get(), "Pending")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["status"], "Pending")
        self.assertEqual(self.app.editing_id, flow["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_suggestions_return_and_tab_save_existing_fields(self):
        self.seed(from_account="AmEx", to_account="Checking", status="Canceled")
        flow = self.seed()
        entry = self.replace_cell(flow, "from_account", "am")
        combo = self.app.ledger_editor.widget
        entry.event_generate("<KeyRelease>", keysym="m")
        self.pump()
        self.assertEqual(combo.matches, ["AmEx"])
        entry.event_generate("<Down>")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.app.form_vars["from_account"].get(), "AmEx")
        self.assertEqual(self.app.editing_id, flow["flow_id"])
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["from_account"], "AmEx")
        entry = self.replace_cell(flow, "to_account", "New endpoint")
        entry.event_generate("<KeyRelease>", keysym="t")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["to_account"], "New endpoint")
        self.assertIsNotNone(self.repo.get_account("New endpoint"))
        entry = self.replace_cell(flow, "name", "New name")
        entry.event_generate("<Tab>")
        self.pump()
        self.assertEqual(self.app.ledger_editor.field, "from_account")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["name"], "New name")
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_suggestion_x_does_not_change_record_or_accept_current_cell(self):
        flow = self.seed(category="Boat")
        entry = self.open_cell(flow, "category")
        combo = self.app.ledger_editor.widget
        combo.open_popup(show_all=True)
        self.pump()
        self.assertIsNotNone(combo.remove_canvas)
        self.assertIn("Boat", combo.matches)
        index = combo.matches.index("Boat")
        entry.delete(0, "end")
        entry.insert(0, "Draft category")
        _, y, _, height = combo.listbox.bbox(index)
        canvas = combo.remove_canvas
        x = canvas.winfo_width() // 2
        canvas.event_generate("<ButtonPress-1>", x=x, y=y + height // 2)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y + height // 2)
        self.pump()
        self.assertEqual(self.app.form_vars["category"].get(), "Draft category")
        self.assertEqual(self.app.ledger_editor.field, "category")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertNotIn("Boat", combo.values)
        self.assertNotIn("Boat", self.app.form_widgets["category"].values)
        self.app.undo()
        self.pump()
        self.assertEqual(self.app.form_vars["category"].get(), "Draft category")
        self.assertIn("Boat", combo.values)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.app.undo()
        self.pump()
        self.assertEqual(self.app.form_vars["category"].get(), "Boat")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_view_change_finishes_existing_cell_without_reopening_editor(self):
        flow = self.seed()
        entry = self.replace_cell(flow, "to_account", "Saved endpoint")
        self.assertTrue(self.app.show_view("accounts"))
        self.settle()
        self.assertFalse(entry.winfo_exists())
        self.assert_new_form()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["to_account"], "Saved endpoint")
        self.app.show_view("flows")
        self.settle()
        self.assert_new_form()

    def test_multiline_notes_selection_does_not_resize_or_hide_the_bottom_cell(self):
        original_notes = "First line\nSecond line\nThird line\nFourth line"
        flow = self.seed(notes=original_notes)
        for index in range(45):
            self.repo.save_flow(dict(units="USD", date="2026-10-03", from_account="Checking",
                                     to_account="Shop", amount="1.00", name=f"Row {index}"))
        self.app.refresh_all()
        self.app.ledger.see(flow["flow_id"])
        self.pump()
        ledger_height = self.app.ledger.winfo_height()
        footer_height = self.app.selection_label.winfo_height()
        self.assertTrue(self.app.ledger.bbox(flow["flow_id"], "notes"))
        self.click_cell(flow, "notes", twice=True)
        self.settle()
        self.assertEqual(self.app.ledger.winfo_height(), ledger_height,
                         "A row's notes preview must not shrink the ledger")
        self.assertEqual(self.app.selection_label.winfo_height(), footer_height,
                         "The selection footer remains one line")
        self.assertTrue(self.app.ledger.bbox(flow["flow_id"], "notes"))
        self.assertEqual(self.app.ledger_editor.field, "notes")
        cell = self.app.ledger_editor.input
        self.assertTrue(cell.winfo_viewable())
        self.assertEqual(cell.get("1.0", "end-1c"), original_notes)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), original_notes)
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], original_notes)
        cell.insert("end-1c", "\nStill editable")
        self.pump()
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"),
                         original_notes + "\nStill editable")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_resize_keeps_editor_on_its_cell_and_scrolling_dismisses_it(self):
        flow = self.seed(date="2026-10-04")
        for index in range(45):
            self.repo.save_flow(dict(units="USD", date="2026-10-03", from_account="Checking",
                                     to_account="Shop", amount="1.00", name=f"Row {index}"))
        self.app.refresh_all()
        self.pump()
        self.replace_cell(flow, 'amount', "33.00")
        widget = self.app.ledger_editor.widget
        for geometry in ("1200x780+0+0", "1500x900+0+0"):
            self.app.geometry(geometry)
            self.pump()
            bounds = self.app.ledger.bbox(flow["flow_id"], 'amount')
            self.assertTrue(bounds)
            self.assertEqual((widget.winfo_x(), widget.winfo_y(),
                              widget.winfo_width(), widget.winfo_height()), bounds)
        self.app.ledger.event_generate("<Button-5>")
        self.pump()
        self.assertFalse(widget.winfo_exists())
        self.assert_closed()
        self.assertEqual(self.app.form_vars["amount"].get(), "33")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '33')

    def test_f2_and_note_newline_keep_editing_until_control_enter(self):
        flow = self.seed(notes="First line")
        self.click_cell(flow)
        self.app.ledger.focus_force()
        self.pump()
        self.app.ledger.event_generate("<F2>")
        self.pump()
        self.assertEqual(self.app.ledger_editor.field, "date")
        self.app.ledger_editor.input.event_generate("<Return>")
        self.pump()
        self.assert_closed()
        cell = self.open_cell(flow, "notes")
        cell.mark_set("insert", "end-1c")
        cell.focus_force()
        self.pump()
        cell.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.app.ledger_editor.field, "notes")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "First line\n")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        cell.event_generate("<Control-Return>")
        self.pump()
        self.assert_closed()
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "First line\n")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], "First line\n")

    def test_escape_reverts_unfinished_cell_and_control_s_finishes_without_duplicate(self):
        flow = self.seed()
        entry = self.replace_cell(flow, 'amount', "44.00")
        entry.focus_force()
        self.pump()
        entry.event_generate("<Escape>")
        self.pump()
        self.assert_closed()
        self.assertEqual(self.app.form_vars["amount"].get(), "20")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        entry = self.replace_cell(flow, 'amount', "44.00")
        entry.focus_force()
        self.pump()
        entry.event_generate("<Control-s>")
        self.settle()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '44')
        self.assertEqual(self.app.editing_id, flow["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)


if __name__ == "__main__":
    unittest.main()
