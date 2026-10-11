# Flow

Flow tracks quantities transferred between named accounts, in any unit. It is a local Python/Tk desktop app using SQLite and exact decimal quantities. Money is one possible resource, alongside materials, fuel, inventory or anything else you can measure.

## Install and update

Download `Flow_patch_update.zip` into `~/Downloads`. Install once:

```bash
unzip -p "$HOME/Downloads/Flow_patch_update.zip" flow/update.py | python3 - --project "$HOME/projects/flow" "$HOME/Downloads/Flow_patch_update.zip"
```

This creates `~/projects/flow`, commits the installed files to a local Git repository, then removes the downloaded ZIP. Use this command once to upgrade an older updater too; it runs the current updater bundled in the ZIP. It needs Python 3.10+, Git, unzip, and your Git name/email configured. It does not push anywhere.

The first commit is `initial flow commit`. Later update commits use the patch's change description.

For each subsequent patch, close Flow, download the ZIP into `~/Downloads`, then run:

```bash
~/projects/flow/update.py
```

The updater uses the most recently downloaded `Flow_patch_*.zip`, prints the change description, validates it, replaces the application files, runs the bundled display-free tests, commits, then deletes that ZIP from Downloads. On test failure, it restores the previous files and keeps the ZIP. GUI tests still require a display and run separately. Launch with `~/projects/flow/launch.sh`. It prints the commit and exact rollback command. Pass a ZIP path to select a particular download. A bad newest download stops the update; it never silently installs an older one. Automatic selection also refuses a version older than the installed release.

An existing repository must be clean: uncommitted changes or untracked files stop the update before anything is replaced or committed. Commit or remove those changes yourself, then rerun. Ignored records and snapshots do not block updates. Files removed from a later release are removed only when the previous release listed them. Other local files, databases and `.git` stay in place. No duplicate commit is made when the installed files are already identical; the already-committed ZIP is still removed. Failed updates keep the ZIP. If deletion alone fails, the successful commit stays and the error says the ZIP remains.

To undo the latest successful update:

```bash
git -C ~/projects/flow revert --no-edit HEAD
```

For an older update, use the specific commit printed by the updater. Git rollback restores code; it does not undo edits to your records or reverse database migrations. [Update details](docs/updates.md) cover Git setup and release packaging.

## Send a project snapshot

```bash
~/projects/flow/export.py
```

Upload `~/projects/flow/flow-snapshot.zip`. The command overwrites that same ZIP each time and works from any directory. It includes current files, including local edits and untracked source allowed by Git, plus Git status, HEAD and the last 20 commit messages. It excludes databases, suggestion preferences, environments, caches, archives, common credential files and `.git` contents. Arbitrarily named Python environments are recognized by `pyvenv.cfg`; symlinks are omitted.

Recognizable Flow transaction exports in JSON or CSV are excluded by their contents, including renamed files and tracked files. Unchanged examples shipped with Flow are retained; replacing an example with personal records does not exempt those records. Ordinary JSON/CSV configuration remains included. This detects Flow records, not arbitrary personal information in other formats.

Export reads the working tree without staging, committing or changing it. It requires the project to be its own Git repository. Send a snapshot when we need to check your local state, especially after local edits or an unexpected result.

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

- **Account:** its name, with a separate starting balance and starting date/time for each unit it holds.
- **Flow:** date, name, from, to, amount, units, status, category, tags, notes, plus a flow ID for editing/imports.

There are no scenario or time-flag fields. Dates store `2026-10-02` or `2026-10-02 12:30:00`. A date alone means midnight in calculations; entered time is stored down to the second. What-if forecast collections are deferred; Experimental animates existing saved flows.

[The model review](docs/model_review.md) shows actual objects, every SQLite table and their interactions. [JSON examples](examples/record_objects.json) and [SQL rows](examples/review.sql) are generated from the repository. **JSON record** shows a selected saved record. **Data > View Schema** shows the database structure.

## Entry and editing

The full-width Flow Ledger sits below the entry form. Its default columns and form fields are **Date, Name, From, To, Amount, Units, Status, Category, Tags, Notes**. At 1920 pixels the ten fields occupy one row; narrower windows wrap them. The Accounts form is also horizontal.

| Field | Behavior |
| --- | --- |
| Date/Time | One field, defaulting to today. Optional 24-hour time, down to seconds. Hover over a saved row to see the full value. |
| Name | Optional label; blank becomes From -> To. Earlier names are suggested. |
| From / To | Required, different named accounts. Focus opens choices; typing filters; click a choice or press Enter to accept new text. |
| Amount | Required positive exact decimal quantity. Leading zero optional: `.50` = 0.5 and `.125` = 0.125. More than two decimal places are supported. |
| Units | Required, default USD. Select a learned unit or type a new one and press Enter. USD, yen, kg, liters or other unit names are labels, with no automatic conversion. |
| Status | Complete, Pending, Planned, Canceled. Defaults to Complete. |
| Category | One learned choice; blank becomes Uncategorized. |
| Tags | Optional comma-separated names. Quote names containing commas, such as `"Austin, TX"`. |
| Notes | All editors share the same text. Enter adds a line; Ctrl+Enter or leaving the field saves an existing record. Up/Down follows wrapped text; the mouse wheel scrolls. |

Readonly quantities show **2 decimal places for USD** and **4 for other units**. The code-only mapping in `flowledger/units.py` holds exceptions; there is no new entry field. This is display formatting only: storage, calculations, editors, JSON/export and graph hover retain exact quantities.

Adding or editing a flow remembers names, categories and units. A new account/unit pair initially has an unknown starting balance; set it in Accounts when known. Account/category/tag names ignore capitalization and surrounding spaces. Units ignore surrounding spaces but are **case-sensitive**: `mW` and `MW` are distinct. Enter accepts a field value. Existing records save that completed edit; new records wait for Add Flow.

Click **×** beside a dropdown suggestion to remove it from entry lists. From and To share the same account suggestions. Removal is remembered after restarting and leaves your draft, saved records, ledger filters and graphs unchanged. To restore a suggestion, type its value and press Enter. The four statuses are fixed choices.

Suggestion preferences live in `<database>.suggestions.json` beside each database. They add no fields to accounts or flows and do not change the SQLite schema. Database backups contain your records; copy this small preferences file too if you want to move the same dropdown choices to another computer.

Click a ledger row to load all fields. Double-click a cell to edit it directly; **F2** starts editing the focused row. The ledger and upper form share the same values, including Notes. From, To, Name, Units, Category and Tags use searchable suggestions; Status offers the four fixed choices. Date and optional time are entered together in one field. Omitting the time resets it to midnight.

For an **existing flow**, **Enter**, **Tab**, or leaving a field saves the completed edit immediately. The same row stays selected. Notes uses Enter for a new line; **Ctrl+Enter**, leaving Notes, or closing expanded Notes saves it. Invalid values display a silent error and leave the saved record unchanged; correct the field or press Escape to restore its saved value. Escape closes an open dropdown first; with no unfinished existing edit, it exits fullscreen without changing a new draft. Filters, totals, graphs and JSON record reflect successful edits immediately. Flow does not play error sounds.

**New Flow** clears the form. **Add Flow** inserts a new draft and returns to a blank form with today's date, USD, Complete and Uncategorized. Moving between draft fields keeps what you typed. Selecting a ledger row or another page discards an unfinished new draft without a prompt. Add Flow is disabled when an existing flow is selected.

Ledger records are saved immediately. Closing finishes a valid existing edit; a new top-form draft still requires Add Flow. **Ctrl+S** completes edits to an existing record and leaves new drafts untouched. A blank or unfinished New Flow does not block saving. Use **Add Flow** to add the top draft to the ledger; the Accounts form follows the same rule with **Add Account**.

Click empty space below the ledger rows to deselect and return to New Flow. Valid edits to existing records save first; invalid unfinished edits and unadded drafts are discarded. The saved record remains unchanged when invalid input is discarded. Dropdowns close and entry fields return to their defaults.

Select a ledger row and press **Ctrl+C**, then **Ctrl+V** to insert and select a saved copy immediately. Every field is copied, including date/time, status, notes and tags; only its ID is new. Edit the copy directly. Inside a field, these shortcuts copy and paste text. Existing filters stay in place; a hidden pasted copy opens in the top editor.

Press **Delete** on a selected ledger row to remove it without confirmation. **Ctrl+Z** restores its complete record; **Ctrl+Y** removes it again. Delete inside a field edits text. Set **Status = Canceled** to keep a record while excluding it from every balance and graph. Unreferenced account/unit rows disappear; their starting balances remain available for reuse and undo.

**Ctrl+Z** undoes and **Ctrl+Y** redoes during the current app session. Undo and Redo buttons use the same history. Record edits, additions, deletion, status changes, imports, suggestion removal/restoration, draft typing, New Flow/New Account clearing and row paste are reversible. Filters, ledger search, date ranges, sorting, graph account selections, basis, dates, combined balance, line visibility, pan/zoom and table column changes join that same action order. A filter changed after an edit undoes first; the next undo reverses the edit. Shortcuts work inside custom dropdowns and Notes. Consecutive typing in one focused field is one step. New changes discard redo. Closing the app or successfully restoring a database clears history; saved records stay saved. File exports, scrolling and opening a page or popup do not create undo steps.

**Drag a column title** to move it in Flow Ledger or Accounts. Click a ledger title to sort; drag its edge to resize. Cell editing and Tab follow the displayed column order. Moves and resizing are undoable. Column order and view settings last for the current session.

Discarding an unadded draft by selecting a row, changing pages or clicking blank ledger space can also be undone. Undo restores the entry fields without adding a record; changing pages stays ordinary navigation.

Dates use year-first order and optional 24-hour time in the same entry. Leading zeros are optional. Missing month/day default to 1; missing hour/minute/second default to 0. These rules apply to flows, account starting values and date filters.

| Entered | Interpreted point in time |
| --- | --- |
| `2026` | `2026-01-01 00:00:00` |
| `2026-10` | `2026-10-01 00:00:00` |
| `2026-10-9` | `2026-10-09 00:00:00` |
| `2026-10-9 7` | `2026-10-09 07:00:00` |
| `2026-10-9 7:5` | `2026-10-09 07:05:00` |
| `2026-10-9 7:5:6` | `2026-10-09 07:05:06` |

A space or ISO `T` separates date and time. Values are local wall-clock times without timezone offsets or fractional seconds. Date-only flow text stays date-only; entered time normalizes to seconds. Both date-only and explicit midnight mean the same instant. No separate time field, button or flag is used.

## Accounts and graphs

Every endpoint is an account, including merchants, warehouses or other resource holders. Accounts has Name, Units, Starting Balance and Starting Date/Time, with **one row for each account/unit pair**. Name and Units identify that row and stay fixed during editing; use Add Account to prepare another pair. Leave an unknown starting balance blank; enter zero for a known zero. Negative balances are supported.

From/To suggestions show names referenced by saved flows. Accounts and graph choices show the referenced **account/unit pairs**. Any status counts, including Canceled; ledger filters do not change these lists. Correcting the last reference hides the unused name or pair. Its starting balance remains available for reuse and undo. Add Account can prepare a starting balance, but the pair appears in these lists only after a flow uses it.

Accounts uses the same editing gestures as the Flow Ledger: click a row to load it, then double-click **Starting Balance** or **Starting Date** to edit directly. **F2** opens Starting Balance; **Tab** moves between those fields. The starting-balance cursor begins at the end; later clicks place it normally. Enter, Tab or leaving the field saves an existing account and updates calculated balances. **New Account** clears the form; **Add Account** inserts a new one. Starting Date/Time follows the same entry rules as flows, including midnight when time is omitted. Account names remain fixed identities.

Balances are calculated independently for each account/unit pair, from its starting balance and eligible flows in that unit:

| Basis | Included statuses |
| --- | --- |
| Confirmed | Complete |
| Including Pending | Complete + Pending |
| Including Planned | Complete + Pending + Planned |

Canceled contributes nothing. The starting balance is confirmed immediately before flows at its timestamp. Earlier Complete flows are already reflected in that snapshot; eligible earlier Pending/Planned flows enter estimates once. Correct the anchor if you change which completed history it reflects. Current statuses do not reconstruct historical bank posting states.

AmEx -> Joe's Diner, 12 USD reduces AmEx's USD balance by 12 and increases Joe's Diner's USD balance by 12. Warehouse -> Workshop, 0.125 kg changes only their kg balances. Transfers conserve each unit independently. Refunds are reverse flows. Settle a Pending flow by updating its existing record.

Select the **account/unit pairs** to graph; **All** and **None** control selection. Separate lines compare accounts. Different units get separate plots sharing the time range; Combine sums selected accounts **within each unit**, never across units. Its legend names the accounts included. Missing starting balances identify their account and unit above the plot. Known pairs can still be plotted separately; a combined unit needs a starting balance for every selected pair in that unit. Selecting every endpoint conserves each unit's total.

When a flow occurs exactly at From, the graph shows the value immediately before those flows and the change at that same timestamp. A dot marks the starting value. Simultaneous flows are combined, and no event is moved to an invented earlier time. Combining accounts preserves both sides of the initial change too.

The graph initially fits its dates to the selected accounts' starting dates and flows included by the balance basis, including future Planned flows only when that basis is selected. Canceled flows do not extend the range. From and Through have calendar buttons; manual dates remain in force until **Fit Dates** resets them. Typed graph dates accept omitted leading zeros, such as `2026-10-1` or `2026-1-3`; optional local times remain supported. Selecting a calendar day preserves a valid entered time. A date-only Through includes the whole day. Invalid dates show an explanation identifying the field and value.

Selections, dates, balance basis and saved records redraw the graph automatically. Each unit's vertical range fits its plotted values with about 10% space above and below; it does not force zero into view. Use the toolbar or mouse wheel to zoom in and out; Home restores the fitted view.

Hover near a line to see its account, exact quantity and units. Overlapping lines can show multiple accounts; vertical changes show the quantities before and after the change. Hidden lines are excluded. Click a time to inspect selected pairs, use the toolbar to pan/zoom, or export PNG/PDF. Calculations and hover quantities remain exact; Matplotlib uses floating-point coordinates to draw them. A quantity outside that display range gets an inline error rather than an infinite or silently zero plot; its stored value remains exact. Hover labels are excluded from exports.

## Experimental flow map

The last tab, **Experimental**, animates transfers between account nodes. It starts from the flows currently visible under the Flow Ledger filters. Select a date range, one unit and a status basis for this view. The transport controls jump to the first or last time step, move back or forward one step, and play or pause. The speed slider runs from 0 (paused) through 1 (normal) to 5 (fastest). Flows sharing a timestamp move together. Each moving pulse scales with the quantity while its label gives the exact amount and unit. Hover over a pulse for its date/time, name, status, route, exact quantity/unit, category, tags and a notes preview. Drag account nodes to arrange the map; positions last only for this app session.

Hover over an account node for its name/unit, saved starting balance/date (or Unknown), and balance after the selected timestamp at the selected status basis. This balance is calculated from **all saved flows**, including flows hidden by Flow Ledger filters. Separately labeled **In/Out** totals count only flows shown in this Experimental view through the current step. Hover details appear near the pointer and in the detail line below the map.

This view reads saved flows and does not edit records or create forecasts. Canceled flows do not animate. Changing ledger filters changes which transfers are shown; it does not change stored balances or the existing graphs. Account placement and playback add no fields, tables or backup files.

The ledger has a compact Search field and six checkbox filters: **From**, **To**, **Status**, **Categories**, **Tags** and **Units**. Empty filters display `<label>: All`; hover over a filter to see every selected value. Each dropdown supports multiple selections and a search box for finding choices. Checking an item updates results immediately and keeps the dropdown open. **All** removes that group's restriction; no checked items also means All. From lists saved source accounts; To lists saved destination accounts, including refunds and Canceled flows. Other filters and removed entry suggestions do not hide these choices.

Selections match **any** selected value within a group. Different groups combine: From Checking/Amex and To Grocery/Diner shows flows from either selected source to either selected destination. Status, category, tags, units, text and dates further narrow those results. Units match exact case: selecting mW does not match MW. Unit choices come from all saved flows, including Canceled, independently of other filters and entry suggestion preferences. Search includes Units. Account matches use the complete account name, ignoring case. Searching within a dropdown does not discard checked choices. Filters remain in place after editing, adding or clearing the form, and support session Undo/Redo. A date-only Through includes the full day. Category totals show **flow volume by unit**, counting each matching non-Canceled flow once, including transfers. Ledger totals also stay separate per unit.

## Import, export and recovery

CSV/JSON examples are in `examples/`. Import accepts a list of objects or `{"flows": [...]}` and requires date, source, destination, amount and units. Use decimal strings such as `"12.00"` or `"0.125"`. Direct Python float amounts are rejected; JSON numeric amounts are parsed exactly from their text. Unit labels must be supplied, and differently named units are never converted or treated as duplicates.

Preview distinguishes New, Duplicate, Conflict, Similar and Error. Same ID/same values is skipped; same ID/changed values blocks the batch. Similar content with a different/no ID requires explicit inclusion. Errors block saving. Approved records and their choices/history save atomically.

Exports contain the ten flow fields and ID. Old files without units need a units column/field, such as USD, before importing. `amount_cents`, `scenario` and `time_specified` imports are rejected explicitly; upgrade the old database and export again. Flow exports omit starting balances and history; use **Backup** for the complete database. **Restore** validates the source and creates a pre-restore safety copy.

Use the updater above to replace application files in place. Existing default databases are reused. Opening the current schema 5 database upgrades its status constraint to schema 6 in one transaction: Void becomes Canceled; other values, IDs, anchors, metadata and historical audit stay unchanged. **This small status migration creates no backup file**, including when there are no Void records. A failure rolls back the schema and data together. Reopening does nothing further.

The earlier migrations from schema 1–4 still preserve a `.pre-v6-<timestamp>.bak` before converting cents/anchors to exact USD quantities and removing obsolete fields. Old named forecasts stay in that backup and recovery JSON, outside active flows. These older migrations are not run on schema 5. Existing backups are not deleted automatically.

Database and backup filenames are Git-ignored and omitted from update ZIPs and project snapshots. Your default database lives outside the project folder. **Backup** on the Data page creates a copy only when requested; **Restore** keeps its existing pre-restore safety copy.

Git rollback restores application code, not schema 6 records. Schema 5 code cannot open schema 6; reverting code does not reverse the status migration.

## Source and verification

| File | Responsibility |
| --- | --- |
| `update.py` | Installs a verified ZIP, runs display-free tests, commits locally, then removes the downloaded ZIP. |
| `export.py` | Overwrites the project snapshot ZIP with current files and Git status/log. |
| `tools/package_update.py` | Builds the application ZIP and its file manifest. |
| `flowledger/core.py` | Validation, repository, transactions, audit and balance functions. |
| `flowledger/schema.sql` | SQLite tables, constraints and indexes. |
| `flowledger/units.py` | Hidden per-unit decimal display settings; exact values stay unchanged. |
| `flowledger/history.py` | Session undo/redo of grouped changes; no persistent schema additions. |
| `flowledger/view_history.py` | UI snapshots and typing groups in the same session action order. |
| `flowledger/table_columns.py` | Header dragging, resizing history and correct display-to-field mapping. |
| `flowledger/gui.py` / `widgets.py` | Draft editing, tables, graphs and controls. |
| `flowledger/ledger_editor.py` | Ledger cell editors sharing the upper form's draft. |
| `flowledger/account_editor.py` | Starting balance/date cells using the same editing lifecycle. |
| `flowledger/suggestions.py` | Remembers removed dropdown suggestions separately from records. |
| `flowledger/filters.py` | Searchable checkbox dropdowns for ledger filtering. |
| `flowledger/graphing.py` | Graph boundary points and permissive graph date entry. |
| `flowledger/experimental.py` | View-only account map and transfer animation. |
| `flowledger/flow_motion.py` | Read-only event selection, timestamp grouping and visual pulse sizing. |
| `flowledger/calendar_widget.py` | Calendar pickers used only by the graph date controls. |
| `flowledger/exchange.py` | CSV/JSON parsing, preview and atomic import/export. |
| `flowledger/demo.py` | Isolated example data. |
| `docs/model_review.md` | Current objects and their interactions. |
| `docs/Flow_Specification.pdf` | Two-page specification; Markdown source also included. |
| `docs/test_results.txt` / `interaction_check.txt` | Executed regression and GUI verification. |
| `docs/dropdown_interaction.txt` | Mouse/keyboard checks for suggestion removal and restoration. |
| `docs/filter_interaction.txt` | Mouse/keyboard checks for multiple filters and resetting after an addition. |
| `docs/control_fixes_interaction.txt` | Native checks for deletion, cursor placement, six filters and selection tooltips. |
| `docs/account_filter_interaction.txt` | Native checks for From/To selections, search, undo/redo, editing, Add and compact layout. |
| `docs/units_interaction.txt` | Native checks for Units, exact quantities, per-unit starting balances and mixed-unit graphs. |
| `docs/graph_interaction.txt` | Mouse/keyboard checks for graph boundaries, calendars, warnings and hover values. |
| `docs/clipboard_interaction.txt` | Native row/text clipboard, deletion, status, precision and layout checks. |
| `docs/experimental_interaction.txt` | Native checks for map controls, playback and node dragging. |
| `docs/ledger_interaction.txt` | Mouse/keyboard checks for automatic flow edits, Add, row paste and undo/redo. |
| `docs/account_interaction.txt` | Mouse/keyboard checks for automatic account edits, timestamps and undo/redo. |
| `docs/view_history_interaction.txt` | Native checks for filter/draft/graph undo and table dragging/editing. |
| `docs/account_visibility_interaction.txt` | Mouse/keyboard checks for referenced accounts, blank-space clearing, silent validation and the single Date/Time entry. |

```bash
python3 -m unittest discover -s tests -v
```

GUI tests require a display. `tools/README.txt` describes the Linux mouse/keyboard smoke test. Regenerate the specification with `python3 docs/generate_spec.py` (ReportLab is a documentation-only dependency).

Current scope: any units, exact decimal quantities, one anchor per account/unit pair, local timestamps and one computer. Forecast collections, automated flow generation, AI input, phone access and shared multi-user operation are deferred. A network-shared SQLite file is not a supported deployment.

Older Void exports import as Canceled. No object fields or tables were added.
