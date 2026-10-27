# Flow

Flow records transfers between accounts and calculates balances over time. Each transfer has an amount and a unit: USD, kg, Wh, or any other unit you use.

A 5 kg transfer from Warehouse to Workshop subtracts 5 kg from Warehouse and adds 5 kg to Workshop. Their balances in other units are unchanged.

![Flow ledger with inline editing and account, unit, status, category, and tag filters](docs/ledger.png)

## Run

From the project directory on Ubuntu, with Python 3.10 or newer:

```bash
sudo apt install python3-tk python3-matplotlib
./flow --demo
```

The demo uses a separate database. Run `./flow` for your records. **Esc** leaves fullscreen; terminal **Ctrl+C** exits.

To use `flow` from any directory:

```bash
./install.sh
flow money
```

The installer checks dependencies and links `~/.local/bin/flow` to this project. Keep the project folder in place; follow its PATH instructions if needed.

## Records

**Date, From, To, Amount, and Units are required.** Date defaults to today, time is optional, and status defaults to Complete. Name, category, tags, and notes describe the transfer.

- Fill the form, then **Add Flow**. **Clear Flow** resets it. Drafts survive tab switches.
- Select a row or double-click a cell to edit. Valid changes save automatically; the row stays put while you edit it.
- **Ctrl-click / Shift-click** select rows; hover for account, category, and unit totals.
- **Ctrl+C / Ctrl+V** duplicate rows; **Delete** removes them. **Ctrl+Z / Ctrl+Y** undo and redo during the session.
- Search and multi-select filters narrow the ledger. Choices come from saved records.

Amounts accept zero, negative values, and arithmetic such as `(12 + 8) / 2`. Matching From and To accounts produce no net balance change. The [data model](docs/model.md) covers fields, defaults, and calculations.

## Accounts and graphs

Set a starting balance and date for each account/unit pair. Unknown balances stay unknown. Accounts calculates balances at its **As of** date and time.

Graphs compare accounts or combine balances within a unit. Controls update the plots automatically. Hover for values, scroll to zoom, and use the toolbar to pan or reset.

| Basis | Flows included |
| --- | --- |
| Confirmed | Complete |
| Including Pending | Complete and Pending |
| Including Planned | Complete, Pending, and Planned |

Canceled flows have no effect on balances. Include Planned flows to check future balances before a transfer happens.

![Battery energy and water balances over three days](docs/graphs.png)

## Experimental

This tab animates transfers between accounts. Direction shows where a resource moves; pulse size represents quantity. Ledger filters, date range, unit, and status basis determine which flows appear.

![Energy transfers between the battery and camp equipment](docs/experimental.png)

Play, pause, step, or jump to either end. Hover accounts for totals or pulses for records; click a pulse to edit.

Drag accounts to arrange the map; positions are saved. Drag the background or scroll to pan, **Ctrl-scroll** to zoom, and double-click empty space to fit. Playback does not change records.

The [camp example](examples/README.md) follows three days of energy in Wh and water in L:

```bash
python3 -m examples.camp
./flow camp
```

It creates `camp.sqlite3`. Choose **Including Planned** to see whether the supplies cover all three days. The examples also include CSV/JSON imports and a Python API example.

## Files

Each dataset is one SQLite file:

```bash
flow money
flow energy
flow /path/to/travel.sqlite3
```

`flow money` opens `money.sqlite3` in the current directory or, failing that, in `~/.local/share/flowledger`, creating it there if needed. Explicit paths are used directly. With no name, Flow opens `flow.sqlite3` in the default directory. `XDG_DATA_HOME` overrides that directory's parent; `--db PATH` accepts an exact filename.

Close Flow before copying a database to another computer. It contains records, starting balances, audit history, and map positions. CSV/JSON exports contain flows only. There is no automatic synchronization or unit conversion.

## Development

Python, Tkinter, Matplotlib, and SQLite. Quantities use exact decimal arithmetic; drawing coordinates use floating point. See the [schema](flowledger/schema.sql) and [data model](docs/model.md).

Run the tests from a graphical desktop:

```bash
python3 -m unittest discover -s tests
```

For headless Linux, with `xvfb` and `xauth` installed:

```bash
xvfb-run -a -s '-screen 0 1920x1200x24' python3 -m unittest discover -s tests
```

GitHub Actions runs the suite on pushes and pull requests.

[MIT License](LICENSE)
