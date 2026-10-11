"""Flow: a local, keyboard-friendly desktop interface for the flow engine.

All entry methods ultimately call Repository.save_flow. Views use the same
balance and timeline functions; no screen stores an independent balance.
"""
from __future__ import annotations

import csv
import json
import io
import math
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .core import (STATUSES, ledger_balance_at, normalize_flow_date, parse_quantity,
                   format_quantity, quantity_text, decimal_sum, normalize_date)
from .exchange import read_import, preview_import, commit_import, export_flows
from .suggestions import SuggestionPreferences
from .filters import CheckFilter
from .calendar_widget import CalendarEntry
from .graphing import graph_timeline, normalize_graph_date
from .widgets import FilterCombo, ScrollFrame, TreeTooltip
from .ledger_editor import LedgerEditor
from .account_editor import AccountEditor
from .history import SessionHistory
from .view_history import ViewHistory
from .experimental import ExperimentalView
from .units import decimal_places, format_display
from .table_columns import ColumnDrag, displayed_columns

BG = "#f2f5f2"
CARD = "#ffffff"
INK = "#18382f"
MUTED = "#6d8179"
TEAL = "#167d67"
LINE = "#dce5df"
FONT = "DejaVu Sans"
PALETTE = ["#16836c", "#e7a13b", "#447cbb", "#b95d75", "#7865b8", "#59a6a1", "#aa783e", "#a970ba", "#508640", "#697897"]
SUGGESTION_KINDS = {"from_account": "account", "to_account": "account",
                    "name": "name", "category": "category", "tags": "tag", "units": "unit"}


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
        # Tk's own entry/dialog bindings can ring too. Silence this app's
        # interpreter only; leave the desktop's sound settings alone.
        self.tk.eval("proc bell {args} {}")
        self.repo = repo
        self.history = SessionHistory(repo)
        self._committing = False
        self._loading = False
        self._active_edit = None
        self._active_edit_history = None
        self._draft_redo = None
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
        self.form_vars = {k: tk.StringVar(self) for k in ("date", "name", "from_account", "to_account", "amount", "units", "status", "category", "tags")}
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
        self._build_experimental()
        for entry_class in ("TEntry", "Entry", "TCombobox"):
            self.bind_class(entry_class, "<Control-a>", self._select_all_entry)
        self.bind_class("Text", "<Control-a>", self._select_all_text)
        self.bind("<Control-n>", self._shortcut_new)
        self.bind("<Control-s>", self._shortcut_save)
        for cls in ("Text", "TEntry", "Entry", "TCombobox", "Treeview"):
            self.bind_class(cls, "<Control-z>", self.undo)
            self.bind_class(cls, "<Control-y>", self.redo)
        self.bind("<Control-z>", self.undo)
        self.bind("<Control-y>", self.redo)
        self.bind("<Escape>", self.exit_fullscreen)
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.new_flow(focus=False, confirm=False)
        self.new_account(focus=False, confirm=False)
        self._bind_form_edits()
        self.refresh_all()
        self.show_view("flows")
        self._build_view_history()
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
        for key, title in (("flows", "Flows"), ("accounts", "Accounts"), ("charts", "Graphs"), ("data", "Data"), ("experimental", "Experimental")):
            button = tk.Button(bar, text=title, font=(FONT, 10, "bold"), padx=17, pady=8,
                               bg=CARD, fg=MUTED, relief="flat", borderwidth=0,
                               activebackground="#e5efe9", activeforeground=INK,
                               command=lambda k=key: self.show_view(k))
            button.pack(side="left", padx=2)
            self.nav_buttons[key] = button
        context = ttk.Frame(bar, style="Card.TFrame")
        context.pack(side="right")
        self.undo_button = ttk.Button(context, text="Undo", style="Small.TButton", command=self.undo, state="disabled")
        self.undo_button.pack(side="left", padx=3)
        self.redo_button = ttk.Button(context, text="Redo", style="Small.TButton", command=self.redo, state="disabled")
        self.redo_button.pack(side="left", padx=3)
        if self.demo:
            ttk.Label(context, text="Demo", style="CardMuted.TLabel").pack(side="left", padx=(0, 18))
        self.view_host = ttk.Frame(self, padding=(16, 14, 16, 0))
        self.view_host.grid(row=1, column=0, sticky="nsew")
        self.view_host.rowconfigure(0, weight=1)
        self.view_host.columnconfigure(0, weight=1)
        self.views = {}
        for name in ("flows", "accounts", "charts", "data", "experimental"):
            self.views[name] = ttk.Frame(self.view_host)
            self.views[name].grid(row=0, column=0, sticky="nsew")
        footer = ttk.Frame(self, padding=(20, 7))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_message, style="Muted.TLabel", font=(FONT, 8), width=1).grid(row=0, column=0, sticky="ew")
        ttk.Label(footer, text="Local", style="Muted.TLabel", font=(FONT, 8)).grid(row=0, column=1, sticky="e")

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
        for widget in (*self.form_widgets.values(), *getattr(self, "account_widgets", {}).values()):
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
            values[list(self.account_vars).index("date")] = normalize_date(self._account_when())
        except ValueError:
            pass  # Invalid input still differs and remains available to fix.
        return tuple(values)

    def _account_when(self):
        return self.account_vars["date"].get().strip()

    def _build_view_history(self):
        """Register UI snapshots in the same session history as record edits."""
        view = self.view_history = ViewHistory(self)
        for kind in ("flow", "account"):
            view.register("draft:" + kind, lambda k=kind: self._draft_state(k),
                          lambda value, k=kind: self._put_draft_state(k, value))
        for key, choice in self.ledger_filters.items():
            view.register("filter:" + key, choice.get,
                          lambda values, c=choice: self._put_filter(c, values))
            view.register("query:" + key, choice.query_variable.get,
                          lambda text, c=choice: self._put_choice_query(c, text))
            choice.query_variable.trace_add("write", lambda *args, k=key:
                view.record("query:" + k, "Search filter choices", merge="typing"))
        view.register("search", self.search_var.get, self._put_search)
        view.watch_entry(self.search_entry)
        view.register("dates", lambda: (self.filter_start.get(), self.filter_end.get()),
                      self._put_date_filters)
        view.register("sort", lambda: (self.sort_column, self.sort_reverse), self._put_sort)
        for key, tree in (("ledger", self.ledger), ("accounts", self.accounts_tree)):
            view.register("columns:" + key, lambda t=tree: displayed_columns(t),
                          lambda values, t=tree: self._put_column_order(t, values))
            view.register("widths:" + key,
                          lambda t=tree: tuple((c, t.column(c, "width")) for c in t["columns"]),
                          lambda values, t=tree: self._put_column_widths(t, values))
        view.register("graph", self._graph_state, self._put_graph_state)
        view.register("experimental", self.experimental.get_state, self.experimental.put_state)
        for variable, key in ((self.chart_basis, "basis"), (self.chart_start, "start"),
                              (self.chart_end, "end"), (self.chart_combined, "combined")):
            variable.trace_add("write", lambda *args, k=key:
                self._graph_setting_changed(k))
        for widget in (self.chart_basis_widget, self.chart_start_picker.entry,
                       self.chart_end_picker.entry):
            view.watch_entry(widget)
        self.chart_accounts.bind("<<ListboxSelect>>", self._graph_accounts_changed)
        self._chart_limit_pending = None
        self.canvas.mpl_connect("button_press_event", lambda event: view.begin())
        self._watch_chart_limits()

    def _draft_state(self, kind):
        values = (self._flow_snapshot() if kind == "flow" else
                  tuple(v.get() for v in self.account_vars.values()))
        title = self.form_title if kind == "flow" else self.account_title
        return self._draft_identity(kind), values, getattr(self, "_clean_" + kind), title.cget("text")

    def _put_draft_state(self, kind, state):
        identity, values, clean, title = state
        self._close_popups()
        self._loading = True
        try:
            if kind == "flow":
                self.ledger_editor.close(commit=False)
                self._restore_draft_row()
                self.editing_id = identity
                for variable, value in zip(self.form_vars.values(), values):
                    variable.set(value)
                # Leave a shared Notes peer open; all views show this buffer.
                self.notes_text.delete("1.0", "end")
                self.notes_text.insert("1.0", values[-1])
                self.notes_text.edit_modified(False)
                self.form_title.configure(text=title)
                self.save_button.configure(state="disabled" if identity else "normal")
                self.form_feedback.set("Edits save automatically" if identity else "From, To, Amount, and Units required")
                self._set_flow_selection(identity)
            else:
                self.account_cell_editor.close(commit=False)
                self._restore_account_draft_row()
                self.account_editing = identity
                for variable, value in zip(self.account_vars.values(), values):
                    variable.set(value)
                self.account_title.configure(text=title)
                self.account_name_entry.configure(state="disabled" if identity else "normal")
                self.account_widgets["units"].entry.configure(state="disabled" if identity else "normal")
                self.account_widgets["units"].arrow.configure(state="disabled" if identity else "normal")
                self.account_save_button.configure(state="disabled" if identity else "normal")
                self.account_feedback.set("Blank = unknown")
                self._set_account_selection(identity)
            setattr(self, "_clean_" + kind, clean)
            self._active_edit = None
            self._draft_redo = None
        finally:
            self._loading = False
        (self._sync_flow_draft if kind == "flow" else self._sync_account_draft)()

    def _remember_draft(self, kind):
        if hasattr(self, "view_history"):
            self.view_history.remember("draft:" + kind)

    def _unadded_draft(self, kind):
        if not hasattr(self, "view_history") or self._draft_identity(kind):
            return None
        state = self._draft_state(kind)
        values = self._account_snapshot() if kind == "account" else state[1]
        if values != state[2] or state[3] == "Repeat Flow":
            return state

    def _record_discarded_draft(self, kind, before):
        if before is not None:
            self.view_history.record("draft:" + kind, "Discard new " + kind, before=before)

    def _record_draft(self, kind, args=()):
        if self._draft_identity(kind) or not hasattr(self, "view_history"):
            return
        variables = self.form_vars if kind == "flow" else self.account_vars
        key = next((key for key, variable in variables.items()
                    if args and str(variable) == args[0]), "notes")
        self.view_history.record("draft:" + kind, "Type " + kind, merge=key)

    def _put_filter(self, choice, values):
        choice.set_selected(values)
        self.refresh_ledger()

    def _put_choice_query(self, choice, text):
        choice.query_variable.set(text)
        choice._restore_query = choice.popup is None

    def _open_filter_query(self, choice):
        if not choice._restore_query:
            with self.view_history.suspended():
                choice.query_variable.set("")
        choice._restore_query = False
        key = next(key for key, widget in self.ledger_filters.items() if widget is choice)
        self.view_history.remember("query:" + key)

    def _ledger_filter_changed(self, key):
        self.refresh_ledger()
        if hasattr(self, "view_history"):
            self.view_history.record("filter:" + key, key + " filter")

    def _search_changed(self, *args):
        self.refresh_ledger()
        if hasattr(self, "view_history"):
            self.view_history.record("search", "Ledger search", merge="typing")

    def _put_search(self, value):
        self.search_var.set(value)
        self.refresh_ledger()

    def _put_date_filters(self, values):
        self.filter_start.set(values[0])
        self.filter_end.set(values[1])
        self.refresh_ledger()

    def _put_sort(self, value):
        self.sort_column, self.sort_reverse = value
        self.refresh_ledger()

    def _prepare_column_move(self, kind):
        self._finish_existing(kind)
        editor = self.ledger_editor if kind == "flow" else self.account_cell_editor
        editor.close(commit=False)
        self._close_popups()

    def _put_column_order(self, tree, order):
        editor = self.ledger_editor if tree is self.ledger else self.account_cell_editor
        editor.close(commit=False)
        tree.configure(displaycolumns=order)

    def _columns_moved(self, key, before):
        if hasattr(self, "view_history"):
            self.view_history.record("columns:" + key, "Move columns", before=before)

    def _put_column_widths(self, tree, widths):
        editor = self.ledger_editor if tree is self.ledger else self.account_cell_editor
        editor.close(commit=False)
        for key, width in widths:
            tree.column(key, width=width)

    def _columns_resized(self, key, before):
        if hasattr(self, "view_history"):
            self.view_history.record("widths:" + key, "Resize columns", before=before)

    def _bind_history_popup(self, popup):
        popup.bind("<Control-z>", self.undo)
        popup.bind("<Control-y>", self.redo)

    def _confirm_changes(self, kind):
        """Finish an existing edit; new drafts may be replaced without a prompt."""
        return self._finish_existing(kind)

    def _bind_form_edits(self):
        for key, widget in self.form_widgets.items():
            self._bind_field(widget, "flow", key)
        self._bind_field(self.notes_text, "flow", "notes")
        for key, widget in self.account_widgets.items():
            self._bind_field(widget, "account", key)

    def _bind_field(self, widget, kind, key):
        entry = widget.entry if isinstance(widget, FilterCombo) else widget
        entry.bind("<FocusIn>", lambda event: self._begin_field(kind, key), add="+")
        entry.bind("<FocusOut>", lambda event: self._defer_field_finish(widget, kind, key), add="+")
        entry.bind("<Escape>", lambda event: self._escape_field(widget, kind, key), add="+")
        if key != "notes" and not isinstance(widget, FilterCombo):
            entry.bind("<Return>", lambda event: self._field_enter(kind, key))
            entry.bind("<KP_Enter>", lambda event: self._field_enter(kind, key))
        entry.bind("<Control-Return>", lambda event: self._field_enter(kind, key))
        if isinstance(widget, ttk.Combobox):
            widget.bind("<<ComboboxSelected>>", lambda event: self._field_enter(kind, key))

    def _draft_identity(self, kind):
        return self.editing_id if kind == "flow" else self.account_editing

    def _field_value(self, kind, key):
        if kind == "flow" and key == "notes":
            return self.notes_text.get("1.0", "end-1c")
        variables = self.form_vars if kind == "flow" else self.account_vars
        return variables[key].get()

    def _put_field_value(self, kind, key, value):
        if kind == "flow" and key == "notes":
            self.notes_text.delete("1.0", "end")
            self.notes_text.insert("1.0", value)
            return
        variables = self.form_vars if kind == "flow" else self.account_vars
        variables[key].set(value)

    def _begin_field(self, kind, key):
        if self._loading or self._committing:
            return
        identity = self._draft_identity(kind)
        token = (kind, key, identity)
        if self._active_edit is None or self._active_edit[:3] != token:
            before = self._field_value(kind, key)
            if identity and kind == "flow" and hasattr(self, "_clean_flow"):
                values = dict(zip(self.form_vars, self._clean_flow))
                before = (self._clean_flow[-1] if key == "notes" else
                          values[key])
            elif identity and kind == "account":
                saved = self._account_record(identity)
                if saved:
                    if key == "opening":
                        before = "" if saved["opening_amount"] is None else format_quantity(saved["opening_amount"]).replace(",", "")
                    elif key == "date":
                        before = saved["opening_at"]
            self._active_edit = token + (before,)
            self._active_edit_history = self.history.undo_token
            if hasattr(self, "view_history"):
                self.view_history.begin()

    def _defer_field_finish(self, widget, kind, key):
        identity = self._draft_identity(kind)
        def finish():
            if self._loading or self._committing or identity != self._draft_identity(kind):
                return
            focused = str(self.tk.call("focus"))
            entry = widget.entry if isinstance(widget, FilterCombo) else widget
            if focused == str(entry) or focused.startswith(str(widget) + "."):
                return
            popup = widget.popup if isinstance(widget, FilterCombo) else None
            if popup is not None and (focused == str(popup) or focused.startswith(str(popup) + ".")):
                return
            if key == "notes" and getattr(self, "notes_dialog", None) is not None:
                return
            if key == "date":
                self._normalize_date_field(kind)
            self._finish_existing(kind)
        self.after_idle(finish)

    def _field_enter(self, kind, key):
        if key == "date":
            self._normalize_date_field(kind)
        self._finish_existing(kind)
        return "break"

    def _escape_field(self, widget, kind, key):
        if isinstance(widget, FilterCombo) and widget.popup is not None:
            widget.close_popup()
            return "break"
        if not self._draft_identity(kind):
            return None  # A new draft survives Escape; the window may exit fullscreen.
        self._begin_field(kind, key)
        token = self._active_edit
        if token and self._field_value(kind, key) != token[3]:
            self._revert_field(kind, key)
            return "break"
        return None

    def _revert_field(self, kind, key):
        token = self._active_edit
        if token and token[:3] == (kind, key, self._draft_identity(kind)):
            self._put_field_value(kind, key, token[3])
        elif self._draft_identity(kind):
            self._reload_current(kind)
        self._draft_redo = None
        if kind == "flow":
            self.form_feedback.set("Edits save automatically" if self.editing_id else "From, To, Amount, and Units required")
        else:
            self.account_feedback.set("Blank = unknown")

    def _draft_is_dirty(self):
        token = self._active_edit
        return bool(token and token[2] is not None and
                    self._active_edit_history is self.history.undo_token and
                    token[2] == self._draft_identity(token[0]) and
                    self._field_value(token[0], token[1]) != token[3])

    def _update_history_buttons(self):
        draft = self._draft_is_dirty()
        if self._draft_redo:
            kind, key, identity, before, after = self._draft_redo
            if identity != self._draft_identity(kind) or self._field_value(kind, key) != before:
                self._draft_redo = None
        self.undo_button.configure(state="normal" if draft or self.history.can_undo else "disabled")
        redo = bool(self._draft_redo or self.history.can_redo)
        if draft and self.history.redo_affects_records:
            redo = False
        self.redo_button.configure(state="normal" if redo else "disabled")

    def _reload_current(self, kind):
        if kind == "flow":
            flow = self.repo.get_flow(self.editing_id) if self.editing_id else None
            if flow:
                self._load_form(flow)
            else:
                self.new_flow(focus=False, confirm=False)
        else:
            account = self._account_record(self.account_editing)
            if account:
                self._load_account(account)
            else:
                self.new_account(focus=False, confirm=False)

    def _undo_draft(self):
        token = self._active_edit
        if self._draft_is_dirty():
            kind, key, identity, before = token
            after = self._field_value(kind, key)
            if after != before:
                self._put_field_value(kind, key, before)
                self._draft_redo = (kind, key, identity, before, after)
                self.status_message.set("Undid typing")
                self._update_history_buttons()
                return True
        return False

    def _history_event_allowed(self, event):
        if event is None:
            return self.grab_current() is None
        top = str(self.tk.call("winfo", "toplevel", str(event.widget)))
        notes = getattr(self, "notes_dialog", None)
        allowed = {str(self), str(notes)}
        for attribute in ("_flow_check_filter", "_flow_filter_combo", "_flow_calendar"):
            control = getattr(self, attribute, None)
            popup = getattr(control, "popup", None)
            if popup is not None:
                allowed.add(str(popup))
        return self.grab_current() is None and top in allowed

    def undo(self, event=None):
        if not self._history_event_allowed(event):
            return "break"
        if self._undo_draft():
            return "break"
        return self._history_step(False)

    def redo(self, event=None):
        if not self._history_event_allowed(event):
            return "break"
        if self._draft_redo:
            kind, key, identity, before, after = self._draft_redo
            self._draft_redo = None
            if identity == self._draft_identity(kind) and self._field_value(kind, key) == before:
                self._put_field_value(kind, key, after)
                self.status_message.set("Redid typing")
                self._update_history_buttons()
                return "break"
        if self._draft_is_dirty() and self.history.redo_affects_records:
            return "break"
        return self._history_step(True)

    def _history_step(self, redo):
        if not (self.history.can_redo if redo else self.history.can_undo):
            return "break"
        records = self.history.redo_affects_records if redo else self.history.undo_affects_records
        action_label = self.history.redo_label if redo else self.history.undo_label
        self.view_history.begin()
        # Restoring a view must not reload or save a record's unfinished fields.
        self._committing = True
        try:
            if records:
                self.ledger_editor.close(commit=False)
                self.account_cell_editor.close(commit=False)
                self._close_notes_editor(commit=False)
                self._close_popups()
            elif action_label in ("Remove suggestion", "Restore suggestion"):
                self._close_popups()
            label = self.history.redo() if redo else self.history.undo()
            if label is not None:
                if records:
                    self.refresh_all()
                    if self.editing_id:
                        self._reload_current("flow")
                    if self.account_editing:
                        self._reload_current("account")
                    self._active_edit = None
                self._restore_draft_row()
                self._restore_account_draft_row()
                self._sync_flow_draft()
                self._sync_account_draft()
                self.refresh_suggestions()
                self.status_message.set(("Redid: " if redo else "Undid: ") + label)
            self._update_history_buttons()
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._feedback(str(exc), True)
        finally:
            self._committing = False
        self._begin_focused_field()
        return "break"

    def _begin_focused_field(self):
        focused = str(self.tk.call("focus"))
        for kind, widgets in (("flow", {**self.form_widgets, "notes": self.notes_text}),
                              ("account", self.account_widgets)):
            for key, widget in widgets.items():
                entry = widget.entry if isinstance(widget, FilterCombo) else widget
                if focused == str(entry):
                    self._begin_field(kind, key)

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
        # Ledger writes already commit immediately. Saving must never turn an
        # unadded draft into a record or validate it as though Add was pressed.
        self._close_popups()
        kind = {"flows": "flow", "accounts": "account"}.get(self.current_view)
        if kind and not self._finish_existing(kind):
            return "break"
        self.status_message.set("Saved")
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
            self.experimental.close()
            self._close_popups()
            self.history.close()
            self.destroy()

    def show_view(self, name):
        if name != self.current_view:
            old_view = self.current_view
            if old_view in {"flows", "accounts"}:
                kind = "flow" if old_view == "flows" else "account"
                before = self._unadded_draft(kind)
                if not self._confirm_changes(kind):
                    return False
                if old_view == "flows":
                    self.new_flow(focus=False, confirm=False)
                else:
                    self.new_account(focus=False, confirm=False)
                self._record_discarded_draft(kind, before)
            if old_view == "experimental":
                self.experimental.pause()
                self.experimental.hide_hover()
            self.focus_set()
        self._close_popups()
        self.current_view = name
        self.views[name].tkraise()
        for key, button in self.nav_buttons.items():
            button.configure(bg="#e1eee7" if key == name else CARD, fg=TEAL if key == name else MUTED)
        if name == "charts":
            if self._chart_auto_dates:
                self._fit_chart_dates()
            self.draw_chart()
        elif name == "experimental":
            self.experimental.refresh()
        return True

    def _repeat_row_click(self, event):
        """A rapid second click must select its row, just like a first click."""
        tree = event.widget
        row = tree.identify_row(event.y)
        if not row and tree is self.ledger:
            return self._ledger_background_click(event)
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

    def _normalize_date_field(self, kind):
        variables = self.form_vars if kind == "flow" else self.account_vars
        try:
            when = normalize_flow_date(variables["date"].get().strip())
        except ValueError:
            return  # Leave invalid input for the ordinary visible validation.
        variables["date"].set(when)

    @staticmethod
    def _scroll_notes(event, direction=None):
        """Keep one-line notes readable using the wheel on each platform."""
        if direction is None:
            direction = -1 if event.delta > 0 else 1
        event.widget.yview_scroll(direction, "units")
        return "break"

    def _close_notes_editor(self, focus=False, commit=True):
        dialog = getattr(self, "notes_dialog", None)
        if dialog is None:
            return
        if commit and not self._finish_existing("flow"):
            return False
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
        self._bind_field(text, "flow", "notes")
        text.bind("<Escape>", lambda event: (self._close_notes_editor(focus=True), "break")[1])
        text.bind("<Control-z>", self.undo)
        text.bind("<Control-y>", self.redo)
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
        self.form_feedback = tk.StringVar(self, "From, To, Amount, and Units required")
        ttk.Label(header, textvariable=self.form_feedback, style="CardMuted.TLabel", width=1).pack(side="left", fill="x", expand=True, padx=18)
        self.save_button = ttk.Button(header, text="Add Flow", style="Primary.TButton", command=self.save_flow)
        self.save_button.pack(side="right")
        self.new_flow_button = ttk.Button(header, text="New Flow", style="Small.TButton", command=self.new_flow)
        self.new_flow_button.pack(side="right")
        form = ttk.Frame(editor, style="Card.TFrame")
        form.grid(row=1, column=0, sticky="ew")
        cells = []
        for key, label, kind in (("date", "Date/Time", "entry"), ("name", "Name", "suggest"), ("from_account", "From", "suggest"), ("to_account", "To", "suggest"),
                                 ("amount", "Amount", "entry"), ("units", "Units", "suggest"), ("status", "Status", "choice"),
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
        self.form_widgets["tags"].multi_value = True
        self.flow_cells = cells
        self._reflow_cells(form, cells, [17, 19, 16, 16, 11, 10, 11, 14, 16, 21])
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
        self.search_var.trace_add("write", self._search_changed)
        self.filter_start = tk.StringVar(self)
        self.filter_end = tk.StringVar(self)
        self.ledger_filters = {}
        filters = ttk.Frame(ledger_card, style="Card.TFrame")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        filters.columnconfigure(0, weight=1)
        primary = ttk.Frame(filters, style="Card.TFrame")
        primary.grid(row=0, column=0, sticky="ew")
        ttk.Label(primary, text="Search", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ttk.Entry(primary, textvariable=self.search_var, width=20)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        for col, key, label, values in ((2, "From", "From", ()),
                                        (3, "To", "To", ()),
                                        (4, "Status", "Status", STATUSES),
                                        (5, "Category", "Categories", ()),
                                        (6, "Tag", "Tags", ()),
                                        (7, "Units", "Units", ())):
            primary.columnconfigure(col, weight=1, uniform="ledger_filters")
            choice = CheckFilter(primary, label=label, values=values,
                                 all_label=f"{label}: All",
                                 on_change=lambda k=key: self._ledger_filter_changed(k))
            # Search has a compact fixed request. The six filter buttons share
            # the remaining width, including in the smallest supported window.
            choice.button.configure(width=1)
            choice.grid(row=0, column=col, sticky="ew", padx=(10, 0))
            self.ledger_filters[key] = choice
        ttk.Button(primary, text="Dates…", style="Small.TButton", command=self.edit_date_filters).grid(row=0, column=8, padx=(10, 0))
        self.date_filter_label = ttk.Label(top, text="All Dates", style="CardMuted.TLabel")
        self.date_filter_label.pack(side="right")
        tree_box = ttk.Frame(ledger_card, style="Card.TFrame")
        tree_box.grid(row=2, column=0, sticky="nsew")
        tree_box.rowconfigure(0, weight=1)
        tree_box.columnconfigure(0, weight=1)
        specs = [("date", "Date", 13), ("name", "Name", 20), ("from_account", "From", 15), ("to_account", "To", 15), ("amount", "Amount", 11), ("units", "Units", 9),
                 ("status", "Status", 10), ("category", "Category", 13), ("tags", "Tags", 17), ("notes", "Notes", 24)]
        self.ledger = ttk.Treeview(tree_box, columns=[spec[0] for spec in specs], show="headings", selectmode="browse")
        for key, label, weight in specs:
            self.ledger.heading(key, text=label, anchor="e" if key == "amount" else "w", command=lambda k=key: self.sort_ledger(k))
            self.ledger.column(key, width=80, minwidth=20, anchor="e" if key == "amount" else "w", stretch=False)
        self._fit_columns(self.ledger, {k: w for k, label, w in specs})
        self.ledger.grid(row=0, column=0, sticky="nsew")
        def scroll_ledger(*args):
            if self.ledger_editor.close():
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
        self.ledger.tag_configure("Canceled", foreground="#92a098")
        self.ledger.bind("<<TreeviewSelect>>", self._select_flow)
        self.ledger.bind("<Button-1>", self._ledger_background_click)
        self.ledger.bind("<Triple-1>", self._repeat_row_click)
        # Bind to the table only: Delete inside a form/cell/Notes edits text.
        self.ledger.bind("<Delete>", self.delete_selected)
        self.ledger.bind("<Control-c>", self.copy_selected)
        self.ledger.bind("<Control-v>", self.paste_flow)
        # Do not bind <<Paste>>: Tk also maps Ctrl+Y to it on X11,
        # which would intercept this application's Redo shortcut.
        self.ledger_tooltip = TreeTooltip(self.ledger, self.flow_tooltip)
        self.ledger_editor = LedgerEditor(self, _NotesPeer,
            font=FONT, background="#fbfcfa", foreground=INK)
        self.ledger_column_drag = ColumnDrag(self.ledger,
            prepare=lambda: self._prepare_column_move("flow"),
            changed=lambda before: self._columns_moved("ledger", before),
            resized=lambda before: self._columns_resized("ledger", before),
            click=self.sort_ledger)
        actions = ttk.Frame(ledger_card, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 8))
        for label, command in [("JSON record", self.show_flow_json)]:
            ttk.Button(actions, text=label, command=command, style="Small.TButton").pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Category Totals", style="Small.TButton", command=self.show_report).pack(side="right")
        self.report_summary = tk.StringVar(self, "")
        ttk.Label(actions, textvariable=self.report_summary, style="CardMuted.TLabel").pack(side="right", padx=14)
        self.selection_detail = tk.StringVar(self, "Double-click to edit · Ctrl+C / Ctrl+V copies rows · Delete removes · Changes save automatically")
        self.selection_label = ttk.Label(ledger_card, textvariable=self.selection_detail, style="CardMuted.TLabel", width=1)
        self.selection_label.grid(row=4, column=0, sticky="ew")

    def _ledger_background_click(self, event):
        """Empty ledger space starts a fresh entry; headings still sort/resize."""
        if (self.ledger.identify_region(event.x, event.y) == "nothing"
                and not self.ledger.identify_row(event.y)):
            before = self._unadded_draft("flow")
            self._finish_existing("flow")
            self.new_flow(confirm=False)
            self._record_discarded_draft("flow", before)
            self.status_message.set("Ready")
            return "break"

    def flow_tooltip(self, item):
        flow = self.ledger_rows.get(item)
        if not flow:
            return None
        when = flow["date"]
        return (f"{flow['name']}\n{when}\n{flow['from_account']} → {flow['to_account']} · {format_quantity(flow['amount'])} {flow['units']}"
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
                values = [normalize_flow_date(v.get().strip()) if v.get().strip() else "" for v in variables]
                normalized = [normalize_date(v) if v else None for v in values]
                if normalized[1] and len(values[1]) == 10:
                    normalized[1] = normalized[1][:11] + "23:59:59"
                if all(normalized) and normalized[0] > normalized[1]:
                    raise ValueError("End must be on or after start.")
                self._put_date_filters(values)
                self.view_history.record("dates", "Ledger dates")
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
        self.form_feedback.set(text if error else "From, To, Amount, and Units required")

    def new_flow(self, focus=True, confirm=True, clear_selection=True):
        before = self._draft_state("flow") if hasattr(self, "view_history") and not self.editing_id else None
        if confirm and not self._finish_existing("flow"):
            return False
        if focus and not self.show_view("flows"):
            return False
        self._loading = True
        try:
            self.ledger_editor.close(commit=False)
            self._restore_draft_row()
            self._close_notes_editor(commit=False)
            self._close_popups()
            self.editing_id = None
            self._active_edit = None
            self._draft_redo = None
            defaults = {"date": datetime.now().strftime("%Y-%m-%d"), "from_account": "", "to_account": "", "amount": "", "units": "USD", "status": "Complete", "name": "", "category": "Uncategorized", "tags": ""}
            for key, value in defaults.items():
                self.form_vars[key].set(value)
            self.notes_text.delete("1.0", "end")
            self.form_title.configure(text="New Flow")
            self.save_button.configure(text="Add Flow", state="normal")
            self.form_widgets["status"].configure(values=STATUSES)
            self.form_feedback.set("From, To, Amount, and Units required")
            self._clean_flow = self._flow_snapshot()
            if clear_selection:
                self._set_flow_selection(None)
        finally:
            self._loading = False
        if confirm and before is not None:
            self.view_history.record("draft:flow", "Clear new flow", before=before)
        self._remember_draft("flow")
        if focus:
            self.form_widgets["date"].focus_set()
            self._begin_field("flow", "date")
        return True

    def _flow_data(self):
        data = {key: var.get().strip() for key, var in self.form_vars.items()}
        data["amount"] = quantity_text(parse_quantity(data["amount"]))
        try:
            data["tags"] = [tag.strip() for tag in next(csv.reader([data["tags"]], skipinitialspace=True, strict=True)) if tag.strip()]
        except csv.Error as exc:
            raise ValueError("Tags use commas; quote a tag that itself contains a comma.") from exc
        data["notes"] = self.notes_text.get("1.0", "end-1c")
        if self.editing_id:
            data["flow_id"] = self.editing_id
        return data

    def _finish_existing(self, kind):
        """Save a valid completed field, retaining selection and showing errors inline."""
        if self._loading or self._committing or not self._draft_identity(kind):
            return True
        snapshot = self._flow_snapshot() if kind == "flow" else self._account_snapshot()
        if snapshot == getattr(self, "_clean_" + kind, snapshot):
            return True
        try:
            self._committing = True
            if kind == "flow":
                data = self._flow_data()
                result = self.history.perform("Edit flow", lambda: self.repo.save_flow(data))
                self._loading = True
                try:
                    for key in ("date", "from_account", "to_account", "name", "units", "category", "status"):
                        self.form_vars[key].set(result[key])
                    self.form_vars["amount"].set(format_quantity(result["amount"]).replace(",", ""))
                finally:
                    self._loading = False
                self._clean_flow = self._flow_snapshot()
                self.form_feedback.set("Edits save automatically")
                message = "Updated: " + result["name"]
            else:
                raw = self.account_vars["opening"].get().strip()
                amount = quantity_text(parse_quantity(raw, allow_negative=True)) if raw else None
                when = normalize_date(self._account_when())
                account = self._account_record(self.account_editing)
                result = self.history.perform("Edit account", lambda: self.repo.save_account(account["name"], opening_amount=amount, opening_at=when, units=account["units"]))
                self._clean_account = self._account_snapshot()
                self.account_feedback.set("Edits save automatically · Blank = unknown")
                message = "Updated: " + result["name"]
            # A commit establishes a fresh undo boundary for continued typing.
            token = self._active_edit
            if token and token[0] == kind and token[2] == self._draft_identity(kind):
                self._active_edit = token[:3] + (self._field_value(kind, token[1]),)
                self._active_edit_history = self.history.undo_token
            self._draft_redo = None
            self.refresh_all()
            self.status_message.set(message)
            self._update_history_buttons()
            return True
        except (ValueError, sqlite3.Error) as exc:
            if kind == "flow":
                self._feedback(str(exc), True)
            else:
                self.account_feedback.set(str(exc))
                self.status_message.set(str(exc))
            return False
        finally:
            self._committing = False

    def save_flow(self):
        """Add a new/repeated draft; existing rows save as fields are finished."""
        self._close_popups()
        if self.editing_id:
            return self._finish_existing("flow")
        try:
            result = self.history.perform("Add flow", lambda: self.repo.save_flow(self._flow_data()))
            self.new_flow(focus=False, confirm=False)
            self.refresh_all()
            if self.ledger.exists(result["flow_id"]):
                self.ledger.see(result["flow_id"])
            self.form_widgets["date"].focus_set()
            self._begin_field("flow", "date")
            self._feedback(f"Added: {result['name']} · {format_quantity(result['amount'])} {result['units']}")
            self._update_history_buttons()
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
        self.new_flow(focus=False, confirm=False, clear_selection=repeat)
        self._loading = True
        try:
            self.editing_id = None if repeat else flow["flow_id"]
            for key in ("date", "from_account", "to_account", "name", "units", "category", "status"):
                self.form_vars[key].set(flow[key])
            self.form_vars["amount"].set(format_quantity(flow["amount"]).replace(",", ""))
            tag_stream = io.StringIO()
            csv.writer(tag_stream, lineterminator="").writerow(flow.get("tags", []))
            self.form_vars["tags"].set(tag_stream.getvalue())
            self.notes_text.insert("1.0", flow.get("notes", ""))
            self.form_title.configure(text="Repeat Flow" if repeat else "Edit Flow")
            self.save_button.configure(state="normal" if repeat else "disabled")
            self.form_feedback.set("New copy · Click Add Flow" if repeat else "Edits save automatically")
            self._clean_flow = self._flow_snapshot()
            if not repeat:
                self._set_flow_selection(flow["flow_id"])
        finally:
            self._loading = False
        self._remember_draft("flow")
        self._active_edit = None
        self._close_popups()
        self._begin_focused_field()

    def _select_flow(self, event=None):
        if self._loading or self._committing:
            return
        selection = self.ledger.selection()
        selected = selection[0] if selection else None
        if selected == self._flow_selection:
            return
        before = self._unadded_draft("flow")
        old = self._flow_selection
        if not self._finish_existing("flow"):
            self._set_flow_selection(old)
            return
        self.ledger_editor.close(commit=False)
        self._set_flow_selection(selected)
        flow = self.repo.get_flow(selected) if selected else None
        if flow:
            self._load_form(flow)
        else:
            self.new_flow(focus=False, confirm=False)
        self._record_discarded_draft("flow", before)

    def repeat_selected(self):
        selected = self.ledger.selection()
        if not selected or not self._finish_existing("flow"):
            return
        flow = self.repo.get_flow(selected[0])
        if flow:
            before = self._draft_state("flow")
            self._load_form(flow, repeat=True)
            self.view_history.record("draft:flow", "Repeat flow", before=before)

    def copy_selected(self, event=None):
        flow = self._selected()
        if not flow or not self._finish_existing("flow"):
            return "break"
        flow = self.repo.get_flow(flow["flow_id"])
        if flow:
            self.clipboard_clear()
            self.clipboard_append(json.dumps(flow, ensure_ascii=False))
            self.status_message.set("Flow copied")
        return "break"

    def paste_flow(self, event=None):
        """Insert one clipboard row under a new ID, then open the saved copy."""
        try:
            data = json.loads(self.clipboard_get())
            fields = {"date", "name", "from_account", "to_account", "amount", "units",
                      "status", "category", "tags", "notes"}
            if not isinstance(data, dict) or set(data) not in (fields, fields | {"flow_id"}):
                raise ValueError("Clipboard must contain one complete flow row.")
            data.pop("flow_id", None)
            if not self._finish_existing("flow"):
                return "break"
            result = self.history.perform("Paste flow", lambda: self.repo.save_flow(data))
            self.new_flow(focus=False, confirm=False)
            self.refresh_all()
            self._load_form(result)
            if self.ledger.exists(result["flow_id"]):
                self.ledger.see(result["flow_id"])
                self.status_message.set("Flow pasted")
            else:
                self.status_message.set("Flow pasted · Hidden by current filters")
            self._update_history_buttons()
        except (tk.TclError, json.JSONDecodeError):
            self._feedback("Clipboard must contain one complete flow row.", True)
        except (ValueError, sqlite3.Error) as exc:
            self._feedback(str(exc), True)
        return "break"

    def void_selected(self):
        flow = self._selected()
        if not flow or not self._finish_existing("flow"):
            return
        try:
            self.history.perform("Void flow", lambda: self.repo.void_flow(flow["flow_id"]))
            self.new_flow(focus=False, confirm=False)
            self.refresh_all()
            self._update_history_buttons()
            self.status_message.set("Flow canceled · Ctrl+Z to undo")
        except (ValueError, sqlite3.Error) as exc:
            self._feedback(str(exc), True)

    def delete_selected(self, event=None):
        """Remove a saved row; session history restores its exact saved record."""
        flow = self._selected()
        if not flow:
            return "break"
        self.ledger_editor.close(commit=False)
        self._committing = True
        try:
            self.history.perform("Delete flow", lambda: self.repo.delete_flow(flow["flow_id"]))
            self.new_flow(focus=False, confirm=False)
            self.refresh_all()
            self._update_history_buttons()
            self.status_message.set("Flow deleted · Ctrl+Z to undo")
        except (ValueError, sqlite3.Error) as exc:
            self._feedback(str(exc), True)
        finally:
            self._committing = False
        return "break"

    def _show_selection(self, event=None):
        selection = self.ledger.selection()
        if not selection:
            self.selection_detail.set("Double-click to edit · Ctrl+C / Ctrl+V copies rows · Delete removes · Changes save automatically")
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
        if hasattr(self, "view_history"):
            self.view_history.record("sort", "Sort ledger")

    @staticmethod
    def _flow_row_values(flow):
        return (flow["date"][:10], flow["name"], flow["from_account"], flow["to_account"],
                format_display(flow["amount"], flow["units"]), flow["units"], flow["status"],
                flow["category"], ", ".join(flow["tags"]),
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
        if self._loading:
            return
        self._record_draft("flow", args)
        self._update_history_buttons()
        row = self.editing_id
        if not row or not self.ledger.exists(row):
            return
        values = self.form_vars
        amount = values["amount"].get()
        try:
            amount = format_display(parse_quantity(amount), values["units"].get())
        except ValueError:
            pass  # Show incomplete input exactly; Save supplies validation.
        notes = self.notes_text.get("1.0", "end-1c")
        displayed = (values["date"].get()[:10], values["name"].get(), values["from_account"].get(),
            values["to_account"].get(), amount, values["units"].get(), values["status"].get(),
            values["category"].get(),
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
        if not self.ledger_editor.close(commit=not self._committing):
            return
        self.ledger_tooltip.hide()
        selection = self.ledger.selection()
        query = self.search_var.get().strip().casefold()
        try:
            query_quantity = parse_quantity(query)
        except ValueError:
            query_quantity = None
        from_accounts = {value.casefold() for value in self.ledger_filters["From"].get()}
        to_accounts = {value.casefold() for value in self.ledger_filters["To"].get()}
        categories = {value.casefold() for value in self.ledger_filters["Category"].get()}
        tags = {value.casefold() for value in self.ledger_filters["Tag"].get()}
        statuses = set(self.ledger_filters["Status"].get())
        units = set(self.ledger_filters["Units"].get())
        try:
            start_text = normalize_flow_date(self.filter_start.get()) if self.filter_start.get().strip() else ""
            end_text = normalize_flow_date(self.filter_end.get()) if self.filter_end.get().strip() else ""
            date_start = normalize_date(start_text) if start_text else None
            date_end = normalize_date(end_text) if end_text else None
            if date_end and len(end_text) == 10:
                date_end = date_end[:11] + "23:59:59"
            if date_start and date_end and date_end < date_start:
                raise ValueError("Filter end must be on or after its start.")
        except ValueError as exc:
            self.status_message.set(str(exc))
            return
        flows = self.repo.list_flows()
        def matches(flow):
            haystack = " ".join(str(flow.get(key, "")) for key in ("date", "name", "from_account", "to_account", "category", "tags", "notes", "amount", "units")).casefold()
            haystack += " " + format_quantity(flow["amount"]) + " " + format_quantity(flow["amount"]).replace(",", "")
            shown = format_display(flow["amount"], flow["units"])
            haystack += " " + shown + " " + shown.replace(",", "")
            return ((query in haystack or (query_quantity is not None and query_quantity == parse_quantity(flow["amount"])))
                    and (not from_accounts or flow["from_account"].casefold() in from_accounts)
                    and (not to_accounts or flow["to_account"].casefold() in to_accounts)
                    and (not units or flow["units"] in units)
                    and (not categories or flow["category"].casefold() in categories)
                    and (not tags or tags.intersection(value.casefold() for value in flow["tags"]))
                    and (not date_start or normalize_date(flow["date"]) >= date_start)
                    and (not date_end or normalize_date(flow["date"]) <= date_end)
                    and (not statuses or flow["status"] in statuses))
        flows = [flow for flow in flows if matches(flow)]
        flows.sort(key=lambda flow: (normalize_date(flow["date"]) if self.sort_column == "date"
                                    else parse_quantity(flow["amount"]) if self.sort_column == "amount"
                                    else flow.get(self.sort_column, "")), reverse=self.sort_reverse)
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
        self.report_rows = [flow for flow in flows if flow["status"] != "Canceled"]
        for index, status in enumerate(("Complete", "Pending")):
            self.metric_vars[index].set(str(sum(f["status"] == status for f in self.report_rows)))
        units = sorted({f["units"] for f in self.report_rows if f["status"] == "Complete"})
        totals = [f"{format_display(decimal_sum(parse_quantity(f['amount']) for f in self.report_rows if f['status'] == 'Complete' and f['units'] == unit), unit)} {unit}"
                  for unit in units]
        self.report_summary.set("Complete · " + (" · ".join(totals) if totals else "0 flows"))
        if self.current_view == "experimental" and hasattr(self, "experimental"):
            self.experimental.refresh()

    def _remove_suggestion(self, field, value):
        """Forget an entry suggestion without touching the draft or records."""
        try:
            kind = SUGGESTION_KINDS[field]
            self.history.perform_external("Remove suggestion",
                lambda: self.suggestion_preferences.remove(kind, value),
                lambda: self.suggestion_preferences.restore(kind, value),
                lambda: self.suggestion_preferences.remove(kind, value))
            self._update_history_buttons()
        except (OSError, ValueError) as exc:
            self._feedback(f"Could not remove suggestion: {exc}", True)
            return False
        self.refresh_suggestions()
        self.status_message.set(f"Removed suggestion: {value}")
        return True

    def _accept_suggestion(self, field, widget=None, record_kind="flow"):
        """Explicit Enter can restore a forgotten value; refresh/save cannot."""
        widget = widget or self.form_widgets[field]
        value = widget._query()
        if not value:
            self._finish_existing(record_kind)
            return
        try:
            kind = SUGGESTION_KINDS[field]
            restored = self.history.perform_external("Restore suggestion",
                lambda: self.suggestion_preferences.restore(kind, value),
                lambda: self.suggestion_preferences.remove(kind, value),
                lambda: self.suggestion_preferences.restore(kind, value))
            self._update_history_buttons()
        except (OSError, ValueError) as exc:
            self._feedback(f"Could not restore suggestion: {exc}", True)
            return
        if restored:
            self.refresh_suggestions()
            self.status_message.set(f"Restored suggestion: {value}")
        self._finish_existing(record_kind)

    def refresh_suggestions(self):
        """Apply entry preferences while retaining all data in other views."""
        accounts = self.repo.list_accounts(referenced_only=True)
        names = list(dict.fromkeys(account["name"] for account in accounts))
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
        for key, values in [("category", self.repo.list_categories()), ("tags", self.repo.list_tags()), ("name", self.repo.list_names()), ("units", self.repo.list_units())]:
            self.form_widgets[key].set_values(self.suggestion_preferences.visible(SUGGESTION_KINDS[key], values))
        self.account_widgets["units"].set_values(self.suggestion_preferences.visible("unit", self.repo.list_units()))
        self.ledger_editor.refresh_suggestions()

    def refresh_all(self):
        self.refresh_suggestions()
        flows = self.repo.list_flows()
        for key, field in (("From", "from_account"), ("To", "to_account")):
            self.ledger_filters[key].set_values(sorted({flow[field] for flow in flows}, key=str.casefold))
        self.ledger_filters["Category"].set_values(self.repo.list_categories())
        self.ledger_filters["Tag"].set_values(self.repo.list_tags())
        self.ledger_filters["Units"].set_values(sorted({flow["units"] for flow in flows}))
        self.refresh_ledger()
        self.refresh_accounts()
        self.refresh_chart_accounts()
        if hasattr(self, "db_path_label"):
            self.db_path_label.configure(text=str(self.repo.path))
        if self.current_view == "charts":
            if self._chart_auto_dates:
                self._fit_chart_dates()
            self.draw_chart()

    def _build_experimental(self):
        self.experimental = ExperimentalView(self.views["experimental"], self.repo,
            get_flows=lambda: list(self.ledger_rows.values()),
            on_select_flow=self._open_experimental_flow,
            on_view_change=self._experimental_view_changed)
        self.experimental.pack(fill="both", expand=True)

    def _experimental_view_changed(self, before, label):
        if hasattr(self, "view_history"):
            self.view_history.record("experimental", label, before=before)
            self._update_history_buttons()

    def _open_experimental_flow(self, flow_id):
        if not self.show_view("flows") or not self.ledger.exists(flow_id):
            return
        flow = self.repo.get_flow(flow_id)
        if flow:
            self._load_form(flow)
            self.ledger.see(flow_id)
            self.ledger.focus_force()

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
        self.account_feedback = tk.StringVar(self, "Blank = unknown")
        ttk.Label(header, textvariable=self.account_feedback, style="CardMuted.TLabel", width=1).pack(side="left", fill="x", expand=True, padx=18)
        self.account_save_button = ttk.Button(header, text="Add Account", style="Primary.TButton", command=self.save_account)
        self.account_save_button.pack(side="right")
        self.account_new_button = ttk.Button(header, text="New Account", style="Small.TButton", command=self.new_account)
        self.account_new_button.pack(side="right")
        ttk.Button(header, text="JSON record", style="Small.TButton", command=self.show_account_json).pack(side="right", padx=8)
        form = ttk.Frame(editor, style="Card.TFrame")
        form.grid(row=1, column=0, sticky="ew")
        self.account_vars = {key: tk.StringVar(self) for key in ("name", "units", "opening", "date")}
        self.account_vars["units"].set("USD")
        self.account_vars["date"].set("2026-10-01")
        self.account_widgets = {}
        cells = []
        for key, label in (("name", "Account Name"), ("units", "Units"), ("opening", "Starting Balance"), ("date", "Starting Date/Time")):
            cell, heading, widget = self._entry_cell(form, label, self.account_vars[key], "suggest" if key == "units" else "entry")
            self.account_widgets[key] = widget
            cells.append(cell)
            if key == "name":
                self.account_name_entry = widget
            elif key == "units":
                widget.on_remove = lambda value: self._remove_suggestion("units", value)
                widget.on_accept = lambda w=widget: self._accept_suggestion("units", w, "account")
            elif key == "opening":
                widget.bind("<FocusIn>", self._starting_balance_focus, add="+")
        self.account_cells = cells
        self._reflow_cells(form, cells, [35, 15, 25, 25], threshold=700)
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
        specs = [("name", "Account", 24), ("units", "Units", 10), ("balance", "Starting Balance", 18), ("anchor", "Starting Date", 18),
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
        self.account_column_drag = ColumnDrag(self.accounts_tree,
            prepare=lambda: self._prepare_column_move("account"),
            changed=lambda before: self._columns_moved("accounts", before),
            resized=lambda before: self._columns_resized("accounts", before))
        self.account_rows = {}
        self.account_tooltip = TreeTooltip(self.accounts_tree, lambda row: "Starting balance immediately before " + self._account_record(row)["opening_at"] if self._account_record(row) else None)
        ttk.Label(card, text="Click a row · Double-click Starting Balance or Starting Date · Changes save automatically", style="CardMuted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _starting_balance_focus(self, event):
        # Mouse entry bindings place the caret after FocusIn; wait until they
        # finish. Later clicks in the already-focused field can place it freely.
        widget = event.widget
        def move_to_end():
            if widget.winfo_exists() and self.focus_get() is widget:
                widget.icursor("end")
                widget.xview_moveto(1)
        widget.after_idle(move_to_end)

    def _accounts_yview(self, *args):
        if self.account_cell_editor.close():
            self.accounts_tree.yview(*args)

    @staticmethod
    def _account_key(account):
        """A widget key for one named account/resource pair, never stored data."""
        return json.dumps([account["name"], account["units"]], ensure_ascii=False)

    def _account_record(self, key):
        if not key:
            return None
        try:
            name, units = json.loads(key)
        except (ValueError, TypeError):
            return None
        return self.repo.get_account(name, units)

    def _restore_account_draft_row(self):
        name = self.account_editing
        if not name or not self.accounts_tree.exists(name):
            return
        account = self._account_record(name)
        if account:
            opening = account["opening_amount"]
            self.accounts_tree.set(name, "balance", "Unknown" if opening is None else format_display(opening, account["units"]))
            self.accounts_tree.set(name, "anchor", account["opening_at"][:10])

    def _sync_account_draft(self, *args):
        """Preview only starting values; calculated balances remain saved values."""
        if self._loading:
            return
        self._record_draft("account", args)
        self._update_history_buttons()
        name = self.account_editing
        if not name or not self.accounts_tree.exists(name):
            return
        raw = self.account_vars["opening"].get().strip()
        try:
            opening = format_display(parse_quantity(raw, allow_negative=True), self.account_vars["units"].get()) if raw else "Unknown"
        except ValueError:
            opening = raw  # Keep mistakes visible without writing a record.
        self.accounts_tree.set(name, "balance", opening)
        self.accounts_tree.set(name, "anchor", self.account_vars["date"].get()[:10])

    def refresh_accounts(self):
        if not hasattr(self, "accounts_tree"):
            return
        if not self.account_cell_editor.close(commit=not self._committing):
            return
        self.account_tooltip.hide()
        selected = self._account_selection
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        self.account_rows = {}
        accounts = self.repo.list_accounts(referenced_only=True)
        flows = self.repo.list_flows()
        now = self._now()
        def quantity(value, units):
            return "Unknown" if value is None else format_display(value, units)
        for index, account in enumerate(accounts):
            confirmed = ledger_balance_at(account, flows, now, ("Complete",))
            pending = ledger_balance_at(account, flows, now, ("Complete", "Pending"))
            key = self._account_key(account)
            self.account_rows[key] = account
            self.accounts_tree.insert("", "end", iid=key, values=(account["name"], account["units"], quantity(account["opening_amount"], account["units"]), account["opening_at"][:10], quantity(confirmed, account["units"]), quantity(pending, account["units"])), tags=("odd" if index % 2 else "even",))
        if self.account_editing and not self.accounts_tree.exists(self.account_editing):
            self.new_account(focus=False, confirm=False)
        else:
            self._set_account_selection(selected)
            self._sync_account_draft()
        self.account_summary.set(f"{len({a['name'] for a in accounts})} accounts · {len(accounts)} balances")

    def _select_account(self, event=None):
        if self._loading or self._committing:
            return
        selection = self.accounts_tree.selection()
        name = selection[0] if selection else None
        if name == self._account_selection:
            return
        before = self._unadded_draft("account")
        if not self._finish_existing("account"):
            self._set_account_selection(self._account_selection)
            return
        self.account_cell_editor.close(commit=False)
        self._restore_account_draft_row()
        self._close_popups()
        account = self._account_record(name)
        if account:
            self._load_account(account)
        else:
            self.new_account(focus=False, confirm=False)
        self._record_discarded_draft("account", before)

    def _load_account(self, account):
        self._loading = True
        try:
            name = account["name"]
            key = self._account_key(account)
            self._set_account_selection(key)
            self.account_editing = key
            self.account_vars["name"].set(name)
            self.account_vars["units"].set(account["units"])
            self.account_name_entry.configure(state="disabled")
            self.account_widgets["units"].entry.configure(state="disabled")
            self.account_widgets["units"].arrow.configure(state="disabled")
            self.account_vars["opening"].set("" if account["opening_amount"] is None else format_quantity(account["opening_amount"]).replace(",", ""))
            self.account_vars["date"].set(account["opening_at"])
            self.account_title.configure(text="Edit Account")
            self.account_save_button.configure(state="disabled")
            self.account_feedback.set("Edits save automatically · Blank = unknown")
            self._clean_account = self._account_snapshot()
            self._active_edit = None
            self._draft_redo = None
        finally:
            self._loading = False
        self._remember_draft("account")
        self._begin_focused_field()

    def new_account(self, focus=True, confirm=True):
        before = self._draft_state("account") if hasattr(self, "view_history") and not self.account_editing else None
        if confirm and not self._finish_existing("account"):
            return False
        self._loading = True
        try:
            self.account_cell_editor.close(commit=False)
            self._restore_account_draft_row()
            self._close_popups()
            self.account_editing = None
            self._active_edit = None
            self._draft_redo = None
            self.account_name_entry.configure(state="normal")
            self.account_widgets["units"].entry.configure(state="normal")
            self.account_widgets["units"].arrow.configure(state="normal")
            for key, value in {"name": "", "units": "USD", "opening": "", "date": "2026-10-01"}.items():
                self.account_vars[key].set(value)
            self.account_title.configure(text="New Account")
            self.account_save_button.configure(state="normal")
            self.account_feedback.set("Blank = unknown")
            self._clean_account = self._account_snapshot()
            self._set_account_selection(None)
        finally:
            self._loading = False
        if confirm and before is not None:
            self.view_history.record("draft:account", "Clear new account", before=before)
        self._remember_draft("account")
        if focus:
            self.account_name_entry.focus_set()
            self._begin_field("account", "name")
        return True

    def save_account(self):
        if self.account_editing:
            return self._finish_existing("account")
        self._close_popups()
        try:
            raw = self.account_vars["opening"].get().strip()
            amount = quantity_text(parse_quantity(raw, allow_negative=True)) if raw else None
            at = normalize_date(self._account_when())
            name = self.account_vars["name"].get().strip()
            units = self.account_vars["units"].get().strip()
            existing = self.repo.get_account(name, units)
            if existing:
                if self.accounts_tree.exists(self._account_key(existing)):
                    raise ValueError("That account/unit balance already exists. Select it to edit.")
                raise ValueError("That account/unit balance already exists. Use it in a flow to show it here.")
            account = self.history.perform("Add account", lambda: self.repo.save_account(name, opening_amount=amount, opening_at=at, units=units))
            self.new_account(focus=False, confirm=False)
            self.refresh_all()
            self.status_message.set(f"Saved: {account['name']} · {account['units']} · Appears when used in a flow")
            self.account_name_entry.focus_set()
            self._begin_field("account", "name")
            self._update_history_buttons()
            return True
        except (ValueError, sqlite3.Error) as exc:
            self.account_feedback.set(str(exc))
            self.status_message.set(str(exc))
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
        ttk.Label(controls, text="ACCOUNTS / UNITS", style="Field.TLabel").grid(row=1, column=0, sticky="w")
        account_list = ttk.Frame(controls, style="Card.TFrame")
        account_list.grid(row=2, column=0, sticky="ew", pady=(7, 7))
        self.chart_accounts = tk.Listbox(account_list, selectmode="multiple", exportselection=False, height=7, width=1, bg="#f8faf7", fg=INK, selectbackground="#d6eade", selectforeground=INK, relief="flat", highlightthickness=1, highlightbackground=LINE, font=(FONT, 10), activestyle="none")
        self.chart_accounts.pack(side="left", fill="both", expand=True)
        account_scroll = ttk.Scrollbar(account_list, command=self.chart_accounts.yview)
        account_scroll.pack(side="right", fill="y")
        self.chart_accounts.configure(yscrollcommand=account_scroll.set)
        account_buttons = ttk.Frame(controls, style="Card.TFrame")
        account_buttons.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ttk.Button(account_buttons, text="All", width=5, style="Small.TButton", command=lambda: self._select_graph_accounts(True)).pack(side="left", padx=5)
        ttk.Button(account_buttons, text="None", width=5, style="Small.TButton", command=lambda: self._select_graph_accounts(False)).pack(side="left")
        self.chart_basis = tk.StringVar(self, "Confirmed")
        self.chart_basis_widget = self._field(controls, "Balance basis", self.chart_basis, 4, kind="choice", values=("Confirmed", "Including Pending", "Including Planned"))
        self.chart_start = tk.StringVar(self, "")
        self.chart_end = tk.StringVar(self, "")
        self._chart_auto_dates = True
        self._chart_setting_dates = False
        self._chart_restoring = False
        self._chart_redraw_pending = None
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
        ttk.Checkbutton(controls, text="Combine accounts within each unit", variable=self.chart_combined, style="Card.TCheckbutton").grid(row=7, column=0, sticky="w", pady=4)
        self.chart_fit_dates_button = ttk.Button(controls, text="Fit Dates", command=self.fit_chart_dates)
        self.chart_fit_dates_button.grid(row=8, column=0, sticky="ew", pady=(14, 0))
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
        self.chart_axes = {"": self.axes}
        self.chart_account_pairs = []
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_card)
        canvas_widget = self.canvas.get_tk_widget()
        canvas_widget.grid(row=2, column=0, sticky="nsew")
        canvas_widget.bind("<Map>", self._fit_chart_canvas, add="+")
        canvas_widget.bind("<Unmap>", self._hide_chart_tooltip, add="+")
        toolbar_frame = ttk.Frame(chart_card, style="Card.TFrame")
        toolbar_frame.grid(row=3, column=0, sticky="ew")
        self.chart_toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame, pack_toolbar=False)
        self.chart_toolbar.pack(fill="x")
        self.chart_note = tk.StringVar(self, "Select accounts to see their balances. Scroll to zoom; drag with the toolbar to pan.")
        ttk.Label(chart_card, textvariable=self.chart_note, style="CardMuted.TLabel", wraplength=750).grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.chart_inspect = tk.StringVar(self, "")
        ttk.Label(chart_card, textvariable=self.chart_inspect, style="CardMuted.TLabel", wraplength=750).grid(row=5, column=0, sticky="w", pady=(5, 0))
        self.canvas.mpl_connect("button_press_event", self.inspect_chart)
        self.canvas.mpl_connect("pick_event", self.toggle_chart_line)
        self.canvas.mpl_connect("motion_notify_event", self.hover_chart)
        self.canvas.mpl_connect("scroll_event", self._scroll_chart)
        self.canvas.mpl_connect("figure_leave_event", self._hide_chart_tooltip)
        self.canvas.mpl_connect("axes_leave_event", self._hide_chart_tooltip)
        self.chart_line_map = {}
        self.chart_series = {}
        self.chart_line_units = {}
        self.chart_tooltip = None
        self.chart_tooltips = {}
        self.chart_inspection_context = None
        self._chart_rendered_controls = None

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

    def _graph_controls(self):
        return (tuple(self.chart_account_pairs[i] for i in self.chart_accounts.curselection()),
                self.chart_basis.get(), self.chart_start.get(), self.chart_end.get(),
                self.chart_combined.get())

    def _put_graph_controls(self, values):
        pairs, basis, start, end, combined = values
        self.chart_accounts.selection_clear(0, "end")
        for index, pair in enumerate(self.chart_account_pairs):
            if pair in pairs:
                self.chart_accounts.selection_set(index)
        self.chart_basis.set(basis)
        self.chart_start.set(start)
        self.chart_end.set(end)
        self.chart_combined.set(combined)

    def _graph_state(self):
        visible = tuple(((line.get_label(), self.chart_line_units[line]), line.get_visible())
                        for line in self.chart_series)
        limits = tuple((units, tuple(axes.get_xlim()), tuple(axes.get_ylim()))
                       for units, axes in self.chart_axes.items())
        return self._graph_controls(), self._chart_rendered_controls, visible, limits, self._chart_auto_dates

    def _put_graph_state(self, state):
        controls, rendered, visible, limits, auto_dates = state
        if self._chart_redraw_pending is not None:
            self.after_cancel(self._chart_redraw_pending)
            self._chart_redraw_pending = None
        self._chart_restoring = True
        try:
            self._chart_auto_dates = auto_dates
            self._put_graph_controls(rendered or controls)
            self.draw_chart()
            visibility = dict(visible)
            for legend, line in self.chart_line_map.items():
                line.set_visible(visibility.get((line.get_label(), self.chart_line_units[line]), True))
                legend.set_alpha(1 if line.get_visible() else .25)
            for units, xlim, ylim in limits:
                axes = self.chart_axes.get(units)
                if axes is not None:
                    axes.set_xlim(xlim)
                    axes.set_ylim(ylim)
            self._put_graph_controls(controls)
            self._chart_rendered_controls = rendered
            self.canvas.draw_idle()
        finally:
            self._chart_restoring = False

    def _fit_chart_dates(self):
        """Use the selected accounts' anchors and eligible flow instants."""
        selected = {self.chart_account_pairs[i] for i in self.chart_accounts.curselection()}
        pairs = {(name.casefold(), units) for name, units in selected}
        dates = [normalize_date(account["opening_at"])
                 for name, units in selected
                 if (account := self.repo.get_account(name, units)) is not None]
        statuses = {"Confirmed": ("Complete",),
                    "Including Pending": ("Complete", "Pending"),
                    "Including Planned": ("Complete", "Pending", "Planned")}[self.chart_basis.get()]
        for flow in self.repo.list_flows():
            if flow["status"] in statuses and (
                    (flow["from_account"].casefold(), flow["units"]) in pairs or
                    (flow["to_account"].casefold(), flow["units"]) in pairs):
                dates.append(normalize_date(flow["date"]))
        def display(when):
            return when[:-9] if when.endswith(" 00:00:00") else when
        self._chart_setting_dates = True
        try:
            self.chart_start.set(display(min(dates)) if dates else "")
            self.chart_end.set(display(max(dates)) if dates else "")
        finally:
            self._chart_setting_dates = False

    def fit_chart_dates(self):
        """Return to a live range after manually choosing From or Through."""
        if not hasattr(self, "view_history"):
            return
        before = self.view_history.before["graph"]
        self._chart_auto_dates = True
        self._fit_chart_dates()
        self.draw_chart(remember=False)
        self.view_history.record("graph", "Fit graph dates", before=before)

    def _graph_setting_changed(self, key):
        if self._chart_setting_dates or self._chart_restoring or self.view_history.depth:
            return
        if key in ("start", "end"):
            self._chart_auto_dates = False
            if self._chart_redraw_pending is not None:
                self.after_cancel(self._chart_redraw_pending)
            self._chart_redraw_pending = self.after(140, lambda k=key: self._apply_graph_settings(k))
        else:
            self._apply_graph_settings(key)

    def _apply_graph_settings(self, key):
        if self._chart_redraw_pending is not None:
            self.after_cancel(self._chart_redraw_pending)
            self._chart_redraw_pending = None
        before = self.view_history.before["graph"]
        if self._chart_auto_dates:
            self._fit_chart_dates()
        self.draw_chart(remember=False)
        self.view_history.record("graph", "Graph settings", merge=key, before=before)

    def _graph_accounts_changed(self, event=None):
        if hasattr(self, "view_history"):
            self._apply_graph_settings("accounts")

    def _select_graph_accounts(self, all_accounts):
        if all_accounts:
            self.chart_accounts.selection_set(0, "end")
        else:
            self.chart_accounts.selection_clear(0, "end")
        self._graph_accounts_changed()

    def update_chart(self):
        """Compatibility for callers that ask to redraw; controls already redraw."""
        self.draw_chart()

    def _watch_chart_limits(self):
        for axes in self.chart_axes.values():
            for signal in ("xlim_changed", "ylim_changed"):
                axes.callbacks.connect(signal, self._chart_limits_changed)

    def _chart_limits_changed(self, axes):
        view = self.view_history
        if view.depth or self._loading or self._committing:
            return
        if self._chart_limit_pending is None:
            self._chart_limit_pending = self.after_idle(self._record_chart_limits)

    def _record_chart_limits(self):
        self._chart_limit_pending = None
        self.view_history.record("graph", "Pan or zoom graph", merge="limits")

    def refresh_chart_accounts(self):
        if not hasattr(self, "chart_accounts"):
            return
        selected = {self.chart_account_pairs[i] for i in self.chart_accounts.curselection()}
        initial = self.chart_accounts.size() == 0
        accounts = self.repo.list_accounts(referenced_only=True)
        self.chart_accounts.delete(0, "end")
        self.chart_account_pairs = [(account["name"], account["units"]) for account in accounts]
        for index, pair in enumerate(self.chart_account_pairs):
            self.chart_accounts.insert("end", pair[0] + " · " + pair[1])
            if pair in selected or (initial and index == 0):
                self.chart_accounts.selection_set(index)
        if hasattr(self, "view_history"):
            self.view_history.remember("graph")

    def draw_chart(self, *, remember=True):
        if self._chart_redraw_pending is not None:
            self.after_cancel(self._chart_redraw_pending)
            self._chart_redraw_pending = None
        if hasattr(self, "view_history"):
            with self.view_history.suspended():
                self._draw_chart()
            self._watch_chart_limits()
            if remember:
                self.view_history.remember("graph")
        else:
            self._draw_chart()
        self._chart_rendered_controls = self._graph_controls()
        if remember and hasattr(self, "view_history"):
            self.view_history.remember("graph")

    def _draw_chart(self):
        from matplotlib import dates as mdates, ticker
        self._hide_chart_tooltip()
        self.chart_tooltip = None
        self.chart_tooltips = {}
        self.chart_series = {}
        self.chart_line_units = {}
        self.chart_line_map = {}
        self.chart_inspection_context = None
        self.chart_inspect.set("")
        self.chart_message.set("")
        self.chart_message_label.grid_remove()
        self.figure.clear()
        self.chart_axes = {}
        self.chart_toolbar.update()
        try:
            selected = [self.chart_account_pairs[i] for i in self.chart_accounts.curselection()]
            normalized = []
            for label, variable in (("From", self.chart_start), ("Through", self.chart_end)):
                try:
                    raw = variable.get() or (datetime.now().date().isoformat() if not selected else "")
                    normalized.append(normalize_graph_date(raw))
                except ValueError as exc:
                    raise ValueError(f"{label}: invalid date {variable.get()!r}. Use YYYY-MM-DD; time is optional.") from exc
            start, end = map(normalize_date, normalized)
            if len(normalized[1]) == 10 and not self._chart_auto_dates:
                end = end[:11] + "23:59:59"
            if end < start:
                raise ValueError("Through must be on or after From.")
            self._chart_setting_dates = True
            try:
                if selected or self.chart_start.get():
                    self.chart_start.set(normalized[0])
                if selected or self.chart_end.get():
                    self.chart_end.set(normalized[1])
            finally:
                self._chart_setting_dates = False
            accounts = [self.repo.get_account(name, units) for name, units in selected]
            accounts = [account for account in accounts if account is not None]
            statuses = {"Confirmed": ("Complete",),
                        "Including Pending": ("Complete", "Pending"),
                        "Including Planned": ("Complete", "Pending", "Planned")}[self.chart_basis.get()]
            flows = self.repo.list_flows()
            groups = {}
            for account in accounts:
                groups.setdefault(account["units"], []).append(account)
            units_order = sorted(groups) or [""]
            unknown = [a["name"] + " · " + a["units"] for a in accounts if a["opening_amount"] is None]
            if unknown:
                self.chart_message.set("Missing starting balance: " + ", ".join(unknown) + ".")
                self.chart_message_label.grid()
            first_axes = None
            for plot_index, units in enumerate(units_order):
                axes = self.figure.add_subplot(len(units_order), 1, plot_index + 1, sharex=first_axes)
                if first_axes is None:
                    first_axes = axes
                    self.axes = axes
                self.chart_axes[units] = axes
                axes.set_facecolor("white")
                axes.spines[["top", "right", "left"]].set_visible(False)
                axes.spines["bottom"].set_color(LINE)
                axes.grid(axis="y", color="#e8eee8", linewidth=.8)
                axes.tick_params(axis="both", labelsize=9, colors=MUTED, length=0, pad=8)
                def format_y_tick(value, pos, u=units, plotted_axes=axes):
                    places = decimal_places(u)
                    span = abs(plotted_axes.get_ylim()[1] - plotted_axes.get_ylim()[0])
                    step = span / 8 or span
                    needed = (max(places, math.ceil(-math.log10(step)) + 1)
                              if step else places)
                    if needed > 15:
                        return f"{value:.15g}"
                    if value and abs(value) < 10.0 ** -places:
                        significant = max(4, needed + math.floor(math.log10(abs(value))) + 1)
                        return f"{value:.{min(15, significant)}g}"
                    return f"{value:,.{needed}f}"
                axes.yaxis.set_major_formatter(ticker.FuncFormatter(format_y_tick))
                axes.set_ylabel(units, color=INK, fontsize=10)
                locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
                axes.xaxis.set_major_locator(locator)
                axes.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
                axes.tick_params(axis="x", labelbottom=plot_index == len(units_order) - 1)
                unit_accounts = groups.get(units, [])
                valid = [a for a in unit_accounts if a["opening_amount"] is not None]
                unit_unknown = [a for a in unit_accounts if a["opening_amount"] is None]
                if not accounts:
                    axes.text(.5, .5, "Choose accounts and units to see their balances", ha="center", va="center", transform=axes.transAxes, color=MUTED, fontsize=14)
                elif self.chart_combined.get() and unit_unknown:
                    valid = []
                    axes.text(.5, .5, "Combined balance is unknown\nSet the missing starting balances in Accounts.", ha="center", va="center", transform=axes.transAxes, color=MUTED, fontsize=13)
                elif unit_unknown and not valid:
                    axes.text(.5, .5, "Starting balance required\nSet a starting balance in Accounts.", ha="center", va="center", transform=axes.transAxes, color=MUTED, fontsize=13)
                series = graph_timeline(valid, flows, start, end, statuses, combined=self.chart_combined.get())
                all_lines = []
                all_values = []
                for index, ((name, series_units), rows) in enumerate(series.items()):
                    xs = [datetime.fromisoformat(date) for date, value in rows]
                    ys = [float(value) for date, value in rows]
                    if any(not math.isfinite(plotted) or (exact != 0 and plotted == 0)
                           for (_, exact), plotted in zip(rows, ys)):
                        raise ValueError(f"Outside graph display range: {name} · {series_units}.")
                    line, = axes.step(xs, ys, where="post", label=name,
                                      color=PALETTE[index % len(PALETTE)], linewidth=2.2,
                                      marker="o", markevery=[0], markersize=5)
                    self.chart_series[line] = rows
                    self.chart_line_units[line] = series_units
                    all_lines.append(line)
                    all_values.extend(ys)
                if all_lines:
                    labels = [line.get_label() for line in all_lines]
                    if self.chart_combined.get():
                        labels = ["Combined: " + " + ".join(account["name"] for account in valid)]
                    legend = axes.legend(all_lines, labels, loc="lower left",
                                         bbox_to_anchor=(0, 1.005), frameon=False,
                                         fontsize=9, ncol=1 if len(all_lines) < 6 else 2)
                    for legline, origline in zip(legend.get_lines(), all_lines):
                        legline.set_picker(6)
                        self.chart_line_map[legline] = origline
                start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
                padding = max((end_dt - start_dt) * .02,
                              timedelta(hours=1 if self._chart_auto_dates else 12)
                              if start_dt == end_dt else timedelta(seconds=1))
                # Keep the opening marker inside the hit-testable plot area.
                # The limited leading margin will not expose a preceding
                # midnight tick; the end stays on its selected calendar day.
                first_day = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                last_day = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                leading = min(padding, timedelta(minutes=1)) if start_dt == end_dt else padding
                x_low = max(start_dt - leading, first_day - timedelta(hours=12))
                x_high = min(end_dt + padding, last_day)
                axes.set_xlim(x_low, x_high)
                if all_values:
                    low, high = min(all_values), max(all_values)
                    padding_y = ((high - low) * .1 if high > low else
                                 abs(low) * .1 if low else 10.0 ** -decimal_places(units))
                    low_limit, high_limit = low - padding_y, high + padding_y
                    if not all(math.isfinite(value) for value in (low_limit, high_limit)):
                        raise ValueError(f"Outside graph display range: {units}.")
                    if low_limit <= 0 <= high_limit:
                        axes.axhline(0, color="#b4c7bb", linewidth=.8)
                    axes.set_ylim(low_limit, high_limit)
                now = datetime.now()
                if start_dt <= now <= end_dt:
                    axes.axvline(now, color="#b4c7bb", linewidth=1, linestyle=":")
                    if plot_index == 0:
                        axes.text(mdates.date2num(now), 1.01, "NOW", transform=axes.get_xaxis_transform(), color=MUTED, fontsize=8, ha="center")
                tooltip = axes.annotate("", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
                                        fontsize=9, color=INK, zorder=20,
                                        bbox=dict(boxstyle="round,pad=.55", fc="white", ec=LINE, alpha=.98))
                tooltip.set_visible(False)
                tooltip.set_in_layout(False)
                self.chart_tooltips[axes] = tooltip
                axes.callbacks.connect("xlim_changed", self._hide_chart_tooltip)
                axes.callbacks.connect("ylim_changed", self._hide_chart_tooltip)
            self.chart_tooltip = self.chart_tooltips[self.axes]
            self.figure.subplots_adjust(left=.14, bottom=.11, right=.97, top=.88, hspace=.48)
            self.chart_toolbar.push_current()
            self.chart_title.configure(text="Account Balances · " + self.chart_basis.get())
            self.chart_note.set("Hover for exact balances; scroll to zoom, toolbar Home to fit; click legend lines to hide/show.")
            self.chart_inspection_context = (accounts, flows, statuses, ledger_balance_at)
        except (ValueError, sqlite3.Error) as exc:
            self.chart_message.set(str(exc))
            self.chart_message_label.grid()
            if not self.chart_axes:
                self.axes = self.figure.add_subplot(111)
                self.chart_axes = {"": self.axes}
            self.axes.text(.5, .5, "Unable to draw graph\nCheck the controls above.", ha="center", va="center", transform=self.axes.transAxes, color=MUTED, fontsize=13)
            self.chart_note.set("")
        self.canvas.draw_idle()

    def _scroll_chart(self, event):
        """Wheel zooms in or out around the cursor, including the date axis."""
        if (event.inaxes not in self.chart_axes.values() or event.xdata is None
                or event.ydata is None or self.chart_toolbar.mode):
            return
        direction = getattr(event, "button", None)
        if direction not in ("up", "down"):
            return
        if hasattr(self, "view_history"):
            self.view_history.begin()
        factor = .8 if direction == "up" else 1.25
        axes = event.inaxes
        for getter, setter, cursor in ((axes.get_xlim, axes.set_xlim, event.xdata),
                                       (axes.get_ylim, axes.set_ylim, event.ydata)):
            low, high = getter()
            setter(cursor + (low - cursor) * factor,
                   cursor + (high - cursor) * factor)
        self.canvas.draw_idle()

    def _hide_chart_tooltip(self, event=None):
        changed = False
        for tooltip in getattr(self, "chart_tooltips", {}).values():
            if tooltip.get_visible():
                tooltip.set_visible(False)
                changed = True
        if changed and getattr(event, "type", None) != tk.EventType.Unmap:
            self.canvas.draw_idle()

    def hover_chart(self, event):
        """Hit-test rendered steps; inspect exact stored quantities, never interpolation."""
        if (event.inaxes not in self.chart_tooltips or event.xdata is None
                or self.chart_toolbar.mode):
            self._hide_chart_tooltip()
            return
        import numpy as np
        axes = event.inaxes
        hits = []
        cursor = np.array([event.x, event.y])
        for line, rows in self.chart_series.items():
            if line.axes is not axes or not line.get_visible() or not rows:
                continue
            units = self.chart_line_units[line]
            points = axes.transData.transform(line.get_xydata())
            if len(points) == 1:
                distance = float(np.linalg.norm(cursor - points[0]))
                text = f"{line.get_label()}: {format_quantity(rows[0][1])} {units}"
            else:
                starts = np.empty((2 * (len(points) - 1), 2))
                ends = np.empty_like(starts)
                starts[::2] = points[:-1]
                ends[::2, 0] = points[1:, 0]
                ends[::2, 1] = points[:-1, 1]
                starts[1::2] = ends[::2]
                ends[1::2] = points[1:]
                vectors = ends - starts
                lengths = (vectors * vectors).sum(axis=1)
                fractions = np.divide(((cursor - starts) * vectors).sum(axis=1), lengths,
                                      out=np.zeros_like(lengths), where=lengths != 0)
                fractions = np.clip(fractions, 0, 1)
                nearest = starts + vectors * fractions[:, None]
                distances = np.linalg.norm(nearest - cursor, axis=1)
                index = int(np.argmin(distances))
                distance = float(distances[index])
                row_index = index // 2
                before, after = rows[row_index][1], rows[row_index + 1][1]
                value = f"{format_quantity(before)} {units}"
                if index % 2 and before != after:
                    if 0 < fractions[index] < 1:
                        value += f" → {format_quantity(after)} {units}"
                    elif fractions[index] == 1:
                        value = f"{format_quantity(after)} {units}"
                text = f"{line.get_label()}: {value}"
                if index % 2 and before != after and 0 < fractions[index] < 1:
                    text += "\n" + rows[row_index + 1][0]
            if distance <= 7:
                hits.append((distance, text))
        if not hits:
            self._hide_chart_tooltip()
            return
        self._hide_chart_tooltip()
        tooltip = self.chart_tooltips[axes]
        self.chart_tooltip = tooltip
        tooltip.set_text("\n".join(text for distance, text in sorted(hits)))
        tooltip.xy = (event.xdata, event.ydata)
        right_half = event.x > (axes.bbox.x0 + axes.bbox.x1) / 2
        top_half = event.y > (axes.bbox.y0 + axes.bbox.y1) / 2
        tooltip.set_position((-12 if right_half else 12, -12 if top_half else 12))
        tooltip.set_ha("right" if right_half else "left")
        tooltip.set_va("top" if top_half else "bottom")
        tooltip.set_visible(True)
        self.canvas.draw_idle()

    def inspect_chart(self, event):
        if event.inaxes not in self.chart_axes.values() or event.xdata is None or not self.chart_inspection_context:
            return
        from matplotlib.dates import num2date
        at = num2date(event.xdata).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        accounts, flows, statuses, calculate = self.chart_inspection_context
        parts = []
        units = next(unit for unit, axes in self.chart_axes.items() if axes is event.inaxes)
        for account in accounts:
            if account["units"] != units:
                continue
            value = calculate(account, flows, at, statuses)
            parts.append(f"{account['name']}: " + ("unknown" if value is None else format_quantity(value) + " " + units))
        self.chart_inspect.set(at + "  |  " + " · ".join(parts))

    def toggle_chart_line(self, event):
        line = self.chart_line_map.get(event.artist)
        if line:
            self._hide_chart_tooltip()
            line.set_visible(not line.get_visible())
            event.artist.set_alpha(1 if line.get_visible() else .25)
            self.canvas.draw_idle()
            self.view_history.record("graph", "Show or hide graph line")

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
        ttk.Label(card, text="DATABASE", style="Field.TLabel").pack(anchor="w")
        self.db_path_label = ttk.Label(card, text=str(self.repo.path), style="CardMuted.TLabel", wraplength=950)
        self.db_path_label.pack(anchor="w", pady=(6, 16))
        ttk.Button(card, text="View Schema", command=self.show_schema).pack(anchor="w", pady=(0, 16))
        ttk.Label(card, text=f"Version {__version__} · Local desktop · SQLite", style="CardMuted.TLabel").pack(anchor="w")

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
        account = self._account_record(self.account_editing)
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
        columns = ("row", "state", "name", "from", "to", "amount", "units", "message")
        tree = ttk.Treeview(dialog, columns=columns, show="headings")
        for key, width in [("row", 45), ("state", 85), ("name", 145), ("from", 110), ("to", 110), ("amount", 90), ("units", 75), ("message", 250)]:
            tree.heading(key, text=key.upper())
            tree.column(key, width=width, stretch=key == "message")
        tree.grid(row=2, column=0, sticky="nsew", padx=22)
        for row in preview:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            try:
                amount = format_display(data.get("amount", ""), data.get("units", ""))
            except (ValueError, TypeError):
                amount = data.get("amount", "")
            tree.insert("", "end", values=(row["row"], row["state"], data.get("name", ""), data.get("from_account", ""), data.get("to_account", ""), amount, data.get("units", ""), row["message"]))
        include_similar = tk.BooleanVar(dialog, False)
        ttk.Checkbutton(dialog, text="Include similar rows as separate flows (possible legitimate repeats)", variable=include_similar).grid(row=3, column=0, sticky="w", padx=22, pady=14)
        bottom = ttk.Frame(dialog)
        bottom.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 20))
        blocked = bool(counts["Error"] or counts["Conflict"])
        ttk.Label(bottom, text="Fix errors and ID conflicts in the source file first." if blocked else "Duplicate IDs are skipped. All accepted rows save together.", style="Muted.TLabel").pack(side="left")
        def commit():
            try:
                result = self.history.perform("Import flows", lambda: commit_import(self.repo, preview, include_similar.get()))
                dialog.destroy()
                self.refresh_all()
                self._update_history_buttons()
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
                self.history.clear()
                self._update_history_buttons()
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
        """Group each visible flow by category, status, and exact unit."""
        self._prepare_dialog()
        groups = {}
        for flow in self.report_rows:
            key = (flow["category"], flow["status"], flow["units"])
            groups.setdefault(key, []).append(parse_quantity(flow["amount"]))
        dialog = tk.Toplevel(self)
        dialog.title("Category totals · current ledger filter")
        dialog.geometry("850x560")
        dialog.configure(bg=BG)
        dialog.transient(self)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)
        ttk.Label(dialog, text="Category totals", font=(FONT, 20, "bold")).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 8))
        ttk.Label(dialog, text="Flow volume · Each flow counted once, including transfers · Current ledger filter", style="Muted.TLabel", wraplength=800).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 18))
        tree = ttk.Treeview(dialog, columns=("category", "status", "amount", "units", "count"), show="headings")
        for key, title, width in [("category", "Category", 230), ("status", "Status", 140), ("amount", "Flow Volume", 190), ("units", "Units", 100), ("count", "Flows", 90)]:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="e" if key in {"amount", "count"} else "w")
        tree.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 22))
        for (category, status, units), amounts in sorted(groups.items()):
            tree.insert("", "end", values=(category, status, format_display(decimal_sum(amounts), units), units, len(amounts)))
