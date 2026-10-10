"""Previewed, atomic flow import and portable CSV/JSON export.

The canonical export has the ten flow fields plus ``flow_id``: ``date``,
``name``, ``from_account``, ``to_account``, ``amount`` (an exact decimal string),
``units``, ``status``, ``category``, ``tags`` (a string array), and ``notes``.
Dates retain optional time: a date alone stays a date alone; an entered time
is normalized to seconds. ``from`` and ``to`` are accepted import aliases.
Units are required, case-sensitive labels; quantities are not limited to
currency precision. CSV tags may use ``|``.

Preview performs no writes. Repeated IDs with identical content are skipped;
conflicting IDs abort the batch; equal content with a new or missing ID needs
explicit acceptance. Unknown fields are rejected. Export contains all flows;
use database backup to preserve account starting balances and audit history.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
import uuid

from .core import STATUSES, normalize_flow_date, parse_quantity, quantity_text


EXPORT_FIELDS = (
    "flow_id", "date", "name", "from_account", "to_account", "amount", "units",
    "status", "category", "tags", "notes",
)
_ALIASES = {"from": "from_account", "to": "to_account"}
_IMPORT_FIELDS = frozenset(EXPORT_FIELDS)


def _key(value: str) -> str:
    return value.strip().casefold()


def _headers(row: dict) -> dict:
    """Normalize header spelling, rejecting ambiguous duplicate aliases."""
    normalized = {}
    for field, value in row.items():
        if not isinstance(field, str):
            raise ValueError("Every field must have a string name; check CSV columns.")
        field = field.strip().casefold().replace(" ", "_")
        field = _ALIASES.get(field, field)
        if field in normalized and normalized[field] != value:
            raise ValueError(f"Conflicting values for {field}.")
        normalized[field] = value
    return normalized


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}.")


def read_import(path: str | os.PathLike) -> list[dict]:
    """Read CSV or JSON without changing the database.

    Individual row errors remain available for ``preview_import``. File-level
    syntax/shape errors raise ValueError. JSON decimal numbers are read as text
    to avoid introducing binary floating-point rounding before validation.
    """
    path = Path(path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            if path.suffix.casefold() == ".json":
                value = json.load(stream, parse_float=str,
                                  parse_constant=_reject_json_constant)
                if isinstance(value, dict):
                    value = value.get("flows")
                if not isinstance(value, list):
                    raise ValueError("JSON must be a list of flows or an object containing flows.")
                return value
            if path.suffix.casefold() == ".csv":
                reader = csv.DictReader(stream)
                if not reader.fieldnames:
                    raise ValueError("CSV must contain a header row.")
                fields = [_ALIASES.get(str(f).strip().casefold().replace(" ", "_"),
                                       str(f).strip().casefold().replace(" ", "_"))
                          for f in reader.fieldnames]
                if len(fields) != len(set(fields)):
                    raise ValueError("CSV contains duplicate or aliased duplicate headers.")
                return list(reader)
            raise ValueError("Choose a .csv or .json file.")
    except (json.JSONDecodeError, csv.Error, UnicodeError) as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc


def _text(value: Any, field: str, *, required: bool = False,
          strip: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    result = value.strip() if strip else value
    if "\x00" in result:
        raise ValueError(f"{field} cannot contain a null character.")
    if required and not result:
        raise ValueError(f"{field} is required.")
    return result


def _parse_tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("tags must be a JSON string array or pipe-separated labels.") from exc
        else:
            value = value.split("|")
    if not isinstance(value, list):
        raise ValueError("tags must be a list of strings.")
    result = []
    seen = set()
    for tag in value:
        if not isinstance(tag, str):
            raise ValueError("Each tag must be text.")
        tag = _text(tag, "Each tag")
        if not tag:
            continue
        if _key(tag) not in seen:
            seen.add(_key(tag))
            result.append(tag)
    return result


def _canonical(row: Any, accounts: dict[str, str],
               categories: dict[str, str], tags: dict[str, str]) -> dict:
    if not isinstance(row, dict):
        raise ValueError("Each flow must be an object or CSV row.")
    row = _headers(row)
    units = _text(row.get("units", ""), "units", required=True)
    unsupported = sorted(set(row) - _IMPORT_FIELDS)
    if unsupported:
        raise ValueError("Unsupported fields: " + ", ".join(unsupported) + ".")
    date = normalize_flow_date(_text(row.get("date"), "date", required=True))
    source = _text(row.get("from_account"), "from_account", required=True)
    destination = _text(row.get("to_account"), "to_account", required=True)
    source = accounts.get(_key(source), source)
    destination = accounts.get(_key(destination), destination)
    if _key(source) == _key(destination):
        raise ValueError("From and To must be different accounts.")
    if "amount" not in row or row["amount"] in (None, ""):
        raise ValueError("amount is required.")
    amount = parse_quantity(row["amount"])
    if amount <= 0:
        raise ValueError("amount must be greater than zero.")
    status = _text(row.get("status", "Complete"), "status") or "Complete"
    if status == "Void":
        status = "Canceled"  # Files exported before the status rename.
    if status not in STATUSES:
        raise ValueError("status must be Complete, Pending, Planned, or Canceled.")
    name = _text(row.get("name", ""), "name") or f"{source} -> {destination}"
    notes = _text(row.get("notes", ""), "notes", strip=False)
    category = _text(row.get("category", ""), "category") or "Uncategorized"
    category = categories.get(_key(category), category)
    parsed_tags = [tags.get(_key(tag), tag) for tag in _parse_tags(row.get("tags", []))]
    result = {"date": date,
              "from_account": source, "to_account": destination,
              "amount": quantity_text(amount), "units": units, "status": status, "name": name,
              "notes": notes, "category": category, "tags": parsed_tags}
    flow_id = row.get("flow_id")
    if flow_id not in (None, ""):
        flow_id = _text(flow_id, "flow_id", required=True)
        try:
            result["flow_id"] = str(uuid.UUID(flow_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("flow_id must be a UUID, or omitted for a new flow.") from exc
    return result


def _fingerprint(data: dict) -> tuple:
    """Payload identity, retaining unit case and normalizing quantities/labels."""
    return (normalize_flow_date(data["date"]), _key(data["from_account"]),
            _key(data["to_account"]), quantity_text(data["amount"]), data["units"], data["status"],
            data["name"].strip(), data["notes"], _key(data["category"]),
            tuple(sorted({_key(tag) for tag in data["tags"]})))


def preview_import(repo, rows: Iterable[dict]) -> list[dict]:
    """Validate all rows, detect ID conflicts and likely repeats, with no writes.

    States: New (safe addition), Duplicate (same ID and content, skip), Conflict
    (same ID with changed content, abort), Error (invalid row, abort), Similar
    (same content under another/no ID, import only with explicit acceptance).
    Row numbers are one-based data-row numbers, excluding a CSV header.
    """
    accounts = {_key(a["name"]): a["name"] for a in repo.list_accounts()}
    categories = {_key(name): name for name in repo.list_categories()}
    tags = {_key(tag): tag for tag in repo.list_tags()}
    existing = repo.list_flows()
    by_id = {item["flow_id"]: _fingerprint(item) for item in existing}
    by_content = {_fingerprint(item) for item in existing}
    results = []
    for index, row in enumerate(rows, 1):
        try:
            data = _canonical(row, accounts, categories, tags)
            fingerprint = _fingerprint(data)
            flow_id = data.get("flow_id")
            if flow_id and flow_id in by_id:
                if by_id[flow_id] == fingerprint:
                    state, message = "Duplicate", "Same ID and content; this row will be skipped."
                else:
                    state, message = "Conflict", "This ID already has different content; no records will be overwritten."
            elif fingerprint in by_content:
                state, message = "Similar", "Matching content under another or missing ID; approve to keep this possible repeat."
            else:
                state, message = "New", "Ready to import."
            if state not in ("Conflict", "Error"):
                if flow_id:
                    by_id[flow_id] = fingerprint
                by_content.add(fingerprint)
                for name in (data["from_account"], data["to_account"]):
                    accounts.setdefault(_key(name), name)
                categories.setdefault(_key(data["category"]), data["category"])
                for tag in data["tags"]:
                    tags.setdefault(_key(tag), tag)
            results.append({"row": index, "data": data, "state": state, "message": message})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            results.append({"row": index, "data": row, "state": "Error", "message": str(exc)})
    return results


def commit_import(repo, preview: Iterable[dict], include_similar: bool = False) -> dict:
    """Commit valid preview rows atomically; recheck against current data.

    Any Error/Conflict aborts the whole batch. Duplicate rows and unapproved
    Similar rows are skipped. Revalidation inside the transaction catches
    changes made after the preview; a later failure rolls back earlier rows.
    """
    preview = list(preview)
    for item in preview:
        if item.get("state") not in ("New", "Duplicate", "Similar"):
            raise ValueError(f"Row {item.get('row', '?')}: {item.get('message', 'Import contains errors or conflicts.')}")
        if not isinstance(item.get("data"), dict):
            raise ValueError("Import preview has invalid row data.")
    if not preview:
        return {"imported": 0, "skipped": 0}
    imported = skipped = 0
    with repo.atomic():
        current_rows = preview_import(repo, [item["data"] for item in preview])
        for current in current_rows:
            if current["state"] in ("Error", "Conflict"):
                raise ValueError(f"Row {current['row']}: {current['message']}")
        for original, current in zip(preview, current_rows):
            similar = "Similar" in (original["state"], current["state"])
            if current["state"] == "Duplicate" or (similar and not include_similar):
                skipped += 1
                continue
            repo.save_flow(current["data"])
            imported += 1
    return {"imported": imported, "skipped": skipped}


def export_flows(repo, path: str | os.PathLike) -> int:
    """Atomically export all flows with IDs and exact decimal amount strings."""
    path = Path(path)
    suffix = path.suffix.casefold()
    if suffix not in (".csv", ".json"):
        raise ValueError("Choose a .csv or .json export file.")
    rows = []
    for flow in repo.list_flows():
        rows.append({"flow_id": flow["flow_id"], "date": flow["date"],
                     "name": flow["name"],
                     "from_account": flow["from_account"], "to_account": flow["to_account"],
                     "amount": quantity_text(flow["amount"]), "units": flow["units"],
                     "status": flow["status"], "category": flow["category"],
                     "tags": list(flow["tags"]), "notes": flow["notes"]})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False,
                                         dir=path.parent, prefix=f".{path.name}.", suffix=".tmp") as stream:
            temporary = stream.name
            if suffix == ".json":
                json.dump(rows, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
            else:
                writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({**row, "tags": json.dumps(row["tags"], ensure_ascii=False)})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return len(rows)
