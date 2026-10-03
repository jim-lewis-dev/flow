"""Exact flow arithmetic and transactional SQLite storage.

The database is authoritative. Each positive flow subtracts from its source and
adds to its destination. Every account participates equally in the arithmetic;
callers choose which accounts to inspect or combine.

Dates retain optional local wall-clock times to whole seconds, without offsets.
A date-only record is interpreted as midnight for calculations. An account
anchor describes its balance immediately BEFORE flows at that time.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import date as Date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Iterator
import uuid

STATUSES = ("Complete", "Pending", "Planned", "Void")
DEFAULT_ANCHOR = "2026-10-01 00:00:00"
SCHEMA_VERSION = 4
APPLICATION_ID = 1179406167
MAX_CENTS = 2**63 - 1


def _name(value: Any, label: str = "Name") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text.")
    result = value.strip()
    if "\x00" in result:
        raise ValueError(f"{label} cannot contain a null character.")
    return result


def _key(value: str) -> str:
    return value.strip().casefold()


def _cents(value: Any, label: str = "Amount", *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be integer cents.")
    if abs(value) > MAX_CENTS:
        raise ValueError(f"{label} exceeds the supported money range.")
    if positive and value <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return value


def parse_money(text: Any, allow_negative: bool = False) -> int:
    """Parse a dollar value exactly; refuse fractional cents and binary floats.

    Dollar signs and correctly grouped thousands commas are accepted. Zero is
    valid for opening balances; save_flow separately requires a positive amount.
    Exponent notation, NaN, infinity and ambiguous commas are rejected.
    """
    if isinstance(text, bool) or isinstance(text, float):
        raise ValueError("Use a decimal string for money, such as '12.34'.")
    value = str(text).strip()
    if value.startswith("$"):
        value = value[1:].strip()
    elif value.startswith(("-$", "+$")):
        value = value[0] + value[2:].strip()
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ValueError("Enter dollars and cents, such as 12.34.")
    try:
        with localcontext() as context:
            context.prec = max(50, len(value) + 5)
            amount = Decimal(value.replace(",", "")) * 100
            if not amount.is_finite() or amount != amount.to_integral_value():
                raise ValueError("Money must be an exact number of cents.")
            cents = int(amount)
    except InvalidOperation as error:
        raise ValueError("Invalid money amount.") from error
    _cents(cents)
    if cents < 0 and not allow_negative:
        raise ValueError("Amount cannot be negative; reverse From and To instead.")
    return cents


def format_money(cents: int) -> str:
    """Return a dollar string with exactly two decimal places (no float use)."""
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise ValueError("Money must be integer cents.")
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def normalize_date(value: Any) -> str:
    """Normalize a local date/time; omitted time means midnight.

    Accept YYYY-MM-DD [HH:MM[:SS]] or the equivalent ISO T separator. Reject
    offsets and fractional seconds to avoid silently losing time information.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.microsecond:
            raise ValueError("Use local time to whole seconds, without a timezone offset.")
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Date):
        return value.isoformat() + " 00:00:00"
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?", value.strip()
    ):
        raise ValueError("Use YYYY-MM-DD and optionally HH:MM or HH:MM:SS.")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("Invalid calendar date or time.") from error
    return parsed.isoformat(sep=" ", timespec="seconds")


def normalize_flow_date(value: Any) -> str:
    """Preserve a date-only value; normalize an entered time to whole seconds."""
    when = normalize_date(value)
    has_time = isinstance(value, datetime) or (
        isinstance(value, str) and len(value.strip()) > 10)
    return when if has_time else when[:10]


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


class Repository:
    """Local SQLite repository with atomic writes and an automatic change log.

    Names identify accounts, categories and tags. Keys are internal
    case-folded forms of names, not additional identifiers users must manage.
    Use atomic() to group several save calls into one all-or-nothing operation.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if not self.path:
            raise ValueError("Database path cannot be empty.")
        if self.path != ":memory:":
            target = Path(self.path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(target)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.migration_backup_path = None
        self.migration_forecasts_path = None
        try:
            tables = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not tables:
                schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
                self.conn.executescript("BEGIN IMMEDIATE;\n" + schema + "\nCOMMIT;")
            self._validate_database(self.conn, allow_legacy=True)
            if self.conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                # Preserve a consistent legacy snapshot before changing schema.
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                self.migration_backup_path = self.path + f".pre-v4-{stamp}.bak"
                self.backup(self.migration_backup_path)
                self.migration_forecasts_path = self._save_legacy_forecasts(
                    self.conn, self.migration_backup_path + ".forecasts.json")
                self._migrate_legacy(self.conn)
            self._validate_database(self.conn)
        except Exception:
            self.conn.close()
            raise

    @staticmethod
    def _validate_database(conn: sqlite3.Connection, *, allow_legacy: bool = False) -> None:
        if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("This is not a Flowledger database.")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION and not (allow_legacy and version in (1, 2, 3)):
            raise ValueError(f"Unsupported database version {version}; expected {SCHEMA_VERSION}.")
        required = {
            "accounts": {"account_key", "name", "opening_cents", "opening_at"},
            "categories": {"category_key", "name"}, "tags": {"tag_key", "name"},
            "flows": {"flow_id", "date", "from_key", "to_key", "amount_cents", "status", "name", "notes", "category_key"},
            "flow_tags": {"flow_id", "tag_key", "position"},
            "audit": {"audit_id", "changed_at", "entity", "entity_key", "action", "before_json", "after_json"},
        }
        if version < 4:
            required["scenarios"] = {"scenario_key", "name"}
            required["scenario_exclusions"] = {"scenario_key", "flow_id"}
            required["flows"].add("scenario_key")
            if version >= 2:
                required["flows"].add("time_specified")
        if version < 3:
            required["accounts"].update(("mine", "archived"))
        for table, columns in required.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise ValueError(f"Database is missing required {table} fields.")
        if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("Database integrity check failed.")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Database contains broken account or flow references.")
        if version < 4 and conn.execute("SELECT 1 FROM scenarios WHERE scenario_key='baseline'").fetchone() is None:
            raise ValueError("Database is missing its baseline scenario.")

    @staticmethod
    def _save_legacy_forecasts(conn: sqlite3.Connection, path: str | Path) -> str | None:
        """Write old hypothetical collections separately before removing their tables.

        This recovery file is not loaded into the active ledger. The full legacy
        database backup additionally preserves every old field and audit entry.
        """
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= 4:
            return None
        names = conn.execute("SELECT scenario_key,name FROM scenarios WHERE scenario_key<>'baseline' ORDER BY scenario_key").fetchall()
        if not names:
            return None
        result = []
        for key, name in names:
            flag = "f.time_specified" if version >= 2 else "substr(f.date,12)<>'00:00:00'"
            rows = conn.execute(
                "SELECT f.flow_id,f.date,a.name,b.name,f.amount_cents,f.status,f.name,f.notes,c.name," + flag +
                " FROM flows f JOIN accounts a ON a.account_key=f.from_key "
                "JOIN accounts b ON b.account_key=f.to_key JOIN categories c ON c.category_key=f.category_key "
                "WHERE f.scenario_key=? ORDER BY f.date,f.flow_id", (key,)).fetchall()
            flows = []
            for row in rows:
                flow = dict(zip(("flow_id", "date", "from_account", "to_account", "amount_cents",
                                 "status", "name", "notes", "category"), row[:9]))
                flow["date"] = normalize_date(flow["date"]) if row[9] else normalize_date(flow["date"])[:10]
                flow["tags"] = [tag[0] for tag in conn.execute(
                    "SELECT t.name FROM flow_tags ft JOIN tags t ON t.tag_key=ft.tag_key "
                    "WHERE ft.flow_id=? ORDER BY ft.position", (flow["flow_id"],))]
                flows.append(flow)
            result.append({"name": name, "flows": flows,
                           "excluded_flow_ids": [r[0] for r in conn.execute(
                               "SELECT flow_id FROM scenario_exclusions WHERE scenario_key=? ORDER BY flow_id", (key,))]})
        target = Path(path)
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(target)

    @staticmethod
    def _migrate_legacy(conn: sqlite3.Connection) -> None:
        """Upgrade v1-v3 atomically; activate only the old Baseline records.

        The caller preserves the complete original database before migration.
        Legacy optional-time flags become date precision. Version 1 did not
        distinguish entered midnight, so its midnight values become date-only.
        Forecast collections are kept in the original backup and recovery JSON,
        never mixed into ordinary flows. Accounts and audit history are retained.
        """
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3):
            raise ValueError(f"Cannot migrate database version {version}.")
        if conn.in_transaction:
            raise ValueError("Finish the current write before migrating.")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            if version < 3:
                conn.execute("CREATE TABLE accounts_v4 (account_key TEXT PRIMARY KEY, "
                             "name TEXT NOT NULL, opening_cents INTEGER "
                             "CHECK(opening_cents IS NULL OR typeof(opening_cents) = 'integer'), "
                             "opening_at TEXT NOT NULL)")
                conn.execute("INSERT INTO accounts_v4 SELECT account_key,name,opening_cents,opening_at FROM accounts")
                conn.execute("DROP TABLE accounts")
                conn.execute("ALTER TABLE accounts_v4 RENAME TO accounts")
            conn.execute("CREATE TABLE flows_v4 (flow_id TEXT PRIMARY KEY, date TEXT NOT NULL, "
                         "from_key TEXT NOT NULL REFERENCES accounts(account_key), "
                         "to_key TEXT NOT NULL REFERENCES accounts(account_key), "
                         "amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0), "
                         "status TEXT NOT NULL CHECK(status IN ('Complete','Pending','Planned','Void')), "
                         "name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', "
                         "category_key TEXT NOT NULL REFERENCES categories(category_key), CHECK(from_key<>to_key))")
            explicit = "time_specified=1" if version >= 2 else "substr(date,12)<>'00:00:00'"
            conn.execute("INSERT INTO flows_v4 SELECT flow_id,CASE WHEN " + explicit +
                         " THEN date ELSE substr(date,1,10) END,from_key,to_key,amount_cents,status,name,notes,category_key "
                         "FROM flows WHERE scenario_key='baseline'")
            conn.execute("DELETE FROM flow_tags WHERE flow_id NOT IN (SELECT flow_id FROM flows_v4)")
            conn.execute("DROP TABLE scenario_exclusions")
            conn.execute("DROP TABLE flows")
            conn.execute("DROP TABLE scenarios")
            conn.execute("ALTER TABLE flows_v4 RENAME TO flows")
            conn.execute("CREATE INDEX flows_date ON flows(datetime(date),flow_id)")
            conn.execute("CREATE INDEX flows_from ON flows(from_key,date)")
            conn.execute("CREATE INDEX flows_to ON flows(to_key,date)")
            conn.execute("PRAGMA user_version = 4")
            Repository._validate_database(conn)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute(f"PRAGMA foreign_keys = {foreign_keys}")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Commit all enclosed writes together; nest safely using savepoints."""
        marker = "flow_" + uuid.uuid4().hex
        self.conn.execute(f"SAVEPOINT {marker}")
        try:
            yield
        except BaseException:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {marker}")
            self.conn.execute(f"RELEASE SAVEPOINT {marker}")
            raise
        else:
            self.conn.execute(f"RELEASE SAVEPOINT {marker}")

    def _audit(self, entity: str, key: str, action: str, before: Any, after: Any) -> None:
        self.conn.execute(
            "INSERT INTO audit(changed_at,entity,entity_key,action,before_json,after_json) VALUES(?,?,?,?,?,?)",
            (datetime.now().isoformat(sep=" ", timespec="microseconds"), entity, key, action, _json(before), _json(after)),
        )

    @staticmethod
    def _account(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {"name": row["name"], "opening_cents": row["opening_cents"],
                "opening_at": row["opening_at"]}

    def list_accounts(self) -> list[dict]:
        return [self._account(row) for row in self.conn.execute("SELECT * FROM accounts ORDER BY account_key")]

    def get_account(self, name: str) -> dict | None:
        return self._account(self.conn.execute("SELECT * FROM accounts WHERE account_key=?", (_key(name),)).fetchone())

    def save_account(self, name: str, opening_cents: int | None = None,
                     opening_at: str = DEFAULT_ANCHOR) -> dict:
        name = _name(name, "Account")
        when = normalize_date(opening_at)
        if opening_cents is not None:
            _cents(opening_cents, "Opening balance")
        with self.atomic():
            before = self.get_account(name)
            canonical = before["name"] if before else name
            self.conn.execute(
                "INSERT INTO accounts(account_key,name,opening_cents,opening_at) VALUES(?,?,?,?) "
                "ON CONFLICT(account_key) DO UPDATE SET "
                "opening_cents=excluded.opening_cents,opening_at=excluded.opening_at",
                (_key(canonical), canonical, opening_cents, when),
            )
            after = self.get_account(canonical)
            if before != after:
                self._audit("account", canonical, "update" if before else "create", before, after)
            return after

    def list_categories(self) -> list[str]:
        return [row[0] for row in self.conn.execute("SELECT name FROM categories ORDER BY category_key")]

    def list_tags(self) -> list[str]:
        return [row[0] for row in self.conn.execute("SELECT name FROM tags ORDER BY tag_key")]

    def list_names(self) -> list[str]:
        names = {}
        for row in self.conn.execute("SELECT name FROM flows ORDER BY datetime(date),flow_id"):
            names.setdefault(_key(row[0]), row[0])
        return sorted(names.values(), key=str.casefold)

    def save_flow(self, data: dict) -> dict:
        """Validate then atomically save one flow, its suggestions, and its audit.

        A supplied existing flow_id updates that record. A supplied new UUID
        preserves imported identity. No other fields are stored on a flow.
        """
        if not isinstance(data, dict):
            raise ValueError("A flow must be a mapping of fields.")
        allowed = {"flow_id", "date", "from_account", "to_account", "amount_cents", "amount",
                   "status", "name", "notes", "category", "tags"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("Unknown flow fields: " + ", ".join(sorted(map(str, unknown))))
        when = normalize_flow_date(data.get("date", Date.today()))
        source = _name(data.get("from_account"), "From account")
        destination = _name(data.get("to_account"), "To account")
        if _key(source) == _key(destination):
            raise ValueError("From and To must be different accounts.")
        amount = (_cents(data["amount_cents"], positive=True) if "amount_cents" in data
                  else _cents(parse_money(data.get("amount", "")), positive=True))
        if "amount_cents" in data and "amount" in data:
            if amount != parse_money(data["amount"]):
                raise ValueError("amount and amount_cents disagree.")
        status = data.get("status", "Complete")
        if status not in STATUSES:
            raise ValueError("Status must be Complete, Pending, Planned, or Void.")
        name = data.get("name", "")
        notes = data.get("notes", "")
        category = data.get("category", "")
        if not all(isinstance(value, str) for value in (name, notes, category)):
            raise ValueError("Name, notes, and category must be text.")
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
                if self.get_account(endpoint) is None:
                    self.save_account(endpoint)
            source = self.get_account(source)["name"]
            destination = self.get_account(destination)["name"]
            name = name.strip() or f"{source} -> {destination}"
            self.conn.execute("INSERT OR IGNORE INTO categories VALUES(?,?)", (_key(category), category))
            self.conn.execute(
                "INSERT INTO flows(flow_id,date,from_key,to_key,amount_cents,status,name,notes,"
                "category_key) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(flow_id) DO UPDATE SET "
                "date=excluded.date,from_key=excluded.from_key,to_key=excluded.to_key,amount_cents=excluded.amount_cents,"
                "status=excluded.status,name=excluded.name,notes=excluded.notes,category_key=excluded.category_key",
                (flow_id, when, _key(source), _key(destination), amount, status, name, notes,
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
        f.amount_cents,f.status,f.name,f.notes,c.name AS category
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
        return self._records(self.conn.execute(self._FLOW_QUERY + " ORDER BY datetime(f.date),f.flow_id"))

    def get_flow(self, flow_id: str) -> dict | None:
        rows = self._records(self.conn.execute(self._FLOW_QUERY + " WHERE f.flow_id=?", (flow_id,)))
        return rows[0] if rows else None

    def void_flow(self, flow_id: str) -> dict:
        record = self.get_flow(flow_id)
        if record is None:
            raise ValueError("Unknown flow.")
        record["status"] = "Void"
        return self.save_flow(record)

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
            self._validate_database(source, allow_legacy=True)
            # Migrate a private snapshot, leaving the source backup unchanged.
            # Validate the whole candidate before making any live-data changes.
            source.backup(prepared)
            prepared.execute("PRAGMA foreign_keys = ON")
            legacy = prepared.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION
            if legacy:
                self._migrate_legacy(prepared)
            self._validate_database(prepared)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            if self.path == ":memory:":
                import tempfile
                safety = Path(tempfile.gettempdir()) / f"flowledger.pre-restore-{stamp}.bak"
            else:
                safety = Path(self.path + f".pre-restore-{stamp}.bak")
            self.backup(safety)
            recovered = self._save_legacy_forecasts(source, str(safety) + ".forecasts.json") if legacy else None
            prepared.backup(self.conn)
            self.migration_backup_path = str(source_path) if legacy else None
            self.migration_forecasts_path = recovered
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
    return result - {"Void"}


def _effect(account_name: str, flow: dict) -> int:
    return flow["amount_cents"] * (int(_key(flow["to_account"]) == account_name)
                                  - int(_key(flow["from_account"]) == account_name))


def balance_at(account: dict, flows: list[dict], at: str,
               statuses: Iterable[str] = ("Complete",)) -> int | None:
    """Apply eligible flows forward or backward from an account's anchor.

    The anchor is BEFORE events at its timestamp; returned balances are AFTER
    all selected events at the target. Unknown opening balances remain unknown.
    """
    when = normalize_date(at)
    anchor = normalize_date(account["opening_at"])
    allowed = _statuses(statuses)
    balance = account["opening_cents"]
    if balance is None:
        return None
    _cents(balance, "Opening balance")
    name = _key(account["name"])
    for flow in flows:
        if flow["status"] not in allowed:
            continue
        moment = normalize_date(flow["date"])
        if when >= anchor and anchor <= moment <= when:
            balance += _effect(name, flow)
        elif when < anchor and when < moment < anchor:
            balance -= _effect(name, flow)
    return balance


def timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
             statuses: Iterable[str] = ("Complete",)) -> dict[str, list[tuple[str, int | None]]]:
    """Build carried-forward step values, combining simultaneous flow effects.

    Start/end points and every relevant eligible event timestamp are included.
    An account anchored inside the window is continuous across its anchor.
    """
    start, end = normalize_date(start), normalize_date(end)
    if start > end:
        raise ValueError("Graph start must not be after its end.")
    allowed = _statuses(statuses)
    names = {_key(account["name"]) for account in accounts}
    moments = {start, end}
    changes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for flow in flows:
        moment = normalize_date(flow["date"])
        if flow["status"] not in allowed or not start < moment <= end:
            continue
        source, destination = _key(flow["from_account"]), _key(flow["to_account"])
        if source not in names and destination not in names:
            continue
        moments.add(moment)
        changes[moment][source] -= flow["amount_cents"]
        changes[moment][destination] += flow["amount_cents"]
    ordered = sorted(moments)
    result = {}
    for account in accounts:
        name = _key(account["name"])
        balance = balance_at(account, flows, start, allowed)
        series = [(start, balance)]
        for moment in ordered[1:]:
            if balance is not None:
                balance += changes[moment].get(name, 0)
            series.append((moment, balance))
        result[account["name"]] = series
    return result


def _ledger_anchor(account: dict, flows: list[dict], statuses: Iterable[str]) -> dict:
    """Expand a confirmed opening into the selected pending/forecast basis."""
    adjusted = dict(account)
    if adjusted["opening_cents"] is None:
        return adjusted
    anchor = normalize_date(account["opening_at"])
    selected_unconfirmed = _statuses(statuses) - {"Complete"}
    name = _key(account["name"])
    adjusted["opening_cents"] += sum(
        _effect(name, flow) for flow in flows
        if flow["status"] in selected_unconfirmed and normalize_date(flow["date"]) < anchor
    )
    return adjusted


def ledger_balance_at(account: dict, flows: list[dict], at: str,
                      statuses: Iterable[str] = ("Complete",)) -> int | None:
    """Calculate a ledger balance from an explicitly CONFIRMED account anchor.

    A confirmed snapshot excludes unsettled Pending and hypothetical Planned
    flows, including those dated before that snapshot. Include their eligible
    effects once when the caller requests the corresponding estimate. Overdue
    Planned flows therefore remain in forecasts until completed, voided, or
    excluded. Use balance_at directly for an arbitrary assumed simulation seed.

    Backdating a completed flow across a real snapshot boundary may require
    updating that snapshot. One flow timestamp cannot reconstruct historical
    bank posting states; this application uses current recorded statuses.
    """
    allowed = _statuses(statuses)
    return balance_at(_ledger_anchor(account, flows, allowed), flows, at, allowed)


def ledger_timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
                    statuses: Iterable[str] = ("Complete",)) -> dict[str, list[tuple[str, int | None]]]:
    """Graph confirmed anchors plus selected unsettled/hypothetical effects.

    This is the normal ledger-view counterpart of ledger_balance_at. The generic
    timeline function is reserved for simulations with an assumed starting state.
    """
    allowed = _statuses(statuses)
    adjusted = [_ledger_anchor(account, flows, allowed) for account in accounts]
    return timeline(adjusted, flows, start, end, allowed)
