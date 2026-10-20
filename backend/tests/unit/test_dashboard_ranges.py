"""Dashboard preset bounds and strict query rules."""

from datetime import date

import pytest
from rest_framework.exceptions import ValidationError

from apps.reports.ranges import inclusive_days, resolve_preset, week_start
from apps.reports.serializers import TenantDashboardQuerySerializer

TODAY = date(2026, 10, 19)


def test_presets_use_full_gym_local_periods() -> None:
    assert resolve_preset("today", today=TODAY, week_starts_on=1) == (TODAY, TODAY)
    assert resolve_preset("this_week", today=TODAY, week_starts_on=1) == (
        date(2026, 10, 19),
        date(2026, 10, 25),
    )
    assert resolve_preset("this_week", today=TODAY, week_starts_on=0) == (
        date(2026, 10, 18),
        date(2026, 10, 24),
    )
    assert week_start(date(2026, 10, 18), 0) == date(2026, 10, 18)
    assert resolve_preset("this_month", today=TODAY, week_starts_on=1) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert resolve_preset("last_month", today=TODAY, week_starts_on=1) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
    )
    assert resolve_preset("last_month", today=date(2026, 1, 15), week_starts_on=1) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
    )
    assert resolve_preset("this_month", today=date(2024, 2, 15), week_starts_on=1) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    assert resolve_preset("this_year", today=TODAY, week_starts_on=1) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )


def test_custom_range_rejects_inverted_unbounded_and_mixed_selectors() -> None:
    assert inclusive_days(date(2026, 1, 1), date(2026, 12, 31)) == 365
    accepted = TenantDashboardQuerySerializer(
        data={"date_from": "2026-01-01", "date_to": "2026-12-31"}
    )
    assert accepted.is_valid(), accepted.errors
    too_long = TenantDashboardQuerySerializer(
        data={"date_from": "2024-01-01", "date_to": "2025-01-01"}
    )
    assert not too_long.is_valid()
    assert "date_to" in too_long.errors
    inverted = TenantDashboardQuerySerializer(
        data={"date_from": "2026-08-15", "date_to": "2026-08-01"}
    )
    assert not inverted.is_valid()
    mixed = TenantDashboardQuerySerializer(
        data={"range": "today", "date_from": "2026-08-01", "date_to": "2026-08-15"}
    )
    assert not mixed.is_valid()
    missing = TenantDashboardQuerySerializer(data={})
    assert not missing.is_valid()
    unknown = TenantDashboardQuerySerializer(data={"range": "month"})
    assert not unknown.is_valid()
    overridden = TenantDashboardQuerySerializer(
        data={"range": "today", "gym_id": "gym", "status": "POSTED"}
    )
    assert not overridden.is_valid()
    assert set(overridden.errors) >= {"gym_id", "status"}


def test_unknown_preset_is_not_resolved() -> None:
    with pytest.raises(ValueError):
        resolve_preset("month", today=TODAY, week_starts_on=1)


def test_serializer_raises_for_a_partial_custom_range() -> None:
    partial = TenantDashboardQuerySerializer(data={"date_from": "2026-08-01"})
    assert not partial.is_valid()
    with pytest.raises(ValidationError):
        partial.is_valid(raise_exception=True)
