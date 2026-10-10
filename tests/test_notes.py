"""The compact and expanded note editors operate on exactly one draft."""

import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class NotesTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "notes.sqlite3")
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.pump()

    def tearDown(self):
        self.app._close_notes_editor()
        self.app._close_popups()
        self.pump()
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def seed(self, notes="Old notes"):
        flow = self.repo.save_flow(dict(units="USD", date="2026-10-02", from_account="Checking",
                                       to_account="Shop", amount="12.00", notes=notes))
        self.app.refresh_all()
        self.app.ledger.selection_set(flow["flow_id"])
        self.pump()
        return flow

    def test_both_views_share_one_draft_without_waiting_for_events(self):
        flow = self.seed()
        self.app.edit_notes()
        expanded = self.app.expanded_notes_text
        expanded.delete("1.0", "end")
        expanded.insert("1.0", "First line\nSecond line")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "First line\nSecond line")
        self.app.notes_text.insert("end-1c", "\nInline addition")
        self.assertEqual(expanded.get("1.0", "end-1c"), "First line\nSecond line\nInline addition")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], "Old notes")
        self.app._close_notes_editor()
        self.app.edit_notes()
        self.assertEqual(self.app.expanded_notes_text.get("1.0", "end-1c"),
                         "First line\nSecond line\nInline addition")

    def test_escape_and_window_close_finish_existing_notes(self):
        flow = self.seed()
        self.app.edit_notes()
        self.app.expanded_notes_text.insert("end-1c", "\nEscape keeps this")
        self.app.expanded_notes_text.focus_force()
        self.pump()
        self.app.expanded_notes_text.event_generate("<Escape>")
        self.pump()
        self.assertIsNone(self.app.notes_dialog)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Old notes\nEscape keeps this")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], "Old notes\nEscape keeps this")
        self.app.edit_notes()
        self.app.expanded_notes_text.insert("end-1c", "\nWindow close keeps this")
        self.app.tk.call(self.app.notes_dialog.protocol("WM_DELETE_WINDOW"))
        self.assertIsNone(self.app.notes_dialog)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"),
                         "Old notes\nEscape keeps this\nWindow close keeps this")
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"],
                         "Old notes\nEscape keeps this\nWindow close keeps this")

    def test_expanded_close_stays_visible_at_default_and_minimum_sizes(self):
        from tkinter import ttk

        def descendants(parent):
            for child in parent.winfo_children():
                yield child
                yield from descendants(child)

        self.app.edit_notes()
        dialog = self.app.notes_dialog
        close = next(widget for widget in descendants(dialog)
                     if isinstance(widget, ttk.Button) and widget.cget("text") == "Close")
        for geometry in ("650x350", "360x220"):
            dialog.geometry(geometry)
            self.pump()
            self.assertTrue(close.winfo_viewable())
            self.assertGreater(close.winfo_width(), 20)
            self.assertGreater(close.winfo_height(), 15)
            self.assertGreaterEqual(close.winfo_rootx(), dialog.winfo_rootx())
            self.assertGreaterEqual(close.winfo_rooty(), dialog.winfo_rooty())
            self.assertLessEqual(close.winfo_rootx() + close.winfo_width(),
                                 dialog.winfo_rootx() + dialog.winfo_width())
            self.assertLessEqual(close.winfo_rooty() + close.winfo_height(),
                                 dialog.winfo_rooty() + dialog.winfo_height())
            self.assertGreater(self.app.expanded_notes_text.winfo_height(), 50)

    def test_expanded_close_reads_immediately_and_preserves_whitespace(self):
        flow = self.seed()
        self.app.edit_notes()
        dialog = self.app.notes_dialog
        expected = "  Indented line\n\nSecond line  \n"
        self.app.expanded_notes_text.delete("1.0", "end")
        self.app.expanded_notes_text.insert("1.0", expected)
        self.app._close_notes_editor()  # No update()/Modified event first.
        self.assertEqual(self.repo.get_flow(flow["flow_id"])["notes"], expected)
        self.assertFalse(dialog.winfo_exists())
        self.assertIsNone(self.app.notes_dialog)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), expected)
        self.assertEqual(self.app.editing_id, flow["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_inline_arrows_reveal_wrapped_text(self):
        text = self.app.notes_text
        text.insert("1.0", "Many words to wrap across the narrow field. " * 12 + "\nLast line")
        text.mark_set("insert", "1.0")
        text.see("insert")
        text.focus_force()
        self.pump()
        self.assertEqual(text.cget("wrap"), "word")
        first = text.index("@0,0")
        for _ in range(8):
            text.event_generate("<Down>")
            self.pump()
        self.assertTrue(text.compare(text.index("@0,0"), ">", first))
        self.assertIsNotNone(text.dlineinfo("insert"), "Caret line must remain visible")
        later = text.index("@0,0")
        for _ in range(8):
            text.event_generate("<Up>")
            self.pump()
        self.assertTrue(text.compare(text.index("@0,0"), "<", later))
        self.assertIsNotNone(text.dlineinfo("insert"))

    def test_inline_wheel_scrolls_multiline_notes(self):
        text = self.app.notes_text
        text.insert("1.0", "\n".join(f"Line {n}" for n in range(20)))
        text.yview_moveto(0)
        self.pump()
        first = text.index("@0,0")
        text.event_generate("<Button-5>")
        self.pump()
        self.assertTrue(text.compare(text.index("@0,0"), ">", first))
        text.event_generate("<Button-4>")
        self.pump()
        self.assertEqual(text.index("@0,0"), first)

    def test_reset_destroys_old_expanded_view_before_loading_another_record(self):
        first = self.seed("First record")
        second = self.repo.save_flow(dict(units="USD", date="2026-10-02", from_account="Checking",
                                         to_account="Shop", amount="9.00", notes="Second record"))
        self.app.edit_notes()
        old_editor = self.app.expanded_notes_text
        self.app._load_form(second)
        self.pump()
        self.assertFalse(old_editor.winfo_exists())
        self.assertIsNone(self.app.notes_dialog)
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Second record")
        self.assertEqual(self.repo.get_flow(first["flow_id"])["notes"], "First record")


if __name__ == "__main__":
    unittest.main()
