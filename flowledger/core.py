"""Exact flow arithmetic and transactional SQLite storage.

The database is authoritative. Each flow subtracts its signed exact quantity
from its source and adds it to its destination, in the same case-sensitive units.
An account can have an independent starting balance for each unit. Quantities
are decimal text in SQLite and Decimal values in arithmetic, without rounding.

Dates retain optional local wall-clock times to whole seconds, without offsets.
A date-only record is interpreted as midnight for calculations. An account
anchor describes its balance immediately BEFORE flows at that time.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date as Date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Iterator
import uuid

STATUSES = ("Complete", "Pending", "Planned", "Canceled")
FLOW_SORT_FIELDS = ("date", "from_account", "to_account", "amount", "units",
                    "status", "category", "name")
DEFAULT_ANCHOR = "2026-10-01 00:00:00"
SCHEMA_VERSION = 8
APPLICATION_ID = 1179406167


def _name(value: Any, label: str = "Name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    result = value.strip()
    if "\x00" in result:
        raise ValueError(f"{label} cannot contain a null character.")
    return result


def _key(value: str) -> str:
    return value.strip().casefold()


def parse_quantity(text: Any, allow_negative: bool = True) -> Decimal:
    """Read an exact quantity without assuming currency or decimal precision.

    Plain decimal text, correctly grouped commas and omitted leading zeros are
    accepted, including zero and negatives. Binary floats, currency symbols
    and exponent notation are refused.
    Decimal objects are accepted for internal arithmetic without conversion loss.
    """
    if isinstance(text, bool) or isinstance(text, float) or not isinstance(text, (str, int, Decimal)):
        raise ValueError("Use an exact decimal quantity, such as 12.345.")
    if isinstance(text, Decimal):
        amount = text
    else:
        value = str(text).strip()
        if not re.fullmatch(r"[+-]?(?:(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?|\.\d+)", value):
            raise ValueError("Enter a decimal quantity, such as 12.345.")
        try:
            amount = Decimal(value.replace(",", ""))
        except InvalidOperation as error:
            raise ValueError("Invalid quantity.") from error
    if not amount.is_finite():
        raise ValueError("Quantity must be finite.")
    if amount < 0 and not allow_negative:
        raise ValueError("Quantity cannot be negative.")
    return amount


def quantity_text(value: Any) -> str:
    """Canonical exact decimal storage text; no rounding or exponent notation."""
    amount = parse_quantity(value, allow_negative=True)
    if amount.is_zero():
        return "0"
    text = format(amount, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_quantity(value: Any) -> str:
    """Readable exact quantity with comma grouping and no currency symbol."""
    amount = parse_quantity(value, allow_negative=True)
    if amount.is_zero():
        return "0"
    text = format(amount, ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def decimal_sum(values: Iterable[Any]) -> Decimal:
    """Sum decimal quantities exactly, independently of the ambient context."""
    amounts = [parse_quantity(value, allow_negative=True) for value in values]
    if not amounts:
        return Decimal(0)
    least = min(amount.as_tuple().exponent for amount in amounts)
    most = max(len(amount.as_tuple().digits) + amount.as_tuple().exponent for amount in amounts)
    with localcontext() as context:
        context.prec = max(1, most - least) + len(str(len(amounts))) + 1
        context.Emax = max(context.Emax, most + 2)
        context.Emin = min(context.Emin, least - 2)
        return sum(amounts, Decimal(0))


def normalize_date(value: Any) -> str:
    """Normalize a year-first local point in time, filling omitted components.

    Month/day default to 1; hour/minute/second default to 0. Leading zeros are
    optional, time uses 24-hour notation, and a space or T separates date/time.
    Offsets and fractional seconds are rejected rather than silently lost.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.microsecond:
            raise ValueError("Use local time to whole seconds, without a timezone offset.")
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Date):
        return value.isoformat() + " 00:00:00"
    match = re.fullmatch(
        r"([0-9]{4})(?:-([0-9]{1,2})(?:-([0-9]{1,2}))?)?"
        r"(?:[ T]([0-9]{1,2})(?::([0-9]{1,2})(?::([0-9]{1,2}))?)?)?",
        value.strip() if isinstance(value, str) else "")
    if match is None:
        raise ValueError("Use YYYY-MM-DD with optional 24-hour time. Leading zeros and trailing components are optional.")
    try:
        year, month, day, hour, minute, second = match.groups()
        parsed = datetime(int(year), int(month or 1), int(day or 1),
                          int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError as error:
        raise ValueError("Invalid calendar date or time.") from error
    return parsed.isoformat(sep=" ", timespec="seconds")


def normalize_flow_date(value: Any) -> str:
    """Preserve a date-only value; normalize an entered time to whole seconds."""
    when = normalize_date(value)
    has_time = isinstance(value, datetime) or (
        isinstance(value, str) and bool(re.search(r"[ T]", value.strip())))
    return when if has_time else when[:10]


def flow_sort_key(flow: dict) -> tuple:
    """Order saved flows by their contents, with exact decimal quantities.

    Date-only values compare as midnight. Account, category and name text is
    case-insensitive; units retain their case-sensitive identity. Statuses use
    their normal Complete/Pending/Planned/Canceled order. Identical keys keep
    their input order; flow IDs, notes and tags do not affect placement.
    """
    return (normalize_date(flow["date"]), _key(flow["from_account"]),
            _key(flow["to_account"]), parse_quantity(flow["amount"]),
            flow["units"], STATUSES.index(flow["status"]),
            _key(flow["category"]), _key(flow["name"]))


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


class Repository:
    """Local SQLite repository with atomic writes and an automatic change log.

    Names identify accounts, categories and tags. Keys are internal
    case-folded forms of names, not additional identifiers users must manage.
    Use atomic() to group several save calls into one all-or-nothing operation.
    """

    def __init__(self, path: str | Path, *, preflight=None):
        """With preflight, open an existing file and check this connection before any schema changes."""

        self.sync = None
        self.path = str(path)
        if not self.path:
            raise ValueError("Database path cannot be empty.")
        if self.path != ":memory:":
            target = Path(self.path).expanduser().resolve()
            if preflight is None:
                target.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(target)
        if preflight is not None and self.path == ":memory:":
            raise ValueError("A preflight check requires an existing file.")
        destination = Path(self.path).as_uri() + "?mode=rw" if preflight is not None else self.path
        self.conn = sqlite3.connect(destination, isolation_level=None, uri=preflight is not None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        try:
            if preflight is not None:
                preflight(self)
            tables = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if (not tables and self.conn.execute("PRAGMA application_id").fetchone()[0] == 0
                    and self.conn.execute("PRAGMA user_version").fetchone()[0] == 0):
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                self.conn.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
            self._validate_database(self.conn)
            if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
                from .sync import SyncState
                SyncState(self)
        except Exception:
            self.conn.close()
            raise

    @staticmethod
    def _validate_database(conn: sqlite3.Connection) -> None:
        if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("This is not a Flowledger database.")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported database version {version}; expected {SCHEMA_VERSION}.")
        required = {
            "accounts": {"account_key", "name"},
            "account_balances": {"account_key", "units", "opening_amount", "opening_at"},
            "account_positions": {"account_key", "x", "y"},
            "categories": {"category_key", "name"}, "tags": {"tag_key", "name"},
            "flows": {"flow_id", "date", "from_key", "to_key", "amount", "units",
                      "status", "name", "notes", "category_key"},
            "flow_tags": {"flow_id", "tag_key", "position"},
            "audit": {"audit_id", "changed_at", "entity", "entity_key", "action", "before_json", "after_json"},
        }
        for table, columns in required.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise ValueError(f"Database is missing required {table} fields.")
        if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("Database integrity check failed.")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Database contains broken account or flow references.")
        for units, amount in conn.execute("SELECT units,amount FROM flows"):
            if _name(units, "Units") != units or quantity_text(amount) != amount:
                raise ValueError("Database contains an invalid flow quantity or units.")
        for units, amount, when in conn.execute("SELECT units,opening_amount,opening_at FROM account_balances"):
            if _name(units, "Units") != units or (amount is not None and quantity_text(amount) != amount):
                raise ValueError("Database contains an invalid starting balance or units.")
            normalize_date(when)
        for row in conn.execute("SELECT x,y FROM account_positions"):
            if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in row):
                raise ValueError("Database contains an invalid account position.")
        from .sync import require_supported_settings
        require_supported_settings(conn)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Commit all enclosed writes together; nest safely using savepoints."""
        outer = not self.conn.in_transaction
        if outer and self.sync is None:
            # Sharing can be enabled by the network worker while an editor
            # still has this database open. Arm its capture before writing.
            if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
                from .sync import SyncState
                SyncState(self)
        if outer and self.sync is not None:
            self.sync._start_capture()
        marker = "flow_" + uuid.uuid4().hex
        self.conn.execute(f"SAVEPOINT {marker}")
        try:
            yield
            if outer and self.sync is not None:
                self.sync._finish_capture()
            self.conn.execute(f"RELEASE SAVEPOINT {marker}")
        except BaseException:
            # RELEASE can itself fail (a deferred foreign key or busy writer).
            # Roll back the records and their journal before returning control.
            if self.conn.in_transaction:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {marker}")
                self.conn.execute(f"RELEASE SAVEPOINT {marker}")
            raise
        finally:
            if outer and self.sync is not None:
                self.sync._stop_capture()

    def _audit(self, entity: str, key: str, action: str, before: Any, after: Any) -> None:
        self.conn.execute(
            "INSERT INTO audit(changed_at,entity,entity_key,action,before_json,after_json) VALUES(?,?,?,?,?,?)",
            (datetime.now().isoformat(sep=" ", timespec="microseconds"), entity, key, action, _json(before), _json(after)),
        )

    @staticmethod
    def _account(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {"name": row["name"], "units": row["units"],
                "opening_amount": row["opening_amount"], "opening_at": row["opening_at"]}

    def list_accounts(self, *, referenced_only: bool = False) -> list[dict]:
        """List account/unit anchors, optionally limited to saved endpoint pairs.

        Displayed account lists use referenced_only. Unreferenced starting
        balances remain stored for reuse and undo; ledger filters and flow
        statuses do not change whether an endpoint/unit pair is referenced.
        """
        query = "SELECT a.name,b.units,b.opening_amount,b.opening_at FROM accounts a JOIN account_balances b USING(account_key)"
        if referenced_only:
            query += " WHERE (a.account_key,b.units) IN (SELECT from_key,units FROM flows UNION SELECT to_key,units FROM flows)"
        return [self._account(row) for row in self.conn.execute(query + " ORDER BY a.account_key,b.units")]

    def get_account(self, name: str, units: str = "USD") -> dict | None:
        units = _name(units, "Units")
        return self._account(self.conn.execute(
            "SELECT a.name,b.units,b.opening_amount,b.opening_at FROM accounts a JOIN account_balances b USING(account_key) "
            "WHERE a.account_key=? AND b.units=?", (_key(name), units)).fetchone())

    def load_map_positions(self) -> dict[str, tuple[float, float]]:
        """Account-node positions shared by all units in this database."""
        return {name: (x, y) for name, x, y in self.conn.execute(
            "SELECT a.name,p.x,p.y FROM account_positions p JOIN accounts a USING(account_key) "
            "ORDER BY a.account_key")}

    def save_map_positions(self, positions: Mapping[str, tuple[float, float]]) -> None:
        """Replace the saved layout without editing flows, anchors or their audit.

        Coordinates are relative to the scene size and may extend past its initial
        bounds. Session view undo/redo calls this method with its restored layout.
        """
        if not isinstance(positions, Mapping):
            raise ValueError("Account positions must map names to coordinates.")
        normalized = {}
        for name, point in positions.items():
            key = _key(_name(name, "Account"))
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Each account position must contain x and y.")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point):
                raise ValueError("Account coordinates must be finite numbers.")
            try:
                point = tuple(float(value) for value in point)
            except (ValueError, OverflowError) as exc:
                raise ValueError("Account coordinates must be finite numbers.") from exc
            if not all(math.isfinite(value) for value in point):
                raise ValueError("Account coordinates must be finite numbers.")
            if key in normalized and normalized[key] != point:
                raise ValueError("An account cannot have two different positions.")
            normalized[key] = point
        with self.atomic():
            accounts = {row[0] for row in self.conn.execute("SELECT account_key FROM accounts")}
            if normalized.keys() - accounts:
                raise ValueError("Account positions must refer to existing accounts.")
            current = {key: (x, y) for key, x, y in self.conn.execute("SELECT account_key,x,y FROM account_positions")}
            for key in current.keys() - normalized.keys():
                self.conn.execute("DELETE FROM account_positions WHERE account_key=?", (key,))
            for key, point in normalized.items():
                if current.get(key) != point:
                    self.conn.execute("INSERT INTO account_positions VALUES(?,?,?) "
                                      "ON CONFLICT(account_key) DO UPDATE SET x=excluded.x,y=excluded.y",
                                      (key, *point))

    def save_account(self, name: str, opening_amount: Any | None = None,
                     opening_at: str = DEFAULT_ANCHOR, units: str = "USD") -> dict:
        name = _name(name, "Account")
        units = _name(units, "Units")
        when = normalize_date(opening_at)
        amount = None if opening_amount is None else quantity_text(opening_amount)
        with self.atomic():
            before = self.get_account(name, units)
            named = self.conn.execute("SELECT name FROM accounts WHERE account_key=?", (_key(name),)).fetchone()
            canonical = named[0] if named else name
            self.conn.execute("INSERT OR IGNORE INTO accounts(account_key,name) VALUES(?,?)", (_key(canonical), canonical))
            self.conn.execute(
                "INSERT INTO account_balances(account_key,units,opening_amount,opening_at) VALUES(?,?,?,?) "
                "ON CONFLICT(account_key,units) DO UPDATE SET "
                "opening_amount=excluded.opening_amount,opening_at=excluded.opening_at",
                (_key(canonical), units, amount, when),
            )
            after = self.get_account(canonical, units)
            if before != after:
                self._audit("account", f"{canonical} [{units}]", "update" if before else "create", before, after)
            return after

    def list_units(self, *, referenced_only: bool = False) -> list[str]:
        """Saved case-sensitive units; optionally only units used by flows."""
        if referenced_only:
            return [row[0] for row in self.conn.execute(
                "SELECT DISTINCT units FROM flows ORDER BY units")]
        return [row[0] for row in self.conn.execute(
            "SELECT units FROM flows UNION SELECT units FROM account_balances ORDER BY units")]

    def list_categories(self, *, referenced_only: bool = False) -> list[str]:
        query = "SELECT name FROM categories"
        if referenced_only:
            query += " WHERE category_key IN (SELECT category_key FROM flows)"
        return [row[0] for row in self.conn.execute(query + " ORDER BY category_key")]

    def list_tags(self, *, referenced_only: bool = False) -> list[str]:
        query = "SELECT name FROM tags"
        if referenced_only:
            query += " WHERE tag_key IN (SELECT tag_key FROM flow_tags)"
        return [row[0] for row in self.conn.execute(query + " ORDER BY tag_key")]

    def list_names(self) -> list[str]:
        names = {}
        for row in self.conn.execute("SELECT name FROM flows ORDER BY datetime(date),rowid"):
            names.setdefault(_key(row[0]), row[0])
        return sorted(names.values(), key=str.casefold)

    def save_flow(self, data: dict) -> dict:
        """Validate then atomically save one flow, its suggestions, and its audit.

        A supplied existing flow_id updates that record. A supplied new UUID
        preserves imported identity. No other fields are stored on a flow.
        """
        if not isinstance(data, dict):
            raise ValueError("A flow must be a mapping of fields.")
        allowed = {"flow_id", "date", "from_account", "to_account", "amount", "units",
                   "status", "name", "notes", "category", "tags"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("Unknown flow fields: " + ", ".join(sorted(map(str, unknown))))
        when = normalize_flow_date(data.get("date", Date.today()))
        source = _name(data.get("from_account"), "From account")
        destination = _name(data.get("to_account"), "To account")
        amount = parse_quantity(data.get("amount", ""))
        amount = quantity_text(amount)
        units = _name(data.get("units"), "Units")
        status = data.get("status", "Complete")
        if status not in STATUSES:
            raise ValueError("Status must be Complete, Pending, Planned, or Canceled.")
        name = data.get("name", "")
        notes = data.get("notes", "")
        category = data.get("category", "")
        if not all(isinstance(value, str) for value in (name, notes, category)):
            raise ValueError("Name, notes, and category must be text.")
        for label, value in (("Name", name), ("Notes", notes)):
            if "\x00" in value:
                raise ValueError(f"{label} cannot contain a null character.")
        category = _name(category or "Uncategorized", "Category") if category.strip() else "Uncategorized"
        raw_tags = data.get("tags", [])
        if not isinstance(raw_tags, (list, tuple)):
            raise ValueError("Tags must be a list of names.")
        tags = {}
        for tag in raw_tags:
            if not isinstance(tag, str):
                raise ValueError("Each tag must be text.")
            if tag.strip():
                tag = _name(tag, "Tag")
                tags.setdefault(_key(tag), tag)
        raw_id = data.get("flow_id")
        try:
            flow_id = str(uuid.UUID(str(raw_id))) if raw_id else str(uuid.uuid4())
        except (ValueError, AttributeError) as error:
            raise ValueError("Flow ID must be a valid UUID.") from error
        with self.atomic():
            before = self.get_flow(flow_id)
            for endpoint in (source, destination):
                if self.get_account(endpoint, units) is None:
                    self.save_account(endpoint, units=units)
            source = self.get_account(source, units)["name"]
            destination = self.get_account(destination, units)["name"]
            name = name.strip() or f"{source} -> {destination}"
            self.conn.execute("INSERT OR IGNORE INTO categories VALUES(?,?)", (_key(category), category))
            self.conn.execute(
                "INSERT INTO flows(flow_id,date,from_key,to_key,amount,units,status,name,notes,"
                "category_key) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(flow_id) DO UPDATE SET "
                "date=excluded.date,from_key=excluded.from_key,to_key=excluded.to_key,amount=excluded.amount,units=excluded.units,"
                "status=excluded.status,name=excluded.name,notes=excluded.notes,category_key=excluded.category_key",
                (flow_id, when, _key(source), _key(destination), amount, units, status, name, notes,
                 _key(category)),
            )
            self.conn.execute("DELETE FROM flow_tags WHERE flow_id=?", (flow_id,))
            for position, (key, tag) in enumerate(tags.items()):
                self.conn.execute("INSERT OR IGNORE INTO tags VALUES(?,?)", (key, tag))
                self.conn.execute("INSERT INTO flow_tags VALUES(?,?,?)", (flow_id, key, position))
            after = self.get_flow(flow_id)
            if before != after:
                self._audit("flow", flow_id, "update" if before else "create", before, after)
            return after

    _FLOW_QUERY = """SELECT f.flow_id,f.date,a.name AS from_account,b.name AS to_account,
        f.amount,f.units,f.status,f.name,f.notes,c.name AS category
        FROM flows f JOIN accounts a ON a.account_key=f.from_key
        JOIN accounts b ON b.account_key=f.to_key
        JOIN categories c ON c.category_key=f.category_key"""

    def _records(self, rows: Iterable[sqlite3.Row]) -> list[dict]:
        records = [dict(row) for row in rows]
        if not records:
            return []
        tags_by_flow = defaultdict(list)
        identities = [record["flow_id"] for record in records]
        for offset in range(0, len(identities), 900):
            batch = identities[offset:offset + 900]
            markers = ",".join("?" for _ in batch)
            for row in self.conn.execute(
                "SELECT ft.flow_id,t.name FROM flow_tags ft JOIN tags t ON t.tag_key=ft.tag_key "
                f"WHERE ft.flow_id IN ({markers}) ORDER BY ft.position", batch
            ):
                tags_by_flow[row[0]].append(row[1])
        for record in records:
            record["tags"] = tags_by_flow[record["flow_id"]]
        return records

    def list_flows(self) -> list[dict]:
        """List flows chronologically, breaking ties by their visible contents.

        Read insertion order first so fully identical keys remain stable.
        Python sorting preserves arbitrary decimal precision without a SQL
        float conversion, and identities never determine record placement.
        """
        records = self._records(self.conn.execute(self._FLOW_QUERY + " ORDER BY f.rowid"))
        return sorted(records, key=flow_sort_key)

    def get_flow(self, flow_id: str) -> dict | None:
        rows = self._records(self.conn.execute(self._FLOW_QUERY + " WHERE f.flow_id=?", (flow_id,)))
        return rows[0] if rows else None

    def delete_flow(self, flow_id: str) -> dict:
        """Atomically remove a flow and its tag links, retaining its audit.

        Return the deleted record. Account/unit starting balances and learned
        categories/tags remain stored for reuse. Use SessionHistory.perform
        around this action when session undo/redo is required.
        """
        with self.atomic():
            record = self.get_flow(flow_id)
            if record is None:
                raise ValueError("Unknown flow.")
            self.conn.execute("DELETE FROM flows WHERE flow_id=?", (flow_id,))
            self._audit("flow", flow_id, "delete", record, None)
            return record

    def history(self, flow_id: str | None = None) -> list[dict]:
        clause = " WHERE entity='flow' AND entity_key=?" if flow_id else ""
        return [dict(row) for row in self.conn.execute(
            "SELECT changed_at,entity,entity_key,action,before_json,after_json FROM audit" + clause + " ORDER BY audit_id",
            (flow_id,) if flow_id else (),
        )]

    def backup(self, path: str | Path) -> None:
        """Create a consistent SQLite snapshot, including records and audit history."""
        if self.conn.in_transaction:
            raise ValueError("Finish the current write before creating a backup.")
        target = Path(path).expanduser().resolve()
        if self.path != ":memory:" and target == Path(self.path):
            raise ValueError("Choose a backup path different from the active database.")
        target.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(str(target))
        try:
            self.conn.backup(destination)
        finally:
            destination.close()

    def restore(self, path: str | Path) -> None:
        """Validate a snapshot, preserve current data, and restore atomically.

        For file databases the safety copy sits beside the current database.
        In-memory repositories retain a temporary safety copy on disk as well.
        """
        from .peer_store import dataset
        if dataset(self):
            raise ValueError("A synchronized file cannot be replaced by a backup. Open the backup separately or join a new local copy.")
        if self.conn.in_transaction:
            raise ValueError("Finish the current write before restoring.")
        source_path = Path(path).expanduser().resolve()
        if self.path != ":memory:" and source_path == Path(self.path):
            raise ValueError("Choose a backup other than the active database.")
        if not source_path.is_file():
            raise ValueError("Backup file does not exist.")
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        prepared = sqlite3.connect(":memory:", isolation_level=None)
        try:
            self._validate_database(source)
            if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
                shared = source.execute("SELECT value FROM sync_meta WHERE key='peer_dataset'")
                if any(json.loads(row[0]) for row in shared):
                    raise ValueError("A synchronized backup cannot replace this file. Open it separately or join a new local copy.")
            # Prepare a private snapshot, leaving the source backup unchanged.
            # Validate the whole candidate before making any live-data changes.
            source.backup(prepared)
            prepared.execute("PRAGMA foreign_keys = ON")
            self._validate_database(prepared)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            if self.path == ":memory:":
                import tempfile
                safety = Path(tempfile.gettempdir()) / f"flowledger.pre-restore-{stamp}.bak"
            else:
                safety = Path(self.path + f".pre-restore-{stamp}.bak")
            self.backup(safety)
            prepared.backup(self.conn)
            if self.sync is not None:
                self.sync.close()
                self.sync = None
            if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_meta'").fetchone():
                from .sync import SyncState
                SyncState(self)
            self.conn.execute("PRAGMA foreign_keys = ON")
        finally:
            prepared.close()
            source.close()


def _statuses(statuses: Iterable[str]) -> set[str]:
    if isinstance(statuses, str):
        statuses = (statuses,)
    result = set(statuses)
    if not result.issubset(STATUSES):
        raise ValueError("Unknown balance status.")
    return result - {"Canceled"}


def _effect(account_name: str, flow: dict) -> Decimal:
    amount = parse_quantity(flow["amount"])
    source, destination = _key(flow["from_account"]), _key(flow["to_account"])
    if destination == account_name and source != account_name:
        return amount
    if source == account_name and destination != account_name:
        return amount.copy_negate()
    return Decimal(0)


def balance_at(account: dict, flows: list[dict], at: str,
               statuses: Iterable[str] = ("Complete",)) -> Decimal | None:
    """Apply eligible flows forward or backward from an account's anchor.

    The anchor is BEFORE events at its timestamp; returned balances are AFTER
    all selected events at the target. Unknown opening balances remain unknown.
    """
    when = normalize_date(at)
    anchor = normalize_date(account["opening_at"])
    allowed = _statuses(statuses)
    balance = account["opening_amount"]
    if balance is None:
        return None
    effects = [parse_quantity(balance, allow_negative=True)]
    name = _key(account["name"])
    units = _name(account["units"], "Units")
    for flow in flows:
        if flow["status"] not in allowed or _name(flow["units"], "Units") != units:
            continue
        moment = normalize_date(flow["date"])
        if when >= anchor and anchor <= moment <= when:
            effects.append(_effect(name, flow))
        elif when < anchor and when < moment < anchor:
            effects.append(_effect(name, flow).copy_negate())
    return decimal_sum(effects)


def timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
             statuses: Iterable[str] = ("Complete",)) -> dict[tuple[str, str], list[tuple[str, Decimal | None]]]:
    """Build carried-forward step values, combining simultaneous flow effects.

    Start/end points and every relevant eligible event timestamp are included.
    An account anchored inside the window is continuous across its anchor.
    """
    start, end = normalize_date(start), normalize_date(end)
    if start > end:
        raise ValueError("Graph start must not be after its end.")
    allowed = _statuses(statuses)
    pairs = {(_key(account["name"]), _name(account["units"], "Units")) for account in accounts}
    moments = {start, end}
    changes = defaultdict(lambda: defaultdict(list))
    for flow in flows:
        moment = normalize_date(flow["date"])
        if flow["status"] not in allowed or not start < moment <= end:
            continue
        source, destination = _key(flow["from_account"]), _key(flow["to_account"])
        units = _name(flow["units"], "Units")
        if (source, units) not in pairs and (destination, units) not in pairs:
            continue
        moments.add(moment)
        amount = parse_quantity(flow["amount"])
        changes[moment][source, units].append(amount.copy_negate())
        changes[moment][destination, units].append(amount)
    ordered = sorted(moments)
    result = {}
    for account in accounts:
        name = _key(account["name"])
        units = _name(account["units"], "Units")
        balance = balance_at(account, flows, start, allowed)
        series = [(start, balance)]
        for moment in ordered[1:]:
            if balance is not None:
                balance = decimal_sum([balance, *changes[moment].get((name, units), ())])
            series.append((moment, balance))
        result[account["name"], units] = series
    return result


def _ledger_anchor(account: dict, flows: list[dict], statuses: Iterable[str]) -> dict:
    """Expand a confirmed opening into the selected pending/forecast basis."""
    adjusted = dict(account)
    if adjusted["opening_amount"] is None:
        return adjusted
    anchor = normalize_date(account["opening_at"])
    selected_unconfirmed = _statuses(statuses) - {"Complete"}
    name = _key(account["name"])
    units = _name(account["units"], "Units")
    adjusted["opening_amount"] = quantity_text(decimal_sum([
        parse_quantity(adjusted["opening_amount"], allow_negative=True),
        *(_effect(name, flow) for flow in flows
          if flow["status"] in selected_unconfirmed and normalize_date(flow["date"]) < anchor
          and _name(flow["units"], "Units") == units)
    ]))
    return adjusted


def ledger_balance_at(account: dict, flows: list[dict], at: str,
                      statuses: Iterable[str] = ("Complete",)) -> Decimal | None:
    """Calculate a ledger balance from an explicitly CONFIRMED account anchor.

    A confirmed snapshot excludes unsettled Pending and hypothetical Planned
    flows, including those dated before that snapshot. Include their eligible
    effects once when the caller requests the corresponding estimate. Overdue
    Planned flows therefore remain in forecasts until completed, canceled, or
    excluded. Use balance_at directly for an arbitrary assumed simulation seed.

    Backdating a completed flow across a real snapshot boundary may require
    updating that snapshot. One flow timestamp cannot reconstruct historical
    bank posting states; this application uses current recorded statuses.
    """
    allowed = _statuses(statuses)
    return balance_at(_ledger_anchor(account, flows, allowed), flows, at, allowed)


def ledger_timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
                    statuses: Iterable[str] = ("Complete",)) -> dict[tuple[str, str], list[tuple[str, Decimal | None]]]:
    """Graph confirmed anchors plus selected unsettled/hypothetical effects.

    This is the normal ledger-view counterpart of ledger_balance_at. The generic
    timeline function is reserved for simulations with an assumed starting state.
    """
    allowed = _statuses(statuses)
    adjusted = [_ledger_anchor(account, flows, allowed) for account in accounts]
    return timeline(adjusted, flows, start, end, allowed)
