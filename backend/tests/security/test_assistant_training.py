from __future__ import annotations

import os
from datetime import date, time, timedelta
from uuid import UUID

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.member_tools import execute_registered_tool as execute_member_tool
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.assistant.training_tools import execute_registered_tool, training_tool_definitions
from apps.audit.models import AuditLog
from apps.classes.booking_services import create_class_booking
from apps.classes.exceptions import ClassTypeConflict, ClassTypeInactive
from apps.classes.models import ClassBooking, ClassOccurrence, ClassSchedule, ClassType
from apps.classes.services import create_class_schedule
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymSettings,
    StaffProfile,
)
from apps.trainers.exceptions import (
    AssignmentOverlap,
    AssignmentStateConflict,
    ServicePackageConflict,
)
from apps.trainers.models import ServicePackage, TrainerAssignment
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
        intent="Training",
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


def _codes(tool_name: str) -> frozenset[str]:
    for tool in training_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _request(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    *,
    codes: frozenset[str] | None = None,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = _codes(tool_name) if codes is None else codes
    request.request_id = "req_training_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str] | None = None,
):
    return execute_registered_tool(
        request=_request(gym, actor, tool_name, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=True,
    )


def _member_codes(tool_name: str) -> frozenset[str]:
    from apps.assistant.member_tools import member_tool_definitions

    for tool in member_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _member(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    payload: dict[str, object],
    expected_version: int,
):
    request = _request(gym, actor, "create_member", codes=_member_codes("create_member"))
    return execute_member_tool(
        request=request,
        view=APIView(),
        tool_name="create_member",
        goal=goal,
        step_id=_step(goal, "create_member").id,
        expected_version=expected_version,
        payload=payload,
        confirmed=True,
    )


def _profile(gym: Gym, membership: GymMembership, *, code: str, name: str) -> StaffProfile:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return StaffProfile.objects.create(
            gym=gym,
            gym_membership=membership,
            staff_code=code,
            display_name=name,
            phone="",
            joined_date=date.today(),
            status=StaffProfile.Status.ACTIVE,
            employment_title="",
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )


def _executions(goal: Goal, tool_name: str) -> int:
    return ToolExecution.objects.filter(goal=goal, tool_name=tool_name).count()


def _audits(gym: Gym, action: str, target_id: str) -> int:
    return AuditLog.objects.filter(gym=gym, action=action, target_id=target_id).count()


@override_settings(**jwt_settings())
def test_assignments_keep_scope_overlap_and_history() -> None:
    client, token = platform_token("asn1-platform@example.test")
    payload = provision(client, token, code="ASN1", owner_email="asn1-owner@example.test")
    other_payload = provision(client, token, code="ASN2", owner_email="asn2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="asn1-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer_membership, code="ADA", name="Ada")
    _enable(gym, "pt")
    _enable(gym, "members")
    _enable(other, "pt")
    _enable(other, "members")
    goal = _goal(gym, owner)
    other_goal = _goal(other, other_owner)
    member = _member(
        gym,
        owner,
        goal,
        {"first_name": "Hla", "last_name": "Aye", "join_date": "2020-01-01"},
        1,
    )
    other_member = _member(
        other,
        other_owner,
        other_goal,
        {"first_name": "Other", "last_name": "Gym", "join_date": "2020-01-01"},
        1,
    )
    assignment = {
        "trainer_staff_profile_id": str(profile.id),
        "member_id": member.effect_reference,
        "effective_from": "2026-11-01",
        "reason": "Program",
    }
    with pytest.raises(Http404):
        _run(gym, trainer_membership, goal, "create_assignment", assignment, member.version)
    with pytest.raises(Http404):
        _run(
            gym,
            owner,
            goal,
            "create_assignment",
            {**assignment, "member_id": other_member.effect_reference},
            member.version,
        )
    assert TrainerAssignment.objects.filter(gym=gym).count() == 0
    assert _executions(goal, "create_assignment") == 0

    created = _run(gym, owner, goal, "create_assignment", assignment, member.version)
    created_again = _run(gym, owner, goal, "create_assignment", assignment, member.version)
    row = TrainerAssignment.objects.get(id=created.effect_reference)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert row.status == TrainerAssignment.Status.ACTIVE
    assert row.gym_id == gym.id
    assert row.member_id == UUID(str(member.effect_reference))
    assert _audits(gym, "TRAINER_ASSIGNMENT_CREATED", str(row.id)) == 1
    assert _executions(goal, "create_assignment") == 1

    with pytest.raises(AssignmentOverlap):
        _run(
            gym,
            owner,
            goal,
            "create_assignment",
            {**assignment, "reason": "Overlap"},
            created.version,
        )
    assert TrainerAssignment.objects.filter(gym=gym).count() == 1
    assert _executions(goal, "create_assignment") == 1

    second_member = _member(
        gym,
        owner,
        goal,
        {"first_name": "Min", "last_name": "Aye", "join_date": "2020-01-01"},
        created.version,
    )
    second = _run(
        gym,
        owner,
        goal,
        "create_assignment",
        {
            "trainer_staff_profile_id": str(profile.id),
            "member_id": second_member.effect_reference,
            "effective_from": "2026-12-01",
            "reason": "Second program",
        },
        second_member.version,
    )
    ended = _run(
        gym,
        owner,
        goal,
        "end_assignment",
        {
            "assignment_id": created.effect_reference,
            "effective_until": "2026-11-30",
            "reason": "Program finished",
        },
        second.version,
    )
    ended_again = _run(
        gym,
        owner,
        goal,
        "end_assignment",
        {
            "assignment_id": created.effect_reference,
            "effective_until": "2026-11-30",
            "reason": "Program finished",
        },
        second.version,
    )
    row.refresh_from_db()
    assert ended.outcome == "committed"
    assert ended_again.outcome == "existing"
    assert row.status == TrainerAssignment.Status.ENDED
    assert row.effective_until == date(2026, 11, 30)
    assert row.ended_at is not None
    assert _audits(gym, "TRAINER_ASSIGNMENT_ENDED", str(row.id)) == 1

    cancelled = _run(
        gym,
        owner,
        goal,
        "cancel_assignment",
        {"assignment_id": second.effect_reference, "reason": "Member stopped"},
        ended.version,
    )
    cancelled_row = TrainerAssignment.objects.get(id=second.effect_reference)
    assert cancelled.outcome == "committed"
    assert cancelled_row.status == TrainerAssignment.Status.CANCELLED
    assert cancelled_row.reason == "Member stopped"
    assert _audits(gym, "TRAINER_ASSIGNMENT_CANCELLED", str(cancelled_row.id)) == 1
    with pytest.raises(AssignmentStateConflict):
        _run(
            gym,
            owner,
            goal,
            "end_assignment",
            {
                "assignment_id": second.effect_reference,
                "effective_until": "2026-12-15",
                "reason": "Too late",
            },
            cancelled.version,
        )
    cancelled_row.refresh_from_db()
    assert cancelled_row.status == TrainerAssignment.Status.CANCELLED
    assert TrainerAssignment.objects.filter(gym=gym).count() == 2
    assert Member.objects.filter(gym=other).count() == 1


@override_settings(**jwt_settings())
def test_packages_and_class_types_keep_schedule_and_booking_rows() -> None:
    client, token = platform_token("pkg1-platform@example.test")
    payload = provision(client, token, code="PKG1", owner_email="pkg1-owner@example.test")
    other_payload = provision(client, token, code="PKG2", owner_email="pkg2-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    trainer_membership = add_staff(gym, email="pkg1-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer_membership, code="YOGA", name="Yoga Trainer")
    _enable(gym, "pt")
    _enable(gym, "members")
    _enable(other, "pt")
    goal = _goal(gym, owner)
    other_goal = _goal(other, other_owner)
    member = _member(
        gym,
        owner,
        goal,
        {"first_name": "Su", "last_name": "Aye", "join_date": "2020-01-01"},
        1,
    )
    bare_user = User.objects.create_user("pkg-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="PKGB", name="Bare", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=bare.id)
        bare_actor = GymMembership.objects.create(
            gym=bare,
            user=bare_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=bare,
            created_by_membership_id=bare_actor.id,
            updated_by_membership_id=bare_actor.id,
        )
    bare_actor.user = bare_user
    bare_goal = _goal(bare, bare_actor)
    with pytest.raises(PermissionDenied):
        _run(
            bare,
            bare_actor,
            bare_goal,
            "create_class_type",
            {"name": "Blocked", "default_duration_minutes": 45, "default_capacity": 8},
            1,
        )
    assert ClassType.objects.filter(gym=bare).count() == 0

    class_payload = {
        "code": "YOGA",
        "name": "Yoga",
        "default_duration_minutes": 45,
        "default_capacity": 8,
    }
    created = _run(gym, owner, goal, "create_class_type", class_payload, member.version)
    created_again = _run(gym, owner, goal, "create_class_type", class_payload, member.version)
    class_type = ClassType.objects.get(id=created.effect_reference)
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert class_type.gym_id == gym.id
    assert _executions(goal, "create_class_type") == 1
    with pytest.raises(ClassTypeConflict):
        _run(
            gym,
            owner,
            goal,
            "create_class_type",
            {**class_payload, "name": "Morning Yoga"},
            created.version,
        )
    assert ClassType.objects.filter(gym=gym).count() == 1

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        schedule = create_class_schedule(
            gym=gym,
            actor=owner,
            class_type_id=class_type.id,
            trainer_staff_profile_id=profile.id,
            weekdays=[0],
            local_start_time=time(18, 0),
            local_end_time=time(18, 45),
            effective_from=date(2026, 11, 16),
            effective_until=date(2026, 11, 16),
            location="Studio",
            location_is_exclusive=True,
            capacity=8,
            request_id="req_schedule_setup",
            ip_address=None,
            user_agent="",
        )
        booking = create_class_booking(
            gym=gym,
            actor=owner,
            class_occurrence_id=schedule.occurrences.get().id,
            member_id=UUID(str(member.effect_reference)),
            member_service_package_id=None,
            unmetered=True,
            unmetered_reason="Front desk booking",
            request_id="req_booking_setup",
            ip_address=None,
            user_agent="",
        )
    occurrence = ClassOccurrence.objects.get(id=booking.class_occurrence_id)
    schedule_before = (
        schedule.id,
        schedule.capacity,
        schedule.local_start_time,
        schedule.status,
        schedule.class_type_id,
    )
    occurrence_before = (
        occurrence.id,
        occurrence.capacity,
        occurrence.starts_at,
        occurrence.status,
        occurrence.class_type_id,
    )
    booking_before = (booking.id, booking.status, booking.class_occurrence_id, booking.member_id)

    updated = _run(
        gym,
        owner,
        goal,
        "update_class_type",
        {
            "class_type_id": str(class_type.id),
            "name": "Power Yoga",
            "default_capacity": 20,
        },
        created.version,
    )
    updated_again = _run(
        gym,
        owner,
        goal,
        "update_class_type",
        {
            "class_type_id": str(class_type.id),
            "name": "Power Yoga",
            "default_capacity": 20,
        },
        created.version,
    )
    class_type.refresh_from_db()
    schedule.refresh_from_db()
    occurrence.refresh_from_db()
    booking.refresh_from_db()
    assert updated.outcome == "committed"
    assert updated_again.outcome == "existing"
    assert class_type.name == "Power Yoga"
    assert class_type.default_capacity == 20
    assert class_type.code == "YOGA"
    assert schedule_before == (
        schedule.id,
        schedule.capacity,
        schedule.local_start_time,
        schedule.status,
        schedule.class_type_id,
    )
    assert occurrence_before == (
        occurrence.id,
        occurrence.capacity,
        occurrence.starts_at,
        occurrence.status,
        occurrence.class_type_id,
    )
    assert booking_before == (
        booking.id,
        booking.status,
        booking.class_occurrence_id,
        booking.member_id,
    )
    assert booking.status == ClassBooking.Status.BOOKED
    assert ClassSchedule.objects.filter(gym=gym).count() == 1

    with pytest.raises(Http404):
        _run(
            other,
            other_owner,
            other_goal,
            "update_class_type",
            {"class_type_id": str(class_type.id), "name": "Stolen"},
            1,
        )
    class_type.refresh_from_db()
    assert class_type.name == "Power Yoga"

    retired = _run(
        gym,
        owner,
        goal,
        "update_class_type",
        {"class_type_id": str(class_type.id), "is_active": False},
        updated.version,
    )
    class_type.refresh_from_db()
    occurrence.refresh_from_db()
    booking.refresh_from_db()
    assert retired.outcome == "committed"
    assert class_type.is_active is False
    assert occurrence.status == ClassOccurrence.Status.SCHEDULED
    assert booking.status == ClassBooking.Status.BOOKED
    with pytest.raises(ClassTypeInactive):
        _run(
            gym,
            owner,
            goal,
            "create_service_package",
            {
                "type": "CLASS",
                "code": "CLASS8",
                "name": "Class Eight",
                "session_count": 8,
                "validity_value": 30,
                "validity_unit": "DAY",
                "price": "1500.00",
                "class_type_id": str(class_type.id),
            },
            retired.version,
        )
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            "create_service_package",
            {
                "type": "CLASS",
                "name": "Missing type",
                "session_count": 4,
                "validity_value": 30,
                "validity_unit": "DAY",
                "price": "800.00",
            },
            retired.version,
        )
    package_payload = {
        "type": "PT",
        "code": "INTRO",
        "name": "Intro",
        "session_count": 8,
        "validity_value": 30,
        "validity_unit": "DAY",
        "price": "1500.00",
    }
    package = _run(
        gym,
        owner,
        goal,
        "create_service_package",
        package_payload,
        retired.version,
    )
    package_again = _run(
        gym,
        owner,
        goal,
        "create_service_package",
        package_payload,
        retired.version,
    )
    stored = ServicePackage.objects.get(id=package.effect_reference)
    assert package.outcome == "committed"
    assert package_again.outcome == "existing"
    assert stored.price == 1500
    assert stored.currency == gym.currency
    assert stored.type == ServicePackage.PackageType.PT
    assert _executions(goal, "create_service_package") == 1
    with pytest.raises(ServicePackageConflict):
        _run(
            gym,
            owner,
            goal,
            "create_service_package",
            {**package_payload, "name": "Intro Plus"},
            package.version,
        )
    assert ServicePackage.objects.filter(gym=gym).count() == 1
    assert _audits(gym, "SERVICE_PACKAGE_CREATED", str(stored.id)) == 1
