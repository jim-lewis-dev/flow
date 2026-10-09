"""History ordering and field mapping through real Tk widget bindings."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from flowledger.core import Repository
from flowledger.table_columns import displayed_columns, field_at


@unittest.skipUnless(os.environ.get("DISPLAY"), "A graphical DISPLAY is required")
class ViewHistoryTests(unittest.TestCase):
    def setUp(self):
        from flowledger.gui import FlowApp
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.directory.name) / "views.sqlite3")
        self.repo.save_account("Checking", 100000)
        self.repo.save_account("Shop", 0)
        self.first = self.repo.save_flow(dict(date="2026-10-02 12:34:56", from_account="Checking",
            to_account="Shop", amount="20", name="Lunch", category="Dining", tags=["Trip"]))
        self.second = self.repo.save_flow(dict(date="2026-10-03", from_account="Checking",
            to_account="Shop", amount="30", name="Repair", category="Boat", status="Pending", tags=["Boat"]))
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
        self.app.destroy()
        self.repo.close()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self):
        self.app.update_idletasks()
        self.app.update()
        self.assertEqual(self.errors, [])

    def select(self, flow=None):
        self.app.ledger.selection_set((flow or self.first)["flow_id"])
        self.pump()

    def type_field(self, key, value):
        widget = self.app.form_widgets[key]
        entry = widget.entry if hasattr(widget, "entry") else widget
        entry.focus_force()
        self.pump()
        entry.delete(0, "end")
        entry.insert(0, value)
        self.pump()
        return entry

    def shortcut(self, sequence, widget=None):
        widget = widget or self.app
        widget.focus_force()
        self.pump()
        widget.event_generate(sequence)
        self.pump()

    def test_status_category_and_tag_selections_undo_in_order_without_database_writes(self):
        before = "\n".join(self.repo.conn.iterdump())
        for key, values in (("Status", ["Complete", "Pending"]),
                            ("Category", ["Boat"]), ("Tag", ["Boat"])):
            self.app.ledger_filters[key].set_selected(values, notify=True)
        self.assertEqual(set(self.app.ledger_rows), {self.second["flow_id"]})
        for key in ("Tag", "Category", "Status"):
            self.shortcut("<Control-z>")
            self.assertEqual(self.app.ledger_filters[key].get(), ())
        self.assertEqual(len(self.app.ledger_rows), 2)
        for key, values in (("Status", ("Complete", "Pending")),
                            ("Category", ("Boat",)), ("Tag", ("Boat",))):
            self.shortcut("<Control-y>")
            self.assertEqual(self.app.ledger_filters[key].get(), values)
        self.assertEqual("\n".join(self.repo.conn.iterdump()), before)

    def test_filter_popup_shortcuts_preserve_popup_checks_and_selection(self):
        choice = self.app.ledger_filters["Status"]
        choice.button.invoke()
        self.pump()
        choice.checkbuttons["Complete"].invoke()
        choice.checkbuttons["Pending"].invoke()
        popup = choice.popup
        self.shortcut("<Control-z>", choice.checkbuttons["Pending"])
        self.assertIs(choice.popup, popup)
        self.assertEqual(choice.get(), ("Complete",))
        self.assertFalse(choice._choice_variables["Pending"].get())
        self.shortcut("<Control-y>", choice.checkbuttons["Pending"])
        self.assertEqual(choice.get(), ("Complete", "Pending"))
        self.assertTrue(choice._choice_variables["Pending"].get())

    def test_choice_search_undo_keeps_selected_values_and_filters(self):
        choice = self.app.ledger_filters["Category"]
        choice.set_selected(["Boat"], notify=True)
        choice.open_popup()
        self.pump()
        entry = choice.search_entry
        entry.focus_force()
        self.pump()
        for character in "din":
            entry.insert("end", character)
        self.assertEqual(list(choice.checkbuttons), ["Dining"])
        self.shortcut("<Control-z>", entry)
        self.assertEqual(entry.get(), "")
        self.assertEqual(choice.get(), ("Boat",))
        self.shortcut("<Control-y>", entry)
        self.assertEqual(entry.get(), "din")
        choice.close_popup()
        self.shortcut("<Control-z>")
        choice.open_popup()
        self.pump()
        self.assertEqual(choice.search_entry.get(), "")

    def test_ledger_search_typing_is_one_action_then_filter_is_next(self):
        entry = self.app.search_entry
        entry.focus_force()
        self.pump()
        for character in "Repair":
            entry.insert("end", character)
        self.app.ledger_filters["Category"].set_selected(["Boat"], notify=True)
        self.shortcut("<Control-z>")
        self.assertEqual(entry.get(), "Repair")
        self.shortcut("<Control-z>")
        self.assertEqual(entry.get(), "")
        self.assertEqual(len(self.app.ledger_rows), 2)
        self.shortcut("<Control-y>")
        self.assertEqual(entry.get(), "Repair")
        self.shortcut("<Control-y>")
        self.assertEqual(self.app.ledger_filters["Category"].get(), ("Boat",))

    def test_record_edit_then_filter_undo_does_not_reload_or_revert_the_edit(self):
        self.select()
        entry = self.type_field("name", "Changed lunch")
        entry.event_generate("<Return>")
        self.pump()
        self.app.ledger_filters["Category"].set_selected(["Boat"], notify=True)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(self.first["flow_id"])["name"], "Changed lunch")
        self.assertEqual(self.app.form_vars["name"].get(), "Changed lunch")
        self.assertEqual(self.app.editing_id, self.first["flow_id"])
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(self.first["flow_id"]), self.first)
        self.shortcut("<Control-y>")
        self.shortcut("<Control-y>")
        self.assertEqual(self.app.ledger_filters["Category"].get(), ("Boat",))
        self.assertEqual(self.repo.get_flow(self.first["flow_id"])["name"], "Changed lunch")

    def test_filter_finishes_a_valid_edit_before_recording_itself(self):
        self.select()
        self.type_field("amount", "33.00")
        self.app.ledger_filters["Status"].button.focus_force()
        self.pump()
        self.app.ledger_filters["Status"].set_selected(["Pending"], notify=True)
        self.assertEqual(self.app.history.undo_label, "Status filter")
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(self.first["flow_id"])["amount_cents"], 3300)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(self.first["flow_id"]), self.first)

    def test_invalid_typing_survives_later_filter_undo_then_can_itself_be_undone(self):
        self.select()
        entry = self.type_field("amount", "invalid")
        self.app.ledger_filters["Status"].set_selected(["Complete"], notify=True)
        self.shortcut("<Control-z>")
        self.assertEqual(entry.get(), "invalid")
        self.assertEqual(self.app.ledger_filters["Status"].get(), ())
        self.assertFalse(self.app.redo_button.instate(["disabled"]))
        self.shortcut("<Control-y>")
        self.assertEqual(entry.get(), "invalid")
        self.assertEqual(self.app.ledger_filters["Status"].get(), ("Complete",))
        self.shortcut("<Control-z>")
        self.shortcut("<Control-z>", entry)
        self.assertEqual(entry.get(), "20.00")
        self.assertEqual(self.repo.get_flow(self.first["flow_id"]), self.first)

    def test_noop_and_filter_vocab_refresh_do_not_clear_redo(self):
        choice = self.app.ledger_filters["Category"]
        choice.set_selected(["Missing category"], notify=True)
        choice.set_values(["Dining"])
        self.shortcut("<Control-z>")
        choice.set_selected([], notify=True)
        self.app.refresh_all()
        self.assertTrue(self.app.history.can_redo)
        self.shortcut("<Control-y>")
        self.assertEqual(choice.get(), ("Missing category",))
        self.assertEqual(len(self.app.ledger_rows), 0)

    def test_a_new_filter_change_discards_redo(self):
        choice = self.app.ledger_filters["Status"]
        choice.set_selected(["Complete"], notify=True)
        self.shortcut("<Control-z>")
        choice.set_selected(["Pending"], notify=True)
        self.assertFalse(self.app.history.can_redo)
        self.shortcut("<Control-y>")
        self.assertEqual(choice.get(), ("Pending",))

    def test_date_pair_is_one_undoable_action(self):
        self.app._put_date_filters(("2026-10-03", "2026-10-03"))
        self.app.view_history.record("dates", "Ledger dates")
        self.assertEqual(set(self.app.ledger_rows), {self.second["flow_id"]})
        self.shortcut("<Control-z>")
        self.assertEqual((self.app.filter_start.get(), self.app.filter_end.get()), ("", ""))
        self.assertEqual(len(self.app.ledger_rows), 2)
        self.shortcut("<Control-y>")
        self.assertEqual(set(self.app.ledger_rows), {self.second["flow_id"]})

    def test_sort_order_undo_redo(self):
        original = (self.app.sort_column, self.app.sort_reverse, self.app.ledger.get_children())
        self.app.sort_ledger("name")
        sorted_rows = self.app.ledger.get_children()
        self.shortcut("<Control-z>")
        self.assertEqual((self.app.sort_column, self.app.sort_reverse, self.app.ledger.get_children()), original)
        self.shortcut("<Control-y>")
        self.assertEqual(self.app.ledger.get_children(), sorted_rows)

    def test_new_draft_fields_have_multiple_undo_steps_and_clear_is_reversible(self):
        self.type_field("from_account", "Checking")
        self.type_field("to_account", "New shop")
        self.type_field("amount", ".50")
        full = self.app._flow_snapshot()
        self.app.new_flow_button.invoke()
        self.pump()
        self.shortcut("<Control-z>")
        self.assertEqual(self.app._flow_snapshot(), full)
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.form_vars["amount"].get(), "")
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.form_vars["to_account"].get(), "")
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.form_vars["from_account"].get(), "")
        for _ in range(4):
            self.shortcut("<Control-y>")
        self.assertEqual(self.app._flow_snapshot(), self.app._clean_flow)
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_new_notes_peer_undo_keeps_expanded_view_open(self):
        self.app.edit_notes()
        peer = self.app.expanded_notes_text
        peer.focus_force()
        self.pump()
        peer.insert("end", "Draft notes")
        self.pump()
        self.shortcut("<Control-z>", peer)
        self.assertTrue(peer.winfo_exists())
        self.assertEqual(peer.get("1.0", "end-1c"), "")
        self.shortcut("<Control-y>", peer)
        self.assertEqual(peer.get("1.0", "end-1c"), "Draft notes")
        self.assertEqual(self.app.notes_text.get("1.0", "end-1c"), "Draft notes")

    def test_discard_by_blank_space_or_row_selection_can_restore_a_whole_unadded_draft(self):
        self.type_field("name", "Unadded draft")
        self.type_field("amount", ".50")
        before = self.app._flow_snapshot()
        self.app.ledger.event_generate("<Button-1>", x=50, y=self.app.ledger.winfo_height()-20)
        self.pump()
        self.assertEqual(self.app.form_vars["name"].get(), "")
        self.shortcut("<Control-z>")
        self.assertEqual(self.app._flow_snapshot(), before)
        self.select()
        self.shortcut("<Control-z>")
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app._flow_snapshot(), before)
        self.shortcut("<Control-y>")
        self.assertEqual(self.app.editing_id, self.first["flow_id"])
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_page_change_discard_is_reversible_without_undoing_navigation(self):
        self.type_field("name", "Unadded draft")
        before = self.app._flow_snapshot()
        self.app.show_view("charts")
        self.pump()
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.current_view, "charts")
        self.assertEqual(self.app._flow_snapshot(), before)
        self.app.show_view("flows")
        self.pump()
        self.assertEqual(self.app._flow_snapshot(), before)
        self.assertEqual(len(self.repo.list_flows()), 2)

    def test_ordinary_filter_reopen_resets_query_but_undo_closed_search_restores_it(self):
        choice = self.app.ledger_filters["Category"]
        choice.open_popup()
        self.pump()
        choice.search_entry.insert(0, "Boat")
        self.pump()
        choice.close_popup()
        choice.open_popup()
        self.pump()
        self.assertEqual(choice.search_entry.get(), "")
        choice.close_popup()
        self.shortcut("<Control-z>")
        self.shortcut("<Control-y>")
        choice.open_popup()
        self.pump()
        self.assertEqual(choice.search_entry.get(), "Boat")

    def test_repeat_undo_restores_selection_and_redo_restores_all_copy_fields(self):
        self.select()
        fields = self.app._flow_snapshot()
        self.app.repeat_selected()
        self.pump()
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.editing_id, self.first["flow_id"])
        self.shortcut("<Control-y>")
        self.assertIsNone(self.app.editing_id)
        self.assertEqual(self.app._flow_snapshot(), fields)
        self.assertEqual(self.app.form_title.cget("text"), "Repeat Flow")

    def test_graph_accounts_basis_and_combine_undo_redo_in_order(self):
        self.app.show_view("charts")
        self.pump()
        original = self.app._graph_controls()
        self.app._select_graph_accounts(True)
        self.app.chart_basis.set("Including Pending")
        self.app.chart_combined.set(True)
        for expected in (False, "Confirmed", original[0]):
            self.shortcut("<Control-z>")
            current = self.app._graph_controls()
            self.assertIn(expected, current)
        self.assertEqual(self.app._graph_controls(), original)
        for _ in range(3):
            self.shortcut("<Control-y>")
        self.assertEqual(self.app.chart_basis.get(), "Including Pending")
        self.assertTrue(self.app.chart_combined.get())
        self.assertEqual(len(self.app.chart_accounts.curselection()), 2)

    def test_graph_date_calendar_and_update_restore_controls_and_rendered_plot(self):
        self.app.show_view("charts")
        self.pump()
        original = self.app._graph_state()
        self.app.chart_start_picker.open_popup()
        self.pump()
        self.app.chart_start_picker.day_buttons[5].invoke()
        self.pump()
        self.assertEqual(self.app.chart_start.get(), "2026-10-05")
        self.app.update_chart()
        self.pump()
        applied = self.app._graph_state()
        self.shortcut("<Control-z>")
        self.assertEqual(self.app.chart_start.get(), "2026-10-05")
        self.assertEqual(self.app._chart_rendered_controls, original[1])
        self.shortcut("<Control-z>")
        self.assertEqual(self.app._graph_state(), original)
        self.shortcut("<Control-y>")
        self.shortcut("<Control-y>")
        self.assertEqual(self.app._graph_state(), applied)

    def test_graph_line_visibility_and_limits_undo_redo(self):
        self.app.show_view("charts")
        self.pump()
        legend, line = next(iter(self.app.chart_line_map.items()))
        self.app.toggle_chart_line(SimpleNamespace(artist=legend))
        self.pump()
        self.assertFalse(line.get_visible())
        original_limits = (self.app.axes.get_xlim(), self.app.axes.get_ylim())
        self.app.axes.set_xlim(20700, 20710)
        self.app.axes.set_ylim(-50, 100)
        self.pump()
        self.shortcut("<Control-z>")
        self.assertEqual((self.app.axes.get_xlim(), self.app.axes.get_ylim()), original_limits)
        self.assertFalse(next(iter(self.app.chart_series)).get_visible())
        self.shortcut("<Control-z>")
        self.assertTrue(next(iter(self.app.chart_series)).get_visible())
        self.shortcut("<Control-y>")
        self.shortcut("<Control-y>")
        self.assertFalse(next(iter(self.app.chart_series)).get_visible())
        self.assertEqual(self.app.axes.get_xlim(), (20700., 20710.))
        self.assertEqual(self.app.axes.get_ylim(), (-50., 100.))

    def drag(self, tree, source, target):
        row = tree.get_children()[0]
        left, _, width, _ = tree.bbox(row, source)
        end, _, endwidth, _ = tree.bbox(row, target)
        start_x, end_x = left + width // 2, end + endwidth // 2
        tree.event_generate("<ButtonPress-1>", x=start_x, y=10)
        tree.event_generate("<B1-Motion>", x=end_x, y=10)
        tree.event_generate("<ButtonRelease-1>", x=end_x, y=10)
        self.pump()

    def test_header_drag_is_one_action_does_not_sort_and_cells_edit_correct_fields(self):
        before = displayed_columns(self.app.ledger)
        sort = self.app.sort_column, self.app.sort_reverse
        self.drag(self.app.ledger, "amount_cents", "date")
        moved = displayed_columns(self.app.ledger)
        self.assertEqual(moved[0], "amount_cents")
        self.assertEqual((self.app.sort_column, self.app.sort_reverse), sort)
        row = self.first["flow_id"]
        bounds = self.app.ledger.bbox(row, "amount_cents")
        x0, y0, width, height = bounds
        self.assertEqual(field_at(self.app.ledger, x0 + width // 2), "amount_cents")
        # Exercise the editor's display-position mapping, rather than open by key.
        self.app.ledger_editor._double_click(SimpleNamespace(x=x0+width//2, y=y0+height//2))
        self.pump()
        self.assertEqual(self.app.ledger_editor.field, "amount_cents")
        editor = self.app.ledger_editor.input
        editor.delete(0, "end")
        editor.insert(0, ".75")
        editor.event_generate("<Return>")
        self.pump()
        self.assertEqual(self.repo.get_flow(row)["amount_cents"], 75)
        self.shortcut("<Control-z>")
        self.assertEqual(self.repo.get_flow(row), self.first)
        self.assertEqual(displayed_columns(self.app.ledger), moved)
        self.shortcut("<Control-z>")
        self.assertEqual(displayed_columns(self.app.ledger), before)
        self.shortcut("<Control-y>")
        self.assertEqual(displayed_columns(self.app.ledger), moved)

    def test_reordered_tab_order_and_account_read_only_columns(self):
        self.drag(self.app.ledger, "amount_cents", "date")
        self.app.ledger_editor.open(self.first["flow_id"], "amount_cents")
        self.pump()
        self.app.ledger_editor.input.event_generate("<Tab>")
        self.pump()
        self.assertEqual(self.app.ledger_editor.field, "date")
        self.app.show_view("accounts")
        self.pump()
        before = displayed_columns(self.app.accounts_tree)
        self.drag(self.app.accounts_tree, "anchor", "balance")
        moved = displayed_columns(self.app.accounts_tree)
        self.assertEqual(moved[:3], ("name", "anchor", "balance"))
        self.assertEqual(self.app.account_cell_editor.editable_columns, ("anchor", "balance"))
        self.assertFalse(self.app.account_cell_editor.open("Checking", "confirmed"))
        self.shortcut("<Control-z>")
        self.assertEqual(displayed_columns(self.app.accounts_tree), before)
        self.shortcut("<Control-y>")
        self.assertEqual(displayed_columns(self.app.accounts_tree), moved)

    def test_session_reopen_resets_filters_columns_and_history_but_keeps_records(self):
        from flowledger.gui import FlowApp
        self.app.ledger_filters["Category"].set_selected(["Boat"], notify=True)
        self.drag(self.app.ledger, "amount_cents", "date")
        self.app.request_close()
        self.app = FlowApp(self.repo)
        self.pump()
        self.assertEqual(self.app.ledger_filters["Category"].get(), ())
        self.assertEqual(displayed_columns(self.app.ledger), tuple(self.app.ledger["columns"]))
        self.assertFalse(self.app.history.can_undo)
        self.assertFalse(self.app.history.can_redo)
        self.assertEqual(len(self.repo.list_flows()), 2)


if __name__ == "__main__":
    unittest.main()
