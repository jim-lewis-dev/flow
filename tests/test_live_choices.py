"""Suggestions and selectable values follow current saved references."""
import os
import unittest

from flowledger.core import Repository


def flow_data():
    return dict(date="2026-10-02", from_account="Source", to_account="Utilities",
                amount="5", units="MW", name="Old name", category="Old category",
                tags=["Old tag"])


class LiveVocabularyTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(":memory:")

    def tearDown(self):
        self.repo.close()

    def test_live_choices_exclude_retained_lookup_rows_and_starting_balances(self):
        saved = self.repo.save_flow(flow_data())
        anchor = self.repo.save_account("Utilities", "123.45", units="MW")
        corrected = self.repo.save_flow({**saved, "to_account": "Power company",
            "units": "mW", "name": "New name", "category": "New category",
            "tags": ["New tag"]})
        self.assertEqual(self.repo.list_categories(referenced_only=True), ["New category"])
        self.assertEqual(self.repo.list_tags(referenced_only=True), ["New tag"])
        self.assertEqual(self.repo.list_units(referenced_only=True), ["mW"])
        self.assertEqual(self.repo.list_names(), ["New name"])
        self.assertIn("Old category", self.repo.list_categories())
        self.assertIn("Old tag", self.repo.list_tags())
        self.assertIn("MW", self.repo.list_units())
        self.assertEqual(self.repo.get_account("Utilities", "MW"), anchor)
        self.repo.delete_flow(corrected["flow_id"])
        for method in (self.repo.list_categories, self.repo.list_tags, self.repo.list_units):
            self.assertEqual(method(referenced_only=True), [])
        self.assertEqual(self.repo.list_names(), [])
        self.assertEqual(self.repo.get_account("Utilities", "MW"), anchor)

    def test_canceled_last_reference_keeps_choices_and_units_remain_case_sensitive(self):
        first = self.repo.save_flow(flow_data())
        canceled = self.repo.save_flow({**first, "flow_id": None, "status": "Canceled"})
        small = self.repo.save_flow({**first, "flow_id": None, "units": "mW",
                                    "category": "Other", "tags": ["Other"]})
        self.repo.delete_flow(first["flow_id"])
        self.assertIn("Old category", self.repo.list_categories(referenced_only=True))
        self.assertIn("Old tag", self.repo.list_tags(referenced_only=True))
        self.assertEqual(self.repo.list_units(referenced_only=True), ["MW", "mW"])
        self.repo.delete_flow(canceled["flow_id"])
        self.assertEqual(self.repo.list_categories(referenced_only=True), ["Other"])
        self.assertEqual(self.repo.list_tags(referenced_only=True), ["Other"])
        self.assertEqual(self.repo.list_units(referenced_only=True), ["mW"])
        self.assertEqual(self.repo.list_flows(), [small])


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs an X display")
class LiveChoicesGuiTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.repo = Repository(":memory:")
        self.flow = self.repo.save_flow(flow_data())
        self.anchor = self.repo.get_account("Utilities", "MW")
        self.kept = self.repo.save_flow({**self.flow, "flow_id": None,
            "from_account": "Keeper", "to_account": "Other", "units": "USD",
            "name": "Keep name", "category": "Keep category", "tags": ["Keep tag"]})
        self.errors = []
        errors = self.errors

        class CheckedApp(FlowApp):
            def report_callback_exception(self, kind, value, traceback):
                errors.append(f"{kind.__name__}: {value}")

        self.app = CheckedApp(self.repo)
        self.app.attributes("-fullscreen", False)
        self.app.geometry("1500x900+0+0")
        self.pump()

    def tearDown(self):
        self.app._close_popups()
        self.app.history.close()
        self.app.destroy()
        self.repo.close()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def select(self):
        self.app.ledger.selection_set(self.flow["flow_id"])
        self.pump()

    def assert_old_choices(self, present):
        for field, value in (("from_account", "Utilities"), ("to_account", "Utilities"),
                             ("name", "Old name"), ("category", "Old category"),
                             ("tags", "Old tag"), ("units", "MW")):
            self.assertEqual(value in self.app.form_widgets[field].values, present, field)
        for key, value in (("To", "Utilities"), ("Category", "Old category"),
                           ("Tag", "Old tag"), ("Units", "MW")):
            choice = self.app.ledger_filters[key]
            self.assertEqual(value in choice.values, present, key)
            self.assertEqual(value in choice._available_values(), present, key)
        self.assertEqual("MW" in self.app.account_widgets["units"].values, present)
        self.assertEqual(("Utilities", "MW") in self.app.chart_account_pairs, present)
        self.assertEqual(any(row["name"] == "Utilities" and row["units"] == "MW"
                             for row in self.app.account_rows.values()), present)
        self.assertEqual(self.repo.get_account("Utilities", "MW"), self.anchor)

    def test_corrected_last_reference_updates_forms_open_filter_and_inline_choices(self):
        self.select()
        choice = self.app.ledger_filters["Category"]
        choice.set_selected(["Old category"], notify=True)
        choice.open_popup()
        self.pump()
        popup = choice.popup
        for field, value in (("to_account", "Power company"), ("name", "New name"),
                             ("category", "New category"), ("tags", "New tag"),
                             ("units", "mW")):
            self.app.form_vars[field].set(value)
        self.assertTrue(self.app._finish_existing("flow"))
        self.pump()
        self.assert_old_choices(False)
        self.assertIs(choice.popup, popup)
        self.assertNotIn("Old category", choice.checkbuttons)
        self.assertEqual(choice.get(), ())
        for field, value in (("name", "New name"), ("category", "New category"),
                             ("tags", "New tag"), ("units", "mW"),
                             ("to_account", "Power company")):
            self.assertTrue(self.app.ledger_editor.open(self.flow["flow_id"], field))
            self.assertIn(value, self.app.ledger_editor.widget.values)
            self.app.ledger_editor.close(commit=False)
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_delete_undo_redo_refreshes_every_live_choice_without_losing_anchors(self):
        self.select()
        for key, value in (("To", "Utilities"), ("Category", "Old category"),
                           ("Tag", "Old tag"), ("Units", "MW")):
            self.app.ledger_filters[key].set_selected([value], notify=True)
        self.app.delete_selected()
        self.pump()
        self.assertIsNone(self.repo.get_flow(self.flow["flow_id"]))
        self.assert_old_choices(False)
        self.assertEqual(self.repo.list_flows(), [self.kept])
        for key in ("To", "Category", "Tag", "Units"):
            self.assertEqual(self.app.ledger_filters[key].get(), ())
        self.app.undo()
        self.pump()
        self.assertEqual(self.repo.get_flow(self.flow["flow_id"]), self.flow)
        self.assert_old_choices(True)
        self.app.redo()
        self.pump()
        self.assertIsNone(self.repo.get_flow(self.flow["flow_id"]))
        self.assert_old_choices(False)


if __name__ == "__main__":
    unittest.main()
