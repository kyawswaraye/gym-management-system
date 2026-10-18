"""Exact retail line extensions.

Quantity is ``NUMERIC(18, 3)`` and unit price, unit cost, and discount are
``NUMERIC(18, 2)``. A quantity times a money amount is rounded to
``NUMERIC(18, 2)`` with ``ROUND_HALF_UP`` (half away from zero). PostgreSQL
``round(quantity * unit_amount, 2)`` uses that same half-away-from-zero rule
for ``numeric``, so the database checks and this function agree for the
nonnegative quantities and amounts a sale can store.

Example: quantity ``1.250`` times unit price ``0.10`` is ``0.125``, which
rounds to ``0.13``. Discount must be from zero through that pre-discount
amount. Line revenue is the rounded extension minus discount. Cost of goods
sold is the rounded quantity times unit cost. Gross profit is revenue minus
that cost and may be negative.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_MONEY = Decimal("0.01")


def extend_money(quantity: Decimal, unit_amount: Decimal) -> Decimal:
    """Return ``quantity * unit_amount`` rounded half up to minor units."""

    return (quantity * unit_amount).quantize(_MONEY, rounding=ROUND_HALF_UP)
