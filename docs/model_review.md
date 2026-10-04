# Flow 0.5.6: model review

The project snapshot exporter adds no fields or tables. It exports source files and Git information; it excludes the database.

Versions 0.5.4–0.5.5 add the ZIP updater, local Git workflow and removal of committed downloads. Account and Flow objects and the database schema are unchanged.

A flow subtracts from one named account and adds to another. The application uses ordinary Python dictionaries and one SQLite repository.

## Actual objects

`Repository.get_account("AmEx")` returns:

```json
{
  "name": "AmEx",
  "opening_cents": 0,
  "opening_at": "2026-10-01 00:00:00"
}
```

`Repository.get_flow(flow_id)` returns:

```json
{
  "flow_id": "11111111-1111-4111-8111-111111111111",
  "date": "2026-10-02 12:30:00",
  "from_account": "AmEx",
  "to_account": "Joe's Diner",
  "amount_cents": 1200,
  "status": "Complete",
  "name": "Friday lunch",
  "notes": "Burger and fries",
  "category": "Dining",
  "tags": ["Road trip"]
}
```

That is the full flow object: the nine agreed fields plus its ID. No `scenario` or `time_specified` field. Repository-generated examples are in `examples/record_objects.json`; `examples/review.sql` contains their stored rows.

| Representation | Meaning |
| --- | --- |
| Account name | Identity; no generated account ID. A starting balance is integer cents, negative for debt, or `null` when unknown. |
| Flow ID | UUID preserves identity during edits/imports. Repeat creates a new record with a new ID. |
| Amount | `1200` means $12.00, calculated with exact integer arithmetic. The form displays dollars. |
| Date | `2026-10-02` or `2026-10-02 12:30:00`. Entered time normalizes to seconds. A date alone evaluates to midnight without adding a flag. |
| Category / tags | One category name and a list of tag names. Shared tags can connect split purchases. |

Every date denotes a point in local time; a date-only value is not an all-day interval. Sorting and calculations compare the interpreted timestamps, not their textual lengths. Account opening times are stored as full timestamps. Timezone conversion and fractional seconds are outside this version.

## GUI to stored record

The nine form fields populate a draft dictionary. Inline and expanded Notes share one text buffer; closing either view retains the draft. **Save** commits the whole flow; **Cancel** discards the draft. Closing Notes is not a separate database save.

`save_flow(draft)` validates endpoints, amount, date and status; creates new named choices; writes the flow and tags; and appends an audit entry in one SQLite transaction. Failed saves roll back together. The method returns the saved dictionary. Clicking a ledger row loads that dictionary into the form. **Record JSON** shows saved records; **Data > View Schema** shows the actual tables.

Accounts has Name, Starting Balance and Starting Date with optional time. Newly typed endpoints have unknown opening balances until supplied. Balances and graphs are calculated outputs, not extra account fields. There are no ownership or archive controls.

## SQLite schema 4: six tables

| Table | Every stored column | Purpose |
| --- | --- | --- |
| `accounts` | `account_key`, `name`, `opening_cents`, `opening_at` | Named starting balances. |
| `flows` | `flow_id`, `date`, `from_key`, `to_key`, `amount_cents`, `status`, `name`, `notes`, `category_key` | One movement per row. |
| `categories` | `category_key`, `name` | Learned category choices. |
| `tags` | `tag_key`, `name` | Learned tag choices. |
| `flow_tags` | `flow_id`, `tag_key`, `position` | Tag assignments and their order. |
| `audit` | `audit_id`, `changed_at`, `entity`, `entity_key`, `action`, `before_json`, `after_json` | Saved before/after history. |

Named keys use `name.strip().casefold()`: `"AmEx"` becomes `"amex"`. They support case-insensitive matching and references; they are not separate generated identities. Display spelling remains in `name`. `audit_id` is an internal log sequence. Full constraints and indexes are in `flowledger/schema.sql`.

## Calculations

An eligible flow contributes `+amount_cents` to its destination and `-amount_cents` to its source. Confirmed includes Complete; Including Pending adds Pending; Including Planned also adds Planned. Void contributes zero. Settle Pending by editing the same record; a refund is a reverse flow.

Each account has one confirmed starting balance immediately before flows at its timestamp. Earlier Complete flows are already reflected in that snapshot; eligible earlier Pending/Planned flows enter estimates once. Unknown starting balances remain unknown. Changing completed history may require correcting the starting balance.

Graphs compare or combine explicitly selected accounts. A transfer cancels within a sum containing both endpoints. Selecting every endpoint conserves the total; that total is not automatically net worth. Category totals count flow volume, including transfers.

## Upgrade and scope

Version 0.5 uses schema 4. Before upgrading schema 1-3, it saves a full `<database>.pre-v4-<timestamp>.bak`. Ordinary records previously labeled Baseline remain active with their IDs, and all accounts and audit history remain. Optional-time flags become date precision and are removed. Schema 1 midnight values become date-only because that version did not record whether midnight was entered.

Old named hypothetical collections remain recoverable in the full backup and a neighboring `.bak.forecasts.json` file; they are not part of the active ledger. Legacy audit JSON remains unchanged. Old import files containing scenario or time-flag columns are rejected; migrate the database and export again.

This release implements accounts, individual flows, filtering, graphs and import/export. Forecast collections and simulation controls are deferred. Current limits are USD cents, one starting balance per account, local timestamps and one computer.

## Dropdown preferences

Version 0.5.1 adds an X beside learned entry suggestions. It changes no object fields or SQLite tables. A separate `<database>.suggestions.json` file remembers removed names, grouped by account, category, tag and flow name. Account suggestions are shared by From and To; these are UI preferences, not properties of an account or flow.

Removal filters entry suggestions only. Saved records, balances, account lists, graphs and ledger filters retain the original values. Refreshing or saving an existing record does not restore a removed suggestion; explicitly typing its value and pressing Enter does. Preferences are written atomically, and failed writes leave the current list unchanged. Database backups do not include this UI preferences file.

Version 0.5.2 adds multiple checkbox selections to ledger Status, Category and Tag filters. These are temporary view selections, not stored record fields. Matching uses OR within each list and AND between lists, search and dates; a tag match requires any selected tag. Empty selections mean unrestricted. Save and Cancel reset the entry form, selection and focused ledger row while retaining these filters.

Version 0.5.3 changes graph presentation without changing stored objects or balance calculations. `graph_timeline` adds a before-flow point at From when an eligible flow occurs exactly there, followed by the ordinary after-flow point at the same timestamp. The before value is the after value minus the net effect of those simultaneous flows. Combined graphs sum aligned points, retaining that initial pair. Hover reads these integer-cent values, never interpolated money; a vertical jump displays before and after. Calendar selections and fullscreen state remain interface state only.
