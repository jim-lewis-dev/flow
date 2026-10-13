"""Real Tk checks for multiple selections and atomic ledger row actions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class MultiRowTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "multi.sqlite3")
        self.repo.save_account("A", "100", "2026-10-01")
        self.repo.save_account("B", "0", "2026-10-01")
        self.originals = [self.repo.save_flow(dict(
            flow_id=str(uuid.UUID(int=index + 1)), date="2026-10-02 01:02:03",
            name=f"Row {index}", from_account="A", to_account="B",
            amount="-12.345678901" if index == 0 else "0" if index == 1 else str(index),
            units="USD", status="Pending" if index == 1 else "Complete",
            category="Dining", tags=["Trip", "Boat, trailer"], notes="First\nSecond café"))
            for index in range(4)]
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.click_time = 1000
        self.pump()

    def tearDown(self):
        try:
            self.app.history.close()
            self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def key(self, gesture):
        self.app.ledger.focus_force()
        self.pump()
        self.app.ledger.event_generate(gesture)
        self.pump()

    def select(self, rows, active=None, tree=None):
        tree = tree or self.app.ledger
        tree.selection_set(rows)
        tree.focus(active or rows[-1])
        tree.focus_force()
        self.pump()

    def click(self, row, state=0):
        tree = self.app.ledger
        tree.see(row)
        self.pump()
        x, y, width, height = tree.bbox(row, "name")
        self.click_time += 1000
        tree.event_generate("<ButtonPress-1>", x=x + width // 2, y=y + height // 2,
                            state=state, time=self.click_time)
        tree.event_generate("<ButtonRelease-1>", x=x + width // 2, y=y + height // 2,
                            state=state, time=self.click_time + 40)
        self.pump()

    def test_ctrl_and_shift_click_selection_survives_form_load_and_refresh(self):
        rows = self.app.ledger.get_children()
        self.click(rows[0])
        self.click(rows[2], state=0x0004)
        self.assertEqual(self.app.ledger.selection(), (rows[0], rows[2]))
        self.assertEqual(self.app.editing_id, self.app.ledger.focus())
        self.app.refresh_all()
        self.pump()
        self.assertEqual(self.app.ledger.selection(), (rows[0], rows[2]))
        self.click(rows[0])
        self.click(rows[3], state=0x0001)
        self.assertEqual(self.app.ledger.selection(), rows)
        self.assertEqual(self.app.editing_id, self.app.ledger.focus())
        active = self.app.editing_id
        self.app.form_vars["name"].set("Only active row changed")
        self.assertTrue(self.app._finish_existing("flow"))
        self.pump()
        self.assertEqual(self.app.ledger.selection(), rows)
        for record in self.originals:
            expected = {**record, "name": "Only active row changed"} if record["flow_id"] == active else record
            self.assertEqual(self.repo.get_flow(record["flow_id"]), expected)
        before_sort = self.app.ledger.get_children()
        self.app.sort_ledger("name")
        self.pump()
        by_name = self.app.ledger.get_children()
        self.assertNotEqual(by_name, before_sort)
        self.key("<Control-z>")
        self.assertEqual(self.app.ledger.get_children(), before_sort)
        self.assertEqual(self.app.ledger.selection(), rows)
        self.key("<Control-y>")
        self.assertEqual(self.app.ledger.get_children(), by_name)

    def test_batch_copy_paste_preserves_all_fields_order_and_one_undo_step(self):
        sources = tuple(record["flow_id"] for record in self.originals[:2])
        self.select(sources)
        self.key("<Control-c>")
        clipboard = json.loads(self.app.clipboard_get())
        self.assertEqual(clipboard, self.originals[:2])
        self.key("<Control-v>")
        copies = self.app.ledger.selection()
        self.assertEqual(len(copies), 2)
        self.assertTrue(set(copies).isdisjoint(sources))
        current = self.app.ledger.get_children()
        for source, fid in zip(clipboard, copies):
            self.assertEqual(self.repo.get_flow(fid), {**source, "flow_id": fid})
            self.assertEqual(current.index(fid), current.index(source["flow_id"]) + 1)
        self.assertEqual(self.app.editing_id, copies[-1])
        self.app.form_vars["name"].set("Edited copy")
        self.assertTrue(self.app._finish_existing("flow"))
        self.pump()
        self.assertEqual(self.app.ledger.selection(), copies)
        self.assertEqual(self.repo.get_flow(copies[0])["name"], clipboard[0]["name"])
        self.assertEqual(self.repo.get_flow(copies[1])["name"], "Edited copy")
        self.key("<Control-z>")
        self.assertEqual(self.repo.get_flow(copies[1])["name"], clipboard[1]["name"])
        self.key("<Control-z>")
        self.assertEqual(self.repo.list_flows(), self.originals)
        self.key("<Control-y>")
        self.key("<Control-y>")
        self.assertEqual(len(self.repo.list_flows()), 6)
        self.assertEqual(self.repo.get_flow(copies[1])["name"], "Edited copy")
        for source in self.originals:
            self.assertEqual(self.repo.get_flow(source["flow_id"]), source)

    def test_delete_multiple_rows_is_one_undoable_action(self):
        selected = (self.originals[0]["flow_id"], self.originals[2]["flow_id"])
        self.select(selected)
        self.key("<Delete>")
        self.assertEqual(len(self.repo.list_flows()), 2)
        self.assertTrue(all(self.repo.get_flow(fid) is None for fid in selected))
        self.assertEqual(self.app.ledger.selection(), ())
        self.assertIsNone(self.app.editing_id)
        self.key("<Control-z>")
        self.assertEqual(self.repo.list_flows(), self.originals)
        self.key("<Control-y>")
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_invalid_second_clipboard_row_rolls_back_the_whole_batch(self):
        before = "\n".join(self.repo.conn.iterdump())
        before_token = self.app.history.undo_token
        payload = [self.originals[0], {**self.originals[1], "amount": "invalid"}]
        self.app.clipboard_clear()
        self.app.clipboard_append(json.dumps(payload))
        self.key("<Control-v>")
        self.assertEqual("\n".join(self.repo.conn.iterdump()), before)
        self.assertIs(self.app.history.undo_token, before_token)
        self.assertIsNone(self.app.editing_id)

    def test_nonstring_clipboard_ids_are_ignored_before_saving(self):
        payload = [{**self.originals[0], "flow_id": ["ignored"]},
                   {**self.originals[1], "flow_id": {"ignored": True}}]
        self.app.clipboard_clear()
        self.app.clipboard_append(json.dumps(payload))
        self.key("<Control-v>")
        self.assertEqual(len(self.repo.list_flows()), 6)
        selected = self.app.ledger.selection()
        self.assertEqual(len(selected), 2)
        for original, fid in zip(self.originals, selected):
            self.assertEqual(self.repo.get_flow(fid), {**original, "flow_id": fid})
            self.assertEqual(str(uuid.UUID(fid)), fid)

    def test_mixed_visibility_batch_preserves_filters_and_selects_visible_copy(self):
        self.select(tuple(record["flow_id"] for record in self.originals[:2]))
        self.key("<Control-c>")
        self.app.ledger_filters["Status"].set_selected(["Complete"], notify=True)
        self.pump()
        self.key("<Control-v>")
        self.assertEqual(len(self.repo.list_flows()), 6)
        selected = self.app.ledger.selection()
        self.assertEqual(len(selected), 1)
        self.assertEqual(self.repo.get_flow(selected[0])["status"], "Complete")
        self.assertEqual(self.app.editing_id, selected[0])
        self.assertEqual(self.app.ledger_filters["Status"].get(), ("Complete",))
        self.assertIn("Some hidden by current filters", self.app.status_message.get())

    def test_accounts_multiple_selection_edits_only_focused_starting_balance(self):
        self.app.show_view("accounts")
        self.pump()
        tree = self.app.accounts_tree
        rows = tree.get_children()
        self.select(rows, active=rows[-1], tree=tree)
        active = self.app.account_editing
        self.assertEqual(active, rows[-1])
        self.app.account_cell_editor.open(active, "balance")
        self.pump()
        self.assertEqual(tree.selection(), rows)
        self.app.account_cell_editor.input.delete(0, "end")
        self.app.account_cell_editor.input.insert(0, "25")
        self.assertTrue(self.app.account_cell_editor.close())
        self.pump()
        self.assertEqual(tree.selection(), rows)
        self.assertEqual(self.repo.get_account("A")["opening_amount"], "100")
        self.assertEqual(self.repo.get_account("B")["opening_amount"], "25")
