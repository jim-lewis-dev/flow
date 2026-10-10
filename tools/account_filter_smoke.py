"""Native XTest audit of endpoint checkboxes, editing, history, and layout."""
from pathlib import Path

fixtures = {}


def seed_accounts(repo):
    repo.save_account("Unused", '1000')
    for day, source, destination, status, name, category, tag, amount in (
        (1, "Checking", "Diner", "Complete", "Lunch", "Dining", "Trip", "12"),
        (2, "AmEx", "Shop", "Pending", "Groceries", "Groceries", "Work", "25"),
        (3, "Shop", "Checking", "Complete", "Refund", "Refund", "Trip", "5"),
        (4, "Checking Reserve", "Diner", "Canceled", "Correction", "Other", "Work", "4"),
        (5, "Cash", "Shop", "Planned", "Supplies", "Groceries", "Trip", "8"),
    ):
        fixtures[name] = repo.save_flow(dict(date=f"2026-10-0{day} 12:34:56", from_account=source,
            to_account=destination, amount=amount, units="USD", status=status, name=name, category=category,
            tags=[tag], notes="Checking mentioned in notes"))


shared = Path(__file__).with_name("ui_smoke.py").read_text()
shared = shared.split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)", "repo=Repository(path);seed_accounts(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def open_filter(key):
    choice = app.ledger_filters[key]
    click(choice.button)
    assert choice.popup is not None
    return choice


def names():
    return {flow["name"] for flow in app.ledger_rows.values()}


def enter(field, value):
    widget = app.form_widgets[field]
    replace(widget.entry if isinstance(widget, FilterCombo) else widget, value)
    if isinstance(widget, FilterCombo):
        key("Return")


try:
    app.attributes("-fullscreen", False)
    app.geometry("1920x1200+0+0")
    pump()
    original = repo.list_flows()
    database = "\n".join(repo.conn.iterdump())
    source = open_filter("From")
    click(source.checkbuttons["Checking"])
    assert names() == {"Lunch"}
    click(source.checkbuttons["AmEx"])
    assert names() == {"Lunch", "Groceries"}
    ok("From checkboxes match whole source names and allow multiple selections")

    replace(source.search_entry, "cash")
    assert list(source.checkbuttons) == ["Cash"]
    assert set(source.get()) == {"Checking", "AmEx"}
    click(source.checkbuttons["Cash"])
    assert names() == {"Lunch", "Groceries", "Supplies"}
    popup = source.popup
    chord("Control_L", "z")
    assert source.popup is popup and set(source.get()) == {"Checking", "AmEx"}
    assert not source._choice_variables["Cash"].get()
    chord("Control_L", "y")
    assert source.popup is popup and set(source.get()) == {"Checking", "AmEx", "Cash"}
    ok("Searching accounts retains hidden checks; checkbox undo/redo keeps the popup synchronized")

    replace(source.search_entry, "")
    chord("Control_L", "z")
    assert source.search_entry.get() == "cash"
    chord("Control_L", "y")
    assert source.search_entry.get() == ""
    assert set(source.get()) == {"Checking", "AmEx", "Cash"}
    key("Escape")
    ok("From choice-search undo/redo restores the query without changing account selections")

    destination = open_filter("To")
    click(destination.checkbuttons["Diner"])
    click(destination.checkbuttons["Shop"])
    assert names() == {"Lunch", "Groceries", "Supplies"}
    click(destination.checkbuttons["Diner"])
    assert names() == {"Groceries", "Supplies"}
    popup = destination.popup
    chord("Control_L", "z")
    assert destination.popup is popup and set(destination.get()) == {"Diner", "Shop"}
    chord("Control_L", "y")
    assert destination.get() == ("Shop",)
    chord("Control_L", "z")
    key("Escape")
    ok("To supports multiple choices and popup undo/redo; From and To combine correctly")

    status = open_filter("Status")
    click(status.checkbuttons["Complete"])
    category = open_filter("Category")
    assert status.popup is None
    click(category.checkbuttons["Dining"])
    tag = open_filter("Tag")
    click(tag.checkbuttons["Trip"])
    key("Escape")
    replace(app.search_entry, "lunch")
    assert names() == {"Lunch"}
    assert [value.get() for value in app.metric_vars] == ["1", "0", "1"]
    assert app.report_summary.get() == "Complete · 12 USD"
    source = open_filter("From")
    assert "Checking Reserve" in source.checkbuttons and "Shop" in source.checkbuttons
    assert "Unused" not in source.checkbuttons
    key("Escape")
    assert "\n".join(repo.conn.iterdump()) == database
    ok("Five filters combine with Search and totals; choices include Void/refunds and ignore other restrictions")

    replace(app.search_entry, "")
    for label in ("From", "To", "Status", "Category", "Tag"):
        choice = open_filter(label)
        click(choice.all_button)
        assert choice.get() == ()
        key("Escape")
    assert names() == set(fixtures)
    ok("All clears each of the six restrictions without changing saved records")

    destination = open_filter("To")
    click(destination.checkbuttons["Checking"])
    source = open_filter("From")
    click(source.checkbuttons["Shop"])
    key("Escape")
    assert names() == {"Refund"}
    ok("Refunds are filterable in their actual From and To directions")

    flow = fixtures["Refund"]
    row(app.ledger, flow["flow_id"])
    enter("from_account", "Vendor")
    assert repo.get_flow(flow["flow_id"])["from_account"] == "Vendor"
    assert names() == set() and app.editing_id == flow["flow_id"]
    assert source.get() == ("Shop",) and "Shop" not in source.values
    assert "Vendor" in source.values
    chord("Control_L", "z")
    assert repo.get_flow(flow["flow_id"]) == flow and names() == {"Refund"}
    chord("Control_L", "y")
    assert repo.get_flow(flow["flow_id"])["from_account"] == "Vendor" and names() == set()
    chord("Control_L", "z")
    ok("Editing a source refreshes choices and hides the matching row; record undo/redo restores both correctly")

    click(app.new_flow_button)
    enter("from_account", "Checking")
    enter("to_account", "New Shop")
    enter("amount", ".50")
    enter("name", "Small purchase")
    click(app.save_button)
    assert len(repo.list_flows()) == 6
    assert "New Shop" in destination.values
    assert source.get() == ("Shop",) and destination.get() == ("Checking",)
    assert names() == {"Refund"}
    assert app.editing_id is None and app._flow_snapshot() == app._clean_flow
    chord("Control_L", "z")
    assert len(repo.list_flows()) == 5 and "New Shop" not in destination.values
    chord("Control_L", "y")
    assert len(repo.list_flows()) == 6 and "New Shop" in destination.values
    chord("Control_L", "z")
    assert repo.list_flows() == original
    ok("Add resets entry and retains endpoint filters; addition undo/redo refreshes saved account choices")

    from PIL import ImageGrab
    docs = Path(__file__).resolve().parents[1] / "docs"
    for width, height in ((1920, 1200), (1920, 1080), (1280, 800), (1120, 700)):
        app.geometry(f"{width}x{height}+0+0")
        pump()
        controls = [app.search_entry, *(choice.button for choice in app.ledger_filters.values()), find_text("Dates…")]
        for left, right in zip(controls, controls[1:]):
            assert right.winfo_rootx() >= left.winfo_rootx() + left.winfo_width()
            assert right.winfo_rootx() + right.winfo_width() <= width
        assert 100 <= app.search_entry.winfo_width() <= 240
        assert app.ledger.xview() == (0.0, 1.0)
        for label in ("From", "To"):
            choice = open_filter(label)
            assert choice.popup.winfo_rootx() + choice.popup.winfo_width() <= width
            key("Escape")
        if width == 1280:
            ImageGrab.grab(bbox=(0, 0, width, height)).save(docs / "Flow_Account_Filters_1280.png")
    ok("Compact Search, all six filters, Dates and ledger columns fit widths 1920, 1280 and 1120")

    app.geometry("1920x1200+0+0")
    pump()
    for label in ("From", "To"):
        choice = open_filter(label)
        click(choice.all_button)
        key("Escape")
    source = open_filter("From")
    click(source.checkbuttons["Checking"])
    click(source.checkbuttons["AmEx"])
    key("Escape")
    destination = open_filter("To")
    click(destination.checkbuttons["Diner"])
    click(destination.checkbuttons["Shop"])
    ImageGrab.grab().save(docs / "Flow_Account_Filters.png")
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    assert repo.list_flows() == original
    ok("Account dropdowns first map at their final position; native edits leave original records intact")
    success = True
    print(f"PASS {len(results)} account-filter interaction checks; no callback exceptions.", flush=True)
finally:
    report.write_text(json.dumps({"success": success, "input": "X11 XTest mouse and keyboard events",
        "passed": results, "errors": errors, "maps": maps, "database": str(path)}, indent=2))
    print("Report:", report, flush=True)
    app.history.close()
    app.destroy()
    repo.close()
