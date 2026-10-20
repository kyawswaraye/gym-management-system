"""PT session and class reports, isolation, and bounded reads."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.classes.models import ClassOccurrence
from apps.common.db.tenant_context import set_gym_context
from apps.reports.selectors import (
    CLASS_REPORT_QUERY_COUNT,
    CLASS_SOURCE_STATUS_POLICY,
    PT_SESSION_QUERY_COUNT,
    PT_SESSION_SOURCE_STATUS_POLICY,
    class_report,
    pt_session_report,
)
from apps.tenants.models import Gym, GymFeatureEntitlement
from tests.security.test_bookings import _key
from tests.security.test_class_schedules import _profile, _schedule_body, _type_body
from tests.security.test_dashboard import FROZEN, _headers, _staff, _zone
from tests.security.test_retail import _enable, _gym
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings
from tests.security.test_trainer_assignments import _assign, _member

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

LA = ZoneInfo("America/Los_Angeles")
PT_ROW_KEYS = {
    "date",
    "bookings",
    "unique_members",
    "unique_trainers",
    "booked_sessions",
    "attended_sessions",
    "no_show_sessions",
    "cancelled_sessions",
    "metered_sessions",
    "unmetered_sessions",
}
CLASS_ROW_KEYS = {
    "date",
    "occurrences",
    "scheduled_occurrences",
    "completed_occurrences",
    "cancelled_occurrences",
    "available_capacity",
    "bookings",
    "unique_members",
    "booked_slots",
    "attended_slots",
    "no_show_slots",
    "cancelled_slots",
    "reserved_slots",
    "remaining_capacity",
}
CLASS_TOTAL_KEYS = CLASS_ROW_KEYS - {"date"} | {"unique_trainers"}


def _report(api: APIClient, headers: dict[str, str], path: str) -> dict[str, Any]:
    response = api.get(path, **headers)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data, dict)
    return data


def _row(report: dict[str, Any], day: str) -> dict[str, Any]:
    rows = report["rows"]
    assert isinstance(rows, list)
    matches = [row for row in rows if isinstance(row, dict) and row["date"] == day]
    assert len(matches) == 1
    return matches[0]


def _assert_shape(report: dict[str, Any], row_keys: set[str], total_keys: set[str]) -> None:
    assert set(report) == {
        "gym",
        "timezone",
        "range",
        "generated_at",
        "source_status_policy",
        "rows",
        "totals",
    }
    assert set(report["gym"]) == {"id", "code", "name"}
    assert "currency" not in report
    for row in report["rows"]:
        assert set(row) == row_keys
    assert set(report["totals"]) == total_keys


def _disable(gym: Gym, code: str) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        updated = GymFeatureEntitlement.objects.filter(gym=gym, feature__code=code).update(
            enabled=False
        )
    assert updated == 1


def _pt_package(api: APIClient, headers: dict[str, str], *, code: str) -> str:
    created = api.post(
        "/api/v1/service-packages/",
        {
            "type": "PT",
            "code": code,
            "name": "PT report",
            "session_count": 2,
            "validity_value": 12,
            "validity_unit": "MONTH",
            "price": "80.00",
        },
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["id"])


def _grant(api: APIClient, headers: dict[str, str], *, member_id: str, package_id: str) -> str:
    created = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": "2026-03-01",
            "complimentary": True,
            "reason": "Report coverage",
        },
        format="json",
        **headers,
        **_key("grant"),
    )
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["member_service_package"]["id"])


def _post(api: APIClient, headers: dict[str, str], path: str, body: dict[str, object]) -> str:
    created = api.post(path, body, format="json", **headers, **_key("cmd"))
    assert created.status_code in {200, 201}, created.json()
    data = created.json()["data"]
    assert isinstance(data, dict)
    return str(data["id"])


def _span(moment: datetime) -> tuple[str, str]:
    return moment.isoformat(), (moment + timedelta(minutes=20)).isoformat()


def _force_occurrence_status(gym: Gym, occurrence_id: UUID, status: str) -> None:
    """Store a current status the published class command does not write.

    Completion is not a published command. Cancelling an occurrence that still
    has an attended booking is also not a published transition. The report
    counts the stored status, so this fixture writes that status directly.
    """

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute(
                """
                UPDATE classes_class_occurrence
                SET status = %s, updated_at = statement_timestamp()
                WHERE id = %s AND gym_id = %s
                """,
                [status, occurrence_id, gym.id],
            )
            updated = cursor.rowcount
        assert updated == 1


def _occurrence(gym: Gym, *, location: str, local_date: date) -> ClassOccurrence:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return ClassOccurrence.objects.get(gym=gym, location=location, local_date=local_date)


@override_settings(**jwt_settings())
def test_session_reports_reuse_the_range_and_reports_view() -> None:
    api, gym, _owner, _owner_headers = _gym("SESGAT")
    staff = _staff(gym)
    reception = _headers(staff["reception"])
    sessions = "/api/v1/reports/pt-sessions/"
    classes = "/api/v1/reports/classes/"
    leap = "date_from=2024-02-28&date_to=2024-03-01"
    for path in (sessions, classes):
        assert api.get(path, **reception).status_code == 400
        assert api.get(f"{path}?date_from=2026-10-01", **reception).status_code == 400
        assert (
            api.get(f"{path}?{leap}&gym_id={gym.id}&status=BOOKED", **reception).status_code == 400
        )
        assert (
            api.get(
                f"{path}?date_from=2026-10-01&date_from=2026-10-02&date_to=2026-10-02",
                **reception,
            ).status_code
            == 400
        )
        assert api.get(f"{path}?date_from=&date_to=2026-10-01", **reception).status_code == 400
        assert (
            api.get(f"{path}?date_from=2026-10-02&date_to=2026-10-01", **reception).status_code
            == 400
        )
        assert (
            api.get(f"{path}?date_from=2024-01-01&date_to=2025-01-01", **reception).status_code
            == 400
        )
        assert (
            api.get(f"{path}?date_from=1899-12-31&date_to=1899-12-31", **reception).status_code
            == 400
        )
        assert (
            api.get(f"{path}?date_from=2100-01-01&date_to=2100-01-01", **reception).status_code
            == 400
        )
        assert api.get(f"{path}?{leap}", **_headers(staff["trainer"])).status_code == 404

    session_leap = _report(api, reception, f"{sessions}?{leap}")
    class_leap = _report(api, reception, f"{classes}?{leap}")
    assert session_leap["source_status_policy"] == PT_SESSION_SOURCE_STATUS_POLICY
    assert class_leap["source_status_policy"] == CLASS_SOURCE_STATUS_POLICY
    for report, row_keys, total_keys in (
        (session_leap, PT_ROW_KEYS, PT_ROW_KEYS - {"date"}),
        (class_leap, CLASS_ROW_KEYS, CLASS_TOTAL_KEYS),
    ):
        _assert_shape(report, row_keys, total_keys)
        assert [row["date"] for row in report["rows"]] == [
            "2024-02-28",
            "2024-02-29",
            "2024-03-01",
        ]
        assert all(
            value == 0 for row in report["rows"] for key, value in row.items() if key != "date"
        )
    year = "/api/v1/reports/pt-sessions/?date_from=2024-01-01&date_to=2024-12-31"
    full_year = _report(api, reception, year)
    assert len(full_year["rows"]) == 366
    assert full_year["rows"][0]["date"] == "2024-01-01"
    assert full_year["rows"][-1]["date"] == "2024-12-31"
    class_year = _report(api, reception, f"{classes}?date_from=2024-01-01&date_to=2024-12-31")
    assert len(class_year["rows"]) == 366
    assert api.get("/api/v1/reports/daily/?date=2026-10-01", **reception).status_code == 403
    GymFeatureEntitlement.objects.filter(gym=gym, feature__code="reports").update(enabled=False)
    assert api.get(f"{sessions}?{leap}", **reception).status_code == 403
    assert api.get(f"{classes}?{leap}", **reception).status_code == 403


@override_settings(**jwt_settings())
def test_pt_session_report_uses_local_start_and_current_status() -> None:
    early = datetime(2026, 3, 7, 23, 30, tzinfo=LA)
    before_gap = datetime(2026, 3, 8, 1, 30, tzinfo=LA)
    after_gap = datetime(2026, 3, 8, 3, 30, tzinfo=LA)
    evening = datetime(2026, 3, 8, 17, 0, tzinfo=LA)
    next_day = datetime(2026, 3, 9, 10, 0, tzinfo=LA)
    assert early.utcoffset() == timedelta(hours=-8)
    assert early.astimezone(UTC).date() == date(2026, 3, 8)
    assert before_gap.utcoffset() == timedelta(hours=-8)
    assert after_gap.utcoffset() == timedelta(hours=-7)
    assert evening.astimezone(UTC).date() == date(2026, 3, 9)

    api, gym, _owner, headers = _gym("PTSESS")
    other_api, other, _other_owner, other_headers = _gym("PTOTHR")
    for tenant in (gym, other):
        _enable(tenant, "members")
        _enable(tenant, "pt")
        _zone(tenant, "America/Los_Angeles", 0)
    reception = _headers(_staff(gym)["reception"])
    trainers = [
        add_staff(gym, email="ptsess-t1@example.test", role_key="TRAINER"),
        add_staff(gym, email="ptsess-t2@example.test", role_key="TRAINER"),
    ]
    first = _profile(gym, trainers[0], code="TR1", name="Trainer One")
    second = _profile(gym, trainers[1], code="TR2", name="Trainer Two")
    ada = _member(api, headers, first_name="Ada")
    bea = _member(api, headers, first_name="Bea")
    cy = _member(api, headers, first_name="Cy")
    for member_id, trainer in ((ada, first), (cy, first), (bea, second)):
        assigned = _assign(
            api,
            headers,
            trainer_id=str(trainer.id),
            member_id=member_id,
            start="2026-03-01",
        )
        assert assigned.status_code == 201, assigned.json()
    package_id = _pt_package(api, headers, code="PT_RPT")
    ada_grant = _grant(api, headers, member_id=ada, package_id=package_id)
    cy_grant = _grant(api, headers, member_id=cy, package_id=package_id)

    def book(
        trainer_id: str,
        member_id: str,
        moment: datetime,
        *,
        grant_id: str | None = None,
    ) -> str:
        starts_at, ends_at = _span(moment)
        body: dict[str, object] = {
            "trainer_staff_profile_id": trainer_id,
            "member_id": member_id,
            "starts_at": starts_at,
            "ends_at": ends_at,
        }
        if grant_id is None:
            body["unmetered"] = True
            body["unmetered_reason"] = "Owner coverage"
        else:
            body["member_service_package_id"] = grant_id
        return _post(api, headers, "/api/v1/pt-bookings/", body)

    book(str(first.id), ada, early, grant_id=ada_grant)
    attended = book(str(first.id), ada, before_gap, grant_id=ada_grant)
    no_show = book(str(second.id), bea, after_gap)
    cancelled = book(str(first.id), cy, evening, grant_id=cy_grant)
    book(str(first.id), ada, next_day)
    _post(api, headers, f"/api/v1/pt-bookings/{attended}/attend/", {})
    _post(api, headers, f"/api/v1/pt-bookings/{no_show}/no-show/", {})
    _post(api, headers, f"/api/v1/pt-bookings/{cancelled}/cancel/", {"reason": "Member cancelled"})

    other_trainer_membership = add_staff(other, email="ptothr-t1@example.test", role_key="TRAINER")
    other_trainer = _profile(other, other_trainer_membership, code="TR1", name="Other Trainer")
    other_member = _member(other_api, other_headers, first_name="Zoe")
    other_assigned = _assign(
        other_api,
        other_headers,
        trainer_id=str(other_trainer.id),
        member_id=other_member,
        start="2026-03-01",
    )
    assert other_assigned.status_code == 201, other_assigned.json()
    other_start, other_end = _span(datetime(2026, 3, 8, 10, 0, tzinfo=LA))
    _post(
        other_api,
        other_headers,
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(other_trainer.id),
            "member_id": other_member,
            "starts_at": other_start,
            "ends_at": other_end,
            "unmetered": True,
            "unmetered_reason": "Other gym",
        },
    )
    _disable(gym, "pt")

    report = _report(
        api,
        reception,
        "/api/v1/reports/pt-sessions/?date_from=2026-03-07&date_to=2026-03-09",
    )
    _assert_shape(report, PT_ROW_KEYS, PT_ROW_KEYS - {"date"})
    assert report["timezone"] == "America/Los_Angeles"
    assert report["gym"]["code"] == "PTSESS"
    march_seventh = _row(report, "2026-03-07")
    assert march_seventh["bookings"] == 1
    assert march_seventh["unique_members"] == 1
    assert march_seventh["unique_trainers"] == 1
    assert march_seventh["booked_sessions"] == 1
    assert march_seventh["metered_sessions"] == 1
    assert march_seventh["unmetered_sessions"] == 0
    march_eighth = _row(report, "2026-03-08")
    assert march_eighth["bookings"] == 3
    assert march_eighth["unique_members"] == 3
    assert march_eighth["unique_trainers"] == 2
    assert march_eighth["booked_sessions"] == 0
    assert march_eighth["attended_sessions"] == 1
    assert march_eighth["no_show_sessions"] == 1
    assert march_eighth["cancelled_sessions"] == 1
    assert march_eighth["metered_sessions"] == 2
    assert march_eighth["unmetered_sessions"] == 1
    march_ninth = _row(report, "2026-03-09")
    assert march_ninth["bookings"] == 1
    assert march_ninth["unique_members"] == 1
    assert march_ninth["unique_trainers"] == 1
    assert march_ninth["booked_sessions"] == 1
    assert march_ninth["unmetered_sessions"] == 1
    assert march_ninth["metered_sessions"] == 0
    for row in report["rows"]:
        assert row["bookings"] == (
            row["booked_sessions"]
            + row["attended_sessions"]
            + row["no_show_sessions"]
            + row["cancelled_sessions"]
        )
        assert row["bookings"] == row["metered_sessions"] + row["unmetered_sessions"]
    assert report["totals"]["bookings"] == 5
    assert report["totals"]["unique_members"] == 3
    assert report["totals"]["unique_trainers"] == 2
    assert report["totals"]["booked_sessions"] == 2
    assert report["totals"]["attended_sessions"] == 1
    assert report["totals"]["no_show_sessions"] == 1
    assert report["totals"]["cancelled_sessions"] == 1
    assert report["totals"]["metered_sessions"] == 3
    assert report["totals"]["unmetered_sessions"] == 2
    daily_members = sum(row["unique_members"] for row in report["rows"])
    daily_trainers = sum(row["unique_trainers"] for row in report["rows"])
    assert report["totals"]["unique_members"] != daily_members
    assert report["totals"]["unique_trainers"] != daily_trainers

    isolated = _report(
        other_api,
        other_headers,
        "/api/v1/reports/pt-sessions/?date_from=2026-03-08&date_to=2026-03-08",
    )
    assert isolated["totals"]["bookings"] == 1
    assert isolated["gym"]["code"] == "PTOTHR"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            pt_session_report(
                gym=loaded,
                date_from=date(2026, 3, 8),
                date_to=date(2026, 3, 8),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_year:
            pt_session_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    assert len(one_day.captured_queries) == PT_SESSION_QUERY_COUNT
    assert len(full_year.captured_queries) == PT_SESSION_QUERY_COUNT


@override_settings(**jwt_settings())
def test_class_report_uses_local_date_capacity_and_current_status() -> None:
    assert date(2026, 3, 7).weekday() == 5
    assert date(2026, 3, 8).weekday() == 6
    assert date(2026, 3, 9).weekday() == 0
    api, gym, _owner, headers = _gym("CLSRPT")
    other_api, other, _other_owner, other_headers = _gym("CLOTHR")
    for tenant in (gym, other):
        _enable(tenant, "members")
        _enable(tenant, "pt")
        _zone(tenant, "America/Los_Angeles", 0)
    reception = _headers(_staff(gym)["reception"])
    trainers = [
        add_staff(gym, email="clsrpt-t1@example.test", role_key="TRAINER"),
        add_staff(gym, email="clsrpt-t2@example.test", role_key="TRAINER"),
    ]
    first = _profile(gym, trainers[0], code="TR1", name="Trainer One")
    second = _profile(gym, trainers[1], code="TR2", name="Trainer Two")
    class_type = api.post("/api/v1/class-types/", _type_body(code="YOGA"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    ada = _member(api, headers, first_name="Ada")
    bea = _member(api, headers, first_name="Bea")
    cy = _member(api, headers, first_name="Cy")
    dee = _member(api, headers, first_name="Dee")
    fay = _member(api, headers, first_name="Fay")

    def schedule(
        *,
        trainer_id: str,
        weekday: int,
        start: str,
        end: str,
        day: str,
        location: str,
        capacity: int,
    ) -> None:
        created = api.post(
            "/api/v1/class-schedules/",
            _schedule_body(
                class_type_id=type_id,
                trainer_staff_profile_id=trainer_id,
                weekdays=[weekday],
                local_start_time=start,
                local_end_time=end,
                effective_from=day,
                effective_until=day,
                location=location,
                location_is_exclusive=False,
                capacity=capacity,
            ),
            format="json",
            **headers,
            **_key("sch"),
        )
        assert created.status_code == 201, created.json()

    schedule(
        trainer_id=str(first.id),
        weekday=5,
        start="10:00:00",
        end="11:00:00",
        day="2026-03-07",
        location="Studio E",
        capacity=1,
    )
    schedule(
        trainer_id=str(first.id),
        weekday=6,
        start="10:00:00",
        end="11:00:00",
        day="2026-03-08",
        location="Studio A",
        capacity=4,
    )
    schedule(
        trainer_id=str(second.id),
        weekday=6,
        start="12:00:00",
        end="13:00:00",
        day="2026-03-08",
        location="Studio B",
        capacity=10,
    )
    schedule(
        trainer_id=str(second.id),
        weekday=6,
        start="15:00:00",
        end="16:00:00",
        day="2026-03-08",
        location="Studio D",
        capacity=3,
    )
    schedule(
        trainer_id=str(first.id),
        weekday=0,
        start="17:00:00",
        end="17:30:00",
        day="2026-03-09",
        location="Studio C",
        capacity=2,
    )
    clamped = _occurrence(gym, location="Studio E", local_date=date(2026, 3, 7))
    shared = _occurrence(gym, location="Studio A", local_date=date(2026, 3, 8))
    dropped = _occurrence(gym, location="Studio B", local_date=date(2026, 3, 8))
    finished = _occurrence(gym, location="Studio D", local_date=date(2026, 3, 8))
    evening = _occurrence(gym, location="Studio C", local_date=date(2026, 3, 9))
    assert evening.local_date == date(2026, 3, 9)
    assert evening.starts_at.astimezone(UTC).date() == date(2026, 3, 10)
    assert shared.capacity == 4

    def book(occurrence_id: UUID, member_id: str) -> str:
        return _post(
            api,
            headers,
            "/api/v1/class-bookings/",
            {
                "class_occurrence_id": str(occurrence_id),
                "member_id": member_id,
                "unmetered": True,
                "unmetered_reason": "Owner coverage",
            },
        )

    fay_booking = book(clamped.id, fay)
    ada_booking = book(shared.id, ada)
    book(shared.id, bea)
    cy_booking = book(dropped.id, cy)
    dee_booking = book(finished.id, dee)
    ada_later = book(evening.id, ada)
    _post(api, headers, f"/api/v1/class-bookings/{fay_booking}/attend/", {})
    _post(api, headers, f"/api/v1/class-bookings/{ada_booking}/attend/", {})
    _post(api, headers, f"/api/v1/class-bookings/{dee_booking}/attend/", {})
    _post(api, headers, f"/api/v1/class-bookings/{ada_later}/no-show/", {})
    _post(
        api,
        headers,
        f"/api/v1/class-occurrences/{dropped.id}/cancel/",
        {"reason": "Closed the room"},
    )
    _force_occurrence_status(gym, clamped.id, ClassOccurrence.Status.CANCELLED)
    _force_occurrence_status(gym, finished.id, ClassOccurrence.Status.COMPLETED)
    assert cy_booking

    other_membership = add_staff(other, email="clothr-t1@example.test", role_key="TRAINER")
    other_trainer = _profile(other, other_membership, code="TR1", name="Other Trainer")
    other_type = other_api.post(
        "/api/v1/class-types/",
        _type_body(code="YOGA"),
        format="json",
        **other_headers,
    )
    assert other_type.status_code == 201, other_type.json()
    other_schedule = other_api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=other_type.json()["data"]["id"],
            trainer_staff_profile_id=str(other_trainer.id),
            weekdays=[6],
            local_start_time="18:00:00",
            local_end_time="18:30:00",
            effective_from="2026-03-08",
            effective_until="2026-03-08",
            location="Other Studio",
            location_is_exclusive=False,
            capacity=5,
        ),
        format="json",
        **other_headers,
        **_key("oth"),
    )
    assert other_schedule.status_code == 201, other_schedule.json()
    _disable(gym, "pt")

    report = _report(
        api,
        reception,
        "/api/v1/reports/classes/?date_from=2026-03-07&date_to=2026-03-09",
    )
    _assert_shape(report, CLASS_ROW_KEYS, CLASS_TOTAL_KEYS)
    assert report["timezone"] == "America/Los_Angeles"
    assert "unique_trainers" not in _row(report, "2026-03-08")
    march_seventh = _row(report, "2026-03-07")
    assert march_seventh["occurrences"] == 1
    assert march_seventh["cancelled_occurrences"] == 1
    assert march_seventh["available_capacity"] == 0
    assert march_seventh["attended_slots"] == 1
    assert march_seventh["reserved_slots"] == 1
    assert march_seventh["remaining_capacity"] == 0
    march_eighth = _row(report, "2026-03-08")
    assert march_eighth["occurrences"] == 3
    assert march_eighth["scheduled_occurrences"] == 1
    assert march_eighth["completed_occurrences"] == 1
    assert march_eighth["cancelled_occurrences"] == 1
    assert march_eighth["available_capacity"] == 7
    assert march_eighth["bookings"] == 4
    assert march_eighth["unique_members"] == 4
    assert march_eighth["booked_slots"] == 1
    assert march_eighth["attended_slots"] == 2
    assert march_eighth["no_show_slots"] == 0
    assert march_eighth["cancelled_slots"] == 1
    assert march_eighth["reserved_slots"] == 3
    assert march_eighth["remaining_capacity"] == 4
    march_ninth = _row(report, "2026-03-09")
    assert march_ninth["occurrences"] == 1
    assert march_ninth["scheduled_occurrences"] == 1
    assert march_ninth["available_capacity"] == 2
    assert march_ninth["no_show_slots"] == 1
    assert march_ninth["reserved_slots"] == 1
    assert march_ninth["remaining_capacity"] == 1
    for row in report["rows"]:
        assert row["occurrences"] == (
            row["scheduled_occurrences"]
            + row["completed_occurrences"]
            + row["cancelled_occurrences"]
        )
        slot_total = (
            row["booked_slots"]
            + row["attended_slots"]
            + row["no_show_slots"]
            + row["cancelled_slots"]
        )
        assert row["bookings"] == slot_total
        reserved = row["booked_slots"] + row["attended_slots"] + row["no_show_slots"]
        assert row["reserved_slots"] == reserved
        remaining = max(row["available_capacity"] - row["reserved_slots"], 0)
        assert row["remaining_capacity"] == remaining
    assert report["totals"]["occurrences"] == 5
    assert report["totals"]["available_capacity"] == 9
    assert report["totals"]["bookings"] == 6
    assert report["totals"]["unique_members"] == 5
    assert report["totals"]["unique_trainers"] == 2
    assert report["totals"]["reserved_slots"] == 5
    assert report["totals"]["remaining_capacity"] == 5
    daily_members = sum(row["unique_members"] for row in report["rows"])
    assert report["totals"]["unique_members"] != daily_members

    isolated = _report(
        other_api,
        other_headers,
        "/api/v1/reports/classes/?date_from=2026-03-08&date_to=2026-03-08",
    )
    assert isolated["totals"]["occurrences"] == 1
    assert isolated["totals"]["available_capacity"] == 5
    assert isolated["totals"]["bookings"] == 0
    assert isolated["gym"]["code"] == "CLOTHR"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        loaded = Gym.objects.get(id=gym.id)
        with CaptureQueriesContext(connection) as one_day:
            class_report(
                gym=loaded,
                date_from=date(2026, 3, 8),
                date_to=date(2026, 3, 8),
                generated_at=FROZEN,
            )
        with CaptureQueriesContext(connection) as full_year:
            class_report(
                gym=loaded,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 12, 31),
                generated_at=FROZEN,
            )
    assert len(one_day.captured_queries) == CLASS_REPORT_QUERY_COUNT
    assert len(full_year.captured_queries) == CLASS_REPORT_QUERY_COUNT
