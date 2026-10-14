"""Editable date/time entry with a calendar. No additional dependencies."""
from __future__ import annotations

import calendar
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk
from typing import Callable


class CalendarEntry(ttk.Frame):
    """Editable date plus a calendar button; choosing a day preserves its time.

    Text is left untouched until a day is chosen. An invalid or empty value
    opens the current month, so merely inspecting the calendar never changes
    the user's input. Each view validates manually typed values.
    """

    def __init__(self, master, variable=None, *, textvariable=None, width=12,
                 on_select: Callable[[], None] | None = None, **kwargs):
        super().__init__(master, **kwargs)
        self.variable = variable if variable is not None else textvariable
        if self.variable is None:
            self.variable = tk.StringVar(self)
        self.on_select = on_select
        self.popup = None
        self.day_buttons = {}
        self.previous_button = None
        self.next_button = None
        self._cells = []
        self._heading = None
        self._focus_check_id = None
        self._size_after_id = None
        self._icon_size = None
        self._calendar_icon = None
        self._year, self._month = date.today().year, date.today().month
        self._selected_date = None
        self._time_suffix = ""
        # Measure the ordinary entry separately: reserving text space for the
        # overlaid button must not widen the field or move neighboring fields.
        self._measure_entry = ttk.Entry(self, width=width, takefocus=False)
        self._entry_style = f"Calendar{id(self)}.TEntry"
        self._style = ttk.Style(self)
        self.entry = ttk.Entry(self, textvariable=self.variable, width=width)
        self.entry.place(x=0, y=0, relwidth=1, relheight=1)
        self.button = ttk.Button(self, width=0, command=self._toggle_popup,
                                 style="Calendar.TButton")
        self.button.place(relx=1, y=0, anchor="ne")
        self.bind("<Configure>", self._layout_button)
        self.bind("<<ThemeChanged>>", self._schedule_size)
        self._measure_size()
        self.button.bind("<Return>", self._toggle_popup)
        self.button.bind("<Down>", self._open_from_key)
        self.entry.bind("<Alt-Down>", self._open_from_key)
        for widget in (self.entry, self.button):
            widget.bind("<Escape>", self._escape)
            widget.bind("<FocusOut>", self._defer_focus_check)
        self.bind("<Destroy>", self._destroyed)
        self.bind("<Unmap>", lambda event: self.close_popup())
        top = self.winfo_toplevel()
        if not getattr(top, "_flow_calendar_click_binding", None):
            top._flow_calendar_click_binding = top.bind(
                "<ButtonPress-1>",
                lambda event: CalendarEntry._dismiss_outside(event, top), add="+")

    def _schedule_size(self, event=None):
        if self._size_after_id is None:
            self._size_after_id = self.after_idle(self._measure_size)

    def _measure_size(self):
        self._size_after_id = None
        width, height = self._measure_entry.winfo_reqwidth(), self._measure_entry.winfo_reqheight()
        if (int(self.cget("width")), int(self.cget("height"))) != (width, height):
            self.configure(width=width, height=height)
        self._layout_button()

    def _layout_button(self, event=None):
        height = self.winfo_height()
        if height <= 1:
            height = self._measure_entry.winfo_reqheight()
        def padding_values(style):
            padding = self._style.lookup(style, "padding")
            parts = padding if isinstance(padding, (tuple, list)) else self.tk.splitlist(str(padding))
            return [self.winfo_pixels(value) for value in parts] if parts else [0]

        if any(padding_values("Calendar.TButton")):
            self._style.configure("Calendar.TButton", padding=0)
        self.button.place_configure(width=height, height=height)
        values = padding_values("TEntry")
        if len(values) == 1:
            left = top = right = bottom = values[0]
        elif len(values) == 2:
            left = right = values[0]
            top = bottom = values[1]
        elif len(values) == 3:
            left, top, right = values
            bottom = top
        else:
            left, top, right, bottom = values[:4]
        reserved = (left, top, right + height, bottom)
        # Style updates emit ThemeChanged; avoid an idle remeasurement loop.
        if tuple(padding_values(self._entry_style)) != reserved:
            self._style.configure(self._entry_style, padding=reserved)
        if self.entry.cget("style") != self._entry_style:
            self.entry.configure(style=self._entry_style)
        size = max(12, round(height * 0.72))
        color = self._style.lookup("TButton", "foreground") or "#244942"
        if (size, color) != self._icon_size:
            self._calendar_icon = self._draw_calendar(size, color)
            self._icon_size = (size, color)
            self.button.configure(image=self._calendar_icon)

    def _draw_calendar(self, size, color):
        """Draw a crisp calendar at the current display size, independent of fonts."""
        icon = tk.PhotoImage(master=self, width=size, height=size)
        stroke = max(1, round(size * 0.07))
        margin = max(1, round(size * 0.10))
        left, right = margin, size - margin
        top, bottom = round(size * 0.23), size - margin

        def rectangle(x0, y0, x1, y1):
            icon.put(color, to=(x0, y0, x1, y1))

        rectangle(left, top, right, top + stroke)
        rectangle(left, bottom - stroke, right, bottom)
        rectangle(left, top, left + stroke, bottom)
        rectangle(right - stroke, top, right, bottom)
        rectangle(left, round(size * 0.40), right, round(size * 0.40) + stroke)
        for position in (0.30, 0.70):
            x = round(size * position)
            rectangle(x, margin, x + stroke, top + stroke)
        for row in (0.55, 0.75):
            for column in (0.28, 0.48, 0.68):
                x, y = round(size * column), round(size * row)
                rectangle(x, y, x + stroke, y + stroke)
        return icon

    def state(self, statespec=None):
        result = super().state(statespec)
        if statespec is not None and hasattr(self, "entry"):
            disabled = self.instate(("disabled",))
            self.entry.state(("disabled",) if disabled else ("!disabled",))
            self.button.state(("disabled",) if disabled else ("!disabled",))
            if disabled:
                self.close_popup()
        return result

    def _toggle_popup(self, event=None):
        if self.popup is not None:
            self.close_popup()
        else:
            self.open_popup()
        return "break"

    def _open_from_key(self, event=None):
        self.open_popup()
        return "break"

    def _initial_date(self):
        from .graphing import normalize_graph_date
        self._time_suffix = ""
        try:
            normalized = normalize_graph_date(self.variable.get())
            selected = datetime.fromisoformat(normalized).date()
            self._time_suffix = normalized[10:]
            return selected
        except (ValueError, TypeError):
            return date.today()

    def open_popup(self):
        if (not self.winfo_viewable() or self.instate(("disabled",))
                or self.entry.instate(("disabled",)) or self.button.instate(("disabled",))):
            self.close_popup()
            return
        if self.popup is not None:
            return
        top = self.winfo_toplevel()
        for attribute in ("_flow_calendar", "_flow_check_filter", "_flow_filter_combo"):
            previous = getattr(top, attribute, None)
            if previous is not None and previous is not self:
                previous.close_popup()
        top._flow_calendar = self
        self._selected_date = self._initial_date()
        self._year, self._month = self._selected_date.year, self._selected_date.month
        self.popup = tk.Toplevel(self)
        if hasattr(top, "_bind_history_popup"):
            top._bind_history_popup(self.popup)
        # Build and position while withdrawn. update() here would risk mapping
        # an unpositioned window and running callbacks during construction.
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.transient(top)
        self.popup.configure(background="#ccd9d6", padx=1, pady=1)
        self.popup.bind("<Escape>", self._escape)
        self.popup.bind("<FocusOut>", self._defer_focus_check)
        self.popup.bind("<Prior>", lambda event: self._change_month(-1))
        self.popup.bind("<Next>", lambda event: self._change_month(1))
        self.popup.bind("<Unmap>", self._popup_unmapped)
        self.popup.bind("<Destroy>", self._popup_unmapped)
        body = ttk.Frame(self.popup, padding=8)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body)
        header.pack(fill="x", pady=(0, 6))
        self.previous_button = ttk.Button(header, text="‹", width=3,
                                          command=lambda: self._change_month(-1))
        self.previous_button.pack(side="left")
        self.next_button = ttk.Button(header, text="›", width=3,
                                      command=lambda: self._change_month(1))
        self.next_button.pack(side="right")
        self._heading = ttk.Label(header, anchor="center", width=18)
        self._heading.pack(side="left", fill="x", expand=True, padx=6)
        days = ttk.Frame(body)
        days.pack(fill="both", expand=True)
        day_heading = None
        for column, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            days.columnconfigure(column, weight=1)
            day_heading = ttk.Label(days, text=name, anchor="center")
            day_heading.grid(row=0, column=column, sticky="ew")
        self._cells = []
        for index in range(42):
            button = ttk.Button(days, width=3, padding=(3, 3))
            button.grid(row=index // 7 + 1, column=index % 7, sticky="nsew", padx=1, pady=1)
            self._cells.append(button)
        self._render_month()
        width = max(280, sum((self._cells[0].winfo_reqwidth() + 2 for _ in range(7))) + 18,
                    self.previous_button.winfo_reqwidth() + self.next_button.winfo_reqwidth()
                    + self._heading.winfo_reqwidth() + 30)
        height = max(self.previous_button.winfo_reqheight(), self._heading.winfo_reqheight()) + 6
        height += self._cells[0].winfo_reqheight() * 6 + 12 + 18
        height += day_heading.winfo_reqheight()
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(screen_width, width), min(screen_height, height)
        x = max(0, min(self.winfo_rootx(), screen_width - width))
        y = self.winfo_rooty() + self.winfo_height()
        if y + height > screen_height:
            y = self.winfo_rooty() - height
        y = max(0, min(y, screen_height - height))
        self.popup.geometry(f"{width}x{height}+{x}+{y}")
        self.popup.deiconify()
        self.day_buttons[self._selected_date.day].focus_set()

    def _render_month(self):
        self._heading.configure(text=f"{calendar.month_name[self._month]} {self._year}")
        self.previous_button.state(("disabled",) if (self._year, self._month) == (1, 1) else ("!disabled",))
        self.next_button.state(("disabled",) if (self._year, self._month) == (9999, 12) else ("!disabled",))
        values = [day for week in calendar.Calendar(firstweekday=0).monthdayscalendar(self._year, self._month)
                  for day in week]
        values.extend([0] * (42 - len(values)))
        self.day_buttons = {}
        for button, day in zip(self._cells, values):
            selected = bool(day and self._selected_date == date(self._year, self._month, day))
            button.configure(text=str(day) if day else "", takefocus=bool(day),
                             command=lambda value=day: self._choose_day(value))
            button.state(("!disabled",) if day else ("disabled",))
            button.state(("pressed",) if selected else ("!pressed",))
            if day:
                self.day_buttons[day] = button

    def _change_month(self, delta):
        if self.popup is None:
            return "break"
        month_index = (self._year - 1) * 12 + self._month - 1 + delta
        if not 0 <= month_index < 9999 * 12:
            return "break"
        focused = self.focus_get()
        focused_day = next((day for day, button in self.day_buttons.items() if button is focused), None)
        self._year, month_zero = divmod(month_index, 12)
        self._year += 1
        self._month = month_zero + 1
        self._render_month()
        if focused_day is not None:
            self.day_buttons[min(focused_day, max(self.day_buttons))].focus_set()
        return "break"

    def _choose_day(self, day):
        if not day or self.popup is None:
            return
        history = getattr(self.winfo_toplevel(), "view_history", None)
        if history is not None:
            history.begin()  # A calendar choice is separate from prior typing.
        value = date(self._year, self._month, day).isoformat() + self._time_suffix
        self.variable.set(value)
        self.close_popup()
        if self.on_select is not None:
            self.on_select()

    def _contains(self, widget):
        if widget in (self, self.entry, self.button):
            return True
        return self.popup is not None and (widget is self.popup
                or str(widget).startswith(str(self.popup) + "."))

    @staticmethod
    def _dismiss_outside(event, top):
        current = getattr(top, "_flow_calendar", None)
        if current is not None and not current._contains(event.widget):
            current.close_popup()

    def _defer_focus_check(self, event=None):
        if self._focus_check_id is not None:
            self.after_cancel(self._focus_check_id)
        if self.popup is not None:
            self._focus_check_id = self.after_idle(self._check_focus)

    def _check_focus(self, allow_pointer_grace=True):
        self._focus_check_id = None
        if self.popup is None:
            return
        try:
            focused = self.focus_get()
            if focused is None and allow_pointer_grace:
                if self._contains(self.winfo_containing(*self.winfo_pointerxy())):
                    self._focus_check_id = self.after(60, lambda: self._check_focus(False))
                    return
            if not self._contains(focused):
                self.close_popup()
        except tk.TclError:
            pass

    def _escape(self, event=None):
        if self.popup is None:
            # Let the application's Escape binding leave fullscreen.
            return None
        self.close_popup()
        return "break"

    def _popup_unmapped(self, event):
        if event.widget is self.popup:
            self.close_popup()

    def _destroyed(self, event):
        if event.widget is self:
            if self._size_after_id is not None:
                self.after_cancel(self._size_after_id)
                self._size_after_id = None
            self.close_popup()

    def close_popup(self):
        if self._focus_check_id is not None:
            try:
                self.after_cancel(self._focus_check_id)
            except tk.TclError:
                pass
            self._focus_check_id = None
        popup, self.popup = self.popup, None
        try:
            top = self.winfo_toplevel()
            if getattr(top, "_flow_calendar", None) is self:
                top._flow_calendar = None
            if popup is not None:
                focused = self.focus_get()
                if (focused is popup or str(focused).startswith(str(popup) + ".")) and self.entry.winfo_viewable():
                    # Return X focus before destroying its focused window.
                    # Do not reclaim focus after a transfer to another app.
                    self.entry.focus_force()
                popup.destroy()
        except tk.TclError:
            pass
        self.day_buttons = {}
        self._cells = []
        self._heading = None
        self.previous_button = self.next_button = None
        return "break"
