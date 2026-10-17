"""Exact money parsing for finance commands.

Inputs are decimal text, integers, or ``Decimal`` values. Binary floats and
exponent notation are rejected. The result is quantized to currency minor
units with ``ROUND_HALF_UP`` and must fit ``NUMERIC(18, 2)``.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_QUANTUM = Decimal("0.01")
_MAX_MONEY = Decimal("9999999999999999.99")
_TEXT = re.compile(r"-?(?:0|[1-9]\d{0,15})(?:\.\d{1,8})?")
_MAX_TEXT_LENGTH = 32


class InvalidMoney(ValueError):
    """The value is not an exact, in-range money amount."""


def parse_money(value: object) -> Decimal:
    """Return ``value`` quantized to two decimal places."""

    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidMoney("Money must be an exact decimal.")
    if isinstance(value, Decimal):
        parsed = _from_decimal(value)
    elif isinstance(value, int):
        parsed = _from_int(value)
    elif isinstance(value, str):
        parsed = _from_text(value)
    else:
        raise InvalidMoney("Money must be an exact decimal.")
    quantized = parsed.quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    if not quantized.is_finite() or abs(quantized) > _MAX_MONEY:
        raise InvalidMoney("Money must be an exact decimal.")
    return quantized


def _from_text(value: str) -> Decimal:
    if len(value) > _MAX_TEXT_LENGTH or _TEXT.fullmatch(value) is None:
        raise InvalidMoney("Money must be an exact decimal.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidMoney("Money must be an exact decimal.") from exc
    if not parsed.is_finite():
        raise InvalidMoney("Money must be an exact decimal.")
    return parsed


def _from_int(value: int) -> Decimal:
    if abs(value) > int(_MAX_MONEY):
        raise InvalidMoney("Money must be an exact decimal.")
    return Decimal(value)


def _from_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise InvalidMoney("Money must be an exact decimal.")
    exponent = value.as_tuple().exponent
    digits = value.as_tuple().digits
    if not isinstance(exponent, int) or exponent < -8 or exponent > 0 or len(digits) > 24:
        raise InvalidMoney("Money must be an exact decimal.")
    return value
