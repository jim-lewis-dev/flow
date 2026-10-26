"""Keep long ledger selection summaries readable in a bounded hover window."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont, ttk

from .widgets import TreeTooltip


class SelectionTooltip(TreeTooltip):
    """Use ordinary row details except when hovering a multi-selected row."""

    def __init__(self, tree, text_for_item, is_group, delay=450):
        self.is_group = is_group
        self.text = None
        self._previous_focus = None
        self._group_visible = False
        self._leave_id = None
        super().__init__(tree, text_for_item, delay)
        for sequence in ("<<TreeviewSelect>>", "<KeyPress>", "<Configure>"):
            tree.bind(sequence, self.hide, add="+")

    def _cancel_leave(self, event=None):
        if self._leave_id is not None:
            self.tree.after_cancel(self._leave_id)
            self._leave_id = None

    def _motion(self, event):
        item = self.tree.identify_row(event.y)
        if self._group_visible and self.popup is not None and self.is_group(item):
            self._item = item
            self._cancel_leave()
            return
        super()._motion(event)

    def _show(self):
        if not self.is_group(self._item):
            return super()._show()
        self._after_id = None
        if not self._item or not self.tree.winfo_viewable() or not self.tree.exists(self._item):
            return
        value = self.text_for_item(self._item)
        if not value:
            return
        window = self.tree.winfo_toplevel()
        left, top = max(0, window.winfo_rootx()), max(0, window.winfo_rooty())
        right = min(self.tree.winfo_screenwidth(), window.winfo_rootx() + window.winfo_width())
        bottom = min(self.tree.winfo_screenheight(), window.winfo_rooty() + window.winfo_height())
        font = tkfont.Font(self.tree, family="DejaVu Sans", size=9)
        lines = value.splitlines()
        width = min(760, max(1, right - left - 16),
                    max(360, max(font.measure(line) for line in lines) + 42))
        wrapped_lines = sum(max(1, math.ceil(font.measure(line) / max(1, width - 42)))
                            for line in lines)
        height = min(560, max(1, bottom - top - 16),
                     wrapped_lines * font.metrics("linespace") + 18)
        self._previous_focus = self.tree.focus_get()
        self.popup = tk.Toplevel(self.tree)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(window)
        self.popup.bind("<FocusIn>", self._restore_focus)
        self.popup.configure(bg="#213b35")
        self.popup.rowconfigure(0, weight=1)
        self.popup.columnconfigure(0, weight=1)
        self.text = tk.Text(self.popup, wrap="word", font=("DejaVu Sans", 9), width=1, height=1,
                            bg="#213b35", fg="#ffffff", padx=10, pady=7,
                            borderwidth=0, highlightthickness=0, cursor="arrow",
                            takefocus=False, exportselection=False)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.popup, command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.insert("1.0", value)
        self.text.tag_configure("heading", font=("DejaVu Sans", 9, "bold"))
        for number, line in enumerate(lines, 1):
            if number == 1 or line in ("From", "To", "Categories"):
                self.text.tag_add("heading", f"{number}.0", f"{number}.end")
        self.text.configure(state="disabled")
        self.text.bind("<Button-1>", lambda event: "break")
        for widget in (self.popup, self.text, scroll):
            widget.bind("<Enter>", self._cancel_leave)
            widget.bind("<Leave>", self.hide)
        # Text's local wheel bindings scroll this body without moving the ledger.
        x = max(left, min(self._position[0], right - width))
        y = max(top, min(self._position[1], bottom - height))
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        self._group_visible = True
        self.popup.deiconify()

    def _restore_focus(self, event=None):
        target = self._previous_focus
        if target is None or not target.winfo_exists():
            target = self.tree
        target.focus_set()

    def _check_leave(self):
        self._leave_id = None
        x, y = self.tree.winfo_pointerxy()
        pointed = self.tree.winfo_containing(x, y)
        if self.popup is not None and pointed is not None:
            if str(pointed) == str(self.popup) or str(pointed).startswith(str(self.popup) + "."):
                return
        if pointed is self.tree and self.is_group(self.tree.identify_row(y - self.tree.winfo_rooty())):
            return
        self.hide()

    def hide(self, event=None):
        if (event is not None and event.type == tk.EventType.Leave
                and self._group_visible and self.popup is not None):
            self._cancel_leave()
            self._leave_id = self.tree.after(180, self._check_leave)
            return
        self._cancel_leave()
        super().hide(event)
        self.text = None
        self._previous_focus = None
        self._group_visible = False
