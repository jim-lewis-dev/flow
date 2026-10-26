"""Ledger row clipboard operations, Canceled and display-only unit precision."""
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class RowClipboardTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory=tempfile.TemporaryDirectory()
        self.repo=Repository(Path(self.directory.name)/"clipboard.sqlite3")
        self.repo.save_account("A","100.123456789","2026-10-01")
        self.repo.save_account("B","0","2026-10-01")
        self.original=self.repo.save_flow(dict(date="2026-10-02 01:02:03", name="Café",
            from_account="A",to_account="B",amount="12.3456789",units="USD",
            status="Pending",category="Dining",tags=["Trip","Boat, trailer"],notes="First\nSecond café"))
        self.errors=[]
        errors=self.errors
        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")
        self.app=CheckedApp(self.repo)
        self.app.attributes("-fullscreen",False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        if self.app is not None:
            self.app.history.close()
            self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors,[])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors,[])

    def select(self, flow):
        self.app.ledger.selection_set(flow["flow_id"])
        self.app.ledger.focus(flow["flow_id"])
        self.app.ledger.focus_force()
        self.pump()

    def key(self, gesture, widget=None):
        widget=widget or self.app.ledger
        widget.focus_force()
        self.pump()
        widget.event_generate(gesture)
        self.pump()

    def copy_paste(self, flow=None):
        self.select(flow or self.original)
        self.key("<Control-c>")
        self.key("<Control-v>")
        return self.repo.get_flow(self.app.editing_id)

    def test_row_copy_paste_immediately_saves_every_field_under_new_id(self):
        copy=self.copy_paste()
        self.assertNotEqual(copy["flow_id"],self.original["flow_id"])
        self.assertEqual(copy,{**self.original,"flow_id":copy["flow_id"]})
        self.assertEqual(self.app.ledger.selection(),(copy["flow_id"],))
        self.assertEqual(self.app.ledger.focus(),copy["flow_id"])
        self.assertIn("disabled",self.app.save_button.state())
        self.assertEqual(self.app.form_title.cget("text"),"Edit Flow")
        self.assertEqual(self.app.form_vars["amount"].get(),"12.3456789")
        self.assertEqual(self.repo.get_flow(self.original["flow_id"]),self.original)
        self.key("<Control-v>")
        self.assertEqual(len({row["flow_id"] for row in self.repo.list_flows()}),3)

    def test_paste_undo_redo_and_subsequent_edit_use_one_ordered_history(self):
        copy=self.copy_paste()
        entry=self.app.form_widgets["name"].entry
        entry.focus_force()
        self.pump()
        entry.delete(0,"end");entry.insert(0,"Edited copy")
        entry.event_generate("<Return>")
        self.pump()
        self.key("<Control-z>")
        self.assertEqual(self.repo.get_flow(copy["flow_id"]),copy)
        self.key("<Control-z>")
        self.assertEqual(self.repo.list_flows(),[self.original])
        self.key("<Control-y>")
        self.assertEqual(self.repo.get_flow(copy["flow_id"]),copy)
        self.key("<Control-y>")
        self.assertEqual(self.repo.get_flow(copy["flow_id"])["name"],"Edited copy")
        self.assertEqual(self.repo.get_flow(self.original["flow_id"]),self.original)

    def test_clipboard_is_a_snapshot_that_survives_source_deletion(self):
        self.select(self.original)
        self.key("<Control-c>")
        self.assertEqual(json.loads(self.app.clipboard_get()),self.original)
        self.assertFalse(self.app.history.can_undo)
        self.key("<Delete>")
        self.key("<Control-v>")
        copy=self.repo.list_flows()[0]
        self.assertEqual(copy,{**self.original,"flow_id":copy["flow_id"]})
        self.assertNotEqual(copy["flow_id"],self.original["flow_id"])

    def test_every_status_including_canceled_is_copied_exactly(self):
        for status in ("Complete","Pending","Planned","Canceled"):
            flow=self.repo.save_flow({**self.original,"status":status})
            self.app.refresh_all()
            self.pump()
            copy=self.copy_paste(flow)
            self.assertEqual(copy,{**flow,"flow_id":copy["flow_id"]})

    def test_filters_survive_paste_and_hidden_copy_is_available_in_top_editor(self):
        kilogram = self.repo.save_flow({
            **{key: value for key, value in self.original.items() if key != "flow_id"},
            "units": "kg", "name": "Weight transfer"})
        self.app.refresh_all()
        self.pump()
        self.select(self.original)
        self.key("<Control-c>")
        self.app.ledger_filters["Units"].set_selected(["kg"],notify=True)
        self.pump()
        self.key("<Control-v>")
        copy=self.repo.get_flow(self.app.editing_id)
        self.assertEqual(copy,{**self.original,"flow_id":copy["flow_id"]})
        self.assertEqual(self.app.ledger_filters["Units"].get(),("kg",))
        self.assertEqual(self.app.ledger.get_children(),(kilogram["flow_id"],))
        self.assertIn("Hidden by current filters",self.app.status_message.get())
        self.assertEqual(self.app.form_vars["name"].get(),"Café")

    def test_invalid_clipboard_leaves_records_and_history_unchanged(self):
        before="\n".join(self.repo.conn.iterdump())
        for value in ("plain text","[]","{}","null",json.dumps({**self.original,"amount":"bad"}),
                      json.dumps({**self.original,"extra":"field"})):
            self.app.clipboard_clear();self.app.clipboard_append(value)
            self.key("<Control-v>")
            self.assertEqual("\n".join(self.repo.conn.iterdump()),before)
            self.assertFalse(self.app.history.can_undo)

    def test_copy_with_no_selected_row_keeps_clipboard(self):
        self.app.clipboard_clear();self.app.clipboard_append("keep me")
        self.key("<Control-c>")
        self.assertEqual(self.app.clipboard_get(),"keep me")
        self.assertFalse(self.app.history.can_undo)

    def test_entry_and_inline_cell_clipboard_remain_text_operations(self):
        self.select(self.original)
        entry=self.app.form_widgets["name"].entry
        entry.focus_force();self.pump()
        entry.selection_range(0,"end")
        self.key("<Control-c>",entry)
        self.assertEqual(self.app.clipboard_get(),"Café")
        entry.icursor("end");entry.selection_clear()
        self.key("<Control-v>",entry)
        self.assertEqual(entry.get(),"CaféCafé")
        entry.event_generate("<Return>");self.pump()
        self.assertEqual(len(self.repo.list_flows()),1)
        self.app.ledger_editor.open(self.original["flow_id"],"notes")
        self.pump()
        notes=self.app.ledger_editor.input
        self.key("<Control-c>",notes)
        self.assertEqual(self.app.clipboard_get(),self.original["notes"])
        notes.tag_remove("sel","1.0","end")
        notes.tag_add("sel","1.0","1.end")
        self.key("<Control-c>",notes)
        self.assertEqual(self.app.clipboard_get(),"First")
        notes.tag_remove("sel","1.0","end");notes.mark_set("insert","end-1c")
        self.key("<Control-v>",notes)
        self.assertTrue(notes.get("1.0","end-1c").endswith("First"))
        self.assertEqual(len(self.repo.list_flows()),1)

    def test_decimal_display_preserves_full_precision_in_editor_and_json(self):
        self.select(self.original)
        self.assertEqual(self.app.ledger.set(self.original["flow_id"],"amount"),"12.35")
        self.assertEqual(self.app.form_vars["amount"].get(),"12.3456789")
        self.app.ledger_editor.open(self.original["flow_id"],"amount")
        self.pump()
        self.assertEqual(self.app.ledger_editor.input.get(),"12.3456789")
        self.app.ledger_editor.input.event_generate("<Return>");self.pump()
        self.assertEqual(self.repo.get_flow(self.original["flow_id"]),self.original)
        with patch.object(self.app,"_show_text") as show:
            self.app.show_flow_json()
        self.assertEqual(json.loads(show.call_args.args[1]),self.original)
        self.app.show_view("accounts");self.pump()
        key=json.dumps(["A","USD"])
        self.assertEqual(self.app.accounts_tree.set(key,"balance"),"100.12")
        self.app.accounts_tree.selection_set(key);self.pump()
        self.assertEqual(self.app.account_vars["opening"].get(),"100.123456789")

    def test_only_requested_record_buttons_remain(self):
        def labels(parent):
            result=[]
            for child in parent.winfo_children():
                if isinstance(child,ttk.Button):
                    result.append(child.cget("text"))
                result.extend(labels(child))
            return result
        all_labels=labels(self.app)
        for removed in ("Repeat","Delete","Void","Cancel","Canceled","History","View all change history","Record JSON"):
            self.assertNotIn(removed,all_labels)
        self.assertEqual(all_labels.count("JSON record"),2)

    def test_close_finishes_valid_existing_edit_and_reopen_keeps_it(self):
        self.select(self.original)
        entry=self.app.form_widgets["name"].entry
        entry.focus_force();self.pump()
        entry.delete(0,"end");entry.insert(0,"Saved on close")
        self.app.request_close()
        self.app=None
        reopened=Repository(Path(self.directory.name)/"clipboard.sqlite3")
        try:
            self.assertEqual(reopened.get_flow(self.original["flow_id"])["name"],"Saved on close")
        finally:
            reopened.close()

    def test_close_does_not_add_new_top_form_draft(self):
        self.app.form_vars["from_account"].set("A")
        self.app.form_vars["to_account"].set("New destination")
        self.app.form_vars["amount"].set(".125")
        self.app.request_close()
        self.app=None
        reopened=Repository(Path(self.directory.name)/"clipboard.sqlite3")
        try:
            self.assertEqual(reopened.list_flows(),[self.original])
        finally:
            reopened.close()
