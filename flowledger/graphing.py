"""Graph presentation helpers; the stored ledger and balance rules stay unchanged."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Iterable

from .core import ledger_timeline, normalize_date


def normalize_graph_date(text: str) -> str:
    """Accept a year-first graph date with optional whole-second local time.

    Month, day, hour, minute and second may omit their leading zero. A date-only
    input stays date-only so the caller can interpret Through as the whole day.
    Explicit times normalize to YYYY-MM-DD HH:MM:SS. This permissiveness is only
    for graph controls; record validation and stored dates are unchanged.
    """
    message = (f"Invalid graph date {text!r}. Use YYYY-MM-DD, optionally followed "
               "by HH:MM or HH:MM:SS; leading zeros are optional.")
    match = re.fullmatch(
        r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:[ T]([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2}))?)?",
        text.strip() if isinstance(text, str) else "",
    )
    if match is None:
        raise ValueError(message)
    year, month, day, hour, minute, second = match.groups()
    try:
        parsed = datetime(int(year), int(month), int(day), int(hour or 0),
                          int(minute or 0), int(second or 0))
    except ValueError as error:
        raise ValueError(message + " Check the calendar date and time.") from error
    return parsed.date().isoformat() if hour is None else parsed.isoformat(sep=" ", timespec="seconds")


def graph_timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
                   statuses: Iterable[str] = ("Complete",),
                   combined: bool = False) -> dict[str, list[tuple[str, int | None]]]:
    """Return aligned exact-cent steps, including both sides of a start event.

    ``ledger_timeline`` supplies the normal balances after each timestamp. When
    an eligible flow touches a selected account exactly at From, prepend the
    balances immediately before those simultaneous flows at the SAME timestamp.
    This deliberately keeps duplicate start timestamps so a step plot can show
    the starting value and the first change without inventing an earlier date.

    All series share timestamp positions, including the duplicate. Missing
    starting balances remain None. Combining sums those aligned positions,
    preserving the pre-flow value; a missing constituent makes the sum unknown.
    Empty account selection returns an empty mapping. Inputs are never changed.
    """
    allowed = (statuses,) if isinstance(statuses, str) else tuple(statuses)
    start = normalize_date(normalize_graph_date(start))
    end = normalize_date(normalize_graph_date(end))
    series = ledger_timeline(accounts, flows, start, end, allowed)
    names = {account["name"].strip().casefold() for account in accounts}
    start_effects = {name: 0 for name in names}
    has_start_flow = False
    for flow in flows:
        if flow["status"] == "Void" or flow["status"] not in allowed:
            continue
        if normalize_date(flow["date"]) != start:
            continue
        source = flow["from_account"].strip().casefold()
        destination = flow["to_account"].strip().casefold()
        if source not in names and destination not in names:
            continue
        has_start_flow = True
        if source in names:
            start_effects[source] -= flow["amount_cents"]
        if destination in names:
            start_effects[destination] += flow["amount_cents"]
    if has_start_flow:
        for name, points in series.items():
            after = points[0][1]
            before = None if after is None else after - start_effects[name.strip().casefold()]
            points.insert(0, (start, before))
    if combined and series:
        combined_points = []
        for row in zip(*series.values()):
            values = [point[1] for point in row]
            balance = None if any(value is None for value in values) else sum(values)
            combined_points.append((row[0][0], balance))
        return {"Combined": combined_points}
    return series
