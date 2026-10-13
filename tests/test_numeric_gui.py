"""Numeric entry expressions and signed flows through real Tk editing paths."""

from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class NumericEntryGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "numeric.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01")
        self.repo.save_account("Shop", "10", "2026-10-01")
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.dialogs = [
            patch("flowledger.gui.messagebox.showerror", side_effect=self.unexpected_dialog),
            patch("flowledger.gui.messagebox.askyesnocancel", side_effect=self.unexpected_dialog),
        ]
        for item in self.dialogs:
            item.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def unexpected_dialog(self, *args, **kwargs):
        self.fail(f"Unexpected dialog: {args!r}")

    def tearDown(self):
        try:
            self.app.ledger_editor.close(commit=False)
            self.app.account_cell_editor.close(commit=False)
            self.app._close_popups()
            self.app.after(150, self.app.quit)
            self.app.mainloop()
            self.app.destroy()
        finally:
            for item in reversed(self.dialogs):
                item.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def replace(self, entry, text):
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, text)
        self.pump()

    def finish(self, entry):
        entry.event_generate("<Return>")
        self.pump()

    def seed(self, **changes):
        data = dict(date="2026-10-02 12:34:56", from_account="Checking",
                    to_account="Shop", amount="20", units="USD", status="Complete",
                    name="Transfer", category="Supplies", notes="Original note")
        data.update(changes)
        flow = self.repo.save_flow(data)
        self.app.refresh_all()
        self.pump()
        return flow

    def select_flow(self, flow):
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.ledger.focus(flow["flow_id"])
        self.pump()
        self.assertEqual(self.app.editing_id, flow["flow_id"])

    def select_account(self, name="Checking"):
        self.app.show_view("accounts")
        account = self.repo.get_account(name)
        key = self.app._account_key(account)
        self.app.accounts_tree.selection_set(key)
        self.app.accounts_tree.focus(key)
        self.pump()
        self.assertEqual(self.app.account_editing, key)
        return key

    def test_new_amount_enter_resolves_arithmetic_without_adding_a_flow(self):
        entry = self.app.form_widgets["amount"]
        expressions = (("1.23 + 4.56", "5.79"), ("2 - 7", "-5"),
                       (".25 * 4", "1"), ("7 / 2", "3.5"),
                       ("-(2 + 3) * +4 / 2", "-10"), ("5-5", "0"),
                       ("  =1.23 + 4.56", "5.79"), ("=-(2 + 3) * 2", "-10"))
        for expression, result in expressions:
            with self.subTest(expression=expression):
                self.replace(entry, expression)
                self.assertEqual(entry.get(), expression, "Typing must remain untouched")
                self.finish(entry)
                self.assertEqual(entry.get(), result)
                self.assertEqual(self.repo.list_flows(), [])
                self.assertIsNone(self.app.editing_id)

    def test_add_evaluates_expression_and_zero_comment_retains_all_metadata(self):
        for amount, expected in (("(1.23+4.56)*2", "11.58"), ("1-1", "0"), ("-3*2", "-6")):
            with self.subTest(amount=amount):
                for key, value in dict(date="2026-10-02 10:11:12", from_account="Checking",
                                       to_account="Shop", amount=amount, units="USD",
                                       name="Observation", category="Supplies", tags="Note").items():
                    self.app.form_vars[key].set(value)
                self.app.notes_text.insert("1.0", "A dated observation")
                self.assertTrue(self.app.save_flow())
                saved = next(flow for flow in self.repo.list_flows() if flow["amount"] == expected)
                self.assertEqual((saved["from_account"], saved["to_account"]), ("Checking", "Shop"))
                self.assertEqual(saved["date"], "2026-10-02 10:11:12")
                self.assertEqual(saved["notes"], "A dated observation")
                self.assertEqual(saved["tags"], ["Note"])
                self.assertIsNone(self.app.editing_id)
                self.assertEqual(self.app.form_vars["amount"].get(), "")

    def test_existing_top_amount_saves_signed_result_and_undo_redo(self):
        flow = self.seed()
        self.select_flow(flow)
        entry = self.app.form_widgets["amount"]
        self.replace(entry, "5 - 12")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.finish(entry)
        saved = {**flow, "amount": "-7"}
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), saved)
        self.assertEqual(entry.get(), "-7")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.app.redo()
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), saved)

    def test_inline_amount_shares_draft_and_saves_zero_without_reversing_endpoints(self):
        flow = self.seed()
        self.select_flow(flow)
        self.assertTrue(self.app.ledger_editor.open(flow["flow_id"], "amount"))
        self.pump()
        entry = self.app.ledger_editor.input
        self.replace(entry, "20 / 2 - 10")
        self.assertEqual(self.app.form_vars["amount"].get(), "20 / 2 - 10")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.finish(entry)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), {**flow, "amount": "0"})
        self.assertEqual(self.app.form_vars["amount"].get(), "0")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.app.redo()
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["amount"], "0")
        self.assertTrue(self.app.ledger_editor.open(flow["flow_id"], "amount"))
        self.pump()
        entry = self.app.ledger_editor.input
        self.replace(entry, "=3 * -2")
        self.assertEqual(self.app.form_vars["amount"].get(), "=3 * -2")
        self.finish(entry)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), {**flow, "amount": "-6"})
        self.assertEqual(self.app.form_vars["amount"].get(), "-6")

    def test_account_top_and_inline_expressions_save_and_blank_stays_unknown(self):
        self.seed()
        key = self.select_account()
        original = self.repo.get_account("Checking")
        entry = self.app.account_widgets["opening"]
        self.replace(entry, "(100 - 25) / 2")
        self.finish(entry)
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "37.5")
        self.assertEqual(entry.get(), "37.5")
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_account("Checking"), original)
        self.app.redo()
        self.pump()
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "37.5")
        self.assertTrue(self.app.account_cell_editor.open(key, "balance"))
        self.pump()
        entry = self.app.account_cell_editor.input
        self.replace(entry, "-5 * (3 + 1)")
        self.finish(entry)
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "-20")
        self.assertEqual(self.app.account_vars["opening"].get(), "-20")
        self.assertTrue(self.app.account_cell_editor.open(key, "balance"))
        self.pump()
        entry = self.app.account_cell_editor.input
        self.replace(entry, "=10 / 2 - 10")
        self.finish(entry)
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "-5")
        self.assertEqual(self.app.account_vars["opening"].get(), "-5")
        self.replace(self.app.account_widgets["opening"], "")
        self.finish(self.app.account_widgets["opening"])
        self.assertIsNone(self.repo.get_account("Checking")["opening_amount"])
        self.assertEqual(self.app.account_vars["opening"].get(), "")
        self.assertEqual(self.app.accounts_tree.set(key, "balance"), "Unknown")

    def test_new_account_enter_evaluates_without_saving_and_add_also_evaluates(self):
        self.app.show_view("accounts")
        self.app.account_vars["name"].set("Tank")
        self.app.account_vars["units"].set("litres")
        entry = self.app.account_widgets["opening"]
        self.replace(entry, "12.5 + 7.5")
        self.finish(entry)
        self.assertEqual(entry.get(), "20")
        self.assertIsNone(self.repo.get_account("Tank", "litres"))
        self.replace(entry, "20 / 4")
        self.assertTrue(self.app.save_account())
        account = self.repo.get_account("Tank", "litres")
        self.assertEqual(account["opening_amount"], "5")
        self.assertFalse(self.app.accounts_tree.exists(self.app._account_key(account)))

    def test_invalid_arithmetic_preserves_draft_and_saved_values_without_dialog_or_sound(self):
        flow = self.seed()
        self.select_flow(flow)
        with patch.object(self.app, "bell") as bell:
            for expression in ("1/0", "1+", "2**3", "abs(-2)"):
                with self.subTest(expression=expression):
                    entry = self.app.form_widgets["amount"]
                    self.replace(entry, expression)
                    self.finish(entry)
                    self.assertEqual(entry.get(), expression)
                    self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
                    self.assertTrue(self.app.form_feedback.get())
            # Invalid existing entries intentionally prevent a page switch;
            # restore its saved value before exercising account validation.
            self.replace(entry, "20")
            self.finish(entry)
            self.assertTrue(self.app.ledger_editor.open(flow["flow_id"], "amount"))
            self.pump()
            inline = self.app.ledger_editor.input
            self.replace(inline, "2 / 0")
            self.finish(inline)
            self.assertEqual(inline.get(), "2 / 0")
            self.assertIs(self.app.ledger_editor.input, inline)
            self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
            self.replace(inline, "20")
            self.finish(inline)
            self.select_account()
            before = self.repo.get_account("Checking")
            entry = self.app.account_widgets["opening"]
            self.replace(entry, "1 / (2 - 2)")
            self.finish(entry)
            self.assertEqual(entry.get(), "1 / (2 - 2)")
            self.assertEqual(self.repo.get_account("Checking"), before)
            self.assertTrue(self.app.account_feedback.get())
            self.replace(entry, "100")
            self.finish(entry)
            self.assertTrue(self.app.account_cell_editor.open(self.app.account_editing, "balance"))
            self.pump()
            inline = self.app.account_cell_editor.input
            self.replace(inline, "1+")
            self.finish(inline)
            self.assertEqual(inline.get(), "1+")
            self.assertIs(self.app.account_cell_editor.input, inline)
            self.assertEqual(self.repo.get_account("Checking"), before)
            self.replace(inline, "100")
            self.finish(inline)
            self.app.show_view("flows")
            self.app.new_flow(focus=False)
            for key, value in dict(from_account="Checking", to_account="Shop", amount="1/0").items():
                self.app.form_vars[key].set(value)
            self.assertFalse(self.app.save_flow())
            self.assertEqual(self.app.form_vars["amount"].get(), "1/0")
            self.assertEqual(self.repo.list_flows(), [flow])
            bell.assert_not_called()
        self.assertEqual(self.app.tk.call("info", "body", "bell"), "")

    def test_unfinished_expression_survives_tab_changes_without_rewriting_or_saving(self):
        entry = self.app.form_widgets["amount"]
        self.replace(entry, "12 + (")
        self.assertTrue(self.app.show_view("accounts"))
        self.pump()
        self.assertTrue(self.app.show_view("flows"))
        self.pump()
        self.assertEqual(self.app.form_vars["amount"].get(), "12 + (")
        self.assertEqual(self.repo.list_flows(), [])
        self.assertIsNone(self.app.editing_id)

    def test_signed_values_sort_search_report_and_graph_with_conserved_balances(self):
        positive = self.seed(amount="8")
        negative = self.seed(amount="-3", date="2026-10-03")
        zero = self.seed(amount="0", date="2026-10-04", notes="A zero-quantity comment")
        self.app.sort_ledger("amount")
        self.assertEqual(list(self.app.ledger_rows), [negative["flow_id"], zero["flow_id"], positive["flow_id"]])
        self.assertEqual(self.app.report_summary.get(), "Complete · 5.00 USD")
        self.app.search_var.set("-3.00")
        self.app.refresh_ledger()
        self.assertEqual(list(self.app.ledger_rows), [negative["flow_id"]])
        self.app.search_var.set("")
        self.app.refresh_ledger()
        self.app.show_view("charts")
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("2026-10-04")
        self.app.chart_accounts.selection_clear(0, "end")
        for index, pair in enumerate(self.app.chart_account_pairs):
            if pair in (("Checking", "USD"), ("Shop", "USD")):
                self.app.chart_accounts.selection_set(index)
        self.app.draw_chart()
        self.pump()
        series = {line.get_label(): rows for line, rows in self.app.chart_series.items()}
        self.assertEqual(series["Checking"][-1][1], Decimal("95"))
        self.assertEqual(series["Shop"][-1][1], Decimal("15"))
        self.assertEqual(series["Checking"][-1][1] + series["Shop"][-1][1], Decimal("110"))
        self.assertEqual(self.repo.get_flow(zero["flow_id"])["notes"], "A zero-quantity comment")
