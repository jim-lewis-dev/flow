"""Focused dropdown-X audit using the shared native X11 interaction helpers.

Run like ui_smoke.py on an isolated Xvfb display. Only fixture creation and
read-only assertions access application internals; interactions use XTest.
"""
from pathlib import Path

# Reuse the established audit setup/helpers, stopping before its unrelated
# workflow suite. This gives both audits identical OS-level input behavior.
shared = Path(__file__).with_name("ui_smoke.py").read_text()
exec(compile(shared.split("try:\n    exec(Path(__file__)", 1)[0],
             str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def show_all(field):
    combo = app.form_widgets[field]
    click(combo.entry)
    assert combo.popup is not None
    return combo


def remove_visible(combo, value):
    index = combo.matches.index(value)
    bounds = combo.listbox.bbox(index)
    assert bounds, (value, "Not visible; scroll using native input first")
    _, y, _, height = bounds
    click_xy(combo.remove_canvas.winfo_rootx() + combo.remove_canvas.winfo_width()/2,
             combo.remove_canvas.winfo_rooty() + y + height/2)
    assert value not in combo.values


try:
    fixture = repo.save_flow(dict(date="2026-10-02 12:30:15", from_account="Checking",
        to_account="Corner Shop", amount="12.34", units="USD", status="Complete", name="Lunch typo",
        category="Boaat", tags=["Trip", "Boat, trailer"], notes="Preserved notes"))
    for number in range(18):
        repo.save_account(f"A{number:02d}", '0')
        repo.save_flow(dict(date="2026-10-01", from_account="Checking",
                            to_account=f"A{number:02d}", amount="1", units="USD", status="Canceled"))
    app.refresh_all()
    pump()
    row(app.ledger, fixture["flow_id"])
    replace(app.form_widgets["amount"], "15.67")
    combo = show_all("from_account")
    assert repo.get_flow(fixture["flow_id"])["amount"] == '15.67'
    draft = app._flow_snapshot()
    database = "\n".join(repo.conn.iterdump())
    remove_visible(combo, "Checking")
    assert app._flow_snapshot() == draft
    assert "Checking" not in app.form_widgets["to_account"].values
    assert "\n".join(repo.conn.iterdump()) == database
    assert ("Checking", "USD") in app.chart_account_pairs
    ok("Finishing Amount autosaves; account X updates both entry lists without changing fields or records")

    key("Escape")
    chord("Control_L", "z")
    assert all("Checking" in app.form_widgets[field].values
               for field in ("from_account", "to_account"))
    assert "\n".join(repo.conn.iterdump()) == database
    chord("Control_L", "y")
    assert "Checking" not in app.form_widgets["from_account"].values
    assert "Checking" not in app.form_widgets["to_account"].values
    assert "\n".join(repo.conn.iterdump()) == database
    clean_popups("Ctrl+Z and Ctrl+Y restore and remove an X suggestion without changing saved flows")

    click(app.new_flow_button)
    assert app.editing_id is None

    combo = app.form_widgets["category"]
    replace(combo.entry, "boaa")
    assert combo.matches == ["Boaat"]
    draft = app._flow_snapshot()
    database = "\n".join(repo.conn.iterdump())
    remove_visible(combo, "Boaat")
    assert app._flow_snapshot() == draft
    assert "\n".join(repo.conn.iterdump()) == database
    assert "Boaat" in app.ledger_filters["Category"].values
    ok("Filtered category X removes the correct row while historical ledger filters remain available")

    combo = show_all("from_account")
    index = combo.matches.index("A12")
    move(combo.remove_canvas.winfo_rootx() + combo.remove_canvas.winfo_width()/2,
         combo.remove_canvas.winfo_rooty() + 20)
    for _ in range(30):
        if combo.listbox.bbox(index):
            break
        xt.XTestFakeButtonEvent(display, 5, True, 0)
        xt.XTestFakeButtonEvent(display, 5, False, 0)
        x.XSync(display, False)
        pump(.05)
    assert combo.listbox.yview()[0] > 0
    draft = app._flow_snapshot()
    remove_visible(combo, "A12")
    assert app._flow_snapshot() == draft
    assert repo.get_account("A12") is not None
    ok("Native wheel scrolling keeps each X aligned with its suggestion")

    combo = show_all("tags")
    remove_visible(combo, "Boat, trailer")
    # Reopening with all tags makes Trip independently removable.
    combo = show_all("tags")
    remove_visible(combo, "Trip")
    replace(combo.entry, 'Trip, "Boat, trailer"')
    key("Return")
    assert "Boat, trailer" in combo.values and "Trip" not in combo.values
    assert combo.get() == 'Trip, "Boat, trailer"'
    assert combo.popup is None
    ok("Enter restores only the final quoted CSV tag without saving or replacing earlier tags")

    app.history.close()
    app.destroy()
    app = FlowApp(repo, demo=True)
    app.geometry("1920x1200+0+0")
    app.report_callback_exception = report_error
    app.bind_all("<Map>", mapped, add="+")
    pump(.3)
    x.XSetInputFocus(display, app.winfo_id(), 1, 0)
    x.XSync(display, False)
    pump()
    assert "Checking" not in app.form_widgets["from_account"].values
    assert "Boaat" not in app.form_widgets["category"].values
    assert "A12" not in app.form_widgets["to_account"].values
    assert not app.history.can_undo and not app.history.can_redo
    database = "\n".join(repo.conn.iterdump())
    combo = app.form_widgets["from_account"]
    replace(combo.entry, "Checking")
    key("Return")
    assert all("Checking" in app.form_widgets[field].values
               for field in ("from_account", "to_account"))
    assert "\n".join(repo.conn.iterdump()) == database
    assert app.history.undo_label == "Restore suggestion"
    ok("Fresh app restart retains removed suggestions; exact Enter restores an account in both lists")

    combo = show_all("from_account")
    index = combo.matches.index("AmEx")
    bounds = combo.listbox.bbox(index)
    assert bounds
    _, y, _, height = bounds
    click_xy(combo.listbox.winfo_rootx()+30, combo.listbox.winfo_rooty()+y+height/2)
    assert combo.get() == "AmEx" and combo.popup is None
    key("Tab")
    combo = show_all("from_account")
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("Suggestion row selection still works and no dropdown first maps at the screen origin")
    from PIL import ImageGrab
    ImageGrab.grab().save(Path(__file__).resolve().parents[1] / "docs" / "Flow_Dropdown.png")
    success = True
    print(f"PASS {len(results)} dropdown interaction checks; no callback exceptions.", flush=True)
finally:
    report.write_text(json.dumps({"success":success,"input":"X11 XTest mouse and keyboard events",
        "passed":results,"errors":errors,"maps":maps,"database":str(path)},indent=2))
    print("Report:", report, flush=True)
    app.history.close()
    app.destroy()
    repo.close()
