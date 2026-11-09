from __future__ import annotations

import os
from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.member_tools import execute_registered_tool, member_tool_definitions
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.exceptions import MemberConflict
from apps.members.models import Member, Membership, MembershipPlan
from apps.members.services import gym_today
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership, GymSettings
from apps.users.models import User
from tests.security.test_tenant_gym_settings import (
    add_staff,
    jwt_settings,
    platform_token,
    provision,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _staff(code: str) -> tuple[Gym, GymMembership]:
    user = User.objects.create_user(f"{code}@example.test", "correct horse battery staple")
    gym = Gym.objects.create(code=code, name=code, status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        actor = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=gym,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    actor.user = user
    return gym, actor


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code,
        defaults={"name": code, "description": code},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _goal(gym: Gym, actor: GymMembership) -> Goal:
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Members",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return goal


def _step(goal: Goal, tool_name: str) -> WorkflowStep:
    found = WorkflowStep.objects.filter(goal=goal, tool_name=tool_name).first()
    if found is not None:
        return found
    with transaction.atomic():
        set_gym_context(gym_id=goal.gym_id)
        return WorkflowStep.objects.create(
            gym_id=goal.gym_id,
            workflow=goal.workflow,
            goal=goal,
            position=WorkflowStep.objects.filter(goal=goal).count() + 1,
            tool_name=tool_name,
        )


def _permissions(tool_name: str) -> frozenset[str]:
    for tool in member_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _request(gym: Gym, actor: GymMembership, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_member_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    *,
    expected_version: int,
    confirmed: bool = True,
    codes: frozenset[str] | None = None,
):
    return execute_registered_tool(
        request=_request(gym, actor, _permissions(tool_name) if codes is None else codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def _member_payload(first_name: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "first_name": first_name,
        "last_name": "Aye",
        "join_date": "2020-01-01",
    }
    payload.update(extra)
    return payload


def test_create_member_is_once_per_execution_identity_and_allocates_numbers() -> None:
    gym, actor = _staff("ex-mem-num")
    _enable(gym, "members")
    goal = _goal(gym, actor)
    payload = _member_payload("Maung")
    first = _run(gym, actor, goal, "create_member", payload, expected_version=1)
    replay = _run(gym, actor, goal, "create_member", payload, expected_version=1)
    second = _run(
        gym,
        actor,
        goal,
        "create_member",
        _member_payload("Hla"),
        expected_version=2,
    )
    members = list(Member.objects.filter(gym=gym).order_by("member_number"))
    settings = GymSettings.objects.get(gym=gym)
    assert first.outcome == "committed"
    assert replay.outcome == "existing"
    assert replay.effect_reference == first.effect_reference
    assert second.outcome == "committed"
    assert [member.member_number for member in members] == ["MEM-000001", "MEM-000002"]
    assert str(members[0].id) == first.effect_reference
    assert settings.next_member_number == 3
    assert ToolExecution.objects.filter(goal=goal).count() == 2


def test_unconfirmed_photo_permission_and_feature_create_no_member() -> None:
    gym, actor = _staff("ex-mem-deny")
    goal = _goal(gym, actor)
    payload = _member_payload("Maung")
    with pytest.raises(PermissionDenied):
        _run(
            gym,
            actor,
            goal,
            "create_member",
            payload,
            expected_version=1,
            codes=frozenset(),
        )
    _enable(gym, "members")
    with pytest.raises(ExecutionRejected) as unconfirmed:
        _run(gym, actor, goal, "create_member", payload, expected_version=1, confirmed=False)
    assert unconfirmed.value.code == "confirmation_required"
    with pytest.raises(ValidationError):
        _run(
            gym,
            actor,
            goal,
            "create_member",
            _member_payload("Maung", profile_photo_asset_id=str(uuid4())),
            expected_version=1,
        )
    bare = _staff("ex-mem-feat")
    feature_gym, feature_actor = bare
    feature_goal = _goal(feature_gym, feature_actor)
    with pytest.raises(PermissionDenied):
        _run(
            feature_gym,
            feature_actor,
            feature_goal,
            "create_member",
            payload,
            expected_version=1,
        )
    assert Member.objects.filter(gym=gym).count() == 0
    assert Member.objects.filter(gym=feature_gym).count() == 0
    assert ToolExecution.objects.filter(goal=goal).count() == 0
    assert GymSettings.objects.get(gym=gym).next_member_number == 1


def test_duplicate_manual_number_rolls_back_the_second_claim() -> None:
    gym, actor = _staff("ex-mem-dup")
    _enable(gym, "members")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(allow_manual_member_numbers=True)
    goal = _goal(gym, actor)
    _run(
        gym,
        actor,
        goal,
        "create_member",
        _member_payload("Ada", member_number="MANUAL-1"),
        expected_version=1,
    )
    with pytest.raises(MemberConflict):
        _run(
            gym,
            actor,
            goal,
            "create_member",
            _member_payload("Bea", member_number="MANUAL-1"),
            expected_version=2,
        )
    assert list(Member.objects.filter(gym=gym).values_list("first_name", flat=True)) == ["Ada"]
    assert ToolExecution.objects.filter(goal=goal).count() == 1
    goal.refresh_from_db()
    assert goal.version == 2


def test_update_archive_and_restore_use_the_existing_member_service() -> None:
    gym, actor = _staff("ex-mem-life")
    _enable(gym, "members")
    goal = _goal(gym, actor)
    created = _run(
        gym,
        actor,
        goal,
        "create_member",
        _member_payload("Maung"),
        expected_version=1,
    )
    member_id = str(created.effect_reference)
    updated = _run(
        gym,
        actor,
        goal,
        "update_member",
        {"member_id": member_id, "last_name": "Htwe"},
        expected_version=2,
    )
    archived = _run(
        gym,
        actor,
        goal,
        "archive_member",
        {"member_id": member_id},
        expected_version=3,
    )
    member = Member.objects.get(id=member_id)
    archived_version = member.authz_version
    replay = _run(
        gym,
        actor,
        goal,
        "archive_member",
        {"member_id": member_id},
        expected_version=3,
    )
    restored = _run(
        gym,
        actor,
        goal,
        "restore_member",
        {"member_id": member_id},
        expected_version=4,
    )
    member.refresh_from_db()
    assert updated.effect_reference == member_id
    assert archived.outcome == "committed"
    assert replay.outcome == "existing"
    assert restored.outcome == "committed"
    assert member.last_name == "Htwe"
    assert member.status == Member.Status.ACTIVE
    assert member.archived_at is None
    assert member.authz_version == archived_version + 1
    restored_audits = AuditLog.objects.filter(
        gym=gym,
        action="MEMBER_RESTORED",
        target_id=member.id,
    )
    assert restored_audits.count() == 1


def test_plan_tools_create_update_and_archive() -> None:
    gym, actor = _staff("ex-plan")
    _enable(gym, "memberships")
    goal = _goal(gym, actor)
    created = _run(
        gym,
        actor,
        goal,
        "create_plan",
        {
            "name": "Monthly",
            "duration_value": 1,
            "duration_unit": "MONTH",
            "price": "1500.00",
        },
        expected_version=1,
    )
    plan_id = str(created.effect_reference)
    _run(
        gym,
        actor,
        goal,
        "update_plan",
        {"plan_id": plan_id, "name": "Monthly Plus"},
        expected_version=2,
    )
    _run(gym, actor, goal, "archive_plan", {"plan_id": plan_id}, expected_version=3)
    plan = MembershipPlan.objects.get(gym=gym, id=plan_id)
    assert plan.name == "Monthly Plus"
    assert plan.is_active is False
    assert plan.archived_at is not None
    assert plan.price == 1500


def test_suspend_and_resume_keep_the_stored_expiry() -> None:
    gym, actor = _staff("ex-span")
    _enable(gym, "members")
    _enable(gym, "memberships")
    goal = _goal(gym, actor)
    member = _run(
        gym,
        actor,
        goal,
        "create_member",
        _member_payload("Maung"),
        expected_version=1,
    )
    plan = _run(
        gym,
        actor,
        goal,
        "create_plan",
        {
            "code": "DAY30",
            "name": "Thirty days",
            "duration_value": 30,
            "duration_unit": "DAY",
            "price": "900.00",
        },
        expected_version=2,
    )
    start = gym_today(gym).isoformat()
    created = _run(
        gym,
        actor,
        goal,
        "create_membership",
        {
            "member_id": member.effect_reference,
            "plan_id": plan.effect_reference,
            "start_date": start,
        },
        expected_version=3,
    )
    membership = Membership.objects.get(id=created.effect_reference)
    expiry = membership.expiry_date
    assert membership.status == Membership.Status.ACTIVE
    suspended = _run(
        gym,
        actor,
        goal,
        "suspend_membership",
        {"membership_id": str(membership.id)},
        expected_version=4,
    )
    membership.refresh_from_db()
    assert suspended.outcome == "committed"
    assert membership.status == Membership.Status.SUSPENDED
    assert membership.expiry_date == expiry
    resumed = _run(
        gym,
        actor,
        goal,
        "resume_membership",
        {"membership_id": str(membership.id)},
        expected_version=5,
    )
    membership.refresh_from_db()
    assert resumed.outcome == "committed"
    assert membership.status == Membership.Status.ACTIVE
    assert membership.expiry_date == expiry
    _run(
        gym,
        actor,
        goal,
        "update_membership_note",
        {"membership_id": str(membership.id), "note": "Desk note"},
        expected_version=6,
    )
    _run(
        gym,
        actor,
        goal,
        "cancel_membership",
        {"membership_id": str(membership.id), "reason": "Member moved"},
        expected_version=7,
    )
    membership.refresh_from_db()
    assert membership.note == "Desk note"
    assert membership.status == Membership.Status.CANCELLED
    assert membership.expiry_date == expiry
    assert membership.cancellation_reason == "Member moved"


@override_settings(**jwt_settings())
def test_trainer_scope_stays_inside_the_existing_services() -> None:
    client, platform_access = platform_token("ex-mem-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="EX-MEM",
        owner_email="ex-mem-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym, email="ex-mem-ada@example.test", role_key="TRAINER")
    _enable(gym, "members")
    _enable(gym, "memberships")
    goal = _goal(gym, owner)
    with pytest.raises(Http404):
        _run(
            gym,
            trainer,
            goal,
            "create_member",
            _member_payload("Hidden"),
            expected_version=1,
        )
    created = _run(
        gym,
        owner,
        goal,
        "create_member",
        _member_payload("Visible"),
        expected_version=1,
    )
    plan = _run(
        gym,
        owner,
        goal,
        "create_plan",
        {
            "name": "Monthly",
            "duration_value": 1,
            "duration_unit": "MONTH",
            "price": "1000.00",
        },
        expected_version=2,
    )
    with pytest.raises(Http404):
        _run(
            gym,
            trainer,
            goal,
            "create_membership",
            {
                "member_id": created.effect_reference,
                "plan_id": plan.effect_reference,
                "start_date": gym_today(gym).isoformat(),
            },
            expected_version=3,
        )
    assert Member.objects.filter(gym=gym).count() == 1
    assert Membership.objects.filter(gym=gym).count() == 0
