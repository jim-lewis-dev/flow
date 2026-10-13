"""An optional, read-only map of the flows visible in the Flow Ledger.

This view owns only its display settings and node positions. It derives every
event from the normal public flow records; it neither saves flows nor invents
balances from a filtered subset of the ledger.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
import math
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .calendar_widget import CalendarEntry
from .core import (decimal_sum, format_quantity, ledger_balance_at,
                   normalize_date, parse_quantity)
from .flow_motion import flow_direction, grouped_events, pulse_radius, select_events
from .graphing import normalize_graph_date
from .table_selection import enable_multi_selection


BG = "#f2f5f2"
CARD = "#ffffff"
INK = "#18382f"
MUTED = "#6d8179"
TEAL = "#167d67"
LINE = "#dce5df"
FONT = "DejaVu Sans"
FLOW_COLORS = {"Complete": "#16836c", "Pending": "#d38a25", "Planned": "#497fbb"}
BASIS = {
    "Confirmed": ("Complete",),
    "Including Pending": ("Complete", "Pending"),
    "Including Planned": ("Complete", "Pending", "Planned"),
}
MAX_SPEED = 5
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
MAP_HINT = ("Drag nodes · Drag background or scroll to pan · Ctrl-scroll to zoom"
            " · Double-click background to fit · Click pulses to edit")


class ExperimentalView(ttk.Frame):
    """Account nodes, directed edges, and quantity-sized moving flow pulses.

    ``get_flows`` supplies exactly the currently visible Flow Ledger records.
    Copying those records prevents this view from mutating its input. UI state
    can be included in session undo/redo through get_state()/put_state().
    """

    def __init__(self, master, repo, get_flows: Callable[[], list[dict]],
                 on_select_flow: Callable[[str], None],
                 on_view_change: Callable[[dict, str], None] | None = None):
        super().__init__(master)
        self.repo = repo
        self.get_flows = get_flows
        self.on_select_flow = on_select_flow
        self.on_view_change = on_view_change
        self.from_var = tk.StringVar(self)
        self.through_var = tk.StringVar(self)
        self.units_var = tk.StringVar(self)
        self.basis_var = tk.StringVar(self, "Confirmed")
        # Integer playback rates: zero pauses; one is the original 1 s/step.
        self.speed_var = tk.IntVar(self, 1)
        self.speed_label_var = tk.StringVar(self, "1×")
        self.progress_var = tk.StringVar(self, "No flows selected")
        self.message_var = tk.StringVar(self, "")
        self.hover_var = tk.StringVar(self, MAP_HINT)
        self._initialized_dates = False
        self._all_visible: list[dict] = []
        self._events: list[dict] = []
        self._groups: list[tuple[str, list[dict]]] = []
        self._directed_pairs: set[tuple[str, str]] = set()
        self._max_amount = 1
        self._invalid_message = ""
        self._index = -1
        self._progress = 0.0
        self._stepped = False
        self._playing = False
        self._after_id = None
        self._started_at = 0.0
        self._positions: dict[str, tuple[float, float]] = {}
        self._drag_name: str | None = None
        self._drag_before: dict | None = None
        self._pan_before: dict | None = None
        self._ignore_pointer_up = False
        self._node_items: dict[int, str] = {}
        self._pulse_items: dict[int, dict] = {}
        self._balance_flows: list[dict] = []
        self._account_anchors: dict[tuple[str, str], dict] = {}
        self._hover_node_cache: dict[tuple[str, str, int, str], str] = {}
        self._hover_popup: tk.Toplevel | None = None
        self._hover_label: tk.Label | None = None
        self._hover_after_id: str | None = None
        self._hover_target: tuple[str, str] | None = None
        self._hover_text = ""
        self._hover_pointer = (0, 0)
        self._scene_width = 1000
        self._scene_height = 510
        self._zoom = 1.0
        self._history_state: dict | None = None
        self._speed_drag_before: dict | None = None
        self._setting_dates = False
        self._date_after_id: str | None = None
        self._date_before: dict | None = None
        self._build()
        for variable in (self.from_var, self.through_var):
            variable.trace_add("write", self._date_changed)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        controls = ttk.Frame(self, style="Card.TFrame", padding=(16, 12))
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for column in (0, 1):
            controls.columnconfigure(column, weight=1)
        ttk.Label(controls, text="Account Flow Map", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(controls, text="Uses current Flow Ledger filters", style="CardMuted.TLabel").grid(
            row=0, column=3, columnspan=3, sticky="e", pady=(0, 8))
        labels = (("FROM", 0), ("THROUGH", 1), ("UNITS", 2),
                  ("STATUS BASIS", 3), ("SPEED", 4))
        for title, column in labels:
            ttk.Label(controls, text=title, style="Field.TLabel").grid(
                row=1, column=column, sticky="w", padx=(0, 12))
        self.from_entry = CalendarEntry(controls, variable=self.from_var, width=14,
                                        on_select=self.update)
        self.from_entry.grid(row=2, column=0, sticky="ew", padx=(0, 12))
        self.through_entry = CalendarEntry(controls, variable=self.through_var, width=14,
                                           on_select=self.update)
        self.through_entry.grid(row=2, column=1, sticky="ew", padx=(0, 12))
        self.units_combo = ttk.Combobox(controls, textvariable=self.units_var,
                                        state="readonly", width=12)
        self.units_combo.grid(row=2, column=2, sticky="ew", padx=(0, 12))
        self.basis_combo = ttk.Combobox(controls, textvariable=self.basis_var,
                                        values=tuple(BASIS), state="readonly", width=19)
        self.basis_combo.grid(row=2, column=3, sticky="ew", padx=(0, 12))
        self.speed_scale = tk.Scale(controls, from_=0, to=MAX_SPEED, resolution=1,
                                    orient="horizontal", variable=self.speed_var,
                                    command=self._speed_changed, showvalue=False,
                                    tickinterval=1, sliderlength=17,
                                    bg=CARD, fg=INK, troughcolor=LINE,
                                    activebackground=TEAL, bd=0,
                                    highlightthickness=0, length=130)
        self.speed_scale.grid(row=2, column=4, sticky="ew", padx=(0, 3))
        ttk.Label(controls, textvariable=self.speed_label_var,
                  style="Card.TLabel", width=3).grid(row=2, column=5, sticky="w", padx=(0, 12))
        self.units_combo.bind("<<ComboboxSelected>>", lambda event: self._control_changed("Choose unit"))
        self.basis_combo.bind("<<ComboboxSelected>>", lambda event: self._control_changed("Choose status basis"))
        self.speed_scale.bind("<ButtonPress-1>", self._speed_change_started)
        self.speed_scale.bind("<ButtonRelease-1>", self._speed_change_finished)
        self.speed_scale.bind("<KeyPress>", self._speed_change_started)
        self.speed_scale.bind("<KeyRelease>", self._speed_change_finished)
        for entry in (self.from_entry.entry, self.through_entry.entry):
            entry.bind("<Return>", lambda event: self.update())

        playback = ttk.Frame(self, style="Card.TFrame", padding=(12, 7))
        playback.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        playback.columnconfigure(5, weight=1)
        self.start_button = ttk.Button(playback, text="|< Start", style="Small.TButton",
                                       command=self.jump_start)
        self.start_button.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.previous_button = ttk.Button(playback, text="< Step", style="Small.TButton",
                                          command=self.previous)
        self.previous_button.grid(row=0, column=1, sticky="w", padx=(0, 5))
        self.play_button = ttk.Button(playback, text="Play", style="Small.TButton",
                                      command=self.toggle_play)
        self.play_button.grid(row=0, column=2, sticky="w", padx=(0, 5))
        self.next_button = ttk.Button(playback, text="Step >", style="Small.TButton",
                                      command=self.next)
        self.next_button.grid(row=0, column=3, sticky="w", padx=(0, 5))
        self.end_button = ttk.Button(playback, text="End >|", style="Small.TButton",
                                     command=self.jump_end)
        self.end_button.grid(row=0, column=4, sticky="w", padx=(0, 13))
        ttk.Label(playback, textvariable=self.progress_var, style="Card.TLabel",
                  font=(FONT, 10, "bold")).grid(row=0, column=5, sticky="w")
        self.message_label = ttk.Label(playback, textvariable=self.message_var,
                                       style="CardMuted.TLabel", wraplength=1100)
        self.message_label.grid(row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))
        self.message_label.grid_remove()

        scene = ttk.Frame(self, style="Card.TFrame")
        scene.grid(row=2, column=0, sticky="nsew")
        scene.columnconfigure(0, weight=1)
        scene.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(scene, bg=CARD, highlightthickness=0, relief="flat",
                                xscrollincrement=1, yscrollincrement=1)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xbar = ttk.Scrollbar(scene, orient="horizontal",
                             command=lambda *args: self._scroll_view("x", *args))
        ybar = ttk.Scrollbar(scene, orient="vertical",
                             command=lambda *args: self._scroll_view("y", *args))
        self.xbar, self.ybar = xbar, ybar
        xbar.grid(row=1, column=0, sticky="ew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<ButtonPress-1>", self._pointer_down)
        self.canvas.bind("<B1-Motion>", self._pointer_drag)
        self.canvas.bind("<ButtonRelease-1>", self._pointer_up)
        self.canvas.bind("<Double-Button-1>", self._fit_from_pointer)
        self.canvas.bind("<Motion>", self._pointer_hover)
        self.canvas.bind("<Leave>", lambda event: self.hide_hover())
        for sequence in ("<MouseWheel>", "<Shift-MouseWheel>", "<Control-MouseWheel>"):
            self.canvas.bind(sequence, self._scroll_canvas)
        for button in (4, 5):
            for prefix in ("", "Shift-", "Control-"):
                self.canvas.bind(f"<{prefix}Button-{button}>",
                                 lambda event, number=button:
                                     self._scroll_canvas(event, button=number))
        # Some Tk builds accept native horizontal wheel buttons; others only
        # expose Shift plus the vertical wheel.
        for button in (6, 7):
            try:
                self.canvas.bind(f"<Button-{button}>",
                                 lambda event, number=button:
                                     self._scroll_canvas(event, button=number))
            except tk.TclError:
                pass

        detail = ttk.Frame(self, style="Card.TFrame", padding=(12, 7))
        detail.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        detail.columnconfigure(0, weight=1)
        ttk.Label(detail, textvariable=self.hover_var, style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 5))
        self.flow_list = ttk.Treeview(detail, height=3, show="headings",
                                      selectmode="browse", columns=("from", "to", "quantity", "status", "name"))
        for key, title, weight in (("from", "From", 2), ("to", "To", 2),
                                   ("quantity", "Quantity", 1), ("status", "Status", 1),
                                   ("name", "Name", 3)):
            self.flow_list.heading(key, text=title)
            self.flow_list.column(key, width=weight * 90, minwidth=70, stretch=True,
                                  anchor="e" if key == "quantity" else
                                         "center" if key == "status" else "w")
        self.flow_list.grid(row=1, column=0, sticky="ew")
        flow_scroll = ttk.Scrollbar(detail, orient="vertical", command=self.flow_list.yview)
        flow_scroll.grid(row=1, column=1, sticky="ns")
        self.flow_list.configure(yscrollcommand=flow_scroll.set)
        self.flow_list.bind("<Double-1>", self._open_detail)
        self.flow_list.bind("<Return>", self._open_detail)
        enable_multi_selection(self.flow_list)

    def get_state(self) -> dict:
        """Return view-only settings for the app's session undo/redo."""
        return {
            "from": self.from_var.get(), "through": self.through_var.get(),
            "units": self.units_var.get(), "basis": self.basis_var.get(),
            "speed": self.speed_var.get(), "index": self._index,
            "positions": {name: [x, y] for name, (x, y) in self._positions.items()},
            "zoom": self._zoom,
            "viewport": [self.canvas.canvasx(0) / self._zoom,
                         self.canvas.canvasy(0) / self._zoom],
        }

    def put_state(self, state: dict) -> None:
        self._cancel_date_update()
        current = self.get_state()
        camera_only = all(current[key] == state.get(key, current[key]) for key in
                          ("from", "through", "units", "basis", "speed", "positions"))
        if camera_only:
            # Playback can advance between a wheel gesture and Undo. Restoring
            # the camera must leave that live timestamp and pulse position alone.
            self.hide_hover()
            self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(state.get("zoom", 1))))
            self._draw_scene(viewport=state.get("viewport", (0, 0)))
            self._history_state = self.get_state()
            return
        self.pause()
        self._setting_dates = True
        try:
            self.from_var.set(state.get("from", ""))
            self.through_var.set(state.get("through", ""))
        finally:
            self._setting_dates = False
        self.units_var.set(state.get("units", ""))
        self.basis_var.set(state.get("basis", "Confirmed"))
        self.speed_var.set(max(0, min(MAX_SPEED, int(state.get("speed", 1)))))
        self.speed_label_var.set(f"{self.speed_var.get()}×")
        self._positions = {name: (float(pos[0]), float(pos[1]))
                           for name, pos in state.get("positions", {}).items()}
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(state.get("zoom", 1))))
        self._initialized_dates = True
        self._load_data(reset_index=True)
        self._index = min(max(-1, int(state.get("index", -1))), len(self._groups) - 1)
        self._progress = 0.55 if self._index >= 0 else 0.0
        self._stepped = self._index >= 0
        self._draw_scene(viewport=state.get("viewport", (0, 0)))
        self._update_progress()
        self._history_state = self.get_state()

    def _notify(self, before: dict | None, label: str) -> None:
        after = self.get_state()
        self._history_state = after
        if before is not None and before != after and self.on_view_change is not None:
            self.on_view_change(before, label)

    def refresh(self) -> None:
        """Re-read current ledger filter results; preserve the current timestamp."""
        if self._date_after_id is not None:
            self.update()
        self.pause()
        self._load_data(reset_index=False)
        self._history_state = self.get_state()

    def update(self) -> None:
        """Apply dates immediately; invalid input clears stale animation."""
        before = self._date_before or self._history_state or self.get_state()
        self._cancel_date_update()
        self._load_data(reset_index=True)
        self._notify(before, "Change flow map dates")

    def _cancel_date_update(self) -> None:
        if self._date_after_id is not None:
            self.after_cancel(self._date_after_id)
            self._date_after_id = None
        self._date_before = None

    def _date_changed(self, *args) -> None:
        if self._setting_dates:
            return
        if self._date_before is None:
            self._date_before = self._history_state or self.get_state()
        if self._date_after_id is not None:
            self.after_cancel(self._date_after_id)
        self._date_after_id = self.after(250, self.update)

    def _control_changed(self, label: str) -> None:
        before = self._date_before or self._history_state or self.get_state()
        self._cancel_date_update()
        self._load_data(reset_index=True)
        self._notify(before, label)

    def _speed_change_started(self, event=None) -> None:
        if self._speed_drag_before is None:
            self._speed_drag_before = self.get_state()

    def _speed_changed(self, value=None) -> None:
        """Apply the slider immediately; record one undo action on release."""
        speed = self.speed_var.get()
        self.speed_label_var.set(f"{speed}×")
        if speed == 0:
            self.pause()
        elif self._playing:
            # Preserve the pulse's position when the rate changes mid-flight.
            self._started_at = time.monotonic() - self._progress * self._duration() / 1000
        self._update_progress()

    def _speed_change_finished(self, event=None) -> None:
        before = self._speed_drag_before
        self._speed_drag_before = None
        if before is not None:
            self._notify(before, "Change flow map speed")

    def _load_data(self, *, reset_index: bool) -> bool:
        visible = [dict(flow) for flow in self.get_flows()]
        if not self._initialized_dates:
            days = sorted(flow["date"][:10] for flow in visible)
            self._setting_dates = True
            try:
                self.from_var.set(days[0] if days else date.today().isoformat())
                self.through_var.set(days[-1] if days else date.today().isoformat())
            finally:
                self._setting_dates = False
            self._initialized_dates = True
        units = sorted({flow["units"] for flow in visible})
        self.units_combo.configure(values=units)
        if self.units_var.get() not in units:
            self.units_var.set("USD" if "USD" in units else (units[0] if units else ""))
        try:
            start = normalize_graph_date(self.from_var.get())
            end = normalize_graph_date(self.through_var.get())
            if normalize_date(start) > normalize_date(end):
                raise ValueError("From must be on or before Through.")
            if self.basis_var.get() not in BASIS:
                raise ValueError("Choose a status basis.")
            previous_at = (self._groups[self._index][0]
                           if not reset_index and 0 <= self._index < len(self._groups) else None)
            chosen = select_events(visible, start, end, self.units_var.get(),
                                   BASIS[self.basis_var.get()]) if self.units_var.get() else []
            groups = grouped_events(chosen)
        except ValueError as error:
            self.pause()
            self.hide_hover()
            self._events = []
            self._groups = []
            self._index = -1
            self._invalid_message = str(error)
            self.message_var.set(str(error))
            self.message_label.grid()
            self._draw_scene()
            self._update_progress()
            return False
        self.pause()
        self.hide_hover()
        self._all_visible = visible
        self._events = chosen
        self._groups = groups
        self._balance_flows = self.repo.list_flows()
        self._account_anchors = {
            (account["name"].casefold(), account["units"]): account
            for account in self.repo.list_accounts()
        }
        self._hover_node_cache.clear()
        self._directed_pairs = {flow_direction(flow) for flow in chosen}
        self._max_amount = max((parse_quantity(flow["amount"], allow_negative=True).copy_abs()
                                for flow in chosen), default=Decimal(0))
        self._invalid_message = ""
        self._index = (next((i for i, (at, _) in enumerate(groups) if at == previous_at), -1)
                       if not reset_index else -1)
        self._progress = 0.55 if self._index >= 0 else 0.0
        self._stepped = self._index >= 0
        self.message_var.set("")
        self.message_label.grid_remove()
        self._draw_scene()
        self._update_progress()
        return True

    def _scene_dimensions(self, number: int) -> tuple[int, int]:
        # Use the containing frame so showing a scrollbar does not change the
        # map's world geometry underneath an anchored zoom or node drag.
        width = max(700, self.canvas.master.winfo_width() - 4)
        height = max(280, self.canvas.master.winfo_height() - 4)
        # Expand a scrollable drawing surface instead of hiding crowded nodes.
        radius = max(0, number - 1) * 112 / (2 * math.pi) + 110
        side = int(2 * radius)
        return max(width, side), max(height, side)

    def _auto_layout(self, names: list[str]) -> None:
        count = len(names)
        if not count:
            return
        degree = Counter()
        for flow in self._events:
            degree[flow["from_account"]] += 1
            degree[flow["to_account"]] += 1
        ordered = sorted(names, key=lambda name: (-degree[name], name.casefold(), name))
        hub = ordered.pop(0) if count >= 7 and degree[ordered[0]] >= max(3, count // 3) else None
        if hub is not None:
            self._positions.setdefault(hub, (0.5, 0.5))
        if count == 1:
            self._positions.setdefault(names[0], (0.5, 0.5))
            return
        if count == 2:
            self._positions.setdefault(ordered[0], (0.24, 0.47))
            self._positions.setdefault(ordered[1], (0.76, 0.47))
            return
        for i, name in enumerate(ordered):
            angle = -math.pi / 2 + 2 * math.pi * i / len(ordered)
            self._positions.setdefault(name, (0.5 + 0.39 * math.cos(angle),
                                               0.48 + 0.31 * math.sin(angle)))

    def _canvas_resized(self, event=None) -> None:
        self._draw_scene()

    def _scroll_canvas(self, event, *, button: int | None = None) -> str:
        """Pan with the wheel, or zoom about the pointer with Ctrl held."""
        self.hide_hover()
        before = self.get_state()
        horizontal = button in (6, 7) or bool(getattr(event, "state", 0) & 1)
        if button in (4, 5, 6, 7):
            distance = -40 if button in (4, 6) else 40
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return "break"
            # Windows uses 120 per notch; macOS uses small trackpad deltas.
            system = self.tk.call("tk", "windowingsystem")
            divisor = 120 if system == "win32" or abs(delta) >= 120 else 1
            distance = -round(delta * 40 / divisor)
        if getattr(event, "state", 0) & 4:
            self._zoom_at(1.2 ** (-distance / 40), event.x, event.y)
            self._notify(before, "Zoom flow map")
            return "break"
        if horizontal:
            self.canvas.xview_scroll(distance, "units")
        else:
            self.canvas.yview_scroll(distance, "units")
        self._notify(before, "Pan flow map")
        return "break"

    def _scroll_view(self, axis: str, *args) -> None:
        before = self.get_state()
        self.hide_hover()
        (self.canvas.xview if axis == "x" else self.canvas.yview)(*args)
        self._notify(before, "Pan flow map")

    def _zoom_at(self, factor: float, x: int, y: int) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        if math.isclose(zoom, self._zoom):
            return
        world_x = self.canvas.canvasx(x) / self._zoom
        world_y = self.canvas.canvasy(y) / self._zoom
        self._zoom = zoom
        self._draw_scene(viewport=(world_x - x / zoom, world_y - y / zoom))

    def _fit_from_pointer(self, event) -> str | None:
        if self._item_at(event) is not None:
            return None
        before = self.get_state()
        self.hide_hover()
        self._pan_before = None
        self._ignore_pointer_up = True
        self.canvas.configure(cursor="")
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM,
            self.canvas.winfo_width() / self._scene_width,
            self.canvas.winfo_height() / self._scene_height))
        self._draw_scene(viewport=(
            (self._scene_width - self.canvas.winfo_width() / self._zoom) / 2,
            (self._scene_height - self.canvas.winfo_height() / self._zoom) / 2))
        self._notify(before, "Fit flow map")
        return "break"

    def _canvas_font(self, size: int, *, bold: bool = False) -> tuple:
        font = (FONT, max(6, round(size * self._zoom)))
        return font + ("bold",) if bold else font

    def _segment(self, source: str, target: str, directed_pairs: set) -> tuple[float, ...]:
        x1, y1 = self._xy(source)
        x2, y2 = self._xy(target)
        dx, dy = x2 - x1, y2 - y1
        distance = max(1, math.hypot(dx, dy))
        px, py = -dy / distance, dx / distance
        lane = 12 if (target, source) in directed_pairs else 0
        margin = min(40, distance * 0.3)
        return (x1 + dx * margin / distance + px * lane,
                y1 + dy * margin / distance + py * lane,
                x2 - dx * margin / distance + px * lane,
                y2 - dy * margin / distance + py * lane)

    def _xy(self, name: str) -> tuple[float, float]:
        x, y = self._positions[name]
        return x * self._scene_width, y * self._scene_height

    def _draw_scene(self, *, viewport=None) -> None:
        canvas = self.canvas
        if viewport is None:
            viewport = (canvas.canvasx(0) / self._zoom,
                        canvas.canvasy(0) / self._zoom)
        canvas.delete("all")
        self._node_items.clear()
        self._pulse_items.clear()
        names = sorted({name for flow in self._events
                        for name in (flow["from_account"], flow["to_account"])},
                       key=lambda s: (s.casefold(), s))
        self._scene_width, self._scene_height = self._scene_dimensions(len(names))
        width, height = self._scene_width * self._zoom, self._scene_height * self._zoom
        left, top = viewport[0] * self._zoom, viewport[1] * self._zoom
        region = (min(0, left), min(0, top),
                  max(width, left + canvas.winfo_width()),
                  max(height, top + canvas.winfo_height()))
        canvas.configure(scrollregion=region)
        canvas.xview_moveto((left - region[0]) / (region[2] - region[0]))
        canvas.yview_moveto((top - region[1]) / (region[3] - region[1]))
        # Scrollbars are useful for crowded maps, not for a two-account line.
        for bar, needed in ((self.xbar, width > canvas.winfo_width()),
                            (self.ybar, height > canvas.winfo_height())):
            (bar.grid if needed else bar.grid_remove)()
        if not names:
            canvas.create_text(self._scene_width / 2, self._scene_height / 2,
                               text=(self._invalid_message or
                                     "No flows match this period, unit, and status basis."),
                               fill=MUTED, font=self._canvas_font(11))
            canvas.scale("all", 0, 0, self._zoom, self._zoom)
            self._fill_details()
            return
        self._auto_layout(names)
        legend_x = 20
        for status in BASIS[self.basis_var.get()]:
            canvas.create_oval(legend_x, 18, legend_x + 10, 28,
                               fill=FLOW_COLORS[status], outline="")
            canvas.create_text(legend_x + 16, 23, text=status, anchor="w",
                               fill=MUTED, font=self._canvas_font(9))
            legend_x += 26 + len(status) * 7
        transfer_pairs = {flow_direction(flow) for flow in self._events
                          if parse_quantity(flow["amount"], allow_negative=True)}
        for source, target in sorted(self._directed_pairs):
            if source == target:
                x, y = self._xy(source)
                cx, cy, radius = x + 48, y - 42, 45
                canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius,
                                  start=242, extent=316, style="arc", outline="#b3c8bd",
                                  width=2, tags=("edge",))
                end_angle = math.radians(242 + 316)
                end_x = cx + radius * math.cos(end_angle)
                end_y = cy - radius * math.sin(end_angle)
                if (source, target) in transfer_pairs:
                    canvas.create_line(end_x - 4, end_y - 13, end_x, end_y,
                                       fill="#b3c8bd", width=2, arrow="last",
                                       arrowshape=(9, 11, 4), tags=("edge",))
                continue
            segment = self._segment(source, target, self._directed_pairs)
            canvas.create_line(*segment, fill="#b3c8bd", width=2,
                               arrow="last" if (source, target) in transfer_pairs else "",
                               arrowshape=(9, 11, 4), tags=("edge",))
        for name in names:
            x, y = self._xy(name)
            node = canvas.create_oval(x - 26, y - 26, x + 26, y + 26,
                                      fill="#dbefe5", outline=TEAL, width=2,
                                      tags=("node",))
            self._node_items[node] = name
            icon = canvas.create_text(x, y, text=name[:1].upper(), fill=INK,
                                      font=self._canvas_font(15, bold=True), tags=("node",))
            self._node_items[icon] = name
            label = canvas.create_text(x, y + 43, text=name, width=140 * self._zoom,
                                       fill=INK, font=self._canvas_font(9, bold=True),
                                       justify="center", tags=("node",))
            self._node_items[label] = name
        canvas.scale("all", 0, 0, self._zoom, self._zoom)
        self._render_pulses()
        self._fill_details()

    def _render_pulses(self) -> None:
        canvas = self.canvas
        canvas.delete("moving_flow")
        self._pulse_items.clear()
        if not (0 <= self._index < len(self._groups)):
            return
        flows = self._groups[self._index][1]
        counts = Counter(flow_direction(flow) for flow in flows)
        ordinal = Counter()
        for flow in flows:
            quantity = parse_quantity(flow["amount"], allow_negative=True)
            key = flow_direction(flow)
            number = ordinal[key]
            ordinal[key] += 1
            # Parallel lanes keep simultaneous flows on one route distinct.
            spread = max(-90, min(90, (number - (counts[key] - 1) / 2) * 48))
            # A zero flow marks its timestamp without depicting a transfer.
            progress = self._progress if quantity else 0.5
            if key[0] == key[1]:
                node_x, node_y = self._xy(key[0])
                theta = math.radians(242 + 316 * progress)
                radius = max(20, 45 + spread * 0.3)
                x = node_x + 48 + radius * math.cos(theta)
                y = node_y - 42 - radius * math.sin(theta)
            else:
                x1, y1, x2, y2 = self._segment(*key, self._directed_pairs)
                distance = max(1, math.hypot(x2 - x1, y2 - y1))
                x = x1 + (x2 - x1) * progress - (y2 - y1) / distance * spread
                y = y1 + (y2 - y1) * progress + (x2 - x1) / distance * spread
            radius = pulse_radius(flow["amount"], self._max_amount)
            color = FLOW_COLORS[flow["status"]]
            oval = canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                      fill=color if quantity else BG,
                                      outline="white" if quantity else color, width=2,
                                      tags=("moving_flow",))
            self._pulse_items[oval] = flow
            if len(flows) <= 8:
                label = canvas.create_text(x, y - radius - 10,
                                           text=f"{format_quantity(flow['amount'])} {flow['units']}",
                                           fill=INK, font=self._canvas_font(9, bold=True),
                                           tags=("moving_flow",))
                self._pulse_items[label] = flow
        canvas.scale("moving_flow", 0, 0, self._zoom, self._zoom)
        canvas.tag_raise("node")
        if self._hover_target is not None:
            self._refresh_hover_pointer()

    def _fill_details(self) -> None:
        selected, focused = self.flow_list.selection(), self.flow_list.focus()
        self.flow_list.delete(*self.flow_list.get_children())
        if 0 <= self._index < len(self._groups):
            for flow in self._groups[self._index][1]:
                self.flow_list.insert("", "end", iid=flow["flow_id"], values=(
                    flow["from_account"], flow["to_account"],
                    f"{format_quantity(flow['amount'])} {flow['units']}",
                    flow["status"], flow["name"]))
        selected = [fid for fid in selected if self.flow_list.exists(fid)]
        self.flow_list.selection_set(selected)
        self.flow_list.focus(focused if focused in selected else selected[0] if selected else "")

    def _update_progress(self) -> None:
        total = len(self._groups)
        if not total:
            self.progress_var.set("No flows selected")
        elif self._index < 0:
            self.progress_var.set(f"Ready · 0 of {total} times · {len(self._events)} flows")
        else:
            stamp, simultaneous = self._groups[self._index]
            self.progress_var.set(f"{stamp} · {self._index + 1} of {total} times"
                                  f" · {len(simultaneous)} flow{'s' if len(simultaneous) != 1 else ''}")
        self.start_button.configure(state="normal" if total else "disabled")
        self.previous_button.configure(state="normal" if self._index >= 0 else "disabled")
        self.next_button.configure(state="normal" if self._index < total - 1 else "disabled")
        self.end_button.configure(state="normal" if total else "disabled")
        self.play_button.configure(state="normal" if total and self.speed_var.get() else "disabled",
                                   text="Pause" if self._playing else "Play")

    def jump_start(self) -> None:
        """Show the first timestamp with its pulses at the sources."""
        self.pause()
        if self._groups:
            self._index = 0
            self._progress = 0.0
            self._stepped = True
            self._render_pulses()
            self._fill_details()
            self._update_progress()
        self._history_state = self.get_state()

    def jump_end(self) -> None:
        """Show the last timestamp with its pulses at the destinations."""
        self.pause()
        if self._groups:
            self._index = len(self._groups) - 1
            self._progress = 1.0
            self._stepped = True
            self._render_pulses()
            self._fill_details()
            self._update_progress()
        self._history_state = self.get_state()

    def next(self) -> None:
        self.pause()
        if self._index < len(self._groups) - 1:
            self._index += 1
            self._progress = 0.55
            self._stepped = True
            self._render_pulses()
            self._fill_details()
            self._update_progress()
        self._history_state = self.get_state()

    def previous(self) -> None:
        self.pause()
        if self._index >= 0:
            self._index -= 1
            self._progress = 0.55 if self._index >= 0 else 0.0
            self._stepped = self._index >= 0
            self._render_pulses()
            self._fill_details()
            self._update_progress()
        self._history_state = self.get_state()

    def _duration(self) -> float:
        speed = self.speed_var.get()
        return 1000 / speed if speed else float("inf")

    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
            return
        if not self._groups or self.speed_var.get() == 0:
            return
        if self._index < 0 or (self._index == len(self._groups) - 1 and self._progress >= 1):
            self._index = 0
            self._progress = 0
        elif self._stepped:
            self._progress = 0
        self._stepped = False
        self._playing = True
        self._started_at = time.monotonic() - self._progress * self._duration() / 1000
        self._fill_details()
        self._update_progress()
        self._tick()

    def _tick(self) -> None:
        self._after_id = None
        if not self._playing or not self.winfo_exists():
            return
        self._progress = min(1.0, (time.monotonic() - self._started_at) * 1000 / self._duration())
        self._render_pulses()
        if self._progress >= 1:
            if self._index == len(self._groups) - 1:
                self.pause()
                return
            self._index += 1
            self._progress = 0.0
            self._started_at = time.monotonic()
            self._fill_details()
            self._update_progress()
        self._after_id = self.after(33, self._tick)

    def pause(self) -> None:
        self._playing = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self._update_progress()
        self._history_state = self.get_state()

    def close(self) -> None:
        self._cancel_date_update()
        self.pause()
        self.hide_hover(destroy=True)
        self.from_entry.close_popup()
        self.through_entry.close_popup()

    def _item_at_position(self, px: int, py: int) -> int | None:
        x, y = self.canvas.canvasx(px), self.canvas.canvasy(py)
        for item in reversed(self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1)):
            if item in self._node_items or item in self._pulse_items:
                return item
        return None

    def _item_at(self, event) -> int | None:
        return self._item_at_position(event.x, event.y)

    def _pointer_down(self, event) -> None:
        self.hide_hover()
        item = self._item_at(event)
        name = self._node_items.get(item)
        if name is not None:
            self._drag_name = name
            self._drag_before = self.get_state()
            self.canvas.configure(cursor="fleur")
        elif item is None:
            self._pan_before = self.get_state()
            self.canvas.scan_mark(event.x, event.y)
            self.canvas.configure(cursor="fleur")

    def _pointer_drag(self, event) -> None:
        if self._drag_name is None:
            if self._pan_before is not None:
                self.hide_hover()
                self.canvas.scan_dragto(event.x, event.y, gain=1)
            return
        self.hide_hover()
        x = self.canvas.canvasx(event.x) / self._zoom
        y = self.canvas.canvasy(event.y) / self._zoom
        self._positions[self._drag_name] = (
            min(0.94, max(0.06, x / self._scene_width)),
            min(0.91, max(0.06, y / self._scene_height)))
        self._draw_scene()

    def _pointer_up(self, event) -> None:
        if self._ignore_pointer_up:
            self._ignore_pointer_up = False
            return
        if self._drag_name is not None:
            name = self._drag_name
            before = self._drag_before
            self._drag_name = None
            self._drag_before = None
            self.canvas.configure(cursor="")
            self._notify(before, f"Move {name} in flow map")
            return
        if self._pan_before is not None:
            before, self._pan_before = self._pan_before, None
            self.canvas.configure(cursor="")
            self._notify(before, "Pan flow map")
            return
        flow = self._pulse_items.get(self._item_at(event))
        if flow is not None:
            self.on_select_flow(flow["flow_id"])

    def _node_hover_text(self, name: str) -> str:
        unit = self.units_var.get()
        key = (name, unit, self._index, self.basis_var.get())
        if key in self._hover_node_cache:
            return self._hover_node_cache[key]
        account = self._account_anchors.get((name.casefold(), unit))
        lines = [f"{name} · {unit}"]
        if account is None or account["opening_amount"] is None:
            lines.append("Starting balance: Unknown")
        else:
            lines.append(f"Starting balance: {format_quantity(account['opening_amount'])} {unit}"
                         f" · {account['opening_at']}")
        if self._index >= 0:
            stamp = self._groups[self._index][0]
            balance = (ledger_balance_at(account, self._balance_flows, stamp,
                                         BASIS[self.basis_var.get()])
                       if account is not None else None)
            amount = "Unknown" if balance is None else f"{format_quantity(balance)} {unit}"
            lines.append(f"Balance after {stamp} ({self.basis_var.get()}): {amount}")
            shown = [flow for _, flows in self._groups[:self._index + 1] for flow in flows]
            scope = "Shown through this step"
        else:
            shown = self._events
            scope = "Shown in selected period"
        movements = [(flow_direction(flow),
                      parse_quantity(flow["amount"], allow_negative=True).copy_abs())
                     for flow in shown]
        incoming = decimal_sum(quantity for (_, target), quantity in movements
                               if target.casefold() == name.casefold())
        outgoing = decimal_sum(quantity for (source, _), quantity in movements
                               if source.casefold() == name.casefold())
        lines.append(f"{scope}: In {format_quantity(incoming)} {unit}"
                     f" · Out {format_quantity(outgoing)} {unit}")
        result = "\n".join(lines)
        self._hover_node_cache[key] = result
        return result

    @staticmethod
    def _flow_hover_text(flow: dict) -> str:
        lines = [flow["name"] or "(Unnamed flow)",
                 f"{normalize_date(flow['date'])} · {flow['status']}",
                 f"{flow['from_account']} → {flow['to_account']}"
                 f" · {format_quantity(flow['amount'])} {flow['units']}"]
        if flow["category"]:
            lines.append(f"Category: {flow['category']}")
        if flow["tags"]:
            lines.append("Tags: " + ", ".join(flow["tags"]))
        notes = " ".join(flow["notes"].split())
        if notes:
            lines.append("Notes: " + (notes[:197] + "…" if len(notes) > 200 else notes))
        return "\n".join(lines)

    def _position_hover(self) -> None:
        if self._hover_popup is None:
            return
        self._hover_popup.update_idletasks()
        width = self._hover_popup.winfo_reqwidth()
        height = self._hover_popup.winfo_reqheight()
        px, py = self._hover_pointer
        x = max(0, min(px + 18, self.winfo_screenwidth() - width))
        y = max(0, min(py + 20, self.winfo_screenheight() - height))
        self._hover_popup.geometry(f"{width}x{height}+{x}+{y}")

    def _show_hover(self, target: tuple[str, str]) -> None:
        self._hover_after_id = None
        if target != self._hover_target or not self.canvas.winfo_viewable():
            return
        if self._hover_popup is None:
            popup = self._hover_popup = tk.Toplevel(self.canvas)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.transient(self.winfo_toplevel())
            popup.configure(bg=TEAL, padx=1, pady=1)
            self._hover_label = tk.Label(popup, bg=CARD, fg=INK, font=(FONT, 10),
                                         justify="left", anchor="w", wraplength=430,
                                         padx=11, pady=8)
            self._hover_label.pack(fill="both", expand=True)
        self._hover_label.configure(text=self._hover_text)
        self._position_hover()
        self._hover_popup.deiconify()

    def hide_hover(self, *, destroy: bool = False) -> None:
        if self._hover_after_id is not None:
            self.after_cancel(self._hover_after_id)
            self._hover_after_id = None
        if self._hover_popup is not None:
            if destroy:
                self._hover_popup.destroy()
                self._hover_popup = None
                self._hover_label = None
            else:
                self._hover_popup.withdraw()
        self._hover_target = None
        self._hover_text = ""
        self.hover_var.set(MAP_HINT)

    def _update_hover_at(self, x: int, y: int, root_x: int, root_y: int) -> None:
        if self._drag_name is not None:
            return
        item = self._item_at_position(x, y)
        flow = self._pulse_items.get(item)
        name = self._node_items.get(item)
        if flow is not None:
            target = ("flow", flow["flow_id"])
            details = self._flow_hover_text(flow)
            brief = (f"{flow['name']} · {flow['status']} · {flow['from_account']} → "
                     f"{flow['to_account']} · {format_quantity(flow['amount'])} {flow['units']}")
        elif name is not None:
            target = ("node", name)
            details = self._node_hover_text(name)
            brief = details.replace("\n", " · ")
        else:
            self.hide_hover()
            return
        self._hover_pointer = (root_x, root_y)
        if target != self._hover_target:
            self.hide_hover()
            self._hover_target = target
            self._hover_after_id = self.after(250, lambda t=target: self._show_hover(t))
        elif details != self._hover_text and self._hover_popup is not None:
            self._hover_label.configure(text=details)
            if self._hover_popup.winfo_viewable():
                self._position_hover()
        self._hover_text = details
        self.hover_var.set(brief)
        if self._hover_popup is not None and self._hover_popup.winfo_viewable():
            self._position_hover()

    def _refresh_hover_pointer(self) -> None:
        px = self.canvas.winfo_pointerx()
        py = self.canvas.winfo_pointery()
        x = px - self.canvas.winfo_rootx()
        y = py - self.canvas.winfo_rooty()
        if 0 <= x < self.canvas.winfo_width() and 0 <= y < self.canvas.winfo_height():
            self._update_hover_at(x, y, px, py)
        else:
            self.hide_hover()

    def _pointer_hover(self, event) -> None:
        self._update_hover_at(event.x, event.y, event.x_root, event.y_root)

    def _open_detail(self, event=None) -> None:
        chosen = self.flow_list.selection()
        if chosen:
            focused = self.flow_list.focus()
            self.on_select_flow(focused if focused in chosen else chosen[0])
