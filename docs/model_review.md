# Flow model review

A flow transfers an exact quantity from one named account to another, in one unit. Each account can hold several units, with a separate starting balance and time for each.

## Actual objects

`Repository.get_account("Warehouse", "kg")` returns one starting balance:

```json
{
  "name": "Warehouse",
  "units": "kg",
  "opening_amount": "20",
  "opening_at": "2026-10-01 00:00:00"
}
```

`Repository.get_flow(flow_id)` returns the ten agreed flow fields plus its editing ID:

```json
{
  "flow_id": "22222222-2222-4222-8222-222222222222",
  "date": "2026-10-03 09:15:06",
  "name": "Materials",
  "from_account": "Warehouse",
  "to_account": "Workshop",
  "amount": "0.125",
  "units": "kg",
  "status": "Complete",
  "category": "Materials",
  "tags": ["Project"],
  "notes": "Exact fractional quantity"
}
```

Repository-generated USD and kg objects are in `examples/record_objects.json`. `examples/review.sql` is their actual SQLite dump. JSON record shows the saved flow or account/unit starting balance; Data > View Schema shows the tables.

| Value | Meaning |
| --- | --- |
| Account name | Account identity. Capitalization and surrounding spaces do not create another account. |
| Units | Required text. Surrounding spaces are removed; case is meaningful: mW and MW differ. Unit labels are not converted. |
| Amount | Positive exact decimal string, not cents or a binary float. `.125` stores as `"0.125"`; `12.00` stores as `"12"`. Removing trailing zeros does not change quantity. |
| Starting balance | Exact signed decimal string, or null when unknown. Each account/unit pair has its own starting time. |
| Date | One local Date/Time field. Date-only means midnight; optional 24-hour time is stored to seconds. Missing components use the existing defaults. |
| Flow ID | UUID preserves editing/import identity. Ctrl+V inserts a new ID while preserving every other field. |
| Category / tags | One category and a list of tag names. Shared tags can connect related flows. |

## GUI and repository

Default form and ledger order is Date, Name, From, To, Amount, Units, Status, Category, Tags, Notes. New drafts default to today, USD, Complete and Uncategorized. Units uses the existing type/filter/Enter dropdown and removable suggestions. New units become choices after a flow or starting balance saves them.

The upper form and inline cell editor use the same variables; Notes editors share a text buffer. Existing flows save a completed valid field on Enter, Tab or leaving it. Notes uses Ctrl+Enter or leaving/closing. Ctrl+S finishes existing edits and does not add new drafts. New Flow clears; Add Flow inserts a draft. Ledger Ctrl+C copies the full saved row as JSON; Ctrl+V discards the copied ID and immediately saves/selects a new record. Text editors retain normal text copy/paste. Row/page changes and blank ledger clicks discard unadded drafts without a prompt. Validation is silent and leaves saved records unchanged.

Accounts has Name, Units, Starting Balance and Starting Date/Time. Its rows identify account/unit pairs; Name and Units are fixed while editing that pair. Add Account prepares another pair. Starting Balance and Date remain editable from either the form or the row. Each pair gets independent Confirmed and Including Pending balances. New endpoint/unit pairs start unknown until a starting balance is supplied.

`save_flow()` validates amount, units, endpoints, date, status and metadata, then writes the flow, its endpoint/unit anchors, choices, tags and audit atomically. Failed writes roll back. `save_account()` changes only the identified account/unit anchor. No calculated balance is stored as a record field.

Names shown in From/To come from saved endpoints. Accounts and graph selections show saved endpoint/unit pairs, regardless of status or ledger filters. Removing the last reference hides an unused pair; its stored starting balance remains available for reuse and undo. Preparing an anchor alone does not make it appear in those views.

## SQLite schema 6: seven tables

| Table | Every stored column | Purpose |
| --- | --- | --- |
| accounts | account_key, name | Account names. |
| account_balances | account_key, units, opening_amount, opening_at | One starting balance/time per account/unit pair. |
| flows | flow_id, date, from_key, to_key, amount, units, status, name, notes, category_key | One quantified transfer. |
| categories | category_key, name | Learned categories. |
| tags | tag_key, name | Learned tags. |
| flow_tags | flow_id, tag_key, position | Tag assignments and order. |
| audit | audit_id, changed_at, entity, entity_key, action, before_json, after_json | Append-only before/after history. |

Named keys use name.strip().casefold(); units keep exact spelling. The account_balances primary key is (account_key, units), and each flow references matching source and destination pairs. Amounts are canonical decimal TEXT. Python decimal validation rejects malformed/nonfinite quantities, floats, zero/negative flows and missing units. Full SQL constraints/indexes are in `flowledger/schema.sql`.

## Calculations and graphs

An eligible flow adds its amount to To and subtracts it from From, in that flow's unit only. Confirmed includes Complete; Including Pending adds Pending; Including Planned also adds Planned. Canceled contributes nothing. Refunds reverse endpoints. There are no exchange rates or implicit unit conversions.

Each anchor describes the confirmed balance immediately before flows at its own timestamp. Earlier Complete flows are reflected in that snapshot; earlier eligible Pending/Planned effects enter estimates once. Unknown anchors remain unknown. Corrections to completed history may require adjusting the anchor. Exact arithmetic uses sufficient Decimal precision for all operands, independently of the caller's default precision.

Graph selections identify account/unit pairs. Units have separate plots on one time range; Combine sums selected accounts only within each unit. Unknown pairs are identified explicitly, and an unknown constituent makes only its own combined unit unknown. A transfer cancels when both endpoints in the same unit are selected.

A flow at the graph start has before/after values at that same timestamp. Hover uses exact quantity/unit values, including both sides of a jump. Plot drawing uses Matplotlib floating-point coordinates; stored values and calculations do not. Ledger and category totals group by units.

## Experimental flow map

Experimental reads saved flows matching the current Flow Ledger filters, then narrows events to its own date range, exact-case unit and status basis. Confirmed includes Complete; Including Pending adds Pending; Including Planned adds Planned. Canceled never creates an animation event. Identical timestamps form one step, so simultaneous records do not acquire an invented order. Previous/Next and Play/Pause move between these groups. Node labels are existing account names; the moving pulse gives relative visual scale and its text gives the exact amount/unit. Dragged positions are session-only UI state.

The map writes no account or flow records, starting balances, session audit or layout preferences. It adds no object field, table, migration or backup. Ledger filters still control the ledger and the existing balance graphs keep their own semantics; the map shows only its eligible subset.

## Session history and view state

SessionHistory groups successful writes, including account_balances, into atomic undo actions using temporary SQLite triggers. Undo/redo restores changed rows and foreign keys, retains IDs and appends audit events. It does not rewrite old audit or add persistent undo tables. Import is one action. Paste is one action; later edits to the copy are separate actions.

Repository.delete_flow() atomically removes a flow and its tag links, appending audit before/after history. SessionHistory restores the exact ID and all fields on undo; redo removes them again. Starting balances remain stored, while referenced account/unit lists refresh. Canceled is chosen in Status and contributes nothing to balances/graphs; there is no action button. The Delete key targets the ledger; text editors use Delete for text.

Six ledger filters cover From, To, Status, Categories, Tags and Units. Empty captions use <label>: All; hover lists every selection. Units match exact case, all selected values within one list are ORed, and different lists combine. Selection/search changes use the same session history.

ViewHistory keeps draft fields, clearing, discarded drafts, filters/search, sorting/dates, graph pair selections/controls/plot/line visibility/axis limits and column changes in the same session order. Restoring a view preserves unfinished record text. Consecutive typing in one focused field is one step; new actions clear redo. Popup and Notes shortcuts use the same history. Closing/restoring ends the session; saved records stay saved.

ColumnDrag changes displaycolumns, keeping stored field order stable. Hit testing and Tab follow visible field order. Header clicks still sort and edges resize. Order and widths are session state. Read-only navigation, scrolling and file exports do not create undo actions.

Dropdown removal is a UI preference in `<database>.suggestions.json`, including the unit kind. Unit removal compares exact case; other named choices ignore case. Removal never edits records, balances or graphs. Type the value and press Enter to restore it. Ledger checkbox selections are temporary restrictions; OR within a list, AND across lists/search/dates. Choice search preserves hidden checks.

## Migration and import/export

Opening schema 5 changes the active status Void to Canceled and updates its SQLite constraint in one transaction. It creates **no backup file**, whether Void records exist or not. Failure rolls back data, schema and version together. IDs, dates, quantities, anchors, notes, categories, tags and historical audit remain unchanged. No object fields or tables are added. Reopening schema 6 needs no migration.

Older schema 1–4 migrations still save `<database>.pre-v6-<timestamp>.bak` before converting cents/anchors to USD and removing obsolete fields. Old named forecasts remain in that backup and recovery JSON, outside active flows. Those migrations do not run on schema 5. Standard database/backup filenames are Git-ignored and excluded from packages/snapshots. Existing backups are left alone. Manual Backup and pre-restore safety copies keep their existing behavior.

Git rollback restores code, not a migrated database; schema 5 code cannot open schema 6.

Legacy Void status imports as Canceled. CSV/JSON export includes ten fields plus flow ID in the default order. Imports require units and exact quantities; add USD to older files without units or re-export the migrated database. amount_cents, scenario and time_specified imports are rejected. Duplicate detection includes exact units and canonical quantities. Import preview/conflicts and atomic commit rules remain.

This release supports any unit labels, multiple per-unit anchors, local timestamps and one computer. Forecast collection controls and automatic unit conversion are outside the current implementation. Update/export commands retain their established single-command workflow.

## Display precision

`flowledger/units.py` contains `UNIT_DECIMAL_PLACES = {"USD": 2}` and `DEFAULT_DECIMAL_PLACES = 4`. This is a code-only display setting, with exact-case unit keys. Readonly ledger/account values, totals, import previews and graph ticks use it. Editors, JSON/export and graph hover retain full precision. Formatting rounds only the visible string; saved quantities and calculations stay exact.

Closing finishes valid edits to existing records. New drafts still require Add Flow/Add Account; invalid edits are not saved.
