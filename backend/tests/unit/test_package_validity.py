"""Inclusive package validity."""

from datetime import date

from apps.trainers.validity import inclusive_until


def test_inclusive_validity_covers_leap_day_and_month_end() -> None:
    assert inclusive_until(date(2026, 3, 1), 1, "DAY") == date(2026, 3, 1)
    assert inclusive_until(date(2026, 3, 1), 3, "DAY") == date(2026, 3, 3)
    assert inclusive_until(date(2026, 3, 1), 1, "WEEK") == date(2026, 3, 7)
    assert inclusive_until(date(2026, 3, 1), 2, "WEEK") == date(2026, 3, 14)
    assert inclusive_until(date(2026, 1, 31), 1, "MONTH") == date(2026, 2, 27)
    assert inclusive_until(date(2024, 1, 31), 1, "MONTH") == date(2024, 2, 28)
    assert inclusive_until(date(2024, 2, 29), 1, "YEAR") == date(2025, 2, 27)
    assert inclusive_until(date(2024, 2, 29), 4, "YEAR") == date(2028, 2, 28)
    assert inclusive_until(date(2023, 1, 31), 1, "MONTH") == date(2023, 2, 27)
