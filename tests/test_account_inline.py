"""Real Tk checks for account cells sharing the upper form and saving on completion.

Names and calculated balances remain read-only; callback failures are surfaced.
"""

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
class AccountInlineEditingTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "accounts.sqlite3")
        self.repo.save_account("Checking", '1000', "2026-10-01 13:45:12")
        self.repo.save_account("AmEx", '-300', "2026-10-01")
        self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Checking",
                                 to_account="AmEx", amount="1", status="Void"))
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
        self.app.show_view("accounts")
        self.pump()

    def unexpected_dialog(self, *args, **kwargs):
        self.fail(f"Unexpected dialog: {args!r}")

    def tearDown(self):
        try:
            self.app.account_cell_editor.close()
            self.app.ledger_editor.close()
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

    def click_cell(self, name, column="balance", twice=False):
        tree = self.app.accounts_tree
        tree.see(account_key(name))
        self.pump()
        bounds = tree.bbox(account_key(name), column)
        self.assertTrue(bounds, "A clicked account cell must be visible")
        x, y, width, height = bounds
        x += min(25, max(1, width // 2))
        y += height // 2
        self.click_time += 1000
        for repeat in range(2 if twice else 1):
            when = self.click_time + repeat * 150
            tree.event_generate("<ButtonPress-1>", x=x, y=y, time=when)
            tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=when + 40)
            self.pump()

    def open_cell(self, name, column):
        if self.app.account_editing != account_key(name):
            self.click_cell(name)
        editor = self.app.account_cell_editor
        if editor.flow_id != account_key(name) or editor.field != column or editor.input is None:
            self.assertTrue(editor.open(account_key(name), column))
        self.pump()
        self.assertEqual(editor.field, column)
        self.assertIsNotNone(editor.input)
        return editor.input

    def replace_cell(self, name, column, text):
        entry = self.open_cell(name, column)
        entry.delete(0, "end")
        entry.insert(0, text)
        self.pump()
        return entry

    def finish_cell(self):
        entry = self.app.account_cell_editor.input
        self.assertIsNotNone(entry)
        entry.focus_force()
        self.pump()
        entry.event_generate("<Control-Return>" if self.app.account_cell_editor.field == "notes" else "<Return>")
        self.settle()

    def assert_closed(self):
        self.assertIsNone(self.app.account_cell_editor.widget)
        self.assertIsNone(self.app.account_cell_editor.input)

    def assert_new_form(self):
        self.assertIsNone(self.app.account_editing)
        self.assertEqual(self.app.accounts_tree.selection(), ())
        self.assertEqual(self.app.accounts_tree.focus(), "")
        self.assertEqual(self.app.account_title.cget("text"), "New Account")
        self.assertEqual(str(self.app.account_name_entry.cget("state")), "normal")
        for field in ("name", "opening"):
            self.assertEqual(self.app.account_vars[field].get(), "", field)
        self.assertEqual(self.app.account_vars["date"].get(), "2026-10-01")
        self.assert_closed()

    def test_double_click_selects_the_clicked_account_and_correct_cell(self):
        originals = self.repo.list_accounts()
        self.click_cell("Checking")
        self.click_cell("AmEx", "anchor", twice=True)
        self.assertEqual(self.app.account_editing, account_key("AmEx"))
        self.assertEqual(self.app.accounts_tree.selection(), (account_key("AmEx"),))
        self.assertEqual(self.app.account_cell_editor.field, "anchor")
        self.assertEqual(self.app.account_cell_editor.input.get(), "2026-10-01 00:00:00")
        self.assertEqual(self.app.account_vars["opening"].get(), "-300")
        self.assertEqual(self.repo.list_accounts(), originals)

    def test_cells_and_upper_fields_share_values_and_completed_fields_save(self):
        original = self.repo.get_account("Checking")
        entry = self.replace_cell("Checking", "balance", "38.20")
        self.assertEqual(self.app.account_vars["opening"].get(), "38.20")
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '38.2')
        self.app.account_vars["opening"].set("41.23")
        self.pump()
        self.assertEqual(entry.get(), "41.23")
        self.assertEqual(self.repo.get_account("Checking"), original)
        self.finish_cell()
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '41.23')
        date = self.replace_cell("Checking", "anchor", "2026-10-07")
        self.assertEqual(self.app.account_vars["date"].get(), "2026-10-07")
        self.app.account_vars["date"].set("2026-10-08")
        self.pump()
        self.assertEqual(date.get(), "2026-10-08")
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "anchor"), "2026-10-08")
        self.finish_cell()
        self.assertEqual(self.repo.get_account("Checking"),
                         {**original, 'opening_amount': '41.23', "opening_at": "2026-10-08 00:00:00"})
        self.assertEqual(len(self.repo.list_accounts()), 2)
        self.assertEqual(self.app.account_editing, account_key("Checking"))
        self.assertEqual(self.app.accounts_tree.selection(), (account_key("Checking"),))

    def test_negative_zero_and_blank_balances_are_saved_without_extra_accounts(self):
        for raw, amount, preview in (('-1,234.56', '-1234.56', '-1,234.56'),
                                    ("0", "0", '0'),
                                    ("", None, "Unknown")):
            with self.subTest(balance=raw):
                before = self.repo.get_account("Checking")
                self.replace_cell("Checking", "balance", raw)
                self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), preview)
                self.assertEqual(self.repo.get_account("Checking"), before)
                self.finish_cell()
                self.assertEqual(self.repo.get_account("Checking")['opening_amount'], amount)
                self.assertEqual(len(self.repo.list_accounts()), 2)
                self.assertEqual(self.app.account_editing, account_key("Checking"))

    def test_date_only_changes_reset_time_and_full_timestamp_keeps_seconds(self):
        self.replace_cell("Checking", "anchor", "2026-10-09")
        self.app.account_cell_editor.close()
        self.assertEqual(self.app.account_vars["date"].get(), "2026-10-09")
        self.assertTrue(self.app.save_account())
        self.assertEqual(self.repo.get_account("Checking")["opening_at"], "2026-10-09 00:00:00")
        self.replace_cell("Checking", "anchor", "2026-10-10T08:09:10")
        self.assertTrue(self.app.save_account())
        self.assertEqual(self.repo.get_account("Checking")["opening_at"], "2026-10-10 08:09:10")
        self.replace_cell("AmEx", "anchor", "2026-10-11")
        self.assertTrue(self.app.save_account())
        self.assertEqual(self.repo.get_account("AmEx")["opening_at"], "2026-10-11 00:00:00")

    def test_upper_date_accepts_the_same_timestamp_as_the_account_cell(self):
        self.click_cell("Checking")
        self.app.account_vars["date"].set("2026-10-12 11:22:33")
        self.assertTrue(self.app.save_account())
        self.assertEqual(self.repo.get_account("Checking")["opening_at"], "2026-10-12 11:22:33")
        self.assertEqual(self.app.account_editing, account_key("Checking"))

    def test_pasting_the_existing_midnight_timestamp_does_not_create_an_unsaved_change(self):
        original = self.repo.get_account("AmEx")
        self.replace_cell("AmEx", "anchor", "2026-10-01 00:00:00")
        # Equivalent midnight text must remain a no-op when the cell closes.
        self.assertTrue(self.app.new_account())
        self.assert_new_form()
        self.replace_cell("AmEx", "anchor", "2026-10-01T00:00:00")
        self.assertTrue(self.app.show_view("flows"))
        self.settle()
        self.assert_new_form()
        self.assertEqual(self.repo.get_account("AmEx"), original)

    def test_equivalent_time_is_clean_but_invalid_date_or_time_keeps_editing(self):
        self.repo.save_account("AmEx", '-300', "2026-10-01 08:09:00")
        self.app.refresh_all()
        self.click_cell("AmEx")
        self.app.account_vars["date"].set("2026-10-01 08:09")
        self.assertTrue(self.app.new_account())
        self.assert_new_form()
        original = self.repo.get_account("AmEx")
        for field, invalid in (("date", "2026-02-30"), ("date", "2026-10-01 08:99")):
            with self.subTest(field=field):
                self.click_cell("AmEx")
                self.app._begin_field("account", field)
                self.app.account_vars[field].set(invalid)
                self.assertFalse(self.app.new_account())
                self.assertEqual(self.app.account_editing, account_key("AmEx"))
                self.assertEqual(self.app.account_vars[field].get(), invalid)
                self.assertEqual(self.repo.get_account("AmEx"), original)
                self.assertTrue(self.app.account_feedback.get())
                self.app.undo()
                self.assertTrue(self.app.new_account())
                self.assert_new_form()

    def test_account_names_and_computed_balance_columns_are_read_only(self):
        original = self.repo.get_account("Checking")
        for column in ("name", "units", "confirmed", "pending"):
            with self.subTest(column=column):
                self.click_cell("Checking", column, twice=True)
                self.assert_closed()
                self.assertFalse(self.app.account_cell_editor.open(account_key("Checking"), column))
                self.assertEqual(self.app.account_editing, account_key("Checking"))
                self.assertEqual(str(self.app.account_name_entry.cget("state")), "disabled")
                self.app.account_name_entry.delete(0, "end")
                self.app.account_name_entry.insert(0, "Accidental new account")
                self.assertEqual(self.app.account_vars["name"].get(), "Checking")
        self.assertEqual(self.repo.get_account("Checking"), original)
        self.assertIsNone(self.repo.get_account("Accidental new account"))

    def test_keyboard_navigation_skips_read_only_columns_and_completes_edits(self):
        original = self.repo.get_account("Checking")
        self.click_cell("Checking")
        tree = self.app.accounts_tree
        tree.focus_force()
        self.pump()
        tree.event_generate("<F2>")
        self.pump()
        self.assertEqual(self.app.account_cell_editor.field, "balance")
        entry = self.app.account_cell_editor.input
        entry.delete(0, "end")
        entry.insert(0, "55.00")
        entry.event_generate("<Tab>")
        self.pump()
        self.assertEqual(self.app.account_cell_editor.field, "anchor")
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '55')
        self.app.account_cell_editor.input.event_generate("<Shift-Tab>")
        self.pump()
        self.assertEqual(self.app.account_cell_editor.field, "balance")
        self.app.account_cell_editor.input.event_generate("<Return>")
        self.pump()
        self.assert_closed()
        self.assertEqual(self.app.account_vars["opening"].get(), "55.00")
        self.assertEqual(self.repo.get_account("Checking"), {**original, 'opening_amount': '55'})
        self.open_cell("Checking", "anchor").event_generate("<Tab>")
        self.pump()
        self.assert_closed()
        self.assertIs(self.app.focus_get(), tree)

    def test_escape_reverts_unfinished_field_and_control_s_finishes_edit(self):
        original = self.repo.get_account("Checking")
        entry = self.replace_cell("Checking", "balance", "44.00")
        entry.focus_force()
        self.pump()
        entry.event_generate("<Escape>")
        self.pump()
        self.assert_closed()
        self.assertEqual(self.app.account_vars["opening"].get(), "1000")
        self.assertEqual(self.repo.get_account("Checking"), original)
        entry = self.replace_cell("Checking", "balance", "44.00")
        entry.focus_force()
        self.pump()
        entry.event_generate("<Control-s>")
        self.settle()
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '44')
        self.assertEqual(self.app.account_editing, account_key("Checking"))
        self.assertEqual(len(self.repo.list_accounts()), 2)

    def test_new_finishes_existing_edit_and_clears_editor_and_row_focus(self):
        entry = self.replace_cell("Checking", "balance", "200.00")
        self.app.account_vars["date"].set("2026-10-07")
        self.app.new_account()
        self.settle()
        self.assertFalse(entry.winfo_exists())
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '200')
        self.assertEqual(self.repo.get_account("Checking")["opening_at"], "2026-10-07 00:00:00")
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '200')
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "anchor"), "2026-10-07")
        self.assert_new_form()
        self.click_cell("AmEx")
        self.assertEqual(self.app.account_vars["opening"].get(), "-300")

    def test_dirty_row_change_saves_correct_account_without_confirmation(self):
        checking = self.repo.get_account("Checking")
        amex = self.repo.get_account("AmEx")
        self.replace_cell("Checking", "balance", "123.45")
        self.click_cell("AmEx")
        self.assertEqual(self.app.account_editing, account_key("AmEx"))
        self.assertEqual(self.app.account_vars["opening"].get(), "-300")
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '123.45')
        self.assertEqual(self.repo.get_account("Checking"), {**checking, 'opening_amount': '123.45'})
        self.replace_cell("AmEx", "balance", "-222.22")
        self.click_cell("Checking")
        self.settle()
        self.assertEqual(self.repo.get_account("AmEx"), {**amex, 'opening_amount': '-222.22'})
        self.assertEqual(self.app.account_editing, account_key("Checking"))
        self.assertEqual(self.app.account_vars["opening"].get(), "123.45")
        self.assert_closed()

    def test_row_switch_with_invalid_field_retains_original_account_without_prompt(self):
        originals = self.repo.list_accounts()
        self.replace_cell("Checking", "balance", "invalid")
        self.click_cell("AmEx")
        self.pump()
        self.assertEqual(self.app.account_editing, account_key("Checking"))
        self.assertEqual(self.app.accounts_tree.selection(), (account_key("Checking"),))
        self.assertEqual(self.app.account_vars["opening"].get(), "invalid")
        self.assertTrue(self.app.account_feedback.get())
        self.assertEqual(self.repo.list_accounts(), originals)
        self.replace_cell("Checking", "balance", "12.34")
        self.assertTrue(self.app.save_account())
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '12.34')

    def test_invalid_quantities_and_dates_can_be_corrected_without_partial_writes(self):
        for column, invalid, correct, stored_field, expected in (
                ("balance", "invalid", "12.34", 'opening_amount', '12.34'),
                ("anchor", "2026-02-30", "2026-10-07", "opening_at", "2026-10-07 00:00:00"),
                ("anchor", "2026-10-08 25:00:00", "2026-10-08 06:07:08", "opening_at", "2026-10-08 06:07:08")):
            with self.subTest(column=column, value=invalid):
                before = self.repo.get_account("Checking")
                self.replace_cell("Checking", column, invalid)
                draft = self.app._account_snapshot()
                self.assertFalse(self.app.save_account())
                self.pump()
                self.assertEqual(self.repo.get_account("Checking"), before)
                self.assertEqual(self.app._account_snapshot(), draft)
                self.assertEqual(self.app.account_editing, account_key("Checking"))
                self.assertTrue(self.app.account_feedback.get())
                self.replace_cell("Checking", column, correct)
                self.assertTrue(self.app.save_account())
                self.assertEqual(self.repo.get_account("Checking")[stored_field], expected)
                self.assertEqual(self.app.account_editing, account_key("Checking"))

    def test_refresh_completes_overlay_and_keeps_values_in_table(self):
        original = self.repo.get_account("Checking")
        for index, refresh in enumerate((self.app.refresh_accounts, self.app.refresh_all)):
            with self.subTest(refresh=refresh):
                value = f"123.{45 + index}"
                entry = self.replace_cell("Checking", "balance", value)
                self.app.account_vars["date"].set("2026-10-07")
                before = self.app._account_snapshot()
                refresh()
                self.pump()
                self.assertFalse(entry.winfo_exists())
                self.assert_closed()
                self.assertEqual(self.app._account_snapshot(), before)
                self.assertEqual(self.app.account_editing, account_key("Checking"))
                self.assertEqual(self.app.accounts_tree.selection(), (account_key("Checking"),))
                self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '' + value)
                self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "anchor"), "2026-10-07")
                self.assertEqual(self.repo.get_account("Checking"),
                                 {**original, 'opening_amount': f"123.{45 + index}",
                                  "opening_at": "2026-10-07 00:00:00"})

    def test_view_change_finishes_existing_account_without_reopening_editor(self):
        original = self.repo.get_account("Checking")
        self.replace_cell("Checking", "balance", "250.00")
        self.assertTrue(self.app.show_view("flows"))
        self.settle()
        self.assert_new_form()
        self.assertEqual(self.repo.get_account("Checking"), {**original, 'opening_amount': '250'})
        self.assertTrue(self.app.show_view("accounts"))
        self.settle()
        self.assert_new_form()
        self.assertEqual(self.app.accounts_tree.set(account_key("Checking"), "balance"), '250')

    def test_resize_repositions_editor_and_scrolling_finishes_it(self):
        original = self.repo.get_account("Checking")
        for index in range(45):
            self.repo.save_account(f"Extra {index:02}", '0', "2026-10-01")
            self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Checking",
                                     to_account=f"Extra {index:02}", amount="1", status="Void"))
        self.app.refresh_all()
        self.pump()
        self.replace_cell("Checking", "balance", "33.00")
        widget = self.app.account_cell_editor.widget
        for geometry in ("1200x780+0+0", "1500x900+0+0"):
            self.app.geometry(geometry)
            self.pump()
            bounds = self.app.accounts_tree.bbox(account_key("Checking"), "balance")
            self.assertTrue(bounds)
            self.assertEqual((widget.winfo_x(), widget.winfo_y(),
                              widget.winfo_width(), widget.winfo_height()), bounds)
        self.app.accounts_tree.event_generate("<Button-5>")
        self.pump()
        self.assertFalse(widget.winfo_exists())
        self.assert_closed()
        self.assertEqual(self.app.account_vars["opening"].get(), "33.00")
        self.assertEqual(self.repo.get_account("Checking"), {**original, 'opening_amount': '33'})

    def test_save_then_edit_another_account_does_not_carry_over_old_draft_or_time(self):
        self.replace_cell("Checking", "anchor", "2026-10-10 08:09:10")
        self.replace_cell("Checking", "balance", "987.65")
        self.finish_cell()
        self.assertEqual(self.repo.get_account("Checking")['opening_amount'], '987.65')
        self.assertEqual(self.repo.get_account("Checking")["opening_at"], "2026-10-10 08:09:10")
        self.click_cell("AmEx", "balance", twice=True)
        self.settle()
        self.assertEqual(self.app.account_editing, account_key("AmEx"))
        self.assertEqual(self.app.account_vars["name"].get(), "AmEx")
        self.assertEqual(self.app.account_vars["opening"].get(), "-300")
        self.assertEqual(self.app.account_vars["date"].get(), "2026-10-01 00:00:00")
        self.assertEqual(self.app.account_cell_editor.field, "balance")
        self.app.new_account()
        self.settle()
        self.assert_new_form()


if __name__ == "__main__":
    unittest.main()
