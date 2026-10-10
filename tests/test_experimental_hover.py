"""Hover details in Experimental must describe saved records without editing them."""

import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest

from flowledger.core import Repository


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ExperimentalHoverTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp

        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "hover.sqlite3")
        self.repo.save_account("Checking", "100", "2026-10-01", units="USD")
        for name, when, amount, status, category, notes, tags, target in (
            ("Hidden groceries", "2026-10-02 09:00:00", "10", "Complete",
             "Groceries", "Other purchase", ["hidden"], "Grocer"),
            ("Lunch", "2026-10-02 10:00:00", ".125", "Complete",
             "Dining", "Soup and tea", ["weekday", "near office"], "Cafe"),
            ("Future coffee", "2026-10-03 10:00:00", "3", "Pending",
             "Dining", "Later", ["weekday"], "Cafe"),
            ("Canceled item", "2026-10-03 11:00:00", "5", "Canceled",
             "Dining", "Never happened", [], "Cafe"),
        ):
            self.repo.save_flow(dict(date=when, from_account="Checking", to_account=target,
                                     amount=amount, units="USD", status=status, name=name,
                                     category=category, notes=notes, tags=tags))
        self.callback_errors = []
        errors = self.callback_errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, error, trace):
                errors.append(f"{kind.__name__}: {error}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1280x800+0+0")
        self.app.ledger_filters["Category"].set_selected(["Dining"], notify=True)
        self.app.show_view("experimental")
        self.pump()
        self.view = self.app.experimental
        self.assertEqual([flow["name"] for flow in self.view._events], ["Lunch"])

    def tearDown(self):
        try:
            if self.app.winfo_exists():
                self.app.experimental.close()
                self.app._close_popups()
                self.app.history.close()
                self.app.destroy()
        finally:
            self.repo.close()
            self.directory.cleanup()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must surface")

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.callback_errors, [], "Tk callback errors must surface")

    def saved_state(self):
        return tuple(self.repo.list_flows()), tuple(self.repo.conn.iterdump())

    def hover(self, item):
        """Deliver a real Canvas pointer motion to the item's visible center."""
        canvas = self.view.canvas
        x0, y0, x1, y1 = canvas.bbox(item)
        x = round((x0 + x1) / 2 - canvas.canvasx(0))
        y = round((y0 + y1) / 2 - canvas.canvasy(0))
        canvas.event_generate("<Motion>", x=x, y=y)
        self.pump()
        # Tooltip timing is part of the interaction; allow its delayed display.
        self.app.after(650, self.app.quit)
        self.app.mainloop()
        self.pump()
        return self.view.hover_var.get(), self._popup_text()

    def _popup_text(self):
        """Read the visible Canvas tooltip, regardless of its widget nesting."""
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        visible = [widget for widget in children(self.app)
                   if isinstance(widget, tk.Toplevel) and widget.winfo_viewable()]
        self.assertTrue(visible, "Hover should open a near-cursor tooltip")
        labels = []
        for popup in visible:
            for widget in children(popup):
                if not isinstance(widget, (tk.Label, ttk.Label)):
                    continue
                literal = widget.cget("text")
                variable = widget.cget("textvariable")
                labels.append(self.app.getvar(variable) if variable else literal)
        return "\n".join(labels)

    def _node(self, name):
        return next(item for item, value in self.view._node_items.items()
                    if value == name and self.view.canvas.type(item) == "oval")

    def test_account_hover_includes_full_ledger_balance_and_visible_totals(self):
        self.view.next_button.invoke()
        self.pump()
        before = self.saved_state()
        detail, popup = self.hover(self._node("Checking"))
        self.assertIn("Checking", detail)
        self.assertIn("Checking", popup)
        self.assertIn("USD", popup)
        self.assertIn("2026-10-01", popup)
        self.assertRegex(popup, r"\b100(?:\.0+)?\b")
        # 100 - hidden 10 - visible .125 = 89.875, presented either exactly
        # or using USD's configured two-place display precision.
        self.assertRegex(popup, r"\b89\.(?:875|88)\b")
        self.assertRegex(popup, r"(?i)\bIn\b")
        self.assertRegex(popup, r"(?i)\bOut\b")
        self.assertIn("0.125", popup)

        self.view.basis_combo.current(1)  # Include the pending flow tomorrow.
        self.view.basis_combo.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.view.next_button.invoke()
        self.view.next_button.invoke()
        self.pump()
        detail, popup = self.hover(self._node("Checking"))
        self.assertIn("Checking", detail)
        self.assertRegex(popup, r"\b86\.(?:875|88)\b")
        self.assertIn("3.125", popup)  # Shown Out, including Pending.
        self.assertEqual(before, self.saved_state())

    def test_unknown_account_and_flow_dot_show_metadata_without_writes(self):
        self.view.next_button.invoke()
        self.pump()
        before = self.saved_state()
        detail, popup = self.hover(self._node("Cafe"))
        self.assertIn("Cafe", detail)
        self.assertIn("Cafe", popup)
        self.assertIn("Unknown", popup)

        pulse = next(item for item, flow in self.view._pulse_items.items()
                     if flow["name"] == "Lunch" and self.view.canvas.type(item) == "oval")
        detail, popup = self.hover(pulse)
        self.assertIn("Lunch", detail)
        for expected in ("Lunch", "2026-10-02", "Complete", "Checking", "Cafe",
                         "0.125 USD", "Dining", "weekday", "near office", "Soup and tea"):
            self.assertIn(expected, popup)
        self.assertEqual(before, self.saved_state())


if __name__ == "__main__":
    unittest.main()
