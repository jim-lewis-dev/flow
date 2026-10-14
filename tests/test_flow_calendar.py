"""Flow calendar choices share normal draft, autosave, and keyboard behavior."""

import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class FlowCalendarTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "flow-calendar.sqlite3")
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1920x1200+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        try:
            self.app._close_popups()
            self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def key(self, sequence, widget=None):
        widget = widget or self.app.ledger
        widget.focus_force()
        self.pump()
        widget.event_generate(sequence)
        self.pump()

    def type_date(self, value, enter=True):
        entry = self.app.form_widgets["date"]
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, value)
        if enter:
            entry.event_generate("<Return>")
        self.pump()

    def existing(self):
        flow = self.repo.save_flow(dict(date="2026-10-13 15:04:05",
            from_account="Checking", to_account="Shop", amount="12.34",
            units="USD", status="Complete", name="Purchase", category="Supplies",
            tags=["Trip"], notes="Keep all metadata"))
        self.app.refresh_all()
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.ledger.focus(flow["flow_id"])
        self.pump()
        return flow

    def choose(self, day):
        picker = self.app.flow_date_picker
        picker.button.invoke()
        self.pump()
        self.assertIsNotNone(picker.popup)
        picker.day_buttons[day].invoke()
        self.pump()
        self.assertIsNone(picker.popup)

    def test_calendar_changes_draft_only_preserves_time_and_is_one_undo(self):
        self.app.form_vars["name"].set("Unadded purchase")
        self.app.form_vars["from_account"].set("Checking")
        self.app.form_vars["amount"].set(".")
        self.app.notes_text.insert("1.0", "Unfinished notes")
        self.type_date("2026-10-13 7:8:9")
        before = self.app._flow_snapshot()
        self.choose(28)
        after = self.app._flow_snapshot()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-28 07:08:09")
        self.assertEqual(after[1:], before[1:])
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.repo.list_flows(), [])
        self.assertEqual(self.app.save_button.cget("text"), "Add Flow")
        self.key("<Control-z>")
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.repo.list_flows(), [])
        self.key("<Control-y>")
        self.assertEqual(self.app._flow_snapshot(), after)
        self.assertEqual(self.repo.list_flows(), [])

    def test_existing_calendar_selection_autosaves_once_and_undo_redo_keeps_id(self):
        original = self.existing()
        picker = self.app.flow_date_picker
        picker.button.invoke()
        self.pump()
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        picker.day_buttons[28].invoke()
        self.pump()
        changed = {**original, "date": "2026-10-28 15:04:05"}
        self.assertEqual(self.repo.get_flow(original["flow_id"]), changed)
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertEqual(self.app.ledger.selection(), (original["flow_id"],))
        self.key("<Control-z>")
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.assertEqual(self.app.form_vars["date"].get(), original["date"])
        self.key("<Control-y>")
        self.assertEqual(self.repo.get_flow(original["flow_id"]), changed)
        self.assertEqual(self.app.form_vars["date"].get(), changed["date"])
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_popup_escape_preserves_unfinished_existing_date_and_other_fields(self):
        original = self.existing()
        self.type_date("2026-12-", enter=False)
        before = self.app._flow_snapshot()
        picker = self.app.flow_date_picker
        picker.open_popup()
        self.pump()
        self.assertEqual(self.app._flow_snapshot(), before)
        focused = self.app.focus_get()
        focused.event_generate("<Escape>")
        self.pump()
        self.assertIsNone(picker.popup)
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.assertEqual(self.app.editing_id, original["flow_id"])
        self.assertIs(self.app.focus_get(), self.app.form_widgets["date"])

    def test_date_typing_keeps_unpadded_day_until_enter_and_has_no_autoinsert(self):
        self.type_date("2026-12-3", enter=False)
        entry = self.app.form_widgets["date"]
        self.assertEqual(entry.get(), "2026-12-3")
        entry.insert("end", "0 7:8:9")
        self.pump()
        self.assertEqual(entry.get(), "2026-12-30 7:8:9")
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(entry.get(), "2026-12-30 07:08:09")
        self.assertEqual(self.repo.list_flows(), [])

    def test_calendar_button_focus_preserves_edit_until_leaving_date_field(self):
        original = self.existing()
        self.type_date("2026-10-15 7:8:9", enter=False)
        button = self.app.flow_date_picker.button
        button.focus_force()
        self.pump()
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)
        self.app.form_widgets["amount"].focus_force()
        self.pump()
        self.assertEqual(self.repo.get_flow(original["flow_id"]),
                         {**original, "date": "2026-10-15 07:08:09"})
        self.key("<Control-z>")
        self.assertEqual(self.repo.get_flow(original["flow_id"]), original)

        self.app.new_flow(focus=False, confirm=False)
        self.type_date("2026-10-30 7:8", enter=False)
        button.focus_force()
        self.pump()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-30 7:8")
        self.app.form_widgets["amount"].focus_force()
        self.pump()
        self.assertEqual(self.app.form_vars["date"].get(), "2026-10-30 07:08:00")
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_calendar_uses_existing_form_space_at_desktop_and_narrow_widths(self):
        from tkinter import ttk

        picker = self.app.flow_date_picker
        for geometry in ("1920x1200", "1920x1080", "1080x800"):
            with self.subTest(geometry=geometry):
                self.app.geometry(geometry + "+0+0")
                self.pump()
                with_calendar = [(cell.winfo_x(), cell.winfo_y(),
                                  cell.winfo_width(), cell.winfo_height())
                                 for cell in self.app.flow_cells]
                # Compare against the original plain entry in the same slot,
                # using the app's normal reflow rather than fixed pixel sizes.
                picker.pack_forget()
                plain = ttk.Entry(picker.master, textvariable=self.app.form_vars["date"],
                                  width=1)
                plain.pack(fill="x")
                self.pump()
                without_calendar = [(cell.winfo_x(), cell.winfo_y(),
                                     cell.winfo_width(), cell.winfo_height())
                                    for cell in self.app.flow_cells]
                plain.destroy()
                picker.pack(fill="x")
                self.pump()
                self.assertEqual(with_calendar, without_calendar)
                expected_rows = 1 if geometry.startswith("1920") else 2
                self.assertEqual(len({int(cell.grid_info()["row"])
                                      for cell in self.app.flow_cells}),
                                 expected_rows)
                self.assertEqual(picker.button.winfo_width(), picker.entry.winfo_height())
                self.assertEqual(picker.button.winfo_height(), picker.entry.winfo_height())


if __name__ == "__main__":
    unittest.main()
