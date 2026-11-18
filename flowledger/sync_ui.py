"""Desktop sharing controls and nonblocking peer status updates."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .peer_ui import PeerController


class SyncController:
    """Keep device networking on its worker and refresh records on Tk."""

    def __init__(self, app):
        self.app = app
        self.closed = False
        self.after_id = None
        self.dialog = None
        self.peer = PeerController(app)
        app.bind("<Destroy>", self._destroyed, add="+")
        self._schedule(100)

    def _destroyed(self, event):
        if event.widget is self.app:
            self.close()

    def _schedule(self, delay=100):
        if not self.closed:
            self.after_id = self.app.after(delay, self._tick)

    def close(self):
        self.closed = True
        self.peer.close()
        if self.after_id is not None:
            try:
                self.app.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _tick(self):
        self.after_id = None
        if self.closed:
            return
        self.peer.poll()
        self._display_status()
        self._schedule(100 if self.peer.pending else 500)

    def _display_status(self):
        peer = self.peer
        self.app.sync_button.configure(text=peer.short_status() or "Sync")
        local = peer.local_entry()
        location = ("Shared | " + local["name"] if local else
                    "Local peer copy" if peer.dataset else "Local")
        if self.app.file_context.get() != location:
            self.app.file_context.set(location)
        if self.dialog is not None and self.dialog.winfo_exists():
            feedback = peer.error or peer.notice.get()
            if peer.pending not in (None, "resume", "status", "nearby_poll"):
                feedback = "Working..."
            self.panel_notice.set(feedback)

    def show(self):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        self.app._prepare_dialog()
        window = self.dialog = tk.Toplevel(self.app)
        window.title("Sync")
        window.transient(self.app)
        window.geometry("700x540")
        window.minsize(560, 460)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        self.book = ttk.Notebook(window)
        self.book.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="nsew")
        self.peer.attach(self.book)
        buttons = ttk.Frame(window)
        buttons.grid(row=1, column=0, sticky="ew", padx=18, pady=10)
        self.panel_notice = tk.StringVar(window)
        ttk.Label(buttons, textvariable=self.panel_notice, wraplength=510).pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda event: window.destroy())
        self._display_status()
