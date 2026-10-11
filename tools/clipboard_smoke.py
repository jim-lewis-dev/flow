"""Physical XTest audit of row clipboard, keyboard deletion and Canceled."""
from pathlib import Path

shared = Path(__file__).with_name("control_fixes_smoke.py").read_text().split('failure = ""', 1)[0]
exec(compile(shared, str(Path(__file__).with_name("control_fixes_smoke.py")), "exec"))


def button_labels():
    from tkinter import ttk
    return [w.cget("text") for w in widgets() if isinstance(w, ttk.Button)]


failure = ""
try:
    key("Escape")
    app.geometry("1920x1200+0+0")
    pump()
    original = flow_named("Alpha")
    initial = repo.list_flows()
    for name in ("Repeat", "Delete", "Void", "Canceled", "Cancel", "History", "View all change history", "Record JSON"):
        assert name not in button_labels(), name
    assert button_labels().count("JSON record") == 2
    ok("Only JSON record and Category Totals remain as ledger inspection actions; unwanted action/history buttons are absent")

    row(app.ledger, original["flow_id"], "name")
    chord("Control_L", "c")
    assert json.loads(app.clipboard_get()) == original
    chord("Control_L", "v")
    copied = repo.get_flow(app.editing_id)
    assert copied["flow_id"] != original["flow_id"]
    assert copied == {**original, "flow_id": copied["flow_id"]}
    assert app.ledger.selection() == (copied["flow_id"],)
    assert "disabled" in app.save_button.state()
    ok("Ctrl+C/V inserts and selects a saved copy with a new ID and every other field intact, including full date/time and exact amount")

    replace(entry("name"), "Copied Alpha")
    key("Return")
    assert repo.get_flow(copied["flow_id"])["name"] == "Copied Alpha"
    assert repo.get_flow(original["flow_id"]) == original
    key("Escape")
    row(app.ledger, copied["flow_id"], "name")
    chord("Control_L", "z")
    assert repo.get_flow(copied["flow_id"]) == copied
    chord("Control_L", "z")
    assert repo.get_flow(copied["flow_id"]) is None
    chord("Control_L", "y")
    assert repo.get_flow(copied["flow_id"]) == copied
    chord("Control_L", "y")
    assert repo.get_flow(copied["flow_id"])["name"] == "Copied Alpha"
    ok("Editing the new copy leaves the source intact; Ctrl+Z/Y reverses editing then paste, and reapplies both in order")

    row(app.ledger, copied["flow_id"], "name")
    key("Delete")
    assert repo.get_flow(copied["flow_id"]) is None
    chord("Control_L", "z")
    assert repo.get_flow(copied["flow_id"])["name"] == "Copied Alpha"
    chord("Control_L", "y")
    assert repo.get_flow(copied["flow_id"]) is None
    chord("Control_L", "v")
    second = repo.get_flow(app.editing_id)
    assert second == {**original, "flow_id": second["flow_id"]}
    assert second["flow_id"] not in (original["flow_id"], copied["flow_id"])
    key("Delete")
    assert repo.list_flows() == initial
    ok("Delete remains keyboard-only and undoable; repeated paste creates another unique saved copy of the clipboard snapshot")

    row(app.ledger, original["flow_id"], "name")
    click(entry("name"))
    chord("Control_L", "a")
    chord("Control_L", "c")
    assert app.clipboard_get() == "Alpha"
    key("End")
    chord("Control_L", "v")
    assert entry("name").get() == "AlphaAlpha"
    key("Return")
    assert len(repo.list_flows()) == len(initial)
    chord("Control_L", "z")
    assert repo.get_flow(original["flow_id"]) == original
    key("Escape")
    double_cell(app.ledger, original["flow_id"], "notes")
    chord("Control_L", "a")
    chord("Control_L", "c")
    assert app.clipboard_get() == original["notes"]
    key("End")
    chord("Control_L", "v")
    assert app.ledger_editor.input.get("1.0", "end-1c") == original["notes"] * 2
    chord("Control_L", "Return")
    assert len(repo.list_flows()) == len(initial)
    chord("Control_L", "z")
    assert repo.get_flow(original["flow_id"]) == original
    ok("Copy/paste in upper fields and inline Notes edits text without inserting extra records")

    row(app.ledger, original["flow_id"], "name")
    click(entry("status"))
    key("Down"); key("Down"); key("Down")
    key("Return")
    assert repo.get_flow(original["flow_id"])["status"] == "Canceled"
    click(app.nav_buttons["accounts"])
    assert app.accounts_tree.set(account_iid("Checking"), "confirmed") == "120.25"
    click(app.nav_buttons["charts"])
    click_text("None")
    bx, by, bw, bh = app.chart_accounts.bbox(chart_index("Checking"))
    click_xy(app.chart_accounts.winfo_rootx() + bx + bw / 2,
             app.chart_accounts.winfo_rooty() + by + bh / 2)
    replace(app.chart_start_picker.entry, "2026-10-01")
    replace(app.chart_end_picker.entry, "2026-10-04")
    pump(.3)
    assert all(value == 120.25 for line in app.chart_series for value in line.get_ydata())
    click(app.nav_buttons["flows"])
    row(app.ledger, original["flow_id"], "name")
    chord("Control_L", "c")
    chord("Control_L", "v")
    canceled_copy = repo.get_flow(app.editing_id)
    assert canceled_copy["status"] == "Canceled"
    key("Delete")
    row(app.ledger, original["flow_id"], "name")
    click(entry("status"))
    key("Up"); key("Up"); key("Up")
    key("Return")
    ok("Canceled is selected through Status, remains in the ledger, contributes nothing to balances/graphs, and copies exactly")

    assert app.ledger.set(original["flow_id"], "amount") == "12.35"
    assert app.form_vars["amount"].get() == "12.3456789"
    beta = flow_named("Beta")
    assert app.ledger.set(beta["flow_id"], "amount") == "0.1250"
    double_cell(app.ledger, original["flow_id"], "amount")
    assert app.ledger_editor.input.get() == "12.3456789"
    key("Return")
    assert repo.get_flow(original["flow_id"])["amount"] == "12.3456789"
    click_text("JSON record", app.views["flows"])
    assert any(isinstance(w, tk.Text) and '12.3456789' in w.get("1.0", "end") for w in widgets())
    key("Escape")
    ok("USD displays two decimals and kg four; inline editing and JSON record retain full precision")

    from PIL import ImageGrab
    docs = Path(__file__).resolve().parents[1] / "docs"
    for width, height in ((1920, 1200), (1280, 800)):
        app.geometry(f"{width}x{height}+0+0")
        pump(.25)
        assert app.ledger.xview() == (0.0, 1.0)
        ImageGrab.grab(bbox=(0, 0, width, height)).save(docs / f"Flow_Clipboard_{width}.png")
    assert repo.list_flows() == initial
    reopened = Repository(path)
    assert reopened.list_flows() == initial
    reopened.close()
    ok("All ten columns fit at 1920 and 1280 pixels; saved records survive reopening, with no callback exceptions")
    success = True
    print(f"PASS {len(results)} clipboard interaction checks.", flush=True)
except Exception:
    failure = traceback.format_exc()
    raise
finally:
    outcome = dict(success=success, input="X11 XTest mouse and keyboard events", passed=results,
                   errors=errors, failure=failure, maps=maps, database=str(path))
    report.write_text(json.dumps(outcome, indent=2))
    report.with_suffix(".txt").write_text("Flow row clipboard native interaction audit\n"
        + f"Result: {'PASS' if success else 'FAIL'} ({len(results)} checks)\n"
        + "\n".join(f"PASS {item}" for item in results)
        + ("\n" + failure if failure else "\nNo callback exceptions.\n"))
    app.history.close()
    app.destroy()
    repo.close()
