"""Focused multiselect, autosave, and Add audit using native X11 input.

Only setup and read-only outcome checks access application internals. Run on an
isolated Xvfb display; see tools/README.txt.
"""
from pathlib import Path

shared = Path(__file__).with_name("ui_smoke.py").read_text()
exec(compile(shared.split("try:\n    exec(Path(__file__)", 1)[0],
             str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def open_filter(label):
    widget = app.ledger_filters[label]
    click(widget.button)
    assert widget.popup is not None
    return widget


def search_box():
    return next(widget for widget in widgets()
                if widget.winfo_class() == "TEntry"
                and str(widget.cget("textvariable")) == str(app.search_var))


def entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def enter(field, value):
    replace(entry(field), value)
    if isinstance(app.form_widgets[field], FilterCombo):
        key("Return")


def clean_new():
    assert app.editing_id is None
    assert app.ledger.selection() == () and app.ledger.focus() == ""
    assert app._flow_snapshot() == app._clean_flow
    assert app.focus_get() is app.form_widgets["date"]
    for field in ("from_account", "to_account", "amount", "name", "tags"):
        assert app.form_vars[field].get() == "", field
    assert app.form_vars["category"].get() == "Uncategorized"
    assert app.form_vars["status"].get() == "Complete"
    assert app.notes_text.get("1.0", "end-1c") == ""
    assert not popups()
    assert all(widget.popup is None for widget in app.ledger_filters.values())


try:
    fixtures = []
    for category, status, tag, name, amount in (
        ("Dining", "Complete", "Audit trip", "Lunch", "12"),
        ("Boat", "Pending", "Audit repair", "Boat repair", "25"),
        ("Boat", "Complete", "Audit trip two", "Supplies", "30"),
        ("Boat", "Planned", "Audit trip", "Next repair", "40"),
        ("Other", "Complete", "Audit trip", "Dining and Boat in title", "50"),
    ):
        fixtures.append(repo.save_flow(dict(date="2026-10-02 12:30:15", from_account="Checking",
            to_account="Corner Shop", amount=amount, status=status, category=category,
            tags=[tag], name=name, notes="Stored notes")))
    app.refresh_all()
    pump()
    database = "\n".join(repo.conn.iterdump())
    category = open_filter("Category")
    replace(category.search_entry, "din")
    assert list(category.checkbuttons) == ["Dining"]
    click(category.checkbuttons["Dining"])
    replace(category.search_entry, "boa")
    assert list(category.checkbuttons) == ["Boat"]
    click(category.checkbuttons["Boat"])
    assert set(category.get()) == {"Dining", "Boat"}
    assert "\n".join(repo.conn.iterdump()) == database
    key("Escape")
    assert category.popup is None and app.focus_get() is category.button
    ok("Select multiple categories through filtered checkbox lists; hidden selections survive search")

    status = open_filter("Status")
    click(status.checkbuttons["Complete"])
    click(status.checkbuttons["Pending"])
    tag = open_filter("Tag")
    assert status.popup is None
    replace(tag.search_entry, "Audit trip")
    click(tag.checkbuttons["Audit trip"])
    replace(tag.search_entry, "Audit repair")
    click(tag.checkbuttons["Audit repair"])
    assert set(tag.get()) == {"Audit trip", "Audit repair"}
    assert set(app.ledger_rows) == {fixtures[0]["flow_id"], fixtures[1]["flow_id"]}
    assert [value.get() for value in app.metric_vars] == ["1", "1", "2"]
    assert app.report_summary.get() == "Complete · $12.00 flow volume"
    click(search_box())
    assert tag.popup is None
    ok("Multiple statuses, categories, and exact tags combine correctly; metrics follow visible rows")

    type_text("repair")
    assert set(app.ledger_rows) == {fixtures[1]["flow_id"]}
    assert [value.get() for value in app.metric_vars] == ["0", "1", "1"]
    replace(search_box(), "")
    assert len(app.ledger_rows) == 2
    ok("Ledger text search combines with the selected groups and updates as typed")

    row(app.ledger, fixtures[0]["flow_id"])
    assert app.editing_id == fixtures[0]["flow_id"]
    assert not any(widget.winfo_viewable() and widget.winfo_class() in ("TButton", "Button")
                   and str(widget.cget("text")) == "Edit" for widget in widgets())
    enter("amount", "23.45")
    replace(app.notes_text, "Updated notes")
    draft = app._flow_snapshot()
    status = open_filter("Status")
    click(status.checkbuttons["Complete"])
    assert set(app.ledger_rows) == {fixtures[1]["flow_id"]}
    assert app._flow_snapshot() == draft and app.editing_id == fixtures[0]["flow_id"]
    assert repo.get_flow(fixtures[0]["flow_id"])["amount_cents"] == 2345
    assert repo.get_flow(fixtures[0]["flow_id"])["notes"] == "Updated notes"
    assert app.save_button.instate(["disabled"])
    # The selected flow stays editable even when filters hide its ledger row.
    enter("amount", "24.56")
    key("Return")
    assert repo.get_flow(fixtures[0]["flow_id"])["amount_cents"] == 2456
    assert fixtures[0]["flow_id"] not in app.ledger_rows
    click(app.new_flow_button)
    clean_new()
    assert status.get() == ("Pending",)
    assert set(category.get()) == {"Dining", "Boat"}
    assert set(tag.get()) == {"Audit trip", "Audit repair"}
    assert set(app.ledger_rows) == {fixtures[1]["flow_id"]}
    ok("Finished fields autosave even on a filtered-out flow; New Flow clears entry and retains filters")

    enter("from_account", "Checking")
    enter("to_account", "Corner Shop")
    enter("amount", "5")
    enter("name", "New filtered flow")
    click(app.save_button)
    clean_new()
    saved = flow_named("New filtered flow")
    assert saved["amount_cents"] == 500 and saved["status"] == "Complete"
    assert saved["flow_id"] not in app.ledger_rows
    assert set(app.ledger_rows) == {fixtures[1]["flow_id"]}
    ok("Add Flow stores a new flow hidden by current filters and immediately clears entry")

    category = open_filter("Category")
    click(entry("from_account"))
    assert category.popup is None and popups()
    tag = open_filter("Tag")
    assert not popups() and tag.popup is not None
    key("Escape")
    assert tag.popup is None
    status = open_filter("Status")
    click(search_box())
    assert status.popup is None
    ok("Entry suggestions and checkbox filters are mutually exclusive; Escape and outside click dismiss")

    for label in ("Status", "Category", "Tag"):
        choice = open_filter(label)
        click(choice.all_button)
        assert choice.get() == ()
        key("Escape")
    assert len(app.ledger_rows) == len(repo.list_flows())
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("All clears each restriction; every observed custom dropdown first maps at its final position")

    app.geometry("1280x800+0+0")
    pump()
    for widget in (search_box(), find_text("Dates…"),
                   *(choice.button for choice in app.ledger_filters.values())):
        assert widget.winfo_rootx() >= app.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    assert search_box().winfo_width() >= 100
    assert app.ledger.xview() == (0.0, 1.0)
    category = open_filter("Category")
    assert category.popup.winfo_rootx() + category.popup.winfo_width() <= 1280
    key("Escape")
    ok("Search, all five filters, dates, and ledger columns fit a 1280-pixel-wide window")

    from PIL import ImageGrab
    app.geometry("1920x1200+0+0")
    pump()
    docs = Path(__file__).resolve().parents[1] / "docs"
    category = open_filter("Category")
    click(category.checkbuttons["Boat"])
    click(category.checkbuttons["Dining"])
    ImageGrab.grab().save(docs / "Flow_Filters.png")
    success = True
    print(f"PASS {len(results)} filter interaction checks; no callback exceptions.", flush=True)
finally:
    report.write_text(json.dumps({"success": success, "input": "X11 XTest mouse and keyboard events",
        "passed": results, "errors": errors, "maps": maps, "database": str(path)}, indent=2))
    print("Report:", report, flush=True)
    app.history.close()
    app.destroy()
    repo.close()
