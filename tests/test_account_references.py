"""Account visibility follows saved endpoints; validation stays silent."""
import os
import unittest
from unittest.mock import patch

from flowledger.core import Repository


class ReferencedAccountsTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")

    def tearDown(self):
        self.repo.close()

    def flow(self, **changes):
        return self.repo.save_flow(dict(date="2026-10-02", from_account="Checking",
                                       to_account="Shop", amount="12.34", **changes))

    def names(self):
        return [a["name"] for a in self.repo.list_accounts(referenced_only=True)]

    def test_starting_balance_alone_is_hidden_but_not_lost(self):
        anchor = self.repo.save_account("Unused", 98765, "2026-10-01 12:34:56")
        self.assertEqual(self.names(), [])
        self.assertEqual(self.repo.get_account("Unused"), anchor)
        self.assertEqual(self.repo.list_accounts(), [anchor])

    def test_both_directions_all_statuses_and_canonical_names_count(self):
        self.repo.save_account("Checking", 100000)
        for status in ("Complete", "Pending", "Planned", "Void"):
            self.repo.save_flow(dict(date="2026-10-02", from_account=" checking ",
                                    to_account=status, amount="1", status=status))
        self.assertEqual(self.names(), ["Checking", "Complete", "Pending", "Planned", "Void"])
        self.assertEqual(self.repo.get_account("Checking")["opening_cents"], 100000)

    def test_last_reference_in_either_direction_controls_visibility(self):
        first = self.flow()
        second = self.repo.save_flow({**first, "flow_id": None,
                                     "from_account": "Shop", "to_account": "Checking"})
        self.repo.save_flow({**first, "to_account": "Correct shop"})
        self.assertEqual(self.names(), ["Checking", "Correct shop", "Shop"])
        self.repo.save_flow({**second, "from_account": "Correct shop"})
        self.assertEqual(self.names(), ["Checking", "Correct shop"])
        self.assertIsNotNone(self.repo.get_account("Shop"))


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class AccountVisibilityGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.repo = Repository(":memory:")
        self.anchor = self.repo.save_account("Typo", 54321, "2026-10-01 12:34:56")
        self.repo.save_account("Checking", 100000)
        self.flow = self.repo.save_flow(dict(date="2026-10-02", from_account="Typo",
                                            to_account="Checking", amount="12.34"))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        self.app._close_popups()
        self.app.after(150, self.app.quit)
        self.app.mainloop()
        self.app.destroy()
        self.repo.close()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def assert_names(self, *names):
        expected = set(names)
        self.assertEqual(set(self.app.accounts_tree.get_children()), expected)
        self.assertEqual(set(self.app.chart_accounts.get(0, "end")), expected)
        for field in ("from_account", "to_account"):
            self.assertEqual(set(self.app.form_widgets[field].values), expected)
        self.assertEqual(self.app.account_summary.get(), f"{len(expected)} accounts")

    def select_flow(self):
        self.app.ledger.selection_set(self.flow["flow_id"])
        self.pump()

    def test_endpoint_correction_and_undo_redo_refresh_all_account_lists(self):
        self.select_flow()
        entry = self.app.form_widgets["from_account"].entry
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, "Correct")
        entry.event_generate("<Return>")
        self.pump()
        self.assert_names("Checking", "Correct")
        self.assertEqual(self.repo.get_account("Typo"), self.anchor)
        self.app.undo()
        self.pump()
        self.assert_names("Checking", "Typo")
        self.assertEqual(self.repo.get_account("Typo"), self.anchor)
        self.app.redo()
        self.pump()
        self.assert_names("Checking", "Correct")

    def test_filters_and_unsaved_typing_do_not_change_account_visibility(self):
        self.select_flow()
        self.app.form_vars["from_account"].set("Unfinished")
        self.app.search_var.set("does not match")
        self.app.ledger_filters["Status"].set_selected(("Planned",))
        self.app.refresh_all()
        self.pump()
        self.assertEqual(self.app.ledger.get_children(), ())
        self.assert_names("Checking", "Typo")
        self.assertIsNone(self.repo.get_account("Unfinished"))

    def test_reusing_hidden_name_restores_saved_starting_balance(self):
        self.repo.save_flow({**self.flow, "from_account": "Correct"})
        self.app.refresh_all()
        self.assert_names("Checking", "Correct")
        self.app.form_vars["from_account"].set("Typo")
        self.app.form_vars["to_account"].set("Checking")
        self.app.form_vars["amount"].set("5")
        self.assertTrue(self.app.save_flow())
        self.pump()
        self.assert_names("Checking", "Correct", "Typo")
        self.assertEqual(self.repo.get_account("Typo"), self.anchor)
        self.app.undo()
        self.pump()
        self.assert_names("Checking", "Correct")
        self.assertEqual(self.repo.get_account("Typo"), self.anchor)

    def test_hidden_selected_account_clears_form_when_last_flow_is_undone(self):
        self.repo.save_flow({**self.flow, "from_account": "Correct"})
        self.app.refresh_all()
        self.app.form_vars["from_account"].set("Typo")
        self.app.form_vars["to_account"].set("Checking")
        self.app.form_vars["amount"].set("5")
        self.assertTrue(self.app.save_flow())
        self.app.show_view("accounts")
        self.app.accounts_tree.selection_set("Typo")
        self.pump()
        self.assertEqual(self.app.account_editing, "Typo")
        self.app.undo()
        self.pump()
        self.assert_names("Checking", "Correct")
        self.assertIsNone(self.app.account_editing)
        self.assertEqual(self.app.account_vars["name"].get(), "")
        self.assertEqual(self.app.accounts_tree.selection(), ())

    def test_add_account_prepares_anchor_without_a_visible_unused_row(self):
        self.app.show_view("accounts")
        self.app.account_vars["name"].set("Savings")
        self.app.account_vars["opening"].set("34.56")
        self.assertTrue(self.app.save_account())
        self.pump()
        self.assert_names("Checking", "Typo")
        self.assertIn("used in a flow", self.app.status_message.get())
        self.assertEqual(self.repo.get_account("Savings")["opening_cents"], 3456)
        self.app.account_vars["name"].set("Savings")
        self.assertFalse(self.app.save_account())
        self.assertIn("Use it in a flow", self.app.account_feedback.get())
        self.assertEqual(self.repo.get_account("Savings")["opening_cents"], 3456)

    def test_validation_reports_errors_without_sound(self):
        # Catch reintroduced Python bell calls while exercising all four former
        # error paths; Tk's own bindings use the separately silenced Tcl command.
        with patch.object(self.app, "bell") as bell:
            self.app.form_vars["amount"].set("invalid")
            self.assertFalse(self.app.save_flow())
            self.assertTrue(self.app.form_feedback.get())
            self.app.show_view("accounts")
            self.app.account_vars["name"].set("New")
            self.app.account_vars["opening"].set("invalid")
            self.assertFalse(self.app.save_account())
            self.assertTrue(self.app.account_feedback.get())
            self.app.accounts_tree.selection_set("Checking")
            self.pump()
            self.app.account_vars["opening"].set("invalid")
            self.assertFalse(self.app._finish_existing("account"))
            self.assertTrue(self.app.account_feedback.get())
            self.app.chart_start.set("invalid")
            self.app.draw_chart()
            self.assertIn("invalid date", self.app.chart_message.get())
            bell.assert_not_called()
        self.assertEqual(self.app.tk.call("info", "body", "bell"), "")
        self.assertFalse(self.app.tk.call("bell"))

    def click_empty_ledger(self):
        tree = self.app.ledger
        x, y = 40, tree.winfo_height() - 8
        self.assertEqual(tree.identify_region(x, y), "nothing")
        self.assertEqual(tree.identify_row(y), "")
        tree.event_generate("<ButtonPress-1>", x=x, y=y)
        tree.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.pump()

    def assert_empty_entry(self):
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertEqual(self.app.ledger.focus(), "")
        self.assertEqual(self.app.form_title.cget("text"), "New Flow")
        for field in ("from_account", "to_account", "amount", "name", "notes", "tags"):
            value = (self.app.notes_text.get("1.0", "end-1c") if field == "notes"
                     else self.app.form_vars[field].get())
            self.assertEqual(value, "", field)
        self.assertEqual(self.app.form_vars["status"].get(), "Complete")
        self.assertEqual(self.app.form_vars["category"].get(), "Uncategorized")
        self.assertIsNone(self.app.ledger_editor.widget)
        self.assertFalse(any(getattr(w, "popup", None) for w in self.app.form_widgets.values()))

    def test_empty_ledger_click_clears_selection_and_saves_valid_edit_once(self):
        self.select_flow()
        self.app.form_widgets["amount"].focus_force()
        self.pump()
        self.app.form_vars["amount"].set("45.67")
        self.click_empty_ledger()
        self.assert_empty_entry()
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"])["amount_cents"], 4567)
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"]), self.flow)
        self.assertFalse(self.app.history.can_undo)

    def test_empty_ledger_click_discards_repeat_and_new_drafts(self):
        self.select_flow()
        self.app.repeat_selected()
        self.app.notes_text.insert("end", "Unadded note")
        self.click_empty_ledger()
        self.assert_empty_entry()
        self.app.form_vars["from_account"].set("Unadded account")
        self.app.form_vars["amount"].set("99")
        self.click_empty_ledger()
        self.assert_empty_entry()
        self.assertEqual(self.repo.list_flows(), [self.flow])
        self.assertIsNone(self.repo.get_account("Unadded account"))

    def test_empty_ledger_click_closes_invalid_inline_edit_without_writing_it(self):
        self.select_flow()
        self.assertTrue(self.app.ledger_editor.open(self.flow["flow_id"], "amount_cents"))
        self.pump()
        self.app.form_vars["amount"].set("invalid")
        self.click_empty_ledger()
        self.assert_empty_entry()
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"]), self.flow)

    def test_heading_click_sorts_without_clearing_selected_flow(self):
        self.select_flow()
        tree = self.app.ledger
        self.assertEqual(tree.identify_region(40, 10), "heading")
        tree.event_generate("<ButtonPress-1>", x=40, y=10)
        tree.event_generate("<ButtonRelease-1>", x=40, y=10)
        self.pump()
        self.assertEqual(self.app.editing_id, self.flow["flow_id"])
        self.assertEqual(tree.selection(), (self.flow["flow_id"],))


if __name__ == "__main__":
    unittest.main()
