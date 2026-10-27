"""Required-field feedback and keyboard entry through the actual Tk form."""

from datetime import datetime
import os
from pathlib import Path
import tempfile
from tkinter import ttk
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class FlowFormValidationTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "flows.sqlite3"
        self.repo = Repository(self.path)
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app_class = CheckedApp
        self.app = None

    def open_app(self):
        self.app = self.app_class(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1600x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        if self.app is not None:
            self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def fill(self, **changes):
        for key, value in changes.items():
            self.app.form_vars[key].set(value)
        self.pump()

    def entry(self, key):
        if key == "notes":
            return self.app.notes_text
        widget = self.app.form_widgets[key]
        return widget.entry if hasattr(widget, "entry") else widget

    def complete(self, **changes):
        values = dict(date="2026-10-27", from_account="Store", to_account="Workshop",
                      amount="2", units="kg")
        values.update(changes)
        self.fill(**values)

    def assert_hint(self, text, disabled=True):
        self.assertEqual(self.app.form_feedback.get(), text)
        self.assertEqual("disabled" in self.app.save_button.state(), disabled)

    def test_missing_hint_punctuation_and_button_follow_current_fields(self):
        self.open_app()
        self.assertEqual(self.app.form_vars["date"].get(), datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(self.app.form_vars["units"].get(), "USD")
        self.assert_hint("To, From, and Amount required")
        style = ttk.Style(self.app)
        disabled_color = style.lookup(self.app.save_button.cget("style"), "background", ("disabled",))
        self.fill(date="", units="")
        self.assert_hint("Date, To, From, Amount, and Units required")
        for key, value, expected in (
                ("date", "2026-10-27", "To, From, Amount, and Units required"),
                ("to_account", "Workshop", "From, Amount, and Units required"),
                ("from_account", "Store", "Amount and Units required"),
                ("amount", "0", "Units required"),
                ("units", "kg", "Ready to add")):
            with self.subTest(field=key):
                self.fill(**{key: value})
                self.assert_hint(expected, key != "units")
        enabled_color = style.lookup(self.app.save_button.cget("style"), "background", ())
        self.assertNotEqual(disabled_color, enabled_color)
        self.fill(to_account=" ", date="", units=" ")
        self.assert_hint("Date, To, and Units required")
        self.fill(units="kg", date="2026-10-27")
        self.assert_hint("To required")
        self.assertEqual(self.repo.list_flows(), [])

    def test_clear_is_reversible_and_self_flow_add_keeps_existing_edit_mode(self):
        self.open_app()
        self.assertEqual(self.app.new_flow_button.cget("text"), "Clear Flow")
        self.complete(from_account="Workshop", amount=".5 + .5")
        self.app.notes_text.insert("1.0", "Recorded without changing the balance")
        self.pump()
        self.app.new_flow_button.invoke()
        self.pump()
        self.assert_hint("To, From, and Amount required")
        self.app.undo()
        self.pump()
        self.assert_hint("Ready to add", False)
        self.assertEqual(self.app.form_vars["amount"].get(), ".5 + .5")
        self.app.redo()
        self.pump()
        self.assert_hint("To, From, and Amount required")
        self.complete(from_account="Workshop", amount=".5 + .5")
        self.app.save_button.invoke()
        self.pump()
        flow = self.repo.list_flows()[0]
        self.assertEqual(flow["amount"], "1")
        self.assertEqual(flow["from_account"], flow["to_account"])
        self.assertEqual(flow["name"], "Workshop -> Workshop")
        self.assert_hint("To, From, and Amount required")
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.ledger.focus(flow["flow_id"])
        self.pump()
        self.assertIn("disabled", self.app.save_button.state())
        self.fill(name="Renamed")
        self.app._field_enter("flow", "name")
        self.pump()
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["name"], "Renamed")
        self.fill(amount="1 / 0")
        self.app._field_enter("flow", "amount")
        self.pump()
        self.assertTrue(self.app.form_feedback.get().startswith("Amount:"))
        self.app.new_flow_button.invoke()
        self.pump()
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertIsNone(self.app.editing_id)
        self.assert_hint("To, From, and Amount required")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["amount"], "1")
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_add_reports_invalid_fields_in_display_order_and_keeps_input(self):
        self.open_app()
        self.complete(date="2026-13-2", amount="1 / 0", tags='"unclosed')
        for prefix, field, corrected in (("Date/Time:", "date", "2026-10-27"),
                                          ("Amount:", "amount", "2"),
                                          ("Tags:", "tags", "Useful")):
            with self.subTest(field=field):
                draft = self.app._flow_snapshot()
                self.app.save_button.invoke()
                self.pump()
                self.assertTrue(self.app.form_feedback.get().startswith(prefix), self.app.form_feedback.get())
                self.assertEqual(self.app._flow_snapshot(), draft)
                self.assertEqual(self.repo.list_flows(), [])
                self.assertEqual(str(self.app.focus_get()), str(self.entry(field)))
            self.fill(**{field: corrected})
        self.app.save_button.invoke()
        self.pump()
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(self.repo.list_flows()[0]["tags"], ["Useful"])

    def test_amount_completion_names_field_without_beeping_or_inserting(self):
        self.open_app()
        self.app.tk.eval("proc bell {args} {error {Unexpected bell}}")
        entry = self.entry("amount")
        entry.focus_force()
        self.pump()
        entry.insert(0, "1 / 0")
        entry.event_generate("<Return>")
        self.pump()
        self.assertTrue(self.app.form_feedback.get().startswith("Amount:"))
        self.assertIn("zero", self.app.form_feedback.get())
        self.assertEqual(self.repo.list_flows(), [])
        self.assertIn("disabled", self.app.save_button.state())

    def test_tab_moves_between_inputs_and_skips_accessory_buttons(self):
        self.open_app()
        self.complete()
        fields = ("date", "name", "from_account", "to_account", "amount", "units",
                  "status", "category", "tags", "notes")
        first = self.entry(fields[0])
        first.focus_force()
        self.pump()
        for key in fields[1:]:
            self.app.focus_get().event_generate("<Tab>")
            self.pump()
            self.assertEqual(str(self.app.focus_get()), str(self.entry(key)), key)
        for key in reversed(fields[:-1]):
            self.app.focus_get().event_generate("<Shift-Tab>")
            self.pump()
            self.assertEqual(str(self.app.focus_get()), str(self.entry(key)), key)
        self.assertEqual(self.repo.list_flows(), [])

    def test_obsolete_suggestion_file_is_removed_without_hiding_saved_values(self):
        flow = self.repo.save_flow(dict(date="2026-10-27", from_account="Store",
                                       to_account="Workshop", amount="1", units="kg"))
        legacy = Path(str(self.path) + ".suggestions.json")
        legacy.write_text('{"account": ["store"], "unit": ["kg"]}')
        self.open_app()
        self.assertFalse(legacy.exists())
        self.assertIn("Store", self.app.form_widgets["from_account"].values)
        self.assertIn("kg", self.app.form_widgets["units"].values)
        self.assertEqual(self.repo.get_flow(flow["flow_id"]), flow)


if __name__ == "__main__":
    unittest.main()
