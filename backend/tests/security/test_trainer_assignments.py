from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connection, connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import gym_today
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Role,
    StaffProfile,
)
from apps.trainers.exceptions import AssignmentOverlap
from apps.trainers.models import TrainerAssignment
from apps.trainers.services import create_assignment
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

ASSIGNMENT_FIELDS = {
    "id",
    "trainer_staff_profile_id",
    "member_id",
    "effective_from",
    "effective_until",
    "status",
    "ended_at",
    "reason",
    "created_at",
    "updated_at",
    "trainer",
    "member",
}
TRAINER_FIELDS = {"id", "staff_code", "display_name"}
MEMBER_FIELDS = {"id", "member_number", "first_name", "last_name"}
ASSIGNED_MEMBER_FIELDS = MEMBER_FIELDS | {"assignment_id", "effective_from", "effective_until"}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _profile(
    gym: Gym,
    membership: GymMembership,
    *,
    code: str,
    name: str,
    status: str = StaffProfile.Status.ACTIVE,
) -> StaffProfile:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return StaffProfile.objects.create(
            gym=gym,
            gym_membership=membership,
            staff_code=code,
            display_name=name,
            phone="",
            joined_date=gym_today(gym),
            status=status,
            employment_title="",
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )


def _member(api: APIClient, headers: dict[str, str], *, first_name: str) -> str:
    created = api.post(
        "/api/v1/members/",
        {"first_name": first_name, "last_name": "Client", "join_date": "2026-10-01"},
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.json()
    return str(created.json()["data"]["id"])


def _assign(
    api: APIClient,
    headers: dict[str, str],
    *,
    trainer_id: str,
    member_id: str,
    start: str,
    until: str | None = None,
    reason: str = "",
) -> object:
    body: dict[str, object] = {
        "trainer_staff_profile_id": trainer_id,
        "member_id": member_id,
        "effective_from": start,
    }
    if until is not None:
        body["effective_until"] = until
    if reason:
        body["reason"] = reason
    return api.post("/api/v1/trainer-assignments/", body, format="json", **headers)


@override_settings(**jwt_settings())
def test_assignment_lifecycle_scope_badge_and_audit() -> None:
    client, platform_access = platform_token("trainer-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="TRN-A", owner_email="trainer-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="TRN-B", owner_email="trainer-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym_a, email="trainer-ada@example.test", role_key="TRAINER")
    other_trainer_membership = add_staff(
        gym_a, email="trainer-bea@example.test", role_key="TRAINER"
    )
    lead_membership = add_staff(gym_a, email="trainer-lead@example.test", role_key="TRAINER")
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymMembershipRole.objects.create(
            gym=gym_a,
            gym_membership=lead_membership,
            role=Role.objects.get(gym=gym_a, key="MANAGER"),
        )
    manager = add_staff(gym_a, email="trainer-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym_a, email="trainer-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym_a, email="trainer-accountant@example.test", role_key="ACCOUNTANT")
    ada = _profile(gym_a, trainer_membership, code="TR-ADA", name="Ada Trainer")
    bea = _profile(gym_a, other_trainer_membership, code="TR-BEA", name="Bea Trainer")
    lead = _profile(gym_a, lead_membership, code="TR-LEAD", name="Lead Trainer")
    desk = _profile(gym_a, receptionist, code="RE-1", name="Front Desk")
    away = _profile(
        gym_a,
        add_staff(gym_a, email="trainer-away@example.test", role_key="TRAINER"),
        code="TR-AWAY",
        name="Away Trainer",
        status=StaffProfile.Status.ON_LEAVE,
    )
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer_membership))
    bea_headers = auth_headers(staff_token(other_trainer_membership))
    lead_headers = auth_headers(staff_token(lead_membership))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    api = _client()
    today = gym_today(gym_a).isoformat()
    yesterday = (gym_today(gym_a) - timedelta(days=1)).isoformat()
    tomorrow = (gym_today(gym_a) + timedelta(days=1)).isoformat()

    assert api.get("/api/v1/trainers/", **accountant_headers).status_code == 403
    assert api.get("/api/v1/trainer-assignments/", **accountant_headers).status_code == 403
    denied = api.post("/api/v1/trainer-assignments/", {}, format="json", **reception_headers)
    assert denied.status_code == 403
    anonymous = APIClient().get("/api/v1/trainers/")
    assert anonymous.status_code == 403
    assert client.get("/api/v1/trainers/", **auth_headers(platform_access)).status_code == 401

    ada_member = _member(api, owner_headers, first_name="Nila")
    bea_member = _member(api, owner_headers, first_name="Omar")
    future_member = _member(api, owner_headers, first_name="Pia")
    closed_member = _member(api, owner_headers, first_name="Quin")
    plain_member = _member(api, owner_headers, first_name="Rin")
    foreign_member = _member(api, other_headers, first_name="Sue")

    rejected = api.post(
        "/api/v1/trainer-assignments/",
        {
            "trainer_staff_profile_id": str(ada.id),
            "member_id": ada_member,
            "effective_from": today,
            "gym_id": str(gym_b.id),
            "status": "ACTIVE",
            "ended_at": "2026-10-19T00:00:00Z",
            "created_by_membership_id": str(owner_a.id),
        },
        format="json",
        **owner_headers,
    )
    assert rejected.status_code == 400, rejected.json()
    assert set(rejected.json()["error"]["fields"]) >= {
        "gym_id",
        "status",
        "ended_at",
        "created_by_membership_id",
    }

    created = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=ada_member,
        start=today,
        reason="Program",
    )
    assert created.status_code == 201, created.json()
    assignment = created.json()["data"]
    assert set(assignment) == ASSIGNMENT_FIELDS
    assert set(assignment["trainer"]) == TRAINER_FIELDS
    assert set(assignment["member"]) == MEMBER_FIELDS
    assert "gym_id" not in assignment
    assert "created_by_membership_id" not in assignment
    assert assignment["status"] == "ACTIVE"
    assert assignment["ended_at"] is None
    assert assignment["trainer"]["display_name"] == "Ada Trainer"
    assert assignment["member"]["first_name"] == "Nila"
    assignment_id = assignment["id"]

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        audit = AuditLog.objects.get(target_id=assignment_id, action="TRAINER_ASSIGNMENT_CREATED")
    assert audit.module == "trainers"
    assert audit.target_type == "TrainerAssignment"
    assert audit.actor_membership_id == owner_a.id
    assert audit.new_values["status"] == "ACTIVE"
    assert audit.new_values["reason"] == "Program"

    overlap = _assign(api, owner_headers, trainer_id=str(ada.id), member_id=ada_member, start=today)
    assert overlap.status_code == 409
    assert overlap.json()["error"]["code"] == "ASSIGNMENT_OVERLAP"
    other_trainer = _assign(
        api, owner_headers, trainer_id=str(bea.id), member_id=ada_member, start=today
    )
    assert other_trainer.status_code == 201, other_trainer.json()
    inverted = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=bea_member,
        start=today,
        until=yesterday,
    )
    assert inverted.status_code == 400
    assert "effective_until" in inverted.json()["error"]["fields"]
    cross_member = _assign(
        api, owner_headers, trainer_id=str(ada.id), member_id=foreign_member, start=today
    )
    assert cross_member.status_code == 404
    missing = _assign(
        api, owner_headers, trainer_id=str(ada.id), member_id=str(uuid4()), start=today
    )
    assert missing.status_code == 404
    desk_target = _assign(
        api, owner_headers, trainer_id=str(desk.id), member_id=bea_member, start=today
    )
    assert desk_target.status_code == 422
    assert desk_target.json()["error"]["code"] == "TRAINER_NOT_ELIGIBLE"
    away_target = _assign(
        api, owner_headers, trainer_id=str(away.id), member_id=bea_member, start=today
    )
    assert away_target.status_code == 422
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        Member.objects.filter(id=plain_member).update(status=Member.Status.SUSPENDED)
    inactive_member = _assign(
        api, owner_headers, trainer_id=str(ada.id), member_id=plain_member, start=today
    )
    assert inactive_member.status_code == 422
    assert inactive_member.json()["error"]["code"] == "MEMBER_NOT_ACTIVE"

    future = _assign(
        api, owner_headers, trainer_id=str(ada.id), member_id=future_member, start=tomorrow
    )
    assert future.status_code == 201, future.json()
    bounded = _assign(
        api,
        owner_headers,
        trainer_id=str(bea.id),
        member_id=bea_member,
        start=yesterday,
        until=today,
    )
    assert bounded.status_code == 201, bounded.json()
    expired = _assign(
        api,
        owner_headers,
        trainer_id=str(ada.id),
        member_id=closed_member,
        start=yesterday,
        until=yesterday,
    )
    assert expired.status_code == 201, expired.json()

    trainers = api.get("/api/v1/trainers/", **owner_headers)
    assert trainers.status_code == 200, trainers.json()
    assert [row["staff_code"] for row in trainers.json()["data"]] == ["TR-ADA", "TR-BEA", "TR-LEAD"]
    assert all(set(row) == TRAINER_FIELDS for row in trainers.json()["data"])
    own_trainers = api.get("/api/v1/trainers/", **trainer_headers)
    assert [row["id"] for row in own_trainers.json()["data"]] == [str(ada.id)]
    assert [row["id"] for row in api.get("/api/v1/trainers/", **lead_headers).json()["data"]] == [
        str(ada.id),
        str(bea.id),
        str(lead.id),
    ]
    hidden_trainer = api.get(f"/api/v1/trainers/{bea.id}/assigned-members/", **trainer_headers)
    assert hidden_trainer.status_code == 404
    ada_members = api.get(f"/api/v1/trainers/{ada.id}/assigned-members/", **owner_headers)
    assert ada_members.status_code == 200, ada_members.json()
    assert [row["first_name"] for row in ada_members.json()["data"]] == ["Nila"]
    assert set(ada_members.json()["data"][0]) == ASSIGNED_MEMBER_FIELDS
    bea_members = api.get(f"/api/v1/trainers/{bea.id}/assigned-members/", **bea_headers)
    assert [row["first_name"] for row in bea_members.json()["data"]] == ["Nila", "Omar"]

    own_assignments = api.get("/api/v1/trainer-assignments/", **trainer_headers)
    assert own_assignments.status_code == 200, own_assignments.json()
    trainer_ids = {row["trainer_staff_profile_id"] for row in own_assignments.json()["data"]}
    assert trainer_ids == {str(ada.id)}
    foreign_detail = api.get(
        f"/api/v1/trainer-assignments/{other_trainer.json()['data']['id']}/", **trainer_headers
    )
    assert foreign_detail.status_code == 404
    owner_detail = api.get(f"/api/v1/trainer-assignments/{assignment_id}/", **owner_headers)
    assert owner_detail.status_code == 200
    cross_detail = api.get(f"/api/v1/trainer-assignments/{assignment_id}/", **other_headers)
    assert cross_detail.status_code == 404
    assert "Nila" not in cross_detail.content.decode()
    assert api.get("/api/v1/trainer-assignments/", **other_headers).json()["data"] == []
    assert api.get("/api/v1/trainer-assignments/", **reception_headers).status_code == 200
    assert api.get("/api/v1/trainer-assignments/", **manager_headers).status_code == 200
    stolen_create = _assign(
        api, trainer_headers, trainer_id=str(bea.id), member_id=bea_member, start=tomorrow
    )
    assert stolen_create.status_code == 404

    listed = api.get("/api/v1/members/?is_pt_client=true", **owner_headers)
    assert listed.status_code == 200, listed.json()
    assert {row["first_name"] for row in listed.json()["data"]} == {"Nila", "Omar"}
    assert all(row["is_pt_client"] is True for row in listed.json()["data"])
    excluded = api.get("/api/v1/members/?is_pt_client=false", **owner_headers)
    excluded_names = {row["first_name"] for row in excluded.json()["data"]}
    assert {"Nila", "Omar"}.isdisjoint(excluded_names)
    assert {"Pia", "Quin", "Rin"} <= excluded_names
    detail = api.get(f"/api/v1/members/{ada_member}/", **owner_headers)
    assert detail.json()["data"]["is_pt_client"] is True
    summary = api.get(f"/api/v1/members/{ada_member}/summary/", **owner_headers)
    assert summary.json()["data"]["is_pt_client"] is True
    assert summary.json()["data"]["member"]["is_pt_client"] is True
    future_detail = api.get(f"/api/v1/members/{future_member}/", **owner_headers)
    assert future_detail.json()["data"]["is_pt_client"] is False
    expired_detail = api.get(f"/api/v1/members/{closed_member}/", **owner_headers)
    assert expired_detail.json()["data"]["is_pt_client"] is False
    foreign_badge = api.get(f"/api/v1/members/{foreign_member}/", **other_headers)
    assert foreign_badge.json()["data"]["is_pt_client"] is False

    with CaptureQueriesContext(connection) as member_queries:
        measured = api.get("/api/v1/members/?limit=25", **owner_headers)
    assert measured.status_code == 200
    member_selects = [
        str(query["sql"]).lower()
        for query in member_queries.captured_queries
        if "from" in str(query["sql"]).lower()
        and "members_member" in str(query["sql"]).lower()
        and "order by" in str(query["sql"]).lower()
    ]
    assert len(member_selects) == 1
    assert "trainers_trainer_assignment" in member_selects[0]
    assert " join " not in member_selects[0]
    with CaptureQueriesContext(connection) as assignment_queries:
        assignment_page = api.get("/api/v1/trainer-assignments/?limit=25", **owner_headers)
    assert assignment_page.status_code == 200
    assert len(assignment_page.json()["data"]) >= 3
    assignment_selects = [
        str(query["sql"]).lower()
        for query in assignment_queries.captured_queries
        if "trainers_trainer_assignment" in str(query["sql"]).lower()
        and str(query["sql"]).lower().lstrip().startswith("select")
    ]
    assert len(assignment_selects) == 1

    ended = api.post(
        f"/api/v1/trainer-assignments/{assignment_id}/end/",
        {"effective_until": today, "reason": "Program finished"},
        format="json",
        **trainer_headers,
    )
    assert ended.status_code == 200, ended.json()
    assert ended.json()["data"]["status"] == "ENDED"
    assert ended.json()["data"]["ended_at"] is not None
    still_client = api.get(f"/api/v1/members/{ada_member}/", **owner_headers)
    assert still_client.json()["data"]["is_pt_client"] is True
    again = api.post(
        f"/api/v1/trainer-assignments/{assignment_id}/end/",
        {"effective_until": today, "reason": "Again"},
        format="json",
        **owner_headers,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ASSIGNMENT_STATE_CONFLICT"
    early = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/end/",
        {"effective_until": yesterday, "reason": "Too early"},
        format="json",
        **owner_headers,
    )
    assert early.status_code == 400
    blank_end = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/end/",
        {"effective_until": tomorrow},
        format="json",
        **owner_headers,
    )
    assert blank_end.status_code == 400
    cancelled = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/cancel/",
        {"reason": "Booked by mistake", "effective_until": tomorrow},
        format="json",
        **owner_headers,
    )
    assert cancelled.status_code == 400
    cancelled = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/cancel/",
        {"reason": "Booked by mistake"},
        format="json",
        **owner_headers,
    )
    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert cancelled.json()["data"]["effective_from"] == tomorrow
    recancel = api.post(
        f"/api/v1/trainer-assignments/{future.json()['data']['id']}/cancel/",
        {"reason": "Again"},
        format="json",
        **owner_headers,
    )
    assert recancel.status_code == 409
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        actions = set(
            AuditLog.objects.filter(target_id=future.json()["data"]["id"]).values_list(
                "action", flat=True
            )
        )
    assert actions == {"TRAINER_ASSIGNMENT_CREATED", "TRAINER_ASSIGNMENT_CANCELLED"}

    replaced = _assign(
        api, owner_headers, trainer_id=str(ada.id), member_id=ada_member, start=today
    )
    assert replaced.status_code == 201, replaced.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="pt").update(enabled=False)
    assert api.get("/api/v1/trainers/", **owner_headers).status_code == 403
    assert (
        api.post(
            "/api/v1/trainer-assignments/",
            {
                "trainer_staff_profile_id": str(ada.id),
                "member_id": bea_member,
                "effective_from": tomorrow,
            },
            format="json",
            **owner_headers,
        ).status_code
        == 403
    )
    badge_after_disable = api.get(f"/api/v1/members/{ada_member}/", **owner_headers)
    assert badge_after_disable.json()["data"]["is_pt_client"] is True


@override_settings(**jwt_settings())
def test_assignment_overlap_is_concurrency_safe_and_tenant_isolated() -> None:
    client, platform_access = platform_token("trainer-race-platform@example.test")
    payload = provision(
        client, platform_access, code="TRN-R", owner_email="trainer-race-owner@example.test"
    )
    other_payload = provision(
        client, platform_access, code="TRN-S", owner_email="trainer-race-other@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = _profile(
        gym,
        add_staff(gym, email="trainer-race@example.test", role_key="TRAINER"),
        code="TR-RACE",
        name="Race Trainer",
    )
    headers = auth_headers(staff_token(owner))
    api = _client()
    member_id = _member(api, headers, first_name="Race")
    start = gym_today(gym).isoformat()
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert TrainerAssignment.objects.count() == 0
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert TrainerAssignment.objects.count() == 0

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'trainers_trainer_assignment', 'SELECT'),
              has_table_privilege('gym_runtime', 'trainers_trainer_assignment', 'INSERT'),
              has_table_privilege('gym_runtime', 'trainers_trainer_assignment', 'UPDATE'),
              has_table_privilege('gym_runtime', 'trainers_trainer_assignment', 'DELETE'),
              has_function_privilege('public', 'app_guard_trainer_assignment()', 'EXECUTE'),
              has_function_privilege(
                'gym_runtime', 'app_guard_trainer_assignment()', 'EXECUTE'
              )
            """
        )
        assert cursor.fetchone() == (True, True, True, False, False, True)

    barrier = Barrier(2, timeout=20)

    def create_overlap() -> str:
        connections.close_all()
        try:
            barrier.wait()
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                actor = GymMembership.objects.select_related("user").get(id=owner.id)
                gym_row = Gym.objects.get(id=gym.id)
            try:
                create_assignment(
                    gym=gym_row,
                    actor=actor,
                    trainer_staff_profile_id=trainer.id,
                    member_id=UUID(member_id),
                    effective_from=date.fromisoformat(start),
                    effective_until=None,
                    reason="",
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            except AssignmentOverlap:
                return "overlap"
            return "created"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _index: create_overlap(), (1, 2)))
    assert outcomes == ["created", "overlap"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert TrainerAssignment.objects.filter(status="ACTIVE").count() == 1
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
        assert TrainerAssignment.objects.count() == 0
