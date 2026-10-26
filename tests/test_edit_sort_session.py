"""Editing a saved row must not move its next field out from under the user."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class EditSortSessionTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "edit-sort.sqlite3")
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        for index in range(100):
            self.repo.save_flow(dict(
                date=(datetime(2026, 10, 1) + timedelta(days=index)).isoformat(" "),
                name=f"Visible flow {index:03d}", from_account="Warehouse",
                to_account="Workshop", amount=str(index + 1), units="kg",
                status="Complete", category="Material", tags=[], notes=""))
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()
        self.initial_order = self.app.ledger.get_children()
        self.target = self.initial_order[0]
        self.original = self.repo.get_flow(self.target)

    def tearDown(self):
        try:
            self.app.ledger_editor.close(commit=False)
            self.app.history.close()
            self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback errors must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [], "Tk callback errors must surface")

    def select_target(self):
        tree = self.app.ledger
        tree.selection_set(self.target)
        tree.focus(self.target)
        tree.see(self.target)
        self.pump()
        self.assertEqual(self.app.editing_id, self.target)

    def open_date(self):
        self.select_target()
        self.assertTrue(self.app.ledger_editor.open(self.target, "date"))
        self.pump()
        return self.app.ledger_editor.input

    def replace(self, entry, text):
        entry.delete(0, "end")
        entry.insert(0, text)
        self.pump()

    def key(self, entry, gesture):
        entry.event_generate(gesture)
        self.pump()

    def assert_editing(self, field):
        editor = self.app.ledger_editor
        self.assertEqual(editor.flow_id, self.target)
        self.assertEqual(editor.field, field)
        self.assertIsNotNone(editor.input)
        self.assertEqual(str(self.app.tk.call("focus")), str(editor.input))
        self.assertTrue(self.app.ledger.bbox(self.target, field))
        self.assertEqual(self.app.ledger.selection(), (self.target,))
        self.assertEqual(self.app.ledger.get_children(), self.initial_order)

    def assert_finished_visible(self):
        self.assertIsNone(self.app.ledger_editor.input)
        self.assertEqual(self.app.ledger.get_children()[-1], self.target)
        self.assertEqual(self.app.ledger.selection(), (self.target,))
        self.assertEqual(self.app.ledger.focus(), self.target)
        self.assertTrue(self.app.ledger.bbox(self.target))

    def test_tab_and_shift_tab_save_fields_without_moving_row_then_enter_sorts(self):
        entry = self.open_date()
        before_bounds = self.app.ledger.bbox(self.target)
        self.replace(entry, "2020-1-2 3:4:5")
        self.key(entry, "<Tab>")
        self.assert_editing("name")
        self.assertEqual(self.repo.get_flow(self.target)["date"], "2020-01-02 03:04:05")
        self.assertEqual(self.app.ledger.bbox(self.target), before_bounds)

        self.replace(self.app.ledger_editor.input, "Revised flow")
        self.key(self.app.ledger_editor.input, "<Tab>")
        self.assert_editing("from_account")
        self.assertEqual(self.repo.get_flow(self.target)["name"], "Revised flow")
        self.key(self.app.ledger_editor.input, "<ISO_Left_Tab>")
        self.assert_editing("name")

        self.key(self.app.ledger_editor.input, "<Return>")
        self.assert_finished_visible()
        self.assertEqual(str(self.app.tk.call("focus")), str(self.app.ledger))

    def test_top_form_tabs_keep_row_in_place_until_focus_leaves_form(self):
        self.select_target()
        date = self.app.form_widgets["date"]
        date.focus_force()
        self.pump()
        self.replace(date, "2020-1-2")
        name = self.app.form_widgets["name"].entry
        # The calendar button can be an intermediate tab stop.
        for _ in range(3):
            self.key(self.app.focus_get(), "<Tab>")
            self.assertEqual(self.app.ledger.get_children(), self.initial_order)
            if self.app.focus_get() is name:
                break
        self.assertIs(self.app.focus_get(), name)
        self.assertEqual(self.repo.get_flow(self.target)["date"], "2020-01-02")
        self.replace(name, "Revised in form")
        self.key(name, "<Tab>")
        self.assertEqual(self.app.ledger.get_children(), self.initial_order)
        self.assertEqual(self.repo.get_flow(self.target)["name"], "Revised in form")

        self.app._close_popups()
        self.app.search_entry.focus_set()
        self.pump()
        self.assert_finished_visible()
        self.assertIs(self.app.focus_get(), self.app.search_entry)

    def test_clicking_another_row_finishes_sort_without_stealing_selection(self):
        clicked = self.initial_order[1]
        original_clicked = self.repo.get_flow(clicked)
        tree = self.app.ledger
        for index, pump_between in enumerate((True, False)):
            with self.subTest(pump_between_clicks=pump_between):
                self.repo.save_flow(self.original)
                self.repo.save_flow(original_clicked)
                self.app.refresh_all()
                self.pump()
                entry = self.open_date()
                self.replace(entry, "2020-01-02")
                self.key(entry, "<Tab>")
                self.assert_editing("name")
                x, y, width, height = tree.bbox(clicked, "name")
                x += min(20, width // 2)
                y += height // 2
                when = 1000 + index * 2000
                tree.event_generate("<ButtonPress-1>", x=x, y=y, time=when)
                tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=when + 40)
                if pump_between:
                    self.pump()
                    self.assertEqual(tree.get_children()[-1], self.target)
                    self.assertEqual(tree.selection(), (clicked,))
                    self.assertEqual(tree.focus(), clicked)
                    self.assertEqual(self.app.editing_id, clicked)
                    self.assertEqual(self.app.form_vars["name"].get(), original_clicked["name"])
                    self.assertTrue(tree.bbox(clicked))
                    self.assertIsNone(self.app.ledger_editor.input)
                # Sort can happen between clicks, or its idle callback can
                # run after the new cell opens. Neither may change its target.
                tree.event_generate("<ButtonPress-1>", x=x, y=y, time=when + 150)
                tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=when + 190)
                self.pump()
                self.assertEqual(self.app.ledger_editor.flow_id, clicked)
                self.assertEqual(self.app.ledger_editor.field, "name")
                self.assertEqual(tree.selection(), (clicked,))
                self.replace(self.app.ledger_editor.input, "Clicked flow revised")
                self.key(self.app.ledger_editor.input, "<Return>")
                self.assertEqual(tree.get_children()[-1], self.target)
                self.assertEqual(self.repo.get_flow(clicked)["name"], "Clicked flow revised")
                self.assertEqual(self.repo.get_flow(self.initial_order[2])["name"], "Visible flow 097")

    def test_double_clicking_next_cell_in_same_row_does_not_edit_another_record(self):
        entry = self.open_date()
        self.replace(entry, "2020-01-02")
        tree = self.app.ledger
        x, y, width, height = tree.bbox(self.target, "name")
        x += min(20, width // 2)
        y += height // 2
        # Real clicks have an event-loop turn between them. The first click
        # must not sort a different record into the second click's location.
        for when in (1000, 1150):
            tree.event_generate("<ButtonPress-1>", x=x, y=y, time=when)
            tree.event_generate("<ButtonRelease-1>", x=x, y=y, time=when + 40)
            self.pump()
            self.assertEqual(tree.get_children(), self.initial_order)
            self.assertEqual(tree.selection(), (self.target,))
        self.assert_editing("name")
        self.replace(self.app.ledger_editor.input, "Same record revised")
        self.key(self.app.ledger_editor.input, "<Return>")
        self.assert_finished_visible()
        self.assertEqual(self.repo.get_flow(self.target)["name"], "Same record revised")
        self.assertEqual(self.repo.get_flow(self.initial_order[1])["name"], "Visible flow 098")

    def test_changed_row_remains_editable_until_finished_when_filter_no_longer_matches(self):
        self.app.search_var.set("Visible")
        self.pump()
        self.select_target()
        self.assertTrue(self.app.ledger_editor.open(self.target, "name"))
        self.pump()
        self.replace(self.app.ledger_editor.input, "Renamed outside filter")
        self.key(self.app.ledger_editor.input, "<Tab>")
        self.assert_editing("from_account")
        self.assertEqual(self.repo.get_flow(self.target)["name"], "Renamed outside filter")
        self.key(self.app.ledger_editor.input, "<Return>")
        self.assertFalse(self.app.ledger.exists(self.target))
        self.assertIsNone(self.app.ledger_editor.input)
        self.assertIn("hidden", self.app.status_message.get().casefold())
        self.assertEqual(len(self.repo.list_flows()), 100)

    def test_invalid_date_is_correctable_and_saved_fields_remain_undoable(self):
        entry = self.open_date()
        self.replace(entry, "2026-02-30")
        self.key(entry, "<Tab>")
        self.assert_editing("date")
        self.assertEqual(self.repo.get_flow(self.target), self.original)
        self.assertTrue(self.app.form_feedback.get())
        self.replace(self.app.ledger_editor.input, "2020-01-02")
        self.key(self.app.ledger_editor.input, "<Tab>")
        self.assert_editing("name")
        self.assertEqual(self.repo.get_flow(self.target)["date"], "2020-01-02")
        self.key(self.app.ledger_editor.input, "<Control-z>")
        self.assertEqual(self.repo.get_flow(self.target), self.original)
        self.assertEqual(self.app.ledger.get_children(), self.initial_order)
        self.app.ledger.focus_set()
        self.pump()
        self.key(self.app.ledger, "<Control-y>")
        self.assertEqual(self.repo.get_flow(self.target)["date"], "2020-01-02")
        self.assert_finished_visible()

    def test_explicit_heading_sort_finishes_edit_and_uses_requested_order(self):
        entry = self.open_date()
        self.replace(entry, "2020-01-02")
        self.key(entry, "<Tab>")
        self.assert_editing("name")
        self.replace(self.app.ledger_editor.input, "AAA revised")
        self.app.sort_ledger("name")
        self.pump()
        self.assertIsNone(self.app.ledger_editor.input)
        self.assertEqual(self.repo.get_flow(self.target)["name"], "AAA revised")
        self.assertEqual(self.app.sort_column, "name")
        self.assertEqual(self.app.ledger.get_children()[0], self.target)
        self.assertEqual(self.app.ledger.selection(), (self.target,))
        self.assertTrue(self.app.ledger.bbox(self.target))


if __name__ == "__main__":
    unittest.main()
