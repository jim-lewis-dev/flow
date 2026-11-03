"""Optional per-file synchronization; Tk owns SQLite, a worker owns HTTPS."""
from __future__ import annotations

from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import ttk

from . import sync_transport


class SyncController:
    """Poll without blocking input or moving a SQLite connection between threads."""

    def __init__(self, app):
        self.app = app
        self.results = Queue()
        self.closed = False
        self.running = False
        self.after_id = None
        self.dialog = None
        self.detail = tk.StringVar(app, "Not connected")
        self.error = None
        self.connected = False
        self.catching_up = False
        self.next_poll = 0
        self._conflicts = []
        app.bind("<Destroy>", self._destroyed, add="+")
        self._schedule(100)

    @property
    def sync(self):
        return getattr(self.app.repo, "sync", None)

    def _destroyed(self, event):
        if event.widget is self.app:
            self.close()

    def _schedule(self, delay=100):
        if not self.closed:
            self.after_id = self.app.after(delay, self._tick)

    def close(self):
        self.closed = True
        if self.after_id is not None:
            try:
                self.app.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _start(self, kind, config, request=None):
        self.running = True
        self._display_status()
        results = self.results

        def run():
            try:
                result = (sync_transport.fetch_bootstrap(config) if kind == "connect"
                          else sync_transport.exchange(config, request))
                results.put((kind, config, result, None))
            except Exception as exc:
                results.put((kind, config, None, str(exc)))

        # Closing Flow leaves the durable outbox intact. The network timeout
        # cannot hold the application open or call into a destroyed Tk widget.
        threading.Thread(target=run, name="flow-sync", daemon=True).start()

    def _tick(self):
        self.after_id = None
        if self.closed:
            return
        try:
            while True:
                kind, config, response, error = self.results.get_nowait()
                self.running = False
                self.error = error
                if error:
                    self.connected = False
                    self.next_poll = 20
                    continue
                try:
                    if kind == "connect":
                        from .sync import SyncState
                        sync = self.sync or SyncState(self.app.repo)
                        first_join = not sync.enabled
                        sync.configure(seed=response, **config)
                        if first_join:
                            self.app.history.clear()
                        self.app._refresh_synced_records()
                        self.app.view_history.remember("experimental")
                        self.app.status_message.set("Connected · Undo history starts here" if first_join else "Connected")
                        if self.dialog is not None and self.dialog.winfo_exists():
                            self.connection_text.delete("1.0", "end")
                    else:
                        previous_cursor = self.sync.status()["cursor"]
                        changed = self.sync.accept_response(response)
                        if changed or previous_cursor != self.sync.status()["cursor"]:
                            self.app._refresh_synced_records()
                    self.connected = True
                    self.catching_up = bool(response.get("more", False))
                    self.next_poll = 0 if self.catching_up else 10
                except Exception as exc:
                    self.error = str(exc)
                    self.connected = False
                    self.next_poll = 20
        except Empty:
            pass
        sync = self.sync
        enabled = sync is not None and sync.status()["enabled"]
        if enabled and not self.running:
            if self.next_poll <= 0:
                try:
                    self._start("exchange", sync.configuration(), sync.prepare_request())
                except Exception as exc:
                    self.error = str(exc)
                    self.connected = False
                    self.next_poll = 20
            else:
                self.next_poll -= 1
        self._display_status()
        self._schedule()

    def _display_status(self):
        status = self.sync.status() if self.sync is not None else {"enabled": False}
        pending = status.get("pending", 0)
        self._conflicts = status.get("conflicts", [])
        if self.error:
            short, text = "Offline", self.error
        elif self._conflicts:
            short, text = "Conflict", "Choose which version to keep. Other saved edits remain on this device."
        elif self.catching_up:
            short, text = "Syncing", "Receiving changes…"
        elif self.running and (pending or not self.connected):
            short, text = "Syncing", "Synchronizing changes…"
        elif not status["enabled"]:
            short, text = "Sync", "Not connected · This file saves locally"
        elif pending:
            short, text = "Sync", f"{pending} pending change{'s' if pending != 1 else ''} · Saved locally"
        elif self.connected:
            short, text = "Up to date", "Up to date"
        else:
            short, text = "Offline", "Saved locally · Waiting for the coordinator"
        self.app.sync_button.configure(text=short)
        self.detail.set(text)
        if self.dialog is not None and self.dialog.winfo_exists():
            self.connect_button.configure(state="disabled" if self.running else "normal")
            self._display_conflict()

    def show(self):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        self.app._prepare_dialog()
        window = self.dialog = tk.Toplevel(self.app)
        window.title("Sync")
        window.transient(self.app)
        window.geometry("640x420")
        window.minsize(480, 350)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(3, weight=1)
        ttk.Label(window, text=str(self.app.repo.path), wraplength=590).grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 8))
        ttk.Label(window, textvariable=self.detail, wraplength=590).grid(
            row=1, column=0, sticky="w", padx=18, pady=(0, 12))
        ttk.Label(window, text="Paste the coordinator's connection text:").grid(
            row=2, column=0, sticky="w", padx=18)
        self.connection_text = tk.Text(window, height=4, wrap="word", font=("DejaVu Sans", 9))
        self.connection_text.grid(row=3, column=0, sticky="nsew", padx=18, pady=6)
        self.conflict_text = tk.Text(window, height=6, wrap="word", font=("DejaVu Sans", 9), state="disabled")
        self.conflict_text.grid(row=4, column=0, sticky="ew", padx=18, pady=6)
        self.conflict_actions = ttk.Frame(window)
        self.conflict_actions.grid(row=5, column=0, sticky="w", padx=18)
        ttk.Button(self.conflict_actions, text="Keep local", command=lambda: self._resolve("local")).pack(side="left", padx=(0, 8))
        ttk.Button(self.conflict_actions, text="Use server", command=lambda: self._resolve("remote")).pack(side="left")
        buttons = ttk.Frame(window)
        buttons.grid(row=6, column=0, sticky="e", padx=18, pady=16)
        self.connect_button = ttk.Button(buttons, text="Connect", command=self._connect)
        self.connect_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="left")
        window.bind("<Escape>", lambda event: window.destroy())
        self._display_status()

    def _connect(self):
        if self.running:
            return
        try:
            config = sync_transport.parse_connection(self.connection_text.get("1.0", "end-1c"))
        except ValueError as exc:
            self.error = str(exc)
            self._display_status()
            return
        self.error = None
        self._start("connect", config)

    def _display_conflict(self):
        if not self._conflicts:
            self.conflict_text.grid_remove()
            self.conflict_actions.grid_remove()
            return
        conflict = self._conflicts[0]
        text = self._conflict_summary(conflict)
        self.conflict_text.configure(state="normal")
        if self.conflict_text.get("1.0", "end-1c") != text:
            self.conflict_text.delete("1.0", "end")
            self.conflict_text.insert("1.0", text)
        self.conflict_text.configure(state="disabled")
        self.conflict_text.grid()
        self.conflict_actions.grid()
        for button in self.conflict_actions.winfo_children():
            button.configure(state="disabled" if self.running else "normal")

    def _conflict_summary(self, conflict):
        lines = [conflict.get("message", "This change overlaps a newer change."), ""]
        labels = {"from_key": "From", "to_key": "To", "category_key": "Category",
                  "opening_amount": "Starting balance", "opening_at": "Starting date",
                  "tag_key": "Tag", "account_key": "Account"}
        for change in conflict.get("changes", []):
            table = change.get("table", "Record")
            key = change.get("key", [])
            if table == "flows" and key:
                record = self.app.repo.get_flow(key[0])
                name = record["name"] if record else key[0]
                lines.append("Flow: " + name)
            elif table == "account_balances":
                lines.append("Account: " + " · ".join(map(str, key)))
            elif table == "account_positions":
                lines.append("Account position: " + " · ".join(map(str, key)))
            else:
                lines.append(table.replace("_", " ").title() + ": " + " · ".join(map(str, key)))
            local, remote = change.get("after"), change.get("remote")
            if local is None:
                lines.append("This device: deleted")
                lines.append("Coordinator: " + ("deleted" if remote is None else "record retained"))
            elif isinstance(local, dict):
                remote = remote or {}
                for field, value in local.items():
                    lines.append(f"{labels.get(field, field.replace('_', ' ').title())}: "
                                 f"this device {value!s} · coordinator {remote.get(field, '(missing)')!s}")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _resolve(self, choice):
        if self.running or not self._conflicts:
            return
        conflict = self._conflicts[0]
        try:
            operation_id = conflict.get("operation_id", conflict.get("id"))
            self.sync.resolve(operation_id, choice=choice)
            self.error = None
            self.app._refresh_synced_records()
            self.next_poll = 0
        except Exception as exc:
            self.error = str(exc)
        self._display_status()
