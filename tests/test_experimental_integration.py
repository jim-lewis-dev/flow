"""Tk integration checks for the optional, read-only account flow map.

These tests use a disposable database and exercise the visible controls and
canvas interactions, rather than comparing pixels or fixture layout.
"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ExperimentalIntegrationTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "experimental.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01", units="USD")
        self.repo.save_account("Wallet", "0", "2026-10-01", units="USD")
        self.flows = {}
        for name, when, amount, units, status, target in (
            ("Midnight", "2026-10-02", "10", "USD", "Complete", "Wallet"),
            ("Simultaneous", "2026-10-02 00:00:00", ".125", "USD", "Complete", "Shop"),
            ("Pending", "2026-10-02 12:00:00", "3", "USD", "Pending", "Shop"),
            ("Planned", "2026-10-03 08:00:00", "4", "USD", "Planned", "Wallet"),
            ("Canceled", "2026-10-03 09:00:00", "5", "USD", "Canceled", "Shop"),
            ("Weight", "2026-10-02", "2.34567", "kg", "Complete", "Wallet"),
        ):
            self.flows[name] = self.repo.save_flow(dict(
                date=when, from_account="Checking", to_account=target,
                amount=amount, units=units, status=status, name=name,
                category="Experiment", tags=["map"], notes=f"Note for {name}"))
        self.callback_errors = []
        callback_errors = self.callback_errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, trace):
                callback_errors.append(f"{kind.__name__}: {error}")

        self.dialog_patch = patch("flowledger.gui.messagebox.showerror",
                                  side_effect=lambda *args, **kwargs: self.fail(str(args)))
        self.dialog_patch.start()
        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1280x800+0+0")
        self.app.show_view("experimental")
        self.pump()

    def tearDown(self):
        try:
            if self.app.winfo_exists():
                self.app.experimental.close()
                self.app._close_popups()
                self.app.history.close()
                self.app.destroy()
        finally:
            self.dialog_patch.stop()
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must surface")

    def saved_state(self):
        return tuple(self.repo.list_flows()), tuple(self.repo.conn.iterdump())

    def visible_names(self):
        return [flow["name"] for flow in self.app.experimental._events]

    def chosen_names(self):
        view = self.app.experimental
        return [flow["name"] for flow in view._groups[view._index][1]]

    def select_basis(self, index):
        view = self.app.experimental
        view.basis_combo.current(index)
        view.basis_combo.event_generate("<<ComboboxSelected>>")
        self.pump()

    def select_unit(self, name):
        view = self.app.experimental
        choices = tuple(view.units_combo["values"])
        view.units_combo.current(choices.index(name))
        view.units_combo.event_generate("<<ComboboxSelected>>")
        self.pump()

    def test_last_tab_groups_equal_instants_and_steps_with_exact_labels(self):
        view = self.app.experimental
        self.assertEqual(tuple(self.app.nav_buttons)[-1], "experimental")
        self.assertEqual(self.app.current_view, "experimental")
        self.assertEqual(set(self.visible_names()), {"Midnight", "Simultaneous"})
        self.assertEqual(len(view._groups), 1)
        self.assertIn("0 of 1 times", view.progress_var.get())
        before = self.saved_state()
        view.next_button.invoke()
        self.pump()
        self.assertEqual(set(self.chosen_names()), {"Midnight", "Simultaneous"})
        self.assertEqual(set(view.flow_list.get_children()),
                         {self.flows["Midnight"]["flow_id"], self.flows["Simultaneous"]["flow_id"]})
        self.assertIn("0.125 USD", [view.canvas.itemcget(item, "text")
                                     for item in view.canvas.find_withtag("moving_flow")
                                     if view.canvas.type(item) == "text"])
        self.assertEqual(before, self.saved_state())
        view.previous_button.invoke()
        self.pump()
        self.assertEqual(view.flow_list.get_children(), ())

    def test_current_ledger_filters_then_unit_date_and_status_basis(self):
        view = self.app.experimental
        self.select_basis(1)
        self.assertEqual(set(self.visible_names()[:2]), {"Midnight", "Simultaneous"})
        self.assertEqual(self.visible_names()[2:], ["Pending"])
        self.select_basis(2)
        self.assertEqual(set(self.visible_names()[:2]), {"Midnight", "Simultaneous"})
        self.assertEqual(self.visible_names()[2:], ["Pending", "Planned"])
        self.assertNotIn("Canceled", self.visible_names())

        self.app.show_view("flows")
        self.pump()
        self.app.ledger_filters["Category"].set_selected(["Experiment"], notify=True)
        self.app.ledger_filters["Status"].set_selected(["Pending", "Canceled"], notify=True)
        self.app.show_view("experimental")
        self.pump()
        self.assertEqual(self.visible_names(), ["Pending"])
        self.assertEqual({flow["status"] for flow in view._all_visible}, {"Pending", "Canceled"})
        self.app.show_view("flows")
        self.app.ledger_filters["Status"].set_selected([], notify=True)
        self.app.show_view("experimental")
        self.pump()

        self.select_unit("kg")
        self.assertEqual(self.visible_names(), ["Weight"])
        self.assertEqual(view._groups[0][1][0]["amount"], self.flows["Weight"]["amount"])
        self.select_unit("USD")
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-03 08:00:00")
        view.through_entry.entry.delete(0, "end")
        view.through_entry.entry.insert(0, "2026-10-03 08:00:00")
        view.update_button.invoke()
        self.pump()
        self.assertEqual(self.visible_names(), ["Planned"])

    def test_filter_change_while_map_visible_refreshes_and_detail_opens_ledger(self):
        view = self.app.experimental
        saved = self.saved_state()
        # Session undo/redo can restore a ledger filter while this tab is open.
        self.app.ledger_filters["Status"].set_selected(["Pending"], notify=True)
        self.pump()
        self.assertEqual(self.visible_names(), [])
        self.select_basis(1)
        self.assertEqual(self.visible_names(), ["Pending"])
        view.next_button.invoke()
        self.pump()
        flow_id = self.flows["Pending"]["flow_id"]
        view.flow_list.selection_set(flow_id)
        view.flow_list.focus_force()
        view.flow_list.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.app.current_view, "flows")
        self.assertEqual(self.app.editing_id, flow_id)
        self.assertEqual(saved, self.saved_state())

    def test_empty_range_error_keeps_prior_map_and_resizes(self):
        view = self.app.experimental
        before = self.saved_state()
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-09")
        view.through_entry.entry.delete(0, "end")
        view.through_entry.entry.insert(0, "2026-10-09")
        view.update_button.invoke()
        self.pump()
        self.assertEqual(self.visible_names(), [])
        self.assertIn("No flows", view.canvas.itemcget(view.canvas.find_all()[0], "text"))
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-20")
        view.update_button.invoke()
        self.pump()
        self.assertIn("before", view.message_var.get())
        self.assertEqual(self.visible_names(), [])
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-02")
        view.through_entry.entry.delete(0, "end")
        view.through_entry.entry.insert(0, "2026-10-03")
        view.update_button.invoke()
        self.pump()
        self.app.geometry("1280x700+0+0")
        self.pump()
        self.assertGreater(view.canvas.winfo_width(), 600)
        self.assertGreater(view.canvas.winfo_height(), 250)
        self.assertTrue(view.canvas.find_withtag("node"))
        self.assertEqual(before, self.saved_state())

    def test_control_and_drag_undo_redo_change_only_session_view(self):
        view = self.app.experimental
        before_database = self.saved_state()
        self.select_basis(1)
        self.assertEqual(view.basis_var.get(), "Including Pending")
        self.app.undo()
        self.pump()
        self.assertEqual(view.basis_var.get(), "Confirmed")
        self.assertEqual(set(self.visible_names()), {"Midnight", "Simultaneous"})
        self.app.redo()
        self.pump()
        self.assertEqual(view.basis_var.get(), "Including Pending")

        node = next(item for item, name in view._node_items.items()
                    if name == "Checking" and view.canvas.type(item) == "oval")
        x0, y0, x1, y1 = view.canvas.bbox(node)
        x = int((x0 + x1) / 2 - view.canvas.canvasx(0))
        y = int((y0 + y1) / 2 - view.canvas.canvasy(0))
        original = tuple(view._positions["Checking"])
        view.canvas.event_generate("<Motion>", x=x, y=y)
        view.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        view.canvas.event_generate("<B1-Motion>", x=x + 40, y=y + 20)
        view.canvas.event_generate("<ButtonRelease-1>", x=x + 40, y=y + 20)
        self.pump()
        moved = tuple(view._positions["Checking"])
        self.assertNotEqual(moved, original)
        self.app.undo()
        self.pump()
        self.assertEqual(tuple(view._positions["Checking"]), original)
        self.app.redo()
        self.pump()
        self.assertEqual(tuple(view._positions["Checking"]), moved)
        self.app.show_view("charts")
        self.app.show_view("experimental")
        self.pump()
        self.assertEqual(tuple(view._positions["Checking"]), moved)
        self.assertEqual(before_database, self.saved_state())

    def test_play_tab_change_and_close_cancel_deferred_animation(self):
        view = self.app.experimental
        saved = self.saved_state()
        view.play_button.invoke()
        self.pump()
        self.assertTrue(view._playing)
        self.assertIsNotNone(view._after_id)
        self.assertTrue(self.app.show_view("charts"))
        self.pump()
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertTrue(self.app.show_view("experimental"))
        view.play_button.invoke()
        self.pump()
        self.assertTrue(view._playing)
        view.close()
        self.pump()
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertEqual(saved, self.saved_state())

    def test_play_advances_to_last_instant_without_writing_records(self):
        view = self.app.experimental
        self.select_basis(2)
        view.speed_combo.current(3)
        view.speed_combo.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(len(view._groups), 3)
        before = self.saved_state()
        view.play_button.invoke()
        self.app.after(1000, self.app.quit)
        self.app.mainloop()
        self.pump()
        self.assertEqual(view._index, 2)
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertEqual(before, self.saved_state())


if __name__ == "__main__":
    unittest.main()
