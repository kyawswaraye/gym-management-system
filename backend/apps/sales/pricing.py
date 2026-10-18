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
_ZERO = Decimal("0.00")


def extend_money(quantity: Decimal, unit_amount: Decimal) -> Decimal:
    """Return ``quantity * unit_amount`` rounded half up to minor units."""

    return (quantity * unit_amount).quantize(_MONEY, rounding=ROUND_HALF_UP)


def allocate_cumulative_money(total: Decimal, taken: Decimal, whole: Decimal) -> Decimal:
    """Allocate ``total`` across ``taken`` of ``whole`` quantity units.

    The result is ``ROUND_HALF_UP(total * taken / whole, 2)`` until ``taken``
    equals ``whole``. That final quantity receives ``total`` exactly, so every
    slice of one sale line sums back to the immutable line amount. ``taken``
    and ``whole`` are nonnegative and ``taken`` cannot exceed ``whole``.
    ``whole`` is positive. PostgreSQL ``round(numeric, 2)`` matches this rule
    for the nonnegative totals a return allocates.
    """

    if whole <= 0 or taken < 0 or taken > whole:
        raise ValueError("Allocation quantity is invalid.")
    if taken == 0:
        return _ZERO
    if taken == whole:
        return total
    return (total * taken / whole).quantize(_MONEY, rounding=ROUND_HALF_UP)


def allocate_return_slice(
    total: Decimal,
    prior_taken: Decimal,
    quantity: Decimal,
    whole: Decimal,
) -> Decimal:
    """Return the money for one return after ``prior_taken`` units.

    Discount, line revenue, and cost of goods sold each use this slice. Gross
    profit is the slice revenue minus the slice cost, so it reconciles when
    those two totals do.
    """

    if quantity <= 0:
        raise ValueError("A return slice quantity must be positive.")
    cumulative = allocate_cumulative_money(total, prior_taken + quantity, whole)
    prior = allocate_cumulative_money(total, prior_taken, whole)
    return cumulative - prior
