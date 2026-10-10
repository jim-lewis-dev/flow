"""Graph presentation helpers; the stored ledger and balance rules stay unchanged."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from .core import decimal_sum, ledger_timeline, normalize_date, normalize_flow_date, parse_quantity


def normalize_graph_date(text: str) -> str:
    """Use the same date/time entry rules as flows and account starting values."""
    try:
        return normalize_flow_date(text)
    except ValueError as error:
        raise ValueError(f"Invalid graph date {text!r}. Use YYYY-MM-DD with optional 24-hour time; leading zeros and trailing components are optional.") from error


def graph_timeline(accounts: list[dict], flows: list[dict], start: str, end: str,
                   statuses: Iterable[str] = ("Complete",),
                   combined: bool = False) -> dict[tuple[str, str], list[tuple[str, Decimal | None]]]:
    """Return aligned exact quantities for each selected (account, units) pair.

    ``ledger_timeline`` supplies the normal balances after each timestamp. When
    an eligible flow touches a selected account/unit pair exactly at From, prepend the
    balances immediately before those simultaneous flows at the SAME timestamp.
    This deliberately keeps duplicate start timestamps so a step plot can show
    the starting value and the first change without inventing an earlier date.

    All series share timestamp positions, including the duplicate. Missing
    starting balances remain None. Combining sums those aligned positions only
    within the same unit, with keys ("Combined", units). A missing constituent
    makes that unit's combined balance unknown without affecting other units.
    Empty account selection returns an empty mapping. Inputs are never changed.
    """
    allowed = (statuses,) if isinstance(statuses, str) else tuple(statuses)
    start = normalize_date(normalize_graph_date(start))
    end = normalize_date(normalize_graph_date(end))
    series = ledger_timeline(accounts, flows, start, end, allowed)
    pairs = {(account["name"].strip().casefold(), account["units"].strip())
             for account in accounts}
    start_effects: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    has_start_flow = False
    for flow in flows:
        if flow["status"] == "Void" or flow["status"] not in allowed:
            continue
        if normalize_date(flow["date"]) != start:
            continue
        units = flow["units"].strip()
        source = (flow["from_account"].strip().casefold(), units)
        destination = (flow["to_account"].strip().casefold(), units)
        if source not in pairs and destination not in pairs:
            continue
        has_start_flow = True
        amount = parse_quantity(flow["amount"])
        if source in pairs:
            start_effects[source].append(amount.copy_negate())
        if destination in pairs:
            start_effects[destination].append(amount)
    if has_start_flow:
        for (name, units), points in series.items():
            after = points[0][1]
            effect = decimal_sum(start_effects[(name.strip().casefold(), units.strip())])
            before = None if after is None else decimal_sum((after, effect.copy_negate()))
            points.insert(0, (start, before))
    if combined and series:
        by_unit: dict[str, list[list[tuple[str, Decimal | None]]]] = defaultdict(list)
        for (_, units), points in series.items():
            by_unit[units].append(points)
        combined_series = {}
        for units, unit_series in by_unit.items():
            combined_points = []
            for row in zip(*unit_series):
                values = [point[1] for point in row]
                balance = None if any(value is None for value in values) else decimal_sum(values)
                combined_points.append((row[0][0], balance))
            combined_series[("Combined", units)] = combined_points
        return combined_series
    return series
