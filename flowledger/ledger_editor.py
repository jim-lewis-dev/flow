"""A cell-sized view of the existing Flow form draft.

The editor shares the form's StringVars and notes buffer. It never writes a
record; the form's Save and Cancel commands still own the whole transaction.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .core import STATUSES
from .widgets import FilterCombo


class LedgerEditor:
    """Keep one editor attached to one visible ledger cell."""

    def __init__(self, app, notes_peer, *, font, background, foreground):
        self.app = app
        self.tree = app.ledger
        self.notes_peer = notes_peer
        self.font = font
        self.background = background
        self.foreground = foreground
        self.widget = self.input = None
        self.field = self.flow_id = None
        self._focus_id = self._position_id = None
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<F2>", self._keyboard_edit)
        self.tree.bind("<Configure>", self.queue_position, add="+")
        self.tree.bind("<Unmap>", lambda event: self.close(), add="+")
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tree.bind(sequence, lambda event: self.close(), add="+")
        app.bind("<Destroy>", self._destroyed, add="+")

    def _double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if row and column:
            key = self.tree["columns"][int(column[1:]) - 1]
            self.open(row, key)
            return "break"

    def _keyboard_edit(self, event=None):
        selected = self.tree.selection()
        if selected:
            self.open(selected[0], "date")
        return "break"

    def open(self, flow_id, field):
        """Open a cell on the selected draft, resolving any other draft first."""
        if field not in self.tree["columns"] or not self.tree.exists(flow_id):
            return False
        self.close()
        if self.app.editing_id != flow_id:
            self.tree.selection_set(flow_id)
            self.app._select_flow()
        if self.app.editing_id != flow_id:
            return False  # Cancel or failed validation retained the old draft.
        self.app._close_popups()
        self.tree.see(flow_id)
        bounds = self.tree.bbox(flow_id, field)
        if not bounds or self.app.current_view != "flows":
            return False
        self.flow_id, self.field = flow_id, field
        variable_key = "amount" if field == "amount_cents" else field
        if field == "notes":
            widget = self.notes_peer(self.tree, self.app.notes_text,
                height=1, width=1, wrap="word", font=(self.font, 9),
                bg=self.background, fg=self.foreground, padx=4, pady=3,
                relief="solid", borderwidth=1, highlightthickness=0)
            widget.bind("<<Modified>>", self.app._flow_notes_changed)
            for sequence, direction in (("<Button-4>", -1), ("<Button-5>", 1)):
                widget.bind(sequence, lambda event, delta=direction: self.app._scroll_notes(event, delta))
            widget.bind("<MouseWheel>", self.app._scroll_notes)
            widget.bind("<KeyRelease>", lambda event: widget.see("insert"), add="+")
            self.input = widget
        elif field in self.app.suggestion_fields:
            source = self.app.form_widgets[field]
            widget = FilterCombo(self.tree, variable=self.app.form_vars[field], values=source.values,
                on_remove=lambda value: self.app._remove_suggestion(field, value))
            widget.multi_value = source.multi_value
            widget.entry.configure(width=1, style="Ledger.TEntry")
            widget.on_accept = lambda: self.app._accept_suggestion(field, widget)
            self.input = widget.entry
        elif field == "status":
            widget = ttk.Combobox(self.tree, textvariable=self.app.form_vars[field],
                values=STATUSES, state="readonly", width=1)
            self.input = widget
        else:
            widget = ttk.Entry(self.tree, textvariable=self.app.form_vars[variable_key],
                width=1, style="Ledger.TEntry")
            self.input = widget
        self.widget = widget
        self.position()
        if field != "notes":
            self.input.bind("<Return>", self._enter)
            self.input.bind("<KP_Enter>", self._enter)
        self.input.bind("<Control-Return>", self._enter)
        self.input.bind("<Tab>", lambda event: self._tab(1))
        self.input.bind("<Shift-Tab>", lambda event: self._tab(-1))
        self.input.bind("<ISO_Left_Tab>", lambda event: self._tab(-1))
        self.input.bind("<Escape>", self._escape)
        self.input.bind("<FocusOut>", self._defer_focus_check, add="+")
        if isinstance(widget, FilterCombo):
            # Geometry has been positioned before focus can map a popup.
            widget.focus_set(open_popup=False)
            widget.entry.selection_range(0, "end")
            widget.entry.icursor("end")
        else:
            self.input.focus_set()
            if field != "notes":
                self.input.selection_range(0, "end")
                self.input.icursor("end")
            else:
                self.input.mark_set("insert", self.app.notes_text.index("insert"))
                self.input.see("insert")
        return True

    def _enter(self, event=None):
        if isinstance(self.widget, FilterCombo):
            self.widget._accept(event)
        self.close(focus_tree=True)
        return "break"

    def _tab(self, direction):
        row, field = self.flow_id, self.field
        columns = tuple(self.tree["columns"])
        index = columns.index(field) + direction
        self.close()
        if 0 <= index < len(columns):
            self.open(row, columns[index])
        else:
            self.tree.focus_set()
        return "break"

    def _escape(self, event=None):
        if isinstance(self.widget, FilterCombo) and self.widget.popup is not None:
            self.widget.close_popup()
        else:
            self.close(focus_tree=True)
        return "break"

    def _defer_focus_check(self, event=None):
        if self._focus_id is not None:
            self.app.after_cancel(self._focus_id)
        self._focus_id = self.app.after_idle(self._check_focus)

    def _check_focus(self):
        self._focus_id = None
        widget = self.widget
        if widget is None:
            return
        notes_dialog = getattr(self.app, "notes_dialog", None)
        if self.field == "notes" and notes_dialog is not None and notes_dialog.winfo_exists():
            return  # The expanded editor is another view of this same buffer.
        # ttk Combobox owns a Tcl-only .popdown window: focus_get() cannot
        # resolve it through tkinter's widget registry. Its path still clearly
        # belongs to this editor, so keep the posted choice list alive.
        focused = str(self.app.tk.call("focus"))
        if focused == str(self.input) or focused.startswith(str(widget) + "."):
            return
        if isinstance(widget, FilterCombo) and widget.popup is not None:
            popup = widget.popup
            if focused == str(popup) or focused.startswith(str(popup) + "."):
                return
            if not focused:
                pointed = self.app.winfo_containing(*self.app.winfo_pointerxy())
                if pointed is popup or (pointed is not None and
                        str(pointed).startswith(str(popup) + ".")):
                    self._focus_id = self.app.after(80, self._check_focus)
                    return  # FilterCombo owns the popup's brief focus transfer.
        self.close()

    def queue_position(self, event=None):
        if self.widget is not None and self._position_id is None:
            self._position_id = self.app.after_idle(self.position)

    def position(self):
        self._position_id = None
        if self.widget is None:
            return
        bounds = self.tree.bbox(self.flow_id, self.field)
        if (not bounds or self.app.editing_id != self.flow_id or
                self.app.current_view != "flows" or not self.tree.winfo_viewable()):
            self.close()
            return
        if isinstance(self.widget, FilterCombo):
            self.widget.close_popup()
        x, y, width, height = bounds
        self.widget.place(x=x, y=y, width=width, height=height)
        self.widget.lift()

    def close(self, *, focus_tree=False):
        """End this field's view while keeping every value in the shared draft."""
        for attr in ("_focus_id", "_position_id"):
            after_id = getattr(self, attr)
            if after_id is not None:
                self.app.after_cancel(after_id)
                setattr(self, attr, None)
        widget, field = self.widget, self.field
        self.widget = self.input = None
        self.field = self.flow_id = None
        if widget is not None and widget.winfo_exists():
            if field == "date":
                self.app._split_flow_date()
            if isinstance(widget, FilterCombo):
                widget.close_popup()
            widget.destroy()
        if focus_tree:
            self.tree.focus_set()

    def refresh_suggestions(self):
        if isinstance(self.widget, FilterCombo):
            self.widget.set_values(self.app.form_widgets[self.field].values)

    def _destroyed(self, event):
        if event.widget is self.app:
            self.close()
