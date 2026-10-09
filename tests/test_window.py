"""Fullscreen requests and Escape behavior; real monitor placement needs a WM."""
import os
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class WindowTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.requests = []
        self.errors = []
        requests, errors = self.requests, self.errors

        class CheckedApp(FlowApp):
            def attributes(self, *args):
                requests.append(args)
                return super().attributes(*args)

            def report_callback_exception(self, kind, error, traceback):
                errors.append(str(error))

        self.repo = Repository(":memory:")
        self.repo.save_account("Checking", 10000)
        self.app = CheckedApp(self.repo)
        self.app.update()
        self.app.focus_force()
        self.app.update()

    def tearDown(self):
        self.app.destroy()
        self.repo.close()
        self.assertEqual(self.errors, [])

    def test_startup_requests_fullscreen_escape_preserves_draft(self):
        self.assertIn(("-fullscreen", True), self.requests)
        self.app.form_vars["amount"].set("12.34")
        self.app.notes_text.insert("end", "Keep this draft")
        snapshot = self.app._flow_snapshot()
        date = self.app.form_widgets["date"]
        date.focus_force()
        self.app.update()
        date.event_generate("<Escape>")
        self.app.update()
        self.assertEqual(self.requests[-1], ("-fullscreen", False))
        self.assertFalse(self.app._fullscreen_requested)
        self.assertEqual(self.app._flow_snapshot(), snapshot)
        self.assertEqual(self.repo.list_flows(), [])

    def test_entry_dropdown_closes_first_then_escape_exits_fullscreen(self):
        combo = self.app.form_widgets["from_account"]
        combo.entry.focus_force()
        self.app.update()
        self.assertIsNotNone(combo.popup)
        combo.entry.event_generate("<Escape>")
        self.app.update()
        self.assertIsNone(combo.popup)
        self.assertTrue(self.app._fullscreen_requested)
        combo.entry.event_generate("<Escape>")
        self.app.update()
        self.assertFalse(self.app._fullscreen_requested)

    def test_filter_button_without_popup_does_not_swallow_escape(self):
        button = self.app.ledger_filters["Status"].button
        button.focus_force()
        self.app.update()
        button.event_generate("<Escape>")
        self.app.update()
        self.assertFalse(self.app._fullscreen_requested)

    def test_escape_reverts_existing_field_then_exits_fullscreen(self):
        flow = self.repo.save_flow(dict(date="2026-10-02", from_account="Checking",
                                       to_account="Shop", amount="12.34"))
        self.app.refresh_all()
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.update()
        amount = self.app.form_widgets["amount"]
        amount.focus_force()
        self.app.update()
        self.app.form_vars["amount"].set("invalid")
        amount.event_generate("<Escape>")
        self.app.update()
        self.assertEqual(self.app.form_vars["amount"].get(), "12.34")
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)
        self.assertTrue(self.app._fullscreen_requested)
        amount.event_generate("<Escape>")
        self.app.update()
        self.assertFalse(self.app._fullscreen_requested)


if __name__ == "__main__":
    unittest.main()
