from __future__ import annotations

import os
from datetime import date, timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.assistant.staff_tools import execute_registered_tool
from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.exceptions import (
    InvalidRolePermissions,
    MFARequired,
    OwnerWorkflowConflict,
    RoleAssignmentInvalid,
    RoleWorkflowConflict,
    StaffDelegationDenied,
    StaffWorkflowConflict,
)
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymSettings,
    Role,
    RolePermission,
    StaffInvitation,
    StaffProfile,
)
from apps.users.models import RefreshSession, User
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

_MISSING = object()


@override_settings(**jwt_settings())
def _provisioned(code: str) -> tuple[Gym, GymMembership]:
    client, token = platform_token(f"{code}-platform@example.test")
    payload = provision(client, token, code=code, owner_email=f"{code}-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    return gym, owner


def _goal(gym: Gym, actor: GymMembership) -> Goal:
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Staff",
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


def _request(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    *,
    authz: object = _MISSING,
    session: RefreshSession | None = None,
    codes: frozenset[str] | None = None,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes if codes is not None else _codes(tool_name)
    request.request_id = "req_staff_tool"
    request.refresh_session = session
    if authz is _MISSING:
        request.auth = {"authz_version": actor.authz_version}
    elif isinstance(authz, int):
        request.auth = {"authz_version": authz}
    else:
        request.auth = None
    return request


def _codes(tool_name: str) -> frozenset[str]:
    from apps.assistant.staff_tools import staff_tool_definitions

    for tool in staff_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    **kwargs: object,
):
    actor.refresh_from_db()
    return execute_registered_tool(
        request=_request(gym, actor, tool_name, **kwargs),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
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


@override_settings(**jwt_settings())
def test_staff_profile_changes_stay_in_one_transaction() -> None:
    gym, owner = _provisioned("STF1")
    receptionist = add_staff(gym, email="stf1-desk@example.test", role_key="RECEPTIONIST")
    profile = _profile(gym, receptionist, code="DESK", name="Desk")
    owner_profile = _profile(gym, owner, code="OWN", name="Owner")
    goal = _goal(gym, owner)
    payload = {"staff_id": str(profile.id), "display_name": "Front Desk"}
    updated = _run(gym, owner, goal, "update_staff_profile", payload, 1)
    replay = _run(gym, owner, goal, "update_staff_profile", payload, 1)
    profile.refresh_from_db()
    assert updated.outcome == "committed"
    assert replay.outcome == "existing"
    assert replay.effect_reference == str(profile.id)
    assert profile.display_name == "Front Desk"
    assert _executions(goal, "update_staff_profile") == 1

    with pytest.raises(PermissionDenied):
        _run(
            gym,
            receptionist,
            goal,
            "update_staff_profile",
            {"staff_id": str(profile.id), "display_name": "Taken"},
            updated.version,
            codes=frozenset({"staff.manage"}),
        )
    profile.refresh_from_db()
    assert profile.display_name == "Front Desk"
    assert _executions(goal, "update_staff_profile") == 1

    with pytest.raises(StaffProfile.DoesNotExist):
        _run(
            gym,
            owner,
            goal,
            "suspend_staff",
            {"staff_id": str(owner_profile.id), "reason": "Owner leave"},
            updated.version,
        )
    owner.refresh_from_db()
    assert owner.status == GymMembership.Status.ACTIVE
    assert _executions(goal, "suspend_staff") == 0

    suspended = _run(
        gym,
        owner,
        goal,
        "suspend_staff",
        {"staff_id": str(profile.id), "reason": "Away this week"},
        updated.version,
    )
    replay_suspend = _run(
        gym,
        owner,
        goal,
        "suspend_staff",
        {"staff_id": str(profile.id), "reason": "Away this week"},
        updated.version,
    )
    receptionist.refresh_from_db()
    assert suspended.outcome == "committed"
    assert replay_suspend.outcome == "existing"
    assert receptionist.status == GymMembership.Status.SUSPENDED
    assert _executions(goal, "suspend_staff") == 1

    activated = _run(
        gym,
        owner,
        goal,
        "activate_staff",
        {"staff_id": str(profile.id), "reason": "Back on the desk"},
        suspended.version,
    )
    with pytest.raises(StaffWorkflowConflict):
        _run(
            gym,
            owner,
            goal,
            "activate_staff",
            {"staff_id": str(profile.id), "reason": "Already back"},
            activated.version,
        )
    receptionist.refresh_from_db()
    assert receptionist.status == GymMembership.Status.ACTIVE
    assert _executions(goal, "activate_staff") == 1

    with pytest.raises(StaffProfile.DoesNotExist):
        _run(
            gym,
            owner,
            goal,
            "update_staff_profile",
            {"staff_id": str(uuid4()), "display_name": "Missing"},
            activated.version,
        )
    assert _executions(goal, "update_staff_profile") == 1


@override_settings(**jwt_settings())
def test_role_changes_keep_delegation_and_gym_boundaries() -> None:
    gym, owner = _provisioned("ROL1")
    other_gym, other_owner = _provisioned("ROL2")
    manager = add_staff(gym, email="rol1-manager@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="rol1-desk@example.test", role_key="RECEPTIONIST")
    goal = _goal(gym, owner)
    other_goal = _goal(other_gym, other_owner)
    created = _run(
        gym,
        owner,
        goal,
        "create_role",
        {"key": "FLOOR", "name": "Floor"},
        1,
    )
    replay = _run(gym, owner, goal, "create_role", {"key": "FLOOR", "name": "Floor"}, 1)
    role = Role.objects.get(id=created.effect_reference)
    assert created.outcome == "committed"
    assert replay.outcome == "existing"
    assert role.gym_id == gym.id
    assert role.source_template_id is None
    assert _executions(goal, "create_role") == 1

    before_roles = Role.objects.filter(gym=gym).count()
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            "create_role",
            {"key": "OWNER", "name": "Second Owner"},
            created.version,
        )
    with pytest.raises(RoleWorkflowConflict):
        _run(
            gym,
            owner,
            goal,
            "create_role",
            {"key": "FLOOR", "name": "Another Floor"},
            created.version,
        )
    assert Role.objects.filter(gym=gym).count() == before_roles
    assert _executions(goal, "create_role") == 1

    hidden = _run(
        other_gym,
        other_owner,
        other_goal,
        "create_role",
        {"key": "FLOOR", "name": "Other Floor"},
        1,
    )
    with pytest.raises(Role.DoesNotExist):
        _run(
            gym,
            owner,
            goal,
            "update_role",
            {"role_id": hidden.effect_reference, "name": "Stolen"},
            created.version,
        )
    assert Role.objects.get(id=hidden.effect_reference).name == "Other Floor"
    assert _executions(goal, "update_role") == 0

    for codes, error in (
        (["platform.gyms.view"], InvalidRolePermissions),
        (["payments.approve"], StaffDelegationDenied),
        (["settings.manage"], StaffDelegationDenied),
    ):
        with pytest.raises(error):
            _run(
                gym,
                owner,
                goal,
                "replace_role_permissions",
                {"role_id": str(role.id), "permission_codes": codes},
                created.version,
            )
    stored_codes = set(
        RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)
    )
    assert stored_codes == set()
    assert _executions(goal, "replace_role_permissions") == 0

    replaced = _run(
        gym,
        owner,
        goal,
        "replace_role_permissions",
        {"role_id": str(role.id), "permission_codes": ["members.view"]},
        created.version,
    )
    replaced_again = _run(
        gym,
        owner,
        goal,
        "replace_role_permissions",
        {"role_id": str(role.id), "permission_codes": ["members.view"]},
        created.version,
    )
    assert replaced.outcome == "committed"
    assert replaced_again.outcome == "existing"
    stored_codes = set(
        RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)
    )
    assert stored_codes == {"members.view"}

    with pytest.raises(StaffDelegationDenied):
        _run(
            gym,
            manager,
            goal,
            "replace_role_permissions",
            {"role_id": str(role.id), "permission_codes": ["payments.approve"]},
            replaced.version,
        )
    assert _executions(goal, "replace_role_permissions") == 1

    owner_role = Role.objects.get(gym=gym, key="OWNER")
    owner_name = owner_role.name
    with pytest.raises(RoleWorkflowConflict):
        _run(
            gym,
            owner,
            goal,
            "update_role",
            {"role_id": str(owner_role.id), "name": "Renamed Owner"},
            replaced.version,
        )
    with pytest.raises(StaffDelegationDenied):
        _run(
            gym,
            manager,
            goal,
            "update_role",
            {"role_id": str(owner_role.id), "name": "Manager Rename"},
            replaced.version,
        )
    owner_role.refresh_from_db()
    assert owner_role.name == owner_name

    assigned = _run(
        gym,
        owner,
        goal,
        "replace_membership_roles",
        {"membership_id": str(receptionist.id), "role_ids": [str(role.id)]},
        replaced.version,
    )
    assigned_again = _run(
        gym,
        owner,
        goal,
        "replace_membership_roles",
        {"membership_id": str(receptionist.id), "role_ids": [str(role.id)]},
        replaced.version,
    )
    assert assigned.outcome == "committed"
    assert assigned_again.outcome == "existing"
    assert assigned.effect_reference == str(receptionist.id)
    receptionist.refresh_from_db()
    assert list(receptionist.gymmembershiprole_set.values_list("role_id", flat=True)) == [role.id]

    with pytest.raises(RoleAssignmentInvalid):
        _run(
            gym,
            owner,
            goal,
            "replace_membership_roles",
            {"membership_id": str(receptionist.id), "role_ids": [str(owner_role.id)]},
            assigned.version,
        )
    with pytest.raises(GymMembership.DoesNotExist):
        _run(
            gym,
            owner,
            goal,
            "replace_membership_roles",
            {"membership_id": str(owner.id), "role_ids": [str(role.id)]},
            assigned.version,
        )
    receptionist.refresh_from_db()
    assert list(receptionist.gymmembershiprole_set.values_list("role_id", flat=True)) == [role.id]

    renamed = _run(
        gym,
        owner,
        goal,
        "update_role",
        {"role_id": str(role.id), "name": "Floor Lead"},
        assigned.version,
    )
    archived = _run(
        gym,
        owner,
        goal,
        "archive_role",
        {"role_id": str(role.id)},
        renamed.version,
    )
    role.refresh_from_db()
    assert role.name == "Floor Lead"
    assert role.is_active is False
    assert archived.outcome == "committed"
    with pytest.raises(AuthenticationFailed):
        _run(
            gym,
            owner,
            goal,
            "create_role",
            {"key": "LATE", "name": "Late"},
            archived.version,
            authz=999,
        )
    with pytest.raises(AuthenticationFailed):
        _run(
            gym,
            owner,
            goal,
            "create_role",
            {"key": "LATER", "name": "Later"},
            archived.version,
            authz=None,
        )
    assert Role.objects.filter(gym=gym, key="LATE").count() == 0
    assert _executions(goal, "create_role") == 1


@override_settings(**jwt_settings())
def test_gym_profile_and_settings_keep_owner_protection() -> None:
    gym, owner = _provisioned("GYM1")
    manager = add_staff(gym, email="gym1-manager@example.test", role_key="MANAGER")
    goal = _goal(gym, owner)
    profile = _run(gym, owner, goal, "update_gym_profile", {"city": "Yangon"}, 1)
    replay = _run(gym, owner, goal, "update_gym_profile", {"city": "Yangon"}, 1)
    gym.refresh_from_db()
    assert profile.outcome == "committed"
    assert replay.outcome == "existing"
    assert gym.city == "Yangon"
    assert _executions(goal, "update_gym_profile") == 1

    manager_goal = _goal(gym, manager)
    manager_update = _run(
        gym,
        manager,
        manager_goal,
        "update_gym_profile",
        {"phone": "091111111"},
        1,
    )
    gym.refresh_from_db()
    assert manager_update.outcome == "committed"
    assert gym.phone == "091111111"

    with pytest.raises(ValidationError):
        _run(gym, owner, goal, "update_gym_profile", {"currency": "USD"}, profile.version)
    gym.refresh_from_db()
    assert gym.currency == "THB"
    assert _executions(goal, "update_gym_profile") == 1

    settings = _run(gym, owner, goal, "update_gym_settings", {"week_starts_on": 0}, profile.version)
    replay_settings = _run(
        gym,
        owner,
        goal,
        "update_gym_settings",
        {"week_starts_on": 0},
        profile.version,
    )
    stored = GymSettings.objects.get(gym=gym)
    assert settings.outcome == "committed"
    assert replay_settings.outcome == "existing"
    assert stored.week_starts_on == 0
    assert stored.require_separate_finance_approval is True
    with pytest.raises(PermissionDenied):
        _run(
            gym,
            manager,
            goal,
            "update_gym_settings",
            {"week_starts_on": 3},
            settings.version,
            codes=frozenset({"settings.manage"}),
        )
    stored.refresh_from_db()
    assert stored.week_starts_on == 0
    assert _executions(goal, "update_gym_settings") == 1


@override_settings(**jwt_settings(), MFA_ENFORCED=True)
def test_owner_revoke_and_cancellation_require_recent_mfa() -> None:
    gym, owner = _provisioned("OWN1")
    owner.user.mfa_enabled = True
    owner.user.mfa_secret_encrypted = "configured"
    owner.user.save(update_fields=["mfa_enabled", "mfa_secret_encrypted", "updated_at"])
    second_user = User.objects.create_user(
        "own1-second@example.test",
        "correct horse battery staple",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        second = GymMembership.objects.create(
            gym=gym,
            user=second_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        invited_user = User.objects.create_user(
            "own1-invited@example.test",
            "correct horse battery staple",
        )
        invited = GymMembership.objects.create(
            gym=gym,
            user=invited_user,
            status=GymMembership.Status.INVITED,
            is_owner=True,
            joined_date=date.today(),
        )
        invitation = StaffInvitation.objects.create(
            gym=gym,
            gym_membership=invited,
            email=invited_user.email,
            normalized_email=invited_user.email,
            token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(days=2),
        )
    recent = RefreshSession.objects.create(
        user=owner.user,
        selected_gym=gym,
        gym_membership=owner,
        principal_type=RefreshSession.PrincipalType.STAFF,
        token_hash="b" * 64,
        issued_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
        mfa_verified_at=timezone.now(),
    )
    stale = RefreshSession.objects.create(
        user=owner.user,
        selected_gym=gym,
        gym_membership=owner,
        principal_type=RefreshSession.PrincipalType.STAFF,
        token_hash="c" * 64,
        issued_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
        mfa_verified_at=timezone.now() - timedelta(hours=2),
    )
    goal = _goal(gym, owner)
    revoke_payload = {"membership_id": str(second.id), "reason": "Left the gym"}
    with pytest.raises(MFARequired):
        _run(gym, owner, goal, "revoke_owner", revoke_payload, 1, session=None)
    with pytest.raises(MFARequired):
        _run(gym, owner, goal, "revoke_owner", revoke_payload, 1, session=stale)
    receptionist = add_staff(gym, email="own1-desk@example.test", role_key="RECEPTIONIST")
    with pytest.raises(PermissionDenied):
        _run(gym, receptionist, goal, "revoke_owner", revoke_payload, 1, session=recent)
    second.refresh_from_db()
    assert second.status == GymMembership.Status.ACTIVE
    assert _executions(goal, "revoke_owner") == 0

    with pytest.raises(MFARequired):
        _run(
            gym,
            owner,
            goal,
            "cancel_owner_invitation",
            {"membership_id": str(invited.id)},
            1,
            session=None,
        )
    invitation.refresh_from_db()
    assert invitation.status == StaffInvitation.Status.PENDING

    revoked = _run(gym, owner, goal, "revoke_owner", revoke_payload, 1, session=recent)
    replay = _run(gym, owner, goal, "revoke_owner", revoke_payload, 1, session=recent)
    second.refresh_from_db()
    assert revoked.outcome == "committed"
    assert replay.outcome == "existing"
    assert second.status == GymMembership.Status.REVOKED
    assert _executions(goal, "revoke_owner") == 1
    with pytest.raises(OwnerWorkflowConflict):
        _run(
            gym,
            owner,
            goal,
            "revoke_owner",
            {"membership_id": str(owner.id), "reason": "Self revoke"},
            revoked.version,
            session=recent,
        )
    owner.refresh_from_db()
    assert owner.status == GymMembership.Status.ACTIVE
    assert _executions(goal, "revoke_owner") == 1

    cancelled = _run(
        gym,
        owner,
        goal,
        "cancel_owner_invitation",
        {"membership_id": str(invited.id)},
        revoked.version,
        session=recent,
    )
    cancelled_again = _run(
        gym,
        owner,
        goal,
        "cancel_owner_invitation",
        {"membership_id": str(invited.id)},
        revoked.version,
        session=recent,
    )
    invitation.refresh_from_db()
    invited.refresh_from_db()
    assert cancelled.outcome == "committed"
    assert cancelled_again.outcome == "existing"
    assert invitation.status == StaffInvitation.Status.REVOKED
    assert invited.status == GymMembership.Status.INVITED
    assert _executions(goal, "cancel_owner_invitation") == 1
