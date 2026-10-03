# Flow

Flow 0.5.5 records movements between named accounts and graphs their balances. It is a local Python/Tk desktop app using SQLite and exact integer cents.

## Install and update

Download `Flow_patch_v0.5.5.zip` into `~/Downloads`. Install once:

```bash
unzip -p "$HOME/Downloads/Flow_patch_v0.5.5.zip" flow/update.py | python3 - "$HOME/Downloads/Flow_patch_v0.5.5.zip"
```

This creates `~/projects/flow`, commits the installed files to a local Git repository, then removes the downloaded ZIP. Use this command for the upgrade from 0.5.4 too: its older installed updater does not delete ZIPs. It needs Python 3.10+, Git, unzip, and your Git name/email configured. It does not push anywhere.

For each subsequent patch, close Flow, download the ZIP into `~/Downloads`, then run:

```bash
~/projects/flow/update.py
```

The updater uses the most recently downloaded `Flow_patch_*.zip`, prints the change description, validates it, replaces the application files, commits, then deletes that ZIP from Downloads. Launch separately with `~/projects/flow/launch.sh`. It prints the commit and exact rollback command. Pass a ZIP path to select a particular download. A bad newest download stops the update; it never silently installs an older one. Automatic selection also refuses a version older than the installed release.

Local edits to managed code are committed first, so they can be recovered. Files removed from a later release are removed only when the previous release listed them. Other local files, databases and `.git` stay in place. No duplicate commit is made when the installed files are already identical; the already-committed ZIP is still removed. Failed updates keep the ZIP. If deletion alone fails, the successful commit stays and the error says the ZIP remains.

To undo the latest successful update:

```bash
git -C ~/projects/flow revert --no-edit HEAD
```

For an older update, use the specific commit printed by the updater. Git rollback restores code; it does not undo edits to your records or reverse database migrations. [Update details](docs/updates.md) cover Git setup and release packaging.

## Start

```bash
sudo apt install python3-tk python3-matplotlib
~/projects/flow/launch.sh --demo
```

Python 3.10 or newer and a graphical desktop are required. Run `./launch.sh` for your own records, or `python3 -m flowledger`.

The app requests fullscreen through your window manager at startup. **Escape** returns to a normal resizable window; an open dropdown or calendar closes first. Window size is no longer based on filling a wide combined desktop. Physical monitor placement depends on your window manager.

| Mode | Default database |
| --- | --- |
| Your records | `~/.local/share/flowledger/flow.sqlite3` |
| Demo | `~/.local/share/flowledger/demo.sqlite3` |

`XDG_DATA_HOME` is respected. `./launch.sh --db /path/to/flows.sqlite3` selects another file. Demo records are separate from your records.

## Two objects

- **Account:** name, starting balance, starting date/time. Name is its identity.
- **Flow:** date, from, to, amount, status, name, notes, category, tags, plus a flow ID for editing/imports.

There are no scenario or time-flag fields. Dates store `2026-10-02` or `2026-10-02 12:30:00`. A date alone means midnight in calculations; entered time is stored down to the second. Forecast collections and simulation controls are deferred.

[The model review](docs/model_review.md) shows actual objects, every SQLite table and their interactions. [JSON examples](examples/record_objects.json) and [SQL rows](examples/review.sql) are generated from the repository. **Record JSON** shows a selected saved record. **Data > View Schema** shows the database structure.

## Entry and editing

The full-width Flow Ledger sits below the entry form. At 1920 pixels, the nine entry fields occupy one row; narrower windows wrap them. The Accounts form is also horizontal.

| Field | Behavior |
| --- | --- |
| Date | Today by default; optional time is blank. Hover over a saved row to see its recorded time. |
| From / To | Required, different named accounts. Focus opens choices; typing filters; click a choice or press Enter to accept new text. |
| Amount | Positive USD with exact cents. |
| Status | Complete, Pending, Planned, Void. Defaults to Complete. |
| Name | Optional label; blank becomes From -> To. Earlier names are suggested. |
| Notes | Inline and expanded editors share one draft. Close, Escape and window close retain it. Up/Down follows wrapped text; the mouse wheel scrolls. |
| Category | One learned choice; blank becomes Uncategorized. |
| Tags | Optional comma-separated names. Quote names containing commas, such as `"Austin, TX"`. |

Save remembers new names and categories. A new endpoint initially has an unknown starting balance; set it in Accounts when known. Names match without capitalization or surrounding-space differences. Enter accepts a field value; it does not save a flow.

Click **×** beside a dropdown suggestion to remove it from entry lists. From and To share the same account suggestions. Removal is remembered after restarting and leaves your draft, saved records, ledger filters and graphs unchanged. To restore a suggestion, type its value and press Enter. The four statuses are fixed choices.

Suggestion preferences live in `<database>.suggestions.json` beside each database. They add no fields to accounts or flows and do not change the SQLite schema. Database backups contain your records; copy this small preferences file too if you want to move the same dropdown choices to another computer.

Click a ledger row to load all fields; no separate Edit button is needed. **Save** or **Ctrl+S** commits that record and returns to **New Flow**, with the defaults restored and both selection and focused ledger row cleared. Keyboard focus returns to Date, ready for the next entry. **Cancel** returns to the same New Flow state without saving. Saving leaves dropdowns closed. Notes commit with the whole flow, not when their expanded editor closes.

Leaving an unsaved draft asks **Save changes to this flow?** with Yes, No and Cancel. Refreshes preserve the draft. Invalid inputs remain available for correction. Account editing follows the same save/cancel pattern.

Dates accept `YYYY-MM-DD`, optionally followed by `HH:MM` or `HH:MM:SS`; ISO `T` is accepted. Entered times normalize to seconds. Values are local wall-clock times without timezone offsets or fractional seconds. Date-only and explicit midnight are the same instant, although their stored text preserves whether time was entered.

## Accounts and graphs

Every endpoint is an account, including merchants. Accounts has Name, Starting Balance and Starting Date, with optional time. No ownership or archive flags. Names stay fixed during editing; a different name creates a different account. Leave an unknown starting balance blank; enter zero for a known zero. Negative balances represent debt.

Balances are calculated from the account's one starting balance and eligible flows:

| Basis | Included statuses |
| --- | --- |
| Confirmed | Complete |
| Including Pending | Complete + Pending |
| Including Planned | Complete + Pending + Planned |

Void contributes nothing. The starting balance is confirmed immediately before flows at its timestamp. Earlier Complete flows are already reflected in that snapshot; eligible earlier Pending/Planned flows enter estimates once. Correct the anchor if you change which completed history it reflects. Current statuses do not reconstruct historical bank posting states.

AmEx -> Joe's Diner $12 reduces AmEx by $12 and increases Joe's Diner by $12. Checking -> AmEx $12 then moves value between those two accounts; their combined balance does not change from that payment. Refunds are reverse flows. Settle a Pending flow by updating its existing record.

Select accounts to graph; **All** and **None** control selection. Separate lines compare accounts; Combine sums exactly those selected. Missing starting balances appear in a warning directly above the plot. Known accounts can still be plotted separately; a combined line needs a starting balance for every selected account. Selecting every endpoint conserves the overall total, so that sum is not automatically net worth.

When a flow occurs exactly at From, the graph shows the value immediately before those flows and the change at that same timestamp. A dot marks the starting value. Simultaneous flows are combined, and no event is moved to an invented earlier time. Combining accounts preserves both sides of the initial change too.

From and Through have calendar buttons. Typed graph dates accept omitted leading zeros, such as `2026-10-1` or `2026-1-3`; optional local times remain supported. Selecting a calendar day preserves a valid entered time. A date-only Through includes the whole day. Invalid dates replace the old graph with a visible explanation identifying the field and value.

Hover near a line to see its account and exact dollar amount. Overlapping lines can show multiple accounts; vertical changes show the amounts before and after the change. Hidden lines are excluded. Click a time to inspect all selected accounts, use the toolbar to pan/zoom, or export PNG/PDF. Hover labels are excluded from exports.

The ledger filters by text, dates, statuses, categories and tags. Each checkbox dropdown supports multiple selections and a search box for finding choices. Checking an item updates results immediately and keeps the dropdown open. **All** removes that group's restriction; no checked items also means All.

Selections match **any** selected value within a group. Different groups combine: selecting Complete/Pending, Dining/Boat and Trip/Work shows flows with either status, either category, and at least one of those tags. Text and date limits further narrow those results. Searching within a dropdown does not discard checked choices. Filters remain in place after Save or Cancel. A date-only Through includes the full day. Category totals show **flow volume**, counting each matching non-Void flow once, including transfers.

## Import, export and recovery

CSV/JSON examples are in `examples/`. Import accepts a list of objects or `{"flows": [...]}` and requires date, source, destination and amount. Use decimal strings such as `"12.00"` or integer `amount_cents`. When both are present they must agree. Direct Python float amounts are rejected; JSON numeric amounts are parsed exactly from their text.

Preview distinguishes New, Duplicate, Conflict, Similar and Error. Same ID/same values is skipped; same ID/changed values blocks the batch. Similar content with a different/no ID requires explicit inclusion. Errors block saving. Approved records and their choices/history save atomically.

Exports contain the nine flow fields and ID. Old files containing `scenario` or `time_specified` are rejected explicitly; upgrade the old database and export again. This prevents hypothetical records from silently becoming ordinary flows. Flow exports omit account anchors and history; use **Backup** for the complete database. **Restore** validates the source and creates a pre-restore safety copy.

Use the updater above to replace application files in place. Existing default databases are reused. Version 0.5 upgrades schema 1-3 to schema 4 after making `<database>.pre-v4-<timestamp>.bak`. Ordinary records previously called Baseline remain active, with their IDs. All accounts and audit history remain. Old optional-time flags become date precision, then disappear from the active schema.

If the old database contains named hypothetical collections, they remain in the full backup and in a neighboring `.bak.forecasts.json` recovery file. They are not loaded into the active ledger. Restoring an old database migrates a copy and leaves the source unchanged. Historical audit entries keep their original JSON.

## Source and verification

| File | Responsibility |
| --- | --- |
| `update.py` | Installs a verified ZIP, commits locally, then removes the downloaded ZIP. |
| `tools/package_update.py` | Builds the application ZIP and its file manifest. |
| `flowledger/core.py` | Validation, repository, transactions, audit and balance functions. |
| `flowledger/schema.sql` | SQLite tables, constraints and indexes. |
| `flowledger/gui.py` / `widgets.py` | Draft editing, tables, graphs and controls. |
| `flowledger/suggestions.py` | Remembers removed dropdown suggestions separately from records. |
| `flowledger/filters.py` | Searchable checkbox dropdowns for ledger filtering. |
| `flowledger/graphing.py` | Graph boundary points and permissive graph date entry. |
| `flowledger/calendar_widget.py` | Calendar pickers used only by the graph date controls. |
| `flowledger/exchange.py` | CSV/JSON parsing, preview and atomic import/export. |
| `flowledger/demo.py` | Isolated example data. |
| `docs/model_review.md` | Current objects and their interactions. |
| `docs/Flow_Specification.pdf` | Two-page specification; Markdown source also included. |
| `docs/test_results.txt` / `interaction_check.txt` | Executed regression and GUI verification. |
| `docs/dropdown_interaction.txt` | Mouse/keyboard checks for suggestion removal and restoration. |
| `docs/filter_interaction.txt` | Mouse/keyboard checks for multiple filters and New Flow after Save. |
| `docs/graph_interaction.txt` | Mouse/keyboard checks for graph boundaries, calendars, warnings and hover values. |

```bash
python3 -m unittest discover -s tests -v
```

GUI tests require a display. `tools/README.txt` describes the Linux mouse/keyboard smoke test. Regenerate the specification with `python3 docs/generate_spec.py` (ReportLab is a documentation-only dependency).

Current scope: USD cents, one anchor per account, local timestamps and one computer. Forecast collections, automated flow generation, AI input, phone access and shared multi-user operation are deferred. A network-shared SQLite file is not a supported deployment.
