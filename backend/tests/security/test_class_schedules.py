"""Class types, schedules, occurrences, and CLASS package linkage."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connection, connections, transaction
from django.db.utils import InternalError, ProgrammingError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.classes.exceptions import ScheduleConflict
from apps.classes.models import ClassOccurrence, ClassSchedule, ClassType
from apps.classes.services import create_class_schedule
from apps.common.db.tenant_context import set_gym_context
from apps.members.services import gym_today
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord, RolePermission, StaffProfile
from apps.trainers.models import ServicePackage
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _key(prefix: str) -> dict[str, str]:
    return {"HTTP_IDEMPOTENCY_KEY": f"{prefix}-{uuid4().hex}"}


def _profile(gym: Gym, membership: GymMembership, *, code: str, name: str) -> StaffProfile:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return StaffProfile.objects.create(
            gym=gym,
            gym_membership=membership,
            staff_code=code,
            display_name=name,
            phone="",
            joined_date=gym_today(gym),
            status=StaffProfile.Status.ACTIVE,
            employment_title="",
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )


def _type_body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": "YOGA",
        "name": "Morning yoga",
        "description": "Floor work",
        "default_duration_minutes": 60,
        "default_capacity": 12,
    }
    payload.update(overrides)
    return payload


def _schedule_body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "weekdays": [0],
        "local_start_time": "23:00:00",
        "local_end_time": "23:30:00",
        "effective_from": "2026-10-19",
        "effective_until": "2026-10-19",
        "location": "Studio A",
        "location_is_exclusive": True,
        "capacity": 8,
    }
    payload.update(overrides)
    return payload


def _codes(gym: Gym, role_key: str) -> set[str]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return set(
            RolePermission.objects.filter(gym=gym, role__key=role_key).values_list(
                "permission__code", flat=True
            )
        )


@override_settings(**jwt_settings())
def test_class_type_and_class_package_follow_the_catalog_contract() -> None:
    client, platform_access = platform_token("class-type-platform@example.test")
    payload = provision(
        client, platform_access, code="CLS-A", owner_email="class-type-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="CLS-B", owner_email="class-type-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    accountant = add_staff(gym, email="class-type-accountant@example.test", role_key="ACCOUNTANT")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    accountant_headers = auth_headers(staff_token(accountant))
    assert {"classes.view", "classes.manage", "classes.book", "classes.attendance"} <= _codes(
        gym, "OWNER"
    )
    assert _codes(gym, "ACCOUNTANT") >= {"classes.view"}
    assert "classes.manage" not in _codes(gym, "ACCOUNTANT")

    denied = api.post("/api/v1/class-types/", _type_body(), format="json", **accountant_headers)
    assert denied.status_code == 403
    listed = api.get("/api/v1/class-types/", **accountant_headers)
    assert listed.status_code == 200
    lowercase = api.post(
        "/api/v1/class-types/", _type_body(code="yoga"), format="json", **owner_headers
    )
    assert lowercase.status_code == 400
    created = api.post(
        "/api/v1/class-types/",
        _type_body(name="  Morning   yoga "),
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    body = created.json()["data"]
    assert body["name"] == "Morning yoga"
    assert body["code"] == "YOGA"
    assert body["is_active"] is True
    immutable = api.patch(
        f"/api/v1/class-types/{body['id']}/",
        {"code": "PILATES"},
        format="json",
        **owner_headers,
    )
    assert immutable.status_code == 400
    patched = api.patch(
        f"/api/v1/class-types/{body['id']}/",
        {"name": "Evening yoga", "is_active": False, "default_capacity": 10},
        format="json",
        **owner_headers,
    )
    assert patched.status_code == 200, patched.json()
    assert patched.json()["data"]["code"] == "YOGA"
    assert patched.json()["data"]["is_active"] is False
    missing = api.patch(
        f"/api/v1/class-types/{body['id']}/",
        {"name": "Elsewhere"},
        format="json",
        **other_headers,
    )
    assert missing.status_code == 404
    with pytest.raises((InternalError, ProgrammingError), match="identity columns are immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ClassType.objects.filter(id=body["id"]).update(code="PILATES")

    active = api.post(
        "/api/v1/class-types/",
        _type_body(code="SPIN", name="Spin"),
        format="json",
        **owner_headers,
    )
    assert active.status_code == 201, active.json()
    class_type_id = active.json()["data"]["id"]
    package = api.post(
        "/api/v1/service-packages/",
        {
            "type": "CLASS",
            "code": "CLASS_8",
            "name": "Eight classes",
            "session_count": 8,
            "validity_value": 1,
            "validity_unit": "MONTH",
            "price": "80.00",
            "class_type_id": class_type_id,
        },
        format="json",
        **owner_headers,
    )
    assert package.status_code == 201, package.json()
    assert package.json()["data"]["class_type_id"] == class_type_id
    rejected_pt = api.post(
        "/api/v1/service-packages/",
        {
            "type": "PT",
            "code": "PT_CLASS",
            "name": "PT with class",
            "session_count": 4,
            "validity_value": 1,
            "validity_unit": "MONTH",
            "price": "40.00",
            "class_type_id": class_type_id,
        },
        format="json",
        **owner_headers,
    )
    assert rejected_pt.status_code == 400
    inactive = api.post(
        "/api/v1/service-packages/",
        {
            "type": "CLASS",
            "code": "CLASS_OFF",
            "name": "Closed class",
            "session_count": 4,
            "validity_value": 1,
            "validity_unit": "MONTH",
            "price": "40.00",
            "class_type_id": body["id"],
        },
        format="json",
        **owner_headers,
    )
    assert inactive.status_code == 422
    assert inactive.json()["error"]["code"] == "CLASS_TYPE_INACTIVE"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = ServicePackage.objects.get(id=package.json()["data"]["id"])
        assert stored.type == "CLASS"
        assert str(stored.class_type_id) == class_type_id
    with pytest.raises((InternalError, ProgrammingError), match="immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ServicePackage.objects.filter(id=stored.id).update(name="Changed")
    assert other.id != gym.id


@override_settings(**jwt_settings())
def test_schedule_materializes_recurrence_and_patch_keeps_history() -> None:
    client, platform_access = platform_token("class-sched-platform@example.test")
    payload = provision(
        client, platform_access, code="SCH-A", owner_email="class-sched-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(
        gym, email="class-sched-trainer@example.test", role_key="TRAINER"
    )
    trainer = _profile(gym, trainer_membership, code="TR-SCH", name="Ada")
    api = _client()
    headers = auth_headers(staff_token(owner))
    class_type = api.post("/api/v1/class-types/", _type_body(code="LIFT"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    empty = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            weekdays=[0],
            effective_from="2026-10-20",
            effective_until="2026-10-20",
        ),
        format="json",
        **headers,
        **_key("sched-empty"),
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "SCHEDULE_NO_MATCHES"
    too_long = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            effective_from="2026-01-01",
            effective_until="2027-01-02",
        ),
        format="json",
        **headers,
    )
    assert too_long.status_code == 400
    today = gym_today(gym)
    created = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            weekdays=[0, 1, 2, 3, 4, 5, 6],
            local_start_time="12:00:00",
            local_end_time="12:30:00",
            effective_from=(today - timedelta(days=2)).isoformat(),
            effective_until=(today + timedelta(days=2)).isoformat(),
            location="  Studio   A ",
        ),
        format="json",
        **headers,
        **_key("sched-create"),
    )
    assert created.status_code == 201, created.json()
    schedule_id = created.json()["data"]["id"]
    assert created.json()["data"]["location"] == "Studio A"
    assert created.json()["data"]["timezone"] == gym.timezone
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Gym.objects.filter(id=gym.id).update(timezone="America/New_York")
    patched = api.patch(
        f"/api/v1/class-schedules/{schedule_id}/",
        {"location": "Studio B", "timezone": "UTC"},
        format="json",
        **headers,
    )
    assert patched.status_code == 400
    moved = api.patch(
        f"/api/v1/class-schedules/{schedule_id}/",
        {"location": "Studio B"},
        format="json",
        **headers,
        **_key("sched-move"),
    )
    assert moved.status_code == 200, moved.json()
    assert moved.json()["data"]["timezone"] == gym.timezone
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = list(
            ClassOccurrence.objects.filter(class_schedule_id=schedule_id).order_by("local_date")
        )
    past = [row for row in rows if row.starts_at <= timezone.now()]
    future = [row for row in rows if row.starts_at > timezone.now()]
    assert past
    assert future
    assert {row.location for row in past} == {"Studio A"}
    assert {row.location for row in future} == {"Studio B"}
    with pytest.raises(
        (InternalError, ProgrammingError),
        match="only a future scheduled occurrence can be deleted",
    ):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    "DELETE FROM classes_class_occurrence WHERE id = %s",
                    [str(past[0].id)],
                )


@override_settings(**jwt_settings())
def test_dst_gap_and_fold_follow_the_gym_timezone_snapshot() -> None:
    client, platform_access = platform_token("class-dst-platform@example.test")
    payload = provision(
        client, platform_access, code="DST-A", owner_email="class-dst-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="class-dst-trainer@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-DST", name="Bea")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Gym.objects.filter(id=gym.id).update(timezone="America/New_York")
    api = _client()
    headers = auth_headers(staff_token(owner))
    class_type = api.post("/api/v1/class-types/", _type_body(code="DST"), format="json", **headers)
    type_id = class_type.json()["data"]["id"]
    gap = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            weekdays=[6],
            local_start_time="02:30:00",
            local_end_time="03:30:00",
            effective_from="2026-03-08",
            effective_until="2026-03-08",
            location="Hall",
            location_is_exclusive=False,
        ),
        format="json",
        **headers,
        **_key("sched-gap"),
    )
    assert gap.status_code == 201, gap.json()
    assert gap.json()["data"]["skipped_local_dates"] == ["2026-03-08"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            ClassOccurrence.objects.filter(class_schedule_id=gap.json()["data"]["id"]).count() == 0
        )
    folded = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            weekdays=[6],
            local_start_time="01:30:00",
            local_end_time="01:45:00",
            effective_from="2026-11-01",
            effective_until="2026-11-01",
            location="Hall",
            location_is_exclusive=False,
        ),
        format="json",
        **headers,
        **_key("sched-fold"),
    )
    assert folded.status_code == 201, folded.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(class_schedule_id=folded.json()["data"]["id"])
    assert occurrence.starts_at.isoformat() == "2026-11-01T05:30:00+00:00"


@override_settings(**jwt_settings())
def test_trainer_and_location_conflicts_are_deterministic() -> None:
    client, platform_access = platform_token("class-conflict-platform@example.test")
    payload = provision(
        client, platform_access, code="CNF-A", owner_email="class-conflict-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    first_membership = add_staff(gym, email="class-conflict-a@example.test", role_key="TRAINER")
    second_membership = add_staff(gym, email="class-conflict-b@example.test", role_key="TRAINER")
    first = _profile(gym, first_membership, code="TR-C1", name="Ada")
    second = _profile(gym, second_membership, code="TR-C2", name="Bea")
    api = _client()
    headers = auth_headers(staff_token(owner))
    class_type = api.post("/api/v1/class-types/", _type_body(code="BOX"), format="json", **headers)
    type_id = class_type.json()["data"]["id"]
    common = {
        "class_type_id": type_id,
        "weekdays": [0],
        "local_start_time": "23:00:00",
        "local_end_time": "23:30:00",
        "effective_from": "2026-10-26",
        "effective_until": "2026-10-26",
        "capacity": 4,
    }
    first_created = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(**common, trainer_staff_profile_id=str(first.id), location="Ring"),
        format="json",
        **headers,
        **_key("sched-ring"),
    )
    assert first_created.status_code == 201, first_created.json()
    overlap = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            **{
                **common,
                "trainer_staff_profile_id": str(first.id),
                "location": "Other",
                "local_start_time": "23:15:00",
                "local_end_time": "23:45:00",
            }
        ),
        format="json",
        **headers,
        **_key("sched-overlap"),
    )
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "SCHEDULE_CONFLICT"
    exclusive = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(**common, trainer_staff_profile_id=str(second.id), location="ring"),
        format="json",
        **headers,
        **_key("sched-exclusive"),
    )
    assert exclusive.status_code == 409
    shared = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            **common,
            trainer_staff_profile_id=str(second.id),
            location="Floor",
            location_is_exclusive=False,
        ),
        format="json",
        **headers,
        **_key("sched-shared"),
    )
    assert shared.status_code == 201, shared.json()
    again = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            **{
                **common,
                "trainer_staff_profile_id": str(first.id),
                "location": "Floor",
                "location_is_exclusive": False,
                "local_start_time": "22:00:00",
                "local_end_time": "22:30:00",
            }
        ),
        format="json",
        **headers,
        **_key("sched-again"),
    )
    assert again.status_code == 201, again.json()
    barrier = Barrier(2, timeout=20)

    def create_same() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                gym_row = Gym.objects.get(id=gym.id)
            create_class_schedule(
                gym=gym_row,
                actor=actor,
                class_type_id=UUID(type_id),
                trainer_staff_profile_id=first.id,
                weekdays=[1],
                local_start_time=datetime.strptime("18:00", "%H:%M").time(),
                local_end_time=datetime.strptime("18:30", "%H:%M").time(),
                effective_from=datetime.strptime("2026-10-27", "%Y-%m-%d").date(),
                effective_until=datetime.strptime("2026-10-27", "%Y-%m-%d").date(),
                location="Race room",
                location_is_exclusive=True,
                capacity=4,
                request_id=f"req_{uuid4().hex}",
                ip_address=None,
                user_agent="",
            )
        except ScheduleConflict:
            return "conflict"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _item: create_same(), (1, 2)))
    assert sorted(results) == ["conflict", "created"]


@override_settings(**jwt_settings())
def test_occurrence_cancel_is_idempotent_and_scoped() -> None:
    client, platform_access = platform_token("class-cancel-platform@example.test")
    payload = provision(
        client, platform_access, code="CAN-A", owner_email="class-cancel-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="CAN-B", owner_email="class-cancel-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(
        gym, email="class-cancel-trainer@example.test", role_key="TRAINER"
    )
    other_trainer_membership = add_staff(
        gym, email="class-cancel-other-trainer@example.test", role_key="TRAINER"
    )
    trainer = _profile(gym, trainer_membership, code="TR-CAN", name="Ada")
    other_trainer = _profile(gym, other_trainer_membership, code="TR-CAN2", name="Bea")
    accountant = add_staff(gym, email="class-cancel-accountant@example.test", role_key="ACCOUNTANT")
    api = _client()
    owner_headers = auth_headers(staff_token(owner))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    other_headers = auth_headers(staff_token(other_owner))
    accountant_headers = auth_headers(staff_token(accountant))
    class_type = api.post(
        "/api/v1/class-types/", _type_body(code="ROW"), format="json", **owner_headers
    )
    type_id = class_type.json()["data"]["id"]
    own = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            weekdays=[0],
            effective_from="2026-10-26",
            effective_until="2026-10-26",
            location="Own room",
        ),
        format="json",
        **trainer_headers,
        **_key("sched-own"),
    )
    assert own.status_code == 201, own.json()
    hidden = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(other_trainer.id),
            weekdays=[0],
            effective_from="2026-11-02",
            effective_until="2026-11-02",
            location="Other room",
            local_start_time="22:00:00",
            local_end_time="22:30:00",
        ),
        format="json",
        **owner_headers,
        **_key("sched-hidden"),
    )
    assert hidden.status_code == 201, hidden.json()
    trainer_list = api.get("/api/v1/class-occurrences/", **trainer_headers)
    assert trainer_list.status_code == 200
    visible_ids = {row["id"] for row in trainer_list.json()["data"]}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        own_occurrence = ClassOccurrence.objects.get(class_schedule_id=own.json()["data"]["id"])
        other_occurrence = ClassOccurrence.objects.get(
            class_schedule_id=hidden.json()["data"]["id"]
        )
    assert str(own_occurrence.id) in visible_ids
    assert str(other_occurrence.id) not in visible_ids
    blocked = api.post(
        f"/api/v1/class-occurrences/{other_occurrence.id}/cancel/",
        {"reason": "Not mine"},
        format="json",
        **trainer_headers,
        **_key("class-cancel-scope"),
    )
    assert blocked.status_code == 404
    accountant_cancel = api.post(
        f"/api/v1/class-occurrences/{own_occurrence.id}/cancel/",
        {"reason": "Books"},
        format="json",
        **accountant_headers,
        **_key("class-cancel-books"),
    )
    assert accountant_cancel.status_code == 403
    cross = api.post(
        f"/api/v1/class-occurrences/{own_occurrence.id}/cancel/",
        {"reason": "Other gym"},
        format="json",
        **other_headers,
        **_key("class-cancel-cross"),
    )
    assert cross.status_code == 404
    key = _key("class-cancel-ok")
    cancelled = api.post(
        f"/api/v1/class-occurrences/{own_occurrence.id}/cancel/",
        {"reason": "  Coach   unavailable "},
        format="json",
        **owner_headers,
        **key,
    )
    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert cancelled.json()["data"]["reason"] == "Coach unavailable"
    replay = api.post(
        f"/api/v1/class-occurrences/{own_occurrence.id}/cancel/",
        {"reason": "  Coach   unavailable "},
        format="json",
        **owner_headers,
        **key,
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == cancelled.json()["data"]["id"]
    again = api.post(
        f"/api/v1/class-occurrences/{own_occurrence.id}/cancel/",
        {"reason": "Coach unavailable"},
        format="json",
        **owner_headers,
        **_key("class-cancel-again"),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "OCCURRENCE_STATE_CONFLICT"
    calendar = api.get(
        "/api/v1/class-occurrences/",
        {"view": "calendar", "date_from": "2026-10-01", "date_to": "2026-12-01"},
        **owner_headers,
    )
    assert calendar.status_code == 400
    with CaptureQueriesContext(connection) as captured:
        bounded = api.get("/api/v1/class-occurrences/", {"limit": "25"}, **owner_headers)
    assert bounded.status_code == 200
    assert any("LIMIT" in query["sql"] for query in captured.captured_queries)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'classes_class_type', 'DELETE'),
              has_table_privilege('gym_runtime', 'classes_class_schedule', 'DELETE'),
              has_table_privilege('gym_runtime', 'classes_class_occurrence', 'SELECT'),
              has_table_privilege('gym_runtime', 'classes_class_occurrence', 'DELETE'),
              has_table_privilege('gym_runtime', 'classes_class_occurrence', 'UPDATE'),
              has_function_privilege(
                'public', 'app_class_occurrence_blocks_regeneration(uuid, uuid)', 'EXECUTE'
              ),
              has_function_privilege(
                'gym_runtime', 'app_class_occurrence_blocks_regeneration(uuid, uuid)', 'EXECUTE'
              )
            """
        )
        assert cursor.fetchone() == (False, False, True, True, True, False, True)
    with pytest.raises((InternalError, ProgrammingError), match="closed occurrences are immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            ClassOccurrence.objects.filter(id=own_occurrence.id).update(reason="Changed")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert ClassSchedule.objects.filter(id=own.json()["data"]["id"]).exists()


@override_settings(**jwt_settings())
def test_schedule_commands_require_an_idempotency_key_and_replay() -> None:
    client, platform_access = platform_token("class-idem-platform@example.test")
    payload = provision(
        client, platform_access, code="IDM-A", owner_email="class-idem-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="class-idem-trainer@example.test", role_key="TRAINER")
    trainer = _profile(gym, trainer_membership, code="TR-IDM", name="Ada")
    api = _client()
    headers = auth_headers(staff_token(owner))
    class_type = api.post("/api/v1/class-types/", _type_body(code="SPIN"), format="json", **headers)
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    renamed = api.patch(
        f"/api/v1/class-types/{type_id}/",
        {"name": "Spin class"},
        format="json",
        **headers,
    )
    assert renamed.status_code == 200, renamed.json()
    body = _schedule_body(
        class_type_id=type_id,
        trainer_staff_profile_id=str(trainer.id),
        weekdays=[1, 0],
        local_start_time="23:00",
        local_end_time="23:30:00",
        effective_from="2026-10-26",
        effective_until="2026-10-27",
        location="  Studio   A ",
        capacity=8,
    )
    missing = api.post("/api/v1/class-schedules/", body, format="json", **headers)
    assert missing.status_code == 400
    assert "Idempotency-Key" in missing.json()["error"]["fields"]
    invalid = api.post(
        "/api/v1/class-schedules/",
        body,
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY="too-short",
    )
    assert invalid.status_code == 400
    assert "Idempotency-Key" in invalid.json()["error"]["fields"]
    create_key = f"sched-idem-create-{uuid4().hex}"
    created = api.post(
        "/api/v1/class-schedules/",
        body,
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY=create_key,
    )
    assert created.status_code == 201, created.json()
    schedule_id = created.json()["data"]["id"]
    listed = api.get("/api/v1/class-schedules/", **headers)
    assert listed.status_code == 200, listed.json()
    assert [row["id"] for row in listed.json()["data"]] == [schedule_id]
    replay = api.post(
        "/api/v1/class-schedules/",
        {
            **body,
            "weekdays": [0, 1],
            "local_start_time": "23:00:00",
            "location": "Studio A",
        },
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY=create_key,
    )
    assert replay.status_code == 201
    assert replay.content == created.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record = IdempotencyRecord.objects.get(idempotency_key=create_key)
        assert record.http_method == "POST"
        assert record.route == "/api/v1/class-schedules/"
        assert record.resource_type == "ClassSchedule"
        assert str(record.resource_id) == schedule_id
        assert record.response_status == 201
        assert ClassSchedule.objects.filter(gym=gym).count() == 1
        occurrence_ids = set(
            ClassOccurrence.objects.filter(class_schedule_id=schedule_id).values_list(
                "id", flat=True
            )
        )
        assert len(occurrence_ids) == 2
        assert (
            AuditLog.objects.filter(
                action="CLASS_SCHEDULE_CREATED",
                target_id=schedule_id,
            ).count()
            == 1
        )
    mismatch = api.post(
        "/api/v1/class-schedules/",
        {**body, "capacity": 9},
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY=create_key,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    patch_key = f"sched-idem-patch-{uuid4().hex}"
    patched = api.patch(
        f"/api/v1/class-schedules/{schedule_id}/",
        {"location": "  Room   B "},
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY=patch_key,
    )
    assert patched.status_code == 200, patched.json()
    assert patched.json()["data"]["location"] == "Room B"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        patched_ids = set(
            ClassOccurrence.objects.filter(class_schedule_id=schedule_id).values_list(
                "id", flat=True
            )
        )
    patch_replay = api.patch(
        f"/api/v1/class-schedules/{schedule_id}/",
        {"location": "Room B"},
        format="json",
        **headers,
        HTTP_IDEMPOTENCY_KEY=patch_key,
    )
    assert patch_replay.status_code == 200
    assert patch_replay.content == patched.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            set(
                ClassOccurrence.objects.filter(class_schedule_id=schedule_id).values_list(
                    "id", flat=True
                )
            )
            == patched_ids
        )
        assert (
            AuditLog.objects.filter(
                action="CLASS_SCHEDULE_UPDATED",
                target_id=schedule_id,
            ).count()
            == 1
        )
        patch_record = IdempotencyRecord.objects.get(idempotency_key=patch_key)
        assert patch_record.http_method == "PATCH"
        assert patch_record.route == f"/api/v1/class-schedules/{schedule_id}/"
        assert patch_record.resource_type == "ClassSchedule"
        assert str(patch_record.resource_id) == schedule_id
        assert patch_record.response_status == 200
