"""Real Tk event tests. Skipped unless a desktop/headless DISPLAY is available."""
import os
import unittest


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class DropdownTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from flowledger.widgets import FilterCombo
        self.root = tk.Tk()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.root.geometry("650x350+0+0")
        self.combo = FilterCombo(self.root, values=["Dining", "Boat", "Groceries"])
        self.combo.pack(fill="x", padx=20, pady=20)
        self.root.update()
        self.combo.entry.focus_force()
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.callback_errors, [])

    def type_value(self, text):
        self.combo.entry.delete(0, "end")
        self.combo.entry.insert(0, text)
        self.combo.entry.event_generate("<KeyRelease>", keysym="a")
        self.root.update()

    def enable_removal(self, callback):
        self.combo.close_popup()
        self.combo.on_remove = callback
        self.combo.open_popup(show_all=True)
        self.root.update()

    def click_remove(self, index):
        _, y, _, height = self.combo.listbox.bbox(index)
        canvas = self.combo.remove_canvas
        x, y = canvas.winfo_width() // 2, y + height // 2
        canvas.event_generate("<ButtonPress-1>", x=x, y=y)
        canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.root.update()

    def test_focus_opens_typing_filters_and_keyboard_selects(self):
        self.assertIsNotNone(self.combo.popup)
        self.assertEqual(self.combo.matches, ["Dining", "Boat", "Groceries"])
        self.type_value("bo")
        self.assertEqual(self.combo.matches, ["Boat"])
        self.combo.entry.event_generate("<Down>")
        self.combo.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.combo.get(), "Boat")
        self.assertIsNone(self.combo.popup)

    def test_enter_preserves_new_value_without_learning_unsaved_text(self):
        self.type_value("Boat maintenance")
        self.assertEqual(self.combo.matches, [])
        self.combo.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.combo.get(), "Boat maintenance")
        self.assertNotIn("Boat maintenance", self.combo.values)

    def test_native_status_popup_closes_suggestions_without_callback_errors(self):
        from tkinter import ttk

        status = ttk.Combobox(self.root, values=("Complete", "Pending"), state="readonly")
        status.set("Complete")
        status.pack(fill="x", padx=20)
        self.root.update()
        self.assertIsNotNone(self.combo.popup)
        x, y = status.winfo_width() - 8, status.winfo_height() // 2
        status.event_generate("<ButtonPress-1>", x=x, y=y)
        status.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.root.update()
        focused = str(self.root.tk.call("focus"))
        self.assertIn("popdown", focused)
        # A deferred FocusOut check can run after ttk has moved focus into
        # its Tcl-only listbox, even when the old suggestions already closed.
        self.combo._defer_focus_check()
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.root.tk.call("event", "generate", focused, "<Down>")
        self.root.tk.call("event", "generate", focused, "<Return>")
        self.root.update()
        self.assertEqual(status.get(), "Pending")
        self.assertIsNone(self.combo.popup)
        self.assertEqual(self.callback_errors, [])

    def test_remove_target_keeps_draft_focus_and_popup_without_accepting(self):
        removed, accepted, maps = [], [], []
        self.combo.on_accept = lambda: accepted.append(True)
        self.enable_removal(lambda value: removed.append(value) or True)
        self.combo.set("Untouched draft")
        self.combo.entry.icursor(4)
        popup = self.combo.popup
        position = popup.winfo_rootx(), popup.winfo_rooty()
        self.root.bind_all("<Map>", lambda event: maps.append(event.widget), add="+")
        self.click_remove(1)
        self.assertEqual(removed, ["Boat"])
        self.assertEqual(accepted, [])
        self.assertEqual(self.combo.get(), "Untouched draft")
        self.assertEqual(self.combo.entry.index("insert"), 4)
        self.assertEqual(self.combo.values, ["Dining", "Groceries"])
        self.assertEqual(self.combo.matches, ["Dining", "Groceries"])
        self.assertIs(self.combo.popup, popup)
        self.assertEqual((popup.winfo_rootx(), popup.winfo_rooty()), position)
        self.assertEqual(maps, [])
        self.assertIs(self.root.focus_get(), self.combo.entry)
        # Normal navigation remains available after the X click.
        self.combo.entry.event_generate("<Down>")
        self.combo.entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.combo.get(), "Dining")
        self.assertEqual(accepted, [True])

    def test_remove_filters_and_caller_refresh_preserve_filter_mode(self):
        self.combo.set_values(["Boat", "Books", "Dining"])
        removed = []

        def persist(value):
            removed.append(value)
            self.combo.set_values([item for item in self.combo.values if item != value])
            return True

        self.enable_removal(persist)
        self.type_value("bo")
        self.assertEqual(self.combo.matches, ["Boat", "Books"])
        self.click_remove(1)
        self.assertEqual(removed, ["Books"])
        self.assertEqual(self.combo.matches, ["Boat"])
        self.assertEqual(self.combo.get(), "bo")
        self.combo.set_values(["Boat", "Bow", "Dining"])
        self.root.update()
        self.assertEqual(self.combo.matches, ["Boat", "Bow"])

    def test_remove_and_set_values_preserve_show_all_with_nonmatching_draft(self):
        self.enable_removal(lambda value: True)
        self.combo.set("unrelated")
        self.combo.set_values(["Boat", "Books", "Dining"])
        self.root.update()
        self.assertEqual(self.combo.matches, ["Boat", "Books", "Dining"])
        self.click_remove(0)
        self.assertEqual(self.combo.matches, ["Books", "Dining"])
        self.assertEqual(self.combo.get(), "unrelated")

    def test_remove_after_scrolling_targets_visible_row_and_redraws_x(self):
        self.combo.set_values([f"Account {index:02}" for index in range(100)])
        removed = []
        self.enable_removal(lambda value: removed.append(value) or True)
        self.assertEqual(len(self.combo.matches), 80)
        self.assertEqual(int(self.combo.listbox.cget("height")), 6)
        self.combo.listbox.yview_scroll(23, "units")
        self.root.update()
        first = self.combo.listbox.nearest(0)
        index = first + 2
        value = self.combo.matches[index]
        _, y, _, height = self.combo.listbox.bbox(index)
        icon = self.combo.remove_canvas.find_withtag(f"remove_{index}")
        self.assertEqual(len(icon), 1)
        icon_x, icon_y = self.combo.remove_canvas.coords(icon[0])
        self.assertEqual(icon_y, y + height / 2)
        self.click_remove(index)
        self.assertEqual(removed, [value])
        self.assertNotIn(value, self.combo.values)
        self.assertEqual(self.combo.listbox.nearest(0), first)
        self.assertEqual(len(self.combo.matches), 80)
        self.combo.remove_canvas.event_generate("<Button-5>", x=int(icon_x), y=10)
        self.root.update()
        self.assertEqual(self.combo.listbox.nearest(0), first + 1)
        # A newly visible row still owns its matching X after gutter scrolling.
        next_index = self.combo.listbox.nearest(0) + 1
        next_value = self.combo.matches[next_index]
        self.click_remove(next_index)
        self.assertEqual(removed, [value, next_value])

    def test_refused_remove_keeps_vocabulary_selection_and_draft(self):
        removed = []
        self.enable_removal(lambda value: removed.append(value) or False)
        self.type_value("bo")
        self.combo.entry.event_generate("<Down>")
        popup = self.combo.popup
        self.click_remove(0)
        self.assertEqual(removed, ["Boat"])
        self.assertEqual(self.combo.values, ["Dining", "Boat", "Groceries"])
        self.assertEqual(self.combo.matches, ["Boat"])
        self.assertEqual(self.combo.nav_index, 0)
        self.assertEqual(self.combo.get(), "bo")
        self.assertIs(self.combo.popup, popup)
        self.assertIs(self.root.focus_get(), self.combo.entry)

    def test_no_remove_control_for_fixed_options_or_new_value_placeholder(self):
        self.assertIsNone(self.combo.remove_canvas)
        removed = []
        self.enable_removal(lambda value: removed.append(value) or True)
        self.type_value("A new account")
        self.assertEqual(self.combo.matches, [])
        self.assertEqual(self.combo.remove_canvas.find_all(), ())
        self.click_remove(0)
        self.assertEqual(removed, [])
        self.assertEqual(self.combo.get(), "A new account")
        self.assertIsNotNone(self.combo.popup)

    def test_remove_release_on_different_row_does_not_delete(self):
        removed = []
        self.enable_removal(lambda value: removed.append(value) or True)
        canvas = self.combo.remove_canvas
        _, first_y, _, height = self.combo.listbox.bbox(0)
        _, second_y, _, _ = self.combo.listbox.bbox(1)
        canvas.event_generate("<ButtonPress-1>", x=14, y=first_y + height // 2)
        canvas.event_generate("<ButtonRelease-1>", x=14, y=second_y + height // 2)
        self.root.update()
        self.assertEqual(removed, [])

    def test_remove_redraw_is_cancelled_on_close_and_destroy(self):
        self.enable_removal(lambda value: True)
        self.combo._queue_remove_redraw()
        timer = self.combo._remove_redraw_id
        self.assertIsNotNone(timer)
        self.combo.close_popup()
        self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.combo.open_popup(show_all=True)
        timer = self.combo._remove_redraw_id
        self.combo.destroy()
        self.root.update()
        self.assertNotIn(timer, self.root.tk.call("after", "info"))

    def test_mouse_selects_existing_option(self):
        self.type_value("gro")
        self.combo.listbox.event_generate("<ButtonRelease-1>", x=10, y=7)
        self.root.update()
        self.assertEqual(self.combo.get(), "Groceries")


    def test_new_combo_closes_previous_popup_immediately(self):
        from flowledger.widgets import FilterCombo
        second = FilterCombo(self.root, values=["Amex", "Checking"])
        second.pack(fill="x", padx=20)
        self.root.update()
        self.combo.open_popup(show_all=True)
        old_popup = self.combo.popup
        second.entry.focus_force()
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.assertFalse(old_popup.winfo_exists())
        self.assertIsNotNone(second.popup)
        self.assertIs(self.root._flow_filter_combo, second)

    def test_popup_stays_inside_screen_at_bottom_right(self):
        width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"180x90+{width - 180}+{height - 90}")
        self.root.update()
        self.combo.open_popup(show_all=True)
        self.root.update()
        popup = self.combo.popup
        self.assertGreaterEqual(popup.winfo_rootx(), 0)
        self.assertGreaterEqual(popup.winfo_rooty(), 0)
        self.assertLessEqual(popup.winfo_rootx() + popup.winfo_width(), width)
        self.assertLessEqual(popup.winfo_rooty() + popup.winfo_height(), height)

    def test_first_map_has_final_geometry_without_origin_flash(self):
        self.combo.close_popup()
        self.root.geometry("650x350+100+150")
        self.root.update()
        mapped = []
        self.root.bind_all("<Map>", lambda event: mapped.append(
            (event.widget, event.widget.winfo_rootx(), event.widget.winfo_rooty(),
             event.widget.winfo_width(), event.widget.winfo_height())), add="+")
        self.combo.open_popup(show_all=True)
        popup = self.combo.popup
        self.root.update()
        popup_maps = [(x, y, w, h) for widget, x, y, w, h in mapped if widget is popup]
        self.assertEqual(len(popup_maps), 1)
        self.assertEqual(popup_maps[0], (self.combo.winfo_rootx(),
                         self.combo.winfo_rooty() + self.combo.winfo_height(),
                         popup.winfo_width(), popup.winfo_height()))
        self.assertGreater(popup_maps[0][0], 0)
        self.assertGreater(popup_maps[0][1], 0)

    def test_silent_focus_and_programmatic_changes_do_not_open(self):
        from tkinter import ttk
        other = ttk.Entry(self.root)
        other.pack()
        self.root.update()
        other.focus_set()
        self.root.update()
        self.combo.set("Boat")
        self.combo.set_values(["Boat", "Shopping"])
        self.combo.focus_set(open_popup=False)
        self.root.update()
        self.assertIs(self.root.focus_get(), self.combo.entry)
        self.assertIsNone(self.combo.popup)
        # The input is already focused: clicking it must still open choices.
        self.combo.entry.event_generate("<ButtonPress-1>", x=10, y=10)
        self.root.update()
        self.assertIsNotNone(self.combo.popup)

    def test_ctrl_s_release_cannot_reopen_after_save_or_focus_move(self):
        self.combo.close_popup()
        self.combo.entry.event_generate("<KeyRelease>", keysym="s", state=0x0004)
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.combo.entry.event_generate("<KeyRelease>", keysym="Left")
        self.root.update()
        self.assertIsNone(self.combo.popup)

    def test_outside_label_click_closes_without_focus_change(self):
        from tkinter import ttk
        label = ttk.Label(self.root, text="Outside")
        label.pack()
        self.root.update()
        self.assertIsNotNone(self.combo.popup)
        label.event_generate("<ButtonPress-1>", x=10, y=5)
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.assertIs(self.root.focus_get(), self.combo.entry)

    def test_tab_closes_popup_and_moves_focus(self):
        from tkinter import ttk
        other = ttk.Entry(self.root)
        other.pack()
        self.root.update()
        self.combo.entry.event_generate("<Tab>")
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.assertIs(self.root.focus_get(), other)

    def test_accepting_mouse_choice_does_not_map_another_popup(self):
        mapped = []
        self.root.bind_all("<Map>", lambda event: mapped.append(event.widget), add="+")
        self.combo.listbox.event_generate("<ButtonPress-1>", x=10, y=7)
        self.combo.listbox.event_generate("<ButtonRelease-1>", x=10, y=7)
        self.root.update()
        self.assertEqual(self.combo.get(), "Dining")
        self.assertIsNone(self.combo.popup)
        self.assertEqual(mapped, [])

    def test_close_and_destroy_cancel_deferred_work(self):
        self.combo._defer_focus_check()
        self.combo._paste()
        timers = [self.combo._focus_check_id, self.combo._paste_id]
        self.combo.close_popup()
        for timer in timers:
            self.assertNotIn(timer, self.root.tk.call("after", "info"))
        self.combo._paste()
        self.combo.destroy()
        self.root.update()
        self.assertIsNone(getattr(self.root, "_flow_filter_combo", None))

    def test_focus_switch_before_queued_callback_never_reopens_old_input(self):
        from tkinter import ttk
        other = ttk.Entry(self.root)
        other.pack()
        self.root.update()
        self.combo.close_popup()
        other.focus_set()
        self.root.update()
        self.combo._focus_in()
        self.assertIsNone(self.combo.popup)

    def test_disabled_or_readonly_input_never_opens_suggestions(self):
        for state in ("disabled", "readonly"):
            with self.subTest(state=state):
                self.combo.close_popup()
                self.combo.entry.state(("!disabled", "!readonly", state))
                self.combo.entry.event_generate("<ButtonPress-1>", x=10, y=10)
                self.root.update()
                self.assertIsNone(self.combo.popup)
                original = self.combo.get()
                self.combo._replace_value("Boat")
                self.assertEqual(self.combo.get(), original)
        self.combo.entry.state(("!disabled", "!readonly"))

    def test_popup_toplevel_focus_keeps_filtered_popup_alive(self):
        self.type_value("bo")
        popup = self.combo.popup
        self.assertEqual(self.combo.matches, ["Boat"])
        popup.focus_force()
        self.root.update()
        self.assertIs(self.combo.popup, popup)
        self.assertEqual(self.combo.matches, ["Boat"])
        self.combo.entry.focus_force()
        self.root.update()
        self.assertIs(self.combo.popup, popup)
        self.assertEqual(self.combo.matches, ["Boat"])

    def test_repeated_close_does_not_suppress_later_tab_focus(self):
        from tkinter import ttk
        other = ttk.Entry(self.root)
        other.pack()
        self.root.update()
        self.combo.close_popup()
        first_cleanup = self.combo._dismiss_focus_id
        self.combo.close_popup()
        self.assertIsNotNone(self.combo._dismiss_focus_id)
        self.assertNotIn(first_cleanup, self.root.tk.call("after", "info"))
        self.root.update()
        self.assertFalse(self.combo._suppress_focus_open)
        other.focus_set()
        self.root.update()
        other.event_generate("<Tab>")
        self.root.update()
        self.assertIs(self.root.focus_get(), self.combo.entry)
        self.assertIsNotNone(self.combo.popup)

    def test_delayed_focus_return_after_dismissal_does_not_reopen(self):
        self.combo.close_popup()
        self.root.update()
        # Simulate the window system reporting entry focus after all idle
        # callbacks have already finished, as real X pointer focus can do.
        self.combo.entry.event_generate("<FocusIn>")
        self.root.update()
        self.combo.entry.event_generate("<FocusIn>")
        self.root.update()
        self.assertIsNone(self.combo.popup)
        self.type_value("bo")
        self.assertEqual(self.combo.matches, ["Boat"])

    def test_hover_uses_current_row_details_and_destroy_cancels_timer(self):
        import tkinter as tk
        from tkinter import ttk
        from flowledger.widgets import TreeTooltip
        self.combo.close_popup()
        tree = ttk.Treeview(self.root, columns=("date",), show="headings")
        tree.heading("date", text="Date")
        tree.insert("", "end", iid="flow1", values=("2026-10-02",))
        tree.pack(fill="both", expand=True)
        tooltip = TreeTooltip(tree, lambda item: f"{item}: 2026-10-02 15:30:00", delay=1)
        self.root.update()
        x, y, _, _ = tree.bbox("flow1")
        mapped = []
        self.root.bind_all("<Map>", lambda event: mapped.append(
            (event.widget, event.widget.winfo_rootx(), event.widget.winfo_rooty())), add="+")
        tree.event_generate("<Motion>", x=x + 10, y=y + 10)
        finished = tk.BooleanVar(self.root, False)
        self.root.after(10, finished.set, True)
        self.root.wait_variable(finished)
        self.root.update()
        self.assertIsNotNone(tooltip.popup)
        first_maps = [(x, y) for widget, x, y in mapped if widget is tooltip.popup]
        self.assertEqual(first_maps, [(tooltip.popup.winfo_rootx(), tooltip.popup.winfo_rooty())])
        self.assertNotEqual(first_maps[0], (0, 0))
        self.assertEqual(tooltip.popup.winfo_children()[0].cget("text"),
                         "flow1: 2026-10-02 15:30:00")
        tree.event_generate("<Leave>")
        self.assertIsNone(tooltip.popup)
        tree.event_generate("<Motion>", x=x + 10, y=y + 10)
        timer = tooltip._after_id
        self.assertIsNotNone(timer)
        tree.destroy()
        self.assertIsNone(tooltip._after_id)
        self.assertNotIn(timer, self.root.tk.call("after", "info"))


if __name__ == "__main__":
    unittest.main()
