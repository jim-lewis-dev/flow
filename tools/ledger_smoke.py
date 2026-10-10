"""Ledger cell editing audit driven by native X11 mouse/keyboard input.

Only fixture setup and read-only assertions access application objects. Run on
an isolated 1920x1200 Xvfb display. No GUI callback is invoked by this audit.
"""
from pathlib import Path


def seed_ledger_data(repo):
    repo.save_account("Checking", '1000', "2026-10-01")
    repo.save_account("Savings", '500', "2026-10-01")
    repo.save_account("AmEx", '-250', "2026-10-01")
    for date, source, destination, amount, name, category, status, notes, tags in (
        ("2026-10-03 12:30:15", "Checking", "Corner Shop", "12.34", "Lunch", "Dining", "Complete", "First saved note", ["Trip"]),
        ("2026-10-02 09:10:11", "AmEx", "Boat Store", "56.78", "Boat supplies", "Boat", "Pending", "Second saved note", ["Weekend"]),
    ):
        repo.save_flow(dict(date=date, from_account=source, to_account=destination,
                            amount=amount, units="USD", name=name, category=category,
                            status=status, notes=notes, tags=tags))
    for number in range(45):
        repo.save_flow(dict(date=f"2026-09-{number % 28 + 1:02d}",
                            from_account="Checking", to_account="Corner Shop",
                            amount="5", units="USD", name=f"Earlier flow {number:02d}",
                            category="Earlier", status="Complete"))
    repo.save_flow(dict(date="2026-09-01", from_account="Checking",
                        to_account="Savings", amount="1", units="USD", status="Void"))


shared = Path(__file__).with_name("ui_smoke.py").read_text()
shared = shared.split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_ledger_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def top_entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def replace_all(widget, text):
    click(widget)
    chord("Control_L", "a")
    key("BackSpace")
    type_text(text)


def edit_cell(flow_id, field):
    double_cell(flow_id, field)
    controller = app.ledger_editor
    assert controller.widget is not None, (flow_id, field, "No cell editor")
    assert controller.flow_id == flow_id and controller.field == field
    assert controller.input.winfo_viewable()
    return controller.input


def double_cell(flow_id, field):
    # Queue both physical clicks before processing Tk. The shared row helper
    # pumps between clicks, which would let a modal answer land between them.
    # Separate gestures so an earlier pair at the same screen position cannot
    # make Tk classify this pair as the third/fourth clicks of that gesture.
    pump(.55)
    app.ledger.see(flow_id)
    pump(.05)
    bounds = app.ledger.bbox(flow_id, field)
    assert bounds
    x0, y0, width, height = bounds
    visible_height = min(height, app.ledger.winfo_height() - y0 - 2)
    assert visible_height > 0, (bounds, app.ledger.winfo_height())
    move(app.ledger.winfo_rootx() + x0 + width / 2,
         app.ledger.winfo_rooty() + y0 + visible_height / 2)
    for _ in range(2):
        xt.XTestFakeButtonEvent(display, 1, True, 0)
        xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.25)


def editor_closed():
    assert app.ledger_editor.widget is None
    assert not popups()


def fresh_form():
    editor_closed()
    assert app.editing_id is None
    assert app.ledger.selection() == ()
    assert app._flow_snapshot() == app._clean_flow, (app._flow_snapshot(), app._clean_flow)
    for field in ("from_account", "to_account", "amount", "name", "tags"):
        assert app.form_vars[field].get() == "", field
    assert app.notes_text.get("1.0", "end-1c") == ""


def saved(flow_id):
    return repo.get_flow(flow_id)


def preview(flow_id, field):
    return app.ledger.set(flow_id, field)


def select_suggestion(combo, value):
    index = combo.matches.index(value)
    bounds = combo.listbox.bbox(index)
    assert bounds
    _, y0, _, height = bounds
    click_xy(combo.listbox.winfo_rootx() + 15,
             combo.listbox.winfo_rooty() + y0 + height / 2)


def header(field):
    bounds = app.ledger.bbox(app.ledger.get_children()[0], field)
    assert bounds
    x0, _, width, _ = bounds
    click_xy(app.ledger.winfo_rootx() + x0 + width / 2,
             app.ledger.winfo_rooty() + 10)


def search_entry():
    return next(widget for widget in widgets()
                if widget.winfo_class() == "TEntry"
                and str(widget.cget("textvariable")) == str(app.search_var))


def wheel_up(widget, count=8):
    move(widget.winfo_rootx() + widget.winfo_width() - 35,
         widget.winfo_rooty() + widget.winfo_height() / 2)
    for _ in range(count):
        xt.XTestFakeButtonEvent(display, 4, True, 0)
        xt.XTestFakeButtonEvent(display, 4, False, 0)
        x.XSync(display, False)
        pump(.06)


def aligned_or_closed(flow_id, field):
    controller = app.ledger_editor
    if controller.widget is None:
        assert not popups()
        return
    bounds = app.ledger.bbox(flow_id, field)
    assert bounds, "Editor remains after its row disappeared"
    x0, y0, width, height = bounds
    widget = controller.widget
    assert abs(widget.winfo_rootx() - app.ledger.winfo_rootx() - x0) <= 3
    assert abs(widget.winfo_rooty() - app.ledger.winfo_rooty() - y0) <= 3
    assert widget.winfo_rootx() + widget.winfo_width() <= app.ledger.winfo_rootx() + app.ledger.winfo_width() + 3


def no_prompt():
    assert not app.tk.splitlist(app.tk.call("grab", "current"))
    assert not errors


def check_copy(flow):
    expected = {
        "from_account": flow["from_account"], "to_account": flow["to_account"],
        "amount": flow["amount"], "units": flow["units"], "status": flow["status"],
        "name": flow["name"], "category": flow["category"],
    }
    for field, value in expected.items():
        assert app.form_vars[field].get() == value, (field, app.form_vars[field].get(), value)
    assert app.form_vars["date"].get() == flow["date"]
    assert app.notes_text.get("1.0", "end-1c") == flow["notes"]
    assert app.form_vars["tags"].get() == ", ".join(flow["tags"])


audit_failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    first = flow_named("Lunch")["flow_id"]
    second = flow_named("Boat supplies")["flow_id"]

    click(top_entry("from_account"))
    assert app.form_widgets["from_account"].popup is not None
    click(top_entry("status"))
    assert "popdown" in str(app.tk.call("focus"))
    assert not popups()
    key("Down")
    key("Return")
    assert app.form_vars["status"].get() == "Pending"
    click(app.new_flow_button)
    fresh_form()
    ok("Moving from suggestions to the native Status list closes the old popup without focus errors")

    replace_all(edit_cell(first, "amount"), "23.45")
    assert app.form_vars["amount"].get() == "23.45"
    assert preview(first, "amount") == "23.45"
    assert saved(first)["amount"] == '12.34'
    key("Return")
    editor_closed()
    assert saved(first)["amount"] == '23.45'
    assert app.editing_id == first and app.ledger.selection() == (first,)
    no_prompt()
    chord("Control_L", "z")
    assert saved(first)["amount"] == '12.34'
    chord("Control_L", "y")
    assert saved(first)["amount"] == '23.45'
    ok("Enter saves an existing ledger amount once; Ctrl+Z and Ctrl+Y reverse and restore the field")

    row(app.ledger, first)
    replace_all(top_entry("name"), "Friday lunch")
    assert saved(first)["name"] == "Lunch"
    click(top_entry("amount"))
    assert saved(first)["name"] == "Friday lunch"
    replace_all(top_entry("amount"), "24.68")
    key("Tab")
    assert saved(first)["amount"] == '24.68'
    chord("Control_L", "z")
    assert saved(first)["amount"] == '23.45' and saved(first)["name"] == "Friday lunch"
    chord("Control_L", "z")
    assert saved(first)["name"] == "Lunch"
    chord("Control_L", "y")
    chord("Control_L", "y")
    assert saved(first)["name"] == "Friday lunch" and saved(first)["amount"] == '24.68'
    ok("Top fields save on click-away and Tab; each completed field has one undo step")

    row(app.ledger, first)
    replace_all(top_entry("name"), "Switching rows")
    row(app.ledger, second)
    assert saved(first)["name"] == "Switching rows" and app.editing_id == second
    assert app.form_vars["name"].get() == "Boat supplies"
    no_prompt()
    chord("Control_L", "z")
    assert saved(first)["name"] == "Friday lunch"
    chord("Control_L", "y")
    assert saved(first)["name"] == "Switching rows"
    ok("Selecting another row saves the completed existing field without a prompt or duplicate history entry")

    original = saved(second)
    count_before = len(repo.list_flows())
    row(app.ledger, second)
    click_text("Repeat")
    assert app.editing_id is None and app.ledger.selection() == ()
    check_copy(original)
    replace_all(top_entry("name"), "Discarded repeat")
    row(app.ledger, second)
    assert app.editing_id == second and len(repo.list_flows()) == count_before
    assert saved(second) == original
    no_prompt()
    ok("Repeat copies date, seconds, status, notes, tags and all other fields; selecting a row silently discards its draft")

    click_text("Repeat")
    check_copy(original)
    replace_all(top_entry("name"), "Repeated boat purchase")
    assert str(app.save_button.cget("text")).startswith("Add Flow")
    click(app.save_button)
    repeated = flow_named("Repeated boat purchase")
    assert {k: v for k, v in repeated.items() if k not in ("flow_id", "name")} == {
        k: v for k, v in original.items() if k not in ("flow_id", "name")}
    assert repeated["flow_id"] != second and len(repo.list_flows()) == count_before + 1
    fresh_form()
    chord("Control_L", "z")
    assert len(repo.list_flows()) == count_before
    assert not any(f["flow_id"] == repeated["flow_id"] for f in repo.list_flows())
    chord("Control_L", "y")
    assert saved(repeated["flow_id"]) == repeated
    ok("Add Flow inserts a repeated draft once and resets the form; undo and redo preserve the new record's identity")

    click(app.new_flow_button)
    replace_all(top_entry("from_account"), "Unsaved account")
    key("Return")
    replace_all(top_entry("amount"), "123")
    click(top_entry("name"))
    assert app.form_vars["from_account"].get() == "Unsaved account"
    row(app.ledger, first)
    assert app.editing_id == first and len(repo.list_flows()) == count_before + 1
    assert "Unsaved account" not in [a["name"] for a in repo.list_accounts()]
    no_prompt()
    ok("Moving between new-flow fields keeps the draft; selecting an existing row forgets it without creating records")

    replace_all(edit_cell(first, "date"), "2026-10-04 14:15:16")
    key("Return")
    assert app.form_vars["date"].get() == "2026-10-04 14:15:16"
    assert saved(first)["date"] == "2026-10-04 14:15:16"
    editor_closed()
    ok("A full timestamp in the Date cell saves to the second without a separate Save button")

    replace_all(edit_cell(first, "from_account"), "Sav")
    combo = app.ledger_editor.widget
    assert isinstance(combo, FilterCombo) and combo.matches == ["Savings"]
    select_suggestion(combo, "Savings")
    assert app.form_vars["from_account"].get() == "Savings"
    replace_all(edit_cell(first, "to_account"), "Harbor Shop")
    key("Return")
    assert saved(first)["from_account"] == "Savings" and saved(first)["to_account"] == "Harbor Shop"
    assert "Harbor Shop" in app.form_widgets["to_account"].values
    editor_closed()
    chord("Control_L", "z")
    assert saved(first)["to_account"] == "Corner Shop"
    chord("Control_L", "y")
    assert saved(first)["to_account"] == "Harbor Shop"
    assert not popups()
    ok("Account suggestions and new names save cleanly; undo/redo never reopens a dropdown")

    replace_all(edit_cell(first, "category"), "Boat maintenance")
    key("Return")
    replace_all(edit_cell(first, "tags"), 'Trip, "Boat, trailer"')
    key("Return")
    assert saved(first)["category"] == "Boat maintenance"
    assert saved(first)["tags"] == ["Trip", "Boat, trailer"]
    editor_closed()
    ok("Category and comma-containing tags save through the same ledger editing gesture")

    status = edit_cell(first, "status")
    assert str(status.cget("state")) == "readonly"
    assert tuple(status.cget("values")) == ("Complete", "Pending", "Planned", "Void")
    click(status)
    key("Escape")
    assert saved(first)["status"] == "Complete"
    click(status)
    key("Down")
    key("Return")
    click(top_entry("amount"))
    assert saved(first)["status"] == "Pending"
    assert not popups()
    ok("The native Status list saves a selected choice and does not leave a stale cell editor")

    notes = "First ledger line\nSecond ledger line\nThird ledger line"
    replace_all(edit_cell(first, "notes"), notes)
    assert app.notes_text.get("1.0", "end-1c") == notes
    assert saved(first)["notes"] == "First saved note"
    chord("Control_L", "Return")
    assert saved(first)["notes"] == notes
    editor_closed()
    click(app.notes_button)
    assert app.expanded_notes_text.get("1.0", "end-1c") == notes
    click(app.expanded_notes_text)
    chord("Control_L", "End")
    type_text("\nExpanded fourth line")
    expanded_notes = notes + "\nExpanded fourth line"
    key("Escape")
    assert app.notes_dialog is None
    assert saved(first)["notes"] == expanded_notes
    chord("Control_L", "z")
    assert saved(first)["notes"] == notes
    chord("Control_L", "y")
    assert saved(first)["notes"] == expanded_notes
    row(app.ledger, first)
    for direction in ("Up", "Up", "Down", "Down"):
        click(app.notes_text)
        key(direction)
        assert app.notes_text.bbox("insert") is not None
    ok("Ledger and expanded Notes save multiline text; closing the expansion saves once and undo restores the prior note")

    previous = saved(first)["amount"]
    replace_all(edit_cell(first, "amount"), "oops")
    key("Return")
    assert saved(first)["amount"] == previous
    assert app.form_feedback.get(), "Invalid edit needs visible feedback"
    assert app.editing_id == first
    key("Escape")
    assert saved(first)["amount"] == previous
    assert app.form_vars["amount"].get() == previous
    no_prompt()
    ok("Invalid amounts remain unsaved with visible feedback; Escape restores the saved value without a confirmation")

    replace_all(edit_cell(first, "name"), "Completed once")
    key("Return")
    edit_cell(first, "name")
    key("Return")
    chord("Control_L", "z")
    assert saved(first)["name"] == "Switching rows"
    chord("Control_L", "y")
    assert saved(first)["name"] == "Completed once"
    row(app.ledger, first)
    replace_all(top_entry("name"), "Not yet completed")
    chord("Control_L", "z")
    assert saved(first)["name"] == "Completed once"
    assert app.form_vars["name"].get() == "Completed once"
    chord("Control_L", "z")
    assert saved(first)["name"] == "Switching rows"
    chord("Control_L", "y")
    assert saved(first)["name"] == "Completed once"
    ok("No-op completion adds no history; Ctrl+Z first abandons an unfinished field, then undoes the saved edit")

    replace_all(edit_cell(first, "amount"), "27.89")
    replace_all(search_entry(), "Boat supplies")
    assert saved(first)["amount"] == '27.89'
    assert first not in app.ledger_rows
    editor_closed()
    replace_all(search_entry(), "")
    ok("Filtering away an edited row saves the correct record and closes its cell overlay")

    replace_all(edit_cell(first, "name"), "Sorted edit")
    header("name")
    assert saved(first)["name"] == "Sorted edit"
    aligned_or_closed(first, "name")
    replace_all(edit_cell(first, "name"), "Scrolled edit")
    before_scroll = app.ledger.yview()
    wheel_up(app.ledger, 18)
    assert app.ledger.yview() != before_scroll
    aligned_or_closed(first, "name")
    assert saved(first)["name"] == "Scrolled edit"
    ok("Sorting and native wheel scrolling save edits without leaving a cell editor on the wrong row")

    replace_all(edit_cell(first, "name"), "Resize edit")
    app.geometry("1280x800+0+0")
    pump(.25)
    aligned_or_closed(first, "name")
    assert app.form_vars["name"].get() == "Resize edit"
    assert app.ledger.xview() == (0.0, 1.0)
    click(app.new_flow_button)
    assert saved(first)["name"] == "Resize edit"
    fresh_form()
    ok("Window resizing keeps the editor aligned or closes it; New Flow completes an existing edit and clears the form")

    app.geometry("1920x1200+0+0")
    pump(.25)
    row(app.ledger, first)
    previous_status = saved(first)["status"]
    click_text("Void")
    assert saved(first)["status"] == "Void"
    fresh_form()
    no_prompt()
    chord("Control_L", "z")
    assert saved(first)["status"] == previous_status
    chord("Control_L", "y")
    assert saved(first)["status"] == "Void"
    chord("Control_L", "z")
    ok("Void needs no confirmation and can be undone and redone for the current session")

    replace_all(edit_cell(first, "name"), "Lunch with friends")
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Ledger_Edit.png")
    key("Return")
    click(app.new_flow_button)
    fresh_form()
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("All custom popups first map at their final position; field completion leaves no reopened suggestions")
    success = True
    print(f"PASS {len(results)} ledger interaction checks; no callback exceptions.", flush=True)
except BaseException:
    audit_failure = traceback.format_exc()
    raise
finally:
    payload = {"success": success, "input": "X11 XTest mouse and keyboard events",
               "passed": results, "errors": errors, "failure": audit_failure,
               "maps": maps, "database": str(path)}
    report.write_text(json.dumps(payload, indent=2))
    report.with_suffix(".txt").write_text(
        "Ledger editing: native X11 XTest interaction audit\n"
        + ("PASS" if success else "FAIL") + f": {len(results)} workflows; {len(errors)} callback exceptions.\n\n"
        + "\n".join("PASS " + label for label in results) + "\n"
        + ("\n".join(errors) if errors else "") + audit_failure)
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
