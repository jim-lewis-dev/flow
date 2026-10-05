"""Flow: a local, keyboard-friendly desktop interface for the flow engine.

All entry methods ultimately call Repository.save_flow. Views use the same
balance and timeline functions; no screen stores an independent balance.
"""
from __future__ import annotations

import csv
import json
import io
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .core import (STATUSES, ledger_balance_at, parse_money,
                   format_money, normalize_date)
from .exchange import read_import, preview_import, commit_import, export_flows
from .suggestions import SuggestionPreferences
from .filters import CheckFilter
from .calendar_widget import CalendarEntry
from .graphing import graph_timeline, normalize_graph_date
from .widgets import FilterCombo, ScrollFrame, TreeTooltip
from .ledger_editor import LedgerEditor
from .account_editor import AccountEditor

BG = "#f2f5f2"
CARD = "#ffffff"
INK = "#18382f"
MUTED = "#6d8179"
TEAL = "#167d67"
LINE = "#dce5df"
FONT = "DejaVu Sans"
PALETTE = ["#16836c", "#e7a13b", "#447cbb", "#b95d75", "#7865b8", "#59a6a1", "#aa783e", "#a970ba", "#508640", "#697897"]
SUGGESTION_KINDS = {"from_account": "account", "to_account": "account",
                    "name": "name", "category": "category", "tags": "tag"}


class _NotesPeer(tk.Text):
    """Another view of the inline notes buffer, with no copied draft to lose.

    Tk supplies text peers; tkinter has no constructor for one. Register the
    peer as an ordinary child widget so standard Text methods and destruction
    still work. Text content is shared immediately, even before the next event.
    """
    def __init__(self, master, source, **options):
        self.widgetName = "text"
        self._setup(master, options)
        self._tclCommands = []
        self.tk.call(source._w, "peer", "create", self._w, *self._options(options))


class FlowApp(tk.Tk):
    """Desktop application; the caller owns and closes the repository."""
    suggestion_fields = SUGGESTION_KINDS
    def __init__(self, repo, demo=False):
        super().__init__()
        self.repo = repo
        self.demo = demo
        self.suggestion_preferences = SuggestionPreferences(repo.path)
        self.title(f"Flow {__version__}" + (" · Demo" if demo else ""))
        # Keep a modest restore size. The window manager chooses one monitor
        # for fullscreen, rather than sizing against a multi-monitor desktop.
        self.geometry(f"{max(1120, min(1280, self.winfo_screenwidth()-80))}x{max(700, min(850, self.winfo_screenheight()-100))}")
        self.minsize(1120, 700)
        self._fullscreen_requested = True
        self.attributes("-fullscreen", True)
        self.configure(bg=BG)
        self.current_view = "flows"
        self.editing_id = None
        self.account_editing = None
        self._flow_selection = None
        self._account_selection = None
        self._confirming = False
        self.form_vars = {k: tk.StringVar(self) for k in ("date", "time", "from_account", "to_account", "amount", "status", "name", "category", "tags")}
        self.form_widgets = {}
        self.ledger_rows = {}
        self.sort_column = "date"
        self.sort_reverse = True
        self.status_message = tk.StringVar(self, "Ready")
        self._configure_styles()
        self._build_shell()
        self._build_flows()
        for variable in self.form_vars.values():
            variable.trace_add("write", self._sync_flow_draft)
        self.notes_text.bind("<<Modified>>", self._flow_notes_changed)
        self._build_accounts()
        for variable in self.account_vars.values():
            variable.trace_add("write", self._sync_account_draft)
        self._build_charts()
        self._build_data()
        for entry_class in ("TEntry", "Entry", "TCombobox"):
            self.bind_class(entry_class, "<Control-a>", self._select_all_entry)
        self.bind_class("Text", "<Control-a>", self._select_all_text)
        self.bind("<Control-n>", self._shortcut_new)
        self.bind("<Control-s>", self._shortcut_save)
        self.bind("<Escape>", self.exit_fullscreen)
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.new_flow(focus=False, confirm=False)
        self.new_account(focus=False, confirm=False)
        self.refresh_all()
        self.show_view("flows")
        if self.suggestion_preferences.load_error:
            self._feedback(self.suggestion_preferences.load_error, True)

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), foreground=INK, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Card.TLabel", background=CARD)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
        style.configure("Field.TLabel", background=CARD, foreground=MUTED, font=(FONT, 8, "bold"))
        style.configure("Title.TLabel", font=(FONT, 18, "bold"))
        style.configure("Section.TLabel", font=(FONT, 11, "bold"), background=CARD)
        style.configure("TEntry", fieldbackground="#fbfcfa", bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=(7, 6), insertcolor=INK)
        style.map("TEntry", bordercolor=[("focus", TEAL)])
        style.configure("Ledger.TEntry", padding=(3, 3))
        style.configure("TCombobox", fieldbackground="#fbfcfa", bordercolor=LINE, padding=(6, 6), arrowsize=12)
        style.map("TCombobox", fieldbackground=[("readonly", "#fbfcfa")], selectbackground=[("readonly", "#fbfcfa")], selectforeground=[("readonly", INK)])
        style.configure("TButton", background="#e6eee8", foreground=INK, borderwidth=0, padding=(10, 7), font=(FONT, 9, "bold"))
        style.map("TButton", background=[("active", "#d5e7db")])
        style.configure("Primary.TButton", background=TEAL, foreground="white", padding=(14, 7))
        style.map("Primary.TButton", background=[("active", "#116752")])
        style.configure("Small.TButton", padding=(7, 5))
        style.configure("TCheckbutton", background=BG, font=(FONT, 9))
        style.configure("Card.TCheckbutton", background=CARD)
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK, borderwidth=0, rowheight=max(32, int(float(self.tk.call("tk", "scaling"))*20)), font=(FONT, 9))
        style.configure("Treeview.Heading", background="#edf3ed", foreground=MUTED, relief="flat", padding=(8, 8), font=(FONT, 9, "bold"))
        style.map("Treeview", background=[("selected", "#d4e9df")], foreground=[("selected", INK)])
        style.configure("Vertical.TScrollbar", background="#d6e2da", troughcolor=BG, arrowsize=10)
        style.configure("Horizontal.TScrollbar", background="#d6e2da", troughcolor=BG)
        self.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.option_add("*TCombobox*Listbox.background", "white")

    def _build_shell(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        bar = tk.Frame(self, bg=CARD, padx=20, pady=10)
        bar.grid(row=0, column=0, sticky="ew")
        tk.Label(bar, text="Flow", bg=CARD, fg=TEAL, font=(FONT, 18, "bold")).pack(side="left", padx=(0, 25))
        self.nav_buttons = {}
        for key, title in (("flows", "Flows"), ("accounts", "Accounts"), ("charts", "Graphs"), ("data", "Data")):
            button = tk.Button(bar, text=title, font=(FONT, 10, "bold"), padx=17, pady=8,
                               bg=CARD, fg=MUTED, relief="flat", borderwidth=0,
                               activebackground="#e5efe9", activeforeground=INK,
                               command=lambda k=key: self.show_view(k))
            button.pack(side="left", padx=2)
            self.nav_buttons[key] = button
        context = ttk.Frame(bar, style="Card.TFrame")
        context.pack(side="right")
        if self.demo:
            ttk.Label(context, text="Demo", style="CardMuted.TLabel").pack(side="left", padx=(0, 18))
        self.view_host = ttk.Frame(self, padding=(16, 14, 16, 0))
        self.view_host.grid(row=1, column=0, sticky="nsew")
        self.view_host.rowconfigure(0, weight=1)
        self.view_host.columnconfigure(0, weight=1)
        self.views = {}
        for name in ("flows", "accounts", "charts", "data"):
            self.views[name] = ttk.Frame(self.view_host)
            self.views[name].grid(row=0, column=0, sticky="nsew")
        footer = ttk.Frame(self, padding=(20, 7))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_message, style="Muted.TLabel", font=(FONT, 8), width=1).grid(row=0, column=0, sticky="ew")
        ttk.Label(footer, text="USD  ·  Local", style="Muted.TLabel", font=(FONT, 8)).grid(row=0, column=1, sticky="e")

    def _close_popups(self):
        """Dismiss every transient suggestion, including those outside Flows."""
        active = getattr(self, "_flow_filter_combo", None)
        if active is not None:
            active.close_popup()
        check_filter = getattr(self, "_flow_check_filter", None)
        if check_filter is not None:
            check_filter.close_popup()
        calendar = getattr(self, "_flow_calendar", None)
        if calendar is not None:
            calendar.close_popup()
        for widget in self.form_widgets.values():
            if isinstance(widget, FilterCombo):
                widget.close_popup()
        for attr in ("ledger_tooltip", "account_tooltip"):
            if hasattr(self, attr):
                getattr(self, attr).hide()

    def _prepare_dialog(self):
        # Native dialogs restore the previous focus on close. Never let that
        # be a suggestion field, or its FocusIn would reopen an old dropdown.
        self.focus_set()
        self._close_popups()

    def _flow_snapshot(self):
        return tuple(v.get() for v in self.form_vars.values()) + (self.notes_text.get("1.0", "end-1c"),)

    def _account_snapshot(self):
        values = [v.get() for v in self.account_vars.values()]
        try:
            # Compare instants, so pasting the existing timestamp or adding
            # optional zero seconds does not invent an unsaved change.
            values[2] = normalize_date(self._account_when())
            values[3] = ""
        except ValueError:
            pass  # Invalid input still differs and remains available to fix.
        return tuple(values)

    def _account_when(self):
        raw = self.account_vars["date"].get().strip()
        time_value = self.account_vars["time"].get().strip()
        if len(raw) == 10 and time_value:
            raw += " " + time_value
        return raw

    def _confirm_changes(self, kind):
        """Resolve an unsaved draft before replacing it, never on mere refresh."""
        current = self._flow_snapshot() if kind == "flow" else self._account_snapshot()
        clean = getattr(self, "_clean_" + kind, current)
        if current == clean:
            return True
        self._prepare_dialog()
        self._confirming = True
        try:
            answer = messagebox.askyesnocancel("Unsaved Changes", f"Save changes to this {kind}?", parent=self)
            if answer is None:
                return False
            if answer:
                return self.save_flow() if kind == "flow" else self.save_account()
            return True
        finally:
            self._confirming = False

    def _set_flow_selection(self, flow_id):
        self._flow_selection = flow_id if flow_id and self.ledger.exists(flow_id) else None
        self.ledger.selection_set((self._flow_selection,) if self._flow_selection else ())
        self.ledger.focus(self._flow_selection or "")
        self._show_selection()

    def _set_account_selection(self, name):
        self._account_selection = name if name and self.accounts_tree.exists(name) else None
        self.accounts_tree.selection_set((self._account_selection,) if self._account_selection else ())
        self.accounts_tree.focus(self._account_selection or "")

    @staticmethod
    def _select_all_entry(event):
        event.widget.selection_range(0, "end")
        event.widget.icursor("end")
        return "break"

    @staticmethod
    def _select_all_text(event):
        event.widget.tag_add("sel", "1.0", "end-1c")
        event.widget.mark_set("insert", "end-1c")
        return "break"

    def _shortcut_save(self, event=None):
        if self.grab_current() is not None:
            return "break"
        if self.current_view == "flows":
            self.save_flow()
        elif self.current_view == "accounts":
            self.save_account()
        return "break"

    def _shortcut_new(self, event=None):
        if self.grab_current() is not None:
            return "break"
        if self.current_view == "accounts":
            self.new_account()
        else:
            self.new_flow()
        return "break"

    def exit_fullscreen(self, event=None):
        """Return to the normal, resizable window without changing a draft."""
        if self._fullscreen_requested:
            self._close_popups()
            self.attributes("-fullscreen", False)
            self._fullscreen_requested = False
            return "break"

    def request_close(self):
        if self._confirm_changes("flow") and self._confirm_changes("account"):
            self._close_popups()
            self.destroy()

    def show_view(self, name):
        if name != self.current_view:
            old_view = self.current_view
            if old_view in {"flows", "accounts"}:
                if not self._confirm_changes("flow" if old_view == "flows" else "account"):
                    return False
                if old_view == "flows":
                    self.new_flow(focus=False, confirm=False)
                else:
                    self.new_account(focus=False, confirm=False)
            self.focus_set()
        self._close_popups()
        self.current_view = name
        self.views[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(bg="#e1eee7" if key == name else CARD, fg=TEAL if key == name else MUTED)
        if name == "charts":
            self.draw_chart()
        return True

    @staticmethod
    def _repeat_row_click(event):
        """A rapid second click must select its row, just like a first click."""
        tree = event.widget
        row = tree.identify_row(event.y)
        if row:
            tree.focus_set()
            tree.focus(row)
            tree.selection_set(row)
            return "break"

    def _fit_columns(self, tree, weights):
        """Use the available table width; long cell values remain in row details."""
        def fit(event):
            available = max(100, event.width - 4)
            total = sum(weights.values())
            used = 0
            for index, (key, weight) in enumerate(weights.items()):
                width = available-used if index == len(weights)-1 else int(available*weight/total)
                tree.column(key, width=width, minwidth=20, stretch=False)
                used += width
        tree.bind("<Configure>", fit, add="+")

    def _entry_cell(self, parent, label, variable=None, kind="entry", values=()):
        frame = ttk.Frame(parent, style="Card.TFrame")
        heading = ttk.Frame(frame, style="Card.TFrame")
        heading.pack(fill="x", pady=(0, 5))
        ttk.Label(heading, text=label, style="Field.TLabel").pack(side="left")
        if kind == "suggest":
            widget = FilterCombo(frame, variable=variable, values=values)
            widget.entry.configure(width=1)
        elif kind == "choice":
            widget = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly", width=1)
        elif kind == "notes":
            widget = tk.Text(frame, height=1, width=1, wrap="word", font=(FONT, 10), bg="#fbfcfa", fg=INK,
                             relief="solid", borderwidth=1, highlightthickness=0, padx=7, pady=6)
            widget.bind("<Button-4>", lambda event: self._scroll_notes(event, -1))
            widget.bind("<Button-5>", lambda event: self._scroll_notes(event, 1))
            widget.bind("<MouseWheel>", self._scroll_notes)
        else:
            widget = ttk.Entry(frame, textvariable=variable, width=1)
        widget.pack(fill="x")
        return frame, heading, widget

    def _reflow_cells(self, parent, cells, weights, threshold=1500):
        """One desktop row, two balanced rows when the window is narrower."""
        previous = [None]
        def arrange(event):
            columns = len(cells) if event.width >= threshold else (len(cells)+1)//2
            if previous[0] == columns:
                return
            previous[0] = columns
            for col in range(len(cells)):
                parent.columnconfigure(col, weight=0, minsize=0, uniform="")
            for index, cell in enumerate(cells):
                row, col = divmod(index, columns)
                cell.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=(0, 8 if row == 0 and columns < len(cells) else 0))
                parent.columnconfigure(col, weight=weights[index] if columns == len(cells) else 1, uniform="fields")
        parent.bind("<Configure>", arrange, add="+")

    def _split_flow_date(self, event=None):
        """Keep the Date and Time controls consistent after a full timestamp."""
        raw = self.form_vars["date"].get().strip()
        if len(raw) <= 10:
            return
        try:
            when = normalize_date(raw)
        except ValueError:
            return  # Save reports invalid input without changing the draft.
        self.form_vars["date"].set(when[:10])
        self.form_vars["time"].set(when[11:])

    def _split_account_date(self, event=None):
        """A pasted timestamp replaces Time; a date alone keeps its seconds."""
        raw = self.account_vars["date"].get().strip()
        if len(raw) <= 10:
            return
        try:
            when = normalize_date(raw)
        except ValueError:
            return  # Save leaves invalid input visible for correction.
        self.account_vars["date"].set(when[:10])
        self.account_vars["time"].set("" if when[11:] == "00:00:00" else when[11:])

    def edit_time(self, variable, title="Flow Time"):
        if variable is self.form_vars["time"]:
            self._split_flow_date()
        elif variable is self.account_vars["time"]:
            self._split_account_date()
        self._prepare_dialog()
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Time (optional)", font=(FONT, 11, "bold")).pack(anchor="w")
        value = tk.StringVar(dialog, variable.get())
        entry = ttk.Entry(body, textvariable=value, width=20)
        entry.pack(fill="x", pady=10)
        ttk.Label(body, text="HH:MM or HH:MM:SS · Blank leaves time unspecified.", style="Muted.TLabel").pack(anchor="w")
        error = tk.StringVar(dialog)
        ttk.Label(body, textvariable=error, foreground="#a44545").pack(anchor="w", pady=5)
        def accept():
            raw = value.get().strip()
            try:
                if raw:
                    normalize_date("2026-10-01 " + raw)
                variable.set(raw)
                dialog.destroy()
            except ValueError:
                error.set("Use a valid time, for example 14:30.")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Clear", command=lambda: value.set("")).pack(side="left")
        ttk.Button(buttons, text="Apply", style="Primary.TButton", command=accept).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)
        entry.bind("<Return>", lambda event: accept())
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.grab_set()
        entry.focus_set()

    @staticmethod
    def _scroll_notes(event, direction=None):
        """Keep one-line notes readable using the wheel on each platform."""
        if direction is None:
            direction = -1 if event.delta > 0 else 1
        event.widget.yview_scroll(direction, "units")
        return "break"

    def _close_notes_editor(self, focus=False):
        dialog = getattr(self, "notes_dialog", None)
        if dialog is None:
            return
        self.notes_dialog = None
        if dialog.winfo_exists():
            self.notes_text.mark_set("insert", self.expanded_notes_text.index("insert"))
            self.notes_text.see("insert")
            dialog.destroy()
        if focus:
            self.notes_text.focus_set()

    def edit_notes(self):
        """Expand the current draft; edits already belong to the main form."""
        if getattr(self, "notes_dialog", None) is not None:
            self.notes_dialog.lift()
            self.expanded_notes_text.focus_set()
            return
        self._prepare_dialog()
        dialog = tk.Toplevel(self)
        self.notes_dialog = dialog
        dialog.title("Flow Notes")
        dialog.geometry("650x350")
        dialog.minsize(360, 220)
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        editor = ttk.Frame(body)
        editor.grid(row=0, column=0, sticky="nsew")
        text = _NotesPeer(editor, self.notes_text, wrap="word", font=(FONT, 11),
                          padx=12, pady=12, relief="flat", bg="#fbfcfa", fg=INK)
        self.expanded_notes_text = text
        scrollbar = ttk.Scrollbar(editor, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.mark_set("insert", self.notes_text.index("insert"))
        text.see("insert")
        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="Close", command=lambda: self._close_notes_editor(focus=True)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._close_notes_editor(focus=True))
        dialog.bind("<Escape>", lambda event: self._close_notes_editor(focus=True))
        dialog.bind("<Control-s>", self._shortcut_save)
        text.focus_set()

    def _card(self, parent, **kwargs):
        return ttk.Frame(parent, style="Card.TFrame", padding=16, **kwargs)

    def _field(self, parent, label, variable, row, column=0, colspan=1, kind="entry", values=()):
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=column, columnspan=colspan, sticky="ew", pady=(0, 12), padx=(0 if column == 0 else 9, 0))
        ttk.Label(frame, text=label.upper(), style="Field.TLabel").pack(anchor="w", pady=(0, 5))
        if kind == "suggest":
            widget = FilterCombo(frame, variable=variable, values=values)
            widget.entry.configure(width=1)
        elif kind == "choice":
            widget = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly", width=1)
        else:
            widget = ttk.Entry(frame, textvariable=variable, width=1)
        widget.pack(fill="x")
        return widget

    def _build_flows(self):
        page = self.views["flows"]
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        editor = self._card(page)
        self.flow_editor = editor
        editor.grid(row=0, column=0, sticky="ew")
        editor.columnconfigure(0, weight=1)
        header = ttk.Frame(editor, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.form_title = ttk.Label(header, text="New Flow", style="Section.TLabel")
        self.form_title.pack(side="left")
        self.form_feedback = tk.StringVar(self, "From, To, and Amount required")
        ttk.Label(header, textvariable=self.form_feedback, style="CardMuted.TLabel", width=1).pack(side="left", fill="x", expand=True, padx=18)
        self.save_button = ttk.Button(header, text="Save Flow  ·  Ctrl+S", style="Primary.TButton", command=self.save_flow)
        self.save_button.pack(side="right")
        self.cancel_button = ttk.Button(header, text="Cancel", style="Small.TButton", command=lambda: self.new_flow(focus=True, confirm=False))
        self.cancel_button.pack(side="right", padx=8)
        self.new_flow_button = ttk.Button(header, text="New Flow", style="Small.TButton", command=self.new_flow)
        self.new_flow_button.pack(side="right")
        form = ttk.Frame(editor, style="Card.TFrame")
        form.grid(row=1, column=0, sticky="ew")
        cells = []
        for key, label, kind in (("date", "Date", "entry"), ("from_account", "From", "suggest"), ("to_account", "To", "suggest"),
                                 ("amount", "Amount · USD", "entry"), ("status", "Status", "choice"), ("name", "Name", "suggest"),
                                 ("category", "Category", "suggest"), ("tags", "Tags", "suggest"), ("notes", "Notes", "notes")):
            cell, heading, widget = self._entry_cell(form, label, self.form_vars.get(key), kind, STATUSES if key == "status" else ())
            cells.append(cell)
            if key == "notes":
                self.notes_text = widget
                widget.bind("<Tab>", lambda event: (event.widget.tk_focusNext().focus_set(), "break")[1])
                widget.bind("<Shift-Tab>", lambda event: (event.widget.tk_focusPrev().focus_set(), "break")[1])
                self.notes_button = tk.Button(heading, text="Expand", command=self.edit_notes, font=(FONT, 8), bg=CARD, fg=TEAL, relief="flat", borderwidth=0, padx=3, pady=0)
                self.notes_button.pack(side="right")
            else:
                self.form_widgets[key] = widget
            if key in SUGGESTION_KINDS:
                widget.on_remove = lambda value, field=key: self._remove_suggestion(field, value)
                widget.on_accept = lambda field=key: self._accept_suggestion(field)
            if key == "date":
                widget.bind("<FocusOut>", self._split_flow_date, add="+")
                self.time_button = tk.Button(heading, text="Time", command=lambda: self.edit_time(self.form_vars["time"]), font=(FONT, 8), bg=CARD, fg=TEAL, relief="flat", borderwidth=0, padx=3, pady=0)
                self.time_button.pack(side="right")
                self.form_vars["time"].trace_add("write", lambda *args: self.time_button.configure(text=self.form_vars["time"].get() or "Time"))
        self.form_widgets["tags"].multi_value = True
        self.flow_cells = cells
        self._reflow_cells(form, cells, [12, 16, 16, 11, 11, 19, 14, 16, 21])
        metrics = ttk.Frame(page, padding=(2, 12))
        metrics.grid(row=1, column=0, sticky="ew")
        self.metric_vars = []
        for index, label in enumerate(("Complete", "Pending", "Flows")):
            ttk.Label(metrics, text=label, style="Muted.TLabel", font=(FONT, 9)).pack(side="left", padx=(0 if index == 0 else 30, 9))
            value = tk.StringVar(self, "—")
            self.metric_vars.append(value)
            ttk.Label(metrics, textvariable=value, font=(FONT, 12, "bold")).pack(side="left")
        ttk.Label(metrics, text="Current ledger filter", style="Muted.TLabel", font=(FONT, 8)).pack(side="right")
        ledger_card = self._card(page)
        ledger_card.grid(row=2, column=0, sticky="nsew")
        ledger_card.columnconfigure(0, weight=1)
        ledger_card.rowconfigure(2, weight=1)
        top = ttk.Frame(ledger_card, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(top, text="Flow Ledger", style="Section.TLabel").pack(side="left")
        self.search_var = tk.StringVar(self)
        self.search_var.trace_add("write", lambda *args: self.refresh_ledger())
        self.filter_start = tk.StringVar(self)
        self.filter_end = tk.StringVar(self)
        self.ledger_filters = {}
        filters = ttk.Frame(ledger_card, style="Card.TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        filters.columnconfigure(0, weight=1)
        primary = ttk.Frame(filters, style="Card.TFrame")
        primary.grid(row=0, column=0, sticky="ew")
        primary.columnconfigure(1, weight=1)
        ttk.Label(primary, text="Search", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(primary, textvariable=self.search_var, width=18).grid(row=0, column=1, sticky="ew")
        for col, key, label, values in ((2, "Status", "Statuses", STATUSES),
                                        (3, "Category", "Categories", ()),
                                        (4, "Tag", "Tags", ())):
            choice = CheckFilter(primary, label=label, values=values,
                                 on_change=self.refresh_ledger)
            choice.grid(row=0, column=col, padx=(10, 0))
            self.ledger_filters[key] = choice
        ttk.Button(primary, text="Dates…", style="Small.TButton", command=self.edit_date_filters).grid(row=0, column=5, padx=(10, 0))
        self.date_filter_label = ttk.Label(top, text="All Dates", style="CardMuted.TLabel")
        self.date_filter_label.pack(side="right")
        tree_box = ttk.Frame(ledger_card, style="Card.TFrame")
        tree_box.grid(row=2, column=0, sticky="nsew")
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)
        specs = [("date", "Date", 13), ("from_account", "From", 15), ("to_account", "To", 15), ("amount_cents", "Amount", 11),
                 ("status", "Status", 10), ("name", "Name", 20), ("category", "Category", 13), ("tags", "Tags", 17), ("notes", "Notes", 24)]
        self.ledger = ttk.Treeview(tree_box, columns=[spec[0] for spec in specs], show="headings", selectmode="browse")
        for key, label, weight in specs:
            self.ledger.heading(key, text=label, anchor="e" if key == "amount_cents" else "w", command=lambda k=key: self.sort_ledger(k))
            self.ledger.column(key, width=80, minwidth=20, anchor="e" if key == "amount_cents" else "w", stretch=False)
        self._fit_columns(self.ledger, {k: w for k, label, w in specs})
        self.ledger.grid(row=0, column=0, sticky="nsew")
        def scroll_ledger(*args):
            self.ledger_editor.close()
            self.ledger.yview(*args)
        ys = ttk.Scrollbar(tree_box, command=scroll_ledger)
        ys.grid(row=0, column=1, sticky="ns")
        def ledger_scrolled(*args):
            ys.set(*args)
            self.ledger_editor.queue_position()
        self.ledger.configure(yscrollcommand=ledger_scrolled)
        self.ledger.tag_configure("odd", background="#f6f9f6")
        self.ledger.tag_configure("Pending", foreground="#926413")
        self.ledger.tag_configure("Planned", foreground="#396f9f")
        self.ledger.tag_configure("Void", foreground="#92a098")
        self.ledger.bind("<<TreeviewSelect>>", self._select_flow)
        self.ledger.bind("<Triple-1>", self._repeat_row_click)
        self.ledger_tooltip = TreeTooltip(self.ledger, self.flow_tooltip)
        self.ledger_editor = LedgerEditor(self, _NotesPeer,
            font=FONT, background="#fbfcfa", foreground=INK)
        actions = ttk.Frame(ledger_card, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        for label, command in [("Repeat", self.repeat_selected), ("Void", self.void_selected), ("History", self.show_history), ("Record JSON", self.show_flow_json)]:
            ttk.Button(actions, text=label, command=command, style="Small.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Category Totals", style="Small.TButton", command=self.show_report).pack(side="right")
        self.report_summary = tk.StringVar(self, "")
        ttk.Label(actions, textvariable=self.report_summary, style="CardMuted.TLabel").pack(side="right", padx=14)
        self.selection_detail = tk.StringVar(self, "Click a row · Double-click a cell to edit · Save commits changes")
        self.selection_label = ttk.Label(ledger_card, textvariable=self.selection_detail, style="CardMuted.TLabel", width=1)
        self.selection_label.grid(row=4, column=0, sticky="ew")

    def flow_tooltip(self, item):
        flow = self.ledger_rows.get(item)
        if not flow:
            return None
        when = flow["date"]
        return (f"{flow['name']}\n{when}\n{flow['from_account']} → {flow['to_account']} · ${format_money(flow['amount_cents'])}"
                f"\n{flow['status']} · {flow['category']}"
                + ("\nTags: " + ", ".join(flow['tags']) if flow['tags'] else "")
                + ("\n" + flow['notes'] if flow['notes'] else ""))

    def edit_date_filters(self):
        self._prepare_dialog()
        dialog = tk.Toplevel(self)
        dialog.title("Ledger Dates")
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        variables = []
        for label, original in (("From", self.filter_start), ("Through", self.filter_end)):
            variable = tk.StringVar(dialog, original.get())
            variables.append(variable)
            ttk.Label(body, text=label).pack(anchor="w")
            ttk.Entry(body, textvariable=variable, width=26).pack(fill="x", pady=(4, 12))
        ttk.Label(body, text="Blank = no limit · YYYY-MM-DD", style="Muted.TLabel").pack(anchor="w")
        error = tk.StringVar(dialog)
        ttk.Label(body, textvariable=error, foreground="#a44545").pack(anchor="w", pady=6)
        def accept():
            try:
                values = [v.get().strip() for v in variables]
                normalized = [normalize_date(v) if v else None for v in values]
                if normalized[1] and len(values[1]) == 10:
                    normalized[1] = normalized[1][:11] + "23:59:59"
                if all(normalized) and normalized[0] > normalized[1]:
                    raise ValueError("End must be on or after start.")
                self.filter_start.set(values[0])
                self.filter_end.set(values[1])
                self.refresh_ledger()
                dialog.destroy()
            except ValueError as exc:
                error.set(str(exc))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Clear", command=lambda: [v.set("") for v in variables]).pack(side="left")
        ttk.Button(buttons, text="Apply", style="Primary.TButton", command=accept).pack(side="right")
        dialog.grab_set()

    def _now(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _feedback(self, text, error=False):
        self.status_message.set(text)
        self.form_feedback.set(text if error else "From, To, and Amount required")
        if error:
            self.bell()

    def new_flow(self, focus=True, confirm=True, clear_selection=True):
        if confirm and not self._confirm_changes("flow"):
            return False
        if focus and not self.show_view("flows"):
            return False
        self.ledger_editor.close()
        self._restore_draft_row()
        self._close_notes_editor()
        self.ledger.focus_set()
        self._close_popups()
        self.editing_id = None
        defaults = {"date": datetime.now().strftime("%Y-%m-%d"), "time": "", "from_account": "", "to_account": "", "amount": "", "status": "Complete", "name": "", "category": "Uncategorized", "tags": ""}
        for key, value in defaults.items():
            self.form_vars[key].set(value)
        self.notes_text.delete("1.0", "end")
        self.form_title.configure(text="New Flow")
        self.save_button.configure(text="Save Flow  ·  Ctrl+S")
        self.form_widgets["status"].configure(values=STATUSES)
        self.form_feedback.set("From, To, and Amount required")
        self._clean_flow = self._flow_snapshot()
        if clear_selection:
            self._set_flow_selection(None)
        if focus:
            self.form_widgets["date"].focus_set()
        return True

    def save_flow(self):
        """Commit one draft, then prepare New Flow with no selected ledger row."""
        self.ledger_editor.close()
        self._close_popups()
        try:
            data = {key: var.get().strip() for key, var in self.form_vars.items() if key != "time"}
            time_value = self.form_vars["time"].get().strip()
            # A complete timestamp entered in Date replaces the old Time value.
            if len(data["date"]) == 10 and time_value:
                data["date"] += " " + time_value
            data["amount_cents"] = parse_money(data.pop("amount"))
            try:
                data["tags"] = [tag.strip() for tag in next(csv.reader([data["tags"]], skipinitialspace=True, strict=True)) if tag.strip()]
            except csv.Error as exc:
                raise ValueError("Tags use commas; quote a tag that itself contains a comma.") from exc
            data["notes"] = self.notes_text.get("1.0", "end-1c")
            if self.editing_id:
                data["flow_id"] = self.editing_id
            new_endpoints = [data[key] for key in ("from_account", "to_account") if not self.repo.get_account(data[key])]
            result = self.repo.save_flow(data)
            saved_name = result["name"]
            self.refresh_all()
            if self.ledger.exists(result["flow_id"]):
                self.ledger.see(result["flow_id"])
            # Clear selection, focused row, and draft after the ledger rebuild.
            # Late TreeviewSelect events then see the finished New Flow state.
            self.new_flow(focus=True, confirm=False)
            message = f"Saved: {saved_name} · ${format_money(result['amount_cents'])}"
            if new_endpoints:
                message += ". New accounts: " + ", ".join(new_endpoints) + "."
            self._feedback(message)
            self._close_popups()
            return True
        except (ValueError, sqlite3.Error) as exc:
            self._feedback(str(exc), True)
            return False

    def _selected(self):
        selection = self.ledger.selection()
        if not selection:
            self.status_message.set("Select a flow first.")
            return None
        return self.ledger_rows.get(selection[0])

    def _load_form(self, flow, repeat=False):
        if not self.show_view("flows"):
            return
        self.new_flow(focus=False, confirm=False, clear_selection=False)
        self.editing_id = None if repeat else flow["flow_id"]
        for key in ("from_account", "to_account", "name", "category"):
            self.form_vars[key].set(flow[key])
        self.form_vars["amount"].set(format_money(flow["amount_cents"]).replace(",", ""))
        tag_stream = io.StringIO()
        csv.writer(tag_stream, lineterminator="").writerow(flow.get("tags", []))
        self.form_vars["tags"].set(tag_stream.getvalue())
        self.notes_text.insert("1.0", flow.get("notes", ""))
        if not repeat:
            parts = flow["date"].split(" ")
            self.form_vars["date"].set(parts[0])
            self.form_vars["time"].set(parts[1] if len(parts) > 1 else "")
            self.form_vars["status"].set(flow["status"])
            self.form_title.configure(text="Edit Flow")
            self.save_button.configure(text="Save Changes  ·  Ctrl+S")
        else:
            self.form_title.configure(text="Repeat Flow")
            self._feedback("New copy: confirm the date, amount, and status, then save.")
        self.form_feedback.set("New copy · Not saved" if repeat else flow["name"])
        if not repeat:
            self._clean_flow = self._flow_snapshot()
        self.ledger.focus_set()
        self._close_popups()

    def _select_flow(self, event=None):
        if self._confirming:
            return
        selection = self.ledger.selection()
        selected = selection[0] if selection else None
        if selected == self._flow_selection:
            return
        old = self._flow_selection
        self._set_flow_selection(old)
        if not self._confirm_changes("flow"):
            return
        self._set_flow_selection(selected)
        flow = self.ledger_rows.get(selected)
        if flow:
            self._load_form(flow)
        else:
            self.new_flow(focus=False, confirm=False)

    def repeat_selected(self):
        flow = self._selected()
        if flow and self._confirm_changes("flow"):
            self._load_form(flow, repeat=True)

    def void_selected(self):
        self._prepare_dialog()
        flow = self._selected()
        if not flow:
            return
        if messagebox.askyesno("Void flow?", f"Exclude “{flow['name']}” from calculations? The record and its history will remain.", parent=self):
            self.repo.void_flow(flow["flow_id"])
            self.new_flow(focus=False, confirm=False)
            self.refresh_all()
            self.status_message.set("Flow voided. Its history is retained.")

    def _show_selection(self, event=None):
        selection = self.ledger.selection()
        if not selection:
            self.selection_detail.set("Click a row · Double-click a cell to edit · Save commits changes")
            return
        flow = self.ledger_rows.get(selection[0])
        if flow:
            tags = ", ".join(flow.get("tags", [])) or "No tags"
            # Keep selecting a multiline note from resizing the ledger and
            # moving the clicked bottom row out from under its cell editor.
            notes = flow.get("notes", "")
            detail = f"{flow['name']} · {flow['date'][:10]} · {tags}" + (f" · {notes}" if notes else "")
            self.selection_detail.set(detail.replace("\n", " · "))

    def sort_ledger(self, key):
        self.sort_reverse = not self.sort_reverse if self.sort_column == key else False
        self.sort_column = key
        self.refresh_ledger()

    @staticmethod
    def _flow_row_values(flow):
        return (flow["date"][:10], flow["from_account"], flow["to_account"],
                "$" + format_money(flow["amount_cents"]), flow["status"],
                flow["name"], flow["category"], ", ".join(flow["tags"]),
                flow["notes"].replace("\n", " · "))

    def _restore_draft_row(self):
        """Put the saved row back when its shared draft is discarded."""
        flow = self.ledger_rows.get(self.editing_id)
        if flow and self.ledger.exists(self.editing_id):
            stripe = self.ledger.item(self.editing_id, "tags")[0]
            self.ledger.item(self.editing_id, values=self._flow_row_values(flow),
                             tags=(stripe, flow["status"]))

    def _sync_flow_draft(self, *args):
        """Render the selected form draft without changing stored flow data."""
        row = self.editing_id
        if not row or not self.ledger.exists(row):
            return
        values = self.form_vars
        amount = values["amount"].get()
        try:
            amount = "$" + format_money(parse_money(amount))
        except ValueError:
            pass  # Show incomplete input exactly; Save supplies validation.
        notes = self.notes_text.get("1.0", "end-1c")
        displayed = (values["date"].get()[:10], values["from_account"].get(),
            values["to_account"].get(), amount, values["status"].get(),
            values["name"].get(), values["category"].get(),
            values["tags"].get(), notes.replace("\n", " · "))
        stripe = self.ledger.item(row, "tags")[0]
        self.ledger.item(row, values=displayed, tags=(stripe, values["status"].get()))

    def _flow_notes_changed(self, event=None):
        if self.notes_text.edit_modified():
            self.notes_text.edit_modified(False)
            self._sync_flow_draft()

    def refresh_ledger(self):
        if not hasattr(self, "ledger"):
            return
        self.ledger_editor.close()
        self.ledger_tooltip.hide()
        selection = self.ledger.selection()
        query = self.search_var.get().strip().casefold()
        categories = {value.casefold() for value in self.ledger_filters["Category"].get()}
        tags = {value.casefold() for value in self.ledger_filters["Tag"].get()}
        statuses = set(self.ledger_filters["Status"].get())
        try:
            date_start = normalize_date(self.filter_start.get()) if self.filter_start.get().strip() else None
            date_end = normalize_date(self.filter_end.get()) if self.filter_end.get().strip() else None
            if date_end and len(self.filter_end.get().strip()) == 10:
                date_end = date_end[:11] + "23:59:59"
            if date_start and date_end and date_end < date_start:
                raise ValueError("Filter end must be on or after its start.")
        except ValueError as exc:
            self.status_message.set(str(exc))
            return
        flows = self.repo.list_flows()
        def matches(flow):
            haystack = " ".join(str(flow.get(key, "")) for key in ("date", "name", "from_account", "to_account", "category", "tags", "notes", "amount_cents")).casefold()
            haystack += " " + format_money(flow["amount_cents"]) + " " + format_money(flow["amount_cents"]).replace(",", "")
            return (query in haystack
                    and (not categories or flow["category"].casefold() in categories)
                    and (not tags or tags.intersection(value.casefold() for value in flow["tags"]))
                    and (not date_start or normalize_date(flow["date"]) >= date_start)
                    and (not date_end or normalize_date(flow["date"]) <= date_end)
                    and (not statuses or flow["status"] in statuses))
        flows = [flow for flow in flows if matches(flow)]
        flows.sort(key=lambda flow: normalize_date(flow["date"]) if self.sort_column == "date" else flow.get(self.sort_column, ""), reverse=self.sort_reverse)
        self.ledger.delete(*self.ledger.get_children())
        self.ledger_rows = {}
        for index, flow in enumerate(flows):
            fid = flow["flow_id"]
            self.ledger_rows[fid] = flow
            values = self._flow_row_values(flow)
            tags = ("odd" if index % 2 else "even", flow["status"])
            self.ledger.insert("", "end", iid=fid, values=values, tags=tags)
        selected = [fid for fid in selection if fid in self.ledger_rows]
        self._set_flow_selection(selected[0] if selected else None)
        self._sync_flow_draft()
        self.metric_vars[2].set(str(len(flows)))
        self.date_filter_label.configure(text=(self.filter_start.get() or "Any Start") + " → " + (self.filter_end.get() or "Any End") if date_start or date_end else "All Dates")
        self.report_rows = [flow for flow in flows if flow["status"] != "Void"]
        for index, status in enumerate(("Complete", "Pending")):
            self.metric_vars[index].set(str(sum(f["status"] == status for f in self.report_rows)))
        volume = sum(f["amount_cents"] for f in self.report_rows if f["status"] == "Complete")
        self.report_summary.set(f"Complete · ${format_money(volume)} flow volume")

    def _remove_suggestion(self, field, value):
        """Forget an entry suggestion without touching the draft or records."""
        try:
            self.suggestion_preferences.remove(SUGGESTION_KINDS[field], value)
        except (OSError, ValueError) as exc:
            self._feedback(f"Could not remove suggestion: {exc}", True)
            return False
        self.refresh_suggestions()
        self.status_message.set(f"Removed suggestion: {value}")
        return True

    def _accept_suggestion(self, field, widget=None):
        """Explicit Enter can restore a forgotten value; refresh/save cannot."""
        widget = widget or self.form_widgets[field]
        value = widget._query()
        if not value:
            return
        try:
            restored = self.suggestion_preferences.restore(SUGGESTION_KINDS[field], value)
        except (OSError, ValueError) as exc:
            self._feedback(f"Could not restore suggestion: {exc}", True)
            return
        if restored:
            self.refresh_suggestions()
            self.status_message.set(f"Restored suggestion: {value}")

    def refresh_suggestions(self):
        """Apply entry preferences while retaining all data in other views."""
        accounts = self.repo.list_accounts()
        names = [account["name"] for account in accounts]
        # Most-used values remain first; both directions still accept every name.
        flows = self.repo.list_flows()
        counts = {name: 0 for name in names}
        for flow in flows:
            for key in ("from_account", "to_account"):
                if flow[key] in counts:
                    counts[flow[key]] += 1
        names.sort(key=lambda name: (-counts[name], name.casefold()))
        for key in ("from_account", "to_account"):
            self.form_widgets[key].set_values(self.suggestion_preferences.visible("account", names))
        for key, values in [("category", self.repo.list_categories()), ("tags", self.repo.list_tags()), ("name", self.repo.list_names())]:
            self.form_widgets[key].set_values(self.suggestion_preferences.visible(SUGGESTION_KINDS[key], values))
        self.ledger_editor.refresh_suggestions()

    def refresh_all(self):
        self.refresh_suggestions()
        self.ledger_filters["Category"].set_values(self.repo.list_categories())
        self.ledger_filters["Tag"].set_values(self.repo.list_tags())
        self.refresh_ledger()
        self.refresh_accounts()
        self.refresh_chart_accounts()
        if hasattr(self, "db_path_label"):
            self.db_path_label.configure(text=str(self.repo.path))
        if self.current_view == "charts":
            self.draw_chart()

    def _build_accounts(self):
        page = self.views["accounts"]
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        editor = self._card(page)
        self.account_editor = editor
        editor.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        editor.columnconfigure(0, weight=1)
        header = ttk.Frame(editor, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.account_title = ttk.Label(header, text="New Account", style="Section.TLabel")
        self.account_title.pack(side="left")
        self.account_feedback = tk.StringVar(self, "Negative = debt · Blank = unknown")
        ttk.Label(header, textvariable=self.account_feedback, style="CardMuted.TLabel", width=1).pack(side="left", fill="x", expand=True, padx=18)
        self.account_save_button = ttk.Button(header, text="Save Account", style="Primary.TButton", command=self.save_account)
        self.account_save_button.pack(side="right")
        self.account_cancel_button = ttk.Button(header, text="Cancel", style="Small.TButton", command=lambda: self.new_account(confirm=False))
        self.account_cancel_button.pack(side="right", padx=8)
        self.account_new_button = ttk.Button(header, text="New Account", style="Small.TButton", command=self.new_account)
        self.account_new_button.pack(side="right")
        ttk.Button(header, text="Record JSON", style="Small.TButton", command=self.show_account_json).pack(side="right", padx=8)
        form = ttk.Frame(editor, style="Card.TFrame")
        form.grid(row=1, column=0, sticky="ew")
        self.account_vars = {key: tk.StringVar(self) for key in ("name", "opening", "date", "time")}
        self.account_vars["date"].set("2026-10-01")
        self.account_widgets = {}
        cells = []
        for key, label in (("name", "Account Name"), ("opening", "Starting Balance · USD"), ("date", "Starting Date")):
            cell, heading, widget = self._entry_cell(form, label, self.account_vars[key])
            self.account_widgets[key] = widget
            cells.append(cell)
            if key == "name":
                self.account_name_entry = widget
            if key == "date":
                widget.bind("<FocusOut>", self._split_account_date, add="+")
                button = tk.Button(heading, text="Time", command=lambda: self.edit_time(self.account_vars["time"], "Starting Time"), font=(FONT, 8), bg=CARD, fg=TEAL, relief="flat", borderwidth=0, padx=3, pady=0)
                button.pack(side="right")
                self.account_vars["time"].trace_add("write", lambda *args: button.configure(text=self.account_vars["time"].get() or "Time"))
        self.account_cells = cells
        self._reflow_cells(form, cells, [40, 30, 30], threshold=700)
        card = self._card(page)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        top = ttk.Frame(card, style="Card.TFrame")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(top, text="Accounts", style="Section.TLabel").pack(side="left")
        self.account_summary = tk.StringVar(self)
        ttk.Label(top, textvariable=self.account_summary, style="CardMuted.TLabel").pack(side="left", padx=14)
        ttk.Label(top, text="Current balances", style="CardMuted.TLabel").pack(side="right")
        specs = [("name", "Account", 28), ("balance", "Starting Balance", 18), ("anchor", "Starting Date", 18),
                 ("confirmed", "Confirmed", 18), ("pending", "Including Pending", 18)]
        self.accounts_tree = ttk.Treeview(card, columns=[s[0] for s in specs], show="headings", selectmode="browse")
        for key, label, weight in specs:
            self.accounts_tree.heading(key, text=label, anchor="e" if key in {"balance", "confirmed", "pending"} else "w")
            self.accounts_tree.column(key, width=80, minwidth=20, stretch=False, anchor="e" if key in {"balance", "confirmed", "pending"} else "w")
        self._fit_columns(self.accounts_tree, {k: w for k, label, w in specs})
        self.accounts_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, command=self._accounts_yview)
        scroll.grid(row=1, column=1, sticky="ns")
        def accounts_scrolled(*args):
            scroll.set(*args)
            self.account_cell_editor.queue_position()
        self.accounts_tree.configure(yscrollcommand=accounts_scrolled)
        self.accounts_tree.tag_configure("odd", background="#f6f9f6")
        self.accounts_tree.bind("<<TreeviewSelect>>", self._select_account)
        self.accounts_tree.bind("<Triple-1>", self._repeat_row_click)
        self.account_cell_editor = AccountEditor(self, font=FONT, background=CARD, foreground=INK)
        self.account_tooltip = TreeTooltip(self.accounts_tree, lambda name: "Starting balance immediately before " + self.repo.get_account(name)["opening_at"] if self.repo.get_account(name) else None)
        ttk.Label(card, text="Click a row · Double-click Starting Balance or Starting Date to edit · Save commits changes · Hover for exact time", style="CardMuted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _accounts_yview(self, *args):
        self.account_cell_editor.close()
        self.accounts_tree.yview(*args)

    def _restore_account_draft_row(self):
        name = self.account_editing
        if not name or not self.accounts_tree.exists(name):
            return
        account = self.repo.get_account(name)
        if account:
            opening = account["opening_cents"]
            self.accounts_tree.set(name, "balance", "Unknown" if opening is None else "$" + format_money(opening))
            self.accounts_tree.set(name, "anchor", account["opening_at"][:10])

    def _sync_account_draft(self, *args):
        """Preview only starting values; calculated balances remain saved values."""
        name = self.account_editing
        if not name or not self.accounts_tree.exists(name):
            return
        raw = self.account_vars["opening"].get().strip()
        try:
            opening = "$" + format_money(parse_money(raw, allow_negative=True)) if raw else "Unknown"
        except ValueError:
            opening = raw  # Keep mistakes visible without writing a record.
        self.accounts_tree.set(name, "balance", opening)
        self.accounts_tree.set(name, "anchor", self.account_vars["date"].get()[:10])

    def refresh_accounts(self):
        if not hasattr(self, "accounts_tree"):
            return
        self.account_cell_editor.close()
        self.account_tooltip.hide()
        selected = self._account_selection
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        accounts = self.repo.list_accounts()
        flows = self.repo.list_flows()
        now = self._now()
        def money(cents):
            return "Unknown" if cents is None else "$" + format_money(cents)
        for index, account in enumerate(accounts):
            confirmed = ledger_balance_at(account, flows, now, ("Complete",))
            pending = ledger_balance_at(account, flows, now, ("Complete", "Pending"))
            self.accounts_tree.insert("", "end", iid=account["name"], values=(account["name"], money(account["opening_cents"]), account["opening_at"][:10], money(confirmed), money(pending)), tags=("odd" if index % 2 else "even",))
        self._set_account_selection(selected)
        self._sync_account_draft()
        self.account_summary.set(f"{len(accounts)} accounts")

    def _select_account(self, event=None):
        if self._confirming:
            return
        selection = self.accounts_tree.selection()
        name = selection[0] if selection else None
        if name == self._account_selection:
            return
        self.account_cell_editor.close()
        self._set_account_selection(self._account_selection)
        if not self._confirm_changes("account"):
            return
        self._restore_account_draft_row()
        self._close_popups()
        if not name:
            self.new_account(focus=False, confirm=False)
            return
        account = self.repo.get_account(name)
        if not account:
            return
        self._set_account_selection(name)
        self.account_editing = name
        self.account_vars["name"].set(name)
        self.account_name_entry.configure(state="disabled")
        self.account_vars["opening"].set("" if account["opening_cents"] is None else format_money(account["opening_cents"]).replace(",", ""))
        self.account_vars["date"].set(account["opening_at"][:10])
        self.account_vars["time"].set(account["opening_at"][11:] if account["opening_at"][11:] != "00:00:00" else "")
        self.account_title.configure(text="Edit Account")
        self.account_feedback.set("Account name is fixed · Negative = debt · Blank = unknown")
        self._clean_account = self._account_snapshot()
        self.accounts_tree.focus_set()

    def new_account(self, focus=True, confirm=True):
        if confirm and not self._confirm_changes("account"):
            return False
        self.account_cell_editor.close()
        self._restore_account_draft_row()
        self._close_popups()
        self.account_editing = None
        self.account_name_entry.configure(state="normal")
        for key, value in {"name": "", "opening": "", "date": "2026-10-01", "time": ""}.items():
            self.account_vars[key].set(value)
        self.account_title.configure(text="New Account")
        self.account_feedback.set("Negative = debt · Blank = unknown")
        self._clean_account = self._account_snapshot()
        self._set_account_selection(None)
        if focus:
            self.account_name_entry.focus_set()
        elif self.current_view == "accounts":
            self.accounts_tree.focus_set()
        return True

    def save_account(self):
        self.account_cell_editor.close()
        self._close_popups()
        try:
            raw = self.account_vars["opening"].get().strip()
            cents = parse_money(raw, allow_negative=True) if raw else None
            at = normalize_date(self._account_when())
            account = self.repo.save_account(self.account_vars["name"].get(), opening_cents=cents, opening_at=at)
            self.new_account(focus=False, confirm=False)
            self.refresh_all()
            self.status_message.set(f"Account saved: {account['name']}")
            self.accounts_tree.see(account["name"])
            self.account_name_entry.focus_set()
            return True
        except (ValueError, sqlite3.Error) as exc:
            self.account_feedback.set(str(exc))
            self.bell()
            return False

    def _build_charts(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        page = self.views["charts"]
        page.rowconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)
        control_width = max(290, int(290 * float(self.tk.call("tk", "scaling")) / 1.39))
        control_host = ttk.Frame(page, width=control_width, style="Card.TFrame")
        control_host.grid(row=0, column=0, sticky="ns", padx=(0, 18))
        control_host.grid_propagate(False)
        control_host.pack_propagate(False)
        control_scroll = ScrollFrame(control_host)
        control_scroll.pack(fill="both", expand=True)
        controls = self._card(control_scroll.inner)
        controls.pack(fill="both", expand=True)
        controls.columnconfigure(0, weight=1)
        ttk.Label(controls, text="Graph Controls", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 16))
        ttk.Label(controls, text="ACCOUNTS · SELECT ONE OR MANY", style="Field.TLabel").grid(row=1, column=0, sticky="w")
        account_list = ttk.Frame(controls, style="Card.TFrame")
        account_list.grid(row=2, column=0, sticky="ew", pady=(7, 7))
        self.chart_accounts = tk.Listbox(account_list, selectmode="multiple", exportselection=False, height=7, width=1, bg="#f8faf7", fg=INK, selectbackground="#d6eade", selectforeground=INK, relief="flat", highlightthickness=1, highlightbackground=LINE, font=(FONT, 10), activestyle="none")
        self.chart_accounts.pack(side="left", fill="both", expand=True)
        account_scroll = ttk.Scrollbar(account_list, command=self.chart_accounts.yview)
        account_scroll.pack(side="right", fill="y")
        self.chart_accounts.configure(yscrollcommand=account_scroll.set)
        account_buttons = ttk.Frame(controls, style="Card.TFrame")
        account_buttons.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ttk.Button(account_buttons, text="All", width=5, style="Small.TButton", command=lambda: self.chart_accounts.selection_set(0, "end")).pack(side="left", padx=5)
        ttk.Button(account_buttons, text="None", width=5, style="Small.TButton", command=lambda: self.chart_accounts.selection_clear(0, "end")).pack(side="left")
        self.chart_basis = tk.StringVar(self, "Confirmed")
        self._field(controls, "Balance basis", self.chart_basis, 4, kind="choice", values=("Confirmed", "Including Pending", "Including Planned"))
        self.chart_start = tk.StringVar(self, "2026-10-01")
        self.chart_end = tk.StringVar(self, "2027-01-01")
        for label, variable, row, attribute in (
                ("From", self.chart_start, 5, "chart_start_picker"),
                ("Through", self.chart_end, 6, "chart_end_picker")):
            frame = ttk.Frame(controls, style="Card.TFrame")
            frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
            ttk.Label(frame, text=label.upper(), style="Field.TLabel").pack(anchor="w", pady=(0, 5))
            picker = CalendarEntry(frame, variable=variable, style="Card.TFrame")
            picker.pack(fill="x")
            setattr(self, attribute, picker)
        self.chart_combined = tk.BooleanVar(self, False)
        ttk.Checkbutton(controls, text="Combine selected accounts", variable=self.chart_combined, style="Card.TCheckbutton").grid(row=7, column=0, sticky="w", pady=4)
        ttk.Button(controls, text="Update Graph", style="Primary.TButton", command=self.draw_chart).grid(row=8, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(controls, text="Export Graph…", command=self.export_chart).grid(row=9, column=0, sticky="ew", pady=(8, 0))
        chart_card = self._card(page)
        chart_card.grid(row=0, column=1, sticky="nsew")
        chart_card.columnconfigure(0, weight=1)
        chart_card.rowconfigure(2, weight=1)
        self.chart_title = ttk.Label(chart_card, text="Account Balances", style="Section.TLabel")
        self.chart_title.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.chart_message = tk.StringVar(self, "")
        self.chart_message_label = ttk.Label(chart_card, textvariable=self.chart_message,
                                            background=CARD, foreground="#a23838",
                                            font=(FONT, 10, "bold"), wraplength=650)
        self.chart_message_label.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.chart_message_label.grid_remove()
        self.figure = Figure(figsize=(9, 6), dpi=100, facecolor="white")
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_card)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.grid(row=2, column=0, sticky="nsew")
        canvas_widget.bind("<Map>", self._fit_chart_canvas, add="+")
        canvas_widget.bind("<Unmap>", self._hide_chart_tooltip, add="+")
        toolbar_frame = ttk.Frame(chart_card, style="Card.TFrame")
        toolbar_frame.grid(row=3, column=0, sticky="ew")
        self.chart_toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
        self.chart_toolbar.pack(fill="x")
        self.chart_note = tk.StringVar(self, "Select accounts and update the graph. Pan, zoom, and click to inspect.")
        ttk.Label(chart_card, textvariable=self.chart_note, style="CardMuted.TLabel", wraplength=750).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.chart_inspect = tk.StringVar(self, "")
        ttk.Label(chart_card, textvariable=self.chart_inspect, style="CardMuted.TLabel", wraplength=750).grid(row=5, column=0, sticky="w", pady=(5, 0))
        self.canvas.mpl_connect("button_press_event", self.inspect_chart)
        self.canvas.mpl_connect("pick_event", self.toggle_chart_line)
        self.canvas.mpl_connect("motion_notify_event", self.hover_chart)
        self.canvas.mpl_connect("figure_leave_event", self._hide_chart_tooltip)
        self.canvas.mpl_connect("axes_leave_event", self._hide_chart_tooltip)
        self.chart_line_map = {}
        self.chart_series = {}
        self.chart_tooltip = None
        self.chart_inspection_context = None

    def _fit_chart_canvas(self, event=None):
        """Fit the mapped figure after Tk applies its display scaling."""
        widget = self.canvas.get_tk_widget()
        width, height = widget.winfo_width(), widget.winfo_height()
        if width > 1 and height > 1:
            # Mapping may change Matplotlib's DPI without a Tk resize event
            # when grid already constrains the canvas to its available space.
            self.figure.set_size_inches(width / self.figure.dpi,
                                        height / self.figure.dpi, forward=False)
            self.canvas.draw_idle()

    def refresh_chart_accounts(self):
        if not hasattr(self, "chart_accounts"):
            return
        selected = {self.chart_accounts.get(i) for i in self.chart_accounts.curselection()}
        initial = self.chart_accounts.size() == 0
        accounts = self.repo.list_accounts()
        self.chart_accounts.delete(0, "end")
        for index, account in enumerate(accounts):
            self.chart_accounts.insert("end", account["name"])
            if account["name"] in selected or (initial and index == 0):
                self.chart_accounts.selection_set(index)

    def draw_chart(self):
        from matplotlib import dates as mdates, ticker
        self._hide_chart_tooltip()
        self.chart_tooltip = None
        self.chart_series = {}
        self.chart_line_map = {}
        self.chart_inspection_context = None
        self.chart_inspect.set("")
        self.chart_message.set("")
        self.chart_message_label.grid_remove()
        self.axes.clear()
        self.chart_toolbar.update()
        self.axes.set_facecolor("white")
        self.axes.spines[["top", "right", "left"]].set_visible(False)
        self.axes.spines["bottom"].set_color(LINE)
        self.axes.grid(axis="y", color="#e8eee8", linewidth=0.8)
        self.axes.tick_params(axis="both", labelsize=9, colors=MUTED, length=0, pad=10)
        self.axes.yaxis.set_major_formatter(ticker.FuncFormatter(lambda value, pos: f"${value:,.0f}"))
        locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
        self.axes.xaxis.set_major_locator(locator)
        self.axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        try:
            normalized = []
            for label, variable in (("From", self.chart_start), ("Through", self.chart_end)):
                try:
                    normalized.append(normalize_graph_date(variable.get()))
                except ValueError as exc:
                    raise ValueError(f"{label}: invalid date {variable.get()!r}. Use YYYY-MM-DD; time is optional.") from exc
            start, end = map(normalize_date, normalized)
            if len(normalized[1]) == 10:
                end = end[:11] + "23:59:59"
            if end < start:
                raise ValueError("Through must be on or after From.")
            self.chart_start.set(normalized[0])
            self.chart_end.set(normalized[1])
            selected = [self.chart_accounts.get(i) for i in self.chart_accounts.curselection()]
            accounts = [self.repo.get_account(name) for name in selected]
            statuses = {"Confirmed": ("Complete",),
                        "Including Pending": ("Complete", "Pending"),
                        "Including Planned": ("Complete", "Pending", "Planned")}[self.chart_basis.get()]
            flows = self.repo.list_flows()
            unknown = [account["name"] for account in accounts if account["opening_cents"] is None]
            valid = [account for account in accounts if account["opening_cents"] is not None]
            if not accounts:
                self.axes.text(.5, .5, "Choose accounts to see their balances", ha="center", va="center", transform=self.axes.transAxes, color=MUTED, fontsize=14)
            elif self.chart_combined.get() and unknown:
                valid = []
                self.axes.text(.5, .5, "Combined balance is unknown\nSet the missing starting balances in Accounts.", ha="center", va="center", transform=self.axes.transAxes, color=MUTED, fontsize=13)
            elif unknown and not valid:
                self.axes.text(.5, .5, "Starting balance required\nSet a starting balance in Accounts.", ha="center", va="center", transform=self.axes.transAxes, color=MUTED, fontsize=13)
            if unknown:
                self.chart_message.set("Missing starting balance: " + ", ".join(unknown) + ".")
                self.chart_message_label.grid()
            series = graph_timeline(valid, flows, start, end, statuses,
                                    combined=self.chart_combined.get())
            all_lines = []
            for index, (name, rows) in enumerate(series.items()):
                xs = [datetime.fromisoformat(date) for date, value in rows]
                ys = [value / 100 for date, value in rows]
                line, = self.axes.step(xs, ys, where="post", label=name,
                                       color=PALETTE[index % len(PALETTE)], linewidth=2.2,
                                       marker="o", markevery=[0], markersize=5)
                self.chart_series[line] = rows
                all_lines.append(line)
            if all_lines:
                legend = self.axes.legend(loc="upper left", frameon=False, fontsize=9, ncol=1 if len(all_lines) < 6 else 2)
                for legline, origline in zip(legend.get_lines(), all_lines):
                    legline.set_picker(6)
                    self.chart_line_map[legline] = origline
            start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
            # A small display margin keeps the first value and any same-time
            # jump visible. The recorded event times are never shifted.
            padding = max((end_dt - start_dt) * .02, timedelta(seconds=1))
            self.axes.set_xlim(start_dt - padding, end_dt + padding)
            self.axes.axhline(0, color="#b4c7bb", linewidth=.8)
            now = datetime.now()
            if start_dt <= now <= end_dt:
                self.axes.axvline(now, color="#b4c7bb", linewidth=1, linestyle=":")
                self.axes.text(mdates.date2num(now), 1.01, "NOW", transform=self.axes.get_xaxis_transform(), color=MUTED, fontsize=8, ha="center")
            self.figure.subplots_adjust(left=.12, bottom=.11, right=.97, top=.92)
            self.chart_title.configure(text="Account Balances · " + self.chart_basis.get())
            self.chart_note.set("Hover for exact balances; click legend lines to hide/show.")
            self.chart_inspection_context = (accounts, flows, statuses, ledger_balance_at)
            self.chart_tooltip = self.axes.annotate(
                "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
                fontsize=9, color=INK, zorder=20,
                bbox=dict(boxstyle="round,pad=.55", fc="white", ec=LINE, alpha=.98))
            self.chart_tooltip.set_visible(False)
            self.chart_tooltip.set_in_layout(False)
            self.axes.callbacks.connect("xlim_changed", self._hide_chart_tooltip)
            self.axes.callbacks.connect("ylim_changed", self._hide_chart_tooltip)
        except (ValueError, sqlite3.Error) as exc:
            self.chart_message.set(str(exc))
            self.chart_message_label.grid()
            self.axes.text(.5, .5, "Unable to draw graph\nCheck the dates above.", ha="center", va="center", transform=self.axes.transAxes, color=MUTED, fontsize=13)
            self.chart_note.set("")
            self.bell()
        self.canvas.draw_idle()

    def _hide_chart_tooltip(self, event=None):
        tooltip = getattr(self, "chart_tooltip", None)
        if tooltip is not None and tooltip.get_visible():
            tooltip.set_visible(False)
            if getattr(event, "type", None) != tk.EventType.Unmap:
                self.canvas.draw_idle()

    def hover_chart(self, event):
        """Hit-test rendered steps in pixels; report stored cents, never interpolation."""
        if (event.inaxes is not self.axes or event.xdata is None
                or self.chart_tooltip is None or self.chart_toolbar.mode):
            self._hide_chart_tooltip()
            return
        import numpy as np
        hits = []
        cursor = np.array([event.x, event.y])
        for line, rows in self.chart_series.items():
            if not line.get_visible() or not rows:
                continue
            points = self.axes.transData.transform(line.get_xydata())
            if len(points) == 1:
                distance = float(np.linalg.norm(cursor - points[0]))
                text = f"{line.get_label()}: ${format_money(rows[0][1])}"
            else:
                # Each post-step has an exact horizontal balance followed by
                # a vertical change at the next event's real timestamp.
                starts = np.empty((2 * (len(points) - 1), 2))
                ends = np.empty_like(starts)
                starts[::2] = points[:-1]
                ends[::2, 0] = points[1:, 0]
                ends[::2, 1] = points[:-1, 1]
                starts[1::2] = ends[::2]
                ends[1::2] = points[1:]
                vectors = ends - starts
                lengths = (vectors * vectors).sum(axis=1)
                fractions = np.divide(((cursor - starts) * vectors).sum(axis=1),
                                      lengths, out=np.zeros_like(lengths), where=lengths != 0)
                fractions = np.clip(fractions, 0, 1)
                nearest = starts + vectors * fractions[:, None]
                distances = np.linalg.norm(nearest - cursor, axis=1)
                index = int(np.argmin(distances))
                distance = float(distances[index])
                row_index = index // 2
                before, after = rows[row_index][1], rows[row_index + 1][1]
                value = f"${format_money(before)}"
                if index % 2 and before != after:
                    if 0 < fractions[index] < 1:
                        value += f" → ${format_money(after)}"
                    elif fractions[index] == 1:
                        value = f"${format_money(after)}"
                text = f"{line.get_label()}: {value}"
                if index % 2 and before != after and 0 < fractions[index] < 1:
                    text += "\n" + rows[row_index + 1][0]
            if distance <= 7:
                hits.append((distance, text))
        if not hits:
            self._hide_chart_tooltip()
            return
        tooltip = self.chart_tooltip
        tooltip.set_text("\n".join(text for distance, text in sorted(hits)))
        tooltip.xy = (event.xdata, event.ydata)
        right_half = event.x > (self.axes.bbox.x0 + self.axes.bbox.x1) / 2
        top_half = event.y > (self.axes.bbox.y0 + self.axes.bbox.y1) / 2
        tooltip.set_position((-12 if right_half else 12, -12 if top_half else 12))
        tooltip.set_ha("right" if right_half else "left")
        tooltip.set_va("top" if top_half else "bottom")
        tooltip.set_visible(True)
        self.canvas.draw_idle()

    def inspect_chart(self, event):
        if event.inaxes is not self.axes or event.xdata is None or not self.chart_inspection_context:
            return
        from matplotlib.dates import num2date
        at = num2date(event.xdata).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        accounts, flows, statuses, calculate = self.chart_inspection_context
        parts = []
        for account in accounts:
            value = calculate(account, flows, at, statuses)
            parts.append(f"{account['name']}: " + ("unknown" if value is None else "$" + format_money(value)))
        self.chart_inspect.set(at + "  |  " + " · ".join(parts))

    def toggle_chart_line(self, event):
        line = self.chart_line_map.get(event.artist)
        if line:
            self._hide_chart_tooltip()
            line.set_visible(not line.get_visible())
            event.artist.set_alpha(1 if line.get_visible() else .25)
            self.canvas.draw_idle()

    def export_chart(self):
        self._hide_chart_tooltip()
        self._prepare_dialog()
        path = filedialog.asksaveasfilename(parent=self, title="Export graph", defaultextension=".png", filetypes=[("PNG image", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")], initialfile="flow_balances.png")
        if path:
            try:
                self.figure.savefig(path, dpi=160, facecolor="white", bbox_inches="tight")
                self.status_message.set(f"Graph exported: {path}")
            except OSError as exc:
                messagebox.showerror("Could not export graph", str(exc), parent=self)

    def _build_data(self):
        page = self.views["data"]
        page.columnconfigure(0, weight=1)
        page.columnconfigure(1, weight=1)
        cards = [
            ("Import", "Preview CSV or JSON records before saving.", "Import Records…", self.import_records),
            ("Export", "Export flows, including their IDs.", "Export Records…", self.export_records),
            ("Backup", "Copy all accounts, flows, and history.", "Create Backup…", self.backup_database),
            ("Restore", "Restore a backup. The current database is copied first.", "Restore Backup…", self.restore_database),
        ]
        for index, (title, desc, button, command) in enumerate(cards):
            card = self._card(page)
            card.grid(row=index//2, column=index%2, sticky="nsew", padx=(0 if index%2 == 0 else 16, 0), pady=(0, 16))
            ttk.Label(card, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 10))
            ttk.Label(card, text=desc, style="CardMuted.TLabel", wraplength=450).pack(anchor="w", pady=(0, 20))
            ttk.Button(card, text=button, command=command).pack(anchor="w")
        card = self._card(page)
        card.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(card, text="Change History", style="Section.TLabel").pack(anchor="w")
        ttk.Label(card, text="Saved edits keep before-and-after values. Nothing in a graph is an independent stored balance.", style="CardMuted.TLabel").pack(anchor="w", pady=10)
        ttk.Button(card, text="View all change history", command=lambda: self.show_history(all_records=True)).pack(anchor="w", pady=(0, 20))
        ttk.Label(card, text="DATABASE", style="Field.TLabel").pack(anchor="w")
        self.db_path_label = ttk.Label(card, text=str(self.repo.path), style="CardMuted.TLabel", wraplength=950)
        self.db_path_label.pack(anchor="w", pady=(6, 16))
        ttk.Button(card, text="View Schema", command=self.show_schema).pack(anchor="w", pady=(0, 16))
        ttk.Label(card, text=f"Version {__version__} · Local desktop · USD · SQLite", style="CardMuted.TLabel").pack(anchor="w")

    def _show_text(self, title, content):
        """Inspect saved records or the live database schema without changing them."""
        self._prepare_dialog()
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry("850x600")
        dialog.transient(self)
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)
        text = tk.Text(dialog, wrap="none", font=("DejaVu Sans Mono", 10), padx=14, pady=14)
        text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(dialog, command=text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(dialog, orient="horizontal", command=text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        text.insert("1.0", content)
        text.configure(state="disabled")
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        text.focus_set()
        return dialog

    def show_flow_json(self):
        flow = self.repo.get_flow(self.editing_id) if self.editing_id else None
        if flow is None:
            self.status_message.set("Select a saved flow.")
            return
        return self._show_text("Saved Flow JSON", json.dumps(flow, indent=2, ensure_ascii=False))

    def show_account_json(self):
        account = self.repo.get_account(self.account_editing) if self.account_editing else None
        if account is None:
            self.status_message.set("Select a saved account.")
            return
        return self._show_text("Saved Account JSON", json.dumps(account, indent=2, ensure_ascii=False))

    def show_schema(self):
        rows = self.repo.conn.execute("SELECT sql FROM sqlite_master WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type DESC, name").fetchall()
        version = self.repo.conn.execute("PRAGMA user_version").fetchone()[0]
        return self._show_text("SQLite Schema", f"-- Schema version {version}\n\n" + "\n\n".join(row[0] + ";" for row in rows))

    def import_records(self):
        self._prepare_dialog()
        path = filedialog.askopenfilename(parent=self, title="Import flow records", filetypes=[("Flow records", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")])
        if not path:
            return
        try:
            rows = read_import(path)
            preview = preview_import(self.repo, rows)
        except (ValueError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("Could not read import", str(exc), parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("Review import")
        dialog.geometry("1040x580")
        dialog.transient(self)
        dialog.configure(bg=BG)
        dialog.rowconfigure(2, weight=1)
        dialog.columnconfigure(0, weight=1)
        ttk.Label(dialog, text="Review before saving", font=(FONT, 20, "bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 10))
        counts = {state: sum(row["state"] == state for row in preview) for state in ("New", "Duplicate", "Similar", "Conflict", "Error")}
        ttk.Label(dialog, text="  ·  ".join(f"{count} {state.lower()}" for state, count in counts.items()), style="Muted.TLabel").grid(row=1, column=0, sticky="w", padx=22, pady=(0, 16))
        columns = ("row", "state", "name", "from", "to", "amount", "message")
        tree = ttk.Treeview(dialog, columns=columns, show="headings")
        for key, width in [("row", 45), ("state", 85), ("name", 145), ("from", 110), ("to", 110), ("amount", 90), ("message", 325)]:
            tree.heading(key, text=key.upper())
            tree.column(key, width=width, stretch=key == "message")
        tree.grid(row=2, column=0, sticky="nsew", padx=22)
        for row in preview:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            cents = data.get("amount_cents")
            amount = format_money(cents) if isinstance(cents, int) and not isinstance(cents, bool) else data.get("amount", "")
            tree.insert("", "end", values=(row["row"], row["state"], data.get("name", ""), data.get("from_account", ""), data.get("to_account", ""), amount, row["message"]))
        include_similar = tk.BooleanVar(dialog, False)
        ttk.Checkbutton(dialog, text="Include similar rows as separate flows (possible legitimate repeats)", variable=include_similar).grid(row=3, column=0, sticky="w", padx=22, pady=14)
        bottom = ttk.Frame(dialog)
        bottom.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 20))
        blocked = bool(counts["Error"] or counts["Conflict"])
        ttk.Label(bottom, text="Fix errors and ID conflicts in the source file first." if blocked else "Duplicate IDs are skipped. All accepted rows save together.", style="Muted.TLabel").pack(side="left")
        def commit():
            try:
                result = commit_import(self.repo, preview, include_similar.get())
                dialog.destroy()
                self.refresh_all()
                self.status_message.set(f"Imported {result['imported']} flows; skipped {result['skipped']}.")
            except (ValueError, sqlite3.Error) as exc:
                messagebox.showerror("Import was not saved", str(exc), parent=dialog)
        ttk.Button(bottom, text="Commit import", style="Primary.TButton", command=commit, state="disabled" if blocked else "normal").pack(side="right")
        ttk.Button(bottom, text="Cancel", command=dialog.destroy).pack(side="right", padx=10)
        dialog.grab_set()

    def export_records(self):
        self._prepare_dialog()
        path = filedialog.asksaveasfilename(parent=self, title="Export flow records", defaultextension=".json", filetypes=[("JSON", "*.json"), ("CSV", "*.csv")], initialfile="flows.json")
        if path:
            try:
                count = export_flows(self.repo, path)
                self.status_message.set(f"Exported {count} flows to {path}")
            except (ValueError, OSError, sqlite3.Error) as exc:
                messagebox.showerror("Could not export", str(exc), parent=self)

    def backup_database(self):
        self._prepare_dialog()
        path = filedialog.asksaveasfilename(parent=self, title="Back up entire database", defaultextension=".sqlite3", filetypes=[("SQLite database", "*.sqlite3")], initialfile="flow_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".sqlite3")
        if path:
            try:
                self.repo.backup(path)
                self.status_message.set(f"Backup saved: {path}")
            except (ValueError, OSError, sqlite3.Error) as exc:
                messagebox.showerror("Could not back up", str(exc), parent=self)

    def restore_database(self):
        self._prepare_dialog()
        path = filedialog.askopenfilename(parent=self, title="Restore Flow backup", filetypes=[("SQLite database", "*.sqlite3 *.db"), ("All files", "*")])
        if path and messagebox.askyesno("Restore this backup?", "Replace the current database with this backup? The current database will first be copied to a safety backup.", parent=self):
            try:
                self.repo.restore(path)
                self.new_flow(focus=False, confirm=False)
                self.new_account(focus=False, confirm=False)
                self.refresh_all()
                self.status_message.set("Backup restored. A safety copy preserves the previous database.")
            except (ValueError, OSError, sqlite3.Error) as exc:
                messagebox.showerror("Could not restore", str(exc), parent=self)

    def show_history(self, all_records=False):
        self._prepare_dialog()
        flow = None if all_records else self._selected()
        if not all_records and not flow:
            return
        rows = self.repo.history(None if all_records else flow["flow_id"])
        dialog = tk.Toplevel(self)
        dialog.title("Change history")
        dialog.geometry("1040x680")
        dialog.transient(self)
        dialog.configure(bg=BG)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        dialog.rowconfigure(2, weight=1)
        ttk.Label(dialog, text="Change history" + (" · " + flow["name"] if flow else ""), font=(FONT, 18, "bold")).grid(row=0, column=0, sticky="w", padx=20, pady=20)
        tree = ttk.Treeview(dialog, columns=("time", "entity", "action", "key"), show="headings")
        for key, width in [("time", 180), ("entity", 110), ("action", 100), ("key", 500)]:
            tree.heading(key, text=key.upper())
            tree.column(key, width=width)
        tree.grid(row=1, column=0, sticky="nsew", padx=20)
        detail = tk.Text(dialog, wrap="word", font=("DejaVu Sans Mono", 10), bg="white", fg=INK, relief="flat", padx=12, pady=12)
        detail.grid(row=2, column=0, sticky="nsew", padx=20, pady=15)
        for index, record in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=(record["changed_at"], record["entity"], record["action"], record["entity_key"]))
        def select(event=None):
            if not tree.selection():
                return
            record = rows[int(tree.selection()[0])]
            parts = []
            for key, title in [("before_json", "BEFORE"), ("after_json", "AFTER")]:
                value = record.get(key)
                try:
                    value = json.dumps(json.loads(value), indent=2, ensure_ascii=False) if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
                except (ValueError, TypeError):
                    value = str(value)
                parts.append(title + "\n" + value)
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", "\n\n".join(parts))
            detail.configure(state="disabled")
        tree.bind("<<TreeviewSelect>>", select)
        if rows:
            tree.selection_set("0")
            select()

    def show_report(self):
        """Group each visible, effective flow once by category and status."""
        self._prepare_dialog()
        groups = {}
        for flow in self.report_rows:
            key = (flow["category"], flow["status"])
            amount, count = groups.get(key, (0, 0))
            groups[key] = (amount + flow["amount_cents"], count + 1)
        dialog = tk.Toplevel(self)
        dialog.title("Category totals · current ledger filter")
        dialog.geometry("850x560")
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)
        ttk.Label(dialog, text="Category totals", font=(FONT, 20, "bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 8))
        ttk.Label(dialog, text="Flow volume · Each flow counted once, including transfers · Current ledger filter", style="Muted.TLabel", wraplength=800).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 18))
        tree = ttk.Treeview(dialog, columns=("category", "status", "amount", "count"), show="headings")
        for key, title, width in [("category", "Category", 260), ("status", "Status", 150), ("amount", "Flow Volume", 200), ("count", "Flows", 100)]:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="e" if key in {"amount", "count"} else "w")
        tree.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 22))
        for (category, status), (amount, count) in sorted(groups.items()):
            tree.insert("", "end", values=(category, status, "$" + format_money(amount), count))
