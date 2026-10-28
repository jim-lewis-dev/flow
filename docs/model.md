# Data model

Flow represents a transfer as one quantity moving from one named account to a destination account. The same account may hold several units. There is one starting balance and timestamp for each account/unit pair; calculated balances are derived from those anchors and the flow ledger.

## Records

An account/unit pair has a starting date/time and an optional starting balance:

```json
{
  "name": "Warehouse",
  "units": "kg",
  "opening_amount": "20",
  "opening_at": "2026-10-01 00:00:00"
}
```

A saved flow has the ten user-facing fields plus its internal ID:

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

The entry form requires Date, From, To, Amount, and Units. Date defaults to today and Units to USD. Time, Name, Tags, and Notes may be omitted; a blank Name becomes `From -> To`. Status defaults to Complete and Category to Uncategorized. Suggestions reflect saved flows rather than unsaved typing.

Account names are identities, matched after trimming whitespace and case folding. Unit names are trimmed but retain case: `mW` and `MW` differ. Amount must be a finite decimal and may be positive, zero, or negative; a starting balance can also be unknown (`null`). Decimal strings avoid binary float rounding and preserve arbitrary supported precision. Read-only displays show USD to two places and other units to four by default; balance calculations and record values remain exact.

GUI amounts and starting balances accept `+`, `-`, `*`, `/`, `^`, parentheses, and unary signs; a leading `=` is optional. Expressions resolve on Enter, leaving the field, or adding the record; only the computed decimal is stored. Arithmetic on rational values uses exact fractions. Integer powers and rational roots remain exact; other fractional powers use high-precision decimal approximation, which carries into subsequent arithmetic. Repeating or approximate final results round half-even to at least 50 significant digits, increased to the total number of digits in the entered literals when larger. CSV/JSON and the repository API still require decimal values rather than expressions.

Powers bind before multiplication and unary signs and group from the right: `2^3^2 = 512`, `-2^2 = -4`, and `(-2)^2 = 4`. Negative exponents are supported (`2^-3 = 0.125`). Fractional exponents accept real roots, including odd roots of negative values when expressed as a fraction (`(-8)^(1/3) = -2`). `0^0`, negative powers of zero, non-real results, and calculations exceeding the entry limits produce field errors without changing the saved value.

Dates are local wall-clock times. Months and days need no leading zero. A date without a time denotes midnight; an entered time can include seconds. Graph date entries keep text unchanged while you type and format it on Enter or when you leave the field. There is no separate flag indicating whether a time was typed. A flow ID is generated when a record is created and remains stable through editing; copying a flow creates a new ID.

Multiple selected flow rows can be copied, pasted, or deleted together. Each pasted row receives a fresh ID; the whole paste or deletion is one transaction and one session undo action. Editing the top form or an inline cell changes only the active row. Equal full sort keys retain their current view order; exact copies are placed after their sources within that order for the session. No ordering field is added to records. The row stays in place while its fields are being edited. On leaving the row, sorting resumes and keeps the selection visible; a row that no longer matches the filters is then hidden with feedback.

The default ledger order is Date/Time (newest first), then From, To, Amount, Units, Status, Category, and Name (ascending). Amounts compare as exact decimals; account, category, and name text is case-insensitive, while unit labels retain case. Status order is Complete, Pending, Planned, Canceled. A chosen column becomes primary, followed by the other fields in that same sequence; reversing affects only the primary column. The repository API uses the same keys with chronological ascending dates.

## Balance calculation

For a flow of quantity `q` and unit `u`:

| Pair | Change |
| --- | ---: |
| `(from, u)` | `-q` |
| `(to, u)` | `+q` |
| Every other unit | `0` |

Within a unit, the sum across all accounts is unchanged by a transfer. No conversion or addition across units occurs. The starting balance is the confirmed value *immediately before flows at its timestamp*. Earlier completed activity is already reflected in that anchor. If you edit completed history from before the anchor, revisit the anchor as needed.

A negative `q` reverses the effect while preserving the recorded account names: `A → B: -5 kg` adds 5 kg to A and removes 5 kg from B. Zero changes no balances and retains the flow's date, status, and notes. From and To remain required but may be the same account. In that case the balance change is zero, both directional totals include the quantity, and category totals count the record once. Category totals sum signed amounts within each category/status/unit group.

Confirmed includes Complete flows. Including Pending also includes Pending; Including Planned adds Planned. Canceled contributes nothing. Graphs create separate plots for different units, and a combined line sums selected accounts only within one unit. An unknown starting balance prevents a trustworthy line for that pair; it is not silently treated as zero.

Accounts calculates both displayed balances at its **As of** date/time, initially the current timestamp. The displayed instant stays fixed until edited; a blank value or `Now` resolves to the current timestamp on Enter or when leaving the field. An explicit future timestamp includes eligible flows through that instant. This is a session view setting, not a field in an account or flow record.

## SQLite

The current schema has eight persistent tables; [`schema.sql`](../flowledger/schema.sql) defines their columns and constraints.

| Table | Role |
| --- | --- |
| `accounts` | Names and identity keys. |
| `account_balances` | Starting amount/time keyed by account and unit. |
| `flows` | Quantified transfers and their metadata. |
| `categories` | Reusable category names. |
| `tags` | Reusable tag names. |
| `flow_tags` | Ordered tag assignments. |
| `audit` | Before/after record of saved changes. |
| `account_positions` | Saved map coordinates, keyed by account identity. |

`Repository.save_flow` validates and writes a flow, its endpoint/unit entries, tags, and audit entry in one SQLite transaction. Failed writes roll back together. Session undo/redo can restore record and view changes but is not a permanent history of UI actions. The audit table preserves saved record changes.

Account lists, learned suggestions, and ledger filters use current saved flow references, including Canceled flows. The entry form also offers the default Uncategorized category and all valid statuses. Removing the last reference removes that choice from the lists and clears its filter selection. Stored names and starting balances remain available internally for undo or later reuse.

CSV/JSON import requires units. The preview distinguishes new records, duplicates, conflicts, similar records, and invalid rows; approved entries commit atomically. Data export contains flows; use database backup to preserve anchors, audit, and map positions as well. Old schema files are migrated when opened; the legacy migrations that convert cents to quantities retain a one-time copy of the old database.

The Experimental tab reads matching saved flows and animates them without changing flow records. Dropping a node saves the map positions in the same database; session undo/redo saves the restored layout too. Coordinates are normalized to the map dimensions and can extend outside the original canvas. Each account has one position shared across units. New accounts receive an automatic position; filtering never discards the stored arrangement. Zoom, playback, and filter settings remain session-only. Database backup and restore include the layout. Negative pulses move in the reverse direction and use absolute magnitude for sizing; zero events use stationary hollow markers. Account hover balances come from all eligible saved flows at the selected time; its displayed In/Out totals use only flows selected for the animation, with actual transfer direction and nonnegative magnitudes.
