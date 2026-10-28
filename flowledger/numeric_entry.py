"""Small arithmetic parser for quantity entry fields.

Entry accepts decimal numbers, grouped commas, +, -, *, /, ^ and parentheses.
This is separate from the strict number parser used for records and imports.
No Python code, names, scientific notation or additional operators are accepted.

Basic arithmetic and integer powers use exact rational intermediates. A
terminating final result is an exact Decimal; a repeating result is rounded
to at least 50 significant digits (or the total count of entered digits, if
larger), using ROUND_HALF_EVEN.
Non-rational powers use Decimal with guard digits, then the same final precision.
The ambient Decimal context cannot change these results.
"""
from __future__ import annotations

from decimal import Context, Decimal, DecimalException, MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Underflow
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
        self.approximate = False
        index = 0
        while index < len(text):
            character = text[index]
            if character.isspace():
                index += 1
                continue
            if character in "+-*/^()":
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
                raise ValueError("Use numbers, +, -, *, /, ^ and parentheses only.")
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
        if self.current() == "^":
            if depth >= MAX_NESTING:
                raise ValueError("This calculation has too many nested powers.")
            self.position += 1
            # The right factor includes its sign and any further power, while
            # the left sign applies after exponentiation: -2^2 = -(2^2).
            value = self.power(value, self.factor(depth + 1))
        return -value if negative else value

    def integer_power(self, value: Fraction, exponent: int) -> Fraction:
        if value in (1, -1):
            return Fraction(-1 if value < 0 and exponent % 2 else 1)
        if exponent < 0:
            value, exponent = 1 / value, -exponent
        result = Fraction(1)
        # Check after each bounded multiplication, never construct an enormous
        # numerator or denominator with an unchecked integer ** operation.
        while exponent:
            if exponent & 1:
                result = self.checked(result * value)
            exponent >>= 1
            if exponent:
                value = self.checked(value * value)
        return result

    @staticmethod
    def perfect_root(value: int, degree: int) -> int | None:
        if value == 1:
            return 1
        if degree > value.bit_length():
            return None
        # Integer Newton iteration; all intermediates stay near the input size.
        root = 1 << ((value.bit_length() + degree - 1) // degree)
        while True:
            following = ((degree - 1) * root + value // root ** (degree - 1)) // degree
            if following >= root:
                return root if root ** degree == value else None
            root = following

    def power(self, value: Fraction, exponent: Fraction) -> Fraction:
        if value == 0:
            if exponent < 0:
                raise ValueError("Cannot divide by zero in a negative power.")
            if exponent == 0:
                raise ValueError("0^0 is undefined.")
            return Fraction(0)
        if exponent.denominator == 1:
            return self.integer_power(value, exponent.numerator)
        if value < 0 and exponent.denominator % 2 == 0:
            raise ValueError("This power has no real result.")
        sign = -1 if value < 0 and exponent.numerator % 2 else 1
        magnitude = abs(value)
        numerator = self.perfect_root(magnitude.numerator, exponent.denominator)
        denominator = self.perfect_root(magnitude.denominator, exponent.denominator)
        if numerator is not None and denominator is not None:
            return sign * self.integer_power(Fraction(numerator, denominator), exponent.numerator)
        precision = max(MIN_DIVISION_PRECISION, self.literal_digits) + 10
        # A power amplifies relative error in its base by the exponent's
        # magnitude. Resolve enough extra digits before converting the exact
        # rational base, especially for values such as (1 + 10^-100)^10^100.
        exponent_digits = max(0, len(Decimal(abs(exponent.numerator)).as_tuple().digits)
                              - len(Decimal(exponent.denominator).as_tuple().digits) + 1)
        working_precision = precision + exponent_digits
        if working_precision > MAX_INTEGER_BITS // 3:
            raise ValueError("This calculation is too large.")
        context = Context(prec=working_precision, rounding=ROUND_HALF_EVEN,
                          Emin=-(MAX_INTEGER_BITS // 3), Emax=MAX_INTEGER_BITS // 3)
        context.traps[Underflow] = True
        try:
            base = context.divide(Decimal(magnitude.numerator), Decimal(magnitude.denominator))
            power = context.divide(Decimal(exponent.numerator), Decimal(exponent.denominator))
            result = context.power(base, power)
            # Keep only the requested intermediate precision after the power;
            # the extra working digits were needed to evaluate it reliably.
            context.prec = precision
            result = context.create_decimal(result)
        except DecimalException as exc:
            raise ValueError("This calculation is too large or too small.") from exc
        self.approximate = True
        return self.checked(sign * Fraction(result))


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

    Powers bind before unary signs and multiplication, and associate to the right.
    Division is left-associative. A leading
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
    if parser.approximate:
        context = Context(prec=max(MIN_DIVISION_PRECISION, parser.literal_digits),
                          rounding=ROUND_HALF_EVEN, Emin=MIN_EMIN, Emax=MAX_EMAX)
        return context.divide(Decimal(result.numerator), Decimal(result.denominator))
    return _decimal_result(result, parser.literal_digits)
