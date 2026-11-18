"""Exercise the real peer listener, its private catalog and offline relay behavior."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from flowledger import peer_wire
from flowledger.core import Repository
from flowledger.peer_network import PeerNode
from discovery_test_support import isolate_discovery


@unittest.skipUnless(shutil.which("openssl"), "desktop test identities require OpenSSL")
class PeerNetworkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        isolate_discovery(self)

    def node(self, name, *, port=0, interval=3600):
        node = PeerNode(self.root / name, name=name, host="127.0.0.1",
                        port=port, interval=interval, discovery=False)
        self.addCleanup(node.stop)
        node.start()
        return node

    def ledger(self, filename):
        path = self.root / filename
        with closing(Repository(path)) as repo:
            record = repo.save_flow(dict(date="2026-11-04", from_account="Water tank",
                to_account="Orchard", amount="1.25000000001", units="L", name="Drip line"))
        return path, record

    def connect(self, client, server):
        client.nearby_open()
        server.nearby_open()
        identity = server.status()["peer_id"]
        client.nearby_lookup(server.status()["address"])
        self.complete_pairing(client, server, identity)
        return identity

    def complete_pairing(self, client, server, identity):
        request = client.nearby_request(identity)["request"]
        incoming = server.nearby_poll()["request"]
        self.assertEqual(request["number"], incoming["number"])
        self.assertEqual([], client.peers())
        server.nearby_approve(incoming["id"])
        client.nearby_poll()
        self.assertIn(identity, [p["peer_id"] for p in client.peers()])
        self.assertIn(client.status()["peer_id"], [p["peer_id"] for p in server.peers()])
        client.nearby_close()
        server.nearby_close()

    def test_catalog_only_contains_opted_in_files_and_never_overwrites_a_file(self):
        a, b = self.node("a"), self.node("b")
        source, record = self.ledger("shared.sqlite3")
        private, _ = self.ledger("private.sqlite3")
        second, _ = self.ledger("other-field.sqlite3")
        dataset = a.share(source, "Irrigation")
        # Display names need not be unique; separate file identities select data.
        other_dataset = a.share(second, "Irrigation")
        self.assertNotEqual(dataset, other_dataset)
        self.assertEqual([], b.available())
        # Knowing (and correctly pinning) a public certificate does not authorize a catalog read.
        with self.assertRaisesRegex(ValueError, "Access refused"):
            peer_wire.call(a.status()["address"], a.status()["peer_id"], {"op": "catalog"})
        peer_id = self.connect(b, a)
        catalog = b.available()
        self.assertCountEqual([(dataset, "Irrigation", peer_id), (other_dataset, "Irrigation", peer_id)],
                              [(f["dataset"], f["name"], f["peer_id"]) for f in catalog])
        self.assertNotIn(str(source), json.dumps(catalog))
        self.assertNotIn(private.name, json.dumps(catalog))

        sentinel = self.root / "existing.sqlite3"
        sentinel.write_bytes(b"keep the existing document")
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            b.join(dataset, peer_id, sentinel)
        self.assertEqual(b"keep the existing document", sentinel.read_bytes())
        self.assertEqual([], b.local_files())
        target = self.root / "orchard.sqlite3"
        b.join(dataset, peer_id, target)
        with closing(Repository(target)) as joined:
            self.assertEqual(record, joined.get_flow(record["flow_id"]))
        self.assertEqual([dataset], [f["dataset"] for f in b.local_files()])
        a.unshare(dataset)
        self.assertEqual([other_dataset], [f["dataset"] for f in a.local_files()])
        self.assertEqual([other_dataset], [f["dataset"] for f in b.available()])
        a.unshare(other_dataset)
        self.assertEqual([], b.available())
        self.assertTrue(source.exists())

    def test_multicast_discovery_pairs_by_name_without_broadcasting_file_or_device_names(self):
        # Use the loopback interface so discovery never advertises a test service on
        # the developer's LAN and still tests two real multicast sockets and TLS.
        observer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.addCleanup(observer.close)
        try:
            observer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            observer.bind(("", peer_wire.DISCOVERY_PORT))
            interface = socket.inet_aton("127.0.0.1")
            observer.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                socket.inet_aton(peer_wire.GROUP) + interface)
        except OSError as err:
            self.skipTest("loopback multicast is unavailable: " + str(err))
        observer.settimeout(4)
        peers = []
        for name in ("Discovery A", "Discovery B"):
            node = PeerNode(self.root / name, name=name, host="127.0.0.1",
                            port=0, discovery=True, interval=3600)
            self.addCleanup(node.stop)
            node.start()
            peers.append(node)
        a, b = peers
        source, _ = self.ledger("discover.sqlite3")
        dataset = a.share(source, "Discovered file")
        a.nearby_open()
        b.nearby_open()
        self.assertEqual([], b.available())
        a_id = a.status()["peer_id"]
        # Discard startup beacons from before the file and pairing window existed.
        observer.setblocking(False)
        try:
            while True:
                observer.recvfrom(1024)
        except BlockingIOError:
            pass
        observer.settimeout(4)
        deadline = time.monotonic() + 8
        while True:
            beacon, _ = observer.recvfrom(1024)
            if a_id.encode("ascii") in beacon:
                self.assertNotIn(b"Discovery A", beacon)
                self.assertNotIn(b"Discovered file", beacon)
                self.assertNotIn(dataset.encode("ascii"), beacon)
                break
            if time.monotonic() >= deadline:
                self.fail("Did not observe the publishing device's multicast beacon.")
        deadline = time.monotonic() + 8
        while a_id not in {p["peer_id"] for p in b.nearby_poll()["devices"]}:
            if time.monotonic() >= deadline:
                self.fail("The device did not appear through multicast discovery.")
            time.sleep(.05)
        self.complete_pairing(b, a, a_id)
        self.assertEqual([dataset], [f["dataset"] for f in b.available()])

    def test_retired_pairing_requests_cannot_authorize_or_read_proofs(self):
        node = self.node("device")
        node.nearby_open()
        state = node.status()
        for header in ({"op": "pair", "code": "OLDINVITATION"},
                       {"op": "proof", "ticket": "old-ticket"}):
            with self.subTest(operation=header["op"]):
                with self.assertRaisesRegex(ValueError, "Access refused"):
                    peer_wire.call(state["address"], state["peer_id"], header)
        self.assertEqual([], node.peers())
        self.assertIsNone(node.nearby_poll()["request"])

    def test_retired_invitation_state_is_removed_without_forgetting_devices(self):
        a, b = self.node("a"), self.node("b")
        self.connect(b, a)
        before = a.peers()
        a.stop()
        with a._db() as db:
            db.execute("CREATE TABLE pending(ticket TEXT PRIMARY KEY, proof TEXT, expires INTEGER)")
            db.execute("INSERT INTO pending VALUES('ticket','proof',9999999999)")
            db.executemany("INSERT INTO meta VALUES(?,?)", [(key, "private-value") for key in
                           ("invite_hash", "invite_until", "invite_attempts")])
        restarted = self.node("a")
        self.assertEqual(before, restarted.peers())
        with restarted._db() as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='pending'").fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM meta WHERE key LIKE 'invite_%'").fetchone())

    def test_missing_or_replaced_file_does_not_become_a_new_shared_database(self):
        a, b = self.node("owner"), self.node("reader")
        watched, _ = self.ledger("published.sqlite3")
        dataset = a.share(watched, "Published")
        peer_id = self.connect(b, a)
        watched.rename(self.root / "moved.sqlite3")
        joined = self.root / "joined.sqlite3"
        with self.assertRaises((ValueError, OSError, RuntimeError, KeyError)):
            b.join(dataset, peer_id, joined)
        self.assertFalse(watched.exists())
        self.assertFalse(joined.exists())

        watched.touch()
        with self.assertRaises((ValueError, OSError, RuntimeError, KeyError)):
            b.join(dataset, peer_id, joined)
        self.assertEqual(b"", watched.read_bytes())
        self.assertFalse(joined.exists())
        watched.unlink()

        private, _ = self.ledger("replacement.sqlite3")
        private.replace(watched)
        original = watched.read_bytes()
        with self.assertRaises((ValueError, OSError, RuntimeError, KeyError)):
            b.join(dataset, peer_id, joined)
        self.assertFalse(joined.exists())
        self.assertEqual(original, watched.read_bytes())
        self.assertEqual([], b.local_files())

    def test_revocation_blocks_remote_catalog_even_if_the_other_device_remembers(self):
        a, b = self.node("host"), self.node("guest")
        source, _ = self.ledger("orchard.sqlite3")
        dataset = a.share(source, "Orchard")
        self.connect(b, a)
        self.assertEqual([dataset], [f["dataset"] for f in b.available()])
        a.forget(a.peers()[0]["peer_id"])
        self.assertEqual([], b.available())
        self.assertEqual([], a.peers())
        self.assertTrue(source.exists())

    def test_legacy_file_requests_are_refused_before_opening_or_receiving_content(self):
        from flowledger import peer_store
        a, b = self.node("current"), self.node("legacy-client")
        source, record = self.ledger("current.sqlite3")
        dataset = a.share(source, "Water")
        identity = self.connect(b, a)
        target = self.root / "client.sqlite3"
        b.join(dataset, identity, target)
        with closing(Repository(target)) as client:
            client.save_flow(dict(record, notes="Must not reach the current server"))
            outgoing = peer_store.missing(client, [])
        with closing(Repository(source)) as server:
            baseline = peer_store.genesis(server)
            before = server.list_flows(), peer_store.inventory(server)
        original = source.read_bytes()
        token = b._peer_snapshot()[0]["token"]
        # The old client omitted this field; explicit protocol 2 is equally
        # incompatible. Every file operation must stop before opening the file.
        with patch.object(a, "_open", wraps=a._open) as opened:
            for protocol in (None, 2):
                for operation in ("catalog", "seed", "pull", "push"):
                    with self.subTest(protocol=protocol, operation=operation):
                        header = dict(op=operation, id=b.status()["peer_id"], token=token,
                                      dataset=dataset, baseline=baseline)
                        if protocol is not None:
                            header["protocol"] = protocol
                        body = outgoing if operation == "push" else [] if operation == "pull" else None
                        with self.assertRaisesRegex(ValueError, "Update Flow on both devices"):
                            peer_wire.call(a.status()["address"], identity, header, body)
            opened.assert_not_called()
        self.assertEqual(original, source.read_bytes())
        with closing(Repository(source)) as server:
            self.assertEqual(before, (server.list_flows(), peer_store.inventory(server)))
        self.assertEqual([identity], [p["peer_id"] for p in b.peers()])

    def test_current_client_refuses_legacy_replies_before_sending_file_content(self):
        from flowledger import peer_store
        a, b = self.node("legacy-server"), self.node("current")
        source, record = self.ledger("server.sqlite3")
        dataset = a.share(source, "Water")
        identity = self.connect(b, a)
        target = self.root / "current.sqlite3"
        b.join(dataset, identity, target)
        with closing(Repository(target)) as client:
            client.save_flow(dict(record, notes="Must not reach the legacy server"))
            outgoing = peer_store.missing(client, [])
        with closing(Repository(source)) as server:
            baseline = peer_store.genesis(server)
            seed = peer_store.seed(server)
            seed["protocol"] = 2
            before = server.list_flows(), peer_store.inventory(server)
        token = b._peer_snapshot()[0]["token"]
        bodies, headers = [], []
        completed = threading.Event()
        unchanged = False

        def legacy_handle(header, stream, host):
            # A pre-0.8 listener authenticates the peer and ignores an unknown
            # protocol header. Its unversioned ready ack must not release data.
            self.assertEqual(token, header["token"])
            headers.append(header)
            operation = header["op"]
            if operation == "catalog":
                return [dict(dataset=dataset, name="Water")]
            if operation == "seed":
                return dict(name="Water", seed=seed)
            if operation == "pull" and unchanged:
                return {"unchanged": True}
            peer_wire.write(stream, {"ready": True}, peer_wire.HEADER)
            try:
                body = peer_wire.read(stream)
                bodies.append(body)
                if operation == "push":
                    with closing(Repository(source)) as server:
                        peer_store.receive(server, body)
                return True
            except OSError:
                return True  # The current client closed without sending a body.
            finally:
                completed.set()

        with patch.object(a._listener, "handle", side_effect=legacy_handle):
            for operation in ("catalog", "seed", "pull", "push"):
                with self.subTest(operation=operation):
                    completed.clear()
                    body = outgoing if operation == "push" else [] if operation == "pull" else None
                    with self.assertRaisesRegex(ValueError, "Update Flow on both devices"):
                        b._remote(identity, operation, dataset=dataset, baseline=baseline, body=body)
                    if body is not None:
                        self.assertTrue(completed.wait(2))
            unchanged = True
            with self.assertRaisesRegex(ValueError, "Update Flow on both devices"):
                b._remote(identity, "pull", dataset=dataset, baseline=baseline, body=[])
            destination = self.root / "not-installed.sqlite3"
            # Refuse an old seed before creating any staged or final file.
            with self.assertRaisesRegex(ValueError, "Update Flow on both devices"):
                b.join("another-dataset", identity, destination)
            self.assertFalse(destination.exists())
            self.assertEqual([], b.available())
            self.assertIn("Update Flow on both devices", b.status()["error"])
        self.assertTrue(headers)
        self.assertTrue(all(h["protocol"] == peer_store.PROTOCOL for h in headers))
        self.assertEqual([], bodies)
        with closing(Repository(source)) as server:
            self.assertEqual(before, (server.list_flows(), peer_store.inventory(server)))
        self.assertEqual([identity], [p["peer_id"] for p in b.peers()])

    def test_relay_without_original_listener_preserves_edits_and_local_only_files(self):
        a, b, c = self.node("a"), self.node("b"), self.node("c")
        source, record = self.ledger("original.sqlite3")
        # Represents a UI that opened this document before a second connection shared it.
        opened = Repository(source)
        self.addCleanup(opened.close)
        dataset = a.share(source, "Water")
        a_id = self.connect(b, a)
        path_b, path_c = self.root / "joined-b.sqlite3", self.root / "joined-c.sqlite3"
        b.join(dataset, a_id, path_b)
        b_id = self.connect(c, b)
        self.assertEqual([b_id], [p["peer_id"] for p in c.peers()])
        c.join(dataset, b_id, path_c)
        c_port = c.status()["port"]
        c.stop()
        opened.save_flow(dict(record, amount="-0.00000000005"))
        b.sync_once()
        with closing(Repository(path_b)) as repo_b:
            self.assertEqual("-0.00000000005", repo_b.get_flow(record["flow_id"])["amount"])

        a.stop()
        # C has not yet received the small exact quantity. Changing a different
        # field now should preserve both independently made edits during relay.
        with closing(Repository(path_c)) as repo_c:
            before = repo_c.get_flow(record["flow_id"])
            self.assertEqual(record["amount"], before["amount"])
            repo_c.save_flow(dict(before, notes="Measured offline from original device"))
        c = self.node("c", port=c_port)
        c.sync_once()
        with closing(Repository(path_c)) as repo_c:
            relayed = repo_c.get_flow(record["flow_id"])
            self.assertEqual("-0.00000000005", relayed["amount"])
            self.assertEqual("Measured offline from original device", relayed["notes"])
        c.sync_once()
        with closing(Repository(path_b)) as repo_b:
            self.assertEqual("Measured offline from original device",
                             repo_b.get_flow(record["flow_id"])["notes"])

    def test_matching_dataset_id_cannot_merge_independently_seeded_files(self):
        from flowledger import peer_store
        a, b = self.node("device-a"), self.node("device-b")
        path_a, first = self.ledger("original-a.sqlite3")
        path_b, second = self.ledger("original-b.sqlite3")
        identifier = str(uuid.uuid4())
        with closing(Repository(path_a)) as left, closing(Repository(path_b)) as right:
            right.save_flow(dict(second, amount="8.75"))
            for repo in (left, right):
                peer_store.share(repo, "Irrigation", dataset_id=identifier)
            self.assertNotEqual(peer_store.genesis(left), peer_store.genesis(right))
            self.assertEqual(identifier, a.share(path_a))
            self.assertEqual(identifier, b.share(path_b))
            self.connect(b, a)
            # Give each independent history an actual change to send.
            left.save_flow(dict(first, notes="Only A"))
            right.save_flow(dict(right.get_flow(second["flow_id"]), notes="Only B"))
            before = [(repo.list_flows(), peer_store.inventory(repo)) for repo in (left, right)]
            for node in (a, b):
                result = node.sync_once()
                self.assertIn("different original snapshot", " ".join(result["errors"]))
                self.assertEqual((0, 0), (result["received"], result["sent"]))
            self.assertEqual(before, [(repo.list_flows(), peer_store.inventory(repo))
                                      for repo in (left, right)])

    def test_one_exchange_drains_multiple_pages_from_both_devices(self):
        from flowledger import peer_store
        a, b = self.node("origin"), self.node("replica")
        source, record = self.ledger("pages-a.sqlite3")
        dataset = a.share(source, "Pages")
        remote = self.connect(b, a)
        target = self.root / "pages-b.sqlite3"
        b.join(dataset, remote, target)
        with closing(Repository(source)) as left, closing(Repository(target)) as right:
            for side, repo in (("left", left), ("right", right)):
                for index in range(4):
                    repo.save_flow(dict(record, flow_id=None, name=side + str(index)))
            # Only shrink the page. All storage, socket exchanges and commits are real.
            original_missing = peer_store.missing
            def single_event(repo, known):
                return original_missing(repo, known)[:1]
            with patch.object(peer_store, "missing", side_effect=single_event):
                result = b.sync_once()
            self.assertEqual([], result["errors"])
            self.assertGreaterEqual(result["sent"], 4)
            self.assertGreaterEqual(result["received"], 4)
            from_left = {r["flow_id"]: r for r in left.list_flows()}
            self.assertEqual(9, len(from_left))
            self.assertEqual(from_left, {r["flow_id"]: r for r in right.list_flows()})

    def test_timer_updates_an_open_document_without_manual_sync(self):
        a = self.node("saved")
        b = self.node("open", interval=0.25)
        source, record = self.ledger("origin.sqlite3")
        dataset = a.share(source, "Water")
        remote = self.connect(b, a)
        target = self.root / "open-for-view.sqlite3"
        b.join(dataset, remote, target)
        with closing(Repository(target)) as view, closing(Repository(source)) as editor:
            before = view.conn.execute("PRAGMA data_version").fetchone()[0]
            editor.save_flow(dict(record, notes="Arrived on timer"))
            deadline = time.monotonic() + 8
            while view.get_flow(record["flow_id"])["notes"] != "Arrived on timer":
                if time.monotonic() >= deadline:
                    self.fail("The automatic exchange did not update the open local document: "
                              + b.status()["error"])
                time.sleep(0.05)
            self.assertNotEqual(before, view.conn.execute("PRAGMA data_version").fetchone()[0])

    def test_persistent_identity_catalog_and_trust_after_restart(self):
        a, b = self.node("a"), self.node("b")
        source, _ = self.ledger("permanent.sqlite3")
        dataset = a.share(source, "Meter")
        peer_id = self.connect(b, a)
        port = a.status()["port"]
        a.stop()
        # DHCP / port reuse must not silently replace a paired device.
        replacement = self.node("different-device", port=port)
        self.assertEqual([], b.available())
        self.assertEqual([], replacement.peers())
        replacement.stop()
        restarted = self.node("a", port=port)
        self.assertEqual([dataset], [f["dataset"] for f in restarted.local_files()])
        self.assertEqual([peer_id], [p["peer_id"] for p in b.peers()])
        self.assertEqual([dataset], [f["dataset"] for f in b.available()])

    def test_two_application_processes_share_one_listener_and_survive_owner_exit(self):
        # Two windows attach to one listener. The second takes ownership
        # when the first exits, without publishing a second port.
        config = self.root / "one-installation"
        script = (
            "import json, os, sys\n"
            "from flowledger.peer_network import PeerNode\n"
            "n = PeerNode(sys.argv[1], name='Shared machine', host='127.0.0.1', "
            "port=0, discovery=False, interval=3600)\n"
            "n.start()\n"
            "print(json.dumps(n.status()), flush=True)\n"
            "sys.stdin.readline()\n"
            "os._exit(0)\n")
        with subprocess.Popen([sys.executable, "-u", "-c", script, str(config)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True) as process:
            try:
                original = json.loads(process.stdout.readline())
                self.assertTrue(original["owner"])
                background = self.node("one-installation", port=original["port"])
                self.assertFalse(background.status()["owner"])
                self.assertEqual(original["port"], background.status()["port"])
                other = self.node("other")
                # One window accepts the nearby request through the other
                # window's single bound socket and shared device configuration.
                self.connect(other, background)
                self.assertEqual(1, len(background.peers()))
                # Exit without PeerNode.stop: OS ownership must be sufficient,
                # even if an application never gets to execute cleanup handlers.
                process.stdin.write("quit immediately\n")
                process.stdin.flush()
                process.wait(timeout=10)
                self.assertEqual(0, process.returncode)
                deadline = time.monotonic() + 12
                while not background.status()["owner"] and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(background.status()["owner"])
                self.assertEqual(original["peer_id"], background.status()["peer_id"])
                self.assertEqual(1, len(background.peers()))
                another = self.node("another")
                self.connect(another, background)
                self.assertEqual(2, len(background.peers()))
            finally:
                if process.poll() is None:
                    process.kill()


if __name__ == "__main__":
    unittest.main()
