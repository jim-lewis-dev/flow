"""Session history and completed-field UX through real Tk event bindings."""

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
class AutosaveTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "autosave.sqlite3"
        self.repo = Repository(self.path)
        self.repo.save_account("Checking", '1000')
        self.repo.save_account("Shop", '0')
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.patches = [patch("flowledger.gui.messagebox.askyesnocancel", side_effect=self.unexpected),
                        patch("flowledger.gui.messagebox.askyesno", side_effect=self.unexpected),
                        patch("flowledger.gui.messagebox.showerror", side_effect=self.unexpected)]
        for mocked in self.patches:
            mocked.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def unexpected(self, *args, **kwargs):
        self.fail(f"Unexpected dialog: {args!r}")

    def tearDown(self):
        self.app.destroy()
        self.repo.close()
        for mocked in reversed(self.patches):
            mocked.stop()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def settle(self):
        self.app.after(180, self.app.quit)
        self.app.mainloop()
        self.pump()

    def seed(self, **changes):
        data = dict(units="USD", date="2026-10-02 12:34:56", from_account="Checking", to_account="Shop",
                    amount="20.00", status="Pending", name="Purchase", notes="Original notes",
                    category="Boat", tags=["Autumn"])
        data.update(changes)
        flow = self.repo.save_flow(data)
        self.app.refresh_all()
        self.pump()
        return flow

    def select(self, flow):
        self.app.ledger.selection_set(flow["flow_id"])
        self.pump()
        self.assertEqual(self.app.editing_id, flow["flow_id"])

    def field(self, key, value):
        widget = self.app.form_widgets[key]
        entry = widget.entry if hasattr(widget, "entry") else widget
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, value)
        self.pump()
        return entry

    def shortcut(self, gesture, widget=None):
        widget = widget or self.app
        widget.focus_force()
        self.pump()
        widget.event_generate(gesture)
        self.settle()

    def test_enter_tab_and_blur_save_existing_fields_without_extra_records(self):
        flow = self.seed()
        self.select(flow)
        entry = self.field("amount", "12.34")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '20')
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '12.34')
        entry = self.field("name", "Renamed")
        entry.event_generate("<Tab>")
        self.settle()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["name"], "Renamed")
        entry = self.field("category", "Travel")
        self.app.form_widgets["amount"].focus_force()
        self.settle()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["category"], "Travel")
        self.assertEqual(self.app.editing_id, flow["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(str(self.app.save_button.cget("state")), "disabled")
        self.app.save_button.invoke()
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_keyboard_undo_redo_restores_each_completed_field_in_order(self):
        flow = self.seed()
        self.select(flow)
        for field, value in (("amount", "12.34"), ("name", "Renamed")):
            self.field(field, value).event_generate("<Return>")
            self.pump()
        self.shortcut("<Control-z>", self.app.ledger)
        saved = self.repo.get_flow(flow["flow_id"])
        self.assertEqual((saved['amount'], saved["name"]), ('12.34', "Purchase"))
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.shortcut("<Control-y>", self.app.ledger)
        self.shortcut("<Control-y>", self.app.ledger)
        saved = self.repo.get_flow(flow["flow_id"])
        self.assertEqual((saved['amount'], saved["name"]), ('12.34', "Renamed"))
        self.assertEqual(self.app.form_vars["name"].get(), "Renamed")
        self.assertEqual(self.app.ledger.set(flow["flow_id"], "name"), "Renamed")
        self.assertEqual(self.app.ledger.set(flow["flow_id"], 'amount'), '12.34')
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_unfinished_invalid_typing_undo_redo_precedes_record_history(self):
        flow = self.seed()
        self.select(flow)
        self.field("name", "Saved name").event_generate("<Return>")
        self.pump()
        entry = self.field("amount", "invalid")
        entry.event_generate("<Return>")
        self.pump()
        self.assertTrue(self.app.form_feedback.get())
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '20')
        self.shortcut("<Control-z>", entry)
        self.assertEqual(entry.get(), "20")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["name"], "Saved name")
        self.shortcut("<Control-y>", entry)
        self.assertEqual(entry.get(), "invalid")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '20')
        self.shortcut("<Control-z>", entry)
        self.shortcut("<Control-z>", entry)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_new_draft_survives_field_navigation_but_row_selection_discards_it(self):
        original = self.seed()
        self.app.new_flow()
        self.field("from_account", "Checking").event_generate("<Return>")
        self.field("to_account", "Never added").event_generate("<Tab>")
        self.field("amount", "9.00").event_generate("<Return>")
        self.pump()
        self.assertEqual(self.app.form_vars["to_account"].get(), "Never added")
        self.assertEqual(self.repo.list_flows(), [original])
        self.assertIsNone(self.repo.get_account("Never added"))
        self.select(original)
        self.assertEqual(self.app.form_vars["to_account"].get(), "Shop")
        self.assertEqual(self.repo.list_flows(), [original])
        self.assertIsNone(self.repo.get_account("Never added"))

    def test_add_and_void_are_reversible_without_confirmations(self):
        for key, value in dict(units="USD", date="2026-10-03", from_account="Checking", to_account="New shop",
                               amount="33.00", name="Added", category="New category", tags="New tag").items():
            self.app.form_vars[key].set(value)
        self.app.save_button.invoke()
        self.pump()
        saved, = self.repo.list_flows()
        self.assertIsNone(self.app.editing_id)
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.list_flows(), [])
        self.assertIsNone(self.repo.get_account("New shop"))
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertEqual(self.repo.list_flows(), [saved])
        self.select(saved)
        self.app.void_selected()
        self.pump()
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["status"], "Canceled")
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["status"], "Canceled")

    def test_repeat_copies_all_statuses_and_optional_timestamp_exactly(self):
        for status, date in (("Complete", "2026-10-01"), ("Pending", "2026-10-01 00:00:00"),
                             ("Planned", "2026-10-02 12:34:56"), ("Canceled", "2026-10-03 01:02:03")):
            with self.subTest(status=status, date=date):
                original = self.seed(status=status, date=date)
                self.select(original)
                before = self.app._flow_snapshot()
                count = len(self.repo.list_flows())
                self.app.repeat_selected()
                self.pump()
                self.assertIsNone(self.app.editing_id)
                self.assertEqual(self.app._flow_snapshot(), before)
                self.assertEqual(len(self.repo.list_flows()), count)
                self.assertTrue(self.app.save_flow())
                copies = [row for row in self.repo.list_flows() if row["flow_id"] != original["flow_id"]
                          and row["date"] == original["date"] and row["status"] == status]
                self.assertEqual(len(copies), 1)
                self.assertEqual(copies[0], {**original, "flow_id": copies[0]["flow_id"]})

    def test_account_change_undo_redo_uses_same_keyboard_and_refreshes_values(self):
        self.seed()
        self.app.show_view("accounts")
        self.app.accounts_tree.selection_set(account_key("Checking"))
        self.pump()
        entry = self.app.account_widgets["opening"]
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, "-12.34")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '-12.34')
        self.assertEqual(self.app.account_editing, account_key("Checking"))
        self.shortcut("<Control-z>", self.app.accounts_tree)
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '1000')
        self.assertEqual(self.app.account_vars["opening"].get(), "1000")
        self.shortcut("<Control-y>", self.app.accounts_tree)
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '-12.34')
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '-12.34')

    def test_expanded_notes_global_undo_does_not_run_tk_local_undo_twice(self):
        flow = self.seed()
        self.select(flow)
        self.app.edit_notes()
        text = self.app.expanded_notes_text
        text.focus_force()
        self.pump()
        text.insert("end-1c", "\nNew paragraph")
        self.shortcut("<Control-z>", text)
        self.assertEqual(text.get("1.0", "end-1c"), "Original notes")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Original notes")
        self.shortcut("<Control-y>", text)
        self.assertEqual(text.get("1.0", "end-1c"), "Original notes\nNew paragraph")
        self.app._close_notes_editor()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], "Original notes\nNew paragraph")
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], "Original notes\nNew paragraph")

    def test_new_completed_edit_clears_redo_but_failed_validation_does_not(self):
        flow = self.seed()
        self.select(flow)
        self.field("amount", "30.00").event_generate("<Return>")
        self.pump()
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertTrue(self.app.history.can_redo)
        invalid = self.field("amount", "not money")
        invalid.event_generate("<Return>")
        self.pump()
        self.assertTrue(self.app.history.can_redo)
        self.shortcut("<Escape>", invalid)
        self.field("amount", "40.00").event_generate("<Return>")
        self.pump()
        self.assertFalse(self.app.history.can_redo)
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"])['amount'], '40')

    def test_new_session_has_empty_history_but_saved_records_remain(self):
        from flowledger.gui import FlowApp
        original = self.seed()
        self.select(original)
        self.field("amount", "33.00").event_generate("<Return>")
        self.pump()
        self.assertTrue(self.app.history.can_undo)
        self.app.request_close()
        self.repo.close()
        self.repo = Repository(self.path)
        self.app = FlowApp(self.repo)
        self.pump()
        self.assertFalse(self.app.history.can_undo)
        self.assertFalse(self.app.history.can_redo)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(original["flow_id"])['amount'], '33')

    def test_swapping_endpoints_can_move_between_fields_without_partial_write(self):
        flow = self.seed()
        self.select(flow)
        source = self.field("from_account", "Shop")
        source.event_generate("<Tab>")
        self.settle()
        target_widget = self.app.form_widgets["to_account"]
        self.assertIs(self.app.focus_get(), target_widget.entry)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertTrue(self.app.form_feedback.get())
        target = target_widget.entry
        target.delete(0, "end")
        target.insert(0, "Checking")
        target.event_generate("<Return>")
        self.settle()
        changed = self.repo.get_flow(flow["flow_id"])
        self.assertEqual((changed["from_account"], changed["to_account"]), ("Shop", "Checking"))
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)

    def test_import_batch_is_one_undo_and_redo_preserves_ids(self):
        import json
        from tkinter import ttk
        original = self.seed()
        source = Path(self.directory.name) / "records.json"
        source.write_text(json.dumps([
            dict(units="USD", date="2026-10-04", from_account="Checking", to_account="Imported shop",
                 amount="12.34", name="First imported", category="Imported category", tags=["Imported tag"]),
            dict(units="USD", date="2026-10-05 11:12:13", from_account="Checking", to_account="Imported shop",
                 amount="56.78", name="Second imported", status="Planned")]), encoding="utf-8")
        with patch("flowledger.gui.filedialog.askopenfilename", return_value=str(source)):
            self.app.import_records()
        self.pump()
        dialog = self.app.grab_current()
        self.assertIsNotNone(dialog)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        commit = next(w for w in descendants(dialog)
                      if isinstance(w, ttk.Button) and w.cget("text") == "Commit import")
        commit.invoke()
        self.pump()
        saved = self.repo.list_flows()
        self.assertEqual(len(saved), 3)
        self.shortcut("<Control-z>", self.app.ledger)
        self.assertEqual(self.repo.list_flows(), [original])
        self.assertIsNone(self.repo.get_account("Imported shop"))
        self.shortcut("<Control-y>", self.app.ledger)
        self.assertEqual(self.repo.list_flows(), saved)
        self.assertIsNotNone(self.repo.get_account("Imported shop"))

    def test_new_account_add_is_explicit_and_reversible(self):
        self.app.show_view("accounts")
        self.app.account_vars["name"].set("Cash")
        self.app.account_vars["opening"].set("15.00")
        self.app.account_vars["date"].set("2026-10-03")
        self.app.account_vars["date"].set("2026-10-03 12:34:56")
        self.app.account_widgets["opening"].focus_force()
        self.pump()
        self.app.account_widgets["opening"].event_generate("<Return>")
        self.pump()
        self.assertIsNone(self.repo.get_account("Cash"))
        self.app.account_save_button.invoke()
        self.pump()
        saved = self.repo.get_account("Cash")
        self.assertEqual(saved, dict(name="Cash", units="USD", opening_amount='15', opening_at="2026-10-03 12:34:56"))
        self.assertIsNone(self.app.account_editing)
        self.shortcut("<Control-z>", self.app.accounts_tree)
        self.assertIsNone(self.repo.get_account("Cash"))
        self.shortcut("<Control-y>", self.app.accounts_tree)
        self.assertEqual(self.repo.get_account("Cash"), saved)

    def test_ctrl_s_preserves_new_and_repeated_drafts_without_validating_or_adding(self):
        saved = self.seed()
        for fields in ({}, {"from_account": "Checking"},
                       {"units": "USD", "from_account": "Checking", "to_account": "Shop", "amount": ".50"},
                       {"units": "USD", "date": "invalid", "amount": "invalid"}):
            with self.subTest(fields=fields):
                self.app.new_flow()
                for key, value in fields.items():
                    self.app.form_vars[key].set(value)
                before = self.app._flow_snapshot()
                history = self.repo.history()
                self.shortcut("<Control-s>", self.app.form_widgets["date"])
                self.assertEqual(self.app._flow_snapshot(), before)
                self.assertEqual(self.repo.list_flows(), [saved])
                self.assertEqual(self.repo.history(), history)
                self.assertEqual(self.app.status_message.get(), "Saved")
        self.select(saved)
        self.app.repeat_selected()
        before = self.app._flow_snapshot()
        self.shortcut("<Control-s>")
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.repo.list_flows(), [saved])
        reopened = Repository(self.path)
        try:
            self.assertEqual(reopened.list_flows(), [saved])
        finally:
            reopened.close()

    def test_ctrl_s_preserves_unadded_account_draft(self):
        self.seed()
        self.app.show_view("accounts")
        for fields in ({}, {"name": "Unadded"}, {"name": "Unadded", "opening": ".50"}):
            with self.subTest(fields=fields):
                self.app.new_account()
                for key, value in fields.items():
                    self.app.account_vars[key].set(value)
                before = self.app._account_snapshot()
                self.shortcut("<Control-s>", self.app.account_name_entry)
                self.assertEqual(self.app._account_snapshot(), before)
                self.assertIsNone(self.repo.get_account("Unadded"))
                self.assertEqual(self.app.status_message.get(), "Saved")

    def test_ctrl_s_invalid_existing_edit_keeps_saved_value_and_visible_error(self):
        saved = self.seed()
        self.select(saved)
        entry = self.field("amount", "invalid")
        self.shortcut("<Control-s>", entry)
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.assertEqual(self.app.form_vars["amount"].get(), "invalid")
        self.assertEqual(self.app.form_feedback.get(), "Use numbers, +, -, *, / and parentheses only.")
        self.assertNotEqual(self.app.status_message.get(), "Saved")

    def test_new_and_add_reset_undo_baseline_when_date_keeps_focus(self):
        self.app.new_flow()
        date = self.app.form_widgets["date"]
        date.focus_force()
        self.pump()
        self.app.new_flow_button.invoke()
        self.pump()
        default_date = self.app.form_vars["date"].get()
        date.delete(0, "end")
        date.insert(0, "2026-11-12")
        self.shortcut("<Control-z>", date)
        self.assertEqual(date.get(), default_date)
        for key, value in dict(units="USD", from_account="Checking", to_account="Shop", amount="33.00").items():
            self.app.form_vars[key].set(value)
        self.app.save_button.invoke()
        self.pump()
        saved, = self.repo.list_flows()
        self.assertIs(self.app.focus_get(), date)
        date.delete(0, "end")
        date.insert(0, "2026-12-13")
        self.shortcut("<Control-z>", date)
        self.assertEqual(date.get(), default_date)
        self.assertEqual(self.repo.list_flows(), [saved])
        self.app.show_view("accounts")
        self.app.new_account()
        name = self.app.account_name_entry
        name.focus_force()
        self.pump()
        self.app.new_account()
        self.pump()
        name.insert(0, "Unfinished account")
        self.shortcut("<Control-z>", name)
        self.assertEqual(name.get(), "")
        self.assertEqual(self.repo.list_flows(), [saved])


if __name__ == "__main__":
    unittest.main()
