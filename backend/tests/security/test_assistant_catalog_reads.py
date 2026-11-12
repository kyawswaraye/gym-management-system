"""PostgreSQL coverage for membership and granted-package reads."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.membership_reads import execute_registered_read as read_membership
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.assistant.package_reads import execute_registered_read as read_package
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Membership, MembershipPlan
from apps.members.services import create_membership, create_plan, gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, IdempotencyRecord
from apps.trainers.models import MemberServicePackage, ServicePackage
from apps.trainers.package_services import grant_package_admin
from apps.trainers.services import create_assignment, create_service_package
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
    request.request_id = "req_catalog_read"
    return request


def _memberships(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return read_membership(
        request=_request(gym, actor, frozenset({"memberships.view"})),
        view=APIView(),
        tool_name="search_memberships",
        payload=payload,
    )


def _packages(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return read_package(
        request=_request(gym, actor, frozenset({"pt.schedule.view"})),
        view=APIView(),
        tool_name="search_pt_packages",
        payload=payload,
    )


def _ids(page: dict[str, object]) -> set[str]:
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows}


def _counts(gym: Gym, goal: Goal) -> tuple[int, ...]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            Membership.objects.filter(gym=gym).count(),
            MemberServicePackage.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


@override_settings(**jwt_settings())
def test_membership_and_package_reads_use_stored_rows() -> None:
    gym, owner = _owner("MB01", "mb01-owner@example.test")
    other_gym, other_owner = _owner("MB02", "mb02-owner@example.test")
    visible = _person(gym, owner, "Hla", "Win")
    hidden = _person(gym, owner, "Aung", "Aung")
    foreign_member = _person(other_gym, other_owner, "Hla", "Win")
    today = gym_today(gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        plan = create_plan(
            gym=gym,
            actor=owner,
            code="M1",
            name="Monthly",
            description="",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("50000.00"),
        )
        first = create_membership(
            gym=gym,
            actor=owner,
            member=visible,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
            note="secret-note",
        )
        second = create_membership(
            gym=gym,
            actor=owner,
            member=hidden,
            plan=plan,
            start_date=today,
            discount_amount=Decimal("0"),
        )
        catalog = create_service_package(
            gym=gym,
            actor=owner,
            package_type=ServicePackage.PackageType.PT,
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id="req_catalog_read",
            ip_address=None,
            user_agent="",
        )
        grant, _entry = grant_package_admin(
            gym=gym,
            actor=owner,
            member_id=visible.id,
            service_package_id=catalog.id,
            business_date=today,
            complimentary=True,
            reason="Welcome sessions",
            request_id="req_catalog_read",
            ip_address=None,
            user_agent="",
        )
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        other_plan = create_plan(
            gym=other_gym,
            actor=other_owner,
            code="M1",
            name="Monthly",
            description="",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("50000.00"),
        )
        foreign = create_membership(
            gym=other_gym,
            actor=other_owner,
            member=foreign_member,
            plan=other_plan,
            start_date=gym_today(other_gym),
            discount_amount=Decimal("0"),
        )
        other_catalog = create_service_package(
            gym=other_gym,
            actor=other_owner,
            package_type=ServicePackage.PackageType.PT,
            code="PT3",
            name="Three sessions",
            session_count=3,
            validity_value=30,
            validity_unit=ServicePackage.ValidityUnit.DAY,
            price=Decimal("100.00"),
            request_id="req_catalog_read",
            ip_address=None,
            user_agent="",
        )
        foreign_grant, _foreign_entry = grant_package_admin(
            gym=other_gym,
            actor=other_owner,
            member_id=foreign_member.id,
            service_package_id=other_catalog.id,
            business_date=gym_today(other_gym),
            complimentary=True,
            reason="Other gym",
            request_id="req_catalog_read",
            ip_address=None,
            user_agent="",
        )
    trainer = add_staff(gym, email="mb01-trainer@example.test", role_key="TRAINER")
    accountant = add_staff(gym, email="mb01-accountant@example.test", role_key="ACCOUNTANT")
    profile = _profile(gym, trainer, code="MBT", name="Maung")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=profile.id,
            member_id=visible.id,
            effective_from=today,
            effective_until=None,
            reason="Program",
            request_id="req_catalog_read",
            ip_address=None,
            user_agent="",
        )
    goal = _goal(gym, owner)
    before = _counts(gym, goal)

    page = _memberships(gym, owner, {"member_id": str(visible.id)})
    assert _ids(page) == {str(first.id)}
    row = page["results"][0]
    assert row["status"] == Membership.Status.ACTIVE
    assert row["member_number"] == visible.member_number
    assert row["plan_name"] == "Monthly"
    assert row["start_date"] == first.start_date.isoformat()
    assert row["expiry_date"] == first.expiry_date.isoformat()
    assert row["final_amount"] == "50000.00"
    assert "secret-note" not in str(page)
    exact = _memberships(gym, owner, {"membership_id": str(first.id)})
    assert _ids(exact) == {str(first.id)}
    with pytest.raises(ExecutionRejected) as missing_membership:
        _memberships(gym, owner, {"membership_id": str(foreign.id)})
    assert missing_membership.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as unknown_membership:
        _memberships(gym, owner, {"membership_id": str(uuid4())})
    assert unknown_membership.value.code == "not_found"
    with pytest.raises(ValidationError):
        _memberships(gym, owner, {"membership_id": "not-a-membership"})
    trainer_page = _memberships(gym, trainer, {})
    assert _ids(trainer_page) == {str(first.id)}
    with pytest.raises(ExecutionRejected) as hidden_membership:
        _memberships(gym, trainer, {"member_id": str(hidden.id)})
    assert hidden_membership.value.code == "not_found"

    seen: set[str] = set()
    cursor: object = None
    for _page_number in range(4):
        payload: dict[str, object] = {"limit": 1}
        if isinstance(cursor, str):
            payload["after"] = cursor
        cursor_page = _memberships(gym, owner, payload)
        found = _ids(cursor_page)
        assert len(found) == 1
        seen |= found
        if cursor_page["has_more"] is False:
            break
        cursor = cursor_page["next_after"]
    assert seen == {str(first.id), str(second.id)}
    with pytest.raises(ValidationError):
        _memberships(other_gym, other_owner, {"limit": 1, "after": cursor})

    packages = _packages(gym, owner, {"member_id": str(visible.id)})
    assert _ids(packages) == {str(grant.id)}
    package_row = packages["results"][0]
    assert package_row["status"] == MemberServicePackage.Status.ACTIVE
    assert package_row["granted_sessions"] == 3
    assert package_row["remaining_sessions"] == 3
    assert package_row["package_code"] == "PT3"
    assert package_row["valid_from"] == today.isoformat()
    exact_package = _packages(gym, owner, {"package_id": str(grant.id)})
    assert _ids(exact_package) == {str(grant.id)}
    with pytest.raises(ExecutionRejected) as foreign_package:
        _packages(gym, owner, {"package_id": str(foreign_grant.id)})
    assert foreign_package.value.code == "not_found"
    trainer_packages = _packages(gym, trainer, {"member_id": str(visible.id)})
    assert _ids(trainer_packages) == {str(grant.id)}
    hidden_packages = _packages(gym, trainer, {"member_id": str(hidden.id)})
    assert hidden_packages["results"] == []
    empty_packages = _packages(gym, owner, {"member_id": str(hidden.id), "package_type": "PT"})
    assert empty_packages["results"] == []

    with pytest.raises(PermissionDenied):
        read_membership(
            request=_request(gym, accountant, frozenset({"gym.view"})),
            view=APIView(),
            tool_name="search_memberships",
            payload={},
        )
    with pytest.raises(PermissionDenied):
        read_package(
            request=_request(gym, accountant, frozenset({"gym.view"})),
            view=APIView(),
            tool_name="search_pt_packages",
            payload={},
        )
    with pytest.raises(ValidationError):
        _memberships(gym, owner, {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _packages(gym, owner, {"package_type": "YOGA"})
    with pytest.raises(ExecutionRejected) as limited:
        _memberships(gym, owner, {"limit": 26})
    assert limited.value.code == "invalid_limit"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="memberships").update(
            enabled=False
        )
    with pytest.raises(PermissionDenied):
        _memberships(gym, owner, {})
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="pt").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _packages(gym, owner, {})

    assert _counts(gym, goal) == before
    assert isinstance(first.id, UUID)
    assert isinstance(grant.id, UUID)


def _plans(gym: Gym, actor: GymMembership, payload: dict[str, object]):
    return read_membership(
        request=_request(gym, actor, frozenset({"memberships.view"})),
        view=APIView(),
        tool_name="search_membership_plans",
        payload=payload,
    )


@override_settings(**jwt_settings())
def test_membership_plan_search_keeps_inactive_plans_inside_the_gym() -> None:
    gym, owner = _owner("PL01", "pl01-owner@example.test")
    other_gym, other_owner = _owner("PL02", "pl02-owner@example.test")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_plan(
            gym=gym,
            actor=owner,
            code="YOGA",
            name="Yoga",
            description="local-secret",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("50000.00"),
        )
        second = create_plan(
            gym=gym,
            actor=owner,
            code="YOGA_2",
            name="Yoga",
            description="local-secret",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("50000.00"),
        )
        create_plan(
            gym=gym,
            actor=owner,
            code="PROBE",
            name="Probe",
            description="",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("1000.00"),
        )
        MembershipPlan.objects.filter(id=second.id).update(is_active=False)
        MembershipPlan.objects.filter(gym=gym, code="PROBE").update(is_active=False)
    with transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        create_plan(
            gym=other_gym,
            actor=other_owner,
            code="YOGA",
            name="Yoga",
            description="foreign-secret",
            duration_value=1,
            duration_unit=MembershipPlan.DurationUnit.MONTH,
            price=Decimal("1.00"),
        )

    named = _plans(gym, owner, {"name": "Yoga"})
    rows = named["results"]
    assert isinstance(rows, list)
    assert {row["code"] for row in rows} == {"YOGA", "YOGA_2"}
    assert named["ambiguous"] is True
    inactive = next(row for row in rows if row["code"] == "YOGA_2")
    assert inactive["status"] == "INACTIVE"
    assert "description" not in inactive
    assert "local-secret" not in str(named)
    assert "foreign-secret" not in str(named)
    catalog = _plans(gym, owner, {})
    catalog_rows = catalog["results"]
    assert isinstance(catalog_rows, list)
    assert {row["code"] for row in catalog_rows} == {"PROBE", "YOGA", "YOGA_2"}
    with pytest.raises(ExecutionRejected) as foreign:
        _plans(gym, owner, {"gym_id": str(other_gym.id), "name": "Yoga"})
    assert foreign.value.code == "gym_mismatch"
