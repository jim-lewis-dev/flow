"""Native checks for referenced accounts, quiet errors, clearing and Date/Time.

Fixture creation and read-only assertions use application objects. All user
actions go through XTest. See tools/README.txt for isolated-display setup.
"""
from pathlib import Path


def seed_visibility_data(repo):
    repo.save_account("Checking", '1000', "2026-10-01")
    repo.save_account("Typo", '543.21', "2026-10-01 12:34:56")
    repo.save_account("Never used", '400', "2026-10-01")
    repo.save_flow(dict(date="2026-10-02", from_account="Typo", to_account="Checking",
                        amount="12.34", units="USD", name="First reference", status="Complete"))
    repo.save_flow(dict(date="2026-10-03", from_account="Checking", to_account="Typo",
                        amount="5", units="USD", name="Last reference", status="Pending"))


shared = Path(__file__).with_name("ui_smoke.py").read_text()
shared = shared.split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_visibility_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def replace_all(widget, value):
    click(widget)
    chord("Control_L", "a")
    key("BackSpace")
    type_text(value)


def entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def account_names(*names):
    expected = set(names)
    assert {app._account_record(iid)["name"] for iid in app.accounts_tree.get_children()} == expected
    assert {name for name, unit in app.chart_account_pairs} == expected
    for field in ("from_account", "to_account"):
        assert set(app.form_widgets[field].values) == expected
    assert app.account_summary.get() == f"{len(expected)} accounts · {len(expected)} balances"


def blank_click():
    tree = app.ledger
    x, y = 40, tree.winfo_height() - 8
    assert tree.identify_region(x, y) == "nothing" and not tree.identify_row(y)
    click_xy(tree.winfo_rootx() + x, tree.winfo_rooty() + y)


def cleared():
    assert app.editing_id is None and app.ledger.selection() == () and app.ledger.focus() == ""
    assert app.form_title.cget("text") == "New Flow"
    for field in ("from_account", "to_account", "amount", "name", "tags"):
        assert app.form_vars[field].get() == "", field
    assert app.notes_text.get("1.0", "end-1c") == ""
    assert app.form_vars["status"].get() == "Complete"
    assert app.form_vars["category"].get() == "Uncategorized"
    assert app.ledger_editor.widget is None and not popups()
    assert not app.tk.splitlist(app.tk.call("grab", "current"))


failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump(.2)
    first = flow_named("First reference")["flow_id"]
    last = flow_named("Last reference")["flow_id"]
    anchor = repo.get_account("Typo")
    account_names("Checking", "Typo")
    assert repo.get_account("Never used")["opening_amount"] == '400'
    ok("Unused starting balances stay off Accounts, suggestions and graph choices")

    row(app.ledger, first)
    replace_all(entry("from_account"), "Correct")
    key("Return")
    account_names("Checking", "Typo", "Correct")
    assert repo.get_flow(first)["from_account"] == "Correct"
    ok("Correcting one From endpoint retains an account still referenced by To")

    row(app.ledger, last, "to_account", count=2)
    assert app.ledger_editor.field == "to_account"
    replace_all(app.ledger_editor.input, "Correct")
    key("Return")
    account_names("Checking", "Correct")
    assert repo.get_account("Typo") == anchor
    chord("Control_L", "z")
    account_names("Checking", "Typo", "Correct")
    chord("Control_L", "y")
    account_names("Checking", "Correct")
    click(app.nav_buttons["accounts"])
    assert tuple(app._account_record(iid)["name"] for iid in app.accounts_tree.get_children()) == ("Checking", "Correct")
    click(app.nav_buttons["charts"])
    assert app.chart_account_pairs == [("Checking", "USD"), ("Correct", "USD")]
    ok("Correcting the last reference hides the old account everywhere; undo and redo restore the right lists")

    click(app.nav_buttons["flows"])
    row(app.ledger, first)
    replace_all(entry("amount"), "45.67")
    blank_click()
    cleared()
    assert repo.get_flow(first)["amount"] == '45.67'
    chord("Control_L", "z")
    assert repo.get_flow(first)["amount"] == '12.34'
    chord("Control_L", "y")
    assert repo.get_flow(first)["amount"] == '45.67'
    ok("Clicking blank ledger space saves a valid edit once and resets selection and every entry field")

    row(app.ledger, first)
    click_text("Repeat")
    replace_all(entry("name"), "Unadded copy")
    blank_click()
    cleared()
    replace_all(entry("from_account"), "Unadded account")
    assert popups()
    blank_click()
    cleared()
    assert len(repo.list_flows()) == 2 and repo.get_account("Unadded account") is None
    ok("Blank-space clicks discard repeated/new drafts and close an open dropdown without reopening it")

    row(app.ledger, first, "amount", count=2)
    assert app.ledger_editor.field == "amount"
    replace_all(app.ledger_editor.input, "invalid")
    key("Return")
    assert "decimal" in app.form_feedback.get()
    assert repo.get_flow(first)["amount"] == '45.67'
    blank_click()
    cleared()
    assert repo.get_flow(first)["amount"] == '45.67'
    assert app.tk.call("info", "body", "bell") == ""
    ok("Invalid cell input shows a silent error; clicking blank space discards it without changing the saved flow")

    replace_all(entry("from_account"), "Typo")
    key("Return")
    replace_all(entry("to_account"), "Checking")
    key("Return")
    replace_all(entry("amount"), "2")
    click(app.save_button)
    account_names("Checking", "Correct", "Typo")
    assert repo.get_account("Typo") == anchor
    click(app.nav_buttons["accounts"])
    row(app.accounts_tree, "Typo")
    chord("Control_L", "z")
    account_names("Checking", "Correct")
    assert app.account_editing is None and app.account_vars["name"].get() == ""
    assert repo.get_account("Typo") == anchor
    chord("Control_L", "y")
    account_names("Checking", "Correct", "Typo")
    assert repo.get_account("Typo") == anchor
    ok("Reusing a hidden account retains its starting balance; undo of its last flow also clears its selected account editor")

    click(app.nav_buttons["flows"])
    row(app.ledger, first)
    for entered, stored in (("2026", "2026-01-01"),
                            ("2026-3", "2026-03-01"),
                            ("2026-10-9 7", "2026-10-09 07:00:00"),
                            ("2026-10-9 7:5", "2026-10-09 07:05:00"),
                            ("2026-10-9 7:5:6", "2026-10-09 07:05:06")):
        replace_all(entry("date"), entered)
        key("Return")
        assert repo.get_flow(first)["date"] == stored
        assert app.form_vars["date"].get() == stored
    assert "time" not in app.form_vars and "time" not in app.account_vars
    assert not hasattr(app, "time_button")
    ok("One Date/Time field accepts a year, year-month and 24-hour time through seconds without leading zeros")

    row(app.ledger, first, "date", count=2)
    assert app.ledger_editor.input.get() == "2026-10-09 07:05:06"
    replace_all(app.ledger_editor.input, "2026-10-9 18:4:3")
    key("Return")
    assert repo.get_flow(first)["date"] == "2026-10-09 18:04:03"
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Ledger_Edit.png")
    click_text("Repeat")
    assert app.form_vars["date"].get() == "2026-10-09 18:04:03"
    blank_click()
    cleared()
    row(app.ledger, first)
    replace_all(entry("date"), "2026-10-9")
    click(entry("amount"))
    assert repo.get_flow(first)["date"] == "2026-10-09"
    chord("Control_L", "z")
    assert repo.get_flow(first)["date"] == "2026-10-09 18:04:03"
    chord("Control_L", "y")
    assert repo.get_flow(first)["date"] == "2026-10-09"
    ok("Inline date edits and Repeat preserve seconds; omitting time resets it to midnight and undo/redo restores the exact value")

    replace_all(entry("date"), "2026-10-9 25")
    key("Return")
    assert "Invalid calendar date or time" in app.form_feedback.get()
    assert repo.get_flow(first)["date"] == "2026-10-09"
    blank_click()
    cleared()
    click(app.nav_buttons["accounts"])
    row(app.accounts_tree, "Typo", "anchor", count=2)
    replace_all(app.account_cell_editor.input, "2026-10-1 8:9:3")
    key("Return")
    assert repo.get_account("Typo")["opening_at"] == "2026-10-01 08:09:03"
    replace_all(app.account_widgets["date"], "2026-10-1")
    click(app.account_widgets["opening"])
    assert repo.get_account("Typo")["opening_at"] == "2026-10-01 00:00:00"
    chord("Control_L", "z")
    assert repo.get_account("Typo")["opening_at"] == "2026-10-01 08:09:03"
    ok("Invalid dates stay unsaved with a silent error; account cell and top Date/Time field use the same defaults and undo")

    replace_all(app.account_widgets["opening"], "-.50")
    key("Return")
    assert repo.get_account("Typo")["opening_amount"] == '-0.5'
    click(app.nav_buttons["flows"])
    row(app.ledger, first, "amount", count=2)
    replace_all(app.ledger_editor.input, ".05")
    key("Return")
    assert repo.get_flow(first)["amount"] == '0.05'
    blank_click()
    replace_all(entry("from_account"), "Checking")
    key("Return")
    replace_all(entry("to_account"), "Typo")
    key("Return")
    replace_all(entry("amount"), ".5")
    count = len(repo.list_flows())
    draft = app._flow_snapshot()
    chord("Control_L", "s")
    assert len(repo.list_flows()) == count and app._flow_snapshot() == draft
    assert app.status_message.get() == "Saved"
    click(app.save_button)
    assert len(repo.list_flows()) == count + 1
    assert any(flow["amount"] == '0.5' for flow in repo.list_flows())
    cleared()
    ok("Flow amounts accept .05 and .5; starting balances accept -.50 without requiring a leading zero")

    persisted = repo.list_flows()
    chord("Control_L", "s")
    cleared()
    assert app.status_message.get() == "Saved"
    replace_all(entry("from_account"), "Unadded endpoint")
    replace_all(entry("amount"), "invalid")
    draft = app._flow_snapshot()
    chord("Control_L", "s")
    assert app._flow_snapshot() == draft and not popups()
    assert app.status_message.get() == "Saved"
    assert repo.list_flows() == persisted and repo.get_account("Unadded endpoint") is None
    blank_click()
    row(app.ledger, first)
    click_text("Repeat")
    draft = app._flow_snapshot()
    chord("Control_L", "s")
    assert app._flow_snapshot() == draft and repo.list_flows() == persisted
    reopened = Repository(path)
    try:
        assert reopened.list_flows() == persisted
    finally:
        reopened.close()
    blank_click()
    cleared()
    ok("Ctrl+S leaves blank, partial, invalid and repeated new drafts unadded; ledger records are already saved in the database")
    success = True
    print(f"PASS {len(results)} native workflows; no callback exceptions.", flush=True)
except BaseException:
    failure = traceback.format_exc()
    raise
finally:
    report.write_text(json.dumps({"success": success,
        "input": "X11 XTest mouse and keyboard events", "passed": results,
        "errors": errors, "failure": failure, "maps": maps}, indent=2))
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
