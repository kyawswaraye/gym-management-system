"""PostgreSQL coverage for class occurrence and booking reads."""

from __future__ import annotations

import os
from datetime import date, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import connection, transaction
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.class_reads import execute_registered_read
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.audit.models import AuditLog
from apps.classes.booking_services import cancel_class_booking, create_class_booking
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.classes.services import create_class_schedule, create_class_type
from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, IdempotencyRecord
from apps.trainers.services import create_assignment
from tests.security.test_assistant_member_reads import _owner, _person
from tests.security.test_assistant_schedule import _goal, _profile
from tests.security.test_tenant_gym_settings import add_staff, jwt_settings

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _request(gym: Gym, actor: GymMembership, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().get("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_class_read"
    return request


def _read(gym: Gym, actor: GymMembership, tool_name: str, payload: dict[str, object]):
    return execute_registered_read(
        request=_request(gym, actor, frozenset({"classes.view"})),
        view=APIView(),
        tool_name=tool_name,
        payload=payload,
    )


def _occurrences(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_class_occurrences", payload)


def _bookings(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return _read(gym, actor, "search_bookings", payload)


def _ids(page: dict[str, object]) -> set[str]:
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows}


def _counts(gym: Gym, goal: Goal) -> tuple[int, ...]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            ClassOccurrence.objects.filter(gym=gym).count(),
            ClassBooking.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


def _schedule(
    gym: Gym,
    actor: GymMembership,
    class_type_id: UUID,
    trainer_id: UUID,
    *,
    on: date,
    start: time,
    end: time,
    capacity: int,
    location: str,
    request_id: str,
):
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    return create_class_schedule(
        gym=gym,
        actor=actor,
        class_type_id=class_type_id,
        trainer_staff_profile_id=trainer_id,
        weekdays=[on.weekday()],
        local_start_time=start,
        local_end_time=end,
        effective_from=on,
        effective_until=on,
        location=location,
        location_is_exclusive=False,
        capacity=capacity,
        request_id=request_id,
        ip_address=None,
        user_agent="",
    )


def _one_occurrence(gym: Gym, schedule_id: UUID) -> ClassOccurrence:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return ClassOccurrence.objects.get(gym=gym, class_schedule_id=schedule_id)


@override_settings(**jwt_settings())
def test_class_occurrence_and_booking_reads_use_stored_rows() -> None:
    gym, owner = _owner("CL01", "cl01-owner@example.test")
    other_gym, other_owner = _owner("CL02", "cl02-owner@example.test")
    today = gym_today(gym)
    early_day = today + timedelta(days=2)
    morning_day = today + timedelta(days=3)
    trainer = add_staff(gym, email="cl01-trainer@example.test", role_key="TRAINER")
    other_trainer = add_staff(gym, email="cl01-other@example.test", role_key="TRAINER")
    accountant = add_staff(gym, email="cl01-accountant@example.test", role_key="ACCOUNTANT")
    trainer_profile = _profile(gym, trainer, code="CLT", name="Maung")
    other_profile = _profile(gym, other_trainer, code="CLO", name="Hla")
    first = _person(
        gym,
        owner,
        "Aung",
        "Aung",
        phone="+959111111111",
        notes="secret-note",
    )
    second = _person(
        gym,
        owner,
        "Aung",
        "Aung",
        phone="+959222222222",
        notes="secret-note",
    )
    third = _person(gym, owner, "Mya", "Mya", phone="+959333333333", notes="secret-note")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        class_type = create_class_type(
            gym=gym,
            actor=owner,
            code="YOGA",
            name="Yoga",
            description="",
            default_duration_minutes=45,
            default_capacity=8,
            request_id="req_class_type",
            ip_address=None,
            user_agent="",
        )
        early_schedule = _schedule(
            gym,
            owner,
            class_type.id,
            other_profile.id,
            on=early_day,
            start=time(0, 15),
            end=time(1, 0),
            capacity=6,
            location="Studio A",
            request_id="req_early_schedule",
        )
        morning_schedule = _schedule(
            gym,
            owner,
            class_type.id,
            trainer_profile.id,
            on=morning_day,
            start=time(9, 0),
            end=time(10, 0),
            capacity=8,
            location="Studio B",
            request_id="req_morning_schedule",
        )
    early = _one_occurrence(gym, early_schedule.id)
    morning = _one_occurrence(gym, morning_schedule.id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        first_booking = create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=morning.id,
            member_id=first.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="secret-reason",
            request_id="req_book_first",
            ip_address=None,
            user_agent="",
        )
        second_booking = create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=morning.id,
            member_id=second.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="secret-reason",
            request_id="req_book_second",
            ip_address=None,
            user_agent="",
        )
        cancelled = create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=morning.id,
            member_id=third.id,
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="secret-reason",
            request_id="req_book_third",
            ip_address=None,
            user_agent="",
        )
        cancel_class_booking(
            gym=gym,
            actor=owner,
            booking_id=cancelled.id,
            reason="secret-cancel",
            request_id="req_cancel_third",
            ip_address=None,
            user_agent="",
        )
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=trainer_profile.id,
            member_id=first.id,
            effective_from=today,
            effective_until=None,
            reason="Program",
            request_id="req_class_assignment",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        foreign_type = create_class_type(
            gym=other_gym,
            actor=other_owner,
            code="YOGA",
            name="Yoga",
            description="",
            default_duration_minutes=45,
            default_capacity=8,
            request_id="req_foreign_type",
            ip_address=None,
            user_agent="",
        )
        foreign_trainer = add_staff(
            other_gym, email="cl02-trainer@example.test", role_key="TRAINER"
        )
        foreign_profile = _profile(other_gym, foreign_trainer, code="CLF", name="Foreign")
        foreign_schedule = _schedule(
            other_gym,
            other_owner,
            foreign_type.id,
            foreign_profile.id,
            on=early_day,
            start=time(9, 0),
            end=time(10, 0),
            capacity=4,
            location="Other studio",
            request_id="req_foreign_schedule",
        )
    foreign = _one_occurrence(other_gym, foreign_schedule.id)
    goal = _goal(gym, owner)
    before = _counts(gym, goal)
    zone = ZoneInfo(gym.timezone)
    assert gym.timezone == "Asia/Yangon"
    assert early.local_date == early_day
    assert early.starts_at.astimezone(zone).strftime("%H:%M") == "00:15"
    assert early.starts_at.astimezone(ZoneInfo("UTC")).date() != early.local_date

    span = {"date_from": early_day.isoformat(), "date_to": morning_day.isoformat()}
    listed = _occurrences(gym, owner, span)
    assert _ids(listed) == {str(early.id), str(morning.id)}
    by_id = {str(row["id"]): row for row in listed["results"]}
    early_row = by_id[str(early.id)]
    morning_row = by_id[str(morning.id)]
    assert early_row["class_name"] == "Yoga"
    assert early_row["local_date"] == early.local_date.isoformat()
    assert early_row["start_time"] == early.starts_at.astimezone(zone).strftime("%H:%M")
    assert early_row["end_time"] == early.ends_at.astimezone(zone).strftime("%H:%M")
    assert early_row["capacity"] == early.capacity == 6
    assert early_row["booked_count"] == 0
    assert early_row["status"] == ClassOccurrence.Status.SCHEDULED
    assert morning_row["capacity"] == morning.capacity == 8
    assert morning_row["booked_count"] == 2
    assert morning_row["start_time"] == "09:00"
    assert morning_row["end_time"] == "10:00"
    assert "secret-note" not in str(listed)
    exact = _occurrences(gym, owner, {"occurrence_id": str(early.id)})
    assert _ids(exact) == {str(early.id)}
    scheduled = _occurrences(gym, owner, {**span, "status": "SCHEDULED"})
    assert _ids(scheduled) == {str(early.id), str(morning.id)}
    assert _occurrences(gym, owner, {**span, "status": "CANCELLED"})["results"] == []
    utc_day = early.starts_at.astimezone(ZoneInfo("UTC")).date()
    assert (
        _occurrences(
            gym,
            owner,
            {"date_from": utc_day.isoformat(), "date_to": utc_day.isoformat()},
        )["results"]
        == []
    )
    assert (
        _occurrences(
            gym,
            owner,
            {"date_from": "2020-01-01", "date_to": "2020-01-01"},
        )["results"]
        == []
    )
    trainer_occurrences = _occurrences(gym, trainer, span)
    assert _ids(trainer_occurrences) == {str(morning.id)}
    with pytest.raises(ExecutionRejected) as foreign_occurrence:
        _occurrences(gym, owner, {"occurrence_id": str(foreign.id)})
    assert foreign_occurrence.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as unknown_occurrence:
        _occurrences(gym, owner, {"occurrence_id": str(uuid4())})
    assert unknown_occurrence.value.code == "not_found"
    with pytest.raises(ValidationError):
        _occurrences(gym, owner, {"occurrence_id": "not-an-occurrence"})
    with pytest.raises(ValidationError):
        _occurrences(gym, owner, {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _occurrences(gym, owner, {"date_from": "2026-12-02", "date_to": "2026-12-01"})
    with pytest.raises(ValidationError):
        _occurrences(gym, owner, {"class_type_id": str(class_type.id)})
    with pytest.raises(ExecutionRejected) as limited:
        _occurrences(gym, owner, {"limit": 26})
    assert limited.value.code == "invalid_limit"

    seen: set[str] = set()
    cursor: object = None
    for _page_number in range(4):
        payload: dict[str, object] = {"limit": 1, **span}
        if isinstance(cursor, str):
            payload["after"] = cursor
        page = _occurrences(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        seen |= found
        if page["has_more"] is False:
            break
        cursor = page["next_after"]
    assert seen == {str(early.id), str(morning.id)}
    with pytest.raises(ValidationError):
        _occurrences(other_gym, other_owner, {"limit": 1, "after": cursor})

    booked = _bookings(gym, owner, {"occurrence_id": str(morning.id), "status": "BOOKED"})
    assert _ids(booked) == {str(first_booking.id), str(second_booking.id)}
    assert booked["ambiguous"] is True
    numbers = {row["member_number"] for row in booked["results"]}
    names = {row["member_name"] for row in booked["results"]}
    assert numbers == {first.member_number, second.member_number}
    assert names == {"Aung Aung"}
    assert first.member_number != second.member_number
    for row in booked["results"]:
        assert row["class_occurrence_id"] == str(morning.id)
        assert row["class_name"] == "Yoga"
        assert row["local_date"] == morning.local_date.isoformat()
        assert row["start_time"] == "09:00"
        assert row["end_time"] == "10:00"
        assert row["status"] == ClassBooking.Status.BOOKED
        assert row["created_at"]
        assert row["cancelled_at"] is None
    cancelled_page = _bookings(
        gym, owner, {"occurrence_id": str(morning.id), "status": "CANCELLED"}
    )
    assert _ids(cancelled_page) == {str(cancelled.id)}
    assert cancelled_page["results"][0]["member_number"] == third.member_number
    assert cancelled_page["results"][0]["cancelled_at"]
    assert cancelled_page["ambiguous"] is False
    one_member = _bookings(gym, owner, {"member_id": str(second.id)})
    assert _ids(one_member) == {str(second_booking.id)}
    dated = _bookings(
        gym,
        owner,
        {
            "date_from": morning_day.isoformat(),
            "date_to": morning_day.isoformat(),
            "status": "BOOKED",
        },
    )
    assert _ids(dated) == {str(first_booking.id), str(second_booking.id)}
    assert (
        _bookings(
            gym,
            owner,
            {"date_from": early_day.isoformat(), "date_to": early_day.isoformat()},
        )["results"]
        == []
    )
    trainer_bookings = _bookings(gym, trainer, {"occurrence_id": str(morning.id)})
    assert _ids(trainer_bookings) == {str(first_booking.id)}
    assert trainer_bookings["ambiguous"] is False
    with pytest.raises(ExecutionRejected) as hidden_occurrence:
        _bookings(gym, trainer, {"occurrence_id": str(early.id)})
    assert hidden_occurrence.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as foreign_booking:
        _bookings(gym, owner, {"occurrence_id": str(foreign.id)})
    assert foreign_booking.value.code == "not_found"
    assert _bookings(gym, owner, {"member_id": str(uuid4())})["results"] == []
    with pytest.raises(ValidationError):
        _bookings(gym, owner, {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _bookings(gym, owner, {"member_id": "not-a-member"})
    with pytest.raises(ValidationError):
        _bookings(gym, owner, {"after": "not-a-cursor", "occurrence_id": str(morning.id)})
    leaked = str(booked) + str(cancelled_page)
    assert "secret-note" not in leaked
    assert "secret-reason" not in leaked
    assert "secret-cancel" not in leaked
    assert "secret street" not in leaked
    assert "+959111111111" not in leaked

    booking_seen: set[str] = set()
    booking_cursor: object = None
    for _page_number in range(4):
        payload = {"occurrence_id": str(morning.id), "limit": 1}
        if isinstance(booking_cursor, str):
            payload["after"] = booking_cursor
        page = _bookings(gym, owner, payload)
        found = _ids(page)
        assert len(found) == 1
        booking_seen |= found
        if page["has_more"] is False:
            break
        booking_cursor = page["next_after"]
    assert booking_seen == {str(first_booking.id), str(second_booking.id), str(cancelled.id)}
    with pytest.raises(ValidationError):
        _bookings(other_gym, other_owner, {"limit": 1, "after": booking_cursor})

    with pytest.raises(PermissionDenied):
        execute_registered_read(
            request=_request(gym, accountant, frozenset({"gym.view"})),
            view=APIView(),
            tool_name="search_class_occurrences",
            payload={},
        )
    with pytest.raises(PermissionDenied):
        execute_registered_read(
            request=_request(gym, accountant, frozenset({"gym.view"})),
            view=APIView(),
            tool_name="search_bookings",
            payload={},
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="pt").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _occurrences(gym, owner, {})
    with pytest.raises(PermissionDenied):
        _bookings(gym, owner, {})

    assert _counts(gym, goal) == before
    assert isinstance(early.id, UUID)
    assert isinstance(first_booking.id, UUID)
