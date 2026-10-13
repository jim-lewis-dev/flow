"""Accounts computes balances at one editable instant without changing records."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class AccountAsOfTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "accounts-as-of.sqlite3")
        for name, amount, units in (("Checking", "1000", "USD"),
                                    ("Amex", "-500", "USD"),
                                    ("Checking", "10", "kg"),
                                    ("Amex", "2", "kg")):
            self.repo.save_account(name, amount, "2026-10-01", units)
        for when, amount, units, status in (("2026-10-28", "200", "USD", "Pending"),
                                            ("2026-10-28 15:04:05", "25", "USD", "Complete"),
                                            ("2026-10-28", "3", "kg", "Pending")):
            self.repo.save_flow(dict(date=when, from_account="Checking", to_account="Amex",
                                     amount=amount, units=units, status=status))
        self.repo.save_flow(dict(date="2026-10-02", from_account="Checking", to_account="Unknown",
                                 amount="1", units="kg", status="Canceled"))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.clock_patch = patch("flowledger.gui.FlowApp._now", return_value="2026-10-13 13:02:28")
        self.clock = self.clock_patch.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.show_view("accounts")
        self.settle()

    def tearDown(self):
        try:
            self.app._close_popups()
            self.app.destroy()
            self.repo.close()
            self.directory.cleanup()
        finally:
            self.clock_patch.stop()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def settle(self):
        self.app.after(180, self.app.quit)
        self.app.mainloop()
        self.pump()

    def balances(self, name, units="USD"):
        key = json.dumps([name, units], ensure_ascii=False)
        return tuple(self.app.accounts_tree.set(key, column)
                     for column in ("confirmed", "pending"))

    def as_of(self, value):
        self.app.account_as_of.set(value)
        self.settle()

    def test_future_pending_payment_affects_selected_instant_not_now(self):
        self.assertEqual(self.app.account_as_of.get(), "Now")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "1,000.00"))
        self.assertEqual(self.balances("Amex"), ("-500.00", "-500.00"))
        self.as_of("2026-10-28")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        self.assertEqual(self.balances("Amex"), ("-500.00", "-300.00"))
        self.assertEqual(self.app.account_as_of_error.get(), "")

    def test_date_defaults_to_midnight_and_seconds_are_inclusive(self):
        self.as_of("2026-10-28")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        for value in ("2026-10-28 15", "2026-10-28 15:4", "2026-10-28 15:4:4"):
            with self.subTest(value=value):
                self.as_of(value)
                self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        self.as_of("2026-10-28 15:4:5")
        self.assertEqual(self.balances("Checking"), ("975.00", "775.00"))
        self.assertEqual(self.balances("Amex"), ("-475.00", "-275.00"))

    def test_now_and_blank_follow_clock_when_accounts_refresh(self):
        for value in ("Now", ""):
            with self.subTest(value=value):
                self.clock.return_value = "2026-10-13 13:02:28"
                self.as_of(value)
                self.assertEqual(self.balances("Checking"), ("1,000.00", "1,000.00"))
                self.clock.return_value = "2026-10-29 00:00:00"
                self.app.refresh_accounts()
                self.pump()
                self.assertEqual(self.balances("Checking"), ("975.00", "775.00"))
                self.assertEqual(self.app.account_as_of.get(), value)

    def test_units_remain_independent_and_unknown_stays_unknown(self):
        self.as_of("2026-10-28")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        self.assertEqual(self.balances("Checking", "kg"), ("10.0000", "7.0000"))
        self.assertEqual(self.balances("Amex", "kg"), ("2.0000", "5.0000"))
        self.assertEqual(self.balances("Unknown", "kg"), ("Unknown", "Unknown"))

    def test_as_of_undo_redo_restores_balances_without_database_writes(self):
        before = "\n".join(self.repo.conn.iterdump())
        changes = self.repo.conn.total_changes
        self.as_of("2026-10-28")
        self.app.focus_force()
        self.pump()
        self.app.event_generate("<Control-z>")
        self.settle()
        self.assertEqual(self.app.account_as_of.get(), "Now")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "1,000.00"))
        self.app.event_generate("<Control-y>")
        self.settle()
        self.assertEqual(self.app.account_as_of.get(), "2026-10-28")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        self.assertEqual(self.repo.conn.total_changes, changes)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), before)

    def test_invalid_as_of_shows_error_clears_calculations_and_keeps_draft(self):
        self.app.account_vars["name"].set("Unfinished account")
        self.app.account_vars["opening"].set(".")
        draft = tuple(variable.get() for variable in self.app.account_vars.values())
        before = "\n".join(self.repo.conn.iterdump())
        self.as_of("2026-13-28")
        self.assertTrue(self.app.account_as_of_error.get())
        self.assertEqual(self.balances("Checking"), ("", ""))
        key = json.dumps(["Checking", "USD"], ensure_ascii=False)
        self.assertEqual(self.app.accounts_tree.set(key, "balance"), "1,000.00")
        self.assertEqual(tuple(variable.get() for variable in self.app.account_vars.values()), draft)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), before)
        self.as_of("2026-10-28")
        self.assertEqual(self.app.account_as_of_error.get(), "")
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))
        self.assertEqual(tuple(variable.get() for variable in self.app.account_vars.values()), draft)

    def test_enter_refreshes_typed_as_of_immediately(self):
        entry = self.app.account_as_of_picker.entry
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, "2026-10-28")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.balances("Checking"), ("1,000.00", "800.00"))

    def test_calendar_selection_updates_balances_and_preserves_time(self):
        self.as_of("2026-10-13 15:4:5")
        picker = self.app.account_as_of_picker
        picker.open_popup()
        self.pump()
        picker.day_buttons[28].invoke()
        self.pump()
        self.assertEqual(self.app.account_as_of.get(), "2026-10-28 15:04:05")
        self.assertEqual(self.balances("Checking"), ("975.00", "775.00"))
        self.assertIsNone(picker.popup)


if __name__ == "__main__":
    unittest.main()
