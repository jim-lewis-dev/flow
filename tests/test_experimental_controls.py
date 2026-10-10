"""GUI transport, playback speed, and session-history checks for Experimental."""

import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ExperimentalControlsTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "transport.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01", units="USD")
        for name, when, status in (
            ("First", "2026-10-02", "Complete"),
            ("Second", "2026-10-02 12:00:00", "Pending"),
            ("Third", "2026-10-03 08:00:00", "Planned"),
        ):
            self.repo.save_flow(dict(
                date=when, from_account="Checking", to_account="Wallet",
                amount="1.125", units="USD", status=status, name=name,
                category="Testing", tags=[], notes=""))
        self.callback_errors = []
        callback_errors = self.callback_errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, trace):
                callback_errors.append(f"{kind.__name__}: {error}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1280x800+0+0")
        self.app.show_view("experimental")
        self.view = self.app.experimental
        self.view.basis_combo.current(2)
        self.view.basis_combo.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(len(self.view._groups), 3)

    def tearDown(self):
        try:
            if self.app.winfo_exists():
                self.view.close()
                self.app._close_popups()
                self.app.history.close()
                self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.callback_errors, [])

    def database(self):
        return tuple(self.repo.list_flows()), tuple(self.repo.conn.iterdump())

    def test_five_transport_controls_navigate_and_cancel_pending_tick(self):
        view = self.view
        database = self.database()
        self.assertEqual(view._index, -1)
        view.end_button.invoke()
        self.pump()
        self.assertEqual((view._index, view._progress), (2, 1.0))
        self.assertEqual(view.flow_list.item(view.flow_list.get_children()[0], "values")[4], "Third")
        view.previous_button.invoke()
        self.pump()
        self.assertEqual(view._index, 1)
        view.start_button.invoke()
        self.pump()
        self.assertEqual((view._index, view._progress), (0, 0.0))
        view.next_button.invoke()
        self.pump()
        self.assertEqual(view._index, 1)
        view.previous_button.invoke()
        view.previous_button.invoke()
        self.pump()
        self.assertEqual(view._index, -1)
        self.assertEqual(view.flow_list.get_children(), ())

        view.play_button.invoke()
        self.pump()
        self.assertTrue(view._playing)
        self.assertIsNotNone(view._after_id)
        view.end_button.invoke()
        self.pump()
        self.assertEqual((view._index, view._progress), (2, 1.0))
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertEqual(database, self.database())

    def test_zero_pauses_five_plays_faster_and_slider_undoes_as_one_change(self):
        view = self.view
        database = self.database()
        # A drag can traverse several values but enters history once on release.
        before = len(self.app.history._undo)
        scale = view.speed_scale
        scale.event_generate("<ButtonPress-1>", x=22, y=scale.winfo_height() // 2)
        scale.set(2)
        scale.set(4)
        scale.set(5)
        scale.event_generate("<ButtonRelease-1>", x=scale.winfo_width() - 22,
                             y=scale.winfo_height() // 2)
        self.pump()
        self.assertEqual(view.speed_var.get(), 5)
        self.assertEqual(len(self.app.history._undo), before + 1)
        self.app.undo()
        self.pump()
        self.assertEqual(view.speed_var.get(), 1)
        self.app.redo()
        self.pump()
        self.assertEqual(view.speed_var.get(), 5)

        scale.set(1)
        self.pump()
        self.assertEqual(view._duration(), 1000)
        view.play_button.invoke()
        self.pump()
        self.assertTrue(view._playing)
        scale.set(0)
        self.pump()
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertEqual(str(view.play_button["state"]), "disabled")
        scale.set(5)
        self.pump()
        self.assertEqual(view._duration(), 200)
        view.play_button.invoke()
        self.assertTrue(view._playing)
        self.app.after(800, self.app.quit)
        self.app.mainloop()
        self.pump()
        self.assertEqual(view._index, 2)
        self.assertFalse(view._playing)
        self.assertIsNone(view._after_id)
        self.assertEqual(database, self.database())

    def test_keyboard_speed_change_is_one_undo_action(self):
        view = self.view
        database = self.database()
        before = len(self.app.history._undo)
        view.speed_scale.focus_force()
        self.pump()
        view.speed_scale.event_generate("<KeyPress-Right>")
        view.speed_scale.event_generate("<KeyRelease-Right>")
        self.pump()
        self.assertEqual(view.speed_var.get(), 2)
        self.assertEqual(len(self.app.history._undo), before + 1)
        self.app.undo()
        self.pump()
        self.assertEqual(view.speed_var.get(), 1)
        self.app.redo()
        self.pump()
        self.assertEqual(view.speed_var.get(), 2)
        self.assertEqual(database, self.database())


if __name__ == "__main__":
    unittest.main()
