"""Gym-local dashboard periods.

Presets are full calendar periods in the active gym's timezone, not
period-to-date slices. ``this_month`` therefore runs from the first through
the last day of the gym-local month. ``week_starts_on`` uses the settings
convention: 0 is Sunday and 6 is Saturday. A custom range is at most 366
inclusive days, the same bound as a class schedule.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

PRESETS = ("today", "this_week", "this_month", "last_month", "this_year")
MAX_INCLUSIVE_DAYS = 366
REPORT_MIN_DATE = date(1900, 1, 1)
REPORT_MAX_DATE = date(2099, 12, 31)
MAX_REPORT_ROWS = 31


def resolve_preset(preset: str, *, today: date, week_starts_on: int) -> tuple[date, date]:
    """Return the inclusive gym-local bounds for one supported preset."""

    if preset == "today":
        return today, today
    if preset == "this_week":
        start = week_start(today, week_starts_on)
        return start, start + timedelta(days=6)
    if preset == "this_month":
        return month_bounds(today.year, today.month)
    if preset == "last_month":
        if today.month == 1:
            return month_bounds(today.year - 1, 12)
        return month_bounds(today.year, today.month - 1)
    if preset == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    raise ValueError(preset)


def week_start(today: date, week_starts_on: int) -> date:
    """Return the first day of the gym week that contains ``today``."""

    start = week_starts_on if 0 <= week_starts_on <= 6 else 1
    target = (start - 1) % 7
    delta = (today.weekday() - target) % 7
    return today - timedelta(days=delta)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def inclusive_days(date_from: date, date_to: date) -> int:
    return (date_to - date_from).days + 1


def iter_dates(date_from: date, date_to: date) -> list[date]:
    """Return each inclusive date from ``date_from`` through ``date_to``."""

    days: list[date] = []
    cursor = date_from
    while cursor <= date_to:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def date_in_report_window(value: date) -> bool:
    return REPORT_MIN_DATE <= value <= REPORT_MAX_DATE
