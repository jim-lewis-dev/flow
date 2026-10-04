"""Ledger cell editing audit driven by native X11 mouse/keyboard input.

Only fixture setup and read-only assertions access application objects. Run on
an isolated 1920x1200 Xvfb display. No GUI callback is invoked by this audit.
"""
from pathlib import Path


def seed_ledger_data(repo):
    repo.save_account("Checking", 100000, "2026-10-01")
    repo.save_account("Savings", 50000, "2026-10-01")
    repo.save_account("AmEx", -25000, "2026-10-01")
    for date, source, destination, amount, name, category, status, notes, tags in (
        ("2026-10-03 12:30:15", "Checking", "Corner Shop", "12.34", "Lunch", "Dining", "Complete", "First saved note", ["Trip"]),
        ("2026-10-02 09:10:11", "AmEx", "Boat Store", "56.78", "Boat supplies", "Boat", "Pending", "Second saved note", ["Weekend"]),
    ):
        repo.save_flow(dict(date=date, from_account=source, to_account=destination,
                            amount=amount, name=name, category=category,
                            status=status, notes=notes, tags=tags))
    for number in range(45):
        repo.save_flow(dict(date=f"2026-09-{number % 28 + 1:02d}",
                            from_account="Checking", to_account="Corner Shop",
                            amount="5", name=f"Earlier flow {number:02d}",
                            category="Earlier", status="Complete"))


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
    assert app._flow_snapshot() == app._clean_flow
    assert app.focus_get() is app.form_widgets["date"]
    for field in ("from_account", "to_account", "amount", "name", "tags", "time"):
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


def answer_dialog(label):
    """Answer the native message box through an actual XTest pointer click."""
    def send():
        def children(path):
            yield path
            for child in app.tk.splitlist(app.tk.call("winfo", "children", path)):
                yield from children(child)
        grabs = app.tk.splitlist(app.tk.call("grab", "current"))
        grab = str(grabs[0]) if grabs else ""
        if not grab:
            app.after(80, send)
            return
        for path in children(grab):
            try:
                caption = str(app.tk.call(path, "cget", "-text"))
                if caption.casefold() != label.casefold():
                    continue
                if not int(app.tk.call("winfo", "viewable", path)):
                    continue
                px = int(app.tk.call("winfo", "rootx", path)) + int(app.tk.call("winfo", "width", path)) // 2
                py = int(app.tk.call("winfo", "rooty", path)) + int(app.tk.call("winfo", "height", path)) // 2
                xt.XTestFakeMotionEvent(display, 0, px, py, 0)
                xt.XTestFakeButtonEvent(display, 1, True, 0)
                xt.XTestFakeButtonEvent(display, 1, False, 0)
                x.XSync(display, False)
                return
            except tk.TclError:
                pass
        raise AssertionError("Could not find native dialog button " + label)
    app.after(180, send)


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


audit_failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    first = flow_named("Lunch")["flow_id"]
    second = flow_named("Boat supplies")["flow_id"]

    replace_all(edit_cell(first, "amount_cents"), "23.45")
    assert app.form_vars["amount"].get() == "23.45"
    assert preview(first, "amount_cents") == "$23.45"
    assert saved(first)["amount_cents"] == 1234
    key("Return")
    editor_closed()
    assert saved(first)["amount_cents"] == 1234
    click(app.save_button)
    assert saved(first)["amount_cents"] == 2345
    fresh_form()
    ok("Native double-click edits an amount in both views; Enter keeps a draft and Save persists it")

    row(app.ledger, first)
    replace_all(top_entry("name"), "Lunch with friends")
    assert preview(first, "name") == "Lunch with friends"
    replace_all(edit_cell(first, "name"), "Friday lunch")
    assert app.form_vars["name"].get() == "Friday lunch"
    assert saved(first)["name"] == "Lunch"
    chord("Control_L", "s")
    assert saved(first)["name"] == "Friday lunch"
    fresh_form()
    ok("Top form edits preview immediately; cell edits share the draft; Ctrl+S saves and clears both editors")

    replace_all(edit_cell(first, "date"), "2026-10-04 14:15:16")
    key("Return")
    assert app.form_vars["date"].get() == "2026-10-04"
    assert app.form_vars["time"].get() == "14:15:16"
    assert saved(first)["date"] == "2026-10-03 12:30:15"
    click(app.save_button)
    assert saved(first)["date"] == "2026-10-04 14:15:16"
    fresh_form()
    ok("Editing a full timestamp in the Date cell updates the optional time and preserves seconds on Save")

    replace_all(edit_cell(first, "from_account"), "Sav")
    combo = app.ledger_editor.widget
    assert isinstance(combo, FilterCombo) and combo.matches == ["Savings"]
    select_suggestion(combo, "Savings")
    assert app.form_vars["from_account"].get() == "Savings"
    replace_all(edit_cell(first, "to_account"), "Harbor Shop")
    combo = app.ledger_editor.widget
    key("Return")
    assert app.form_vars["to_account"].get() == "Harbor Shop"
    assert saved(first)["to_account"] == "Corner Shop"
    click(app.save_button)
    assert saved(first)["from_account"] == "Savings" and saved(first)["to_account"] == "Harbor Shop"
    assert "Harbor Shop" in app.form_widgets["to_account"].values
    fresh_form()
    ok("Account cells filter existing suggestions and accept new names without saving the flow early")

    replace_all(edit_cell(first, "category"), "Boat maintenance")
    key("Return")
    replace_all(edit_cell(first, "tags"), 'Trip, "Boat, trailer"')
    key("Return")
    assert app.form_vars["category"].get() == "Boat maintenance"
    assert app.form_vars["tags"].get() == 'Trip, "Boat, trailer"'
    assert saved(first)["category"] == "Dining"
    click(app.save_button)
    assert saved(first)["category"] == "Boat maintenance"
    assert saved(first)["tags"] == ["Trip", "Boat, trailer"]
    fresh_form()
    ok("Category and quoted comma-containing tags edit through ledger suggestions and save exactly")

    status = edit_cell(first, "status")
    assert str(status.cget("state")) == "readonly"
    assert tuple(status.cget("values")) == ("Complete", "Pending", "Planned", "Void")
    click(status)
    key("Escape")
    assert app.form_vars["status"].get() == "Complete"
    assert app.ledger_editor.widget is status
    click(status)
    key("Down")
    key("Return")
    assert app.form_vars["status"].get() == "Pending"
    assert saved(first)["status"] == "Complete"
    click(app.save_button)
    assert saved(first)["status"] == "Pending"
    fresh_form()
    ok("Status cell offers the four fixed choices and changes the saved status only on Save")

    notes = "First ledger line\nSecond ledger line\nThird ledger line"
    replace_all(edit_cell(first, "notes"), notes)
    assert app.notes_text.get("1.0", "end-1c") == notes
    assert saved(first)["notes"] == "First saved note"
    click(app.notes_button)
    assert app.expanded_notes_text.get("1.0", "end-1c") == notes
    click(app.expanded_notes_text)
    chord("Control_L", "End")
    type_text("\nExpanded fourth line")
    notes += "\nExpanded fourth line"
    key("Escape")
    assert app.notes_dialog is None
    assert app.notes_text.get("1.0", "end-1c") == notes
    click(app.cancel_button)
    assert saved(first)["notes"] == "First saved note"
    fresh_form()
    ok("Ledger, top, and expanded Notes share multiline text; Cancel discards the draft without data loss")

    replace_all(edit_cell(first, "notes"), notes)
    click(app.save_button)
    assert saved(first)["notes"] == notes
    fresh_form()
    row(app.ledger, first)
    assert app.notes_text.get("1.0", "end-1c") == notes
    for direction in ("Up", "Up", "Down", "Down"):
        click(app.notes_text)
        key(direction)
        assert app.notes_text.bbox("insert") is not None
    click(app.cancel_button)
    ok("Multiline ledger Notes survive Save and row reload; compact Notes keeps the caret visible")

    replace_all(edit_cell(first, "name"), "Draft to cancel")
    assert saved(first)["name"] == "Friday lunch"
    answer_dialog("Cancel")
    double_cell(second, "amount_cents")
    assert app.editing_id == first and app.form_vars["name"].get() == "Draft to cancel"
    assert app.ledger.selection() == (first,)
    assert app.ledger_editor.widget is None or app.ledger_editor.flow_id == first
    answer_dialog("No")
    double_cell(second, "amount_cents")
    assert app.editing_id == second
    assert saved(first)["name"] == "Friday lunch"
    assert app.ledger_editor.widget is not None and app.ledger_editor.flow_id == second
    replace_all(app.ledger_editor.input, "67.89")
    answer_dialog("Yes")
    double_cell(first, "name")
    assert saved(second)["amount_cents"] == 6789
    assert app.editing_id == first and app.ledger_editor.flow_id == first
    click(app.cancel_button)
    fresh_form()
    ok("Double-clicking another row respects Cancel, No, and Yes without opening an editor for the wrong record")

    replace_all(edit_cell(first, "amount_cents"), "24.68")
    replace_all(search_entry(), "Boat supplies")
    assert first not in app.ledger_rows and app.editing_id == first
    assert app.form_vars["amount"].get() == "24.68"
    editor_closed()
    click(app.save_button)
    assert saved(first)["amount_cents"] == 2468
    fresh_form()
    replace_all(search_entry(), "")
    ok("Filtering away an edited row closes its overlay but keeps and saves the correct draft")

    replace_all(edit_cell(first, "name"), "Sort draft")
    header("name")
    assert app.editing_id == first and app.form_vars["name"].get() == "Sort draft"
    aligned_or_closed(first, "name")
    assert preview(first, "name") == "Sort draft"
    click(app.cancel_button)
    replace_all(edit_cell(first, "name"), "Scroll draft")
    before_scroll = app.ledger.yview()
    wheel_up(app.ledger, 18)
    assert app.ledger.yview() != before_scroll
    aligned_or_closed(first, "name")
    assert app.form_vars["name"].get() == "Scroll draft"
    click(app.cancel_button)
    ok("Sorting and native wheel scrolling preserve the draft without leaving a cell overlay on another row")

    replace_all(edit_cell(first, "name"), "Resize draft")
    app.geometry("1280x800+0+0")
    pump(.25)
    aligned_or_closed(first, "name")
    assert app.form_vars["name"].get() == "Resize draft"
    assert app.ledger.xview() == (0.0, 1.0)
    click(app.cancel_button)
    fresh_form()
    ok("Window resizing keeps the editor aligned or closes it and keeps all ledger columns visible")

    app.geometry("1920x1200+0+0")
    pump(.25)
    # A screenshot of a real cell edit, with both views showing the same draft.
    replace_all(edit_cell(first, "name"), "Lunch with friends")
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Ledger_Edit.png")
    click(app.cancel_button)
    fresh_form()
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("Save and Cancel return to a clean New Flow; every custom popup first maps at its final position")
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
