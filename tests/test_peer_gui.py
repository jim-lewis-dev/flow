"""Exercise the peer controls against real local files and device listeners."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import threading
import time
import unittest
from gui_test_support import IsolatedGuiTestCase
from unittest.mock import patch
from tempfile import TemporaryDirectory

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "Tk needs a display")
class PeerDesktopTests(IsolatedGuiTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enter = self._context
        self.enter(patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root / "settings"),
                              XDG_DATA_HOME=str(self.root / "documents")))
        self.repo = Repository(self.root / "Water.sqlite3")
        self.addCleanup(self.repo.close)
        self.record = self.repo.save_flow(dict(name="Irrigation", date="2026-10-01", from_account="Tank",
            to_account="Field", units="L", amount="13", notes="Original meter"))
        self.errors = []
        self.app = None

    def _context(self, manager):
        result = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return result

    def launch(self, **opts):
        from flowledger.gui import FlowApp
        app = self.app = FlowApp(self.repo, **opts)
        app.report_callback_exception = lambda typ, exc, tb: self.errors.append(str(exc))
        app.attributes("-fullscreen", False)
        app.geometry("1280x900")
        app.update()
        self.controller = app.sync_controller
        self.peer = self.controller.peer
        self.addCleanup(self.shut)
        return app

    def shut(self):
        self.controller.close()
        if self.peer.worker.thread:
            self.peer.worker.thread.join(5)
        self.app.update_idletasks()
        self.app.experimental.close()
        self.app.history.close()
        self.app.destroy()
        self.assertEqual([], self.errors)

    def until(self, check):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.update()
            if check():
                return
            time.sleep(.015)
        self.fail(self.peer.error or self.peer.notice.get() or "Timed out waiting for the peer worker")

    def test_standalone_opens_only_device_sharing_without_starting_network(self):
        from flowledger.peer_network import default_config_dir
        self.launch()
        self.controller.show()
        self.assertIsNone(self.peer.worker.thread)
        self.assertFalse(default_config_dir().exists())
        self.assertFalse(self.peer.share_button.instate(["disabled"]))
        self.assertEqual([self.controller.book.tab(tab, "text") for tab in self.controller.book.tabs()],
                         ["Shared files", "Devices", "Conflicts"])

    def test_demo_does_not_start_a_listener_even_with_an_existing_configuration(self):
        from flowledger.peer_network import default_config_dir
        settings = default_config_dir()
        settings.mkdir(parents=True)
        (settings / "devices.sqlite3").touch()
        self.launch(demo=True)
        self.controller.show()
        self.peer._submit("nearby_open")
        self.peer._share()
        self.assertIsNone(self.peer.worker.thread)
        self.assertTrue(self.peer.share_button.instate(["disabled"]))
        self.assertTrue(self.peer.near_button.instate(["disabled"]))

    @unittest.skipUnless(shutil.which("openssl"), "Desktop device identities require OpenSSL")
    def test_pair_and_join_opens_a_different_file_and_reopens_it_offline(self):
        from flowledger import peer_store
        from flowledger.peer_network import PeerNode
        from flowledger.peer_ui import copy_path
        remote = PeerNode(self.root / "remote", name="Other desktop", host="127.0.0.1",
                          port=0, discovery=False, interval=3600).start()
        self.addCleanup(remote.stop)
        path = self.root / "yesterday.sqlite3"
        with closing(Repository(path)) as repo:
            flow = repo.save_flow(dict(self.record, name="Remote meter"))
        dataset = remote.share(path, "Water")
        workers = []
        def factory():
            workers.append(threading.get_ident())
            return PeerNode(name="This desktop", host="127.0.0.1", port=0, discovery=False, interval=3600)
        self.enter(patch("flowledger.peer_ui.PeerNode", side_effect=factory))
        opened = self.enter(patch("flowledger.peer_ui.open_copy"))
        self.launch()
        self.app.form_vars["name"].set("unfinished local draft")
        self.controller.show()
        remote.nearby_open()
        self.peer.near_button.invoke()
        self.until(lambda: self.peer.pending is None and self.peer.nearby.get("open"))
        self.peer.address.set(remote.status()["address"])
        self.peer._lookup_nearby()
        remote_id = remote.status()["peer_id"]
        self.until(lambda: self.peer.pending is None and self.peer.near_tree.exists(remote_id))
        self.peer.near_tree.selection_set(remote_id)
        self.peer._request_nearby()
        self.until(lambda: self.peer.pending is None and self.peer.nearby.get("request"))
        remote.nearby_approve(self.peer.nearby["request"]["id"])
        self.until(lambda: self.peer.pending is None and self.peer.tree.exists(dataset))
        self.assertNotEqual(threading.get_ident(), workers[0])
        self.assertEqual("Water", self.peer.tree.item(dataset, "values")[0])
        self.peer.tree.selection_set(dataset)
        self.peer._open_selected()
        self.until(lambda: self.peer.pending is None and opened.called)
        destination = copy_path(dataset)
        opened.assert_called_once_with(str(destination), dataset)
        self.assertNotEqual(Path(self.repo.path), destination)
        self.assertEqual("", peer_store.dataset(self.repo))
        self.assertEqual("unfinished local draft", self.app.form_vars["name"].get())
        with closing(Repository(destination)) as joined:
            self.assertEqual(flow, joined.get_flow(flow["flow_id"]))
        remote.stop()
        self.controller.dialog.destroy()
        self.controller.show()
        self.until(lambda: self.peer.pending is None)
        self.peer.tree.selection_set(dataset)
        self.app.update()
        self.assertEqual("Open local copy", self.peer.join_button.cget("text"))
        self.peer._open_selected()
        self.assertEqual(2, opened.call_count)

    def test_nearby_pairing_uses_one_confirmation_and_preserves_address(self):
        from unittest.mock import Mock
        node = Mock()
        row = {"peer_id": "1" * 64, "name": "Custard", "address": "192.168.0.70:47621"}
        idle = {"open": True, "devices": [row], "request": None, "paired": []}
        request = {"id": "r-1", "name": "Custard", "number": "031245", "peer_id": row["peer_id"],
                   "approved": True, "direction": "outgoing"}
        node.status.return_value = {"peers": [], "files": [], "address": "192.168.0.88:47621",
                                    "addresses": ["192.168.0.88:47621"], "name": "XPS"}
        node.nearby_open.return_value = idle
        node.nearby_poll.return_value = idle
        node.nearby_request.return_value = dict(idle, request=request)
        node.nearby_approve.return_value = dict(idle, request=dict(request, approved=True))
        node.nearby_close.return_value = dict(idle, open=False)
        self.enter(patch("flowledger.peer_ui.PeerNode", return_value=node))
        self.launch()
        self.controller.show()
        self.peer.book.select(self.peer.devices)
        self.app.update()
        self.assertFalse(self.peer.address_box.winfo_ismapped())
        self.assertIsNone(self.peer.worker.thread)
        self.peer.address.set("custard.local")
        self.peer.near_button.invoke()
        self.until(lambda: self.peer.pending is None and self.peer.near_tree.exists(row["peer_id"]))
        self.peer.near_tree.selection_set(row["peer_id"])
        self.app.update()
        box = self.peer.near_tree.bbox(row["peer_id"])
        self.peer.near_tree.event_generate("<ButtonRelease-1>", x=10, y=box[1] + 5)
        self.until(lambda: self.peer.near_title.get() == "Waiting for Custard to confirm.")
        self.assertFalse(hasattr(self.peer, "digits"))
        node.nearby_request.assert_called_once_with(row["peer_id"])
        node.nearby_approve.assert_not_called()
        self.assertEqual("custard.local", self.peer.address.get())
        self.assertFalse(self.peer.match_button.winfo_ismapped())
        self.assertIn("Waiting", self.peer.near_title.get())
        # The receiver alone gets the explicit Pair button.
        self.peer.nearby = dict(idle, request=dict(request, direction="incoming", approved=False))
        self.peer.render()
        self.app.update()
        self.assertTrue(self.peer.match_button.winfo_ismapped())
        self.assertEqual("Pair", self.peer.match_button.cget("text"))
        self.assertEqual("Pair with Custard?", self.peer.near_title.get())
        self.peer.match_button.invoke()
        self.until(lambda: self.peer.pending is None and self.peer.match_button.instate(["disabled"]))
        node.nearby_approve.assert_called_once_with("r-1")
        # Closing the dialog retires that window rather than opening a new one.
        self.controller.dialog.destroy()
        self.until(lambda: node.nearby_close.called and self.peer.pending is None)
        node.nearby_open.assert_called_once()

    def test_discovery_error_appears_on_devices_and_clears_on_recovery(self):
        self.launch()
        self.controller.show()
        self.peer.book.select(self.peer.devices)
        self.peer.nearby = {"open": True, "devices": [], "request": None}
        self.peer.state["discovery_error"] = "Discovery send: [Errno 1] Operation not permitted"
        self.peer.render()
        self.assertIn("Operation not permitted", self.peer.near_title.get())
        self.peer.state["discovery_error"] = ""
        self.peer.render()
        self.assertNotIn("Operation not permitted", self.peer.near_title.get())

    def test_find_by_address_uses_nearby_pairing_only(self):
        from unittest.mock import Mock
        node = Mock()
        idle = {"open": True, "devices": [], "request": None, "paired": []}
        node.status.return_value = {"peers": [], "files": [], "address": "192.168.0.70:47621"}
        node.nearby_open.return_value = idle
        node.nearby_lookup.return_value = idle
        node.nearby_poll.return_value = idle
        node.nearby_close.return_value = dict(idle, open=False)
        self.enter(patch("flowledger.peer_ui.PeerNode", return_value=node))
        self.launch()
        self.controller.show()
        self.peer.book.select(self.peer.devices)
        self.peer.address_open.set(True)
        self.peer._show_address()
        self.app.update()
        self.assertTrue(self.peer.address_box.winfo_ismapped())
        self.assertTrue(self.peer.lookup_button.instate(["disabled"]))
        self.peer.near_button.invoke()
        self.until(lambda: self.peer.pending is None and self.peer.nearby.get("open"))
        self.peer.address.set("192.168.0.107")
        self.peer.lookup_button.invoke()
        self.until(lambda: self.peer.pending is None)
        node.nearby_lookup.assert_called_once_with("192.168.0.107")
        self.assertFalse(hasattr(self.peer, "code"))

    def test_external_save_refreshes_ledger_and_graph_without_replacing_a_draft(self):
        from flowledger import peer_store
        self.launch()
        with closing(Repository(self.repo.path)) as other:
            peer_store.share(other, "Water")
            self.peer.poll(force=True)
            self.app.form_vars["name"].set("unfinished local draft")
            self.app.show_view("charts")
            with patch.object(self.app, "draw_chart", wraps=self.app.draw_chart) as chart:
                other.save_flow(dict(self.record, amount="17"))
                self.peer.poll(force=True)
                self.assertTrue(chart.called)
            self.assertEqual("unfinished local draft", self.app.form_vars["name"].get())
            self.assertEqual("17", self.app.ledger_rows[self.record["flow_id"]]["amount"])

    def test_delete_and_cancel_guard_against_unseen_commits(self):
        from flowledger import peer_store
        self.launch()
        peer_store.share(self.repo, "Water")
        self.peer.poll(force=True)
        self.app._load_form(self.record)
        fid = self.record["flow_id"]
        with closing(Repository(self.repo.path)) as second:
            second.save_flow(dict(self.record, notes="A later reading", status="Pending"))
            self.app.void_selected()
            self.assertIn("status changed", self.app.status_message.get())
            self.assertEqual("Pending", self.repo.get_flow(fid)["status"])
            self.app.delete_selected()
            self.assertIn("selected flow changed", self.app.status_message.get())
            self.assertEqual("A later reading", self.repo.get_flow(fid)["notes"])
            self.peer.poll(force=True)
            self.app.delete_selected()
            self.assertIsNone(self.repo.get_flow(fid))

    def test_conflict_ui_preserves_base_and_applies_the_chosen_version(self):
        from flowledger import peer_store
        self.launch()
        peer_store.share(self.repo, "Water")
        with closing(Repository(self.root / "other-flow.sqlite3")) as other:
            peer_store.join(other, peer_store.seed(self.repo))
            self.repo.peer_origin, other.peer_origin = "Workshop desktop", "Field phone"
            self.repo.save_flow(dict(self.record, notes="local meter"))
            other.save_flow(dict(self.record, notes="other meter"))
            peer_store.receive(self.repo, peer_store.missing(other, list(peer_store.inventory(self.repo))))
            self.peer.poll(force=True)
            self.controller.show()
            self.assertEqual(self.controller.book.select(), str(self.peer.choices))
            text = self.peer.preview.get("1.0", "end")
            for word in ("Previous value", "Original meter", "local meter", "other meter",
                         "Workshop desktop", "Field phone"):
                self.assertIn(word, text)
            self.assertEqual("Original meter", self.repo.get_flow(self.record["flow_id"])["notes"])
            conflict = self.peer.conflicts[0]
            i = next(i for i, ch in enumerate(conflict["choices"]) if ch["value"] == "other meter")
            self.peer.choice_pick.current(i)
            self.peer.choose_button.invoke()
            self.until(lambda: self.peer.pending is None and not self.peer.conflicts)
            self.assertEqual("other meter", self.repo.get_flow(self.record["flow_id"])["notes"])
            self.assertEqual([], self.peer.conflicts)
            self.assertTrue(self.peer.choose_button.instate(["disabled"]))


if __name__ == "__main__":
    unittest.main()
