"""Removed suggestions persist independently of the ledger and its objects."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger.suggestions import SuggestionPreferences


class SuggestionPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "flow.sqlite3"
        self.preferences = SuggestionPreferences(self.database)

    def tearDown(self):
        self.temp.cleanup()

    def test_removal_and_explicit_restoration_survive_restart(self):
        values = ["Dining", "Boat", "Groceries"]
        self.assertTrue(self.preferences.remove("category", "Boat"))
        self.assertFalse(self.preferences.remove("category", "boat"))
        restarted = SuggestionPreferences(self.database)
        self.assertIsNone(restarted.load_error)
        self.assertEqual(restarted.visible("category", values), ["Dining", "Groceries"])
        self.assertTrue(restarted.restore("category", "BOAT"))
        self.assertFalse(restarted.restore("category", "Boat"))
        self.assertEqual(SuggestionPreferences(self.database).visible("category", values), values)

    def test_normalization_keeps_original_display_order_and_shares_account_kind(self):
        self.preferences.remove("account", "  STRASSE ")
        values = ["Cash", "Straße", "Checking"]
        self.assertEqual(self.preferences.visible("account", values), ["Cash", "Checking"])
        # Both From and To pass the same vocabulary kind, while other fields
        # with the same text are unrelated.
        self.assertEqual(self.preferences.visible("account", reversed(values)), ["Checking", "Cash"])
        for kind in ("category", "tag", "name"):
            self.assertEqual(self.preferences.visible(kind, values), values)

    def test_unit_identity_is_case_sensitive_and_survives_restart(self):
        values = ["USD", "mW", "MW", "kg"]
        self.assertTrue(self.preferences.remove("unit", " mW "))
        self.assertEqual(self.preferences.visible("unit", values), ["USD", "MW", "kg"])
        restarted = SuggestionPreferences(self.database)
        self.assertEqual(restarted.visible("unit", values), ["USD", "MW", "kg"])
        self.assertFalse(restarted.restore("unit", "MW"))
        self.assertEqual(restarted.visible("unit", values), ["USD", "MW", "kg"])
        self.assertTrue(restarted.restore("unit", "mW"))
        self.assertEqual(restarted.visible("unit", values), values)

    def test_old_preferences_load_with_empty_unit_removals(self):
        self.preferences.path.write_text('{"account": ["cash"], "category": [], "tag": [], "name": []}')
        restarted = SuggestionPreferences(self.database)
        self.assertIsNone(restarted.load_error)
        self.assertEqual(restarted.visible("unit", ["USD", "kg"]), ["USD", "kg"])
        self.assertEqual(restarted.visible("account", ["Cash", "Shop"]), ["Shop"])

    def test_different_databases_and_memory_instances_are_isolated(self):
        self.preferences.remove("name", "Lunch")
        other = SuggestionPreferences(Path(self.temp.name) / "other.sqlite3")
        self.assertEqual(other.visible("name", ["Lunch"]), ["Lunch"])
        memory = SuggestionPreferences(":memory:")
        with patch("flowledger.suggestions.tempfile.NamedTemporaryFile",
                   side_effect=AssertionError("In-memory preferences must not write files")):
            self.assertTrue(memory.remove("name", "Lunch"))
            self.assertEqual(memory.visible("name", ["Lunch"]), [])
            self.assertEqual(SuggestionPreferences(":memory:").visible("name", ["Lunch"]), ["Lunch"])
            self.assertTrue(memory.restore("name", "Lunch"))

    def test_preferences_never_change_saved_objects_or_database(self):
        repo = Repository(self.database)
        flow = repo.save_flow(dict(from_account="Cash", to_account="Shop",
                                   amount="12.00", units="USD", name="Lunch", category="Dining",
                                   tags=["Trip"]))
        before = "\n".join(repo.conn.iterdump())
        for kind, value in (("account", "Shop"), ("category", "Dining"),
                            ("name", "Lunch"), ("tag", "Trip")):
            self.preferences.remove(kind, value)
        self.assertEqual(repo.get_flow(flow["flow_id"]), flow)
        self.assertEqual("\n".join(repo.conn.iterdump()), before)
        self.assertEqual(self.preferences.visible("account", [a["name"] for a in repo.list_accounts()]), ["Cash"])
        repo.save_flow(flow)
        self.assertEqual(self.preferences.visible("name", repo.list_names()), [])
        repo.close()

    def test_failed_atomic_replace_keeps_memory_and_original_file(self):
        self.preferences.remove("tag", "Trip")
        before = self.preferences.path.read_bytes()
        for operation, value in ((self.preferences.remove, "Errand"),
                                 (self.preferences.restore, "Trip")):
            with self.subTest(operation=operation.__name__):
                with patch("flowledger.suggestions.os.replace", side_effect=OSError("Disk failure")):
                    with self.assertRaisesRegex(OSError, "Disk failure"):
                        operation("tag", value)
                self.assertEqual(self.preferences.visible("tag", ["Trip", "Errand"]), ["Errand"])
                self.assertEqual(self.preferences.path.read_bytes(), before)
                self.assertEqual(list(Path(self.temp.name).glob("*.tmp")), [])

    def test_failed_write_does_not_claim_removal(self):
        with patch("flowledger.suggestions.os.fsync", side_effect=OSError("Disk full")):
            with self.assertRaisesRegex(OSError, "Disk full"):
                self.preferences.remove("category", "Boat")
        self.assertEqual(self.preferences.visible("category", ["Boat"]), ["Boat"])
        self.assertFalse(self.preferences.path.exists())
        self.assertEqual(list(Path(self.temp.name).glob("*.tmp")), [])

    def test_malformed_preferences_launch_safely_and_cannot_be_overwritten(self):
        malformed = (b"{", b"[]", b'{"account": "Cash"}',
                     b'{"unknown": []}', b'{"tag": [12]}',
                     b'{"tag": [""]}', b'{"tag": ["\\u0000"]}', b"\xff")
        for original in malformed:
            with self.subTest(original=original):
                self.preferences.path.write_bytes(original)
                damaged = SuggestionPreferences(self.database)
                self.assertIsInstance(damaged.load_error, str)
                self.assertEqual(damaged.visible("category", ["Boat"]), ["Boat"])
                for method in (damaged.remove, damaged.restore):
                    with self.assertRaises(ValueError):
                        method("category", "Boat")
                self.assertEqual(self.preferences.path.read_bytes(), original)

    def test_unreadable_preferences_also_block_overwrite(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("No access")):
            blocked = SuggestionPreferences(self.database)
        self.assertIn("No access", blocked.load_error)
        with self.assertRaises(ValueError):
            blocked.remove("name", "Lunch")

    def test_invalid_requests_do_not_write_preferences(self):
        for kind, value in (("status", "Complete"), ("tag", ""),
                            ("tag", None), ("tag", "bad\x00value")):
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                self.preferences.remove(kind, value)
        self.assertFalse(self.preferences.path.exists())


if __name__ == "__main__":
    unittest.main()
