"""Unit display settings. These never change saved quantities or arithmetic."""
from decimal import ROUND_HALF_EVEN, localcontext

from .core import parse_quantity


DEFAULT_DECIMAL_PLACES = 4
UNIT_DECIMAL_PLACES = {"USD": 2}


def decimal_places(units: str) -> int:
    return UNIT_DECIMAL_PLACES.get(units, DEFAULT_DECIMAL_PLACES)


def format_display(value, units: str) -> str:
    """Format a quantity at its unit's display precision, without a float."""
    amount = parse_quantity(value, allow_negative=True)
    with localcontext() as context:
        context.rounding = ROUND_HALF_EVEN
        text = format(amount, f",.{decimal_places(units)}f")
    # A rounded display zero should not carry a minus sign.
    if text.startswith("-") and parse_quantity(text, allow_negative=True).is_zero():
        text = text[1:]
    return text
