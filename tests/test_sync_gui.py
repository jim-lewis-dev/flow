"""Incoming edits preserve unfinished input and cannot resurrect stale fields."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class SyncGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "sync.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01", units="USD")
        self.flow = self.repo.save_flow(dict(date="2026-10-02", from_account="Checking",
            to_account="Shop", amount="10", units="USD", name="Purchase", notes="Original"))
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(str(value))

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1400x900+0+0")
        self.pump()
        self.app._load_form(self.flow)
        self.pump()

    def tearDown(self):
        self.app.sync_controller.close()
        self.app.experimental.close()
        self.app.history.close()
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()

    def test_full_form_commit_preserves_remote_untouched_fields(self):
        self.app.notes_text.delete("1.0", "end")
        self.app.notes_text.insert("1.0", "Local notes")
        self.repo.save_flow({**self.flow, "amount": "25", "tags": ["Remote tag"]})
        self.assertTrue(self.app._finish_existing("flow"))
        record = self.repo.get_flow(self.flow["flow_id"])
        self.assertEqual((record["amount"], record["tags"], record["notes"]),
                         ("25", ["Remote tag"], "Local notes"))
        self.assertEqual(self.app.form_vars["tags"].get(), "Remote tag")

    def test_same_field_changed_while_typing_does_not_overwrite_remote(self):
        self.app.form_vars["amount"].set("30")
        self.repo.save_flow({**self.flow, "amount": "25"})
        self.assertFalse(self.app._finish_existing("flow"))
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"])["amount"], "25")
        self.assertEqual(self.app.form_vars["amount"].get(), "30")
        self.assertIn("changed on another device", self.app.form_feedback.get())

    def test_incoming_edit_refresh_keeps_incomplete_field_and_focus(self):
        self.app.form_widgets["amount"].focus_force()
        self.pump()
        self.app.form_vars["amount"].set("2+")
        self.repo.save_flow({**self.flow, "notes": "Phone notes", "name": "New name"})
        self.app._refresh_synced_records()
        self.assertEqual(self.app.form_vars["amount"].get(), "2+")
        self.assertEqual(self.app.form_vars["name"].get(), "New name")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Phone notes")
        self.assertEqual(self.app.editing_id, self.flow["flow_id"])
        self.assertEqual(self.app.focus_get(), self.app.form_widgets["amount"])
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"])["amount"], "10")
        self.app.form_vars["amount"].set("2+3")
        self.assertTrue(self.app._finish_existing("flow"))
        record = self.repo.get_flow(self.flow["flow_id"])
        self.assertEqual((record["amount"], record["notes"]), ("5", "Phone notes"))

    def test_new_draft_survives_remote_record_and_account_refresh(self):
        self.app.new_flow(focus=False, confirm=False)
        self.app.form_vars["name"].set("Unfinished")
        self.app.form_vars["amount"].set("12+")
        draft = self.app._flow_snapshot()
        self.repo.save_flow({**self.flow, "notes": "Remote"})
        self.app._refresh_synced_records()
        self.assertEqual(self.app._flow_snapshot(), draft)
        self.assertIsNone(self.app.editing_id)

    def test_account_commit_only_changes_edited_starting_value(self):
        self.app.show_view("accounts")
        self.app._load_account(self.repo.get_account("Checking", "USD"))
        self.app.account_vars["opening"].set("200")
        self.repo.save_account("Checking", "100", "2026-09-01", units="USD")
        self.assertTrue(self.app._finish_existing("account"))
        account = self.repo.get_account("Checking", "USD")
        self.assertEqual((account["opening_amount"], account["opening_at"]),
                         ("200", "2026-09-01 00:00:00"))

    def test_incoming_updates_keep_both_inline_editors_alive(self):
        self.assertTrue(self.app.ledger_editor.open(self.flow["flow_id"], "notes"))
        self.pump()
        overlay = self.app.ledger_editor.widget
        self.app.notes_text.delete("1.0", "end")
        self.app.notes_text.insert("1.0", "Still typing")
        self.repo.save_flow({**self.flow, "amount": "25"})
        self.app._refresh_synced_records()
        self.assertIs(self.app.ledger_editor.widget, overlay)
        self.assertEqual(overlay.get("1.0", "end-1c"), "Still typing")
        self.assertEqual(self.app.focus_get(), overlay)
        self.assertTrue(self.app.ledger_editor.close())
        self.app.show_view("accounts")
        account = self.repo.get_account("Checking", "USD")
        self.app._load_account(account)
        key = self.app._account_key(account)
        self.pump()
        self.assertTrue(self.app.account_cell_editor.open(key, "balance"))
        self.pump()
        overlay = self.app.account_cell_editor.widget
        self.app.account_vars["opening"].set("500+")
        self.repo.save_account("Checking", "100", "2026-09-01", units="USD")
        self.app._refresh_synced_records()
        self.assertIs(self.app.account_cell_editor.widget, overlay)
        self.assertEqual(overlay.get(), "500+")
        self.assertEqual(self.app.account_vars["date"].get(), "2026-09-01 00:00:00")
        self.assertEqual(self.app.focus_get(), overlay)

    def enable_sync(self):
        from flowledger.sync import SyncState
        self.app.sync_controller.close()
        SyncState(self.repo).configure(dataset="money")
        server, peer = Repository(":memory:"), Repository(":memory:")
        self.addCleanup(server.close)
        self.addCleanup(peer.close)
        SyncState(server).configure(dataset="money", server=True, seed=self.repo.sync.export_seed())
        SyncState(peer).configure(dataset="money", seed=server.sync.export_seed())
        self.app._refresh_synced_records()
        self.app.view_history.remember("experimental")
        return server, peer

    def test_remote_change_back_to_original_is_still_guarded_while_typing(self):
        server, peer = self.enable_sync()
        self.app.form_vars["amount"].set("30")
        for amount in ("25", "10"):
            peer.save_flow({**peer.get_flow(self.flow["flow_id"]), "amount": amount})
            peer.sync.accept_response(server.sync.server_exchange(peer.sync.prepare_request()))
        self.repo.sync.accept_response(server.sync.server_exchange(self.repo.sync.prepare_request()))
        self.app._refresh_synced_records()
        self.assertFalse(self.app._finish_existing("flow"))
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"])["amount"], "10")
        self.assertEqual(self.app.form_vars["amount"].get(), "30")

    def test_synced_node_undo_preserves_another_devices_other_node(self):
        server, peer = self.enable_sync()
        view = self.app.experimental
        self.app.show_view("experimental")
        self.pump()
        before = view.get_state()
        view._positions["Checking"] = (0.2, 0.3)
        self.assertTrue(view._save_positions(before))
        self.repo.sync.accept_response(server.sync.server_exchange(self.repo.sync.prepare_request()))
        peer.sync.accept_response(server.sync.server_exchange(peer.sync.prepare_request()))
        positions = peer.load_map_positions()
        positions["Shop"] = (0.9, 0.8)
        peer.save_map_positions(positions)
        peer.sync.accept_response(server.sync.server_exchange(peer.sync.prepare_request()))
        self.repo.sync.accept_response(server.sync.server_exchange(self.repo.sync.prepare_request()))
        self.app._refresh_synced_records()
        self.app.undo()
        self.assertEqual(self.repo.load_map_positions()["Shop"], (0.9, 0.8))
        self.assertNotIn("Checking", self.repo.load_map_positions())

    def test_connect_fetches_on_worker_and_applies_on_tk_thread(self):
        from flowledger.sync import SyncState
        server = Repository(":memory:")
        self.addCleanup(server.close)
        self.repo.conn.backup(server.conn)
        SyncState(server).configure(dataset="money", server=True)
        seed = server.sync.export_seed()
        connection = dict(dataset="money", endpoint="https://sync.example.test:8765",
                          token="a" * 40, certificate="b" * 64)
        worker_threads = []

        def fetch(_config):
            worker_threads.append(threading.get_ident())
            return seed

        import json
        controller = self.app.sync_controller
        controller.show()
        controller.connection_text.insert("1.0", json.dumps(connection))
        with patch("flowledger.sync_ui.sync_transport.fetch_bootstrap", side_effect=fetch):
            controller._connect()
            deadline = time.monotonic() + 2
            while controller.running and time.monotonic() < deadline:
                self.pump()
                time.sleep(.01)
        self.assertFalse(controller.running)
        self.assertTrue(self.repo.sync.enabled)
        self.assertEqual(self.repo.sync.configuration()["endpoint"], connection["endpoint"])
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], threading.get_ident())
        self.assertEqual(controller.connection_text.get("1.0", "end-1c"), "")
        self.assertEqual(self.repo.list_flows(), [self.flow])

    def test_paginated_pull_stays_syncing_until_last_page(self):
        server, peer = self.enable_sync()
        peer.save_flow({**peer.get_flow(self.flow["flow_id"]), "name": "Phone name"})
        peer.save_flow({**peer.get_flow(self.flow["flow_id"]), "notes": "Phone notes"})
        peer.sync.accept_response(server.sync.server_exchange(peer.sync.prepare_request()))
        request = self.repo.sync.prepare_request()
        with patch("flowledger.sync.MAX_BATCH_OPERATIONS", 1):
            first = server.sync.server_exchange(request)
            second = server.sync.server_exchange({**request, "cursor": first["cursor"]})
        self.assertTrue(first["more"])
        self.assertFalse(second["more"])
        controller = self.app.sync_controller
        controller.closed = False
        controller.running = True
        controller.results.put(("exchange", {}, first, None))
        with patch.object(controller, "_start") as start:
            controller._tick()
            self.assertEqual(self.app.sync_button.cget("text"), "Syncing")
            self.assertTrue(controller.catching_up)
            start.assert_called_once()
            self.app.after_cancel(controller.after_id)
            controller.after_id = None
            controller.running = True
            controller.results.put(("exchange", {}, second, None))
            controller._tick()
        self.assertEqual(self.app.sync_button.cget("text"), "Up to date")
        self.assertFalse(controller.catching_up)
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"])["notes"], "Phone notes")


if __name__ == "__main__":
    unittest.main()
