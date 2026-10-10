"""Read-only event selection and pulse sizing for the Experimental flow view."""
from __future__ import annotations

from decimal import Decimal
from math import log10
from typing import Iterable

from .core import STATUSES, normalize_date, normalize_flow_date, parse_quantity


def select_events(flows: Iterable[dict], start: str, end: str, units: str,
                  statuses: Iterable[str]) -> list[dict]:
    """Select one unit's eligible flows in timestamp order, preserving tie order.

    A date-only end includes that entire day. An explicit end time includes
    events through that second. Returned records retain their original fields,
    exact amounts, and IDs; this function makes no changes to the records.
    """
    beginning = normalize_date(start)
    finish = normalize_date(end)
    if normalize_flow_date(end) == finish[:10]:
        finish = finish[:10] + " 23:59:59"
    if beginning > finish:
        raise ValueError("Start must not be after end.")
    selected = {statuses} if isinstance(statuses, str) else set(statuses)
    if not selected.issubset(STATUSES):
        raise ValueError("Unknown flow status.")
    selected.discard("Canceled")
    return sorted(
        (flow for flow in flows
         if flow["status"] in selected and flow["units"] == units
         and beginning <= normalize_date(flow["date"]) <= finish),
        key=lambda flow: normalize_date(flow["date"]),
    )


def grouped_events(events: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    """Group simultaneous flows by normalized local timestamp, oldest first."""
    groups: list[tuple[str, list[dict]]] = []
    for event in sorted(events, key=lambda flow: normalize_date(flow["date"])):
        when = normalize_date(event["date"])
        if groups and groups[-1][0] == when:
            groups[-1][1].append(event)
        else:
            groups.append((when, [event]))
    return groups


def _log10_positive(value: Decimal) -> float:
    """Find log10 without converting potentially huge quantities to float."""
    digits = value.as_tuple().digits[:16]
    mantissa = float(f"{digits[0]}." + "".join(map(str, digits[1:])))
    return value.adjusted() + log10(mantissa)


def pulse_radius(amount: str | Decimal, max_amount: str | Decimal) -> float:
    """Give a visible 5–17 pixel pulse using compressed quantity scaling.

    The compression is for drawing only. Quantity comparisons and the ledger
    retain their exact Decimal values, including fractions and large numbers.
    """
    quantity = parse_quantity(amount)
    maximum = parse_quantity(max_amount)
    if quantity <= 0 or maximum <= 0:
        raise ValueError("Pulse quantities must be positive.")
    if quantity >= maximum:
        return 17.0
    if maximum < 1:
        # Ratio via logs also works when float(quantity / maximum) underflows.
        strength = 10 ** (0.5 * (_log10_positive(quantity) - _log10_positive(maximum)))
    else:
        def log_one_plus(value: Decimal) -> float:
            return (log10(1 + float(value)) if value.adjusted() < 14
                    else _log10_positive(value))

        strength = log_one_plus(quantity) / log_one_plus(maximum)
    return min(17.0, max(5.0, 5.0 + 12.0 * strength))
