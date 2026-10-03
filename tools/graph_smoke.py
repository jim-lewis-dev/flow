"""Focused graph audit driven by native X11 mouse and keyboard events.

Fixture setup and read-only result inspection use application objects. All
audited interactions use XTest against an isolated display.
"""
from pathlib import Path


def seed_graph_data(repo):
    anchor = "2026-10-01 00:00:00"
    repo.save_account("Checking", 100000, anchor)
    repo.save_account("Savings", 50000, anchor)
    repo.save_account("Unknown", None, anchor)
    for date, source, destination, amount, name in (
        ("2026-10-01", "Checking", "Shop", "12", "Midnight purchase"),
        ("2026-10-02 12:00:00", "Shop", "Checking", "20", "Refund"),
        ("2026-10-02 18:00:00", "Savings", "Checking", "50", "Transfer"),
    ):
        repo.save_flow(dict(date=date, from_account=source, to_account=destination,
                            amount=amount, name=name, status="Complete"))


shared = Path(__file__).with_name("ui_smoke.py").read_text()
shared = shared.split("try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_graph_data(repo)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def variable_entry(variable):
    return next(widget for widget in widgets()
                if widget.winfo_class() == "TEntry"
                and str(widget.cget("textvariable")) == str(variable))


def select_accounts(*names):
    click_text("None")
    for name in names:
        index = app.chart_accounts.get(0, "end").index(name)
        x, y, width, height = app.chart_accounts.bbox(index)
        click_xy(app.chart_accounts.winfo_rootx() + 15,
                 app.chart_accounts.winfo_rooty() + y + height / 2)
    assert {app.chart_accounts.get(index)
            for index in app.chart_accounts.curselection()} == set(names)


def update():
    click_text("Update Graph")
    pump(.2)


def line(name):
    return next(item for item in app.axes.lines if item.get_label() == name)


def point(at, value):
    from matplotlib.dates import date2num
    from datetime import datetime
    dx, dy = app.axes.transData.transform((date2num(datetime.fromisoformat(at)), value))
    canvas = app.canvas.get_tk_widget()
    return canvas.winfo_rootx() + dx, canvas.winfo_rooty() + canvas.winfo_height() - dy


def hover(at, value):
    move(*point(at, value))
    pump(.2)


def tooltip_has(*parts):
    tooltip = app.chart_tooltip
    assert tooltip is not None and tooltip.get_visible()
    text = tooltip.get_text()
    for part in parts:
        assert part in text, (part, text)
    return text


def click_legend(name):
    legend_line = next(legend for legend, original in app.chart_line_map.items()
                       if original.get_label() == name)
    box = legend_line.get_window_extent(app.canvas.get_renderer())
    canvas = app.canvas.get_tk_widget()
    click_xy(canvas.winfo_rootx() + (box.x0 + box.x1) / 2,
             canvas.winfo_rooty() + canvas.winfo_height() - (box.y0 + box.y1) / 2)


def visible_message(part):
    assert part.lower() in app.chart_message.get().lower(), app.chart_message.get()
    assert app.chart_message_label.winfo_viewable()
    assert app.chart_message_label.winfo_rooty() < app.canvas.get_tk_widget().winfo_rooty()


try:
    # Xvfb has no window manager: it cannot prove fullscreen placement on one
    # physical monitor. Escape is still sent as the user's next window action.
    assert app._fullscreen_requested
    key("Escape")
    assert not app._fullscreen_requested
    ok("Startup requests fullscreen and a native Escape key returns to windowed mode")
    app.geometry("1920x1200+0+0")
    pump()
    click(app.nav_buttons["charts"])
    start_entry = variable_entry(app.chart_start)
    end_entry = variable_entry(app.chart_end)
    replace(start_entry, "2026-10-01")
    replace(end_entry, "2026-10-03")
    select_accounts("Checking")
    update()
    values = list(line("Checking").get_ydata())
    assert values[:2] == [1000.0, 988.0], values
    assert values[-1] == 1058.0, values
    dates = list(line("Checking").get_xdata())
    assert dates[0] == dates[1], dates[:2]
    ok("A flow at the graph's first midnight preserves the opening value and the resulting balance")

    hover("2026-10-01 12:00:00", 988)
    tooltip_has("Checking", "$988.00")
    hover("2026-10-01 00:00:00", 1000)
    tooltip_has("Checking", "$1,000.00")
    hover("2026-10-02 18:00:00", 1033)
    tooltip_has("Checking", "$1,008.00", "$1,058.00")
    move(start_entry.winfo_rootx() + 5, start_entry.winfo_rooty() + 5)
    assert not app.chart_tooltip.get_visible()
    ok("Native hover shows exact account balances, opening value, and both ends of a vertical change")

    select_accounts("Checking", "Savings")
    update()
    hover("2026-10-01 12:00:00", 500)
    tooltip_has("Savings", "$500.00")
    click_legend("Savings")
    assert not line("Savings").get_visible()
    hover("2026-10-01 12:00:00", 500)
    assert not app.chart_tooltip.get_visible()
    hover("2026-10-01 12:00:00", 988)
    assert "Savings" not in tooltip_has("Checking", "$988.00")
    click_legend("Savings")
    assert line("Savings").get_visible()
    hover("2026-10-01 12:00:00", 500)
    tooltip_has("Savings", "$500.00")
    ok("Multiple accounts have distinct hover amounts; hidden lines stop responding until shown")

    click_text("Combine selected accounts")
    update()
    combined = list(line("Combined").get_ydata())
    assert combined[:2] == [1500.0, 1488.0], combined
    assert combined[-1] == 1508.0, combined
    hover("2026-10-01 12:00:00", 1488)
    tooltip_has("Combined", "$1,488.00")
    ok("Combined plotting preserves the same first instant and counts transfers once per endpoint")

    click_text("Combine selected accounts")
    select_accounts("Unknown")
    update()
    visible_message("Unknown")
    assert not app.chart_series
    assert any("Starting balance required" in text.get_text() for text in app.axes.texts)
    select_accounts("Checking", "Unknown")
    update()
    visible_message("Unknown")
    assert len(app.chart_series) == 1
    assert line("Checking").get_ydata()[-1] == 1058
    click_text("Combine selected accounts")
    update()
    visible_message("Unknown")
    assert not app.chart_series
    assert any("Combined balance is unknown" in text.get_text() for text in app.axes.texts)
    ok("Missing starting balances are prominent for single, mixed, and combined account selections")

    click_text("Combine selected accounts")
    select_accounts("Checking", "Savings")
    replace(start_entry, "2026-9-30")
    replace(end_entry, "2026-10-3")
    update()
    assert app.chart_start.get() == "2026-09-30"
    assert app.chart_end.get() == "2026-10-03"
    assert app.chart_message.get() == ""
    assert not app.chart_message_label.winfo_viewable()
    assert line("Checking").get_ydata()[-1] == 1058
    ok("Typed month/day values without leading zeros normalize and graph successfully")

    replace(start_entry, "2026-2-30")
    update()
    visible_message("From: invalid date")
    assert not app.chart_series and app.chart_tooltip is None
    replace(start_entry, "2026-10-04")
    update()
    visible_message("Through must be on or after From")
    ok("Invalid dates and reversed ranges display visible errors and clear stale graph contents")

    click(app.chart_start_picker.button)
    assert app.chart_start_picker.popup is not None
    click(app.chart_start_picker.next_button)
    assert app.chart_start_picker._month == 11
    click(app.chart_start_picker.previous_button)
    assert app.chart_start_picker._month == 10
    click(app.chart_start_picker.day_buttons[1])
    assert app.chart_start_picker.popup is None
    assert app.chart_start.get() == "2026-10-01"
    click(app.chart_end_picker.button)
    assert app.chart_end_picker.popup is not None
    # The From calendar overlays the Through row. Switch in the other direction
    # so the second button is actually visible to a person using the app.
    click(app.chart_start_picker.button)
    assert app.chart_end_picker.popup is None
    assert app.chart_start_picker.popup is not None
    key("Escape")
    click(app.chart_end_picker.button)
    click(app.chart_end_picker.day_buttons[3])
    assert app.chart_end.get() == "2026-10-03"
    click(app.chart_start_picker.button)
    key("Escape")
    assert app.chart_start_picker.popup is None
    assert app.chart_start.get() == "2026-10-01"
    click(app.chart_end_picker.button)
    click(app.chart_title)
    assert app.chart_end_picker.popup is None
    update()
    assert app.chart_end_picker.popup is None
    assert app.chart_message.get() == ""
    assert not app.chart_message_label.winfo_viewable()
    for event in maps:
        if event["override"]:
            assert not event["geometry"].endswith("+0+0"), event
    ok("Both calendars select dates; month navigation, Escape, switching calendars, and outside click work without origin flashing")

    app.geometry("1280x800+0+0")
    pump()
    update()
    hover("2026-10-03 12:00:00", 1058)
    tooltip_has("Checking", "$1,058.00")
    box = app.chart_tooltip.get_window_extent(app.canvas.get_renderer())
    bounds = app.figure.bbox
    assert box.x0 >= bounds.x0 and box.x1 <= bounds.x1, (box, bounds)
    assert box.y0 >= bounds.y0 and box.y1 <= bounds.y1, (box, bounds)
    assert app.chart_start_picker.button.winfo_viewable()
    assert app.chart_end_picker.button.winfo_viewable()
    ok("Resized graphs keep the date controls and edge hover labels visible")

    from PIL import ImageGrab
    app.geometry("1920x1200+0+0")
    pump()
    update()
    hover("2026-10-02 08:00:00", 988)
    tooltip_has("Checking", "$988.00")
    docs = Path(__file__).resolve().parents[1] / "docs"
    ImageGrab.grab().save(docs / "Flow_Graphs.png")

    success = True
    print(f"PASS {len(results)} graph interaction checks; no callback exceptions.", flush=True)
finally:
    report.write_text(json.dumps({"success": success, "input": "X11 XTest mouse and keyboard events",
        "passed": results, "errors": errors, "maps": maps, "database": str(path),
        "limitations": ["Xvfb has no window manager or physical monitor layout; this audit does not verify fullscreen placement across real monitors."]}, indent=2))
    print("Report:", report, flush=True)
    app.destroy()
    repo.close()
