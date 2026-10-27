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

    def apply_typed_dates(self):
        self.app.after(300, self.app.quit)
        self.app.mainloop()
        self.pump()

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
        self.apply_typed_dates()
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

    def test_typed_date_ranges_refresh_errors_clear_and_map_resizes(self):
        view = self.app.experimental
        before = self.saved_state()
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-09")
        view.through_entry.entry.delete(0, "end")
        view.through_entry.entry.insert(0, "2026-10-09")
        self.apply_typed_dates()
        self.assertEqual(self.visible_names(), [])
        self.assertIn("No flows", view.canvas.itemcget(view.canvas.find_all()[0], "text"))
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-20")
        self.apply_typed_dates()
        self.assertIn("before", view.message_var.get())
        self.assertEqual(self.visible_names(), [])
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-02")
        view.through_entry.entry.delete(0, "end")
        view.through_entry.entry.insert(0, "2026-10-03")
        self.apply_typed_dates()
        before_width = view.canvas.winfo_width()
        before_height = view.canvas.winfo_height()
        self.app.geometry("1280x700+0+0")
        self.pump()
        self.assertEqual(view.canvas.winfo_width(), before_width)
        self.assertGreater(view.canvas.winfo_height(), 1)
        self.assertLess(view.canvas.winfo_height(), before_height)
        self.assertTrue(view.canvas.find_withtag("node"))
        self.assertEqual(before, self.saved_state())

    def test_date_typing_and_calendar_selection_refresh_and_undo_without_button(self):
        view = self.app.experimental
        saved = self.saved_state()
        self.select_basis(2)
        original = view.get_state()
        before_history = len(self.app.history._undo)
        self.assertFalse(hasattr(view, "update_button"))
        view.from_entry.entry.delete(0, "end")
        view.from_entry.entry.insert(0, "2026-10-03")
        self.apply_typed_dates()
        self.assertEqual(self.visible_names(), ["Planned"])
        self.assertEqual(len(self.app.history._undo), before_history + 1)
        self.assertIsNone(view._date_after_id)
        self.app.undo()
        self.pump()
        self.assertEqual(view.get_state(), original)
        self.assertIsNone(view._date_after_id)
        self.app.redo()
        self.pump()
        self.assertEqual(self.visible_names(), ["Planned"])

        view.from_entry.open_popup()
        self.pump()
        view.from_entry.day_buttons[2].invoke()
        self.pump()
        self.assertEqual(view.from_var.get(), "2026-10-02")
        self.assertIn("Pending", self.visible_names())
        self.assertIsNone(view._date_after_id)
        after_calendar_history = len(self.app.history._undo)
        self.apply_typed_dates()
        self.assertEqual(len(self.app.history._undo), after_calendar_history)

        view.from_var.set("bad date")
        self.apply_typed_dates()
        self.assertEqual(view.from_var.get(), "bad date")
        self.assertTrue(view.message_var.get())
        self.app.undo()
        self.pump()
        self.assertEqual(view.from_var.get(), "2026-10-02")
        self.assertIn("Pending", self.visible_names())
        self.assertEqual(saved, self.saved_state())

    def test_canvas_scroll_moves_view_without_moving_accounts_or_writing_records(self):
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        positions = dict(view._positions)
        canvas.configure(scrollregion=(0, 0, 2400, 1800))
        canvas.xview_moveto(0.25)
        canvas.yview_moveto(0.25)
        self.pump()
        start_x, start_y = canvas.canvasx(0), canvas.canvasy(0)
        canvas.event_generate("<Button-5>", x=60, y=60)
        self.pump()
        self.assertEqual(canvas.canvasx(0), start_x)
        self.assertGreater(canvas.canvasy(0), start_y)
        scrolled_y = canvas.canvasy(0)
        canvas.event_generate("<Shift-Button-5>", x=60, y=60)
        self.pump()
        self.assertGreater(canvas.canvasx(0), start_x)
        self.assertEqual(canvas.canvasy(0), scrolled_y)
        canvas.event_generate("<MouseWheel>", delta=120, x=60, y=60)
        self.pump()
        self.assertLess(canvas.canvasy(0), scrolled_y)
        self.assertEqual(view._positions, positions)
        self.assertEqual(saved, self.saved_state())

    def test_ctrl_wheel_anchors_zoom_and_history_preserves_playback(self):
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        view.next_button.invoke()
        self.pump()
        positions = dict(view._positions)
        x, y = canvas.winfo_width() // 2, canvas.winfo_height() // 2
        before = view.get_state()
        anchor = (canvas.canvasx(x) / view._zoom,
                  canvas.canvasy(y) / view._zoom)
        playback = (view._index, view._progress)
        canvas.event_generate("<Control-Button-4>", x=x, y=y, state=4)
        self.pump()
        self.assertAlmostEqual(view._zoom, 1.2)
        self.assertAlmostEqual(canvas.canvasx(x) / view._zoom, anchor[0], delta=2)
        self.assertAlmostEqual(canvas.canvasy(y) / view._zoom, anchor[1], delta=2)
        self.assertEqual((view._index, view._progress), playback)
        self.assertEqual(view._positions, positions)
        zoomed = view.get_state()
        pulse = next(item for item in view._pulse_items if canvas.type(item) == "oval")
        coordinates = canvas.coords(pulse)
        px = round((coordinates[0] + coordinates[2]) / 2 - canvas.canvasx(0))
        py = round((coordinates[1] + coordinates[3]) / 2 - canvas.canvasy(0))
        self.assertEqual(view._item_at_position(px, py), pulse)
        view._render_pulses()
        recreated = next(item for item in view._pulse_items if canvas.type(item) == "oval")
        self.assertEqual(canvas.coords(recreated), coordinates)
        self.app.undo()
        self.pump()
        self.assertEqual(view.get_state(), before)
        self.assertEqual((view._index, view._progress), playback)
        self.app.redo()
        self.pump()
        self.assertEqual(view.get_state(), zoomed)
        self.assertEqual((view._index, view._progress), playback)
        canvas.event_generate("<Control-Button-5>", x=x, y=y, state=4)
        self.pump()
        self.assertAlmostEqual(view._zoom, 1.0)

        # Playback may advance after the camera action enters history. Undo
        # should restore the camera while keeping the current live step.
        self.select_basis(2)
        view.speed_scale.set(5)
        self.pump()
        view.play_button.invoke()
        canvas.event_generate("<Control-Button-4>", x=x, y=y, state=4)
        self.app.after(260, self.app.quit)
        self.app.mainloop()
        self.pump()
        live_index, live_progress = view._index, view._progress
        self.assertEqual(live_index, 1)
        self.app.undo()
        self.pump()
        self.assertTrue(view._playing)
        self.assertEqual(view._index, live_index)
        self.assertGreaterEqual(view._progress, live_progress)
        self.assertAlmostEqual(view._zoom, 1.0)
        self.app.redo()
        self.pump()
        self.assertTrue(view._playing)
        self.assertEqual(view._index, live_index)
        self.assertAlmostEqual(view._zoom, 1.2)
        self.assertEqual(saved, self.saved_state())

    def test_background_pan_fit_and_redraw_keep_zoom_and_node_layout(self):
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        positions = dict(view._positions)
        x, y = canvas.winfo_width() // 2, canvas.winfo_height() // 2
        for _ in range(4):
            canvas.event_generate("<Control-Button-4>", x=x, y=y, state=4)
            self.pump()
        before_pan = view.get_state()
        canvas.event_generate("<Button-5>", x=40, y=100, state=0)
        self.pump()
        panned = view.get_state()
        self.assertGreater(panned["viewport"][1], before_pan["viewport"][1])
        self.app.undo()
        self.pump()
        self.assertEqual(view.get_state(), before_pan)
        self.app.redo()
        self.pump()
        self.assertEqual(view.get_state(), panned)
        view._draw_scene()
        self.pump()
        self.assertEqual(view.get_state(), panned)

        # Drag an empty region to move the camera, leaving node positions alone.
        self.assertIsNone(view._item_at_position(20, 130))
        canvas.event_generate("<ButtonPress-1>", x=20, y=130, time=1000)
        canvas.event_generate("<B1-Motion>", x=10, y=100, time=1010)
        canvas.event_generate("<ButtonRelease-1>", x=10, y=100, time=1020)
        self.pump()
        self.assertNotEqual(view.get_state()["viewport"], panned["viewport"])
        self.assertEqual(view._positions, positions)
        before_fit = view.get_state()
        # Actual repeated clicks exercise the double-click binding.
        for at in (2000, 2100):
            canvas.event_generate("<ButtonPress-1>", x=15, y=130, time=at)
            canvas.event_generate("<ButtonRelease-1>", x=15, y=130, time=at + 20)
        self.pump()
        self.assertLess(view._zoom, before_fit["zoom"])
        self.assertEqual(view._positions, positions)
        self.app.undo()
        self.pump()
        self.assertEqual(view.get_state(), before_fit)
        self.assertEqual(saved, self.saved_state())

    def test_camera_undo_dismisses_hover_before_playback_has_started(self):
        view = self.app.experimental
        canvas = view.canvas
        canvas.event_generate("<Control-Button-4>",
                              x=canvas.winfo_width() // 2,
                              y=canvas.winfo_height() // 2, state=4)
        self.pump()
        node = next(item for item, name in view._node_items.items()
                    if name == "Checking" and canvas.type(item) == "oval")
        x0, y0, x1, y1 = canvas.coords(node)
        canvas.event_generate("<Motion>",
            x=round((x0 + x1) / 2 - canvas.canvasx(0)),
            y=round((y0 + y1) / 2 - canvas.canvasy(0)))
        self.app.after(600, self.app.quit)
        self.app.mainloop()
        self.pump()
        self.assertEqual(view._index, -1)
        self.assertTrue(view._hover_popup.winfo_viewable())
        self.app.undo()
        self.pump()
        self.assertEqual(view._index, -1)
        self.assertIsNone(view._hover_target)
        self.assertIsNone(view._hover_after_id)
        self.assertFalse(view._hover_popup.winfo_viewable())

    def test_node_drag_uses_world_coordinates_after_zoom(self):
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        canvas.event_generate("<Control-Button-4>",
                              x=canvas.winfo_width() // 2,
                              y=canvas.winfo_height() // 2, state=4)
        self.pump()
        node = next(item for item, name in view._node_items.items()
                    if name == "Checking" and canvas.type(item) == "oval")
        x0, y0, x1, y1 = canvas.coords(node)
        x, y = round((x0 + x1) / 2 - canvas.canvasx(0)), round((y0 + y1) / 2 - canvas.canvasy(0))
        before = view.get_state()
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<B1-Motion>", x=x + 36, y=y + 24)
        canvas.event_generate("<ButtonRelease-1>", x=x + 36, y=y + 24)
        self.pump()
        self.assertAlmostEqual(view._positions["Checking"][0],
            before["positions"]["Checking"][0] + 36 / (view._scene_width * view._zoom), delta=.002)
        self.assertAlmostEqual(view._positions["Checking"][1],
            before["positions"]["Checking"][1] + 24 / (view._scene_height * view._zoom), delta=.002)
        moved = view.get_state()
        self.app.undo()
        self.pump()
        self.assertEqual(view.get_state(), before)
        self.app.redo()
        self.pump()
        self.assertEqual(view.get_state(), moved)
        self.assertEqual(saved, self.saved_state())

    def test_drag_outside_original_layout_follows_pointer_at_each_zoom_and_undoes(self):
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        for index, (zoom, corner) in enumerate(((.5, 1), (.5, -1), (2, 1), (2, -1))):
            with self.subTest(zoom=zoom, corner=corner):
                start = .5 if zoom < 1 else (1.1 if corner > 0 else -.1)
                view._positions["Checking"] = (start, start)
                view._zoom_at(zoom / view._zoom, canvas.winfo_width() // 2,
                              canvas.winfo_height() // 2)
                x, y = view._xy("Checking")
                view._draw_scene(viewport=(x - canvas.winfo_width() / (2 * zoom),
                                           y - canvas.winfo_height() / (2 * zoom)))
                self.pump()
                node = next(item for item, name in view._node_items.items()
                            if name == "Checking" and canvas.type(item) == "oval")
                x0, y0, x1, y1 = canvas.coords(node)
                start_x = round((x0 + x1) / 2 - canvas.canvasx(0))
                start_y = round((y0 + y1) / 2 - canvas.canvasy(0))
                target_x = canvas.winfo_width() - 40 if corner > 0 else 40
                target_y = canvas.winfo_height() - 35 if corner > 0 else 35
                expected = (canvas.canvasx(target_x) / zoom, canvas.canvasy(target_y) / zoom)
                before = view.get_state()
                when = 1000 * (index + 1)
                canvas.event_generate("<ButtonPress-1>", x=start_x, y=start_y, time=when)
                canvas.event_generate("<B1-Motion>", x=target_x, y=target_y, time=when + 20)
                canvas.event_generate("<ButtonRelease-1>", x=target_x, y=target_y, time=when + 40)
                self.pump()
                actual = view._xy("Checking")
                for coordinate in view._positions["Checking"]:
                    if corner > 0:
                        self.assertGreater(coordinate, 1)
                    else:
                        self.assertLess(coordinate, 0)
                self.assertAlmostEqual(actual[0], expected[0], delta=2 / zoom)
                self.assertAlmostEqual(actual[1], expected[1], delta=2 / zoom)
                self.assertAlmostEqual(actual[0] * zoom - canvas.canvasx(0), target_x, delta=2)
                self.assertAlmostEqual(actual[1] * zoom - canvas.canvasy(0), target_y, delta=2)
                moved = view.get_state()
                self.app.undo()
                self.pump()
                self.assertEqual(view.get_state(), before)
                self.app.redo()
                self.pump()
                self.assertEqual(view.get_state(), moved)
        self.assertEqual(saved, self.saved_state())

    def test_fit_and_scroll_include_accounts_beyond_every_original_edge(self):
        from types import SimpleNamespace
        view = self.app.experimental
        canvas = view.canvas
        saved = self.saved_state()
        view._positions["Checking"] = (-.3, -.4)
        view._positions["Shop"] = (1.4, 1.5)
        view._draw_scene()
        self.pump()
        positions = dict(view._positions)
        left, top, right, bottom = map(float, canvas.cget("scrollregion").split())
        for item in view._node_items:
            x0, y0, x1, y1 = canvas.bbox(item)
            self.assertLessEqual(left, x0)
            self.assertLessEqual(top, y0)
            self.assertGreaterEqual(right, x1)
            self.assertGreaterEqual(bottom, y1)
        view._fit_from_pointer(SimpleNamespace(x=2, y=canvas.winfo_height() - 3))
        self.pump()
        for item in view._node_items:
            x0, y0, x1, y1 = canvas.bbox(item)
            self.assertGreaterEqual(x0 - canvas.canvasx(0), 0)
            self.assertGreaterEqual(y0 - canvas.canvasy(0), 0)
            self.assertLessEqual(x1 - canvas.canvasx(0), canvas.winfo_width())
            self.assertLessEqual(y1 - canvas.canvasy(0), canvas.winfo_height())
        self.app.show_view("flows")
        self.app.show_view("experimental")
        self.pump()
        self.assertEqual(view._positions, positions)
        self.assertEqual(saved, self.saved_state())

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
        view.speed_scale.set(5)
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
