# Flow software specification

10 October 2026 | Schema 6

## Objects and editing

01 / Flow - 10 October 2026

A flow subtracts a quantity from one named account and adds it to another, in one unit. Accounts can hold several units; each account/unit pair has its own starting balance and time.

```text
{
  "name": "Warehouse", "units": "kg",
  "opening_amount": "20",
  "opening_at": "2026-10-01 00:00:00"
}
```

```text
{
  "flow_id": "22222222-2222-4222-8222-222222222222",
  "date": "2026-10-03 09:15:06", "name": "Materials",
  "from_account": "Warehouse", "to_account": "Workshop",
  "amount": "0.125", "units": "kg", "status": "Complete",
  "category": "Materials", "tags": ["Project"],
  "notes": "Exact fractional quantity"
}
```

| Representation | Meaning |
| --- | --- |
| Account / units | Account names identify holders. Unit labels are required and case-sensitive: mW and MW differ. No implicit conversion. Starting balance is signed decimal text, or null when unknown. |
| Amount / flow ID | Exact decimal text, without a cents limit. .125 stores as 0.125; 12.00 stores as 12. UUID preserves edits/imports; Paste inserts a new UUID immediately. |
| Date | One Date/Time entry, optional 24-hour time to seconds. Leading zeros optional. Missing month/day = 1; missing hour/minute/second = 0. Date-only means midnight. |
| Category / tags | One category and a list of tag names. Shared tags can connect related transfers. Ten agreed flow fields plus ID. |

### The GUI edits these objects

Default order: Date, Name, From, To, Amount, Units, Status, Category, Tags, Notes. Defaults: today, USD, Complete and Uncategorized. Units uses the learned dropdown with filtering, Enter and X. Accounts has Name, Units, Starting Balance and Starting Date/Time.

Existing records save completed fields on Enter, Tab or leaving them. Ctrl+S finishes existing edits; new drafts wait for Add. Notes: Ctrl+Enter or leaving/closing saves. Invalid edits stay unsaved with a silent error. New clears; ledger Ctrl+C/V inserts a full copy under a new ID. Delete removes a selected row without confirmation; undo restores its record. Delete inside text edits text.

Row/page changes and blank ledger clicks discard unadded drafts without prompts; undo restores them. Drag titles to move columns; cell editing and Tab follow their visible order. Account Name/Units identify a row; starting balance/date remain editable. Starting Balance enters with the caret at the end.

Filters: From, To, Status, Categories, Tags and Units. Checkboxes select multiple values; hover lists every selection. Filter selections and choice search support session undo/redo. Unit matches keep exact case.

Actual objects and SQL rows: examples/record_objects.json and examples/review.sql. Source walkthrough: docs/model_review.md. Controls and update/export commands: README.md.

## Storage and calculations

02 / Schema 6 - seven tables

Account/category/tag keys use name.strip().casefold(), retaining display spelling. Unit labels keep exact case. Amounts are canonical decimal TEXT; Python Decimal arithmetic uses sufficient precision for every operand.

| Table | Every stored column |
| --- | --- |
| accounts | account_key, name |
| account_balances | account_key, units, opening_amount, opening_at |
| flows | flow_id, date, from_key, to_key, amount, units, status, name, notes, category_key |
| categories | category_key, name |
| tags | tag_key, name |
| flow_tags | flow_id, tag_key, position |
| audit | audit_id, changed_at, entity, entity_key, action, before_json, after_json |

account_balances is keyed by (account_key, units); each flow references matching endpoint/unit pairs. Lists show pairs used by saved flows, including Canceled. Unreferenced anchors remain stored for reuse/undo. audit retains before/after history. SQL: flowledger/schema.sql.

### Save, calculate and inspect

GUI draft -> Repository.save_flow() -> validate -> write flow, endpoint/unit anchors, choices, tags and audit atomically. Failed writes roll back. Ctrl+Z/Ctrl+Y reverse records/deletion, drafts, filters, searches, graph controls and all plot/column changes in one session order.

Within its unit, a flow contributes +amount to To and -amount to From. Confirmed includes Complete; Including Pending adds Pending; Including Planned adds Planned. Canceled contributes zero. Each pair has a confirmed anchor immediately before events at its own timestamp; unknown remains unknown.

Select account/unit pairs to graph. Different units have separate plots sharing time. Combine sums selected accounts within each unit only. Hover gives exact quantity/unit; drawing uses floating-point coordinates. A transfer cancels if both endpoints are selected in its unit. Readonly display: USD 2 decimals, others 4 (flowledger/units.py). Editors, JSON and hover keep exact values.

Experimental animates ledger-filtered flows. First/last jump; previous/next step by timestamp; play/pause speed runs 0 to 5 (0 pauses). Hover pulses for flow details, accounts for saved anchors, balances from all flows and shown-flow In/Out totals. Canceled is excluded; node placement lasts this session. No data or schema changes.

### Migration and scope

Schema 5 -> 6 changes Void to Canceled in one transaction, without a backup file. Failure rolls back schema and data. IDs, quantities, anchors, metadata and audit remain exact. Older schema 1-4 conversions retain their one-time backup/recovery. Databases and backups stay out of Git and project snapshots.

Import/export: ten fields plus ID; units required. Git rollback changes code, not records; schema 5 code cannot open schema 6. Scope: local timestamps and per-unit anchors on one computer; no automatic conversions or forecast collections.
