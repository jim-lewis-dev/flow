"""Regression checks for editing state across real Tk selection/focus events.

Run with DISPLAY set. These tests deliberately fail on unexpected confirmation
dialogs so a queued selection event cannot quietly discard or save a draft.
"""

from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository


def account_key(name, units="USD"):
    import json
    return json.dumps([name, units], ensure_ascii=False)


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class GuiEditingStateTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "editing.sqlite3")
        self.repo.save_account("Checking", '1000')
        self.repo.save_account("AmEx", '0')
        self.callback_errors = []
        errors = self.callback_errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, exception_type, exception, traceback):
                errors.append(f"{exception_type.__name__}: {exception}")

        self.dialog_patches = [
            patch("flowledger.gui.messagebox.showerror", side_effect=self.unexpected_dialog),
            patch("flowledger.gui.messagebox.askyesnocancel", side_effect=self.unexpected_dialog),
        ]
        for item in self.dialog_patches:
            item.start()
        self.app = CheckedApp(self.repo)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def unexpected_dialog(self, *args, **kwargs):
        self.fail(f"Unexpected dialog: {args!r}")

    def tearDown(self):
        try:
            self.app._close_popups()
            self.app.focus_force()
            self.settle()
            self.app.destroy()
        finally:
            for item in reversed(self.dialog_patches):
                item.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.callback_errors, [], "Tk callback failures must surface")

    def settle(self):
        # Include deferred FocusOut callbacks: a popup reopening 120ms after Save
        # is a regression even if the immediate state looked correct.
        self.app.after(180, self.app.quit)
        self.app.mainloop()
        self.pump()

    def seed_flow(self, **changes):
        data = dict(units="USD", date="2026-10-02", from_account="Checking", to_account="Shop",
                    amount="20.00", status="Complete", name="Purchase",
                    category="Dining", tags=[], notes="")
        data.update(changes)
        flow = self.repo.save_flow(data)
        self.app.refresh_all()
        self.pump()
        return flow

    def click_row(self, tree, row_id):
        tree.see(row_id)
        self.pump()
        bounds = tree.bbox(row_id, "#1")
        self.assertTrue(bounds, "A clicked row must be visible")
        x, y, width, height = bounds
        tree.event_generate("<Motion>", x=x + min(width // 2, 25), y=y + height // 2)
        tree.event_generate("<ButtonPress-1>", x=x + min(width // 2, 25), y=y + height // 2)
        tree.event_generate("<ButtonRelease-1>", x=x + min(width // 2, 25), y=y + height // 2)
        self.pump()

    def click_flow(self, flow):
        self.click_row(self.app.ledger, flow["flow_id"])

    def assert_no_popups(self):
        for key, widget in self.app.form_widgets.items():
            if hasattr(widget, "popup"):
                self.assertIsNone(widget.popup, f"{key} suggestions should be closed")

    def assert_empty_flow(self, status="Complete"):
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertEqual(self.app.ledger.focus(), "", "A cleared editor must not retain a focused ledger row")
        self.assertEqual(self.app.form_title.cget("text"), "New Flow")
        self.assertEqual(self.app.save_button.cget("text"), "Add Flow")
        for key in ("from_account", "to_account", "amount", "name", "tags"):
            self.assertEqual(self.app.form_vars[key].get(), "", key)
        self.assertEqual(self.app.form_vars["date"].get(), datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(self.app.form_vars["category"].get(), "Uncategorized")
        self.assertEqual(self.app.form_vars["status"].get(), status)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "")
        self.assert_no_popups()

    def test_single_click_populates_record_and_next_click_replaces_all_metadata(self):
        first = self.seed_flow(date="2026-10-02 13:45:12", name="Boat parts",
                               from_account="AmEx", to_account="Marina", amount="38.20",
                               category="Boat", tags=["Trailer, repair", "Fall"],
                               notes="Keep the original receipt.", status="Pending")
        second = self.seed_flow(name="Coffee", to_account="Cafe", amount="4.50")
        self.click_flow(first)
        expected = {"units": "USD", "date": "2026-10-02 13:45:12", "from_account": "AmEx",
                    "to_account": "Marina", "amount": "38.2", "name": "Boat parts",
                    "category": "Boat", "status": "Pending"}
        self.assertEqual(self.app.editing_id, first["flow_id"])
        for key, value in expected.items():
            self.assertEqual(self.app.form_vars[key].get(), value, key)
        self.assertIn('"Trailer, repair"', self.app.form_vars["tags"].get())
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), first["notes"])
        self.click_flow(second)
        self.assertEqual(self.app.editing_id, second["flow_id"])
        self.assertEqual(self.app.form_vars["from_account"].get(), "Checking")
        self.assertEqual(self.app.form_vars["to_account"].get(), "Cafe")
        for key in ("tags",):
            self.assertEqual(self.app.form_vars[key].get(), "", key)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "")
        self.assertEqual(self.app.form_vars["status"].get(), "Complete")
        self.assert_no_popups()

    def test_completing_account_field_keeps_record_selected_without_reopening_popup(self):
        row = self.seed_flow(tags=["Old tag"], notes="Old notes", date="2026-10-02 10:30")
        self.click_flow(row)
        source = self.app.form_widgets["from_account"]
        source.entry.focus_force()
        self.pump()
        self.assertIsNotNone(source.popup)
        self.app.form_vars["from_account"].set("AmEx")
        self.app.ledger.event_generate("<<TreeviewSelect>>", when="tail")
        source.entry.event_generate("<Return>")
        self.settle()
        self.assertEqual(self.repo.get_flow(row["flow_id"])["from_account"], "AmEx")
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(self.app.editing_id, row["flow_id"])
        self.assertEqual(self.app.ledger.selection(), (row["flow_id"],))
        self.assert_no_popups()
        self.app.new_flow_button.invoke()
        self.settle()
        self.assert_empty_flow()

    def test_saving_a_new_flow_returns_to_a_clean_ready_to_type_form(self):
        old = self.seed_flow(name="Existing record")
        self.click_flow(old)
        self.app.new_flow_button.invoke()
        self.pump()
        values = {"units": "USD", 
            "date": "2026-10-03 12:34:56", "from_account": "AmEx",
            "to_account": "Marina", "amount": "42.01", "status": "Pending",
            "name": "Boat parts", "category": "Boat", "tags": "Trailer, Fall",
        }
        for key, value in values.items():
            self.app.form_vars[key].set(value)
        self.app.notes_text.insert("1.0", "Line one\nLine two")
        self.app.form_widgets["category"].entry.focus_force()
        self.pump()
        self.assertIsNotNone(self.app.form_widgets["category"].popup)
        self.app.ledger.event_generate("<<TreeviewSelect>>", when="tail")
        self.app.save_button.invoke()
        self.settle()
        saved = [row for row in self.repo.list_flows() if row["flow_id"] != old["flow_id"]]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["date"], "2026-10-03 12:34:56")
        self.assertEqual(saved[0]['amount'], '42.01')
        self.assertEqual(saved[0]["notes"], "Line one\nLine two")
        self.assertEqual(saved[0]["tags"], ["Trailer", "Fall"])
        self.assert_empty_flow()
        self.assertEqual(self.app.focus_get(), self.app.form_widgets["date"])
        self.app.focus_get().event_generate("<KeyPress-Down>")
        self.app.focus_get().event_generate("<KeyRelease-Down>")
        self.settle()
        self.assert_empty_flow()

    def test_refresh_sort_and_filter_do_not_overwrite_an_unsaved_draft(self):
        row = self.seed_flow(name="Original")
        self.click_flow(row)
        self.app.form_vars["name"].set("Unsaved draft")
        self.app.form_vars["to_account"].set("Unsaved endpoint")
        self.app.notes_text.insert("1.0", "Unsaved note")
        before = self.app._flow_snapshot()
        self.app.refresh_all()
        self.pump()
        self.app.sort_ledger("name")
        self.pump()
        self.assertEqual(self.app._flow_snapshot(), before)
        self.app.search_var.set("hide every row")
        self.app.refresh_ledger()
        self.pump()
        self.assertEqual(self.app.ledger.get_children(), ())
        self.assertEqual(self.app.editing_id, row["flow_id"])
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.repo.get_flow(row["flow_id"])["name"], "Original")

    def test_dirty_row_change_saves_each_original_record_without_prompt(self):
        first = self.seed_flow(name="First")
        second = self.seed_flow(name="Second")
        self.click_flow(first)
        self.app.form_vars["name"].set("First saved")
        self.click_flow(second)
        self.assertEqual(self.repo.get_flow(first["flow_id"])["name"], "First saved")
        self.assertEqual(self.app.editing_id, second["flow_id"])
        self.app.form_vars["name"].set("Second saved")
        self.click_flow(first)
        self.settle()
        self.assertEqual(self.repo.get_flow(second["flow_id"])["name"], "Second saved")
        self.assertEqual(self.app.form_vars["name"].get(), "First saved")
        self.assertEqual(self.app.ledger.selection(), (first["flow_id"],))
        self.assertEqual(self.app.ledger.focus(), first["flow_id"])
        self.assertEqual(self.app.form_title.cget("text"), "Edit Flow")
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_invalid_save_during_row_change_preserves_draft_and_selection(self):
        first = self.seed_flow(name="First")
        second = self.seed_flow(name="Second")
        self.click_flow(first)
        self.app.form_vars["amount"].set("invalid")
        self.app.form_vars["to_account"].set("Not yet an account")
        before = self.app._flow_snapshot()
        self.click_flow(second)
        self.assertEqual(self.app.editing_id, first["flow_id"])
        self.assertEqual(self.app.ledger.selection(), (first["flow_id"],))
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.repo.get_flow(first["flow_id"])['amount'], '20')
        self.assertIsNone(self.repo.get_account("Not yet an account"))

    def test_new_finishes_existing_edit_then_clears_every_optional_value(self):
        row = self.seed_flow(date="2026-10-02 14:00", tags=["Boat"], notes="Original note")
        self.click_flow(row)
        self.app.form_vars["name"].set("Keep this change")
        self.app.form_widgets["category"].entry.focus_force()
        self.pump()
        self.app.ledger.event_generate("<<TreeviewSelect>>", when="tail")
        self.app.new_flow_button.invoke()
        self.settle()
        self.assert_empty_flow()
        self.assertEqual(self.repo.get_flow(row["flow_id"])["name"], "Keep this change")
        self.assertEqual(self.app.focus_get(), self.app.form_widgets["date"])
        self.app.focus_get().event_generate("<KeyPress-Down>")
        self.app.focus_get().event_generate("<KeyRelease-Down>")
        self.settle()
        self.assert_empty_flow()

    def test_repeat_copies_timestamp_and_status_but_row_selection_discards_draft(self):
        first = self.seed_flow(name="Repeat this", date="2026-10-02 12:34:56", tags=["Trip"], status="Planned")
        second = self.seed_flow(name="Another record")
        self.click_flow(first)
        before = self.app._flow_snapshot()
        self.app.repeat_selected()
        self.pump()
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.app.ledger.selection(), ())
        self.app.form_vars["name"].set("Unadded copy")
        self.click_flow(second)
        self.assertEqual(self.app.editing_id, second["flow_id"])
        self.assertEqual(self.app.form_vars["name"].get(), "Another record")
        self.assertEqual(len(self.repo.list_flows()), 2)
        self.assertEqual(self.repo.get_flow(first["flow_id"]), first)

    def test_void_clears_editor_so_old_complete_status_cannot_be_saved_back(self):
        row = self.seed_flow(name="Void me")
        self.click_flow(row)
        with patch("flowledger.gui.messagebox.askyesno", side_effect=self.unexpected_dialog):
            self.app.void_selected()
        self.settle()
        self.assert_empty_flow()
        self.assertEqual(self.repo.get_flow(row["flow_id"])["status"], "Canceled")
        self.assertFalse(self.app.save_flow())
        self.pump()
        self.assertEqual(self.repo.get_flow(row["flow_id"])["status"], "Canceled")
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_account_refresh_preserves_field_and_completion_keeps_selected_account(self):
        self.seed_flow(to_account="AmEx", status="Canceled")
        self.app.show_view("accounts")
        self.pump()
        self.click_row(self.app.accounts_tree, account_key("Checking"))
        self.app.account_vars["opening"].set("1234.56")
        self.app.account_vars["date"].set("2026-10-01 08:09:10")
        before = self.app._account_snapshot()
        self.app.refresh_all()
        self.pump()
        self.assertEqual(self.app._account_snapshot(), before)
        self.click_row(self.app.accounts_tree, account_key("AmEx"))
        saved = self.repo.get_account("Checking")
        self.assertEqual(saved['opening_amount'], '1234.56')
        self.assertEqual(saved["opening_at"], "2026-10-01 08:09:10")
        self.assertEqual(set(saved), {"name", "units", 'opening_amount', "opening_at"})
        self.assertEqual(self.app.account_editing, account_key("AmEx"))
        self.assertEqual(self.app.account_vars["opening"].get(), "0")

    def test_restore_discards_editors_bound_to_replaced_database(self):
        backup = Path(self.directory.name) / "before.sqlite3"
        self.repo.backup(backup)
        row = self.seed_flow(name="Absent from backup", notes="Stale note")
        self.click_flow(row)
        self.app.form_vars["name"].set("Stale draft")
        # Both editors may have a populated record before a database-level
        # operation. Queue a real selection event without changing the page.
        self.app.accounts_tree.selection_set(account_key("Checking"))
        self.pump()
        self.app.account_vars["opening"].set("9999.00")
        with patch("flowledger.gui.filedialog.askopenfilename", return_value=str(backup)), \
                patch("flowledger.gui.messagebox.askyesno", return_value=True):
            self.app.restore_database()
        self.settle()
        self.assertEqual(self.repo.list_flows(), [])
        self.assert_empty_flow()
        self.assertIsNone(self.app.account_editing)
        self.assertEqual(self.app.accounts_tree.selection(), ())
        self.assertEqual(self.app.account_vars["name"].get(), "")
        self.assertEqual(self.app.account_vars["opening"].get(), "")


if __name__ == "__main__":
    unittest.main()
