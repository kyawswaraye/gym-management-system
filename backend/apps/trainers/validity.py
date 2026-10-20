"""Inclusive calendar validity for a granted service package."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

_UNITS = frozenset({"DAY", "WEEK", "MONTH", "YEAR"})


def inclusive_until(start: date, value: int, unit: str) -> date:
    """Return the inclusive last valid day for one package window.

    ``DAY`` adds ``value - 1`` days. ``WEEK`` adds ``value * 7 - 1`` days.
    ``MONTH`` and ``YEAR`` add that many calendar months or years, clamp the
    day to the last day of the destination month, then subtract one day.
    """

    if value < 1 or unit not in _UNITS:
        raise ValueError("Validity must be a positive calendar unit.")
    if unit == "DAY":
        return start + timedelta(days=value - 1)
    if unit == "WEEK":
        return start + timedelta(days=value * 7 - 1)
    months = value * 12 if unit == "YEAR" else value
    return _add_months(start, months) - timedelta(days=1)


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day))
