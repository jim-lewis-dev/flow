"""Searchable checkbox filters for the ledger. No selection means all records."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable


class CheckFilter(ttk.Frame):
    """A compact button that opens a searchable, multiple-choice filter.

    Choices are ORed by the caller. An empty selection imposes no restriction.
    Searching preserves selected values. Refreshing choices removes selections
    that no longer exist; restoring a selection cannot create a new choice.
    """

    def __init__(self, master, label: str, values: Iterable[str] = (),
                 on_change: Callable[[], None] | None = None,
                 all_label: str | None = None, case_sensitive: bool = False,
                 **kwargs):
        super().__init__(master, **kwargs)
        self.label = label
        self.all_label = all_label
        self.case_sensitive = case_sensitive
        self.values = self._canonical_values(values)
        self.on_change = on_change
        self._selected: tuple[str, ...] = ()
        self.popup = None
        self.search_entry = None
        self.search_variable = None
        # The variable outlives a popup so session undo can restore its search.
        # An ordinary reopen still starts with an empty search, as before.
        self.query_variable = tk.StringVar(self)
        self._restore_query = False
        self.canvas = None
        self.all_button = None
        self.checkbuttons = {}
        self._choice_variables = {}
        self._rows = None
        self._all_variable = None
        self._search_trace = None
        self._focus_check_id = None
        self._tooltip_after_id = None
        self._tooltip_popup = None
        self._tooltip_label = None
        self._tooltip_position = (0, 0)
        self.button = ttk.Button(self, width=22, command=self._toggle_popup)
        self.button.pack(fill="x", expand=True)
        self.button.bind("<Return>", self._toggle_popup)
        self.button.bind("<Down>", self._open_from_key)
        self.button.bind("<Escape>", self._escape)
        self.button.bind("<FocusOut>", self._defer_focus_check)
        self.button.bind("<Enter>", self._queue_tooltip, add="+")
        for sequence in ("<Leave>", "<ButtonPress>", "<Unmap>"):
            self.button.bind(sequence, self._hide_tooltip, add="+")
        self.bind("<Destroy>", self._destroyed)
        self.bind("<Unmap>", self._unmapped)
        top = self.winfo_toplevel()
        if not getattr(top, "_flow_check_click_binding", None):
            top._flow_check_click_binding = top.bind(
                "<ButtonPress-1>",
                lambda event: CheckFilter._dismiss_outside(event, top), add="+")
        self._update_caption()

    def get(self) -> tuple[str, ...]:
        """Return selected values; an empty tuple means All."""
        return self._selected

    def tooltip_text(self) -> str:
        """Return every selected value, without shortening long names."""
        return "\n".join(self._selected) if self._selected else "All"

    def _key(self, value: str) -> str:
        return value if self.case_sensitive else value.casefold()

    def _canonical_values(self, values: Iterable[str]) -> list[str]:
        """Keep one database spelling for each distinct choice."""
        canonical = {}
        for value in values:
            value = str(value)
            canonical.setdefault(self._key(value), value)
        return list(canonical.values())

    def _valid_selection(self, values: Iterable[str]) -> tuple[str, ...]:
        available = {self._key(value): value for value in self.values}
        return tuple(dict.fromkeys(available[self._key(str(value))]
                     for value in values if self._key(str(value)) in available))

    def set_selected(self, values: Iterable[str], notify: bool = False) -> bool:
        """Select existing choices, returning whether the selection changed."""
        selected = self._valid_selection(values)
        changed = selected != self._selected
        self._selected = selected
        self._update_caption()
        if self.popup is not None:
            # A checkbox click keeps its own focus unless choices changed.
            candidates = self._available_values()
            query = self.search_variable.get().strip().casefold()
            visible = [value for value in candidates if query in value.casefold()]
            if visible != list(self.checkbuttons):
                self._populate()
            else:
                self._sync_checks()
        if changed and notify and self.on_change is not None:
            self.on_change()
        return changed

    def set_values(self, values: Iterable[str]) -> bool:
        """Refresh choices and silently prune obsolete selected values.

        Return whether the selection changed so callers can synchronize view
        state without producing an extra user action or recursive callback.
        """
        self.values = self._canonical_values(values)
        selected = self._valid_selection(self._selected)
        changed = selected != self._selected
        self._selected = selected
        self._update_caption()
        if self.popup is not None:
            self._populate(preserve_scroll=True)
        return changed

    def state(self, statespec=None):
        result = super().state(statespec)
        if statespec is not None and hasattr(self, "button"):
            disabled = self.instate(("disabled",))
            self.button.state(("disabled",) if disabled else ("!disabled",))
            if disabled:
                self.close_popup()
                self._hide_tooltip()
        return result

    def _update_caption(self):
        count = len(self._selected)
        if not count:
            caption = self.all_label or f"{self.label}: All"
        elif count == 1:
            caption = f"{self.label}: {self._selected[0]}"
        else:
            caption = f"{self.label} · {count}"
        self.button.configure(text=(caption if len(caption) <= 25 else caption[:24] + "…") + " ▾")
        if self._tooltip_popup is not None:
            self._tooltip_label.configure(text=self.tooltip_text())
            self._position_tooltip()

    def _queue_tooltip(self, event):
        self._hide_tooltip()
        if self.popup is None and self._enabled():
            self._tooltip_position = (event.x_root + 14, event.y_root + 20)
            self._tooltip_after_id = self.after(450, self._show_tooltip)

    def _show_tooltip(self):
        self._tooltip_after_id = None
        if self.popup is not None or not self.winfo_viewable() or not self._enabled():
            return
        self._tooltip_popup = tk.Toplevel(self)
        # Set size and position before mapping, without nested event dispatch.
        self._tooltip_popup.withdraw()
        self._tooltip_popup.overrideredirect(True)
        self._tooltip_popup.transient(self.winfo_toplevel())
        self._tooltip_label = tk.Label(self._tooltip_popup, text=self.tooltip_text(),
            justify="left", anchor="w", bg="#213b35", fg="#ffffff",
            padx=10, pady=7, font=("DejaVu Sans", 9), wraplength=500)
        self._tooltip_label.pack()
        self._position_tooltip()
        self._tooltip_popup.deiconify()

    def _position_tooltip(self):
        width = self._tooltip_label.winfo_reqwidth()
        height = self._tooltip_label.winfo_reqheight()
        x = max(0, min(self._tooltip_position[0], self.winfo_screenwidth() - width))
        y = max(0, min(self._tooltip_position[1], self.winfo_screenheight() - height))
        self._tooltip_popup.geometry(f"{width}x{height}+{x}+{y}")

    def _hide_tooltip(self, event=None):
        if self._tooltip_after_id is not None:
            try:
                self.after_cancel(self._tooltip_after_id)
            except tk.TclError:
                pass
            self._tooltip_after_id = None
        if self._tooltip_popup is not None:
            try:
                self._tooltip_popup.destroy()
            except tk.TclError:
                pass
            self._tooltip_popup = None
            self._tooltip_label = None

    def _unmapped(self, event):
        self._hide_tooltip()
        self.close_popup()

    def _available_values(self):
        return self.values

    def _enabled(self):
        return not (self.instate(("disabled",)) or self.button.instate(("disabled",)))

    def _toggle_popup(self, event=None):
        if self.popup is not None:
            self.close_popup()
        else:
            self.open_popup()
        return "break"

    def _open_from_key(self, event=None):
        self.open_popup()
        return "break"

    def open_popup(self):
        self._hide_tooltip()
        if not self._enabled() or not self.winfo_viewable():
            self.close_popup()
            return
        if self.popup is not None:
            self.search_entry.focus_set()
            return
        top = self.winfo_toplevel()
        for attribute in ("_flow_check_filter", "_flow_filter_combo", "_flow_calendar"):
            previous = getattr(top, attribute, None)
            if previous is not None and previous is not self:
                previous.close_popup()
        top._flow_check_filter = self
        if hasattr(top, "view_history"):
            top._open_filter_query(self)
        else:
            self.query_variable.set("")
        self.popup = tk.Toplevel(self)
        if hasattr(top, "_bind_history_popup"):
            top._bind_history_popup(self.popup)
        # Place before the first map. Nested update calls here can flash the
        # popup at the origin and dispatch unrelated form callbacks.
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(top)
        self.popup.configure(background="#ccd9d6", padx=1, pady=1)
        self.popup.bind("<Escape>", self._escape)
        self.popup.bind("<FocusOut>", self._defer_focus_check)
        self.popup.bind("<MouseWheel>", self._wheel)
        self.popup.bind("<Button-4>", lambda event: self._scroll(-1))
        self.popup.bind("<Button-5>", lambda event: self._scroll(1))
        body = ttk.Frame(self.popup, padding=8)
        body.pack(fill="both", expand=True)
        self.search_variable = self.query_variable
        self.search_entry = ttk.Entry(body, textvariable=self.search_variable)
        self.search_entry.pack(fill="x")
        if hasattr(top, "view_history"):
            top.view_history.watch_entry(self.search_entry)
        self._all_variable = tk.BooleanVar(self.popup, value=not self._selected)
        self.all_button = ttk.Checkbutton(body, text="All", variable=self._all_variable,
                                         command=self._clear)
        self.all_button.pack(fill="x", pady=(6, 3))
        ttk.Separator(body).pack(fill="x", pady=(0, 4))
        viewport = ttk.Frame(body)
        viewport.pack(fill="both", expand=True)
        background = ttk.Style(self).lookup("TFrame", "background") or "white"
        self.canvas = tk.Canvas(viewport, background=background, highlightthickness=0,
                                borderwidth=0, takefocus=False)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview,
                                   takefocus=False)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._rows = ttk.Frame(self.canvas)
        window = self.canvas.create_window(0, 0, window=self._rows, anchor="nw")
        self._rows.bind("<Configure>", lambda event: self._update_scrollregion())
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self._populate()
        self._search_trace = self.search_variable.trace_add("write", lambda *_: self._populate())
        row_height = max(24, self.all_button.winfo_reqheight() + 4)
        row_count = max(1, min(8, len(self._available_values())))
        width = min(self.winfo_screenwidth(), max(300, self.winfo_width()))
        height = min(self.winfo_screenheight(), self.search_entry.winfo_reqheight()
                     + self.all_button.winfo_reqheight() + row_count * row_height + 38)
        x = max(0, min(self.winfo_rootx(), self.winfo_screenwidth() - width))
        y = self.winfo_rooty() + self.winfo_height()
        if y + height > self.winfo_screenheight():
            y = self.winfo_rooty() - height
        y = max(0, min(y, self.winfo_screenheight() - height))
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        self.popup.deiconify()
        self.search_entry.focus_set()

    def _populate(self, preserve_scroll=False):
        if self.popup is None:
            return
        position = self.canvas.yview()[0] if preserve_scroll else 0
        focused = self.focus_get()
        focus_value = next((value for value, button in self.checkbuttons.items()
                            if button is focused), None)
        for child in self._rows.winfo_children():
            child.destroy()
        self.checkbuttons = {}
        self._choice_variables = {}
        query = self.search_variable.get().strip().casefold()
        for value in self._available_values():
            if query not in value.casefold():
                continue
            variable = tk.BooleanVar(self.popup, value=value in self._selected)
            button = ttk.Checkbutton(self._rows, text=value, variable=variable,
                                      command=lambda choice=value: self._choice_changed(choice))
            button.pack(fill="x", pady=2)
            button.bind("<FocusIn>", self._reveal_focused)
            self._choice_variables[value] = variable
            self.checkbuttons[value] = button
        if not self.checkbuttons:
            ttk.Label(self._rows, text="No matches", padding=(2, 4)).pack(anchor="w")
        self._sync_checks()
        # Requests are synchronous even though packing is deferred. Supplying
        # a request-based region makes restoring the scroll position reliable.
        height = sum(child.winfo_reqheight() + (4 if isinstance(child, ttk.Checkbutton) else 0)
                     for child in self._rows.winfo_children())
        self.canvas.configure(scrollregion=(0, 0, max(1, self.canvas.winfo_width()), height))
        self.canvas.yview_moveto(position)
        if focus_value is not None:
            self.checkbuttons.get(focus_value, self.search_entry).focus_set()

    def _sync_checks(self):
        self._all_variable.set(not self._selected)
        for value, variable in self._choice_variables.items():
            variable.set(value in self._selected)

    def _choice_changed(self, value):
        if not self._enabled():
            self._sync_checks()
            return
        selected = list(self._selected)
        if self._choice_variables[value].get():
            if value not in selected:
                selected.append(value)
        elif value in selected:
            selected.remove(value)
        self.set_selected(selected, notify=True)

    def _clear(self):
        if self._enabled():
            self.set_selected((), notify=True)
        else:
            self._sync_checks()

    def _update_scrollregion(self):
        if self.canvas is not None:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _reveal_focused(self, event):
        if self.canvas is None:
            return
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        y, height = event.widget.winfo_y(), event.widget.winfo_height()
        full_height = self._rows.winfo_height()
        if full_height > 0 and y < top:
            self.canvas.yview_moveto(y / full_height)
        elif full_height > 0 and y + height > bottom:
            self.canvas.yview_moveto((y + height - self.canvas.winfo_height()) / full_height)

    def _scroll(self, direction):
        if self.canvas is not None:
            self.canvas.yview_scroll(direction, "units")
        return "break"

    def _wheel(self, event):
        if event.delta:
            return self._scroll(-int(event.delta / 120) if abs(event.delta) >= 120
                                else (-1 if event.delta > 0 else 1))

    @staticmethod
    def _dismiss_outside(event, top):
        current = getattr(top, "_flow_check_filter", None)
        if current is None or current._contains(event.widget):
            return
        current.close_popup()

    def _contains(self, widget):
        if widget is self or widget is self.button:
            return True
        return self.popup is not None and (widget is self.popup or
                str(widget).startswith(str(self.popup) + "."))

    def _defer_focus_check(self, event=None):
        if self._focus_check_id is not None:
            self.after_cancel(self._focus_check_id)
        self._focus_check_id = self.after_idle(self._check_focus)

    def _check_focus(self, allow_pointer_grace=True):
        self._focus_check_id = None
        if self.popup is None:
            return
        try:
            focused = self.focus_get()
            if focused is None and allow_pointer_grace:
                pointed = self.winfo_containing(*self.winfo_pointerxy())
                if self._contains(pointed):
                    self._focus_check_id = self.after(60, lambda: self._check_focus(False))
                    return
            if not self._contains(focused):
                self.close_popup()
        except tk.TclError:
            pass

    def _escape(self, event=None):
        if self.popup is None:
            return
        self.close_popup()
        if self.winfo_exists():
            # Escape explicitly returns keyboard control to the opener. An
            # override-redirect window can leave X focus unset on destruction,
            # in which case focus_set merely records a future preference.
            self.button.focus_force()
        return "break"

    def _destroyed(self, event):
        if event.widget is self:
            self._hide_tooltip()
            self.close_popup()

    def close_popup(self):
        if self._focus_check_id is not None:
            try:
                self.after_cancel(self._focus_check_id)
            except tk.TclError:
                pass
            self._focus_check_id = None
        try:
            top = self.winfo_toplevel()
            if getattr(top, "_flow_check_filter", None) is self:
                top._flow_check_filter = None
            if self.search_variable is not None and self._search_trace is not None:
                self.search_variable.trace_remove("write", self._search_trace)
            if self.popup is not None:
                focused = self.focus_get()
                in_popup = focused is self.popup or str(focused).startswith(str(self.popup) + ".")
                if in_popup and self.button.winfo_viewable():
                    # Hand X focus back before destroying its focused window.
                    # Otherwise a click on an entry can leave X focus unset:
                    # Entry's subsequent focus_set only records a preference,
                    # and even a newly opened popup receives no key events.
                    # A genuine focus transfer outside the app has no focused
                    # Tk widget here, so it never takes this focus-return path.
                    self.button.focus_force()
                self.popup.destroy()
        except tk.TclError:
            pass
        self.popup = None
        self.search_entry = None
        self.search_variable = None
        self.canvas = None
        self.all_button = None
        self._all_variable = None
        self._rows = None
        self._search_trace = None
        self.checkbuttons = {}
        self._choice_variables = {}
        return "break"
