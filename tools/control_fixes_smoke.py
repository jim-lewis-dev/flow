"""Native XTest audit of row deletion, balance carets and ledger filters.

Only fixture construction and read-only assertions use application objects.
Every click, edit, filter selection and history command uses X11 mouse/keyboard
input against a fresh disposable database, never the user's records.
"""
from pathlib import Path


def seed_controls(repo):
    for name, units, opening in (
        ("Checking", "USD", "120.25"), ("Diner", "USD", "0"),
        ("Shop", "USD", "0"), ("Warehouse", "kg", "10.125"),
        ("Workshop", "kg", "0"), ("Source", "mW", "100"),
        ("Source", "MW", "100"), ("Meter", "mW", "0"),
        ("Meter", "MW", "0"),
    ):
        repo.save_account(name, opening, "2026-10-01", units=units)
    for name, source, destination, amount, units, status, category, tags in (
        ("Alpha", "Checking", "Diner", "12.3456789", "USD", "Complete", "Dining", ["Trip", "Work", "Boat"]),
        ("Beta", "Warehouse", "Workshop", ".125", "kg", "Pending", "Materials", ["Trip"]),
        ("Gamma", "Source", "Meter", "1.25", "mW", "Complete", "Power", ["Work"]),
        ("Delta", "Source", "Meter", "2.5", "MW", "Planned", "Power", ["Boat"]),
        ("Epsilon", "Checking", "Shop", "3", "USD", "Void", "Other", ["Boat"]),
    ):
        repo.save_flow(dict(date="2026-10-02 12:34:56", name=name,
            from_account=source, to_account=destination, amount=amount, units=units,
            status=status, category=category, tags=tags, notes="Exact original notes"))


shared = Path(__file__).with_name("ui_smoke.py").read_text().split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_controls(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


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


def open_filter(key):
    choice = app.ledger_filters[key]
    click(choice.button)
    assert choice.popup is not None, key
    assert choice._tooltip_popup is None, key
    return choice


def names():
    return {flow["name"] for flow in app.ledger_rows.values()}


def tooltip(choice, expected):
    move(choice.button.winfo_rootx() + choice.button.winfo_width() / 2,
         choice.button.winfo_rooty() + choice.button.winfo_height() / 2)
    pump(.65)
    assert choice._tooltip_popup is not None and choice._tooltip_popup.winfo_viewable()
    actual = choice._tooltip_label.cget("text")
    assert actual.splitlines() == list(expected), (expected, actual)
    return actual


failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    original = repo.list_flows()
    alpha = flow_named("Alpha")
    assert set(app.ledger_filters) == {"From", "To", "Status", "Category", "Tag", "Units"}
    labels = {"From": "From", "To": "To", "Status": "Status", "Category": "Categories", "Tag": "Tags", "Units": "Units"}
    for filter_key, label in labels.items():
        assert app.ledger_filters[filter_key].button.cget("text") == f"{label}: All ▾"
    ok("All six filter headings use From, To, Status, Categories, Tags and Units: All")

    row(app.ledger, alpha["flow_id"], "name")
    before_maps = len(maps)
    key("Delete")
    assert repo.get_flow(alpha["flow_id"]) is None
    assert not app.ledger.exists(alpha["flow_id"]) and app.editing_id is None
    assert not app.ledger.selection() and app.form_vars["name"].get() == ""
    assert len(maps) == before_maps, maps[before_maps:]
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    assert app.ledger.exists(alpha["flow_id"])
    chord("Control_L", "y")
    assert repo.get_flow(alpha["flow_id"]) is None and not app.ledger.exists(alpha["flow_id"])
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    ok("Delete removes the selected flow immediately without a dialog; Ctrl+Z/Y restores/removes every exact field including units and tags")

    row(app.ledger, alpha["flow_id"], "name")
    key("Delete")
    assert repo.get_flow(alpha["flow_id"]) is None
    click(app.nav_buttons["accounts"])
    assert not app.accounts_tree.exists(account_iid("Diner", "USD"))
    assert repo.get_account("Diner", "USD")["opening_amount"] == "0"
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    assert app.accounts_tree.exists(account_iid("Diner", "USD"))
    ok("Deleting the final account/unit reference hides that Accounts row, retaining its starting balance; Undo restores both the flow and row")

    checking = account_iid("Checking", "USD")
    row(app.accounts_tree, checking, "name")
    opening = app.account_widgets["opening"]
    click(app.account_widgets["date"])
    click_xy(opening.winfo_rootx() + 4, opening.winfo_rooty() + opening.winfo_height() / 2)
    assert opening.index("insert") == opening.index("end"), (opening.index("insert"), opening.get())
    type_text("7")
    key("Return")
    assert repo.get_account("Checking", "USD")["opening_amount"] == "120.257"
    chord("Control_L", "z")
    assert repo.get_account("Checking", "USD")["opening_amount"] == "120.25"
    chord("Control_L", "y")
    assert repo.get_account("Checking", "USD")["opening_amount"] == "120.257"
    chord("Control_L", "z")
    ok("The upper Starting Balance caret begins at the right edge even when first clicked on the left; appending an exact digit auto-saves and supports Undo/Redo")

    double_cell(app.accounts_tree, checking, "balance")
    cell = app.account_cell_editor.input
    assert cell is not None and cell.index("insert") == cell.index("end")
    key("Escape")
    click(app.account_widgets["date"])
    click_xy(opening.winfo_rootx() + 4, opening.winfo_rooty() + opening.winfo_height() / 2)
    assert opening.index("insert") == opening.index("end")
    pump(.55)  # Deliberate single click, not Tk's double-click word selection.
    click_xy(opening.winfo_rootx() + 4, opening.winfo_rooty() + opening.winfo_height() / 2)
    assert opening.index("insert") == 0, (opening.index("insert"), opening.get())
    ok("Inline Starting Balance editing also begins at the right; a second focused click still permits deliberate cursor placement")

    click(app.nav_buttons["flows"])
    row(app.ledger, alpha["flow_id"], "name")
    click(entry("name"))
    key("Home")
    key("Delete")
    assert repo.get_flow(alpha["flow_id"]) is not None
    assert app.form_vars["name"].get() == "lpha"
    key("Return")
    assert repo.get_flow(alpha["flow_id"])["name"] == "lpha"
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    ok("Delete inside the upper text field removes text instead of a selected flow, with ordinary autosave and Undo")

    double_cell(app.ledger, alpha["flow_id"], "name")
    key("Home")
    key("Delete")
    assert app.form_vars["name"].get() == "lpha" and repo.get_flow(alpha["flow_id"]) is not None
    key("Return")
    assert repo.get_flow(alpha["flow_id"])["name"] == "lpha"
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    ok("Delete inside an inline ledger text editor edits its characters instead of deleting the flow")

    row(app.ledger, alpha["flow_id"], "name")
    before_maps = len(maps)
    click_text("Delete")
    assert repo.get_flow(alpha["flow_id"]) is None and len(maps) == before_maps
    chord("Control_L", "z")
    assert repo.get_flow(alpha["flow_id"]) == alpha
    ok("The visible Delete action uses the same immediate undoable deletion as the keyboard")

    click(app.new_flow_button)
    click_xy(app.ledger.winfo_rootx() + 12,
             app.ledger.winfo_rooty() + app.ledger.winfo_height() - 12)
    assert not app.ledger.selection()
    key("Delete")
    assert repo.list_flows() == original and app.editing_id is None
    ok("Delete with no selected row leaves records unchanged and entry in clean New Flow")

    units = open_filter("Units")
    click(units.checkbuttons["mW"])
    assert names() == {"Gamma"} and units.get() == ("mW",)
    replace(units.search_entry, "m")
    assert set(units.checkbuttons) == {"mW", "MW"}
    click(units.checkbuttons["MW"])
    assert names() == {"Gamma", "Delta"} and units.get() == ("mW", "MW")
    popup = units.popup
    chord("Control_L", "z")
    assert units.popup is popup and units.get() == ("mW",) and names() == {"Gamma"}
    assert not units._choice_variables["MW"].get()
    chord("Control_L", "y")
    assert units.popup is popup and units.get() == ("mW", "MW") and names() == {"Gamma", "Delta"}
    key("Escape")
    tooltip(units, ("mW", "MW"))
    ok("Units checkboxes distinguish mW from MW, keep multiple selected values through search, and remain synchronized during popup Undo/Redo")

    move(app.ledger.winfo_rootx() + 15, app.ledger.winfo_rooty() + 60)
    assert units._tooltip_popup is None
    units = open_filter("Units")
    replace(units.search_entry, "m")
    key("Tab")  # Finish one search action before clearing it in another.
    replace(units.search_entry, "")
    chord("Control_L", "z")
    assert units.search_entry.get() == "m" and units.get() == ("mW", "MW")
    chord("Control_L", "y")
    assert units.search_entry.get() == "" and units.get() == ("mW", "MW")
    click(units.all_button)
    assert names() == {f["name"] for f in original} and units.get() == ()
    chord("Control_L", "z")
    assert units.get() == ("mW", "MW") and names() == {"Gamma", "Delta"}
    chord("Control_L", "y")
    assert units.get() == () and names() == {f["name"] for f in original}
    key("Escape")
    ok("Units choice-search and All participate in session Undo/Redo; leaving the filter button dismisses its tooltip")

    selections = {"From": ("Checking", "Warehouse"), "To": ("Diner", "Shop"),
                  "Status": ("Complete", "Pending"), "Category": ("Dining", "Materials"),
                  "Tag": ("Trip", "Work", "Boat"), "Units": ("USD", "kg")}
    for filter_key, selected in selections.items():
        choice = open_filter(filter_key)
        for value in selected:
            click(choice.checkbuttons[value])
        key("Escape")
        tooltip(choice, selected)
        click(choice.button)
        assert choice.popup is not None and choice._tooltip_popup is None
        click(choice.all_button)
        key("Escape")
        assert choice.get() == ()
    ok("Hovering every filter shows every selected value, including all three tags; opening a dropdown hides the tooltip and All clears the restriction")

    units = open_filter("Units")
    click(units.checkbuttons["USD"])
    click(units.checkbuttons["kg"])
    key("Escape")
    status = open_filter("Status")
    click(status.checkbuttons["Complete"])
    click(status.checkbuttons["Pending"])
    key("Escape")
    tag = open_filter("Tag")
    click(tag.checkbuttons["Trip"])
    key("Escape")
    assert names() == {"Alpha", "Beta"}
    assert app.report_summary.get() == "Complete · 12.3456789 USD", app.report_summary.get()
    assert {flow["units"] for flow in app.ledger_rows.values()} == {"USD", "kg"}
    ok("Units combines correctly with statuses and tags; both selected units remain visible and Complete totals carry their unit")

    tooltip(tag, ("Trip",))
    click(app.nav_buttons["accounts"])
    assert tag._tooltip_popup is None
    click(app.nav_buttons["flows"])
    assert units.get() == ("USD", "kg") and names() == {"Alpha", "Beta"}
    ok("Changing pages cancels active filter tooltips while retaining Units and all other filter selections")

    from PIL import ImageGrab
    docs = Path(__file__).resolve().parents[1] / "docs"
    for width, height in ((1920, 1200), (1280, 800), (1120, 700)):
        app.geometry(f"{width}x{height}+0+0")
        pump(.25)
        controls = [app.search_entry, *(choice.button for choice in app.ledger_filters.values()), find_text("Dates…")]
        for left, right in zip(controls, controls[1:]):
            assert right.winfo_rootx() >= left.winfo_rootx() + left.winfo_width()
            assert right.winfo_rootx() + right.winfo_width() <= width
        assert app.ledger.xview() == (0.0, 1.0)
        ImageGrab.grab(bbox=(0, 0, width, height)).save(docs / f"Flow_Control_Fixes_{width}.png")
    ok("Search, all six filters, Dates and ten ledger columns fit at 1920, 1280 and 1120 pixels without overlap")

    app.geometry("1920x1200+0+0")
    pump(.25)
    tooltip(tag, ("Trip",))
    ImageGrab.grab().save(docs / "Flow_Control_Fixes_Tooltip.png")
    move(app.ledger.winfo_rootx() + 15, app.ledger.winfo_rooty() + 60)
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    assert repo.list_flows() == original
    ok("Dropdowns and tooltips first map at final positions, without origin flashes; all original flows are exact after the audit")
    success = True
    print(f"PASS {len(results)} control-fix interaction checks; no callback exceptions.", flush=True)
except Exception:
    failure = traceback.format_exc()
    raise
finally:
    outcome = dict(success=success, input="X11 XTest mouse and keyboard events", passed=results,
                   errors=errors, failure=failure, maps=maps, database=str(path))
    report.write_text(json.dumps(outcome, indent=2))
    text_report = report.with_suffix(".txt")
    text_report.write_text("Flow control-fix native interaction audit\n"
        + f"Result: {'PASS' if success else 'FAIL'} ({len(results)} checks)\n"
        + "Input: physical X11 XTest mouse and keyboard events\n"
        + "\n".join(f"PASS {item}" for item in results)
        + ("\n" + failure if failure else "\nNo callback exceptions.\n"))
    print("Report:", report, flush=True)
    app.history.close()
    app.destroy()
    repo.close()
