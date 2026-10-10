"""Graph presentation checks using a real Tk canvas and exact saved quantities."""
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class GraphUiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "graph.sqlite3")
        self.repo.save_account("Checking", '1000', "2026-10-01")
        self.repo.save_account("Savings", '500', "2026-10-01")
        self.repo.save_account("Unknown", None, "2026-10-01")
        self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Savings",
                                 to_account="Unknown", amount="1", status="Canceled"))
        self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Checking", to_account="Shop",
                                 amount="12.00", status="Complete", name="Lunch"))
        self.errors = []
        errors = self.errors
        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, traceback):
                errors.append(str(error))
        self.app = CheckedApp(self.repo)
        self.app.geometry("1500x950+0+0")
        self.app.show_view("charts")
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("2026-10-03")
        self.select("Checking")

    def tearDown(self):
        self.app._close_popups()
        self.app._hide_chart_tooltip()
        self.pump()
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()

    def select(self, *names):
        self.app.chart_accounts.selection_clear(0, "end")
        for index in range(self.app.chart_accounts.size()):
            if self.app.chart_account_pairs[index][0] in names:
                self.app.chart_accounts.selection_set(index)
        self.app.draw_chart()
        self.pump()
        self.app.canvas.draw()

    def line(self, name="Checking"):
        return next(line for line in self.app.chart_series if line.get_label() == name)

    def hover(self, at, amount):
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.dates import date2num
        x, y = self.app.axes.transData.transform((date2num(datetime.fromisoformat(at)), amount))
        event = MouseEvent("motion_notify_event", self.app.canvas, x, y)
        self.app.canvas.callbacks.process("motion_notify_event", event)
        self.pump()
        return self.app.chart_tooltip.get_text()

    def test_midnight_start_keeps_marker_and_both_exact_values(self):
        from matplotlib.dates import date2num
        line = self.line()
        self.assertEqual(self.app.chart_series[line][:2],
                         [("2026-10-01 00:00:00", 1000), ("2026-10-01 00:00:00", 988)])
        self.assertEqual(list(line.get_ydata()[:2]), [1000, 988])
        self.assertEqual(line.get_markevery(), [0])
        self.assertLess(self.app.axes.get_xlim()[0], date2num(datetime(2026, 10, 1)))
        self.assertIn('1,000', self.hover("2026-10-01", 1000))

    def test_hover_horizontal_vertical_and_leave_reports_exact_quantities(self):
        self.assertIn('Checking: 988 USD', self.hover("2026-10-01 12:00:00", 988))
        self.assertTrue(self.app.chart_tooltip.get_visible())
        text = self.hover("2026-10-01", 994)
        self.assertIn('1,000 USD → 988 USD', text)
        self.assertNotIn('994', text)
        self.app._hide_chart_tooltip()
        self.assertFalse(self.app.chart_tooltip.get_visible())

    def test_combined_keeps_start_transition_and_hover(self):
        self.app.chart_combined.set(True)
        self.select("Checking", "Savings")
        self.assertEqual(self.app.chart_series[self.line("Combined")][0][1], 1500)
        self.assertIn('Combined: 1,488 USD', self.hover("2026-10-02", 1488))

    def test_missing_balance_is_visible_for_single_partial_and_combined(self):
        self.select("Unknown")
        self.assertIn("Unknown", self.app.chart_message.get())
        self.assertTrue(self.app.chart_message_label.winfo_ismapped())
        self.assertFalse(self.app.chart_series)
        self.assertTrue(any("Starting balance required" in text.get_text() for text in self.app.axes.texts))
        self.select("Checking", "Unknown")
        self.assertEqual([line.get_label() for line in self.app.chart_series], ["Checking"])
        self.assertIn("Unknown", self.app.chart_message.get())
        self.app.chart_combined.set(True)
        self.select("Checking", "Unknown")
        self.assertFalse(self.app.chart_series)
        self.assertTrue(any("Combined balance is unknown" in text.get_text() for text in self.app.axes.texts))

    def test_unpadded_dates_normalize_and_through_keeps_whole_day(self):
        self.repo.save_flow(dict(units="USD", date="2026-10-03 23:59:59", from_account="Checking", to_account="Shop",
                                 amount="0.01", status="Complete", name="Late"))
        self.app.chart_start.set("2026-10-1")
        self.app.chart_end.set("2026-10-3")
        self.select("Checking")
        self.assertEqual(self.app.chart_start.get(), "2026-10-01")
        self.assertEqual(self.app.chart_end.get(), "2026-10-03")
        self.assertEqual(self.app.chart_series[self.line()][-1], ("2026-10-03 23:59:59", Decimal("987.99")))

    def test_invalid_input_clears_old_plot_tooltip_and_inspection(self):
        self.hover("2026-10-02", 988)
        self.app.chart_start.set("2026-2-31")
        self.app.draw_chart()
        self.pump()
        self.assertIn("From:", self.app.chart_message.get())
        self.assertIn("2026-2-31", self.app.chart_message.get())
        self.assertEqual(self.app.chart_series, {})
        self.assertIsNone(self.app.chart_tooltip)
        self.assertIsNone(self.app.chart_inspection_context)
        self.assertFalse(self.app.axes.lines)
        self.app.chart_start.set("2026-10-01")
        self.app.chart_end.set("nonsense")
        self.app.draw_chart()
        self.assertIn("Through:", self.app.chart_message.get())

    def test_hidden_lines_and_redraw_remove_old_tooltip(self):
        self.hover("2026-10-02", 988)
        legend = next(key for key, line in self.app.chart_line_map.items() if line is self.line())
        self.app.toggle_chart_line(SimpleNamespace(artist=legend))
        self.hover("2026-10-02", 988)
        self.assertFalse(self.app.chart_tooltip.get_visible())
        old = self.app.chart_tooltip
        self.app.draw_chart()
        self.assertIsNot(old, self.app.chart_tooltip)
        self.assertFalse(self.app.chart_tooltip.get_visible())

    def test_hover_survives_zoom_and_suppresses_during_pan(self):
        from matplotlib.dates import date2num
        self.app.axes.set_xlim(date2num(datetime(2026, 10, 1, 12)), date2num(datetime(2026, 10, 2, 12)))
        self.app.canvas.draw()
        self.assertIn('988', self.hover("2026-10-02", 988))
        self.app.chart_toolbar.pan()
        self.hover("2026-10-02", 988)
        self.assertFalse(self.app.chart_tooltip.get_visible())
        self.app.chart_toolbar.pan()

    def test_single_instant_and_negative_balance_hover(self):
        self.repo.save_account("Debt", '-123.45', "2026-10-01")
        self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Checking",
                                 to_account="Debt", amount="1", status="Canceled"))
        self.app.refresh_chart_accounts()
        self.app.chart_start.set("2026-10-01 12:00:00")
        self.app.chart_end.set("2026-10-01 12:00:00")
        self.select("Debt")
        self.assertEqual(len(self.app.chart_series[self.line("Debt")]), 1)
        self.assertIn('Debt: -123.45 USD', self.hover("2026-10-01 12:00:00", -123.45))

    def test_overlapping_accounts_both_report_exact_value(self):
        self.repo.save_account("Twin", '988', "2026-10-01")
        self.repo.save_flow(dict(units="USD", date="2026-10-01", from_account="Checking",
                                 to_account="Twin", amount="1", status="Canceled"))
        self.app.refresh_chart_accounts()
        self.select("Checking", "Twin")
        text = self.hover("2026-10-02", 988)
        self.assertIn('Checking: 988 USD', text)
        self.assertIn('Twin: 988 USD', text)

    def test_export_excludes_hover_annotation(self):
        self.hover("2026-10-02", 988)
        exported = []
        with patch("flowledger.gui.filedialog.asksaveasfilename", return_value="plot.png"), \
                patch.object(self.app.figure, "savefig", side_effect=lambda *a, **k: exported.append(self.app.chart_tooltip.get_visible())):
            self.app.export_chart()
        self.assertEqual(exported, [False])


if __name__ == "__main__":
    unittest.main()
