"""Undo, filters, graph controls and header dragging with native X11 input.

Only setup and outcome checks access application objects. All user actions are
physical XTest mouse/keyboard events, including drags and popup shortcuts.
"""
from pathlib import Path


def seed_view_data(repo):
    for name, cents in (("Checking", 100000), ("Savings", 50000), ("Shop", 0)):
        repo.save_account(name, cents, "2026-10-01")
    for when, target, amount, name, category, status, tags in (
        ("2026-10-02 12:34:56", "Shop", "20", "Lunch", "Dining", "Complete", ["Trip"]),
        ("2026-10-03", "Shop", "30", "Repair", "Boat", "Pending", ["Boat"]),
        ("2026-10-15", "Savings", "50", "Transfer", "Transfer", "Planned", []),
    ):
        repo.save_flow(dict(date=when, from_account="Checking", to_account=target,
            amount=amount, name=name, category=category, status=status, tags=tags))


shared = Path(__file__).with_name("ui_smoke.py").read_text().split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_view_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))
from flowledger.table_columns import displayed_columns


def top_entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def undo():
    chord("Control_L", "z")


def redo():
    chord("Control_L", "y")


def open_filter(key):
    choice = app.ledger_filters[key]
    click(choice.button)
    assert choice.popup is not None
    return choice


def clear_filters():
    for key in ("Status", "Category", "Tag"):
        choice = open_filter(key)
        click(choice.all_button)
        key_escape()


def key_escape():
    key("Escape")


def pointer_drag(start, end):
    pump(.55)
    move(*start)
    xt.XTestFakeButtonEvent(display, 1, True, 0)
    x.XSync(display, False)
    pump(.06)
    for fraction in (.15, .35, .55, .75, 1):
        move(start[0] + fraction * (end[0] - start[0]),
             start[1] + fraction * (end[1] - start[1]))
    xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.25)


def header_point(tree, field):
    bounds = tree.bbox(tree.get_children()[0], field)
    assert bounds
    x0, _, width, _ = bounds
    return tree.winfo_rootx() + x0 + width / 2, tree.winfo_rooty() + 12


def drag_header(tree, source, target):
    pointer_drag(header_point(tree, source), header_point(tree, target))


def double_cell(tree, iid, field):
    pump(.55)
    tree.see(iid)
    pump(.08)
    bx, by, width, height = tree.bbox(iid, field)
    move(tree.winfo_rootx() + bx + width / 2, tree.winfo_rooty() + by + height / 2)
    for _ in range(2):
        xt.XTestFakeButtonEvent(display, 1, True, 0)
        xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.2)


success = False
audit_failure = ""
try:
    lunch = flow_named("Lunch")["flow_id"]
    original_records = repo.list_flows()
    database = "\n".join(repo.conn.iterdump())
    status = open_filter("Status")
    click(status.checkbuttons["Complete"])
    click(status.checkbuttons["Pending"])
    popup = status.popup
    undo()
    assert status.popup is popup and status.get() == ("Complete",)
    assert not status._choice_variables["Pending"].get()
    redo()
    assert status.get() == ("Complete", "Pending") and status._choice_variables["Pending"].get()
    ok("Ctrl+Z/Ctrl+Y inside a checkbox dropdown restores selections and checkmarks without closing it")
    key_escape()

    category = open_filter("Category")
    click(category.checkbuttons["Boat"])
    tag = open_filter("Tag")
    click(tag.checkbuttons["Boat"])
    assert set(app.ledger_rows) == {flow_named("Repair")["flow_id"]}
    undo()
    assert tag.get() == () and category.get() == ("Boat",)
    undo()
    assert category.get() == () and status.get() == ("Complete", "Pending")
    redo()
    redo()
    assert category.get() == ("Boat",) and tag.get() == ("Boat",)
    assert "\n".join(repo.conn.iterdump()) == database
    ok("Category, status, and tag changes share one undo order and never write transaction data")
    key_escape()

    category = open_filter("Category")
    replace(category.search_entry, "din")
    assert list(category.checkbuttons) == ["Dining"]
    undo()
    assert category.search_entry.get() == "" and category.get() == ("Boat",)
    redo()
    assert category.search_entry.get() == "din" and category.get() == ("Boat",)
    key_escape()
    undo()
    category = open_filter("Category")
    assert category.search_entry.get() == ""
    ok("Choice-search typing is undoable as one field, including after closing and reopening its dropdown")
    key_escape()
    clear_filters()

    replace(app.search_entry, "Lunch")
    assert set(app.ledger_rows) == {lunch}
    undo()
    assert app.search_var.get() == "" and len(app.ledger_rows) == 3
    redo()
    assert app.search_var.get() == "Lunch" and set(app.ledger_rows) == {lunch}
    undo()
    ok("Ledger search reverses a whole typing gesture and immediately restores the visible rows")

    row(app.ledger, lunch, "name")
    replace(top_entry("amount"), ".75")
    key("Return")
    category = open_filter("Category")
    click(category.checkbuttons["Boat"])
    assert lunch not in app.ledger_rows
    undo()
    assert category.get() == () and repo.get_flow(lunch)["amount_cents"] == 75
    assert app.editing_id == lunch and app.form_vars["amount"].get() == "0.75"
    key_escape()
    undo()
    assert repo.get_flow(lunch)["amount_cents"] == 2000
    redo()
    redo()
    assert category.get() == ("Boat",) and repo.get_flow(lunch)["amount_cents"] == 75
    undo()
    undo()
    ok("An edit followed by a filter undoes the filter first, then the edit, including hidden rows and redo")

    click(app.new_flow_button)
    replace(top_entry("from_account"), "Checking")
    key("Return")
    replace(top_entry("to_account"), "Draft shop")
    key("Return")
    replace(top_entry("amount"), ".50")
    key("Return")
    full = app._flow_snapshot()
    click(app.new_flow_button)
    undo()
    assert app._flow_snapshot() == full
    undo()
    assert app.form_vars["amount"].get() == ""
    undo()
    assert app.form_vars["to_account"].get() == ""
    undo()
    assert app.form_vars["from_account"].get() == ""
    for _ in range(4):
        redo()
    assert app._flow_snapshot() == app._clean_flow
    assert repo.list_flows() == original_records
    ok("New draft fields undo separately; New Flow clearing is reversible and never adds records")

    replace(top_entry("name"), "Discarded draft")
    replace(top_entry("amount"), ".50")
    draft = app._flow_snapshot()
    click_xy(app.ledger.winfo_rootx()+50, app.ledger.winfo_rooty()+app.ledger.winfo_height()-20)
    assert app.editing_id is None and app.form_vars["name"].get() == ""
    undo()
    assert app._flow_snapshot() == draft
    row(app.ledger, lunch, "name")
    undo()
    assert app.editing_id is None and app._flow_snapshot() == draft
    redo()
    assert app.editing_id == lunch
    click(app.new_flow_button)
    ok("Blank-space and row-selection draft discards can be undone without saving a new flow")

    replace(top_entry("name"), "Page draft")
    draft = app._flow_snapshot()
    click(app.nav_buttons["accounts"])
    undo()
    assert app.current_view == "accounts" and app._flow_snapshot() == draft
    click(app.nav_buttons["flows"])
    assert app._flow_snapshot() == draft
    click(app.new_flow_button)
    ok("Changing pages still discards a new draft; undo restores its fields without changing pages")

    click(app.notes_button)
    type_text("Draft notes")
    peer = app.expanded_notes_text
    assert peer.get("1.0", "end-1c") == "Draft notes"
    undo()
    assert peer.winfo_exists() and peer.get("1.0", "end-1c") == ""
    redo()
    assert peer.get("1.0", "end-1c") == "Draft notes"
    click_text("Close", app.notes_dialog)
    click(app.new_flow_button)
    ok("New-flow Notes undo/redo stays synchronized between the inline field and open expanded editor")

    row(app.ledger, lunch, "name")
    fields = app._flow_snapshot()
    click_text("Repeat")
    undo()
    assert app.editing_id == lunch and app.ledger.selection() == (lunch,)
    redo()
    assert app.editing_id is None and app._flow_snapshot() == fields
    assert app.form_vars["date"].get() == "2026-10-02 12:34:56"
    click(app.new_flow_button)
    ok("Repeat can be undone and redone, copying every field including the full date/time")

    order = displayed_columns(app.ledger)
    sort = app.sort_column, app.sort_reverse
    drag_header(app.ledger, "amount_cents", "date")
    moved = displayed_columns(app.ledger)
    assert moved[0] == "amount_cents", moved
    assert (app.sort_column, app.sort_reverse) == sort
    undo()
    assert displayed_columns(app.ledger) == order
    redo()
    assert displayed_columns(app.ledger) == moved
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Columns.png")
    ok("Drag a ledger header across several columns; one undo restores it and dragging does not sort")

    double_cell(app.ledger, lunch, "amount_cents")
    editor = app.ledger_editor
    assert editor.field == "amount_cents"
    replace(editor.input, ".05")
    key("Tab")
    assert repo.get_flow(lunch)["amount_cents"] == 5
    assert editor.field == "date"
    key("Return")
    undo()
    assert repo.get_flow(lunch)["amount_cents"] == 2000
    assert displayed_columns(app.ledger) == moved
    ok("Reordered cells still edit the correct field; Tab follows the visible column order")

    # A click still sorts rather than starting a drag.
    click_xy(*header_point(app.ledger, "name"))
    assert app.sort_column == "name"
    undo()
    assert (app.sort_column, app.sort_reverse) == sort
    redo()
    assert app.sort_column == "name"
    ok("Ordinary header clicks still sort, and sorting is undoable")

    widths = tuple((c, app.ledger.column(c, "width")) for c in app.ledger["columns"])
    first_field = displayed_columns(app.ledger)[0]
    bx, _, width, _ = app.ledger.bbox(app.ledger.get_children()[0], first_field)
    start = app.ledger.winfo_rootx() + bx + width, app.ledger.winfo_rooty() + 12
    pointer_drag(start, (start[0] + 35, start[1]))
    resized = tuple((c, app.ledger.column(c, "width")) for c in app.ledger["columns"])
    assert widths != resized
    undo()
    assert tuple((c, app.ledger.column(c, "width")) for c in app.ledger["columns"]) == widths
    redo()
    assert tuple((c, app.ledger.column(c, "width")) for c in app.ledger["columns"]) == resized
    ok("Separator resizing still works after reordering and has one undo/redo step")

    click(app.nav_buttons["accounts"])
    account_order = displayed_columns(app.accounts_tree)
    drag_header(app.accounts_tree, "anchor", "balance")
    account_moved = displayed_columns(app.accounts_tree)
    assert account_moved[:3] == ("name", "anchor", "balance"), account_moved
    undo()
    assert displayed_columns(app.accounts_tree) == account_order
    redo()
    assert displayed_columns(app.accounts_tree) == account_moved
    double_cell(app.accounts_tree, "Checking", "anchor")
    assert app.account_cell_editor.field == "anchor"
    key("Tab")
    assert app.account_cell_editor.field == "balance"
    replace(app.account_cell_editor.input, "1000.50")
    key("Return")
    assert repo.get_account("Checking")["opening_cents"] == 100050
    undo()
    assert repo.get_account("Checking")["opening_cents"] == 100000
    assert displayed_columns(app.accounts_tree) == account_moved
    ok("Accounts headers reorder too; date/balance editing and visual Tab order survive, calculated cells stay read-only")

    click(app.account_new_button)
    replace(app.account_name_entry, "Unadded cash")
    replace(app.account_widgets["opening"], ".50")
    click(app.account_new_button)
    undo()
    assert app.account_vars["name"].get() == "Unadded cash" and app.account_vars["opening"].get() == ".50"
    undo()
    assert app.account_vars["opening"].get() == "" and app.account_vars["name"].get() == "Unadded cash"
    click(app.account_new_button)
    ok("Unadded account fields and New Account clearing use the same session undo")

    click(app.nav_buttons["flows"])
    click_text("Dates…")
    dialog = app.grab_current()
    entries = [w for w in widgets(dialog) if w.winfo_class() == "TEntry"]
    replace(entries[0], "2026-10-03")
    replace(entries[1], "2026-10-03")
    click_text("Apply", dialog)
    assert set(app.ledger_rows) == {flow_named("Repair")["flow_id"]}
    undo()
    assert (app.filter_start.get(), app.filter_end.get()) == ("", "") and len(app.ledger_rows) == 3
    redo()
    assert app.filter_start.get() == app.filter_end.get() == "2026-10-03"
    undo()
    ok("Applying a ledger date range is one undo action for both bounds")

    click(app.nav_buttons["charts"])
    controls = app._graph_controls()
    click_text("All")
    assert len(app.chart_accounts.curselection()) == 3
    undo()
    assert app._graph_controls() == controls
    redo()
    assert len(app.chart_accounts.curselection()) == 3
    click_text("None")
    assert not app.chart_accounts.curselection()
    undo()
    assert len(app.chart_accounts.curselection()) == 3
    ok("Graph All/None account selections undo and redo correctly")

    click(app.chart_basis_widget)
    key("Home")
    key("Down")
    key("Return")
    assert app.chart_basis.get() == "Including Pending"
    undo()
    assert app.chart_basis.get() == "Confirmed"
    redo()
    assert app.chart_basis.get() == "Including Pending"
    click_text("Combine selected accounts")
    assert app.chart_combined.get()
    undo()
    assert not app.chart_combined.get()
    redo()
    assert app.chart_combined.get()
    undo()
    ok("Balance basis and combined-balance checkbox share the same undo order as other controls")

    render_before = app._chart_rendered_controls
    click(app.chart_start_picker.button)
    click(app.chart_start_picker.day_buttons[5])
    assert app.chart_start.get() == "2026-10-05"
    click_text("Update Graph")
    render_after = app._chart_rendered_controls
    undo()
    assert app.chart_start.get() == "2026-10-05" and app._chart_rendered_controls == render_before
    undo()
    assert app.chart_start.get() == "2026-10-01"
    redo()
    redo()
    assert app._chart_rendered_controls == render_after
    ok("Calendar day selection and Update Graph undo independently while restoring the displayed plot")

    legend, line = next(iter(app.chart_line_map.items()))
    bounds = legend.get_window_extent(app.canvas.get_renderer())
    canvas = app.canvas.get_tk_widget()
    click_xy(canvas.winfo_rootx() + (bounds.x0 + bounds.x1)/2,
             canvas.winfo_rooty() + canvas.winfo_height() - (bounds.y0 + bounds.y1)/2)
    assert not next(iter(app.chart_series)).get_visible()
    undo()
    assert next(iter(app.chart_series)).get_visible()
    redo()
    assert not next(iter(app.chart_series)).get_visible()
    undo()
    ok("Clicking a legend line to hide/show it can be undone and redone")

    original_limits = app.axes.get_xlim(), app.axes.get_ylim()
    click_text("Zoom", app.chart_toolbar)
    area = app.axes.bbox
    pointer_drag((canvas.winfo_rootx()+area.x0+area.width*.25,
                  canvas.winfo_rooty()+canvas.winfo_height()-area.y0-area.height*.25),
                 (canvas.winfo_rootx()+area.x0+area.width*.7,
                  canvas.winfo_rooty()+canvas.winfo_height()-area.y0-area.height*.7))
    zoomed = app.axes.get_xlim(), app.axes.get_ylim()
    assert zoomed != original_limits
    click_text("Zoom", app.chart_toolbar)
    undo()
    assert (app.axes.get_xlim(), app.axes.get_ylim()) == original_limits
    redo()
    assert (app.axes.get_xlim(), app.axes.get_ylim()) == zoomed
    ok("Physical plot zoom is one undo/redo action restoring exact axis limits")

    assert repo.list_flows() == original_records
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("All checks leave saved records intact; custom popups map in place with no callback exceptions")
    success = True
    print(f"PASS {len(results)} view interaction checks; no callback exceptions.", flush=True)
except BaseException:
    audit_failure = traceback.format_exc()
    raise
finally:
    payload = {"success": success, "input": "X11 XTest mouse and keyboard events",
               "passed": results, "errors": errors, "failure": audit_failure,
               "maps": maps, "database": str(path)}
    report.write_text(json.dumps(payload, indent=2))
    report.with_suffix(".txt").write_text(
        "Views and column dragging: native X11 XTest interaction audit\n"
        + ("PASS" if success else "FAIL") + f": {len(results)} workflows; {len(errors)} callback exceptions.\n\n"
        + "\n".join("PASS " + label for label in results) + "\n" + audit_failure)
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
