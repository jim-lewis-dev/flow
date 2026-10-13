"""Actual ledger ordering, clipboard adjacency, and selection history."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class FlowSortGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "sort.sqlite3")
        self.next_id = 1000
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.app.focus_force()
        self.pump()

    def tearDown(self):
        try:
            self.app.ledger_editor.close(commit=False)
            self.app.history.close()
            self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [], "Tk callback failures must surface")

    def save(self, **changes):
        self.next_id += 1
        values = dict(flow_id=str(uuid.UUID(int=self.next_id)),
                      date="2026-10-13 10:20:30", from_account="Checking",
                      to_account="Store", amount="5", units="USD",
                      status="Complete", category="Supplies", name="Purchase",
                      tags=["Trip"], notes="Notes")
        values.update(changes)
        return self.repo.save_flow(values)["flow_id"]

    def refresh(self):
        self.app.refresh_all()
        self.pump()

    def order(self):
        return self.app.ledger.get_children()

    def key(self, gesture):
        self.app.ledger.focus_force()
        self.pump()
        self.app.ledger.event_generate(gesture)
        self.pump()

    def test_default_sort_uses_each_logical_tie_field_and_complete_timestamp(self):
        # Each pair differs at exactly one tier; later metadata cannot mask
        # that tier. Insert higher values first so storage order cannot pass.
        cases = (
            ("date", "2026-10-13 10:20:30", "2026-10-13 10:20:31"),
            ("from_account", "alpha", "Zeta"),
            ("to_account", "alpha", "Zeta"),
            ("amount", "-2", "0"),
            ("units", "MW", "mW"),
            ("status", "Complete", "Pending"),
            ("category", "alpha", "Zeta"),
            ("name", "alpha", "Zeta"),
        )
        expected = []
        for index, (field, low, high) in enumerate(cases):
            high_id = self.save(**{field: high}, notes=f"Sort pair {index}.")
            low_id = self.save(**{field: low}, notes=f"Sort pair {index}.")
            expected.append((high_id, low_id) if field == "date"
                            else (low_id, high_id))
        self.refresh()
        for index, (field, _, _) in enumerate(cases):
            with self.subTest(field=field):
                self.app.search_var.set(f"Sort pair {index}.")
                self.app.refresh_ledger()
                self.pump()
                self.assertEqual(self.order(), expected[index])

    def test_units_are_exact_and_status_ties_follow_lifecycle_order(self):
        expected = []
        for units in ("mW", "MW"):
            ids = {}
            for status in ("Canceled", "Planned", "Pending", "Complete"):
                ids[status] = self.save(units=units, status=status)
            if units == "MW":
                expected[0:0] = [ids[status] for status in
                                 ("Complete", "Pending", "Planned", "Canceled")]
            else:
                expected.extend(ids[status] for status in
                                ("Complete", "Pending", "Planned", "Canceled"))
        self.refresh()
        self.assertEqual(self.order(), tuple(expected))

    def test_amount_header_is_numeric_and_reverses_only_primary(self):
        ten = self.save(amount="10")
        neg_later = self.save(amount="-2", date="2026-10-13 10:20:31")
        zero = self.save(amount="0")
        neg_ten = self.save(amount="-10")
        two = self.save(amount="2")
        neg_earlier = self.save(amount="-2", date="2026-10-13 10:20:30")
        self.refresh()
        self.app.sort_ledger("amount")
        self.pump()
        self.assertEqual(self.order(),
                         (neg_ten, neg_earlier, neg_later, zero, two, ten))
        self.app.sort_ledger("amount")
        self.pump()
        self.assertEqual(self.order(),
                         (ten, two, zero, neg_earlier, neg_later, neg_ten))

    def test_identical_sort_fields_keep_copy_adjacent_and_ids_unique(self):
        first = self.save(notes="First saved row")
        source = self.save(notes="Source to copy")
        last = self.save(notes="Last saved row")
        self.refresh()
        self.assertEqual(self.order(), (first, source, last))
        self.app.ledger.selection_set(source)
        self.app.ledger.focus(source)
        self.pump()
        original = self.repo.get_flow(source)
        self.key("<Control-c>")
        with patch("flowledger.core.uuid.uuid4", return_value=uuid.UUID(int=1)):
            self.key("<Control-v>")
        copied = self.app.editing_id
        self.assertNotIn(copied, (first, source, last))
        self.assertEqual(self.repo.get_flow(copied), {**original, "flow_id": copied})
        for field in ("date", "from_account", "to_account", "amount",
                      "units", "status", "category", "name"):
            with self.subTest(field=field):
                self.app.sort_ledger(field)
                self.pump()
                self.assertEqual(self.order(), (first, source, copied, last))
                self.assertEqual(self.app.ledger.selection(), (copied,))
                self.assertEqual(self.app.editing_id, copied)

    def test_sort_undo_redo_preserves_multiple_selection_and_active_record(self):
        alpha_name = self.save(from_account="Zeta", name="alpha")
        zeta_name = self.save(from_account="alpha", name="Zeta")
        self.refresh()
        before = (zeta_name, alpha_name)
        self.assertEqual(self.order(), before)
        self.app.ledger.selection_set(before)
        self.app.ledger.focus(alpha_name)
        self.pump()
        self.assertEqual(self.app.editing_id, alpha_name)
        self.app.sort_ledger("name")
        self.pump()
        after = (alpha_name, zeta_name)
        self.assertEqual(self.order(), after)
        self.assertEqual(set(self.app.ledger.selection()), set(before))
        self.assertEqual(self.app.editing_id, alpha_name)
        self.key("<Control-z>")
        self.assertEqual(self.order(), before)
        self.assertEqual(set(self.app.ledger.selection()), set(before))
        self.assertEqual(self.app.editing_id, alpha_name)
        self.key("<Control-y>")
        self.assertEqual(self.order(), after)
        self.assertEqual(set(self.app.ledger.selection()), set(before))
        self.assertEqual(self.app.editing_id, alpha_name)
