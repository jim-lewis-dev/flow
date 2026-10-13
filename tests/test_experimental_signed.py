"""Signed and zero events keep animation, hover details, and balances consistent."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger.experimental import BG, FLOW_COLORS, ExperimentalView


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class SignedExperimentalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "motion.sqlite3")
        self.repo.save_account("A", "100", "2026-10-01", units="kg")
        self.repo.save_account("B", "10", "2026-10-01", units="kg")
        self.selected = []
        self.callback_errors = []
        errors = self.callback_errors

        class CheckedRoot(tk.Tk):
            def report_callback_exception(self, kind, error, trace):
                errors.append(f"{kind.__name__}: {error}")

        self.root = CheckedRoot()
        self.root.geometry("1200x800+0+0")
        self.view = ExperimentalView(self.root, self.repo, self.repo.list_flows,
                                     self.selected.append)
        self.view.pack(fill="both", expand=True)
        self.pump()

    def tearDown(self):
        try:
            self.view.close()
            self.root.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [])

    def pump(self):
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(self.callback_errors, [])

    def add_flow(self, name, amount, when="2026-10-02", source="A", target="B"):
        return self.repo.save_flow(dict(date=when, from_account=source, to_account=target,
                                       amount=amount, units="kg", status="Complete", name=name,
                                       category="Test", tags=["motion"], notes=f"Note for {name}"))

    def prepare(self):
        self.view.refresh()
        self.pump()
        return deepcopy(self.repo.list_flows()), tuple(self.repo.conn.iterdump())

    def oval(self, flow_id):
        return next(item for item, flow in self.view._pulse_items.items()
                    if flow["flow_id"] == flow_id and self.view.canvas.type(item) == "oval")

    def center(self, flow_id):
        x0, y0, x1, y1 = self.view.canvas.coords(self.oval(flow_id))
        return (x0 + x1) / 2, (y0 + y1) / 2

    def assert_records_unchanged(self, before):
        self.assertEqual(before, (self.repo.list_flows(), tuple(self.repo.conn.iterdump())))

    def test_mixed_signs_have_actual_direction_distinct_lanes_and_correct_totals(self):
        positive = self.add_flow("Forward", "5")
        negative = self.add_flow("Reverse", "-2")
        before = self.prepare()
        self.assertEqual(self.view._directed_pairs, {("A", "B"), ("B", "A")})
        self.view.jump_start()
        positive_start = self.center(positive["flow_id"])
        negative_start = self.center(negative["flow_id"])
        self.assertLess(positive_start[0], negative_start[0])
        self.assertNotEqual(positive_start[1], negative_start[1])
        self.view.jump_end()
        self.assertGreater(self.center(positive["flow_id"])[0], positive_start[0])
        self.assertLess(self.center(negative["flow_id"])[0], negative_start[0])
        self.assertIn("In 2 kg · Out 5 kg", self.view._node_hover_text("A"))
        self.assertIn("In 5 kg · Out 2 kg", self.view._node_hover_text("B"))
        self.assertIn("97 kg", self.view._node_hover_text("A"))
        self.assertIn("13 kg", self.view._node_hover_text("B"))
        self.assertIn("A → B · -2 kg", self.view._flow_hover_text(negative))
        self.assert_records_unchanged(before)

    def test_all_negative_range_sizes_by_magnitude_and_keeps_original_hover_record(self):
        small = self.add_flow("Small reverse", "-1")
        large = self.add_flow("Large reverse", "-100")
        before = self.prepare()
        self.assertEqual(self.view._max_amount, 100)
        self.assertEqual(self.view._directed_pairs, {("B", "A")})
        self.view.next()
        small_box = self.view.canvas.coords(self.oval(small["flow_id"]))
        large_box = self.view.canvas.coords(self.oval(large["flow_id"]))
        self.assertLess(small_box[2] - small_box[0], large_box[2] - large_box[0])
        pulse_record = self.view._pulse_items[self.oval(large["flow_id"])]
        self.assertEqual(pulse_record, large)
        for value in ("Large reverse", "A → B · -100 kg", "Note for Large reverse"):
            self.assertIn(value, self.view._flow_hover_text(pulse_record))
        self.assertIn("In 101 kg · Out 0 kg", self.view._node_hover_text("A"))
        self.assert_records_unchanged(before)

    def test_zero_comment_is_stationary_hollow_hoverable_and_in_time_steps(self):
        comment = self.add_flow("Inspection", "0")
        self.add_flow("Later comment", "0", when="2026-10-03 12:00:00")
        before = self.prepare()
        self.assertEqual(self.view._max_amount, 0)
        self.assertEqual(len(self.view._groups), 2)
        self.view.jump_start()
        beginning = self.center(comment["flow_id"])
        item = self.oval(comment["flow_id"])
        self.assertEqual(self.view.canvas.itemcget(item, "fill"), BG)
        self.assertEqual(self.view.canvas.itemcget(item, "outline"), FLOW_COLORS["Complete"])
        self.assertEqual([self.view.canvas.itemcget(item, "arrow")
                          for item in self.view.canvas.find_withtag("edge")], ["none"])
        self.view._progress = 0.9
        self.view._render_pulses()
        self.assertEqual(self.center(comment["flow_id"]), beginning)
        x = round(beginning[0] - self.view.canvas.canvasx(0))
        y = round(beginning[1] - self.view.canvas.canvasy(0))
        self.view.canvas.event_generate("<Motion>", x=x, y=y)
        self.pump()
        self.assertIn("Inspection", self.view.hover_var.get())
        self.assertIn("A → B · 0 kg", self.view.hover_var.get())
        self.view.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        self.view.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.pump()
        self.assertEqual(self.selected, [comment["flow_id"]])
        self.assertIn("100 kg", self.view._node_hover_text("A"))
        self.assertIn("In 0 kg · Out 0 kg", self.view._node_hover_text("A"))
        self.view.next()
        self.assertEqual(self.view._index, 1)
        self.assertEqual(self.view.flow_list.get_children(),
                         (self.view._groups[1][1][0]["flow_id"],))
        self.view.previous()
        self.assertEqual(self.center(comment["flow_id"]), beginning)
        self.assert_records_unchanged(before)

    def test_zero_only_playback_runs_to_end_without_moving_markers(self):
        comment = self.add_flow("Comment", "0")
        before = self.prepare()
        self.view.jump_start()
        stationary = self.center(comment["flow_id"])
        self.view.toggle_play()
        self.assertTrue(self.view._playing)
        self.view.pause()
        self.view._playing = True
        with patch("flowledger.experimental.time.monotonic",
                   return_value=self.view._started_at + 2):
            self.view._tick()
        self.assertFalse(self.view._playing)
        self.assertEqual(self.view._progress, 1)
        self.assertEqual(self.center(comment["flow_id"]), stationary)
        self.assert_records_unchanged(before)


if __name__ == "__main__":
    unittest.main()
