"""Real Tk checks for searchable multiple-choice ledger filters."""
import os
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class CheckFilterTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        from flowledger.filters import CheckFilter
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.root.geometry("650x420+90+80")
        self.changes = []
        self.filter = CheckFilter(self.root, "Categories",
            values=["Dining", "Boat", "Groceries"],
            on_change=lambda: self.changes.append(self.filter.get()))
        self.filter.pack(anchor="w", padx=20, pady=20)
        self.outside = ttk.Entry(self.root)
        self.outside.pack()
        self.root.update()
        self.filter.button.focus_force()
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def open(self):
        self.filter.open_popup()
        self.root.update()
        self.assertIsNotNone(self.filter.popup)
        return self.filter.popup

    def test_explicit_selection_callbacks_caption_and_all(self):
        self.assertEqual(self.filter.get(), ())
        self.assertEqual(self.filter.button.cget("text"), "Categories: All ▾")
        popup = self.open()
        self.filter.checkbuttons["Boat"].invoke()
        self.filter.checkbuttons["Dining"].invoke()
        self.root.update()
        self.assertIs(self.filter.popup, popup)
        self.assertEqual(self.changes, [("Boat",), ("Boat", "Dining")])
        self.assertEqual(self.filter.get(), ("Boat", "Dining"))
        self.assertEqual(self.filter.button.cget("text"), "Categories · 2 ▾")
        self.assertFalse(self.filter._all_variable.get())
        self.filter.all_button.invoke()
        self.assertEqual(self.filter.get(), ())
        self.assertTrue(self.filter._all_variable.get())
        self.assertEqual(self.changes[-1], ())
        self.filter.all_button.invoke()
        self.assertEqual(len(self.changes), 3)
        self.assertTrue(self.filter._all_variable.get())

    def test_search_preserves_hidden_selection_and_geometry(self):
        popup = self.open()
        self.filter.checkbuttons["Dining"].invoke()
        geometry = popup.geometry()
        self.filter.search_variable.set("BO")
        self.root.update()
        self.assertEqual(list(self.filter.checkbuttons), ["Boat"])
        self.filter.checkbuttons["Boat"].invoke()
        self.assertEqual(self.filter.get(), ("Dining", "Boat"))
        self.filter.search_variable.set("no matches")
        self.root.update()
        self.assertEqual(self.filter.checkbuttons, {})
        self.assertEqual(self.filter.get(), ("Dining", "Boat"))
        self.filter.search_variable.set("")
        self.root.update()
        self.assertTrue(self.filter._choice_variables["Dining"].get())
        self.assertTrue(self.filter._choice_variables["Boat"].get())
        self.assertEqual(popup.geometry(), geometry)

    def test_refresh_keeps_missing_selected_values_until_explicit_clear(self):
        self.filter.set_selected(["Dining", "Dining"])
        self.filter.set_values(["Boat", "Boat", "Books"])
        self.assertEqual(self.filter.get(), ("Dining",))
        self.assertEqual(self.filter.values, ["Boat", "Books"])
        self.open()
        self.assertEqual(list(self.filter.checkbuttons), ["Boat", "Books", "Dining"])
        self.assertTrue(self.filter._choice_variables["Dining"].get())
        self.filter.set_values(["Music"])
        self.root.update()
        self.assertEqual(list(self.filter.checkbuttons), ["Music", "Dining"])
        self.assertEqual(self.changes, [])
        self.filter.all_button.invoke()
        self.root.update()
        self.assertEqual(list(self.filter.checkbuttons), ["Music"])
        self.assertEqual(self.filter.get(), ())

    def test_programmatic_selection_is_silent_unless_requested(self):
        self.filter.set_selected(["Boat"])
        self.assertEqual(self.changes, [])
        self.assertEqual(self.filter.button.cget("text"), "Categories: Boat ▾")
        self.filter.set_selected(["Boat"], notify=True)
        self.assertEqual(self.changes, [])
        self.filter.set_selected(["A category whose full name is much longer than the button"], notify=True)
        self.assertEqual(len(self.changes), 1)
        self.assertLessEqual(len(self.filter.button.cget("text")), 27)
        self.assertIn("…", self.filter.button.cget("text"))

    def test_popup_is_positioned_before_mapping_and_checking_does_not_remap(self):
        maps = []
        self.root.bind_all("<Map>", lambda event: maps.append(
            (event.widget, event.widget.winfo_rootx(), event.widget.winfo_rooty())))
        popup = self.open()
        popup_maps = [item for item in maps if item[0] is popup]
        self.assertEqual(len(popup_maps), 1)
        self.assertEqual(popup_maps[0][1:], (self.filter.winfo_rootx(),
                         self.filter.winfo_rooty() + self.filter.winfo_height()))
        maps.clear()
        self.filter.checkbuttons["Boat"].invoke()
        self.filter.checkbuttons["Dining"].invoke()
        self.root.update()
        self.assertEqual(maps, [])
        self.assertIsNone(self.root.grab_current())

    def test_keyboard_tab_space_and_escape(self):
        self.filter.button.event_generate("<Down>")
        self.root.update()
        self.assertIs(self.root.focus_get(), self.filter.search_entry)
        self.filter.search_entry.event_generate("<Tab>")
        self.root.update()
        self.assertIs(self.root.focus_get(), self.filter.all_button)
        self.filter.all_button.event_generate("<Tab>")
        self.root.update()
        first = self.filter.checkbuttons["Dining"]
        self.assertIs(self.root.focus_get(), first)
        first.event_generate("<KeyPress-space>")
        first.event_generate("<KeyRelease-space>")
        self.root.update()
        self.assertEqual(self.filter.get(), ("Dining",))
        first.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.assertIs(self.root.focus_get(), self.filter.button)

    def test_scroll_and_keyboard_focus_reveal_long_list(self):
        self.filter.set_values([f"Category {index:03}" for index in range(120)])
        self.open()
        first_position = self.filter.canvas.yview()[0]
        self.filter.checkbuttons["Category 000"].event_generate("<Button-5>")
        self.root.update()
        self.assertGreater(self.filter.canvas.yview()[0], first_position)
        last = self.filter.checkbuttons["Category 119"]
        last.focus_set()
        self.root.update()
        self.assertGreater(self.filter.canvas.yview()[0], 0.8)
        top = self.filter.canvas.canvasy(0)
        self.assertGreaterEqual(last.winfo_y(), top - 1)
        self.assertLessEqual(last.winfo_y() + last.winfo_height(),
                             top + self.filter.canvas.winfo_height() + 1)
        last.event_generate("<KeyPress-space>")
        last.event_generate("<KeyRelease-space>")
        self.root.update()
        self.assertEqual(self.filter.get(), ("Category 119",))
        self.filter.set_values([f"Category {index:03}" for index in range(121)])
        self.root.update()
        self.assertGreater(self.filter.canvas.yview()[0], 0.8)

    def test_outside_click_focus_and_unmap_close_without_clearing(self):
        self.filter.set_selected(["Boat"])
        self.open()
        self.outside.event_generate("<ButtonPress-1>", x=2, y=2)
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.assertEqual(self.filter.get(), ("Boat",))
        self.open()
        self.outside.focus_set()
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.open()
        self.filter.pack_forget()
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.assertIsNone(getattr(self.root, "_flow_check_filter", None))

    def test_other_filter_and_entry_combo_cooperate(self):
        from flowledger.filters import CheckFilter
        from flowledger.widgets import FilterCombo
        other = CheckFilter(self.root, "Statuses", values=["Complete", "Pending"])
        other.pack()
        combo = FilterCombo(self.root, values=["Checking", "AmEx"])
        combo.pack()
        self.root.update()
        combo.open_popup(show_all=True)
        self.root.update()
        self.assertIsNotNone(combo.popup)
        self.open()
        self.assertIsNone(combo.popup)
        other.open_popup()
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.assertIsNotNone(other.popup)
        self.assertIs(self.root._flow_check_filter, other)

    def test_disabled_controls_and_destroy_cancel_pending_focus_work(self):
        self.filter.state(["disabled"])
        self.filter.open_popup()
        self.assertIsNone(self.filter.popup)
        self.assertTrue(self.filter.button.instate(["disabled"]))
        self.filter.state(["!disabled"])
        self.open()
        self.filter.button.state(["disabled"])
        self.filter.checkbuttons["Boat"].invoke()
        self.assertEqual(self.filter.get(), ())
        self.assertFalse(self.filter._choice_variables["Boat"].get())
        self.filter._defer_focus_check()
        self.filter.destroy()
        self.root.update()
        self.assertIsNone(self.filter._focus_check_id)
        self.assertIsNone(self.root._flow_check_filter)

    def test_entry_handoff_then_new_filter_keeps_keyboard_focus(self):
        from flowledger.filters import CheckFilter
        from flowledger.widgets import FilterCombo
        other = CheckFilter(self.root, "Tags", values=["Trip", "Repair"])
        other.pack()
        combo = FilterCombo(self.root, values=["Checking", "AmEx"])
        combo.pack()
        self.root.update()
        self.open()
        self.assertIs(self.root.focus_get(), self.filter.search_entry)
        combo.entry.event_generate("<ButtonPress-1>", x=4, y=4)
        combo.entry.event_generate("<ButtonRelease-1>", x=4, y=4)
        self.root.update()
        self.assertIsNone(self.filter.popup)
        self.assertIsNotNone(combo.popup)
        self.assertIs(self.root.focus_get(), combo.entry)
        other.button.event_generate("<ButtonPress-1>", x=4, y=4)
        other.button.event_generate("<ButtonRelease-1>", x=4, y=4)
        self.root.update()
        self.assertIsNone(combo.popup)
        self.assertIs(self.root.focus_get(), other.search_entry)
        other.search_entry.event_generate("<Escape>")
        self.root.update()
        self.assertIsNone(other.popup)
        self.assertIs(self.root.focus_get(), other.button)

    def test_hover_lists_all_choices_and_updates_with_restored_selection(self):
        names = ("Boat", "Dining", "A category whose full name is longer than the button")
        self.filter.set_selected(names)
        self.assertEqual(self.filter.tooltip_text(), "\n".join(names))
        maps = []
        self.root.bind_all("<Map>", lambda event: maps.append(
            (event.widget, event.widget.winfo_rootx(), event.widget.winfo_rooty())))
        self.filter.button.event_generate("<Enter>", x=5, y=5)
        self.root.after(500, self.root.quit)
        self.root.mainloop()
        popup = self.filter._tooltip_popup
        self.assertIsNotNone(popup)
        self.assertEqual(self.filter._tooltip_label.cget("text"), "\n".join(names))
        self.assertIs(self.root.focus_get(), self.filter.button)
        popup_maps = [item for item in maps if item[0] is popup]
        self.assertEqual(len(popup_maps), 1)
        self.assertEqual(popup_maps[0][1:], self.filter._tooltip_position)
        # Undo/redo restores filters through silent set_selected calls. A
        # visible tooltip must read that restored state without stale names.
        self.filter.set_selected(("Dining",))
        self.root.update()
        self.assertIs(self.filter._tooltip_popup, popup)
        self.assertEqual(self.filter._tooltip_label.cget("text"), "Dining")
        self.filter.set_selected(names)
        self.root.update()
        self.assertEqual(self.filter._tooltip_label.cget("text"), "\n".join(names))
        self.filter.set_selected(())
        self.root.update()
        self.assertEqual(self.filter._tooltip_label.cget("text"), "All")
        self.assertEqual(self.changes, [])
        self.filter.button.event_generate("<Leave>")
        self.root.update()
        self.assertIsNone(self.filter._tooltip_popup)

    def test_tooltip_is_dismissed_on_open_and_pending_work_on_unmap_or_destroy(self):
        self.filter.button.event_generate("<Enter>", x=5, y=5)
        self.assertIsNotNone(self.filter._tooltip_after_id)
        self.open()
        self.assertIsNone(self.filter._tooltip_after_id)
        self.assertIsNone(self.filter._tooltip_popup)
        self.filter.close_popup()
        self.filter.button.event_generate("<Enter>", x=5, y=5)
        self.filter.pack_forget()
        self.root.update()
        self.assertIsNone(self.filter._tooltip_after_id)
        self.filter.pack()
        self.root.update()
        self.filter.button.event_generate("<Enter>", x=5, y=5)
        self.assertIsNotNone(self.filter._tooltip_after_id)
        self.filter.destroy()
        self.root.update()
        self.assertIsNone(self.filter._tooltip_after_id)

    def test_units_keep_case_sensitive_choices_and_full_tooltip_names(self):
        from flowledger.filters import CheckFilter
        units = CheckFilter(self.root, "Units", values=("mW", "MW", "mW"))
        units.pack()
        self.root.update()
        self.assertEqual(units.button.cget("text"), "Units: All ▾")
        units.open_popup()
        units.search_variable.set("mw")
        self.root.update()
        self.assertEqual(list(units.checkbuttons), ["mW", "MW"])
        units.checkbuttons["mW"].invoke()
        self.assertEqual(units.get(), ("mW",))
        units.checkbuttons["MW"].invoke()
        self.assertEqual(units.get(), ("mW", "MW"))
        self.assertEqual(units.tooltip_text(), "mW\nMW")

    def test_empty_captions_are_consistent_and_custom_all_label_is_retained(self):
        from flowledger.filters import CheckFilter
        for label in ("From", "To", "Status", "Categories", "Tags", "Units"):
            choice = CheckFilter(self.root, label)
            self.assertEqual(choice.button.cget("text"), f"{label}: All ▾")
            self.assertEqual(choice.tooltip_text(), "All")
            choice.destroy()
        custom = CheckFilter(self.root, "From", all_label="From: All")
        self.assertEqual(custom.button.cget("text"), "From: All ▾")


if __name__ == "__main__":
    unittest.main()
