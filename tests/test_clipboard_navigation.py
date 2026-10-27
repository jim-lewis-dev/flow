"""Clipboard copies remain predictable and editable in a scrolled ledger."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ClipboardNavigationTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "navigation.sqlite3")
        # Equal full timestamps expose accidental ordering by random flow IDs.
        # More than a screen of rows makes viewport and focus failures visible.
        for index in range(1, 121):
            self.repo.save_flow(dict(
                flow_id=str(uuid.UUID(int=index * 1000)),
                date="2026-10-13 01:02:03", name=f"Original {index:03}",
                from_account="Checking", to_account="Café", amount="12.3456789",
                units="USD", status="Pending", category="Dining",
                tags=["Trip", "Boat, trailer"], notes=f"Row {index}\nSecond café"))
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
        try:
            self.app.ledger_editor.close(commit=False)
            self.app.history.close()
            self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def keys(self, gesture):
        """Send keys to actual focus without silently restoring ledger focus."""
        focused = self.app.focus_get()
        self.assertIsNotNone(focused)
        focused.event_generate(gesture)
        self.pump()

    def select(self, identity):
        tree = self.app.ledger
        tree.selection_set(identity)
        tree.focus(identity)
        tree.see(identity)
        tree.focus_force()
        self.pump()

    def copy_paste(self, identity, new_id=1):
        self.select(identity)
        self.keys("<Control-c>")
        with patch("flowledger.core.uuid.uuid4", return_value=uuid.UUID(int=new_id)):
            self.keys("<Control-v>")
        copy_id = self.app.editing_id
        self.assertNotEqual(copy_id, identity)
        return copy_id

    def top_edit(self, field, value):
        order = self.app.ledger.get_children()
        identity = self.app.editing_id
        widget = self.app.form_widgets[field]
        entry = getattr(widget, "entry", widget)
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, value)
        entry.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(identity)[field], self.app.form_vars[field].get())
        self.assertEqual(self.app.ledger.get_children(), order)
        # A completed field saves immediately; leaving the row applies its
        # new sort/filter position after the rest of its fields can be edited.
        self.app._close_popups()
        self.app.search_entry.focus_set()
        self.pump()

    def wheel(self, delta, repetitions=1):
        tree = self.app.ledger
        for _ in range(repetitions):
            tree.event_generate("<MouseWheel>", delta=delta, x=300,
                                y=min(150, tree.winfo_height() // 2))
            self.pump()

    def test_equal_timestamp_copies_follow_source_despite_low_middle_or_high_uuid(self):
        for new_id in (1, 60500, 121000):
            with self.subTest(new_id=new_id):
                source = self.app.ledger.get_children()[-1]
                original = self.repo.get_flow(source)
                before = len(self.repo.list_flows())
                copied = self.copy_paste(source, new_id)
                self.assertEqual(self.repo.get_flow(copied),
                                 {**original, "flow_id": copied})
                self.assertEqual(len(self.repo.list_flows()), before + 1)
                order = self.app.ledger.get_children()
                self.assertEqual(order.index(copied), order.index(source) + 1)
                self.assertEqual(self.app.ledger.selection(), (copied,))
                self.assertTrue(self.app.ledger.bbox(copied))
                self.app.refresh_all()
                self.pump()
                self.assertEqual(self.app.ledger.get_children(), order)
                self.top_edit("name", f"Edited copy {new_id}")
                edited_order = self.app.ledger.get_children()
                self.assertNotEqual(edited_order, order)
                self.assertEqual(edited_order.index(copied),
                                 sum(self.repo.get_flow(fid)["name"].casefold()
                                     < f"Edited copy {new_id}".casefold()
                                     for fid in edited_order))
                self.assertEqual(self.app.ledger.selection(), (copied,))
                self.assertEqual(self.app.editing_id, copied)
                self.assertTrue(self.app.ledger.bbox(copied))
                self.assertEqual(self.repo.get_flow(source), original)
                self.assertEqual(self.repo.get_flow(copied)["name"], f"Edited copy {new_id}")

    def test_changing_copy_date_follows_same_saved_row_to_its_new_sorted_position(self):
        source = self.app.ledger.get_children()[-1]
        original = self.repo.get_flow(source)
        copied = self.copy_paste(source)
        self.assertGreater(self.app.ledger.yview()[0], .5)
        self.top_edit("date", "2026-12-30 4:5:6")
        self.assertEqual(self.app.ledger.get_children()[0], copied)
        self.assertTrue(self.app.ledger.bbox(copied))
        self.assertEqual(self.app.ledger.selection(), (copied,))
        self.assertEqual(self.app.editing_id, copied)
        self.assertEqual(self.repo.get_flow(copied)["date"], "2026-12-30 04:05:06")
        self.assertEqual(self.repo.get_flow(source), original)
        self.assertEqual(len(self.repo.list_flows()), 121)

    def test_plain_wheel_scrolling_preserves_copy_selection_and_saved_record(self):
        source = self.app.ledger.get_children()[-1]
        copied = self.copy_paste(source)
        saved = self.repo.get_flow(copied)
        before = self.app.ledger.yview()
        self.wheel(120, 40)
        self.assertLess(self.app.ledger.yview()[0], before[0])
        self.assertFalse(self.app.ledger.bbox(copied))
        self.assertEqual(self.app.ledger.selection(), (copied,))
        self.assertEqual(self.app.editing_id, copied)
        self.wheel(-120, 40)
        self.assertTrue(self.app.ledger.bbox(copied))
        self.assertEqual(self.app.ledger.selection(), (copied,))
        self.assertEqual(self.repo.get_flow(copied), saved)
        self.assertEqual(len(self.repo.list_flows()), 121)
        self.assertEqual(self.app.form_vars["name"].get(), saved["name"])

    def test_arrow_keys_reveal_entire_first_and_last_rows_at_different_window_heights(self):
        tree = self.app.ledger
        rows = tree.get_children()
        for height in (700, 900, 1080, 1200):
            with self.subTest(height=height):
                self.app.geometry(f"1500x{height}+0+0")
                self.pump()
                self.select(rows[0])
                for row in rows[1:]:
                    self.keys("<Down>")
                    self.assertEqual(tree.focus(), row)
                    _, y, _, h = tree.bbox(row)
                    self.assertLess(y + h - 1, tree.winfo_height())
                    self.assertEqual(tree.identify_region(20, y + h - 1), "cell")
                self.assertEqual(tree.selection(), (rows[-1],))
                for _ in rows[1:]:
                    self.keys("<Up>")
                self.assertEqual(tree.selection(), (rows[0],))
                _, y, _, _ = tree.bbox(rows[0])
                self.assertEqual(tree.identify_region(20, y), "cell")

    def test_wheel_finishes_inline_edit_and_keeps_row_keyboard_clipboard_working(self):
        source = self.app.ledger.get_children()[-1]
        copied = self.copy_paste(source)
        self.assertTrue(self.app.ledger_editor.open(copied, "name"))
        self.pump()
        entry = self.app.ledger_editor.input
        entry.delete(0, "end")
        entry.insert(0, "Edited while scrolling")
        self.app.clipboard_clear()
        self.app.clipboard_append("sentinel")
        self.wheel(120)
        self.assertEqual(self.repo.get_flow(copied)["name"], "Edited while scrolling")
        self.assertIsNone(self.app.ledger_editor.widget)
        self.assertIs(self.app.focus_get(), self.app.ledger)
        # No intervening focus_force: this is the exact next user action.
        self.keys("<Control-c>")
        self.keys("<Control-v>")
        second = self.app.editing_id
        self.assertNotIn(second, (copied, source))
        self.assertEqual(self.repo.get_flow(second),
                         {**self.repo.get_flow(copied), "flow_id": second})
        self.assertEqual(len(self.repo.list_flows()), 122)

    def test_edit_hidden_by_filters_retains_copy_and_explains_where_it_went(self):
        source = self.app.ledger.get_children()[-1]
        original = self.repo.get_flow(source)
        copied = self.copy_paste(source)
        self.app.ledger_filters["Category"].set_selected(["Dining"], notify=True)
        self.pump()
        self.top_edit("category", "Boat")
        self.assertFalse(self.app.ledger.exists(copied))
        self.assertEqual(self.app.ledger_filters["Category"].get(), ("Dining",))
        self.assertEqual(self.app.editing_id, copied)
        self.assertEqual(self.app.form_vars["category"].get(), "Boat")
        self.assertEqual(self.repo.get_flow(copied)["category"], "Boat")
        self.assertEqual(self.repo.get_flow(source), original)
        self.assertEqual(len(self.repo.list_flows()), 121)
        self.assertIn("Hidden by current filters", self.app.status_message.get())
