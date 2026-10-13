"""Multiple row selection keeps native gestures and extends via Shift-scroll."""

import os
import tkinter as tk
from tkinter import ttk
import unittest

from flowledger.table_selection import enable_multi_selection


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class TableSelectionTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("640x420+0+0")
        self.errors = []
        self.root.report_callback_exception = lambda kind, value, traceback: self.errors.append(str(value))
        self.tree = ttk.Treeview(self.root, columns=("name",), show="headings", height=8)
        self.tree.heading("name", text="Name")
        self.tree.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Listbox(self.root, height=8)
        self.listbox.pack(side="left", fill="both", expand=True)
        for index in range(30):
            self.tree.insert("", "end", iid=f"r{index}", values=(f"Row {index}",))
            self.listbox.insert("end", f"Row {index}")
        self.controllers = {widget: enable_multi_selection(widget) for widget in (self.tree, self.listbox)}
        self.pump()

    def tearDown(self):
        self.root.destroy()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(self.errors, [])

    def click(self, widget, index, modifier=""):
        if widget is self.tree:
            widget.see(f"r{index}")
            self.pump()
            x, y, width, height = widget.bbox(f"r{index}")
        else:
            widget.see(index)
            self.pump()
            x, y, width, height = widget.bbox(index)
        widget.event_generate(f"<{modifier}ButtonPress-1>", x=x + 8, y=y + height // 2)
        widget.event_generate(f"<{modifier}ButtonRelease-1>", x=x + 8, y=y + height // 2)
        self.pump()

    def key(self, widget, sequence, **kwargs):
        widget.focus_force()
        self.pump()
        widget.event_generate(sequence, **kwargs)
        self.pump()

    def selected(self, widget):
        return tuple(int(row[1:]) for row in widget.selection()) if widget is self.tree else widget.curselection()

    def test_native_click_control_click_and_shift_click(self):
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 1)
                self.assertEqual(self.selected(widget), (1,))
                self.click(widget, 3, "Control-")
                self.assertEqual(self.selected(widget), (1, 3))
                focused = widget.focus() if widget is self.tree else widget.index("active")
                self.assertEqual(focused, "r3" if widget is self.tree else 3)
                self.click(widget, 5, "Shift-")
                expected = (3, 4, 5) if widget is self.tree else (1, 3, 4, 5)
                self.assertEqual(self.selected(widget), expected)
                focused = widget.focus() if widget is self.tree else widget.index("active")
                self.assertEqual(focused, "r5" if widget is self.tree else 5)
                self.click(widget, 8)
                self.assertEqual(self.selected(widget), (8,))

    def test_tree_control_toggle_off_keeps_a_selected_focus(self):
        changes = []
        self.tree.bind("<<TreeviewSelect>>", lambda event: changes.append(self.tree.focus()))
        self.click(self.tree, 1)
        self.click(self.tree, 3, "Control-")
        self.assertEqual(self.tree.focus(), "r3")
        self.click(self.tree, 3, "Control-")
        self.assertEqual(self.selected(self.tree), (1,))
        self.assertEqual(self.tree.focus(), "r1")
        self.assertEqual(changes[-1], "r1")

    def test_shift_click_after_shift_scroll_keeps_original_anchor(self):
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 2)
                self.key(widget, "<Shift-MouseWheel>", delta=-240)
                self.click(widget, 6, "Shift-")
                self.assertEqual(self.selected(widget), (2, 3, 4, 5, 6))

    def test_shift_scroll_focuses_list_after_editing_a_text_field(self):
        entry = ttk.Entry(self.root)
        entry.pack()
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 2)
                entry.focus_force()
                self.pump()
                self.assertIs(self.root.focus_get(), entry)
                widget.event_generate("<Shift-MouseWheel>", delta=-120)
                self.pump()
                self.assertIs(self.root.focus_get(), widget)
                self.assertEqual(self.selected(widget), (2, 3))

    def test_destroyed_child_removes_its_bindings_without_affecting_other_lists(self):
        child = ttk.Treeview(self.root)
        controller = enable_multi_selection(child)
        bindings = list(controller._bindings)
        commands = [command for _, _, command in bindings]
        self.assertTrue(set(commands).issubset(self.root._tclCommands))
        child.destroy()
        self.pump()
        for tag, sequence, command in bindings:
            self.assertFalse(self.root.bind_class(tag, sequence))
            self.assertFalse(self.root.tk.call("info", "commands", command))
            self.assertNotIn(command, self.root._tclCommands)
        self.click(self.tree, 1)
        self.key(self.tree, "<Shift-Down>")
        self.assertEqual(self.selected(self.tree), (1, 2))

    def test_shift_wheel_then_native_keyboard_extends_and_shrinks(self):
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 3)
                self.key(widget, "<Shift-MouseWheel>", delta=-120)
                self.assertEqual(self.selected(widget), (3, 4))
                self.key(widget, "<Shift-Down>")
                self.assertEqual(self.selected(widget), (3, 4, 5))
                self.key(widget, "<Shift-Up>")
                self.assertEqual(self.selected(widget), (3, 4))
                self.key(widget, "<Shift-Button-4>")
                self.assertEqual(self.selected(widget), (3,))
                self.key(widget, "<Shift-Button-4>")
                self.assertEqual(self.selected(widget), (2, 3))
                self.key(widget, "<Shift-Button-5>")
                self.assertEqual(self.selected(widget), (3,))

    def test_plain_arrow_resets_anchor_and_shift_click_uses_new_anchor(self):
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 2)
                self.key(widget, "<Shift-MouseWheel>", delta=-240)
                self.assertEqual(self.selected(widget), (2, 3, 4))
                self.key(widget, "<Down>")
                self.assertEqual(self.selected(widget), (5,))
                self.click(widget, 7, "Shift-")
                self.assertEqual(self.selected(widget), (5, 6, 7))

    def test_normal_wheel_only_scrolls_without_changing_selection(self):
        for widget in self.controllers:
            with self.subTest(widget=widget.winfo_class()):
                self.click(widget, 1)
                self.click(widget, 3, "Control-")
                before = self.selected(widget)
                self.key(widget, "<MouseWheel>", delta=-120)
                self.assertEqual(self.selected(widget), before)
                self.key(widget, "<Button-5>")
                self.assertEqual(self.selected(widget), before)
                self.assertGreater(widget.yview()[0], 0)

    def test_deleted_anchor_falls_back_to_remaining_selected_row(self):
        self.click(self.tree, 2)
        self.key(self.tree, "<Shift-MouseWheel>", delta=-240)
        self.tree.delete("r2")
        self.key(self.tree, "<Shift-MouseWheel>", delta=-120)
        self.assertEqual(self.selected(self.tree), (4, 5))
        self.click(self.listbox, 2)
        self.key(self.listbox, "<Shift-MouseWheel>", delta=-240)
        self.listbox.delete(2)
        self.key(self.listbox, "<Shift-MouseWheel>", delta=-120)
        self.assertEqual(self.listbox.get(self.listbox.index("anchor")), "Row 4")
        self.assertEqual(tuple(self.listbox.get(i) for i in self.listbox.curselection()), ("Row 4", "Row 5"))

    def test_empty_disabled_and_repeated_attachment_are_safe(self):
        for widget, controller in self.controllers.items():
            with self.subTest(widget=widget.winfo_class()):
                self.assertIs(enable_multi_selection(widget), controller)
                self.click(widget, 0)
                if widget is self.tree:
                    widget.state(("disabled",))
                else:
                    widget.configure(state="disabled")
                self.key(widget, "<Shift-MouseWheel>", delta=-120)
                self.assertEqual(self.selected(widget), (0,))
                if widget is self.tree:
                    widget.state(("!disabled",))
                    widget.delete(*widget.get_children())
                else:
                    widget.configure(state="normal")
                    widget.delete(0, "end")
                self.key(widget, "<Shift-MouseWheel>", delta=-120)
                self.key(widget, "<Shift-Button-5>")
                self.assertEqual(self.selected(widget), ())


if __name__ == "__main__":
    unittest.main()
