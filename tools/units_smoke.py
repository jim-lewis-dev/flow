"""Resource units, exact quantities and account/unit graphs via native XTest.

Fixture setup and read-only assertions access application objects. Every user
edit, selection, undo and column drag uses physical X11 mouse/keyboard input.
"""
from pathlib import Path
from decimal import Decimal


def seed_units_data(repo):
    for name, units, balance in (
        ("Warehouse", "kg", "10.123456789"),
        ("Warehouse", "USD", "100"),
        ("Warehouse", "yen", "2000"),
        ("Market", "kg", "0"),
        ("Market", "USD", "20"),
        ("Market", "yen", "0"),
    ):
        repo.save_account(name, balance, "2026-10-01", units=units)
    for when, amount, units, status, name in (
        ("2026-10-02", "0.000000001", "kg", "Complete", "Grain"),
        ("2026-10-02", "12.345", "USD", "Complete", "Payment"),
        ("2026-10-03", "3", "yen", "Pending", "Yen transfer"),
        ("2026-10-03", ".5", "kg", "Pending", "Pending grain"),
        ("2026-10-04", ".25", "kg", "Planned", "Planned grain"),
    ):
        repo.save_flow(dict(date=when, from_account="Warehouse", to_account="Market",
                            amount=amount, units=units, status=status, name=name,
                            category="Resources", tags=["Audit"], notes="Unit-specific fixture"))


shared = Path(__file__).with_name("ui_smoke.py").read_text().split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_units_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))
from flowledger.table_columns import displayed_columns
from flowledger.core import format_quantity


def entry(field):
    widget = app.form_widgets[field]
    return widget.entry if isinstance(widget, FilterCombo) else widget


def enter(field, text):
    replace(entry(field), text)
    key("Return")


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


def select_pairs(*pairs):
    click_text("None")
    for pair in pairs:
        index = app.chart_account_pairs.index(pair)
        app.chart_accounts.see(index)
        pump(.06)
        bx, by, width, height = app.chart_accounts.bbox(index)
        click_xy(app.chart_accounts.winfo_rootx() + bx + 15,
                 app.chart_accounts.winfo_rooty() + by + height / 2)
    selected = tuple(app.chart_account_pairs[index] for index in app.chart_accounts.curselection())
    assert set(selected) == set(pairs), selected


def plotted(name, units):
    return next(line for line in app.chart_series
                if app.chart_line_units[line] == units and name in line.get_label())


def hover(name, units, at, amount):
    from matplotlib.dates import date2num
    from datetime import datetime
    axes = app.chart_axes[units]
    px, py = axes.transData.transform((date2num(datetime.fromisoformat(at)), float(amount)))
    canvas = app.canvas.get_tk_widget()
    move(canvas.winfo_rootx() + px, canvas.winfo_rooty() + canvas.winfo_height() - py)
    pump(.25)
    tip = app.chart_tooltip
    assert tip is not None and tip.get_visible()
    text = tip.get_text()
    assert name in text and units in text and format_quantity(amount) in text, text
    return text


failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    order = ("date", "name", "from_account", "to_account", "amount", "units", "status", "category", "tags", "notes")
    assert displayed_columns(app.ledger) == order
    assert (*app.form_widgets, "notes") == order, tuple(app.form_widgets)
    assert app.form_vars["units"].get() == "USD"
    assert isinstance(app.form_widgets["units"], FilterCombo)
    assert "$" not in " ".join(str(app.ledger.item(iid, "values")) for iid in app.ledger.get_children())
    ok("Default ten columns and top fields use the requested order, with a reusable Units dropdown and no currency symbols")

    grain = flow_named("Grain")
    row(app.ledger, grain["flow_id"])
    assert app.form_vars["units"].get() == "kg" and app.form_vars["amount"].get() == "0.000000001"
    click_text("Repeat")
    assert app.form_vars["units"].get() == "kg" and app.form_vars["date"].get() == grain["date"]
    enter("name", "Repeated grain")
    click(app.save_button)
    repeated = flow_named("Repeated grain")
    assert repeated["amount"] == grain["amount"] and repeated["units"] == "kg"
    assert repeated["date"] == grain["date"] and repeated["flow_id"] != grain["flow_id"]
    assert app.form_vars["units"].get() == "USD" and app.editing_id is None
    chord("Control_L", "z")
    assert not any(f["name"] == "Repeated grain" for f in repo.list_flows())
    ok("Repeat and Add preserve exact fractional quantities, units and date; Add resets the default unit and undo removes only the copy")

    enter("from_account", "Warehouse")
    enter("to_account", "Market")
    enter("date", "2026-10-02 7:8:9")
    enter("name", "Electricity")
    enter("amount", ".000000000123456789")
    enter("units", "")
    count = len(repo.list_flows())
    click(app.save_button)
    assert len(repo.list_flows()) == count and app.form_feedback.get()
    enter("units", "kWh")
    click(app.save_button)
    electricity = flow_named("Electricity")
    assert electricity["amount"] == "0.000000000123456789" and electricity["units"] == "kWh"
    assert electricity["date"] == "2026-10-02 07:08:09"
    assert "kWh" in app.form_widgets["units"].values
    ok("Units are mandatory; a new nonmonetary unit and a sub-cent fractional quantity save without rounding")

    row(app.ledger, electricity["flow_id"])
    enter("units", "")
    assert repo.get_flow(electricity["flow_id"])["units"] == "kWh"
    assert app.form_feedback.get()
    key("Escape")
    assert app.form_vars["units"].get() == "kWh"
    double_cell(app.ledger, electricity["flow_id"], "units")
    assert isinstance(app.ledger_editor.widget, FilterCombo)
    replace(app.ledger_editor.input, "hours")
    key("Return")
    assert repo.get_flow(electricity["flow_id"])["units"] == "hours"
    assert app.form_vars["units"].get() == "hours" and "hours" in app.form_widgets["units"].values
    chord("Control_L", "z")
    assert repo.get_flow(electricity["flow_id"])["units"] == "kWh"
    chord("Control_L", "y")
    assert repo.get_flow(electricity["flow_id"])["units"] == "hours"
    chord("Control_L", "z")
    ok("Top and inline Units share validation and auto-save; exact unit changes participate in session undo and redo")

    click(app.new_flow_button)
    replace(entry("units"), "k")
    combo = app.form_widgets["units"]
    assert "kg" in combo.matches and "kWh" in combo.matches
    index = combo.matches.index("kWh")
    bounds = combo.listbox.bbox(index)
    assert bounds
    click_xy(combo.remove_canvas.winfo_rootx() + combo.remove_canvas.winfo_width() / 2,
             combo.listbox.winfo_rooty() + bounds[1] + bounds[3] / 2)
    assert "kWh" not in combo.values and repo.get_flow(electricity["flow_id"])["units"] == "kWh"
    chord("Control_L", "z")
    assert "kWh" in combo.values
    chord("Control_L", "y")
    assert "kWh" not in combo.values
    chord("Control_L", "z")
    assert "kWh" in combo.values and not popups()
    ok("Units type-ahead filters choices; the X hides a suggestion and undo/redo restores it without changing any flow")

    click(app.nav_buttons["accounts"])
    warehouse_kg = account_iid("Warehouse", "kg")
    warehouse_usd = account_iid("Warehouse", "USD")
    assert app.accounts_tree.exists(warehouse_kg) and app.accounts_tree.exists(warehouse_usd)
    assert app.accounts_tree.set(warehouse_kg, "confirmed") == "10.123456788"
    assert app.accounts_tree.set(warehouse_kg, "pending") == "9.623456788"
    assert app.accounts_tree.set(warehouse_usd, "confirmed") == "87.655"
    row(app.accounts_tree, warehouse_kg)
    assert app.account_vars["units"].get() == "kg"
    assert str(app.account_widgets["units"].entry.cget("state")) == "disabled"
    replace(app.account_widgets["opening"], "12.123456789123456789")
    key("Return")
    assert repo.get_account("Warehouse", "kg")["opening_amount"] == "12.123456789123456789"
    assert repo.get_account("Warehouse", "USD")["opening_amount"] == "100"
    assert app.accounts_tree.set(warehouse_kg, "confirmed") == "12.123456788123456789"
    chord("Control_L", "z")
    assert repo.get_account("Warehouse", "kg")["opening_amount"] == "10.123456789"
    chord("Control_L", "y")
    assert repo.get_account("Warehouse", "kg")["opening_amount"] == "12.123456789123456789"
    ok("One account has separate unit rows, anchors and confirmed/pending balances; editing one starting balance leaves other units intact")

    double_cell(app.accounts_tree, warehouse_kg, "anchor")
    replace(app.account_cell_editor.input, "2026-10-1 1:2:3")
    key("Return")
    assert repo.get_account("Warehouse", "kg")["opening_at"] == "2026-10-01 01:02:03"
    assert repo.get_account("Warehouse", "USD")["opening_at"] == "2026-10-01 00:00:00"
    chord("Control_L", "z")
    assert repo.get_account("Warehouse", "kg")["opening_at"] == "2026-10-01 00:00:00"
    ok("Inline starting dates also belong to the selected account/unit pair and remain undoable")

    click(app.account_new_button)
    replace(app.account_name_entry, "Warehouse")
    unit_widget = app.account_widgets["units"]
    replace(unit_widget.entry, "liters")
    key("Return")
    replace(app.account_widgets["opening"], ".000000000987654321")
    replace(app.account_widgets["date"], "2026-10-01")
    click(app.account_save_button)
    assert repo.get_account("Warehouse", "liters")["opening_amount"] == "0.000000000987654321"
    assert repo.get_account("Warehouse", "kg")["opening_amount"] == "12.123456789123456789"
    assert app.account_editing is None
    ok("Add Account sets another unit's starting balance on an existing account name without overwriting prior units")
    row(app.accounts_tree, account_iid("Warehouse", "kWh"))
    replace(app.account_widgets["opening"], ".000000000987654321")
    key("Return")
    assert repo.get_account("Warehouse", "kWh")["opening_amount"] == "0.000000000987654321"
    ok("Selecting a referenced pair with an unknown balance lets its exact starting balance be set directly")

    click(app.nav_buttons["charts"])
    assert ("Warehouse", "kg") in app.chart_account_pairs and ("Warehouse", "USD") in app.chart_account_pairs
    start = next(w for w in widgets() if w.winfo_class() == "TEntry" and str(w.cget("textvariable")) == str(app.chart_start))
    end = next(w for w in widgets() if w.winfo_class() == "TEntry" and str(w.cget("textvariable")) == str(app.chart_end))
    replace(start, "2026-10-01")
    replace(end, "2026-10-05")
    select_pairs(("Warehouse", "kg"), ("Warehouse", "USD"), ("Market", "kg"), ("Market", "USD"))
    click_text("Update Graph")
    pump(.3)
    assert set(app.chart_axes) == {"kg", "USD"}, app.chart_axes
    assert len(app.chart_series) == 4
    assert app.chart_series[plotted("Warehouse", "kg")][-1][1] == Decimal("12.123456788123456789")
    assert app.chart_series[plotted("Warehouse", "USD")][-1][1] == Decimal("87.655")
    hover("Warehouse", "kg", "2026-10-04", "12.123456788123456789")
    hover("Warehouse", "USD", "2026-10-04", "87.655")
    ok("Selecting account/unit pairs draws separate unit plots with one time range, exact values and unit-specific hover labels")
    kg_line = plotted("Warehouse", "kg")
    usd_line = plotted("Warehouse", "USD")
    legend = next(legend for legend, line in app.chart_line_map.items() if line is kg_line)
    bounds = legend.get_window_extent(app.canvas.get_renderer())
    canvas = app.canvas.get_tk_widget()
    click_xy(canvas.winfo_rootx() + (bounds.x0 + bounds.x1) / 2,
             canvas.winfo_rooty() + canvas.winfo_height() - (bounds.y0 + bounds.y1) / 2)
    assert not kg_line.get_visible() and usd_line.get_visible()
    chord("Control_L", "z")
    assert plotted("Warehouse", "kg").get_visible() and plotted("Warehouse", "USD").get_visible()
    chord("Control_L", "y")
    assert not plotted("Warehouse", "kg").get_visible() and plotted("Warehouse", "USD").get_visible()
    chord("Control_L", "z")
    ok("Legend toggles and undo target one account/unit line even when the same account appears on another unit plot")

    click_text("Combine accounts within each unit")
    click_text("Update Graph")
    pump(.25)
    assert len(app.chart_series) == 2 and set(app.chart_line_units.values()) == {"kg", "USD"}
    assert app.chart_series[plotted("Combined", "kg")][-1][1] == Decimal("12.123456789123456789")
    assert app.chart_series[plotted("Combined", "USD")][-1][1] == Decimal("120")
    hover("Combined", "kg", "2026-10-04", "12.123456789123456789")
    hover("Combined", "USD", "2026-10-04", "120")
    chord("Control_L", "z")
    chord("Control_L", "z")
    assert not app.chart_combined.get() and len(app.chart_series) == 4
    ok("Combine totals only compatible units; kg and USD stay separate, and graph controls/rendering undo without changing flows")
    select_pairs(("Warehouse", "kg"))
    basis = next(w for w in widgets() if w.winfo_class() == "TCombobox"
                 and str(w.cget("textvariable")) == str(app.chart_basis))
    click(basis)
    key("Down")
    key("Down")
    key("Return")
    assert app.chart_basis.get() == "Including Planned"
    click_text("Update Graph")
    pump(.2)
    assert set(app.chart_axes) == {"kg"}
    assert app.chart_series[plotted("Warehouse", "kg")][-1][1] == Decimal("11.373456788123456789")
    hover("Warehouse", "kg", "2026-10-05", "11.373456788123456789")
    click(basis)
    key("Up")
    key("Up")
    key("Return")
    assert app.chart_basis.get() == "Confirmed", app.chart_basis.get()
    ok("Pending and planned flows forecast the selected unit with exact quantities and leave other units out")

    select_pairs(("Warehouse", "kWh"), ("Market", "kWh"))
    click_text("Update Graph")
    assert "Market" in app.chart_message.get() and "kWh" in app.chart_message.get()
    assert app.chart_series[plotted("Warehouse", "kWh")][-1][1] == Decimal("0.000000000864197532")
    ok("A missing starting balance identifies the exact account/unit pair while valid pairs remain graphed")

    from PIL import ImageGrab
    docs = Path(__file__).resolve().parents[1] / "docs"
    select_pairs(("Warehouse", "kg"), ("Warehouse", "USD"))
    click_text("Update Graph")
    pump(.3)
    ImageGrab.grab().save(docs / "Flow_Units_Graphs.png")
    click(app.nav_buttons["flows"])
    click(app.new_flow_button)
    for width, height in ((1920, 1200), (1280, 800)):
        app.geometry(f"{width}x{height}+0+0")
        pump(.25)
        assert app.ledger.xview() == (0.0, 1.0)
        assert displayed_columns(app.ledger) == order
        for field in order:
            assert (app.notes_text if field == "notes" else app.form_widgets[field]).winfo_viewable(), field
            if field != "notes":
                widget = entry(field)
                assert widget.winfo_rootx() + widget.winfo_width() <= width, field
        ImageGrab.grab(bbox=(0, 0, width, height)).save(docs / f"Flow_Units_{width}.png")
    ok("The ten-field horizontal entry and full ledger stay visible at 1920 and 1280 pixels")
    assert not errors and not [m for m in maps if m["override"] and m["geometry"].endswith("+0+0")]
    success = True
    print(f"PASS {len(results)} multiunit interaction checks; no callback exceptions.", flush=True)
except BaseException:
    failure = traceback.format_exc()
    raise
finally:
    report.write_text(json.dumps({"success": success, "input": "X11 XTest mouse and keyboard events",
        "passed": results, "errors": errors, "failure": failure, "maps": maps, "database": str(path)}, indent=2))
    report.with_suffix(".txt").write_text("Resource units: native X11 XTest interaction audit\n"
        + ("PASS" if success else "FAIL") + f": {len(results)} workflows; {len(errors)} callback exceptions.\n\n"
        + "\n".join("PASS " + label for label in results) + "\n" + failure)
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
