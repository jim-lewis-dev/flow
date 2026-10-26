"""Exact, read-only summaries of the flow rows currently selected in the ledger."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from .core import STATUSES, decimal_sum, format_quantity, normalize_date, parse_quantity


def _label_order(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _totals(amounts_by_unit) -> str:
    return " · ".join(
        f"{format_quantity(decimal_sum(amounts_by_unit[unit]))} {unit}"
        for unit in sorted(amounts_by_unit, key=_label_order))


def _display_date(value: str) -> str:
    return value[:10] if value.endswith(" 00:00:00") else value


def format_selection_summary(flows: Iterable[dict]) -> str:
    """Describe saved selected records without interpreting them as balances.

    Every selected status contributes, including Canceled. Signed quantities
    stay attached to the recorded From, To and category; negative entries do
    not silently swap their endpoints. Units remain separate and case-sensitive.
    All sums and displayed quantities retain their exact decimal precision.

    Inputs are not changed. An empty selection produces no tooltip content.
    """
    records = list(flows)
    if not records:
        return ""

    totals = defaultdict(list)
    grouped = {field: defaultdict(lambda: defaultdict(list))
               for field in ("from_account", "to_account", "category")}
    for flow in records:
        amount = parse_quantity(flow["amount"])
        unit = flow["units"]
        totals[unit].append(amount)
        for field, labels in grouped.items():
            labels[flow[field]][unit].append(amount)

    dates = [normalize_date(flow["date"]) for flow in records]
    first, last = min(dates), max(dates)
    date_range = _display_date(first)
    if last != first:
        date_range += " → " + _display_date(last)
    statuses = Counter(flow["status"] for flow in records)
    status_counts = " · ".join(f"{status} {statuses[status]}"
                                for status in STATUSES if statuses[status])
    count = len(records)
    lines = [f"{count} selected {'flow' if count == 1 else 'flows'}",
             "Selected record totals · all selected statuses",
             "Dates: " + date_range,
             "Statuses: " + status_counts,
             "Total quantities: " + _totals(totals)]
    for field, heading in (("from_account", "From"), ("to_account", "To"),
                           ("category", "Categories")):
        lines.extend(("", heading))
        for label in sorted(grouped[field], key=_label_order):
            lines.append(f"  {label}: {_totals(grouped[field][label])}")
    return "\n".join(lines)
