"""Functional checks for session undo, including dependencies and failed writes."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from flowledger.core import Repository
from flowledger.exchange import commit_import, preview_import
from flowledger.history import SessionHistory
from flowledger.suggestions import SuggestionPreferences


class SessionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")
        self.history = SessionHistory(self.repo)

    def tearDown(self):
        self.history.close()
        self.repo.close()

    def flow(self, **changes):
        return {"date": "2026-10-01 12:34:56", "from_account": "Amex",
                "to_account": "Joe's Diner", "amount": "24.99", "units": "USD",
                "status": "Complete", "name": "Lunch", "notes": "Two dishes\nwith tea",
                "category": "Dining", "tags": ["Friday", "Friends"], **changes}

    def add(self, **changes):
        return self.history.perform("Add Flow", lambda: self.repo.save_flow(self.flow(**changes)))

    def state(self):
        return {table: [tuple(row) for row in self.repo.conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
                for table in ("accounts", "account_balances", "categories", "tags", "flows", "flow_tags")}

    def consistent(self):
        self.assertIsNone(self.repo.conn.execute("PRAGMA foreign_key_check").fetchone())
        self.assertFalse(self.repo.conn.in_transaction)

    def test_add_undo_redo_restores_exact_fields_ids_and_learned_names(self):
        original = self.state()
        saved = self.add()
        added = self.state()
        self.assertEqual(self.history.undo_label, "Add Flow")
        self.assertEqual(self.history.undo(), "Add Flow")
        self.assertEqual(self.state(), original)
        self.assertFalse(self.history.can_undo)
        self.assertEqual(self.history.redo_label, "Add Flow")
        self.assertEqual(self.history.redo(), "Add Flow")
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.assertEqual(self.state(), added)
        self.consistent()

    def test_edit_restores_related_rows_and_tag_order(self):
        saved = self.add()
        before = self.state()
        edited = self.history.perform("Edit Flow", lambda: self.repo.save_flow({
            **saved, "from_account": "Cash", "category": "Travel", "amount": "123.45",
            "tags": ["Friends", "Road trip", "Friday"], "notes": "New notes"}))
        after = self.state()
        self.history.undo()
        self.assertEqual(self.state(), before)
        self.assertIsNone(self.repo.get_account("Cash"))
        self.history.redo()
        self.assertEqual(self.state(), after)
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), edited)
        self.consistent()

    def test_void_flow_restored_with_same_id(self):
        saved = self.add()
        self.history.perform("Void Flow", lambda: self.repo.void_flow(saved["flow_id"]))
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["status"], "Canceled")
        self.history.undo()
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.history.redo()
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["status"], "Canceled")

    def test_actual_delete_restores_flow_and_cascaded_tags(self):
        saved = self.add()
        before = self.state()
        audit = self.repo.history()
        deleted = self.history.perform("Delete Flow", lambda: self.repo.delete_flow(saved["flow_id"]))
        self.assertEqual(deleted, saved)
        self.assertIsNone(self.repo.get_flow(saved["flow_id"]))
        self.assertEqual(self.repo.conn.execute("SELECT COUNT(*) FROM flow_tags WHERE flow_id=?",
                                               (saved["flow_id"],)).fetchone()[0], 0)
        self.assertEqual(self.repo.history()[:len(audit)], audit)
        deletion = self.repo.history(saved["flow_id"])[-1]
        self.assertEqual(deletion["action"], "delete")
        self.assertEqual(json.loads(deletion["before_json"]), saved)
        self.assertIsNone(deletion["after_json"])
        self.history.undo()
        self.assertEqual(self.repo.get_flow(saved["flow_id"]), saved)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.repo.history(saved["flow_id"])[-1]["action"], "undo")
        self.history.redo()
        self.assertIsNone(self.repo.get_flow(saved["flow_id"]))
        self.assertEqual(self.repo.history(saved["flow_id"])[-1]["action"], "redo")
        self.consistent()

    def test_account_add_and_edit(self):
        self.history.perform("Add Account", lambda: self.repo.save_account("Checking", 50000))
        initial = self.repo.get_account("Checking")
        self.history.perform("Edit Account", lambda: self.repo.save_account(
            "Checking", -12500, "2026-10-02 00:00:04"))
        self.history.undo()
        self.assertEqual(self.repo.get_account("Checking"), initial)
        self.history.undo()
        self.assertIsNone(self.repo.get_account("Checking"))
        self.history.redo()
        self.history.redo()
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "-12500")

    def test_external_typing_merges_before_and_latest_after_without_touching_records(self):
        state = [""]
        for text in ("s", "se", "sea"):
            before = state[0]
            self.history.perform_external("Search", lambda t=text: state.__setitem__(0, t) or True,
                lambda v=before: state.__setitem__(0, v),
                lambda v=text: state.__setitem__(0, v), merge_key=("search", 1))
        self.assertFalse(self.history.undo_affects_records)
        self.history.undo()
        self.assertEqual(state, [""])
        self.assertFalse(self.history.can_undo)
        self.assertFalse(self.history.redo_affects_records)
        self.history.redo()
        self.assertEqual(state, ["sea"])
        self.assertEqual(self.repo.list_flows(), [])

    def test_merge_does_not_cross_record_action_or_an_undone_branch(self):
        state = [""]
        def type_value(text):
            before = state[0]
            self.history.perform_external("Search", lambda: state.__setitem__(0, text) or True,
                lambda: state.__setitem__(0, before), lambda: state.__setitem__(0, text),
                merge_key="same field")
        type_value("first")
        flow = self.add()
        type_value("second")
        self.history.undo()
        self.assertEqual(state, ["first"])
        self.assertTrue(self.history.undo_affects_records)
        self.history.undo()
        type_value("new branch")
        self.assertFalse(self.history.can_redo)
        self.history.undo()
        self.assertEqual(state, ["first"])
        self.history.undo()
        self.assertEqual(state, [""])
        self.assertIsNone(self.repo.get_flow(flow["flow_id"]))

    def test_batch_import_is_one_step_and_redo_keeps_generated_ids(self):
        before = self.state()
        preview = preview_import(self.repo, [self.flow(), self.flow(name="Dinner", amount="41")])
        result = self.history.perform("Import Flows", lambda: commit_import(self.repo, preview))
        after = self.state()
        self.assertEqual(result, {"imported": 2, "skipped": 0})
        self.history.undo()
        self.assertEqual(self.state(), before)
        self.assertFalse(self.history.can_undo)
        self.history.redo()
        self.assertEqual(self.state(), after)

    def test_failed_action_rolls_back_records_audit_and_keeps_redo(self):
        self.add()
        self.history.undo()
        original = self.state()
        audit = self.repo.history()

        def fail():
            self.repo.save_flow(self.flow())
            raise ValueError("failed after writing")

        with self.assertRaisesRegex(ValueError, "failed after writing"):
            self.history.perform("Failed", fail)
        self.assertEqual(self.state(), original)
        self.assertEqual(self.repo.history(), audit)
        self.assertEqual(self.history.redo_label, "Add Flow")
        self.history.redo()
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_caught_inner_rollback_does_not_leave_capture_events(self):
        def change():
            try:
                with self.repo.atomic():
                    self.repo.save_account("Discarded", 100)
                    raise ValueError("rolled back")
            except ValueError:
                pass
            return self.repo.save_account("Kept", 200)

        self.history.perform("Add Account", change)
        self.history.undo()
        self.assertEqual(self.repo.list_accounts(), [])
        self.history.redo()
        self.assertEqual([row["name"] for row in self.repo.list_accounts()], ["Kept"])

    def test_noop_including_rewritten_tag_rows_keeps_stacks(self):
        saved = self.add()
        self.history.perform("No change", lambda: self.repo.save_flow(saved))
        self.assertEqual(self.history.undo_label, "Add Flow")
        self.history.undo()
        self.history.perform("No change", lambda: 42)
        self.assertFalse(self.history.can_undo)
        self.assertTrue(self.history.can_redo)
        self.history.redo()

    def test_caught_nested_history_failure_rolls_back_its_changes(self):
        def failing_inner():
            self.repo.save_account("Discarded", 100)
            raise ValueError("inner action failed")

        def outer():
            self.repo.save_account("Kept", 200)
            try:
                self.history.perform("Inner", failing_inner)
            except ValueError:
                pass

        self.history.perform("Outer", outer)
        self.assertEqual([row["name"] for row in self.repo.list_accounts()], ["Kept"])
        self.history.undo()
        self.assertEqual(self.repo.list_accounts(), [])
        self.history.redo()
        self.assertEqual([row["name"] for row in self.repo.list_accounts()], ["Kept"])

    def test_new_action_discards_redo_but_failed_action_does_not(self):
        self.add()
        self.history.undo()
        with self.assertRaises(ValueError):
            self.add(amount="NaN")
        self.assertTrue(self.history.can_redo)
        self.history.perform("Add Account", lambda: self.repo.save_account("Checking"))
        self.assertFalse(self.history.can_redo)
        self.assertIsNone(self.history.redo())

    def test_multiple_changes_to_same_row_collapse_and_nested_actions_group(self):
        def change():
            self.repo.save_account("Checking", 100)
            self.history.perform("Inner", lambda: self.repo.save_account("Checking", 200))
            self.repo.save_account("Checking", 300)

        self.history.perform("Outer", change)
        self.assertEqual(len(self.history._undo[-1].changes), 2)
        self.assertEqual(self.history.undo(), "Outer")
        self.assertIsNone(self.repo.get_account("Checking"))
        self.assertFalse(self.history.can_undo)
        self.history.redo()
        self.assertEqual(self.repo.get_account("Checking")["opening_amount"], "300")

    def test_history_is_sparse_and_unrelated_changes_are_preserved(self):
        for number in range(100):
            self.repo.save_account(f"Account {number}", number)
        self.history.perform("Edit Account", lambda: self.repo.save_account("Account 1", 500))
        self.assertEqual(len(self.history._undo[-1].changes), 1)
        self.repo.save_account("Account 99", 999)
        self.history.undo()
        self.assertEqual(self.repo.get_account("Account 1")["opening_amount"], "1")
        self.assertEqual(self.repo.get_account("Account 99")["opening_amount"], "999")

    def test_unrecorded_change_to_affected_row_blocks_without_overwriting(self):
        saved = self.add()
        self.repo.save_flow({**saved, "name": "Changed elsewhere"})
        before = self.state()
        with self.assertRaisesRegex(ValueError, "changed outside"):
            self.history.undo()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.history.undo_label, "Add Flow")
        self.assertFalse(self.history.can_redo)
        self.consistent()

    def test_foreign_key_failure_rolls_back_undo_and_retains_stack(self):
        self.history.perform("Add Account", lambda: self.repo.save_account("Amex"))
        self.repo.save_flow(self.flow())
        before = self.state()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "depends"):
            self.history.undo()
        self.assertEqual(self.state(), before)
        self.assertTrue(self.history.can_undo)
        self.consistent()

    def test_audit_retains_original_events_and_appends_undo_redo(self):
        saved = self.add()
        original = self.repo.history()
        self.history.undo()
        self.assertEqual(self.repo.history()[:len(original)], original)
        undone = self.repo.history(saved["flow_id"])[-1]
        self.assertEqual(undone["action"], "undo")
        self.assertEqual(json.loads(undone["before_json"]), saved)
        self.assertIsNone(undone["after_json"])
        self.history.redo()
        redone = self.repo.history(saved["flow_id"])[-1]
        self.assertEqual(redone["action"], "redo")
        self.assertEqual(json.loads(redone["after_json"]), saved)

    def test_close_removes_temp_objects_and_reopening_has_no_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            repo = Repository(database)
            history = SessionHistory(repo)
            history.perform("Add Flow", lambda: repo.save_flow(self.flow()))
            history.close()
            self.assertEqual(repo.conn.execute("SELECT name FROM sqlite_temp_master").fetchall(), [])
            history.close()
            repo.close()
            repo = Repository(database)
            try:
                history = SessionHistory(repo)
                self.assertFalse(history.can_undo)
                self.assertFalse(history.can_redo)
                self.assertEqual(len(repo.list_flows()), 1)
                self.assertIsNone(history.undo())
                history.close()
            finally:
                repo.close()

    def test_backup_excludes_session_history_and_clear_follows_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "backup.sqlite3"
            self.add()
            self.repo.backup(database)
            with sqlite3.connect(database) as backup:
                names = {row[0] for row in backup.execute("SELECT name FROM sqlite_master")}
            self.assertFalse(any(name.startswith("flow_history_") for name in names))
            self.repo.restore(database)
            self.history.clear()
            self.assertFalse(self.history.can_undo)
            self.add(name="Second")
            self.history.undo()
            self.assertEqual(len(self.repo.list_flows()), 1)

    def test_external_preferences_share_order_and_skip_noops(self):
        preferences = SuggestionPreferences(":memory:")
        self.add()

        def remove():
            return preferences.remove("category", "Dining")

        def restore():
            return preferences.restore("category", "Dining")

        self.assertTrue(self.history.perform_external("Remove suggestion", remove, restore, remove))
        self.assertFalse(self.history.perform_external("Already removed", remove, restore, remove))
        self.assertEqual(self.history.undo(), "Remove suggestion")
        self.assertEqual(preferences.visible("category", ["Dining"]), ["Dining"])
        self.assertEqual(self.history.undo(), "Add Flow")
        self.assertEqual(self.history.redo(), "Add Flow")
        self.assertEqual(self.history.redo(), "Remove suggestion")
        self.assertEqual(preferences.visible("category", ["Dining"]), [])

    def test_failed_external_undo_keeps_stack(self):
        def fail():
            raise OSError("cannot write preference file")

        self.history.perform_external("Remove suggestion", lambda: True, fail, lambda: True)
        with self.assertRaises(OSError):
            self.history.undo()
        self.assertEqual(self.history.undo_label, "Remove suggestion")
        self.assertFalse(self.history.can_redo)


if __name__ == "__main__":
    unittest.main()
