"""Small, exact arithmetic for quantity entry fields.

Entry accepts decimal numbers, grouped commas, +, -, *, / and parentheses.
This is separate from the strict number parser used for records and imports.
No Python code, names, exponent notation or additional operators are accepted.

Calculations use exact rational intermediates. A terminating final result is
an exact Decimal; a repeating final result is rounded to at least 50 significant
digits (or the total count of entered digits, if larger), using ROUND_HALF_EVEN.
The ambient Decimal context cannot change these results.
"""
from __future__ import annotations

from decimal import Context, Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN
from fractions import Fraction

from .core import parse_quantity


MAX_EXPRESSION_LENGTH = 2048
MAX_TOKENS = 256
MAX_NESTING = 32
MAX_INTEGER_BITS = 32768
MIN_DIVISION_PRECISION = 50


class _Parser:
    def __init__(self, text: str):
        self.tokens: list[str] = []
        self.position = 0
        self.literal_digits = 0
        index = 0
        while index < len(text):
            character = text[index]
            if character.isspace():
                index += 1
                continue
            if character in "+-*/()":
                self.tokens.append(character)
                index += 1
            elif character in "0123456789.":
                start = index
                while index < len(text) and text[index] in "0123456789.,":
                    index += 1
                token = text[start:index]
                # Validate the whole literal, including comma grouping.
                parse_quantity(token, allow_negative=True)
                self.literal_digits += sum(c.isdigit() for c in token)
                self.tokens.append(token)
            else:
                raise ValueError("Use numbers, +, -, *, / and parentheses only.")
            if len(self.tokens) > MAX_TOKENS:
                raise ValueError("This calculation has too many terms.")

    def current(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    @staticmethod
    def checked(value: Fraction) -> Fraction:
        if (value.numerator.bit_length() > MAX_INTEGER_BITS
                or value.denominator.bit_length() > MAX_INTEGER_BITS):
            raise ValueError("This calculation is too large.")
        return value

    def expression(self, depth: int = 0) -> Fraction:
        value = self.term(depth)
        while self.current() in ("+", "-"):
            operator = self.current()
            self.position += 1
            right = self.term(depth)
            value = self.checked(value + right if operator == "+" else value - right)
        return value

    def term(self, depth: int) -> Fraction:
        value = self.factor(depth)
        while self.current() in ("*", "/"):
            operator = self.current()
            self.position += 1
            right = self.factor(depth)
            if operator == "/" and right == 0:
                raise ValueError("Cannot divide by zero.")
            value = self.checked(value * right if operator == "*" else value / right)
        return value

    def factor(self, depth: int) -> Fraction:
        negative = False
        while self.current() in ("+", "-"):
            negative ^= self.current() == "-"
            self.position += 1
        token = self.current()
        if token == "(":
            if depth >= MAX_NESTING:
                raise ValueError("This calculation has too many nested parentheses.")
            self.position += 1
            value = self.expression(depth + 1)
            if self.current() != ")":
                raise ValueError("Close every opening parenthesis.")
            self.position += 1
        elif token is not None and token[0] in "0123456789.":
            value = self.checked(Fraction(parse_quantity(token, allow_negative=True)))
            self.position += 1
        else:
            raise ValueError("Enter a number after each operator.")
        return -value if negative else value


def _decimal_result(value: Fraction, precision: int) -> Decimal:
    if value == 0:
        return Decimal(0)
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator == 1:
        scale = max(twos, fives)
        coefficient = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
        # Decimal(int) is exact and avoids Python's int-to-string digit limit.
        digits = Decimal(coefficient).as_tuple().digits
        return Decimal((int(value < 0), digits, -scale))
    context = Context(prec=max(MIN_DIVISION_PRECISION, precision), rounding=ROUND_HALF_EVEN,
                      Emin=MIN_EMIN, Emax=MAX_EMAX)
    return context.divide(Decimal(value.numerator), Decimal(value.denominator))


def evaluate_quantity(text: str) -> Decimal:
    """Evaluate a quantity entry, returning a finite Decimal without code execution.

    Operators use ordinary precedence and division is left-associative. A leading
    equals sign is optional. Bounded input and nesting keep
    accidental or hostile calculations from occupying the UI indefinitely.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a number or a calculation.")
    if text.lstrip().startswith("="):
        text = text.lstrip()[1:]
    # Existing plain quantities have no entry-size limit. Bound only expressions
    # that need calculation, rather than imposing a new limit on stored numbers.
    try:
        return parse_quantity(text, allow_negative=True)
    except ValueError:
        pass
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise ValueError("This calculation is too long.")
    parser = _Parser(text)
    result = parser.expression()
    if parser.current() is not None:
        if parser.current() == ")":
            raise ValueError("There is an extra closing parenthesis.")
        raise ValueError("Put an operator between numbers or parentheses.")
    return _decimal_result(result, parser.literal_digits)
