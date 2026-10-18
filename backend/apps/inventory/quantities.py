"""Exact stock quantities.

Quantities are ``NUMERIC(18, 3)``. Inputs are decimal text, integers, or
``Decimal`` values. Binary floats, NaN, infinities, exponent notation, and
more than three decimal places are rejected. Extra places are not rounded.
The absolute value must fit fifteen digits before the decimal point.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_QUANTUM = Decimal("0.001")
_MAX_QUANTITY = Decimal("999999999999999.999")
_TEXT = re.compile(r"-?(?:0|[1-9]\d{0,14})(?:\.\d{1,3})?")
_MAX_TEXT_LENGTH = 32


class InvalidQuantity(ValueError):
    """The value is not an exact, in-range stock quantity."""


def parse_quantity(value: object, *, nonzero: bool = False) -> Decimal:
    """Return ``value`` as a three-place decimal."""

    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidQuantity("Quantity must be an exact decimal.")
    if isinstance(value, Decimal):
        parsed = _from_decimal(value)
    elif isinstance(value, int):
        parsed = _from_int(value)
    elif isinstance(value, str):
        parsed = _from_text(value)
    else:
        raise InvalidQuantity("Quantity must be an exact decimal.")
    if parsed != parsed.quantize(_QUANTUM):
        raise InvalidQuantity("Quantity must be an exact decimal.")
    quantized = parsed.quantize(_QUANTUM)
    if not quantized.is_finite() or abs(quantized) > _MAX_QUANTITY:
        raise InvalidQuantity("Quantity must be an exact decimal.")
    if nonzero and quantized == 0:
        raise InvalidQuantity("Quantity must be non-zero.")
    return quantized


def format_quantity(value: Decimal) -> str:
    return format(parse_quantity(value), "f")


def _from_text(value: str) -> Decimal:
    if len(value) > _MAX_TEXT_LENGTH or _TEXT.fullmatch(value) is None:
        raise InvalidQuantity("Quantity must be an exact decimal.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidQuantity("Quantity must be an exact decimal.") from exc
    if not parsed.is_finite():
        raise InvalidQuantity("Quantity must be an exact decimal.")
    return parsed


def _from_int(value: int) -> Decimal:
    if abs(value) > int(_MAX_QUANTITY):
        raise InvalidQuantity("Quantity must be an exact decimal.")
    return Decimal(value)


def _from_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise InvalidQuantity("Quantity must be an exact decimal.")
    exponent = value.as_tuple().exponent
    digits = value.as_tuple().digits
    if not isinstance(exponent, int) or exponent < -3 or exponent > 0 or len(digits) > 18:
        raise InvalidQuantity("Quantity must be an exact decimal.")
    return value
