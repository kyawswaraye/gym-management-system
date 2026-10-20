"""Gym-local recurrence for class schedules.

Weekdays use Python's convention: 0 is Monday and 6 is Sunday. Each matching
local date becomes one occurrence. A fall-back ambiguous local time uses the
earlier offset (``fold=0``). A spring-forward gap skips that local date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apps.classes.exceptions import ScheduleNoMatches, ScheduleTooLarge

MAX_INCLUSIVE_DAYS = 366
MAX_OCCURRENCES = 400


@dataclass(frozen=True)
class PlannedOccurrence:
    local_date: date
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class Materialization:
    occurrences: tuple[PlannedOccurrence, ...]
    skipped_local_dates: tuple[date, ...]


def matching_dates(
    *,
    effective_from: date,
    effective_until: date,
    weekdays: tuple[int, ...],
) -> tuple[date, ...]:
    """Return inclusive dates whose weekday is in ``weekdays``."""

    selected = set(weekdays)
    found: list[date] = []
    current = effective_from
    while current <= effective_until:
        if current.weekday() in selected:
            found.append(current)
        current += timedelta(days=1)
    return tuple(found)


def resolve_local_instant(*, local_date: date, local_time: time, zone: ZoneInfo) -> datetime | None:
    """Return the UTC instant for a gym-local wall time, or None if it does not exist.

    Ambiguous fall-back times use ``fold=0``, which is the earlier UTC instant.
    """

    naive = datetime.combine(local_date, local_time)
    earlier = naive.replace(tzinfo=zone, fold=0)
    round_trip = earlier.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    if round_trip != naive:
        return None
    return earlier.astimezone(UTC)


def materialize_occurrences(
    *,
    effective_from: date,
    effective_until: date,
    weekdays: tuple[int, ...],
    local_start_time: time,
    local_end_time: time,
    zone: ZoneInfo,
    enforce_bounds: bool = True,
) -> Materialization:
    """Build the occurrence set for one schedule.

    ``enforce_bounds`` rejects an empty match and a set larger than 400. The
    366-day range is checked by the caller before this function runs. Tests of
    the 400 cap pass ``enforce_bounds`` after constructing a longer range.
    """

    dates = matching_dates(
        effective_from=effective_from,
        effective_until=effective_until,
        weekdays=weekdays,
    )
    if enforce_bounds and not dates:
        raise ScheduleNoMatches()
    if enforce_bounds and len(dates) > MAX_OCCURRENCES:
        raise ScheduleTooLarge()
    planned: list[PlannedOccurrence] = []
    skipped: list[date] = []
    for local_date in dates:
        starts_at = resolve_local_instant(
            local_date=local_date, local_time=local_start_time, zone=zone
        )
        ends_at = resolve_local_instant(local_date=local_date, local_time=local_end_time, zone=zone)
        if starts_at is None or ends_at is None or ends_at <= starts_at:
            skipped.append(local_date)
            continue
        planned.append(
            PlannedOccurrence(local_date=local_date, starts_at=starts_at, ends_at=ends_at)
        )
    return Materialization(occurrences=tuple(planned), skipped_local_dates=tuple(skipped))
