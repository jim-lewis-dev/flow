"""Accounts editing audit driven by native X11 mouse and keyboard input.

Only isolated fixture setup, window sizing, and read-only assertions access
application objects. Every edit and dialog answer uses physical XTest events.
"""
from pathlib import Path


def seed_account_data(repo):
    for name, cents, date in (
        ("AmEx", -25000, "2026-10-01"),
        ("Checking", 100000, "2026-10-01 09:10:11"),
        ("Savings", 50000, "2026-10-01"),
        ("Unknown", None, "2026-10-01"),
        ("Zero", 0, "2026-10-01"),
    ):
        repo.save_account(name, cents, date)
    for index in range(45):
        repo.save_account(f"Z reserve {index:02d}", index * 100, "2026-10-01")
    repo.save_flow(dict(date="2026-10-02 12:00:00", from_account="Checking",
                        to_account="AmEx", amount="12.34", status="Complete",
                        name="Card payment"))
    repo.save_flow(dict(date="2026-10-03 12:00:00", from_account="Checking",
                        to_account="Savings", amount="23.45", status="Pending",
                        name="Pending transfer"))


shared = Path(__file__).with_name("ui_smoke.py").read_text()
shared = shared.split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_account_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def replace_all(widget, text):
    click(widget)
    chord("Control_L", "a")
    key("BackSpace")
    type_text(text)


def double_cell(name, column):
    # Queue the two physical clicks before Tk handles either one. Pumping
    # between them would let a row-change dialog interrupt the gesture.
    pump(.55)
    app.accounts_tree.see(name)
    pump(.05)
    bounds = app.accounts_tree.bbox(name, column)
    assert bounds, (name, column)
    x0, y0, width, height = bounds
    visible_height = min(height, app.accounts_tree.winfo_height() - y0 - 2)
    assert visible_height > 0, (bounds, app.accounts_tree.winfo_height())
    move(app.accounts_tree.winfo_rootx() + x0 + width / 2,
         app.accounts_tree.winfo_rooty() + y0 + visible_height / 2)
    for _ in range(2):
        xt.XTestFakeButtonEvent(display, 1, True, 0)
        xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.25)


def edit_cell(name, column):
    double_cell(name, column)
    controller = app.account_cell_editor
    assert controller.widget is not None, (name, column, "No cell editor")
    assert controller.flow_id == name and controller.field == column
    assert controller.input.winfo_viewable()
    return controller.input


def saved(name):
    return repo.get_account(name)


def preview(name, column):
    return app.accounts_tree.set(name, column)


def editor_closed():
    assert app.account_cell_editor.widget is None
    assert not popups()


def fresh_form(check_focus=True):
    editor_closed()
    assert app.account_editing is None
    assert app.accounts_tree.selection() == ()
    assert app.accounts_tree.focus() == ""
    assert app._account_snapshot() == app._clean_account
    assert app.account_vars["name"].get() == ""
    assert app.account_vars["opening"].get() == ""
    assert app.account_vars["time"].get() == ""
    assert str(app.account_name_entry.cget("state")) == "normal"
    if check_focus:
        assert app.focus_get() is app.account_name_entry


def aligned_or_closed(name, column):
    controller = app.account_cell_editor
    if controller.widget is None:
        assert not popups()
        return
    bounds = app.accounts_tree.bbox(name, column)
    assert bounds, "Account editor remains after its row disappeared"
    x0, y0, width, height = bounds
    widget = controller.widget
    assert abs(widget.winfo_rootx() - app.accounts_tree.winfo_rootx() - x0) <= 3
    assert abs(widget.winfo_rooty() - app.accounts_tree.winfo_rooty() - y0) <= 3
    assert widget.winfo_rootx() + widget.winfo_width() <= app.accounts_tree.winfo_rootx() + app.accounts_tree.winfo_width() + 3


def wheel_down(widget, count=20):
    move(widget.winfo_rootx() + widget.winfo_width() - 35,
         widget.winfo_rooty() + widget.winfo_height() / 2)
    for _ in range(count):
        xt.XTestFakeButtonEvent(display, 5, True, 0)
        xt.XTestFakeButtonEvent(display, 5, False, 0)
        x.XSync(display, False)
        pump(.06)


def no_prompt():
    assert not app.tk.splitlist(app.tk.call("grab", "current"))
    assert not errors


audit_failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    click_text("Accounts")
    original = saved("Checking")
    confirmed = preview("Checking", "confirmed")
    pending = preview("Checking", "pending")
    assert confirmed == "$987.66" and pending == "$964.21"
    replace_all(edit_cell("Checking", "balance"), "1234.56")
    assert app.account_vars["opening"].get() == "1234.56"
    assert preview("Checking", "balance") == "$1,234.56"
    assert saved("Checking") == original
    assert preview("Checking", "confirmed") == confirmed
    key("Return")
    editor_closed()
    assert saved("Checking")["opening_cents"] == 123456
    assert preview("Checking", "confirmed") == "$1,222.22"
    assert preview("Checking", "pending") == "$1,198.77"
    assert app.account_editing == "Checking"
    no_prompt()
    chord("Control_L", "z")
    assert saved("Checking") == original
    assert preview("Checking", "confirmed") == confirmed
    chord("Control_L", "y")
    assert saved("Checking")["opening_cents"] == 123456
    ok("Enter saves an inline starting balance and recalculates totals; Ctrl+Z/Ctrl+Y restore both values and totals")

    row(app.accounts_tree, "Checking")
    replace_all(app.account_widgets["opening"], "1357.90")
    assert saved("Checking")["opening_cents"] == 123456
    click(app.account_widgets["date"])
    assert saved("Checking")["opening_cents"] == 135790
    replace_all(edit_cell("Checking", "balance"), "1400.01")
    key("Tab")
    assert app.account_cell_editor.field == "anchor"
    assert saved("Checking")["opening_cents"] == 140001
    key("Return")
    chord("Control_L", "z")
    assert saved("Checking")["opening_cents"] == 135790
    chord("Control_L", "z")
    assert saved("Checking")["opening_cents"] == 123456
    chord("Control_L", "y")
    chord("Control_L", "y")
    assert saved("Checking")["opening_cents"] == 140001
    ok("Account top fields save on click-away and inline Tab saves once before moving to Starting Date")

    for name, text, cents, display_text in (
        ("AmEx", "-300.25", -30025, "$-300.25"),
        ("Zero", "0", 0, "$0.00"),
        ("Unknown", "", None, "Unknown"),
    ):
        replace_all(edit_cell(name, "balance"), text)
        assert preview(name, "balance") == display_text
        key("Return")
        assert saved(name)["opening_cents"] == cents
        editor_closed()
    replace_all(edit_cell("Zero", "balance"), "")
    key("Return")
    assert saved("Zero")["opening_cents"] is None
    replace_all(edit_cell("Zero", "balance"), "0")
    key("Return")
    assert saved("Zero")["opening_cents"] == 0
    for column in ("name", "confirmed", "pending"):
        double_cell("Checking", column)
        editor_closed()
        assert app.account_editing == "Checking"
        assert str(app.account_name_entry.cget("state")) == "disabled"
        assert saved("Checking")["name"] == "Checking"
    click(app.account_new_button)
    fresh_form()
    ok("Negative, zero, and Unknown balances auto-save; account identity and calculated balances remain read-only")

    replace_all(edit_cell("Checking", "anchor"), "2026-10-01")
    key("Return")
    assert app.account_vars["time"].get() == "09:10:11"
    assert saved("Checking")["opening_at"] == "2026-10-01 09:10:11"
    replace_all(edit_cell("Checking", "anchor"), "2026-10-01 08:09:10")
    key("Return")
    assert app.account_vars["date"].get() == "2026-10-01"
    assert app.account_vars["time"].get() == "08:09:10"
    assert saved("Checking")["opening_at"] == "2026-10-01 08:09:10"
    replace_all(app.account_widgets["date"], "2026-10-02")
    key("Return")
    assert saved("Checking")["opening_at"] == "2026-10-02 08:09:10"
    chord("Control_L", "z")
    assert saved("Checking")["opening_at"] == "2026-10-01 08:09:10"
    ok("Starting Date preserves optional time and accepts seconds; each completed date edit can be undone")

    replace_all(edit_cell("Checking", "balance"), "2000.25")
    double_cell("AmEx", "balance")
    assert saved("Checking")["opening_cents"] == 200025
    assert app.account_editing == "AmEx" and app.account_cell_editor.flow_id == "AmEx"
    replace_all(app.account_cell_editor.input, "-400.75")
    double_cell("Checking", "balance")
    assert saved("AmEx")["opening_cents"] == -40075
    assert app.account_editing == "Checking"
    key("Return")
    no_prompt()
    chord("Control_L", "z")
    assert saved("AmEx")["opening_cents"] == -30025
    chord("Control_L", "y")
    assert saved("AmEx")["opening_cents"] == -40075
    ok("Switching between account rows saves the old field and opens the right record without confirmations")

    saved_balance = saved("Checking")["opening_cents"]
    replace_all(edit_cell("Checking", "balance"), "oops")
    key("Return")
    assert app.account_editing == "Checking"
    assert app.account_vars["opening"].get() == "oops"
    assert saved("Checking")["opening_cents"] == saved_balance
    assert app.account_feedback.get()
    key("Escape")
    assert app.account_vars["opening"].get() == f"{saved_balance / 100:.2f}"
    replace_all(edit_cell("Checking", "anchor"), "2026-99-01")
    key("Return")
    assert saved("Checking")["opening_at"] == "2026-10-01 08:09:10"
    assert app.account_feedback.get()
    key("Escape")
    assert app.account_vars["date"].get() == "2026-10-01"
    no_prompt()
    ok("Invalid balances and dates stay unsaved with visible errors; Escape restores their saved values")

    row(app.accounts_tree, "Checking")
    key("F2")
    assert app.account_cell_editor.field == "balance"
    type_text("1200.25")
    key("Tab")
    assert app.account_cell_editor.field == "anchor"
    assert saved("Checking")["opening_cents"] == 120025
    key("Shift_L", True)
    key("Tab")
    key("Shift_L", False)
    assert app.account_cell_editor.field == "balance"
    type_text("9999.99")
    key("Escape")
    editor_closed()
    assert saved("Checking")["opening_cents"] == 120025
    assert app.account_vars["opening"].get() == "1200.25"
    ok("F2 and Tab/Shift+Tab navigate editable cells; Escape abandons only the current unfinished field")

    click(app.account_new_button)
    replace_all(app.account_name_entry, "Draft reserve")
    replace_all(app.account_widgets["opening"], "123.45")
    row(app.accounts_tree, "Checking")
    assert "Draft reserve" not in [a["name"] for a in repo.list_accounts()]
    no_prompt()
    click(app.account_new_button)
    replace_all(app.account_name_entry, "Travel cash")
    replace_all(app.account_widgets["opening"], "88.25")
    assert str(app.account_save_button.cget("text")) == "Add Account"
    click(app.account_save_button)
    assert saved("Travel cash")["opening_cents"] == 8825
    fresh_form()
    chord("Control_L", "z")
    assert "Travel cash" not in [a["name"] for a in repo.list_accounts()]
    chord("Control_L", "y")
    assert saved("Travel cash")["opening_cents"] == 8825
    ok("New account drafts are discarded on row selection; Add Account creates one undoable record")

    replace_all(edit_cell("Checking", "balance"), "1777.88")
    app.geometry("1280x800+0+0")
    pump(.25)
    aligned_or_closed("Checking", "balance")
    assert app.account_vars["opening"].get() == "1777.88"
    assert app.accounts_tree.xview() == (0.0, 1.0)
    before_scroll = app.accounts_tree.yview()
    wheel_down(app.accounts_tree)
    assert app.accounts_tree.yview() != before_scroll
    aligned_or_closed("Checking", "balance")
    assert saved("Checking")["opening_cents"] == 177788
    click_text("Flows")
    assert app.current_view == "flows"
    editor_closed()
    click_text("Accounts")
    fresh_form(check_focus=False)
    no_prompt()
    ok("Resizing, scrolling, and changing pages save completed account edits without stale overlays or prompts")

    app.geometry("1920x1200+0+0")
    pump(.25)
    replace_all(edit_cell("Checking", "balance"), "1525.75")
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Accounts_Edit.png")
    key("Return")
    click(app.account_new_button)
    fresh_form()
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("New Account clears selection and editing state; popups never first appear at the top-left corner")
    success = True
    print(f"PASS {len(results)} account interaction checks; no callback exceptions.", flush=True)
except BaseException:
    audit_failure = traceback.format_exc()
    raise
finally:
    payload = {"success": success, "input": "X11 XTest mouse and keyboard events",
               "passed": results, "errors": errors, "failure": audit_failure,
               "maps": maps, "database": str(path)}
    report.write_text(json.dumps(payload, indent=2))
    report.with_suffix(".txt").write_text(
        "Accounts editing: native X11 XTest interaction audit\n"
        + ("PASS" if success else "FAIL") + f": {len(results)} workflows; {len(errors)} callback exceptions.\n\n"
        + "\n".join("PASS " + label for label in results) + "\n"
        + ("\n".join(errors) if errors else "") + audit_failure)
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
