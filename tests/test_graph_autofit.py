"""Graph display follows selected resources and redraws when controls change."""

from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class GraphAutoFitTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "graph-autofit.sqlite3")
        self.repo.save_account("Checking", "1000", "2026-10-01", "USD")
        self.repo.save_account("Savings", "200", "2026-10-01", "USD")
        self.repo.save_account("Tank", "9", "2026-10-20", "litres")
        self.repo.save_flow(dict(date="2026-10-02 09:00:00", from_account="Checking",
                                 to_account="Shop", amount="100", units="USD",
                                 status="Complete", name="Supplies"))
        self.repo.save_flow(dict(date="2026-10-05 12:00:00", from_account="Checking",
                                 to_account="Shop", amount="20", units="USD",
                                 status="Pending", name="Pending supplies"))
        self.repo.save_flow(dict(date="2026-10-07", from_account="Checking",
                                 to_account="Shop", amount="30", units="USD",
                                 status="Planned", name="Planned supplies"))
        self.repo.save_flow(dict(date="2026-10-10", from_account="Checking",
                                 to_account="Shop", amount="40", units="USD",
                                 status="Canceled", name="Canceled supplies"))
        self.repo.save_flow(dict(date="2026-10-12", from_account="Savings",
                                 to_account="Shop", amount="1", units="USD",
                                 status="Canceled", name="Canceled transfer"))
        self.repo.save_flow(dict(date="2026-10-22", from_account="Tank",
                                 to_account="Workshop", amount="0.125", units="litres",
                                 status="Canceled", name="Canceled fuel use"))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, traceback):
                errors.append(f"{kind.__name__}: {error}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x950+0+0")
        self.app.show_view("charts")
        self.settle()

    def tearDown(self):
        self.app._close_popups()
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def settle(self):
        self.app.after(250, self.app.quit)
        self.app.mainloop()
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def select(self, *pairs):
        self.app.chart_accounts.selection_clear(0, "end")
        for index, pair in enumerate(self.app.chart_account_pairs):
            if pair in pairs:
                self.app.chart_accounts.selection_set(index)
        self.app.chart_accounts.event_generate("<<ListboxSelect>>")
        self.settle()

    def line(self, label):
        return next(line for line in self.app.chart_series if line.get_label() == label)

    def test_y_range_tracks_real_values_without_forcing_zero(self):
        self.select(("Checking", "USD"))
        self.assertEqual(list(self.line("Checking").get_ydata())[-1], 900)
        bottom, top = self.app.axes.get_ylim()
        self.assertAlmostEqual(bottom, 890, delta=0.1)
        self.assertAlmostEqual(top, 1010, delta=0.1)
        self.assertGreater(bottom, 0)

    def test_tiny_constant_balances_stay_on_their_own_side_of_zero(self):
        for name, amount in (("Tiny positive", "0.00001"),
                             ("Tiny negative", "-0.00001")):
            with self.subTest(name=name):
                self.repo.save_account(name, amount, "2026-10-01", "kg")
                self.repo.save_flow(dict(date="2026-10-02", from_account=name,
                                         to_account="Workshop", amount="0.00001", units="kg",
                                         status="Canceled", name="Canceled sample"))
                self.app.refresh_all()
                self.select((name, "kg"))
                bottom, top = self.app.axes.get_ylim()
                self.app.canvas.draw()
                tick_texts = [tick.get_text().replace("−", "-").replace(",", "")
                              for tick in self.app.axes.get_yticklabels() if tick.get_text().strip()]
                self.assertTrue(tick_texts)
                self.assertTrue(any(float(text) > 0 if name == "Tiny positive" else float(text) < 0
                                    for text in tick_texts), tick_texts)
                if name == "Tiny positive":
                    self.assertGreater(bottom, 0)
                    self.assertGreater(top, bottom)
                else:
                    self.assertLess(top, 0)
                    self.assertLess(bottom, top)

    def test_auto_dates_follow_selected_account_and_basis_without_canceled_flows(self):
        from matplotlib.dates import num2date

        self.select(("Checking", "USD"))
        self.assertEqual(self.app.chart_start.get()[:10], "2026-10-01")
        self.assertEqual(self.app.chart_end.get()[:10], "2026-10-02")
        self.app.chart_basis.set("Including Pending")
        self.settle()
        self.assertEqual(self.app.chart_end.get()[:10], "2026-10-05")
        self.assertEqual(list(self.line("Checking").get_ydata())[-1], 880)
        self.app.chart_basis.set("Including Planned")
        self.settle()
        self.assertEqual(self.app.chart_end.get()[:10], "2026-10-07")
        self.assertEqual(list(self.line("Checking").get_ydata())[-1], 850)
        self.assertEqual(num2date(self.app.axes.get_xlim()[1]).date().isoformat(), "2026-10-07")
        self.select(("Tank", "litres"))
        self.assertEqual(self.app.chart_start.get()[:10], "2026-10-20")
        self.assertEqual(self.app.chart_end.get()[:10], "2026-10-20")
        self.assertEqual(list(self.line("Tank").get_ydata())[-1], 9)

    def test_manual_dates_remain_manual_until_fit_dates_and_records_redraw(self):
        from matplotlib.dates import num2date

        self.select(("Checking", "USD"))
        self.app.chart_start.set("2026-09-30")
        self.app.chart_end.set("2026-10-31")
        self.settle()
        self.assertEqual(self.app.chart_series[self.line("Checking")][-1][0][:10], "2026-10-31")
        self.assertEqual(num2date(self.app.axes.get_xlim()[1]).date().isoformat(), "2026-10-31")
        self.app.chart_basis.set("Including Pending")
        self.settle()
        self.assertEqual((self.app.chart_start.get(), self.app.chart_end.get()),
                         ("2026-09-30", "2026-10-31"))
        self.repo.save_flow(dict(date="2026-10-06", from_account="Checking",
                                 to_account="Shop", amount="5", units="USD",
                                 status="Complete", name="Additional supplies"))
        self.app.refresh_all()
        self.settle()
        self.assertEqual(list(self.line("Checking").get_ydata())[-1], 875)
        self.assertEqual(self.app.chart_end.get(), "2026-10-31")
        self.app.chart_fit_dates_button.invoke()
        self.settle()
        self.assertEqual((self.app.chart_start.get()[:10], self.app.chart_end.get()[:10]),
                         ("2026-10-01", "2026-10-06"))

    def test_combined_legend_names_accounts_and_keeps_units_separate(self):
        self.select(("Checking", "USD"), ("Savings", "USD"), ("Tank", "litres"))
        self.app.chart_combined.set(True)
        self.settle()
        self.assertEqual(set(self.app.chart_axes), {"USD", "litres"})
        usd = [text.get_text() for text in self.app.chart_axes["USD"].get_legend().get_texts()]
        litres = [text.get_text() for text in self.app.chart_axes["litres"].get_legend().get_texts()]
        self.assertTrue(any("Checking" in label and "Savings" in label for label in usd))
        self.assertTrue(any("Tank" in label for label in litres))
        self.assertEqual(list(self.line("Combined").get_ydata())[-1], 1100)
        self.app.canvas.draw()
        renderer = self.app.canvas.get_renderer()
        for axes in self.app.chart_axes.values():
            self.assertGreaterEqual(axes.get_legend().get_window_extent(renderer).y0,
                                    axes.bbox.y1 - 1, "legend should not cover plotted balances")

    def test_ticks_distinguish_small_change_on_large_quantity(self):
        self.repo.save_account("Precision", "12.123456789", "2026-10-01", "kg")
        self.repo.save_flow(dict(date="2026-10-02", from_account="Precision",
                                 to_account="Workshop", amount="0.000000001", units="kg",
                                 status="Complete", name="Small sample"))
        self.app.refresh_all()
        self.select(("Precision", "kg"))
        self.app.canvas.draw()
        labels = [tick.get_text() for tick in self.app.axes.get_yticklabels() if tick.get_text().strip()]
        self.assertGreater(len(set(labels)), 1, labels)
        self.assertEqual(list(self.line("Precision").get_ydata())[-1], 12.123456788)

    def test_wheel_zooms_both_directions_and_toolbar_home_restores_range(self):
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.dates import date2num

        self.select(("Checking", "USD"))
        self.app.canvas.draw()
        original = self.app.axes.get_xlim()
        x, y = self.app.axes.transData.transform((date2num(datetime(2026, 10, 1, 12)), 950))
        for button in ("up", "down"):
            event = MouseEvent("scroll_event", self.app.canvas, x, y,
                               button=button, step=1)
            self.app.canvas.callbacks.process("scroll_event", event)
            self.settle()
            if button == "up":
                self.assertLess(self.app.axes.get_xlim()[1] - self.app.axes.get_xlim()[0],
                                original[1] - original[0])
            else:
                self.assertGreater(self.app.axes.get_xlim()[1] - self.app.axes.get_xlim()[0],
                                   original[1] - original[0] - 0.00001)
        self.app.chart_toolbar.home()
        self.settle()
        restored = self.app.axes.get_xlim()
        for observed, expected in zip(restored, original):
            self.assertAlmostEqual(observed, expected, places=5)


if __name__ == "__main__":
    unittest.main()
