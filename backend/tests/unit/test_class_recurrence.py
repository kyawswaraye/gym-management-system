"""Recurrence bounds and DST resolution for class schedules."""

from __future__ import annotations

from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from apps.classes.exceptions import ScheduleNoMatches, ScheduleTooLarge
from apps.classes.recurrence import materialize_occurrences, resolve_local_instant

NEW_YORK = ZoneInfo("America/New_York")


def test_weekdays_are_monday_zero_and_reject_an_empty_match() -> None:
    plan = materialize_occurrences(
        effective_from=date(2026, 10, 19),
        effective_until=date(2026, 10, 25),
        weekdays=(0, 2),
        local_start_time=time(9, 0),
        local_end_time=time(10, 0),
        zone=ZoneInfo("Asia/Yangon"),
    )
    assert [item.local_date.isoformat() for item in plan.occurrences] == [
        "2026-10-19",
        "2026-10-21",
    ]
    assert plan.skipped_local_dates == ()
    with pytest.raises(ScheduleNoMatches):
        materialize_occurrences(
            effective_from=date(2026, 10, 20),
            effective_until=date(2026, 10, 20),
            weekdays=(0,),
            local_start_time=time(9, 0),
            local_end_time=time(10, 0),
            zone=ZoneInfo("Asia/Yangon"),
        )


def test_more_than_400_matching_dates_inserts_nothing() -> None:
    with pytest.raises(ScheduleTooLarge):
        materialize_occurrences(
            effective_from=date(2026, 1, 1),
            effective_until=date(2027, 2, 5),
            weekdays=(0, 1, 2, 3, 4, 5, 6),
            local_start_time=time(9, 0),
            local_end_time=time(10, 0),
            zone=ZoneInfo("UTC"),
            enforce_bounds=True,
        )


def test_spring_forward_gap_is_skipped_and_fall_back_uses_the_earlier_offset() -> None:
    missing = resolve_local_instant(
        local_date=date(2026, 3, 8), local_time=time(2, 30), zone=NEW_YORK
    )
    assert missing is None
    gap = materialize_occurrences(
        effective_from=date(2026, 3, 8),
        effective_until=date(2026, 3, 8),
        weekdays=(6,),
        local_start_time=time(2, 30),
        local_end_time=time(3, 30),
        zone=NEW_YORK,
    )
    assert gap.occurrences == ()
    assert gap.skipped_local_dates == (date(2026, 3, 8),)
    folded = materialize_occurrences(
        effective_from=date(2026, 11, 1),
        effective_until=date(2026, 11, 1),
        weekdays=(6,),
        local_start_time=time(1, 30),
        local_end_time=time(1, 45),
        zone=NEW_YORK,
    )
    assert len(folded.occurrences) == 1
    assert folded.skipped_local_dates == ()
    assert folded.occurrences[0].starts_at.isoformat() == "2026-11-01T05:30:00+00:00"
    assert folded.occurrences[0].ends_at.isoformat() == "2026-11-01T05:45:00+00:00"
