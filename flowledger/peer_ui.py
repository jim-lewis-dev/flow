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
        owns_window = False
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
                        if action == "nearby_open":
                            owns_window = True
                        elif action == "nearby_close":
                            owns_window = False
                    elif action != "status":
                        getattr(node, action)(*args)
                except TimeoutError:
                    error = ("Connection timed out. Check both devices are on the same Wi-Fi "
                             "and that the LAN allows Flow on TCP 47621.")
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
                try:
                    if owns_window:
                        node.nearby_close()
                finally:
                    node.stop()


class PeerController:
    def __init__(self, app):
        self.app, self.worker = app, _Worker()
        self.state = {}
        self.remote = []
        self._scan_on_show = False
        self.scan_after_pair = False
        self.pending = None
        self.deferred = None
        self.nearby = {}
        self.next_nearby_poll = 0
        self.end_nearby = False
        self.error = ""
        self.last_poll = self.last_status = 0
        self.version = None
        self.dataset = ""
        self.conflicts = []
        self.book = None
        self.notice = tk.StringVar(app)
        self.shared_name = tk.StringVar(app)
        self.file_label = tk.StringVar(app)
        self.address_label = tk.StringVar(app)
        self.devices_label = tk.StringVar(app)
        self.address = tk.StringVar(app)
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
        if self.pending in ("nearby_poll", "status") and action not in ("nearby_poll", "status"):
            # A background check must not swallow a real click.
            if not self.deferred:
                self.deferred = (action, args)
            return
        if self.app.demo or self.pending or self.worker.closed.is_set():
            return
        self.pending = action
        if action == "available":
            self._scan_on_show = False
        if action not in ("nearby_poll", "status"):
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
            if action not in ("status", "nearby_poll") or error:
                self.error = error
            if action.startswith("nearby_"):
                self.next_nearby_poll = time.monotonic() + 1.5
                if not error:
                    old = self.nearby
                    self.nearby = value
                    if value.get("paired"):
                        names = [p["name"] for p in state.get("peers", ()) if p["peer_id"] in value["paired"]]
                        self.notice.set("Paired with " + (", ".join(names) or "the other device") + ".")
                        self.end_nearby = bool(value.get("open"))
                        self._scan_on_show = self.scan_after_pair = True
                    elif value.get("message"):
                        self.notice.set(value["message"])
                    elif old.get("request") and not value.get("request") and action == "nearby_poll":
                        self.notice.set("Pairing request ended. Select the device to try again.")
                    elif old.get("open") and not value.get("open") and action != "nearby_close":
                        self.notice.set("Pairing ended. Choose Pair device to start again.")
                    elif action == "nearby_cancel":
                        self.notice.set("Pairing canceled.")
                    elif action not in ("nearby_close", "nearby_poll"):
                        self.notice.set("")
            if not error:
                if action == "available":
                    self.remote = value
                    if not self.scan_after_pair:
                        self.notice.set("No shared files found on reachable devices." if not value else "")
                    self.scan_after_pair = False
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
            if self.end_nearby and self.pending is None:
                self.end_nearby = False
                self.deferred = None
                self._submit("nearby_close")
            elif self.deferred and self.pending is None:
                action, args = self.deferred
                self.deferred = None
                self._submit(action, *args)
            elif self.visible and self.nearby.get("open") and self.pending is None and now >= self.next_nearby_poll:
                self._submit("nearby_poll")
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
        book.bind("<Destroy>", self._close_pairing_dialog, add="+")
        self._conflict_frame()
        self.poll(force=True)
        if self.dataset and self.conflicts:
            book.select(self.choices)

    def _build_devices(self):
        d = self.devices
        top = ttk.Frame(d)
        top.grid(row=0, sticky="ew", pady=(0, 6))
        self.near_button = ttk.Button(top, text="Pair device", command=self._toggle_nearby)
        self.near_button.pack(side="left")
        self.near_title = tk.StringVar(d)
        ttk.Label(top, textvariable=self.near_title, wraplength=430).pack(side="left", padx=8)
        self.near_list = ttk.Frame(d)
        self.near_list.grid(row=1, sticky="ew", pady=(0, 5))
        self.near_list.columnconfigure(0, weight=1)
        self.near_tree = ttk.Treeview(self.near_list, columns=("name",), show="tree", height=3,
                                      selectmode="browse")
        self.near_tree.column("#0", width=260)
        self.near_tree.column("name", width=0, stretch=False)
        self.near_tree.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(self.near_list, command=self.near_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.near_tree.configure(yscrollcommand=sb.set)
        self.near_connect = ttk.Button(self.near_list, text="Pair selected", command=self._request_nearby)
        self.near_connect.grid(row=0, column=2, padx=(6, 0))
        self.near_tree.bind("<<TreeviewSelect>>", lambda e: self._near_buttons())
        self.near_tree.bind("<Return>", lambda e: self._request_nearby())
        self.near_tree.bind("<ButtonRelease-1>", self._choose_nearby)
        self.comparison = ttk.Frame(d)
        self.comparison.grid(row=1, sticky="ew", pady=6)
        self.match_button = ttk.Button(self.comparison, text="Pair")
        self.match_button.pack(side="left", padx=(0, 5))
        self.deny_button = ttk.Button(self.comparison, text="Cancel")
        self.deny_button.pack(side="left")
        self._near_items = ()
        self.address_open = tk.BooleanVar(d, False)
        ttk.Checkbutton(d, text="Find by address", variable=self.address_open,
                        command=self._show_address).grid(row=2, sticky="w", pady=4)
        self.address_box = box = ttk.Frame(d, padding=(4, 3))
        box.grid(row=3, sticky="ew")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, textvariable=self.address_label, wraplength=495).grid(row=0, columnspan=3, sticky="w")
        ttk.Label(box, text="Other device address").grid(row=1, sticky="w", padx=(0, 5))
        ttk.Entry(box, textvariable=self.address, width=26).grid(row=1, column=1, sticky="ew", pady=3)
        self.lookup_button = ttk.Button(box, text="Find", command=self._lookup_nearby)
        self.lookup_button.grid(row=1, column=2, sticky="w", padx=6)
        self._show_address()
        ttk.Label(d, textvariable=self.devices_label, wraplength=550).grid(row=4, sticky="w", pady=(8, 0))
        self.device_bar = ttk.Frame(d)
        self.device_bar.grid(row=5, sticky="ew", pady=8)
        self.device_pick = ttk.Combobox(self.device_bar, state="readonly", width=29)
        self.device_pick.pack(side="left")
        self.forget_button = ttk.Button(self.device_bar, text="Remove paired device", command=self._forget)
        self.forget_button.pack(side="left", padx=6)

    def _show_address(self):
        if self.address_open.get():
            self.address_box.grid()
        else:
            self.address_box.grid_remove()

    def _close_pairing_dialog(self, event):
        if event.widget is self.book:
            if self.nearby.get("open") or (self.pending or "").startswith("nearby_"):
                self.end_nearby = True
            if self.deferred and self.deferred[0].startswith("nearby_"):
                self.deferred = None

    def _toggle_nearby(self):
        self._submit("nearby_close" if self.nearby.get("open") else "nearby_open")

    def _lookup_nearby(self):
        address = self.address.get().strip()
        if address:
            self._submit("nearby_lookup", address)
        else:
            self.notice.set("Enter the other device's Wi-Fi address.")

    def _request_nearby(self):
        choice = self.near_tree.selection()
        if choice and self.nearby.get("open") and not self.nearby.get("request"):
            self._submit("nearby_request", choice[0])

    def _choose_nearby(self, event):
        row = self.near_tree.identify_row(event.y)
        if row:
            self.near_tree.selection_set(row)
            self._request_nearby()

    def _near_buttons(self):
        ready = self.pending in (None, "status", "nearby_poll") and not self.app.demo and not self.deferred
        self.near_connect.configure(state="normal" if ready and self.near_tree.selection() else "disabled")

    def _near_render(self, state):
        open_ = self.nearby.get("open")
        request = self.nearby.get("request") if open_ else None
        self.near_button.configure(state=state, text="Stop pairing" if open_ else "Pair device")
        self.lookup_button.configure(state=state if open_ and not request else "disabled")
        show_list = open_ and not request and self.nearby.get("devices")
        for widget, show in ((self.comparison, request), (self.near_list, show_list)):
            if show and not widget.winfo_manager():
                widget.grid()
            elif not show and widget.winfo_manager():
                widget.grid_remove()
        if request:
            who = request["name"]
            self.near_title.set(("Waiting for " + who + " to confirm." if request["approved"]
                                 else "Pair with " + who + "?"))
            rid = request["id"]
            self.match_button.configure(command=lambda: self._submit("nearby_approve", rid),
                                        state="disabled" if request["approved"] else state)
            if request.get("direction") == "outgoing" or request["approved"]:
                self.match_button.pack_forget()
            elif not self.match_button.winfo_manager():
                self.match_button.pack(side="left", padx=(0, 5), before=self.deny_button)
            self.deny_button.configure(command=lambda: self._submit("nearby_cancel", rid), state=state)
        elif open_:
            devices = self.nearby.get("devices", ())
            self.near_title.set(self.state.get("discovery_error") or
                                ("Select a device or choose this computer on the other screen." if devices
                                 else "Choose Pair device on the other device. Already paired devices are listed below."))
            rows = tuple((v["peer_id"], v["name"], v["address"]) for v in devices)
            if self._near_items != rows:
                selected = next(iter(self.near_tree.selection()), None)
                self.near_tree.delete(*self.near_tree.get_children())
                names = [v[1] for v in rows]
                for key, name, address in rows:
                    label = name if names.count(name) == 1 else name + " | " + address
                    self.near_tree.insert("", "end", iid=key, text=label)
                if selected and self.near_tree.exists(selected):
                    self.near_tree.selection_set(selected)
                elif len(rows) == 1:
                    self.near_tree.selection_set(rows[0][0])
                self._near_items = rows
            self._near_buttons()
        else:
            self.near_title.set("Connect to Flow on another device on your Wi-Fi.")

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
        return not self.app.demo and self.app.repo.path != ":memory:"

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
        busy = self.app.demo or self.pending not in (None, "nearby_poll", "status") or self.deferred is not None
        state = "disabled" if busy else "normal"
        local = self.local_entry()
        can_share = self.can_share()
        self.file_label.set(("Shared as " + local["name"] if local else
                             "Peer file | Not offered from this location" if self.dataset else
                             "Demo | Device sharing is off" if self.app.demo else "This file is local only") + "\n" + self.app.repo.path)
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
        self._near_render(state)
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
        addresses = self.state.get("addresses") or ([self.state["address"]] if self.state.get("address") else [])
        self.address_label.set(("This device: " + ", ".join(addresses)) if addresses
                               else "Start nearby pairing to see this device's address.")
        last = self.state.get("last_sync")
        self.peer_detail.set(self.state.get("error") or self.state.get("discovery_error") or
                             ("Last check: " + datetime.fromtimestamp(last).strftime("%H:%M:%S") if last
                              else "Paired devices exchange edits when they are reachable."))
        self._show_conflict()
        self.choose_button.configure(state="normal" if self.conflicts and not busy else "disabled")
