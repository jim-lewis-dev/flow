"""Desktop device pairing, opted-in files, and peer conflict choices."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
from queue import Empty, Queue
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

from . import peer_store
from .peer_network import PeerNode, default_config_dir


def copy_path(dataset):
    # Remote names are for display only. Never interpret them as paths.
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return root / "flowledger" / "shared" / (sha256(dataset.encode()).hexdigest() + ".sqlite3")


def open_copy(path, dataset):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("The local copy is no longer at this location.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.15)) as conn:
        if peer_store.dataset(SimpleNamespace(conn=conn)) != dataset:
            raise ValueError("A different file is now at the saved location.")
    subprocess.Popen([sys.executable, "-m", "flowledger", "--db", str(path)],
                     cwd=Path(__file__).resolve().parents[1], start_new_session=True)


def describe(value, group):
    if value is None:
        return "No record" if group == ["__row__"] else "(none)"
    if group == ["tags"] and isinstance(value, dict):
        return ", ".join(sorted(value, key=value.get)) or "(no tags)"
    if isinstance(value, dict):
        if set(value) == {"amount", "units"}:
            return str(value["amount"]) + " " + str(value["units"])
        return "\n".join(k.replace("_", " ").capitalize() + ": " +
                         describe(v, [k]) for k, v in value.items() if k != "flow_id")
    return str(value) if value != "" else "(empty)"


class _Worker:
    """One queue owns network startup, I/O, and teardown; no Tk objects cross it."""
    def __init__(self):
        self.jobs, self.results = Queue(), Queue()
        self.thread = None
        self.closed = threading.Event()

    def submit(self, action, args):
        self.jobs.put((action, args))
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name="flow-peer-ui", daemon=True)
            self.thread.start()

    def close(self):
        self.closed.set()
        self.jobs.put(None)

    def run(self):
        node = None
        try:
            while not self.closed.is_set():
                item = self.jobs.get()
                if item is None:
                    return
                action, args = item
                value, error, status = None, "", {}
                try:
                    if node is None and action != "resolve":
                        node = PeerNode()
                    if action == "resolve":
                        from .core import Repository
                        path, dataset, conflict, choice, origin = args
                        if not Path(path).is_file():
                            raise ValueError("The selected file is no longer at this location.")
                        with closing(Repository(path)) as repo:
                            if peer_store.dataset(repo) != dataset:
                                raise ValueError("The selected file has changed.")
                            repo.peer_origin = origin
                            value = peer_store.resolve(repo, conflict, choice)
                    elif action == "resume":
                        if node.peers() or node.local_files():
                            node.start()
                    elif action == "join":
                        node.start()
                        dataset, peer, path = args
                        Path(path).parent.mkdir(parents=True, exist_ok=True)
                        node.join(dataset, peer, path)
                        value = path
                    elif action == "share":
                        value = node.share(*args)
                        node.start()
                    elif action not in ("status", "forget", "unshare"):
                        node.start()
                        value = getattr(node, action)(*args)
                    elif action != "status":
                        getattr(node, action)(*args)
                except Exception as exc:
                    error = str(exc) or "Could not reach the other device."
                try:
                    if node is not None:
                        status = node.status()
                except Exception as exc:
                    error = error or str(exc)
                self.results.put((action, value, status, error))
        finally:
            if node is not None:
                node.stop()


class PeerController:
    def __init__(self, app):
        self.app, self.worker = app, _Worker()
        self.state = {}
        self.remote = []
        self._scan_on_show = False
        self.pending = None
        self.error = ""
        self.last_poll = self.last_status = self.code_until = 0
        self.version = None
        self.dataset = ""
        self.conflicts = []
        self.book = None
        self.notice = tk.StringVar(app)
        self.shared_name = tk.StringVar(app)
        self.file_label = tk.StringVar(app)
        self.address_label = tk.StringVar(app)
        self.devices_label = tk.StringVar(app)
        self.issued = tk.StringVar(app)
        self.code, self.address = tk.StringVar(app), tk.StringVar(app)
        self.peer_detail = tk.StringVar(app)
        self.selected_info = tk.StringVar(app)
        self._file_rows = self._conflict_key = None
        if not app.demo and (default_config_dir() / "devices.sqlite3").is_file():
            self._submit("resume")

    @property
    def visible(self):
        return self.book is not None and self.book.winfo_exists()

    def close(self):
        self.worker.close()

    def _submit(self, action, *args):
        if self.app.demo or self.pending or self.worker.closed.is_set():
            return
        self.pending = action
        if action == "available":
            self._scan_on_show = False
        self.error = ""
        self.worker.submit(action, args)
        if self.visible:
            self.render()

    def poll(self, force=False):
        """A commit on another SQLite connection wakes the UI, even for a service."""
        while True:
            try:
                action, value, state, error = self.worker.results.get_nowait()
            except Empty:
                break
            self.pending = None
            self.state = state
            if state.get("name"):
                self.app.repo.peer_origin = state["name"]
            if action != "status" or error:
                self.error = error
            if not error:
                if action == "available":
                    self.remote = value
                    self.notice.set("No shared files found on reachable devices." if not value else "")
                elif action == "pair_code":
                    self.issued.set(value)
                    self.code_until = time.monotonic() + 300
                    self.notice.set("Enter this code on the other device within five minutes.")
                elif action == "pair":
                    self.code.set("")
                    self.address.set("")
                    self.notice.set("Paired. Look in Shared files to join a file.")
                    self._submit("available")
                elif action == "share":
                    self.notice.set("This file is available to paired devices.")
                elif action == "resolve":
                    self.notice.set("Choice saved." if value else "This conflict has already changed.")
                elif action == "unshare":
                    self.notice.set("Sharing stopped. Your local file and other devices' copies are kept.")
                elif action == "forget":
                    self.remote = []
                    self.notice.set("Device removed.")
                elif action == "join":
                    self.notice.set("Joined. Opening the local copy in a new Flow window.")
                    joined = next((f for f in state.get("files", ()) if f["path"] == value), None)
                    if joined:
                        self._open(value, joined["dataset"])
                elif action == "sync_once":
                    self.notice.set("Checked paired devices for changes.")
            self.last_status = time.monotonic()
            force = True
        now = time.monotonic()
        if force or now - self.last_poll >= 1:
            self.last_poll = now
            if self.code_until and now >= self.code_until:
                self.issued.set("")
                self.code_until = 0
            try:
                repo = self.app.repo
                version = repo.conn.execute("PRAGMA data_version").fetchone()[0]
                if force or version != self.version:
                    dataset = peer_store.dataset(repo)
                    if dataset:
                        if repo.sync is None:
                            from .sync import SyncState
                            SyncState(repo)
                        first_share = bool(self.version is not None and self.dataset != dataset)
                        if first_share:
                            self.app.history.clear()
                        self.conflicts = peer_store.conflicts(repo)
                        if first_share or version != self.version:
                            self.app._refresh_synced_records()
                            self.app.view_history.remember("experimental")
                    else:
                        self.conflicts = []
                    self.dataset, self.version = dataset, version
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                self.error = str(exc)
            if self.visible:
                self.render()
                if self._scan_on_show and not self.pending and self.state.get("peers"):
                    self._scan_on_show = False
                    self._submit("available")
            if (self.state and not self.pending and now - self.last_status >= 5
                    and (self.visible or self.dataset)):
                self.last_status = now
                self._submit("status")

    def _open(self, path, dataset):
        try:
            if Path(path).resolve() == Path(self.app.repo.path).resolve():
                self.notice.set("This file is already open in this window.")
            else:
                open_copy(path, dataset)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error = str(exc)

    def local_entry(self):
        return next((f for f in self.state.get("files", ())
                     if f["dataset"] == self.dataset and f["path"] == self.app.repo.path), None)

    def short_status(self):
        if self.conflicts:
            return "Conflict"
        if self.dataset:
            return "Shared" if self.local_entry() else "Peer file"
        return None

    def attach(self, book):
        self.book = book
        self._scan_on_show = True
        self._file_rows = self._conflict_key = None
        self.files, self.devices, self.choices = (ttk.Frame(book, padding=12) for _ in range(3))
        for frame, name in ((self.files, "Shared files"), (self.devices, "Devices"),
                            (self.choices, "Conflicts")):
            book.add(frame, text=name)
            frame.columnconfigure(0, weight=1)
        f = self.files
        ttk.Label(f, textvariable=self.file_label, wraplength=590).grid(row=0, sticky="w")
        bar = ttk.Frame(f)
        bar.grid(row=1, sticky="ew", pady=(8, 15))
        ttk.Label(bar, text="Shared name").pack(side="left")
        self.shared_name.set(Path(self.app.repo.path).stem)
        self.name_entry = ttk.Entry(bar, textvariable=self.shared_name, width=24)
        self.name_entry.pack(side="left", padx=7)
        self.share_button = ttk.Button(bar, text="Share this file", command=self._share)
        self.share_button.pack(side="left")
        ttk.Label(f, text="Files on this device and files offered by paired devices").grid(row=2, sticky="w")
        table = ttk.Frame(f)
        table.grid(row=3, sticky="nsew", pady=6)
        f.rowconfigure(3, weight=1)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("file", "place"), show="headings", height=4,
                                 selectmode="browse")
        for key, label in (("file", "File"), ("place", "Location")):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=190, minwidth=60)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda event: self._buttons())
        self.tree.bind("<Double-1>", lambda event: self._open_selected())
        self.tree.bind("<Return>", lambda event: self._open_selected())
        ttk.Label(f, textvariable=self.selected_info, wraplength=590).grid(row=4, sticky="w")
        bar = ttk.Frame(f)
        bar.grid(row=5, sticky="ew", pady=(6, 0))
        self.join_button = ttk.Button(bar, text="Join on this device", command=self._open_selected)
        self.join_button.pack(side="left")
        self.find_button = ttk.Button(bar, text="Find shared files", command=lambda: self._submit("available"))
        self.find_button.pack(side="left", padx=6)
        self.sync_button = ttk.Button(bar, text="Sync now", command=lambda: self._submit("sync_once"))
        self.sync_button.pack(side="right")
        ttk.Label(f, textvariable=self.peer_detail, wraplength=590).grid(row=6, sticky="w", pady=(10, 0))
        self._build_devices()
        self._conflict_frame()
        self.poll(force=True)
        if self.dataset and self.conflicts:
            book.select(self.choices)

    def _build_devices(self):
        d = self.devices
        ttk.Label(d, text="Pair devices once to see the files each one shares.").grid(row=0, sticky="w")
        ttk.Label(d, textvariable=self.devices_label, wraplength=590).grid(row=1, sticky="w", pady=(4, 12))
        box = ttk.LabelFrame(d, text="Show a code on this device", padding=8)
        box.grid(row=2, sticky="ew")
        self.make_code = ttk.Button(box, text="Create pairing code", command=lambda: self._submit("pair_code"))
        self.make_code.grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.issued, state="readonly", width=27).grid(row=0, column=1, padx=8)
        ttk.Label(box, textvariable=self.address_label).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        box = ttk.LabelFrame(d, text="Enter a code from the other device", padding=8)
        box.grid(row=3, sticky="ew", pady=(8, 0))
        for row, (title, var) in enumerate((("Pairing code", self.code), ("Address (optional)", self.address))):
            ttk.Label(box, text=title).grid(row=row, column=0, sticky="w", padx=(0, 8))
            entry = ttk.Entry(box, textvariable=var, width=30)
            entry.grid(row=row, column=1, sticky="w", pady=3)
            entry.bind("<Return>", lambda event: self._pair())
        self.pair_button = ttk.Button(box, text="Pair device", command=self._pair)
        self.pair_button.grid(row=0, column=2, padx=8)
        ttk.Label(box, text="Use the displayed address if discovery is unavailable.").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.device_bar = ttk.Frame(d)
        self.device_bar.grid(row=4, sticky="ew", pady=8)
        self.device_pick = ttk.Combobox(self.device_bar, state="readonly", width=29)
        self.device_pick.pack(side="left")
        self.forget_button = ttk.Button(self.device_bar, text="Remove paired device", command=self._forget)
        self.forget_button.pack(side="left", padx=6)

    def _pair(self):
        if not self.code.get().strip():
            self.notice.set("Enter the code displayed on the other device.")
            return
        self._submit("pair", self.code.get().strip(), self.address.get().strip() or None)

    def _forget(self):
        i = self.device_pick.current()
        if i >= 0:
            self._submit("forget", self.state["peers"][i]["peer_id"])

    def _share(self):
        local = self.local_entry()
        if local:
            self._submit("unshare", local["dataset"])
        elif self.can_share():
            self._submit("share", self.app.repo.path, self.shared_name.get().strip())

    def can_share(self):
        sync = self.app.repo.sync
        return (not self.app.demo and self.app.repo.path != ":memory:" and not (sync is not None and sync.enabled)
                and not self.app.sync_controller.running)

    def _rows(self):
        rows = {}
        for f in self.state.get("files", ()):
            rows[f["dataset"]] = (f["name"], "This device", f["path"], None)
        for f in self.remote:
            rows.setdefault(f["dataset"], (f["name"], f["peer_name"], "", f["peer_id"]))
        return rows

    def _open_selected(self):
        selection = self.tree.selection()
        if not selection or self.pending:
            return
        dataset = selection[0]
        row = self._file_rows.get(dataset)
        if not row:
            return
        if row[2]:
            self._open(row[2], dataset)
            self.render()
        else:
            self._submit("join", dataset, row[3], str(copy_path(dataset)))

    def _buttons(self):
        row = (self._file_rows or {}).get(next(iter(self.tree.selection()), ""))
        self.selected_info.set((row[2] or ("From " + row[1])) if row else "")
        self.join_button.configure(text="Open local copy" if row and row[2] else "Join on this device",
                                   state="normal" if row and not self.pending else "disabled")

    def _conflict_frame(self):
        f = self.choices
        self.choice_title = tk.StringVar(self.app)
        ttk.Label(f, textvariable=self.choice_title, wraplength=600).grid(row=0, sticky="w")
        box = ttk.Frame(f)
        box.grid(row=1, sticky="nsew", pady=8)
        f.rowconfigure(1, weight=1)
        self.preview = tk.Text(box, wrap="word", height=6, state="disabled", font=("DejaVu Sans", 10))
        sc = ttk.Scrollbar(box, command=self.preview.yview)
        self.preview.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y")
        self.preview.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(f)
        bar.grid(row=2, sticky="ew")
        self.choice_pick = ttk.Combobox(bar, state="readonly", width=35)
        self.choice_pick.pack(side="left", fill="x", expand=True)
        self.choose_button = ttk.Button(bar, text="Keep selected version", command=self._resolve)
        self.choose_button.pack(side="left", padx=(8, 0))

    def _show_conflict(self):
        c = self.conflicts[0] if self.conflicts else None
        key = (c["id"] if c else "", len(self.conflicts))
        if self._conflict_key == key:
            return
        self._conflict_key = key
        self.book.tab(self.choices, text="Conflicts" + (" (" + str(len(self.conflicts)) + ")" if c else ""))
        if not c:
            title, body, labels = "No conflicts in this file.", "", []
        else:
            title = "1 of %d | %s" % (len(self.conflicts), c["title"])
            if c.get("table") == "flows":
                record = self.app.repo.get_flow(c["key"][0])
                if record:
                    title = title.replace(c["key"][0], record["name"] or c["key"][0])
            group = c.get("group", [])
            parts = ["Previous value\n" + describe(c["base"], group)]
            labels = []
            for i, choice in enumerate(c["choices"], 1):
                value = ("Delete record" if choice["value"] is None and group == ["__row__"]
                         else describe(choice["value"], group))
                origin_text = ", ".join(choice.get("origins", []))
                label = ("Version %d" % i) + ((" | " + origin_text) if origin_text else "")
                parts.append(label + "\n" + value)
                labels.append(label + ": " + value.replace("\n", " ")[:70])
            body = "\n\n".join(parts)
        self.choice_title.set(title)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", body)
        self.preview.configure(state="disabled")
        self.choice_pick.configure(values=labels)
        self.choice_pick.set("")
        self.choose_button.configure(state="normal" if c else "disabled")

    def _resolve(self):
        i = self.choice_pick.current()
        if i < 0 or not self.conflicts:
            self.notice.set("Select the version to keep.")
            return
        c = self.conflicts[0]
        self._submit("resolve", self.app.repo.path, self.dataset, c["id"], c["choices"][i]["id"],
                     getattr(self.app.repo, "peer_origin", ""))

    def render(self):
        busy = self.app.demo or self.pending is not None
        state = "disabled" if busy else "normal"
        local = self.local_entry()
        can_share = self.can_share()
        self.file_label.set(("Shared as " + local["name"] if local else
                             "Peer file | Not offered from this location" if self.dataset else
                             "This file uses a coordinator" if self.app.repo.sync and self.app.repo.sync.enabled
                             else "Demo | Device sharing is off" if self.app.demo else "This file is local only") + "\n" + self.app.repo.path)
        if local:
            self.shared_name.set(local["name"])
        self.name_entry.configure(state="normal" if can_share and not local and not busy else "disabled")
        self.share_button.configure(text="Stop sharing" if local else "Share this file",
                                    state="normal" if can_share and not busy else "disabled")
        rows = self._rows()
        if self._file_rows != rows:
            selected = next(iter(self.tree.selection()), "")
            self.tree.delete(*self.tree.get_children())
            for dataset, row in rows.items():
                self.tree.insert("", "end", iid=dataset, values=row[:2])
            if selected in rows:
                self.tree.selection_set(selected)
            self._file_rows = rows
        self._buttons()
        self.find_button.configure(state=state)
        self.sync_button.configure(state=state if self.state.get("peers") and local else "disabled")
        self.make_code.configure(state=state)
        self.pair_button.configure(state=state)
        peers = self.state.get("peers", [])
        self.devices_label.set("Paired: " + (", ".join(p["name"] for p in peers) or "none"))
        if peers:
            self.device_bar.grid()
            self.device_pick.configure(values=[p["name"] + " | " + p["address"] for p in peers])
            if self.device_pick.current() < 0:
                self.device_pick.current(0)
            self.forget_button.configure(state=state)
        else:
            self.device_bar.grid_remove()
        address = self.state.get("address")
        self.address_label.set(("This device: " + self.state["name"] + " | " + address) if address
                               else "The device address appears with its code.")
        last = self.state.get("last_sync")
        self.peer_detail.set(self.state.get("error") or self.state.get("discovery_error") or
                             ("Last check: " + datetime.fromtimestamp(last).strftime("%H:%M:%S") if last
                              else "Paired devices exchange edits when they are reachable."))
        self._show_conflict()
        self.choose_button.configure(state="normal" if self.conflicts and not busy else "disabled")
