"""Calendar behavior, date precision, and popup lifecycle on a real Tk display."""
from datetime import date
import os
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class CalendarEntryTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        from flowledger.calendar_widget import CalendarEntry
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.root.geometry("680x460+120+100")
        self.variable = tk.StringVar(self.root, value="2026-10-03")
        self.selected = []
        self.calendar = CalendarEntry(self.root, textvariable=self.variable,
                                     on_select=lambda: self.selected.append(self.variable.get()))
        self.calendar.pack(anchor="w", padx=20, pady=20)
        self.outside = ttk.Entry(self.root)
        self.outside.pack()
        self.root.update()
        self.calendar.entry.focus_force()
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def open(self, value=None):
        if value is not None:
            self.variable.set(value)
        self.calendar.open_popup()
        self.root.update()
        self.assertIsNotNone(self.calendar.popup)
        return self.calendar.popup

    def test_leap_day_month_navigation_and_padded_selection(self):
        self.open("2024-1-31")
        self.calendar.next_button.invoke()
        self.root.update()
        self.assertEqual((self.calendar._year, self.calendar._month), (2024, 2))
        self.assertEqual(set(self.calendar.day_buttons), set(range(1, 30)))
        self.calendar.day_buttons[29].invoke()
        self.root.update()
        self.assertEqual(self.variable.get(), "2024-02-29")
        self.assertEqual(self.selected, ["2024-02-29"])
        self.assertIsNone(self.calendar.popup)
        self.assertIs(self.root.focus_get(), self.calendar.entry)
        self.open("2025-2-1")
        self.assertEqual(set(self.calendar.day_buttons), set(range(1, 29)))

    def test_year_boundaries_and_calendar_limits(self):
        self.open("2026-12-31")
        self.calendar.next_button.invoke()
        self.assertEqual((self.calendar._year, self.calendar._month), (2027, 1))
        self.calendar.previous_button.invoke()
        self.assertEqual((self.calendar._year, self.calendar._month), (2026, 12))
        self.calendar.close_popup()
        self.open("0001-01-01")
        self.assertTrue(self.calendar.previous_button.instate(("disabled",)))
        self.calendar._change_month(-1)
        self.assertEqual((self.calendar._year, self.calendar._month), (1, 1))
        self.calendar.close_popup()
        self.open("9999-12-31")
        self.assertTrue(self.calendar.next_button.instate(("disabled",)))
        self.calendar._change_month(1)
        self.assertEqual((self.calendar._year, self.calendar._month), (9999, 12))

    def test_timestamp_seconds_and_explicit_midnight_are_preserved(self):
        for initial, expected in (("2026-9-3 7:8:9", "2026-09-05 07:08:09"),
                                  ("2026-09-03T00:00:00", "2026-09-05 00:00:00"),
                                  ("2026-09-03 07:08", "2026-09-05 07:08:00")):
            with self.subTest(initial=initial):
                self.open(initial)
                self.calendar.day_buttons[5].invoke()
                self.root.update()
                self.assertEqual(self.variable.get(), expected)

    def test_invalid_or_empty_input_is_untouched_until_selection(self):
        for value in ("", "not a date", "2026-02-30", "2026-10-03 27:00"):
            with self.subTest(value=value):
                self.open(value)
                self.assertEqual((self.calendar._year, self.calendar._month),
                                 (date.today().year, date.today().month))
                self.calendar.day_buttons[1].event_generate("<Escape>")
                self.root.update()
                self.assertIsNone(self.calendar.popup)
                self.assertEqual(self.variable.get(), value)
                self.assertEqual(self.selected, [])
                self.open()
                self.calendar.day_buttons[1].invoke()
                self.root.update()
                self.assertEqual(self.variable.get(), date.today().replace(day=1).isoformat())
                self.selected.clear()

    def test_keyboard_tab_space_escape_and_page_navigation(self):
        self.calendar.button.focus_set()
        self.root.update()
        self.calendar.button.event_generate("<Down>")
        self.root.update()
        self.assertIs(self.root.focus_get(), self.calendar.day_buttons[3])
        self.calendar.day_buttons[3].event_generate("<Tab>")
        self.root.update()
        self.assertIs(self.root.focus_get(), self.calendar.day_buttons[4])
        self.calendar.day_buttons[4].event_generate("<KeyPress-space>")
        self.root.event_generate("<KeyRelease-space>")
        self.root.update()
        self.assertEqual(self.variable.get(), "2026-10-04")
        self.assertIsNone(self.calendar.popup)
        self.open("2026-01-31")
        self.calendar.day_buttons[31].event_generate("<Next>")
        self.root.update()
        self.assertEqual((self.calendar._year, self.calendar._month), (2026, 2))
        self.assertIs(self.root.focus_get(), self.calendar.day_buttons[28])
        self.calendar.day_buttons[28].event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.assertIs(self.root.focus_get(), self.calendar.entry)
        root_escapes = []
        self.root.bind("<Escape>", lambda event: root_escapes.append(True))
        self.calendar.entry.event_generate("<Escape>")
        self.root.update()
        self.assertEqual(root_escapes, [True])

    def test_popup_positions_before_map_without_a_grab_or_origin_flash(self):
        maps = []
        self.root.bind_all("<Map>", lambda event: maps.append(
            (event.widget, event.widget.winfo_rootx(), event.widget.winfo_rooty())))
        popup = self.open()
        popup_maps = [item for item in maps if item[0] is popup]
        self.assertEqual(len(popup_maps), 1)
        self.assertEqual(popup_maps[0][1:], (self.calendar.winfo_rootx(),
                         self.calendar.winfo_rooty() + self.calendar.winfo_height()))
        self.assertIsNone(self.root.grab_current())
        maps.clear()
        self.calendar.next_button.invoke()
        self.root.update()
        self.assertEqual(maps, [])
        last = self.calendar._cells[-1]
        self.assertLessEqual(last.winfo_rooty() + last.winfo_height(),
                             popup.winfo_rooty() + popup.winfo_height())

    def test_outside_click_focus_change_unmap_and_destroy_close_cleanly(self):
        self.open()
        self.outside.event_generate("<ButtonPress-1>", x=2, y=2)
        self.outside.event_generate("<ButtonRelease-1>", x=2, y=2)
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.assertIs(self.root.focus_get(), self.outside)
        self.outside.event_generate("<KeyPress-x>")
        self.assertEqual(self.outside.get(), "x")
        self.open()
        self.outside.focus_set()
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.open()
        self.calendar.pack_forget()
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.calendar.pack()
        self.root.update()
        self.open()
        self.calendar._defer_focus_check()
        self.calendar.destroy()
        self.root.update()
        self.assertIsNone(self.calendar._focus_check_id)
        self.assertIsNone(self.root._flow_calendar)

    def test_other_calendar_combo_and_filter_handoff(self):
        from flowledger.calendar_widget import CalendarEntry
        from flowledger.filters import CheckFilter
        from flowledger.widgets import FilterCombo
        other = CalendarEntry(self.root)
        other.pack()
        check_filter = CheckFilter(self.root, "Categories", values=["Boat"])
        check_filter.pack()
        combo = FilterCombo(self.root, values=["Checking", "AmEx"])
        combo.pack()
        self.root.update()
        combo.open_popup(show_all=True)
        self.root.update()
        self.open()
        self.assertIsNone(combo.popup)
        other.open_popup()
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.assertIs(self.root._flow_calendar, other)
        check_filter.open_popup()
        self.root.update()
        self.assertIsNone(other.popup)
        self.open()
        self.assertIsNone(check_filter.popup)
        combo.entry.event_generate("<ButtonPress-1>", x=4, y=4)
        combo.entry.event_generate("<ButtonRelease-1>", x=4, y=4)
        self.root.update()
        self.assertIsNone(self.calendar.popup)
        self.assertIs(self.root.focus_get(), combo.entry)

    def test_disabled_entry_does_not_open_and_close_clears_active_pointer(self):
        self.calendar.state(("disabled",))
        self.calendar.open_popup()
        self.assertIsNone(self.calendar.popup)
        self.assertTrue(self.calendar.entry.instate(("disabled",)))
        self.assertTrue(self.calendar.button.instate(("disabled",)))
        self.calendar.state(("!disabled",))
        self.open()
        self.calendar.state(("disabled",))
        self.assertIsNone(self.calendar.popup)
        self.assertIsNone(self.root._flow_calendar)


if __name__ == "__main__":
    unittest.main()
