"""Native multiple selection, with Shift-scroll range selection for row lists."""

import tkinter as tk
from tkinter import ttk


def enable_multi_selection(widget):
    """Enable Ctrl/Shift selection and Shift-wheel ranges on a row list.

    Ordinary wheel events retain Tk's scrolling behavior. No global bindings
    are changed. Repeated attachment returns the existing controller.
    """
    controller = getattr(widget, "_multi_selection", None)
    if controller is None:
        controller = widget._multi_selection = MultiSelection(widget)
    return controller


class MultiSelection:
    def __init__(self, widget):
        if not isinstance(widget, (ttk.Treeview, tk.Listbox)):
            raise TypeError("Multiple selection requires a Treeview or Listbox.")
        self.widget = widget
        self.is_tree = isinstance(widget, ttk.Treeview)
        self.anchor = None
        self.clicked = None
        widget.configure(selectmode="extended")
        if not self.is_tree:
            widget.configure(exportselection=False)

        # Observe native mouse/key selection before and after the class binding.
        # This also keeps the Treeview anchor separate from its moving focus.
        before, after = f"FlowRangeBefore:{widget}", f"FlowRangeAfter:{widget}"
        tags = list(widget.bindtags())
        tags.insert(0, before)
        tags.insert(tags.index(widget.winfo_class()) + 1, after)
        widget.bindtags(tuple(tags))
        self._bindings = []

        def bind(tag, sequence, callback):
            self._bindings.append((tag, sequence, widget.bind_class(tag, sequence, callback)))

        bind(before, "<Button-1>", self._press)
        bind(before, "<Shift-Button-1>", self._shift_press)
        bind(after, "<Button-1>", self._pressed)
        bind(after, "<ButtonRelease-1>", self._released)
        bind(after, "<KeyPress>", self._key)
        bind(before, "<Shift-MouseWheel>", self._wheel)
        bind(before, "<Shift-Button-4>", lambda event: self._step(-1))
        bind(before, "<Shift-Button-5>", lambda event: self._step(1))
        if self.is_tree:
            # Tk Treeview has no native Shift-Up/Down range bindings.
            bind(before, "<Shift-Up>", lambda event: self._step(-1))
            bind(before, "<Shift-Down>", lambda event: self._step(1))
        widget.bind("<Destroy>", self._destroy, add="+")

    def _destroy(self, event):
        if event.widget is self.widget:
            root = self.widget._root()
            for tag, sequence, command in self._bindings:
                self.widget.unbind_class(tag, sequence)
                root.deletecommand(command)
            self._bindings.clear()

    def _rows(self):
        if not self.is_tree:
            # Values identify rebuilt Listbox rows; occurrence distinguishes
            # duplicate labels without adding any model fields.
            counts, rows = {}, []
            for value in self.widget.get(0, "end"):
                count = counts.get(value, 0)
                rows.append((value, count))
                counts[value] = count + 1
            return rows
        rows = []

        def visit(parent):
            for row in self.widget.get_children(parent):
                rows.append(row)
                if self.widget.item(row, "open"):
                    visit(row)

        visit("")
        return rows

    def _selected(self, rows):
        if self.is_tree:
            return [row for row in self.widget.selection() if row in rows]
        return [rows[index] for index in self.widget.curselection() if index < len(rows)]

    def _focused(self, rows):
        if self.is_tree:
            focused = self.widget.focus()
            return focused if focused in rows else None
        index = self.widget.index("active")
        return rows[index] if 0 <= index < len(rows) else None

    def _pointed(self, event, rows):
        if self.is_tree:
            row = self.widget.identify_row(event.y)
            return row if row in rows else None
        index = self.widget.nearest(event.y)
        return rows[index] if 0 <= index < len(rows) else None

    def _press(self, event):
        self.clicked = self._pointed(event, self._rows())
        if self.clicked is not None:
            self.anchor = self.clicked

    def _shift_press(self, event):
        rows = self._rows()
        self.clicked = self._pointed(event, rows)
        if self.is_tree and self.clicked is not None:
            anchor, _ = self._range_start(rows)
            self.widget.focus(anchor)

    def _pressed(self, event):
        if self.is_tree and self.clicked in self._rows():
            # Treeview's native modified click changes selection, not focus.
            # Editors need the clicked selected row to remain the active row.
            selected = self.widget.selection()
            focused = self.widget.focus()
            target = self.clicked if self.clicked in selected else (
                focused if focused in selected else (selected[0] if selected else ""))
            if focused != target:
                self.widget.focus(target)
                self.widget.event_generate("<<TreeviewSelect>>", when="tail")

    def _released(self, event):
        if not event.state & 1:
            rows = self._rows()
            self.anchor = self._focused(rows)

    def _key(self, event):
        if event.keysym in ("Up", "Down", "Home", "End", "Prior", "Next"):
            if not event.state & 1:
                self.anchor = self._focused(self._rows())
            if self.is_tree:
                self._see_row(self.widget.focus())

    def _see_row(self, row):
        if not row or not self.widget.exists(row):
            return
        self.widget.see(row)
        bounds = self.widget.bbox(row)
        if bounds:
            _, y, _, height = bounds
            # Tk's see() can leave the last row clipped by the bottom border.
            bottom = y + height - 1
            if (bottom >= self.widget.winfo_height() or
                    self.widget.identify_region(self.widget.winfo_width() // 2, bottom) == "nothing"):
                self.widget.yview_scroll(1, "units")

    def _range_start(self, rows):
        selected = self._selected(rows)
        focused = self._focused(rows)
        # A removed/filtered anchor, or a fresh programmatic selection, starts
        # at a remaining focused selected row, then any remaining selected row.
        fallback = focused if focused in selected else (selected[0] if selected else focused)
        fallback = fallback if fallback is not None else rows[0]
        if self.anchor not in rows or self.anchor not in selected:
            self.anchor = fallback
        return self.anchor, focused if focused in selected else fallback

    def _wheel(self, event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return "break"
        amount = (-1 if delta > 0 else 1) * max(1, abs(delta) // 120)
        return self._step(amount)

    def _step(self, amount):
        widget, rows = self.widget, self._rows()
        if not rows or (self.is_tree and widget.instate(("disabled",))) or (
                not self.is_tree and str(widget.cget("state")) == "disabled"):
            return "break"
        widget.focus_set()
        anchor, current = self._range_start(rows)
        endpoint = rows[max(0, min(len(rows) - 1, rows.index(current) + amount))]
        start, end = sorted((rows.index(anchor), rows.index(endpoint)))
        if self.is_tree:
            widget.selection_set(rows[start:end + 1])
            widget.focus(endpoint)
            self._see_row(endpoint)
        else:
            widget.selection_clear(0, "end")
            widget.selection_set(start, end)
            widget.selection_anchor(rows.index(anchor))
            widget.activate(rows.index(endpoint))
            widget.see(rows.index(endpoint))
            # Native Shift-arrow/drag continues from the Shift-wheel endpoint.
            widget.tk.setvar("::tk::Priv(listboxPrev)", rows.index(endpoint))
            widget.tk.setvar("::tk::Priv(listboxSelection)", ())
            widget.event_generate("<<ListboxSelect>>")
        return "break"
