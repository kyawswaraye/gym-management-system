"""Future attendance and no-show are rejected in the service and the database."""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction
from django.db.utils import InternalError, ProgrammingError
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.classes.booking_services import attend_class_booking, no_show_class_booking
from apps.classes.exceptions import BookingStateConflict
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership
from apps.trainers.models import PTBooking, ServiceSessionLedger
from apps.trainers.pt_booking_services import attend_pt_booking, no_show_pt_booking
from tests.security.test_bookings import _client, _key, _package
from tests.security.test_class_schedules import _schedule_body, _type_body
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _member, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _grant(api, headers, *, member_id: str, package_id: str, day: str) -> str:
    created = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": day,
            "complimentary": True,
            "reason": "Terminal time coverage",
        },
        format="json",
        **headers,
        **_key("grant"),
    )
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["member_service_package"]["id"])


def _effects(gym: Gym, booking_id: str, *, kind: str) -> tuple[int, int, str]:
    field = "pt_booking_id" if kind == "pt" else "class_booking_id"
    actions = (
        ("PT_BOOKING_ATTENDED", "PT_BOOKING_NO_SHOW")
        if kind == "pt"
        else ("CLASS_BOOKING_ATTENDED", "CLASS_BOOKING_NO_SHOW")
    )
    model = PTBooking if kind == "pt" else ClassBooking
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            ServiceSessionLedger.objects.filter(**{field: booking_id}).count(),
            AuditLog.objects.filter(gym=gym, target_id=booking_id, action__in=actions).count(),
            str(model.objects.get(id=booking_id).status),
        )


def _runtime(gym: Gym, sql: str, params: list[str]) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(sql, params)


@override_settings(**jwt_settings())
def test_future_terminal_booking_transitions_fail_without_effects() -> None:
    client, platform_access = platform_token("term-platform@example.test")
    payload = provision(
        client, platform_access, code="TERM-A", owner_email="term-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Gym.objects.filter(id=gym.id).update(timezone="America/Los_Angeles")
    gym.refresh_from_db()
    zone = ZoneInfo(gym.timezone)
    trainer_membership = add_staff(gym, email="term-ada@example.test", role_key="TRAINER")
    boundary_membership = add_staff(gym, email="term-bea@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TERM-ADA", name="Ada")
    boundary_trainer = _profile(gym, boundary_membership, code="TERM-BEA", name="Bea")
    api = _client()
    headers = auth_headers(staff_token(owner))
    member_id = _member(api, headers, first_name="Nila")
    other_id = _member(api, headers, first_name="Omar")
    today = gym_today(gym)
    past_day = today - timedelta(days=2)
    future_day = today + timedelta(days=2)
    assert (
        _assign(
            api,
            headers,
            trainer_id=str(trainer.id),
            member_id=member_id,
            start=past_day.isoformat(),
        ).status_code
        == 201
    )
    assert (
        _assign(
            api,
            headers,
            trainer_id=str(boundary_trainer.id),
            member_id=other_id,
            start=past_day.isoformat(),
        ).status_code
        == 201
    )
    package_id = _package(api, headers, package_type="PT", code="PT_TERM")
    grant_id = _grant(
        api, headers, member_id=member_id, package_id=package_id, day=past_day.isoformat()
    )
    now = timezone.now()
    # 14:00-15:00 gym-local on the same future day as the 10:00 class, so the
    # clock time of this run cannot make the two sessions overlap.
    future_start = datetime.combine(future_day, time(14, 0), tzinfo=zone)
    future_end = future_start + timedelta(hours=1)
    future = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": future_start.isoformat(),
            "ends_at": future_end.isoformat(),
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("future-pt"),
    )
    assert future.status_code == 201, future.json()
    future_id = str(future.json()["data"]["id"])
    before = _effects(gym, future_id, kind="pt")
    for action in ("attend", "no-show"):
        rejected = api.post(
            f"/api/v1/pt-bookings/{future_id}/{action}/",
            {},
            format="json",
            **headers,
            **_key(f"future-{action}"),
        )
        assert rejected.status_code == 409, rejected.json()
        assert rejected.json()["error"]["code"] == "BOOKING_STATE_CONFLICT"
        assert _effects(gym, future_id, kind="pt") == before == (0, 0, "BOOKED")
    attend_sql = """
        UPDATE trainers_pt_booking
        SET status = 'ATTENDED',
            attended_at = statement_timestamp(),
            attended_by_membership_id = %s,
            updated_at = statement_timestamp(),
            updated_by_membership_id = %s
        WHERE gym_id = %s AND id = %s
    """
    no_show_sql = """
        UPDATE trainers_pt_booking
        SET status = 'NO_SHOW',
            no_show_at = statement_timestamp(),
            no_show_by_membership_id = %s,
            updated_at = statement_timestamp(),
            updated_by_membership_id = %s
        WHERE gym_id = %s AND id = %s
    """
    params = [str(owner.id), str(owner.id), str(gym.id), future_id]
    with pytest.raises((InternalError, ProgrammingError), match="before the start"):
        _runtime(gym, attend_sql, params)
    with pytest.raises((InternalError, ProgrammingError), match="before the end"):
        _runtime(gym, no_show_sql, params)
    assert _effects(gym, future_id, kind="pt") == before

    def boundary(status: str, start_expr: str, end_expr: str) -> None:
        attended = "statement_timestamp()" if status == "ATTENDED" else "NULL"
        no_show = "statement_timestamp()" if status == "NO_SHOW" else "NULL"
        attended_by = "%s" if status == "ATTENDED" else "NULL"
        no_show_by = "%s" if status == "NO_SHOW" else "NULL"
        values = [
            str(gym.id),
            str(boundary_trainer.id),
            other_id,
            str(owner.id),
            str(owner.id),
        ]
        if status == "ATTENDED":
            values.append(str(owner.id))
        if status == "NO_SHOW":
            values.append(str(owner.id))
        values.extend([str(owner.id), str(gym.id)])
        _runtime(
            gym,
            f"""
            DO $boundary$
            DECLARE
              booking_id uuid := gen_random_uuid();
            BEGIN
              INSERT INTO trainers_pt_booking (
                id, gym_id, created_at, updated_at, trainer_staff_profile_id, member_id,
                member_service_package_id, starts_at, ends_at, unmetered, unmetered_reason,
                status, reason, created_by_membership_id, updated_by_membership_id
              ) VALUES (
                booking_id, %s, statement_timestamp(), statement_timestamp(),
                %s, %s, NULL, {start_expr}, {end_expr}, true, 'Boundary', 'BOOKED', '',
                %s, %s
              );
              UPDATE trainers_pt_booking
              SET status = '{status}',
                  attended_at = {attended},
                  attended_by_membership_id = {attended_by},
                  no_show_at = {no_show},
                  no_show_by_membership_id = {no_show_by},
                  updated_at = statement_timestamp(),
                  updated_by_membership_id = %s
              WHERE gym_id = %s AND id = booking_id;
            END
            $boundary$
            """,
            values,
        )

    boundary("ATTENDED", "statement_timestamp()", "statement_timestamp() + interval '30 minutes'")
    with pytest.raises((InternalError, ProgrammingError), match="before the start"):
        boundary(
            "ATTENDED",
            "statement_timestamp() + interval '2 hours'",
            "statement_timestamp() + interval '3 hours'",
        )
    boundary(
        "NO_SHOW",
        "statement_timestamp() - interval '3 hours'",
        "statement_timestamp() - interval '2 hours'",
    )
    with pytest.raises((InternalError, ProgrammingError), match="before the end"):
        boundary(
            "NO_SHOW",
            "statement_timestamp() + interval '4 hours'",
            "statement_timestamp() + interval '5 hours'",
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        exact = list(
            PTBooking.objects.filter(gym=gym, member_id=other_id).values_list("status", flat=True)
        )
        assert sorted(exact) == ["ATTENDED", "NO_SHOW"]
        assert not ServiceSessionLedger.objects.filter(
            gym=gym, pt_booking__member_id=other_id
        ).exists()

    ended_start = now - timedelta(minutes=90)
    ended_end = now - timedelta(minutes=60)
    ended = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": ended_start.isoformat(),
            "ends_at": ended_end.isoformat(),
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("ended-pt"),
    )
    assert ended.status_code == 201, ended.json()
    ended_id = ended.json()["data"]["id"]
    early = _effects(gym, str(ended_id), kind="pt")
    with pytest.raises(BookingStateConflict):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with patch(
                "apps.trainers.pt_booking_services.timezone.now",
                return_value=ended_start - timedelta(microseconds=1),
            ):
                attend_pt_booking(
                    gym=gym,
                    actor=owner,
                    booking_id=ended_id,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
    assert _effects(gym, str(ended_id), kind="pt") == early
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with patch(
            "apps.trainers.pt_booking_services.timezone.now",
            return_value=ended_start,
        ):
            attend_pt_booking(
                gym=gym,
                actor=owner,
                booking_id=ended_id,
                request_id=f"req_{uuid4().hex}",
                ip_address=None,
                user_agent="",
            )
    quiet_end = now - timedelta(minutes=5)
    quiet_start = quiet_end - timedelta(minutes=20)
    quiet_booking = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": quiet_start.isoformat(),
            "ends_at": quiet_end.isoformat(),
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("quiet-pt"),
    )
    assert quiet_booking.status_code == 201, quiet_booking.json()
    quiet_id = quiet_booking.json()["data"]["id"]
    quiet_before = _effects(gym, str(quiet_id), kind="pt")
    with pytest.raises(BookingStateConflict):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with patch(
                "apps.trainers.pt_booking_services.timezone.now",
                return_value=quiet_end - timedelta(microseconds=1),
            ):
                no_show_pt_booking(
                    gym=gym,
                    actor=owner,
                    booking_id=quiet_id,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
    assert _effects(gym, str(quiet_id), kind="pt") == quiet_before
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with patch(
            "apps.trainers.pt_booking_services.timezone.now",
            return_value=quiet_end,
        ):
            no_show_pt_booking(
                gym=gym,
                actor=owner,
                booking_id=quiet_id,
                request_id=f"req_{uuid4().hex}",
                ip_address=None,
                user_agent="",
            )
    local_now = timezone.now().astimezone(zone)
    same_day_past = local_now - timedelta(hours=1)
    same_day_future = local_now + timedelta(hours=1)
    if same_day_past.date() == same_day_future.date():
        assert same_day_past.date() == local_now.date()
    allowed = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": same_day_past.isoformat(),
            "ends_at": (same_day_past + timedelta(minutes=30)).isoformat(),
            "unmetered": True,
            "unmetered_reason": "Past instant on the gym clock",
        },
        format="json",
        **headers,
        **_key("zone-past"),
    )
    assert allowed.status_code == 201, allowed.json()
    attended = api.post(
        f"/api/v1/pt-bookings/{allowed.json()['data']['id']}/attend/",
        {},
        format="json",
        **headers,
        **_key("zone-attend"),
    )
    assert attended.status_code == 200, attended.json()
    blocked = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": same_day_future.isoformat(),
            "ends_at": (same_day_future + timedelta(minutes=30)).isoformat(),
            "unmetered": True,
            "unmetered_reason": "Future instant on the gym clock",
        },
        format="json",
        **headers,
        **_key("zone-future"),
    )
    assert blocked.status_code == 201, blocked.json()
    zone_denied = api.post(
        f"/api/v1/pt-bookings/{blocked.json()['data']['id']}/attend/",
        {},
        format="json",
        **headers,
        **_key("zone-deny"),
    )
    assert zone_denied.status_code == 409
    assert zone_denied.json()["error"]["code"] == "BOOKING_STATE_CONFLICT"

    class_type = api.post("/api/v1/class-types/", _type_body(code="TERM"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    class_package = _package(
        api, headers, package_type="CLASS", code="CLS_TERM", class_type_id=type_id
    )
    class_grant = _grant(
        api, headers, member_id=member_id, package_id=class_package, day=past_day.isoformat()
    )

    def occurrence(day: object, start: str, end: str, location: str) -> ClassOccurrence:
        created = api.post(
            "/api/v1/class-schedules/",
            _schedule_body(
                class_type_id=type_id,
                trainer_staff_profile_id=str(trainer.id),
                capacity=4,
                location=location,
                location_is_exclusive=False,
                effective_from=day.isoformat(),
                effective_until=day.isoformat(),
                weekdays=[day.weekday()],
                local_start_time=start,
                local_end_time=end,
            ),
            format="json",
            **headers,
            **_key(location.replace(" ", "-")),
        )
        assert created.status_code == 201, created.json()
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            return ClassOccurrence.objects.get(class_schedule_id=created.json()["data"]["id"])

    future_occurrence = occurrence(future_day, "10:00:00", "11:00:00", "Future room")
    past_occurrence = occurrence(past_day, "10:00:00", "11:00:00", "Past room")
    later_occurrence = occurrence(past_day, "12:00:00", "13:00:00", "Later room")
    future_class = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(future_occurrence.id),
            "member_id": member_id,
            "member_service_package_id": class_grant,
        },
        format="json",
        **headers,
        **_key("future-class"),
    )
    assert future_class.status_code == 201, future_class.json()
    future_class_id = str(future_class.json()["data"]["id"])
    class_before = _effects(gym, future_class_id, kind="class")
    class_rejected = api.post(
        f"/api/v1/class-bookings/{future_class_id}/attend/",
        {},
        format="json",
        **headers,
        **_key("future-class-attend"),
    )
    assert class_rejected.status_code == 409, class_rejected.json()
    assert class_rejected.json()["error"]["code"] == "BOOKING_STATE_CONFLICT"
    class_no_show = api.post(
        f"/api/v1/class-bookings/{future_class_id}/no-show/",
        {},
        format="json",
        **headers,
        **_key("future-class-noshow"),
    )
    assert class_no_show.status_code == 409, class_no_show.json()
    with pytest.raises((InternalError, ProgrammingError), match="before the start"):
        _runtime(
            gym,
            """
            UPDATE classes_class_booking
            SET status = 'ATTENDED',
                attended_at = statement_timestamp(),
                attended_by_membership_id = %s,
                updated_at = statement_timestamp(),
                updated_by_membership_id = %s
            WHERE gym_id = %s AND id = %s
            """,
            [str(owner.id), str(owner.id), str(gym.id), future_class_id],
        )
    assert _effects(gym, future_class_id, kind="class") == class_before == (0, 0, "BOOKED")

    def book_class(occurrence_id: object) -> str:
        created = api.post(
            "/api/v1/class-bookings/",
            {
                "class_occurrence_id": str(occurrence_id),
                "member_id": member_id,
                "member_service_package_id": class_grant,
            },
            format="json",
            **headers,
            **_key(f"class-{occurrence_id}"),
        )
        assert created.status_code == 201, created.json()
        return str(created.json()["data"]["id"])

    attend_id = book_class(past_occurrence.id)
    no_show_id = book_class(later_occurrence.id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        past_occurrence.refresh_from_db()
        later_occurrence.refresh_from_db()
    missed = _effects(gym, attend_id, kind="class")
    with pytest.raises(BookingStateConflict):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with patch(
                "apps.classes.booking_services.timezone.now",
                return_value=past_occurrence.starts_at - timedelta(microseconds=1),
            ):
                attend_class_booking(
                    gym=gym,
                    actor=owner,
                    booking_id=attend_id,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
    assert _effects(gym, attend_id, kind="class") == missed
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with patch(
            "apps.classes.booking_services.timezone.now",
            return_value=past_occurrence.starts_at,
        ):
            attend_class_booking(
                gym=gym,
                actor=owner,
                booking_id=attend_id,
                request_id=f"req_{uuid4().hex}",
                ip_address=None,
                user_agent="",
            )
    quiet = _effects(gym, no_show_id, kind="class")
    with pytest.raises(BookingStateConflict):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with patch(
                "apps.classes.booking_services.timezone.now",
                return_value=later_occurrence.ends_at - timedelta(microseconds=1),
            ):
                no_show_class_booking(
                    gym=gym,
                    actor=owner,
                    booking_id=no_show_id,
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
    assert _effects(gym, no_show_id, kind="class") == quiet
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with patch(
            "apps.classes.booking_services.timezone.now",
            return_value=later_occurrence.ends_at,
        ):
            no_show_class_booking(
                gym=gym,
                actor=owner,
                booking_id=no_show_id,
                request_id=f"req_{uuid4().hex}",
                ip_address=None,
                user_agent="",
            )
    assert _effects(gym, attend_id, kind="class")[0] == 1
    assert _effects(gym, no_show_id, kind="class")[0] == 1
