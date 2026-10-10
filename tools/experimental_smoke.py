"""Physical XTest audit of the optional account flow animation.

Runs against a fresh disposable database. All GUI interaction except the final
window-manager close callback uses actual X11 mouse and keyboard input.
"""
from pathlib import Path


def seed_experimental(repo):
    for name, units, opening in (
        ("Checking", "USD", "120.25"), ("Diner", "USD", "0"),
        ("Shop", "USD", "0"), ("Warehouse", "kg", "10"),
        ("Workshop", "kg", "0"),
    ):
        repo.save_account(name, opening, "2026-10-01", units=units)
    for name, when, source, target, amount, units, status in (
        ("Alpha", "2026-10-02", "Checking", "Diner", "12.3456789", "USD", "Complete"),
        ("Beta", "2026-10-02 00:00:00", "Checking", "Shop", ".125", "USD", "Complete"),
        ("Pending", "2026-10-02 12:34:56", "Checking", "Diner", "2.5", "USD", "Pending"),
        ("Planned", "2026-10-03 09:10:11", "Checking", "Shop", "4", "USD", "Planned"),
        ("Canceled", "2026-10-03 09:10:11", "Checking", "Shop", "99", "USD", "Canceled"),
        ("Reverse", "2026-10-03 09:10:12", "Diner", "Checking", "1", "USD", "Complete"),
        ("Weight", "2026-10-02 09:00:00", "Warehouse", "Workshop", ".25", "kg", "Complete"),
    ):
        repo.save_flow(dict(name=name, date=when, from_account=source,
                            to_account=target, amount=amount, units=units,
                            status=status, category="Experiment", tags=["Map"],
                            notes="Native map fixture"))


shared = Path(__file__).with_name("ui_smoke.py").read_text().split(
    "try:\n    exec(Path(__file__)", 1)[0]
shared = shared.replace("repo=Repository(path);seed_demo(repo)",
                        "repo=Repository(path);seed_experimental(repo)")
shared = shared.replace("app=FlowApp(repo,demo=True)", "app=FlowApp(repo,demo=False)")
exec(compile(shared, str(Path(__file__).with_name("ui_smoke.py")), "exec"))


def event_names():
    return [flow["name"] for flow in app.experimental._events]


def choose_next(combo):
    click(combo)
    key("Down")
    key("Return")


def choose_previous(combo):
    click(combo)
    key("Up")
    key("Return")


def drag_node(name, dx, dy):
    view = app.experimental
    node = next(item for item, value in view._node_items.items()
                if value == name and view.canvas.type(item) == "oval")
    x0, y0, x1, y1 = view.canvas.bbox(node)
    px = view.canvas.winfo_rootx() + (x0 + x1) / 2 - view.canvas.canvasx(0)
    py = view.canvas.winfo_rooty() + (y0 + y1) / 2 - view.canvas.canvasy(0)
    move(px, py)
    xt.XTestFakeButtonEvent(display, 1, True, 0)
    x.XSync(display, False)
    for step in range(1, 9):
        xt.XTestFakeMotionEvent(display, 0,
                                int(px + dx * step / 8),
                                int(py + dy * step / 8), 0)
        x.XSync(display, False)
        pump(.012)
    xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.18)


def click_pulse(name):
    view = app.experimental
    pulse = next(item for item, flow in view._pulse_items.items()
                 if flow["name"] == name and view.canvas.type(item) == "oval")
    x0, y0, x1, y1 = view.canvas.bbox(pulse)
    px = view.canvas.winfo_rootx() + (x0 + x1) / 2 - view.canvas.canvasx(0)
    py = view.canvas.winfo_rooty() + (y0 + y1) / 2 - view.canvas.canvasy(0)
    click_xy(px, py)


def pointer_on_item(item):
    view = app.experimental
    x0, y0, x1, y1 = view.canvas.bbox(item)
    px = view.canvas.winfo_rootx() + (x0 + x1) / 2 - view.canvas.canvasx(0)
    py = view.canvas.winfo_rooty() + (y0 + y1) / 2 - view.canvas.canvasy(0)
    move(px, py)
    pump(.34)
    assert view._hover_popup is not None and view._hover_popup.winfo_viewable()
    return view._hover_label.cget("text")


def slider_to(value):
    scale = app.experimental.speed_scale
    from_x, from_y = scale.coords(scale.get())
    sx, sy = scale.coords(value)
    begin_x, begin_y = scale.winfo_rootx() + from_x, scale.winfo_rooty() + from_y
    move(begin_x, begin_y)
    xt.XTestFakeButtonEvent(display, 1, True, 0)
    x.XSync(display, False)
    for step in range(1, 9):
        xt.XTestFakeMotionEvent(display, 0,
                                int(begin_x + (sx - from_x) * step / 8),
                                int(begin_y + (sy - from_y) * step / 8), 0)
        x.XSync(display, False)
        pump(.012)
    xt.XTestFakeButtonEvent(display, 1, False, 0)
    x.XSync(display, False)
    pump(.12)
    assert app.experimental.speed_var.get() == value, (value, app.experimental.speed_var.get())


def persistent_state():
    """Include the audit table so even a no-op record write is observable."""
    return tuple(repo.list_flows()), tuple(repo.conn.iterdump())


failure = ""
closed = False
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump(.2)
    initial = persistent_state()
    original = {flow["name"]: flow for flow in repo.list_flows()}
    assert tuple(app.nav_buttons)[-1] == "experimental"
    click(app.nav_buttons["experimental"])
    view = app.experimental
    assert app.current_view == "experimental"
    assert set(event_names()) == {"Alpha", "Beta", "Reverse"}, event_names()
    assert view.units_var.get() == "USD" and view.basis_var.get() == "Confirmed"
    assert len(view._groups) == 2 and len(view._groups[0][1]) == 2
    ok("Experimental is the last tab; Confirmed USD groups date-only and explicit midnight together")

    click(view.next_button)
    assert view._index == 0
    assert set(view.flow_list.get_children()) == {original["Alpha"]["flow_id"], original["Beta"]["flow_id"]}
    assert any(view.canvas.itemcget(item, "text") == "0.125 USD"
               for item in view.canvas.find_withtag("moving_flow")
               if view.canvas.type(item) == "text")
    click(view.previous_button)
    assert view._index == -1 and not view.flow_list.get_children()
    ok("Previous and Next step by timestamp, showing simultaneous flows with exact amount labels")

    click(view.end_button)
    assert view._index == len(view._groups) - 1 and view._progress == 1
    assert view.flow_list.get_children() == (original["Reverse"]["flow_id"],)
    click(view.start_button)
    assert view._index == 0 and view._progress == 0
    assert set(view.flow_list.get_children()) == {original["Alpha"]["flow_id"], original["Beta"]["flow_id"]}
    click(view.next_button)
    assert view._index == 1
    click(view.previous_button)
    assert view._index == 0
    ok("Start and End jump to the first source and last destination; Step controls move one timestamp")

    click(view.play_button)
    pump(.2)
    assert view._playing and view._after_id is not None
    assert view._pulse_items
    click(view.play_button)
    assert not view._playing and view._after_id is None
    ok("Play animates moving pulses; Pause cancels its scheduled callback")

    slider_to(5)
    assert view._duration() == 200
    slider_to(0)
    assert "disabled" in view.play_button.state()
    assert view._playing is False and view._after_id is None
    slider_to(1)
    assert "disabled" not in view.play_button.state()
    click(view.play_button)
    assert view._playing
    slider_to(0)
    assert not view._playing and view._after_id is None
    slider_to(1)
    ok("Mouse-operated speed slider reaches 0 and 5; zero pauses and disables Play until speed increases")

    click(view.next_button)
    old_position = view._positions["Checking"]
    drag_node("Checking", 80, 42)
    moved_position = view._positions["Checking"]
    assert moved_position != old_position, (old_position, moved_position)
    chord("Control_L", "z")
    assert view._positions["Checking"] == old_position
    chord("Control_L", "y")
    assert view._positions["Checking"] == moved_position
    ok("Physical node drag changes only the layout and Ctrl+Z/Y reverses and reapplies it")

    click(view.previous_button)
    assert view._index == 0 and len(view._pulse_items) == 4
    checking_node = next(item for item, value in view._node_items.items()
                         if value == "Checking" and view.canvas.type(item) == "oval")
    details = pointer_on_item(checking_node)
    assert "Checking · USD" in details and "Starting balance: 120.25 USD" in details, details
    assert "Balance after 2026-10-02 00:00:00 (Confirmed): 107.7793211 USD" in details, details
    assert "Shown through this step: In 0 USD · Out 12.4706789 USD" in details, details
    assert view.hover_var.get().startswith("Checking · USD")
    beta_dot = next(item for item, flow in view._pulse_items.items()
                    if flow["name"] == "Beta" and view.canvas.type(item) == "oval")
    details = pointer_on_item(beta_dot)
    assert all(part in details for part in ("Beta", "2026-10-02 00:00:00 · Complete",
                                            "Checking → Shop · 0.125 USD", "Category: Experiment",
                                            "Tags: Map", "Notes: Native map fixture")), details
    ok("Physical hover shows the account anchor, exact full-ledger balance, filtered totals, and full flow details")
    from PIL import ImageGrab
    docs = Path(__file__).resolve().parents[1] / "docs"
    for width, height in ((1920, 1200), (1280, 800)):
        app.geometry(f"{width}x{height}+0+0")
        pump(.28)
        assert view._positions["Checking"] == moved_position
        assert view.canvas.winfo_width() >= 700 and view.canvas.winfo_height() >= 300
        assert view.canvas.find_withtag("node") and view.canvas.find_withtag("moving_flow")
        if width == 1920:
            target = next(item for item, value in view._node_items.items()
                          if value == "Checking" and view.canvas.type(item) == "oval")
            details = pointer_on_item(target)
            assert "Balance after 2026-10-02 00:00:00" in details
        else:
            target = next(item for item, flow in view._pulse_items.items()
                          if flow["name"] == "Beta" and view.canvas.type(item) == "oval")
            details = pointer_on_item(target)
            assert "Category: Experiment" in details
        ImageGrab.grab(bbox=(0, 0, width, height)).save(docs / f"Flow_Experimental_{width}.png")
    ok("Dragged node stays placed across 1920 and 1280 windows; hover cards and pulses render in both")

    click_pulse("Beta")
    assert app.current_view == "flows" and app.editing_id == original["Beta"]["flow_id"]
    assert view._hover_popup is None or not view._hover_popup.winfo_viewable()
    click(app.nav_buttons["experimental"])
    ok("Clicking a moving flow opens its saved ledger row and dismisses the hover card")

    choose_next(view.basis_combo)
    assert view.basis_var.get() == "Including Pending", view.basis_var.get()
    assert set(event_names()) == {"Alpha", "Beta", "Pending", "Reverse"}
    choose_next(view.basis_combo)
    assert view.basis_var.get() == "Including Planned"
    assert set(event_names()) == {"Alpha", "Beta", "Pending", "Planned", "Reverse"}
    assert "Canceled" not in event_names()
    ok("Status basis includes Pending and Planned in order, while Canceled is always excluded")

    choose_next(view.units_combo)
    assert view.units_var.get() == "kg" and event_names() == ["Weight"]
    choose_previous(view.units_combo)
    assert view.units_var.get() == "USD"
    replace(view.from_entry.entry, "2026-10-02 12:34:56")
    replace(view.through_entry.entry, "2026-10-03 09:10:11")
    click(view.update_button)
    assert set(event_names()) == {"Pending", "Planned"}, event_names()
    assert view._groups[0][0] == "2026-10-02 12:34:56"
    assert view._groups[-1][0] == "2026-10-03 09:10:11"
    ok("Unit switching and typed From/Through dates honor exact second boundaries")

    click(app.nav_buttons["flows"])
    choice = app.ledger_filters["Status"]
    click(choice.button)
    assert choice.popup is not None
    click(choice.checkbuttons["Pending"])
    key("Escape")
    assert {flow["name"] for flow in app.ledger_rows.values()} == {"Pending"}
    click(app.nav_buttons["experimental"])
    assert event_names() == ["Pending"], event_names()
    click(view.next_button)
    pending_node = next(item for item, value in view._node_items.items()
                        if value == "Checking" and view.canvas.type(item) == "oval")
    details = pointer_on_item(pending_node)
    assert "Balance after 2026-10-02 12:34:56 (Including Planned): 105.2793211 USD" in details, details
    assert "Shown through this step: In 0 USD · Out 2.5 USD" in details, details
    ok("Filtered scene totals remain separate from the saved ledger's full account balance")
    row(view.flow_list, original["Pending"]["flow_id"], "name", count=2)
    assert app.current_view == "flows" and app.editing_id == original["Pending"]["flow_id"]
    assert choice.get() == ("Pending",)
    ok("The map respects live ledger filters and double-click opens the selected visible ledger flow")

    click(app.nav_buttons["experimental"])
    click(view.play_button)
    pump(.12)
    assert view._playing and view._after_id is not None
    click(app.nav_buttons["charts"])
    assert not view._playing and view._after_id is None
    pump(.24)
    click(app.nav_buttons["experimental"])
    assert event_names() == ["Pending"] and choice.get() == ("Pending",)
    assert persistent_state() == initial
    ok("Leaving during playback stops the timer; reentry retains filters and the database/audit are untouched")

    # Xvfb has no window manager or close decoration. Invoke the registered
    # window-manager close protocol after physical interaction above.
    app.request_close()
    closed = True
    assert not view._playing and view._after_id is None
    assert persistent_state() == initial
    ok("Window close leaves no pending playback callback and makes no record or audit changes")
    success = True
    print(f"PASS {len(results)} experimental interaction checks.", flush=True)
except Exception:
    failure = traceback.format_exc()
    raise
finally:
    outcome = dict(success=success, input="X11 XTest mouse and keyboard; window close via protocol callback",
                   passed=results, errors=errors, failure=failure, maps=maps,
                   database=str(path), record_count=len(repo.list_flows()))
    report.write_text(json.dumps(outcome, indent=2))
    report.with_suffix(".txt").write_text("Flow Experimental native interaction audit\n"
        + f"Result: {'PASS' if success else 'FAIL'} ({len(results)} checks)\n"
        + "\n".join(f"PASS {item}" for item in results)
        + ("\n" + failure if failure else "\nNo callback exceptions.\n"))
    if not closed and app.winfo_exists():
        view.close() if "view" in globals() else None
        app.history.close()
        app.destroy()
    repo.close()
