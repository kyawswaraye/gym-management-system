"""PT and class booking commands, ledger effects, and database guards."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import IntegrityError, InternalError, ProgrammingError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.audit.models import AuditLog
from apps.classes.booking_services import create_class_booking
from apps.classes.exceptions import ClassFull, ScheduleConflict
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership, GymMembershipRole, GymSettings, Role
from apps.trainers.exceptions import LedgerBounds
from apps.trainers.models import PTBooking, ServiceSessionLedger
from apps.trainers.pt_booking_services import attend_pt_booking
from tests.security.test_class_schedules import _profile, _schedule_body, _type_body
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _member

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _client():
    from rest_framework.test import APIClient

    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _key(prefix: str) -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"{prefix}-{uuid4().hex}"}


def _package(
    api, headers, *, package_type: str, code: str, class_type_id: str | None = None
) -> str:
    body: dict[str, object] = {
        "type": package_type,
        "code": code,
        "name": code.replace("_", " ").title(),
        "session_count": 2,
        "validity_value": 6,
        "validity_unit": "MONTH",
        "price": "80.00",
    }
    if class_type_id is not None:
        body["class_type_id"] = class_type_id
    created = api.post("/api/v1/service-packages/", body, format="json", **headers)
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["id"])


def _grant(api, headers, *, member_id: str, package_id: str) -> str:
    created = api.post(
        "/api/v1/member-service-packages/",
        {
            "member_id": member_id,
            "service_package_id": package_id,
            "business_date": "2026-10-01",
            "complimentary": True,
            "reason": "Booking coverage",
        },
        format="json",
        **headers,
        **_key("grant"),
    )
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["member_service_package"]["id"])


@override_settings(**jwt_settings())
def test_pt_booking_consumes_once_and_replays() -> None:
    client, platform_access = platform_token("pt-book-platform@example.test")
    payload = provision(
        client, platform_access, code="PTB-A", owner_email="pt-book-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pt-book-trainer@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-PTB", name="Ada")
    api = _client()
    headers = auth_headers(staff_token(owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    member_id = _member(api, headers, first_name="Nila")
    assigned = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=member_id,
        start="2026-10-01",
    )
    assert assigned.status_code == 201, assigned.json()
    package_id = _package(api, headers, package_type="PT", code="PT_BOOK")
    grant_id = _grant(api, headers, member_id=member_id, package_id=package_id)
    body = {
        "trainer_staff_profile_id": str(trainer.id),
        "member_id": member_id,
        "starts_at": "2026-10-18T03:00:00Z",
        "ends_at": "2026-10-18T04:00:00Z",
        "member_service_package_id": grant_id,
    }
    missing = api.post("/api/v1/pt-bookings/", body, format="json", **headers)
    assert missing.status_code == 400
    create_key = _key("pt-create")
    created = api.post("/api/v1/pt-bookings/", body, format="json", **headers, **create_key)
    assert created.status_code == 201, created.json()
    booking_id = created.json()["data"]["id"]
    replay = api.post("/api/v1/pt-bookings/", body, format="json", **headers, **create_key)
    assert replay.status_code == 201
    assert replay.content == created.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert PTBooking.objects.filter(gym=gym).count() == 1
        assert AuditLog.objects.filter(gym=gym, action="PT_BOOKING_CREATED").count() == 1
    mismatch = api.post(
        "/api/v1/pt-bookings/",
        {**body, "ends_at": "2026-10-18T05:00:00Z"},
        format="json",
        **headers,
        **create_key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    overlap = api.post(
        "/api/v1/pt-bookings/",
        {**body, "starts_at": "2026-10-18T03:30:00Z", "ends_at": "2026-10-18T04:30:00Z"},
        format="json",
        **headers,
        **_key("pt-overlap"),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "SCHEDULE_CONFLICT"
    hidden = api.get(f"/api/v1/pt-bookings/{booking_id}/", **trainer_headers)
    assert hidden.status_code == 200
    other = add_staff(gym, email="pt-book-other@example.test", role_key="TRAINER")
    other_profile = _profile(gym, other, code="TR-OTH", name="Bea")
    other_headers = auth_headers(staff_token(other))
    assert api.get(f"/api/v1/pt-bookings/{booking_id}/", **other_headers).status_code == 404
    assert api.get("/api/v1/pt-bookings/", **other_headers).json()["data"] == []
    attend = api.post(
        f"/api/v1/pt-bookings/{booking_id}/attend/",
        {},
        format="json",
        **headers,
        **_key("pt-attend"),
    )
    assert attend.status_code == 200, attend.json()
    again = api.post(
        f"/api/v1/pt-bookings/{booking_id}/attend/",
        {},
        format="json",
        **headers,
        **_key("pt-attend-2"),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "BOOKING_STATE_CONFLICT"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            ServiceSessionLedger.objects.filter(
                gym=gym,
                pt_booking_id=booking_id,
                reason="ATTENDED",
                session_delta=-1,
            ).count()
            == 1
        )
    assert other_profile.id
    wide = api.get(
        "/api/v1/pt-bookings/?view=calendar&date_from=2026-10-01&date_to=2026-12-01",
        **headers,
    )
    assert wide.status_code == 400
    with CaptureQueriesContext(connection) as captured:
        listed = api.get("/api/v1/pt-bookings/?limit=25", **headers)
    assert listed.status_code == 200
    assert any("LIMIT" in query["sql"] for query in captured.captured_queries)


@override_settings(**jwt_settings())
def test_class_booking_capacity_policy_cutoff_and_occurrence_cancel() -> None:
    client, platform_access = platform_token("cls-book-platform@example.test")
    payload = provision(
        client, platform_access, code="CLB-A", owner_email="cls-book-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="CLB-B", owner_email="cls-book-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    receptionist = add_staff(gym, email="cls-book-desk@example.test", role_key="RECEPTIONIST")
    trainer_membership = add_staff(gym, email="cls-book-trainer@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-CLB", name="Ada")
    api = _client()
    headers = auth_headers(staff_token(owner))
    desk_headers = auth_headers(staff_token(receptionist))
    class_type = api.post("/api/v1/class-types/", _type_body(code="YOGA"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=1,
            effective_from="2026-10-12",
            effective_until="2026-10-12",
        ),
        format="json",
        **headers,
        **_key("cls-sched"),
    )
    assert schedule.status_code == 201, schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(gym=gym)
    member_id = _member(api, headers, first_name="Mina")
    second_id = _member(api, headers, first_name="Omar")
    package_id = _package(
        api, headers, package_type="CLASS", code="CLS_BOOK", class_type_id=type_id
    )
    grant_id = _grant(api, headers, member_id=member_id, package_id=package_id)
    second_grant = _grant(api, headers, member_id=second_id, package_id=package_id)
    body = {
        "class_occurrence_id": str(occurrence.id),
        "member_id": member_id,
        "member_service_package_id": grant_id,
    }
    created = api.post(
        "/api/v1/class-bookings/", body, format="json", **headers, **_key("cls-create")
    )
    assert created.status_code == 201, created.json()
    booking_id = created.json()["data"]["id"]
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_get_functiondef(
              'app_class_occurrence_blocks_regeneration(uuid,uuid)'::regprocedure
            )
            """
        )
        definition = cursor.fetchone()
    assert definition is not None
    assert "BOOKED" in definition[0]
    patched = api.patch(
        f"/api/v1/class-schedules/{schedule.json()['data']['id']}/",
        {"effective_until": "2026-11-02"},
        format="json",
        **headers,
        **_key("cls-patch-booked"),
    )
    assert patched.status_code == 200, patched.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert ClassOccurrence.objects.filter(id=occurrence.id).exists()
        assert ClassBooking.objects.filter(
            id=booking_id, class_occurrence_id=occurrence.id
        ).exists()
    full = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(occurrence.id),
            "member_id": second_id,
            "member_service_package_id": second_grant,
        },
        format="json",
        **headers,
        **_key("cls-full"),
    )
    assert full.status_code == 409
    assert full.json()["error"]["code"] == "CLASS_FULL"
    cross = api.post(
        "/api/v1/class-bookings/",
        body,
        format="json",
        **auth_headers(
            staff_token(
                GymMembership.objects.select_related("user").get(
                    id=other_payload["owner"]["gym_membership"]["id"]
                )
            )
        ),
        **_key("cls-cross"),
    )
    assert cross.status_code == 404
    assert other.id
    no_show = api.post(
        f"/api/v1/class-bookings/{booking_id}/no-show/",
        {},
        format="json",
        **headers,
        **_key("cls-noshow"),
    )
    assert no_show.status_code == 200, no_show.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            ServiceSessionLedger.objects.filter(
                gym=gym, class_booking_id=booking_id, reason="POLICY_NO_SHOW"
            ).count()
            == 1
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(class_no_show_consumes_session=False)
    second_schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=2,
            location="Studio B",
            location_is_exclusive=False,
            effective_from="2026-10-13",
            effective_until="2026-10-13",
            weekdays=[1],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("cls-sched-2"),
    )
    assert second_schedule.status_code == 201, second_schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence_two = ClassOccurrence.objects.get(
            class_schedule_id=second_schedule.json()["data"]["id"]
        )
    skipped = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(occurrence_two.id),
            "member_id": second_id,
            "member_service_package_id": second_grant,
        },
        format="json",
        **headers,
        **_key("cls-policy"),
    )
    assert skipped.status_code == 201, skipped.json()
    quiet = api.post(
        f"/api/v1/class-bookings/{skipped.json()['data']['id']}/no-show/",
        {},
        format="json",
        **headers,
        **_key("cls-quiet"),
    )
    assert quiet.status_code == 200, quiet.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not ServiceSessionLedger.objects.filter(
            class_booking_id=skipped.json()["data"]["id"]
        ).exists()
        GymSettings.objects.filter(gym=gym).update(
            class_no_show_consumes_session=True,
            class_cancellation_cutoff_minutes=100000,
        )
    late_schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=2,
            location="Studio Late",
            location_is_exclusive=False,
            effective_from="2026-11-03",
            effective_until="2026-11-03",
            weekdays=[1],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("cls-sched-late"),
    )
    assert late_schedule.status_code == 201, late_schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        late_occurrence = ClassOccurrence.objects.get(
            class_schedule_id=late_schedule.json()["data"]["id"]
        )
    late_booking = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(late_occurrence.id),
            "member_id": second_id,
            "member_service_package_id": second_grant,
        },
        format="json",
        **headers,
        **_key("cls-late-book"),
    )
    assert late_booking.status_code == 201, late_booking.json()
    late_id = late_booking.json()["data"]["id"]
    late = api.post(
        f"/api/v1/class-bookings/{late_id}/cancel/",
        {},
        format="json",
        **desk_headers,
        **_key("cls-late-desk"),
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "CLASS_CUTOFF"
    owner_late = api.post(
        f"/api/v1/class-bookings/{late_id}/cancel/",
        {"reason": "Closed the room"},
        format="json",
        **headers,
        **_key("cls-late-owner"),
    )
    assert owner_late.status_code == 200, owner_late.json()
    blocked = api.post(
        f"/api/v1/class-occurrences/{occurrence.id}/cancel/",
        {"reason": "Already attended history"},
        format="json",
        **headers,
        **_key("occ-terminal"),
    )
    assert blocked.status_code == 409
    third = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=2,
            location="Studio C",
            location_is_exclusive=False,
            effective_from="2026-11-02",
            effective_until="2026-11-02",
            weekdays=[0],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("cls-sched-3"),
    )
    assert third.status_code == 201, third.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        third_occurrence = ClassOccurrence.objects.get(class_schedule_id=third.json()["data"]["id"])
    held = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(third_occurrence.id),
            "member_id": member_id,
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("cls-held"),
    )
    assert held.status_code == 201, held.json()
    cancelled = api.post(
        f"/api/v1/class-occurrences/{third_occurrence.id}/cancel/",
        {"reason": "Trainer unavailable"},
        format="json",
        **headers,
        **_key("occ-cancel"),
    )
    assert cancelled.status_code == 200, cancelled.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = ClassBooking.objects.get(id=held.json()["data"]["id"])
        assert stored.status == ClassBooking.Status.CANCELLED
        assert not ServiceSessionLedger.objects.filter(class_booking_id=stored.id).exists()


@override_settings(**jwt_settings())
def test_booking_tables_reject_forged_ledger_and_cross_gym_reads() -> None:
    client, platform_access = platform_token("book-sql-platform@example.test")
    payload = provision(
        client, platform_access, code="SQL-A", owner_email="book-sql-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    with pytest.raises((InternalError, ProgrammingError, IntegrityError)):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    INSERT INTO trainers_service_session_ledger (
                      id, gym_id, created_at, updated_at, member_service_package_id,
                      session_delta, reason, occurred_at, created_by_membership_id
                    ) VALUES (
                      gen_random_uuid(), %s, now(), now(), gen_random_uuid(),
                      -1, 'ATTENDED', now(), %s
                    )
                    """,
                    [str(gym.id), str(owner.id)],
                )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'classes_class_booking', 'DELETE'),
              has_table_privilege('gym_runtime', 'trainers_pt_booking', 'DELETE'),
              has_table_privilege('gym_runtime', 'classes_class_booking', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_pt_booking', 'UPDATE')
            """
        )
        assert cursor.fetchone() == (False, False, True, True)


@override_settings(**jwt_settings())
def test_unmetered_overlap_and_last_session_race() -> None:
    client, platform_access = platform_token("book-race-platform@example.test")
    payload = provision(
        client, platform_access, code="RACE-A", owner_email="book-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="book-race-trainer@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-RACE", name="Ada")
    api = _client()
    headers = auth_headers(staff_token(owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    member_id = _member(api, headers, first_name="Race")
    assigned = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=member_id, start="2026-10-01"
    )
    assert assigned.status_code == 201, assigned.json()
    denied = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": "2026-10-18T03:00:00Z",
            "ends_at": "2026-10-18T04:00:00Z",
            "unmetered": True,
            "unmetered_reason": "Owner exception",
        },
        format="json",
        **trainer_headers,
        **_key("unmetered-trainer"),
    )
    assert denied.status_code == 403
    package = api.post(
        "/api/v1/service-packages/",
        {
            "type": "PT",
            "code": "PT_ONE",
            "name": "One session",
            "session_count": 1,
            "validity_value": 6,
            "validity_unit": "MONTH",
            "price": "40.00",
        },
        format="json",
        **headers,
    )
    assert package.status_code == 201, package.json()
    grant_id = _grant(
        api, headers, member_id=member_id, package_id=str(package.json()["data"]["id"])
    )
    first = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": "2026-10-18T03:00:00Z",
            "ends_at": "2026-10-18T04:00:00Z",
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("race-a"),
    )
    second = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": "2026-10-18T05:00:00Z",
            "ends_at": "2026-10-18T06:00:00Z",
            "member_service_package_id": grant_id,
        },
        format="json",
        **headers,
        **_key("race-b"),
    )
    assert first.status_code == 201, first.json()
    assert second.status_code == 201, second.json()
    barrier = Barrier(2, timeout=20)
    booking_ids = [first.json()["data"]["id"], second.json()["data"]["id"]]

    def attend(booking_id: str) -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    attend_pt_booking(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        booking_id=UUID(booking_id),
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except LedgerBounds:
                    return "blocked"
            return "attended"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attend, booking_ids))
    assert results == ["attended", "blocked"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            ServiceSessionLedger.objects.filter(
                gym=gym, reason="ATTENDED", member_service_package_id=grant_id
            ).count()
            == 1
        )
    class_type = api.post("/api/v1/class-types/", _type_body(code="RACE"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=1,
            effective_from="2026-10-26",
            effective_until="2026-10-26",
        ),
        format="json",
        **headers,
        **_key("race-sched"),
    )
    assert schedule.status_code == 201, schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(gym=gym, class_schedule__class_type_id=type_id)
    other_member = _member(api, headers, first_name="Other")
    class_package = _package(
        api, headers, package_type="CLASS", code="CLS_RACE", class_type_id=type_id
    )
    first_grant = _grant(api, headers, member_id=member_id, package_id=class_package)
    second_grant = _grant(api, headers, member_id=other_member, package_id=class_package)
    created_class = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(occurrence.id),
            "member_id": member_id,
            "member_service_package_id": first_grant,
        },
        format="json",
        **headers,
        **_key("race-class"),
    )
    assert created_class.status_code == 201, created_class.json()
    overlap = api.post(
        "/api/v1/pt-bookings/",
        {
            "trainer_staff_profile_id": str(trainer.id),
            "member_id": member_id,
            "starts_at": "2026-10-26T16:30:00Z",
            "ends_at": "2026-10-26T17:00:00Z",
            "unmetered": True,
            "unmetered_reason": "Covers the class hour",
        },
        format="json",
        **headers,
        **_key("race-overlap"),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "SCHEDULE_CONFLICT"
    open_schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=1,
            location="Race room",
            location_is_exclusive=False,
            effective_from="2026-11-02",
            effective_until="2026-11-02",
            weekdays=[0],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("race-open"),
    )
    assert open_schedule.status_code == 201, open_schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        open_occurrence = ClassOccurrence.objects.get(
            class_schedule_id=open_schedule.json()["data"]["id"]
        )
    third_member = _member(api, headers, first_name="Third")
    third_grant = _grant(api, headers, member_id=third_member, package_id=class_package)
    capacity = Barrier(2, timeout=20)

    def book(grant: str, person: str) -> str:
        connections.close_all()
        try:
            capacity.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                try:
                    create_class_booking(
                        gym=Gym.objects.get(id=gym.id),
                        actor=GymMembership.objects.select_related("user").get(id=owner.id),
                        class_occurrence_id=open_occurrence.id,
                        member_id=UUID(person),
                        member_service_package_id=UUID(grant),
                        unmetered=False,
                        unmetered_reason="",
                        request_id=f"req_{uuid4().hex}",
                        ip_address=None,
                        user_agent="",
                    )
                except (ClassFull, ScheduleConflict):
                    return "blocked"
            return "booked"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        booked = sorted(
            pool.map(
                lambda item: book(item[0], item[1]),
                ((second_grant, other_member), (third_grant, third_member)),
            )
        )
    assert booked == ["blocked", "booked"]


def _next_monday(day):
    offset = (0 - day.weekday()) % 7
    return day + timedelta(days=7 if offset == 0 else offset)


@override_settings(**jwt_settings())
def test_class_booking_trainer_scope_requires_a_current_assignment() -> None:
    client, platform_access = platform_token("cls-scope-platform@example.test")
    payload = provision(
        client, platform_access, code="CSC-A", owner_email="cls-scope-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="cls-scope-ada@example.test", role_key="TRAINER")
    other_membership = add_staff(gym, email="cls-scope-bea@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-CSC", name="Ada")
    other_trainer = _profile(gym, other_membership, code="TR-CS2", name="Bea")
    api = _client()
    headers = auth_headers(staff_token(owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    today = gym_today(gym)
    class_day = _next_monday(today)
    class_type = api.post(
        "/api/v1/class-types/", _type_body(code="SCOPE"), format="json", **headers
    )
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=8,
            location="Scope room",
            effective_from=class_day.isoformat(),
            effective_until=class_day.isoformat(),
            weekdays=[0],
            local_start_time="09:00:00",
            local_end_time="10:00:00",
        ),
        format="json",
        **headers,
        **_key("scope-sched"),
    )
    assert schedule.status_code == 201, schedule.json()
    other_schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(other_trainer.id),
            capacity=8,
            location="Other room",
            location_is_exclusive=False,
            effective_from=class_day.isoformat(),
            effective_until=class_day.isoformat(),
            weekdays=[0],
            local_start_time="11:00:00",
            local_end_time="12:00:00",
        ),
        format="json",
        **headers,
        **_key("scope-other-sched"),
    )
    assert other_schedule.status_code == 201, other_schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(class_schedule_id=schedule.json()["data"]["id"])
        other_occurrence = ClassOccurrence.objects.get(
            class_schedule_id=other_schedule.json()["data"]["id"]
        )
    package_id = _package(
        api, headers, package_type="CLASS", code="CLS_SCOPE", class_type_id=type_id
    )

    def book(member_id: str) -> str:
        grant_id = _grant(api, headers, member_id=member_id, package_id=package_id)
        created = api.post(
            "/api/v1/class-bookings/",
            {
                "class_occurrence_id": str(occurrence.id),
                "member_id": member_id,
                "member_service_package_id": grant_id,
            },
            format="json",
            **headers,
            **_key("scope-book"),
        )
        assert created.status_code == 201, created.json()
        return str(created.json()["data"]["id"])

    open_member = _member(api, headers, first_name="Open")
    future_member = _member(api, headers, first_name="Future")
    expired_member = _member(api, headers, first_name="Expired")
    ended_member = _member(api, headers, first_name="Ended")
    cancelled_member = _member(api, headers, first_name="Cancelled")
    stranger = _member(api, headers, first_name="Stranger")
    other_member = _member(api, headers, first_name="Other")
    open_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=open_member, start=today.isoformat()
    )
    future_assignment = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=future_member,
        start=today.isoformat(),
        until=(today + timedelta(days=30)).isoformat(),
    )
    expired_assignment = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=expired_member,
        start=(today - timedelta(days=10)).isoformat(),
        until=(today - timedelta(days=1)).isoformat(),
    )
    ended_assignment = _assign(
        api, headers, trainer_id=str(trainer.id), member_id=ended_member, start=today.isoformat()
    )
    cancelled_assignment = _assign(
        api,
        headers,
        trainer_id=str(trainer.id),
        member_id=cancelled_member,
        start=today.isoformat(),
    )
    other_assignment = _assign(
        api,
        headers,
        trainer_id=str(other_trainer.id),
        member_id=other_member,
        start=today.isoformat(),
    )
    for created in (
        open_assignment,
        future_assignment,
        expired_assignment,
        ended_assignment,
        cancelled_assignment,
        other_assignment,
    ):
        assert created.status_code == 201, created.json()
    ended = api.post(
        f"/api/v1/trainer-assignments/{ended_assignment.json()['data']['id']}/end/",
        {"effective_until": today.isoformat(), "reason": "Plan finished"},
        format="json",
        **headers,
    )
    assert ended.status_code == 200, ended.json()
    cancelled = api.post(
        f"/api/v1/trainer-assignments/{cancelled_assignment.json()['data']['id']}/cancel/",
        {"reason": "Booked in error"},
        format="json",
        **headers,
    )
    assert cancelled.status_code == 200, cancelled.json()
    visible = {
        "open": book(open_member),
        "future": book(future_member),
    }
    hidden = {
        "expired": book(expired_member),
        "ended": book(ended_member),
        "cancelled": book(cancelled_member),
        "stranger": book(stranger),
        "other": book(other_member),
    }
    other_grant = _grant(api, headers, member_id=open_member, package_id=package_id)
    other_class = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(other_occurrence.id),
            "member_id": open_member,
            "member_service_package_id": other_grant,
        },
        format="json",
        **headers,
        **_key("scope-other-class"),
    )
    assert other_class.status_code == 201, other_class.json()
    listed = api.get("/api/v1/class-bookings/?limit=25", **trainer_headers)
    assert listed.status_code == 200, listed.json()
    assert {row["id"] for row in listed.json()["data"]} == set(visible.values())
    calendar = api.get(
        "/api/v1/class-bookings/"
        f"?view=calendar&date_from={class_day.isoformat()}&date_to={class_day.isoformat()}",
        **trainer_headers,
    )
    assert calendar.status_code == 200, calendar.json()
    assert {row["id"] for row in calendar.json()["data"]} == set(visible.values())
    filtered = api.get(
        f"/api/v1/class-bookings/?member_id={stranger}&limit=25",
        **trainer_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []
    page = api.get("/api/v1/class-bookings/?limit=1", **trainer_headers)
    assert page.status_code == 200
    assert len(page.json()["data"]) == 1
    follow = api.get(
        f"/api/v1/class-bookings/?limit=1&after={page.json()['meta']['next_after']}",
        **trainer_headers,
    )
    assert follow.status_code == 200
    paged_ids = {page.json()["data"][0]["id"], follow.json()["data"][0]["id"]}
    assert paged_ids == set(visible.values())
    assert set(hidden.values()).isdisjoint(paged_ids)
    with CaptureQueriesContext(connection) as captured:
        counted = api.get("/api/v1/class-bookings/?limit=25", **trainer_headers)
    assert counted.status_code == 200
    booking_sql = [
        query["sql"]
        for query in captured.captured_queries
        if "classes_class_booking" in query["sql"]
    ]
    assert len(booking_sql) == 1
    assert "LIMIT" in booking_sql[0]
    assert "trainers_trainer_assignment" in booking_sql[0]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        audits = AuditLog.objects.filter(gym=gym, target_id=hidden["stranger"]).count()
        ledger = ServiceSessionLedger.objects.filter(class_booking_id=hidden["stranger"]).count()
    for action in ("attend", "no-show", "cancel"):
        denied = api.post(
            f"/api/v1/class-bookings/{hidden['stranger']}/{action}/",
            {} if action != "cancel" else {"reason": "Should stay booked"},
            format="json",
            **trainer_headers,
            **_key(f"scope-{action}"),
        )
        assert denied.status_code == 404, denied.json()
    other_denied = api.post(
        f"/api/v1/class-bookings/{other_class.json()['data']['id']}/attend/",
        {},
        format="json",
        **trainer_headers,
        **_key("scope-other-attend"),
    )
    assert other_denied.status_code == 404
    fresh_member = _member(api, headers, first_name="Fresh")
    fresh_grant = _grant(api, headers, member_id=fresh_member, package_id=package_id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        created_audits = AuditLog.objects.filter(gym=gym, action="CLASS_BOOKING_CREATED").count()
    denied_create = api.post(
        "/api/v1/class-bookings/",
        {
            "class_occurrence_id": str(occurrence.id),
            "member_id": fresh_member,
            "member_service_package_id": fresh_grant,
        },
        format="json",
        **trainer_headers,
        **_key("scope-create"),
    )
    assert denied_create.status_code == 404
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert AuditLog.objects.filter(gym=gym, target_id=hidden["stranger"]).count() == audits
        assert (
            ServiceSessionLedger.objects.filter(class_booking_id=hidden["stranger"]).count()
            == ledger
        )
        assert not ClassBooking.objects.filter(gym=gym, member_id=fresh_member).exists()
        assert (
            AuditLog.objects.filter(gym=gym, action="CLASS_BOOKING_CREATED").count()
            == created_audits
        )
        floor = Role.objects.create(
            gym=gym,
            key="FLOOR",
            name="Floor Coach",
            created_by_membership_id=owner.id,
        )
        GymMembershipRole.objects.create(gym=gym, gym_membership=trainer_membership, role=floor)
    mixed_headers = auth_headers(staff_token(trainer_membership))
    mixed = api.get("/api/v1/class-bookings/?limit=25", **mixed_headers)
    assert {row["id"] for row in mixed.json()["data"]} == set(visible.values())
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer_membership,
            role=Role.objects.get(gym=gym, key="MANAGER"),
        )
    manager_headers = auth_headers(staff_token(trainer_membership))
    widened = api.get("/api/v1/class-bookings/?limit=25", **manager_headers)
    assert hidden["stranger"] in {row["id"] for row in widened.json()["data"]}
    assert other_class.json()["data"]["id"] in {row["id"] for row in widened.json()["data"]}
