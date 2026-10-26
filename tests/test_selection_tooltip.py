"""Real ledger selection and hover checks using disposable saved records."""

import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class SelectionTooltipTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "selection.sqlite3")
        self.flows = []
        for index, (source, target, amount, unit, status, category) in enumerate((
            ("Checking", "Cafe", "10", "USD", "Complete", "Dining"),
            ("Checking", "Cafe", "-3", "USD", "Pending", "Dining"),
            ("Store", "Checking", "2", "kg", "Planned", "Supplies"),
            ("Checking", "Store", "0", "USD", "Canceled", "Supplies"),
            ("Other", "Shop", "999", "USD", "Complete", "Unselected"),
        )):
            self.flows.append(self.repo.save_flow(dict(
                date=f"2026-10-{index + 2:02d} 01:02:03", name=f"Flow {index}",
                from_account=source, to_account=target, amount=amount, units=unit,
                status=status, category=category, tags=["test"], notes=f"Note {index}")))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, trace):
                errors.append(f"{kind.__name__}: {error}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1280x800+0+0")
        self.app.focus_force()
        self.click_time = 1000
        self.pump()

    def tearDown(self):
        try:
            self.app.experimental.close()
            self.app._close_popups()
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

    def wait(self, milliseconds=20):
        finished = tk.BooleanVar(self.app, False)
        self.app.after(milliseconds, finished.set, True)
        self.app.wait_variable(finished)
        self.pump()

    def click(self, row, state=0):
        tree = self.app.ledger
        tree.see(row)
        self.pump()
        x, y, width, height = tree.bbox(row, "name")
        self.click_time += 1000
        for gesture, elapsed in (("<ButtonPress-1>", 0), ("<ButtonRelease-1>", 40)):
            tree.event_generate(gesture, x=x + width // 2, y=y + height // 2,
                                state=state, time=self.click_time + elapsed)
        self.pump()

    def hover(self, row):
        tree = self.app.ledger
        tree.see(row)
        self.pump()
        tooltip = self.app.ledger_tooltip
        tooltip.delay = 1
        x, y, width, height = tree.bbox(row, "name")
        tree.event_generate("<Motion>", x=x + width // 2, y=y + height // 2)
        self.wait()
        self.assertIsNotNone(tooltip.popup)
        return tooltip

    def test_ctrl_and_shift_selection_summary_and_normal_row_routing(self):
        ids = [flow["flow_id"] for flow in self.flows]
        normal = self.app.flow_tooltip(ids[0])
        other_normal = self.app.flow_tooltip(ids[4])
        self.click(ids[0])
        self.assertEqual(self.app.flow_tooltip(ids[0]), normal)
        tooltip = self.hover(ids[0])
        self.assertIsNone(tooltip.text, "Single rows retain their ordinary label tooltip")
        tooltip.hide()
        self.click(ids[1], state=0x0004)
        self.assertEqual(set(self.app.ledger.selection()), set(ids[:2]))
        summary = self.app.flow_tooltip(ids[0])
        self.assertIn("2 selected flows", summary)
        self.assertIn("Total quantities: 7 USD", summary)
        self.assertIn("From\n  Checking: 7 USD", summary)
        self.assertIn("To\n  Cafe: 7 USD", summary)
        self.assertIn("Categories\n  Dining: 7 USD", summary)
        self.assertEqual(self.app.flow_tooltip(ids[1]), summary)
        self.assertEqual(self.app.flow_tooltip(ids[4]), other_normal)

        # Range selection follows the displayed order, independently of the
        # record insertion order or which end of the range was clicked first.
        rows = self.app.ledger.get_children()
        self.click(rows[0])
        self.click(rows[-1], state=0x0001)
        self.assertEqual(self.app.ledger.selection(), rows)
        summary = self.app.flow_tooltip(rows[0])
        self.assertIn("all selected statuses", summary)
        self.assertIn("Complete 2", summary)
        self.assertIn("Pending 1", summary)
        self.assertIn("Planned 1", summary)
        self.assertIn("Canceled 1", summary)
        self.assertIn("Total quantities: 2 kg · 1,006 USD", summary)
        self.assertIn("Checking: 7 USD", summary)
        self.assertIn("Supplies: 2 kg · 0 USD", summary)
        self.assertNotIn("1008", summary, "Quantities of different units cannot be added")

    def test_keyboard_selection_dismisses_open_summary_without_changing_records(self):
        rows = self.app.ledger.get_children()
        before = tuple(self.repo.conn.iterdump())
        self.click(rows[0])
        self.click(rows[1], state=0x0004)
        tooltip = self.hover(rows[0])
        self.assertIsNotNone(tooltip.text)
        selected_before = self.app.ledger.selection()
        self.app.ledger.focus_force()
        self.app.ledger.event_generate("<Shift-Down>")
        self.pump()
        self.assertNotEqual(self.app.ledger.selection(), selected_before)
        self.assertIsNone(tooltip.popup)
        self.assertIsNone(tooltip._after_id)
        self.assertEqual(tuple(self.repo.conn.iterdump()), before)

    def test_large_selected_hover_is_bounded_scrollable_and_keeps_focus(self):
        for index in range(55):
            self.repo.save_flow(dict(
                date="2026-10-01", name=f"Extra {index}",
                from_account=f"Source {index:02d}", to_account=f"Target {index:02d}",
                amount="1", units="kg", status="Complete",
                category=f"Category {index:02d}", tags=[], notes=""))
        self.app.refresh_all()
        self.pump()
        rows = self.app.ledger.get_children()
        self.app.ledger.selection_set(rows)
        self.app.ledger.focus(rows[0])
        self.app.ledger.focus_force()
        self.pump()
        before = tuple(self.repo.conn.iterdump())
        tooltip = self.hover(rows[0])
        popup, text = tooltip.popup, tooltip.text
        self.assertIsNotNone(text)
        self.assertEqual(str(text.cget("state")), "disabled")
        self.assertIs(self.app.focus_get(), self.app.ledger)
        x, y = popup.winfo_rootx(), popup.winfo_rooty()
        right, bottom = x + popup.winfo_width(), y + popup.winfo_height()
        self.assertGreaterEqual(x, max(0, self.app.winfo_rootx()))
        self.assertGreaterEqual(y, max(0, self.app.winfo_rooty()))
        self.assertLessEqual(right, min(self.app.winfo_screenwidth(),
                                       self.app.winfo_rootx() + self.app.winfo_width()))
        self.assertLessEqual(bottom, min(self.app.winfo_screenheight(),
                                        self.app.winfo_rooty() + self.app.winfo_height()))
        self.assertLess(text.yview()[1], 1.0, "Long summaries must expose scrolling")
        text.event_generate("<MouseWheel>", delta=-12000)
        self.pump()
        self.assertEqual(text.yview()[1], 1.0)
        self.assertIsNotNone(text.dlineinfo("end-1c"), "The last summary line must be reachable")
        self.assertIs(self.app.focus_get(), self.app.ledger)
        self.assertEqual(tuple(self.repo.conn.iterdump()), before)

    def test_pointer_inside_summary_preserves_ledger_undo_redo_and_delete(self):
        for index in range(20):
            self.repo.save_flow(dict(
                date="2026-10-01", name=f"Pointer {index}",
                from_account=f"Pointer source {index:02d}",
                to_account=f"Pointer target {index:02d}",
                amount="1", units="kg", status="Complete",
                category=f"Pointer category {index:02d}", tags=[], notes=""))
        data = {**self.flows[0], "name": "Undo target"}
        data.pop("flow_id")
        added = self.app.history.perform("Add flow", lambda: self.repo.save_flow(data))
        self.app.refresh_all()
        self.pump()

        def point_inside_summary():
            rows = self.app.ledger.get_children()
            self.app.ledger.selection_set(rows)
            self.app.ledger.focus(rows[0])
            self.app.ledger.focus_force()
            self.pump()
            tooltip = self.hover(rows[0])
            # Warp the actual X pointer into the separate hover window. A
            # synthetic Motion alone misses window-manager focus transitions.
            tooltip.text.event_generate("<Motion>", x=20, y=20, warp=True)
            self.wait(220)
            self.assertIs(self.app.focus_get(), self.app.ledger)
            self.assertIsNotNone(tooltip.popup)
            self.assertIsNone(tooltip._leave_id)
            return tooltip

        tooltip = point_inside_summary()
        focus_events = []
        tooltip.popup.bind("<FocusIn>", lambda event: focus_events.append(event.widget), add="+")
        self.wait(100)
        settled = len(focus_events)
        self.wait(100)
        self.assertEqual(len(focus_events), settled, "Restoring focus must settle without a loop")
        self.assertLess(tooltip.text.yview()[1], 1.0)
        tooltip.text.event_generate("<MouseWheel>", delta=-12000)
        self.pump()
        self.assertEqual(tooltip.text.yview()[1], 1.0)
        self.assertIsNotNone(tooltip.popup)
        self.assertIs(self.app.focus_get(), self.app.ledger)
        self.app.focus_get().event_generate("<Control-z>")
        self.pump()
        self.assertIsNone(self.repo.get_flow(added["flow_id"]))

        point_inside_summary()
        self.app.focus_get().event_generate("<Control-y>")
        self.pump()
        self.assertEqual(self.repo.get_flow(added["flow_id"]), added)

        point_inside_summary()
        saved = self.repo.list_flows()
        self.app.focus_get().event_generate("<Delete>")
        self.pump()
        self.assertEqual(self.repo.list_flows(), [])
        self.app.focus_get().event_generate("<Control-z>")
        self.pump()
        self.assertEqual(self.repo.list_flows(), saved)

    def test_hide_tab_switch_and_tree_destruction_cancel_hover_timers(self):
        rows = self.app.ledger.get_children()
        self.click(rows[0])
        self.click(rows[1], state=0x0004)
        tooltip = self.hover(rows[0])
        tooltip.hide()
        self.assertIsNone(tooltip.popup)
        self.assertIsNone(tooltip.text)

        def pending_hover():
            tooltip.delay = 1000
            x, y, width, height = self.app.ledger.bbox(rows[0], "name")
            self.app.ledger.event_generate("<Motion>", x=x + width // 2,
                                           y=y + height // 2)
            self.assertIsNotNone(tooltip._after_id)
            return tooltip._after_id

        timer = pending_hover()
        self.app.show_view("accounts")
        self.pump()
        self.assertIsNone(tooltip._after_id)
        self.assertNotIn(timer, self.app.tk.call("after", "info"))
        self.app.show_view("flows")
        self.pump()
        timer = pending_hover()
        self.app.ledger.destroy()
        self.pump()
        self.assertIsNone(tooltip._after_id)
        self.assertIsNone(tooltip.popup)
        self.assertNotIn(timer, self.app.tk.call("after", "info"))


if __name__ == "__main__":
    unittest.main()
