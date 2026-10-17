from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.core import mail
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.exceptions import InvalidInvitation, StaffWorkflowConflict
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
    StaffInvitation,
    StaffInvitationRole,
    StaffProfile,
)
from apps.tenants.services import _hash_invitation_token, invite_owner
from apps.tenants.staff_services import accept_staff_invitation, invite_staff
from apps.users.models import RefreshSession, User
from apps.users.tokens import decode_access_token
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
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


def _roles(payload: dict[str, object]) -> dict[str, str]:
    return {row["key"]: row["id"] for row in payload["roles"]}  # type: ignore[index]


def _invite_body(role_id: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "email": "tara.trainer@example.test",
        "first_name": "Tara",
        "last_name": "Trainer",
        "phone": "5551112222",
        "staff_code": "TR-01",
        "display_name": "Tara Trainer",
        "joined_date": "2026-10-01",
        "employment_title": "Coach",
        "default_commission_percent": "12.50",
        "role_ids": [role_id],
    }
    body.update(overrides)
    return body


def _token_from_mail() -> str:
    message = mail.outbox[-1].body
    marker = "token="
    token = message.split(marker, 1)[1].split()[0]
    assert token
    return token


@override_settings(**jwt_settings())
def test_staff_invitation_lifecycle_is_gym_scoped_and_revokes_sessions(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client, platform_access = platform_token("staff-life-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="STF-A", owner_email="owner-a-staff@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="STF-B", owner_email="owner-b-staff@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    mail.outbox.clear()

    invited = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(_roles(gym_a_payload)["RECEPTIONIST"]),
        format="json",
        **owner_headers,
    )
    assert invited.status_code == 201, invited.json()
    invitation = invited.json()["data"]
    token = _token_from_mail()
    raw = invited.content.decode()
    assert token not in raw
    assert "invitation_token" not in raw
    assert token not in caplog.text
    assert invitation["staff"]["membership"]["status"] == "INVITED"
    assert invitation["staff"]["membership"]["is_owner"] is False
    assert invitation["role_ids"] == [_roles(gym_a_payload)["RECEPTIONIST"]]
    audit = AuditLog.objects.get(action="STAFF_INVITED", target_id=invitation["id"])
    assert token not in json.dumps(audit.new_values)
    assert "token" not in audit.new_values

    hidden = client.get(f"/api/v1/staff/{invitation['staff']['id']}/", **other_headers)
    assert hidden.status_code == 404
    other_list = client.get("/api/v1/staff/?search=Tara", **other_headers)
    assert other_list.status_code == 200
    assert other_list.json()["data"] == []
    assert other_list.json()["meta"] == {"has_more": False, "next_after": None}

    bare = APIClient()
    assert (
        bare.post(
            "/api/v1/staff/invitations/accept/",
            {
                "invitation_token": token,
                "password": PASSWORD,
                "password_confirmation": PASSWORD,
            },
            format="json",
        ).status_code
        == 403
    )
    accepted = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "display_name": "Tara Confirmed",
        },
        format="json",
    )
    assert accepted.status_code == 200, accepted.json()
    session = accepted.json()["data"]
    assert session["requires_gym_selection"] is False
    assert "gym_selection_ticket" not in session
    claims = decode_access_token(session["access_token"])
    assert claims["gym_id"] == str(gym_a.id)
    staff_headers = auth_headers(session["access_token"])
    detail = client.get(f"/api/v1/staff/{invitation['staff']['id']}/", **owner_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["display_name"] == "Tara Confirmed"
    assert detail.json()["data"]["membership"]["status"] == "ACTIVE"
    assert detail.json()["data"]["role_ids"] == [_roles(gym_a_payload)["RECEPTIONIST"]]
    assert (
        GymMembershipRole.objects.filter(gym_membership_id=claims["gym_membership_id"]).count() == 1
    )

    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        other_membership = GymMembership.objects.create(
            gym=gym_b,
            user_id=claims["sub"],
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(
            gym=gym_b,
            gym_membership=other_membership,
            role=Role.objects.get(gym=gym_b, key="RECEPTIONIST"),
        )
    other_staff_headers = auth_headers(staff_token(other_membership))
    early_activate = client.post(
        f"/api/v1/staff/{invitation['staff']['id']}/activate/",
        {"reason": "Already active"},
        format="json",
        **owner_headers,
    )
    assert early_activate.status_code == 409

    suspended = client.post(
        f"/api/v1/staff/{invitation['staff']['id']}/suspend/",
        {"reason": "Leave of absence"},
        format="json",
        **owner_headers,
    )
    assert suspended.status_code == 200, suspended.json()
    assert suspended.json()["data"]["membership"]["status"] == "SUSPENDED"
    assert (
        suspended.json()["data"]["membership"]["authz_version"]
        > invitation["staff"]["membership"]["authz_version"]
    )
    assert client.get("/api/v1/staff/", **staff_headers).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh/",
            {"refresh_token": session["refresh_token"]},
            format="json",
        ).status_code
        == 401
    )
    assert client.get("/api/v1/gym/", **other_staff_headers).status_code == 200
    assert (
        RefreshSession.objects.filter(
            gym_membership_id=claims["gym_membership_id"], revoked_at__isnull=True
        ).count()
        == 0
    )

    activated = client.post(
        f"/api/v1/staff/{invitation['staff']['id']}/activate/",
        {"reason": "Returned from leave"},
        format="json",
        **owner_headers,
    )
    assert activated.status_code == 200, activated.json()
    assert activated.json()["data"]["membership"]["status"] == "ACTIVE"
    assert (
        client.post(
            "/api/v1/auth/refresh/",
            {"refresh_token": session["refresh_token"]},
            format="json",
        ).status_code
        == 401
    )
    relogin = client.post(
        "/api/v1/auth/login/",
        {"email": "tara.trainer@example.test", "password": PASSWORD},
        format="json",
    )
    assert relogin.status_code == 200, relogin.json()
    assert relogin.json()["data"]["requires_gym_selection"] is True
    assert str(gym_a.id) in {
        row["gym_id"] for row in relogin.json()["data"]["available_gym_memberships"]
    }
    suspend_audit = AuditLog.objects.get(action="STAFF_SUSPENDED")
    assert suspend_audit.new_values["reason"] == "Leave of absence"
    assert "token" not in suspend_audit.new_values


@override_settings(**jwt_settings())
def test_staff_permissions_delegation_and_mass_assignment() -> None:
    client, platform_access = platform_token("staff-authz-platform@example.test")
    payload = provision(client, platform_access, code="STF-AZ", owner_email="owner-az@example.test")
    other = provision(client, platform_access, code="STF-AY", owner_email="owner-ay@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="manager-az@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="reception-az@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="accountant-az@example.test", role_key="ACCOUNTANT")
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    roles = _roles(payload)

    platform_denied = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["RECEPTIONIST"]),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {platform_access}",
    )
    assert platform_denied.status_code == 401
    assert client.get("/api/v1/staff/", **reception_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/staff/invitations/", {}, format="json", **reception_headers
        ).status_code
        == 403
    )
    assert client.get("/api/v1/roles/", **accountant_headers).status_code == 200
    assert client.get("/api/v1/staff/", **accountant_headers).status_code == 403
    role_rows = client.get("/api/v1/roles/", **owner_headers)
    assert role_rows.status_code == 200
    listed = role_rows.json()["data"]
    assert "OWNER" in {row["key"] for row in listed}
    assert role_rows.json()["meta"] == {"has_more": False, "next_after": None}
    assert {
        "id",
        "key",
        "name",
        "description",
        "is_active",
        "is_protected",
        "is_template",
        "permission_codes",
        "permission_count",
        "assignment_count",
        "created_at",
        "updated_at",
    } <= set(listed[0])
    assert all(row["is_protected"] for row in listed)

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        delegated = Role.objects.create(
            gym=gym,
            key="FLOOR",
            name="Floor",
            created_by_membership_id=owner.id,
        )
        approver = Role.objects.create(
            gym=gym,
            key="APPROVER",
            name="Approver",
            created_by_membership_id=owner.id,
        )
        with pytest.raises(DatabaseError, match=r"settings\.manage"):
            with transaction.atomic():
                RolePermission.objects.create(
                    gym=gym,
                    role=delegated,
                    permission=Permission.objects.get(code="settings.manage"),
                    created_by_membership_id=owner.id,
                )
        with pytest.raises(DatabaseError, match="hard-reserved"):
            with transaction.atomic():
                RolePermission.objects.create(
                    gym=gym,
                    role=approver,
                    permission=Permission.objects.get(code="payments.approve"),
                    created_by_membership_id=owner.id,
                )
        inactive = Role.objects.create(
            gym=gym,
            key="OLD",
            name="Old",
            is_active=False,
            created_by_membership_id=owner.id,
        )
    listed_after = client.get("/api/v1/roles/", **owner_headers).json()["data"]
    old_role = next(row for row in listed_after if row["key"] == "OLD")
    assert old_role["is_active"] is False
    assert old_role["is_protected"] is False

    manager_invite = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["RECEPTIONIST"], email="front-desk@example.test", staff_code="FD-01"),
        format="json",
        **manager_headers,
    )
    assert manager_invite.status_code == 201, manager_invite.json()
    manager_escalation = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["MANAGER"], email="boss@example.test", staff_code="BOSS-01"),
        format="json",
        **manager_headers,
    )
    assert manager_escalation.status_code == 403
    assert manager_escalation.json()["error"]["code"] == "DELEGATION_DENIED"
    owner_manager = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["MANAGER"], email="lead@example.test", staff_code="LEAD-01"),
        format="json",
        **owner_headers,
    )
    assert owner_manager.status_code == 201, owner_manager.json()
    unavailable = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["OWNER"], email="pretend-owner@example.test", staff_code="OWN-99"),
        format="json",
        **owner_headers,
    )
    assert unavailable.status_code == 422
    assert unavailable.json()["error"]["code"] == "STAFF_INVITATION_INVALID"
    inactive_denied = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(str(inactive.id), email="old-role@example.test", staff_code="OLD-01"),
        format="json",
        **owner_headers,
    )
    assert inactive_denied.status_code == 422
    foreign_role = _roles(other)["RECEPTIONIST"]
    foreign = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(foreign_role, email="foreign-role@example.test", staff_code="FOR-01"),
        format="json",
        **owner_headers,
    )
    assert foreign.status_code == 422

    forged = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(
            roles["RECEPTIONIST"],
            email="forged@example.test",
            staff_code="FG-01",
            is_owner=True,
            gym_id=str(other_gym.id),
            status="ACTIVE",
            authz_version=9,
        ),
        format="json",
        **owner_headers,
    )
    assert forged.status_code == 400
    assert "is_owner" in forged.json()["error"]["fields"]
    staff_id = manager_invite.json()["data"]["staff"]["id"]
    before = client.get(f"/api/v1/staff/{staff_id}/", **owner_headers).json()["data"]
    patched = client.patch(
        f"/api/v1/staff/{staff_id}/",
        {
            "display_name": "Front Desk",
            "default_commission_percent": "0.00",
            "status": "TERMINATED",
            "is_owner": True,
            "role_ids": [roles["MANAGER"]],
            "gym_id": str(other_gym.id),
            "authz_version": 4,
        },
        format="json",
        **manager_headers,
    )
    assert patched.status_code == 400
    unchanged = client.get(f"/api/v1/staff/{staff_id}/", **owner_headers).json()["data"]
    assert unchanged["display_name"] == before["display_name"]
    assert unchanged["membership"]["authz_version"] == before["membership"]["authz_version"]
    updated = client.patch(
        f"/api/v1/staff/{staff_id}/",
        {"display_name": "Front Desk", "employment_title": "Desk"},
        format="json",
        **manager_headers,
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["display_name"] == "Front Desk"
    assert updated.json()["data"]["membership"]["status"] == before["membership"]["status"]
    assert updated.json()["data"]["role_ids"] == before["role_ids"]
    duplicate_code = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["RECEPTIONIST"], email="another@example.test", staff_code="FD-01"),
        format="json",
        **owner_headers,
    )
    assert duplicate_code.status_code == 409
    duplicate_email = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(roles["RECEPTIONIST"], email="front-desk@example.test", staff_code="FD-02"),
        format="json",
        **owner_headers,
    )
    assert duplicate_email.status_code == 409

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        owner_profile = StaffProfile.objects.create(
            gym=gym,
            gym_membership=owner,
            staff_code="OWNER-PROFILE",
            display_name="Owner Profile",
            joined_date=date.today(),
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
        )
    assert client.get(f"/api/v1/staff/{owner_profile.id}/", **manager_headers).status_code == 404
    assert (
        client.post(
            f"/api/v1/staff/{owner_profile.id}/suspend/",
            {"reason": "Cannot suspend owner"},
            format="json",
            **manager_headers,
        ).status_code
        == 404
    )
    assert GymMembership.objects.get(id=owner.id).status == GymMembership.Status.ACTIVE


@override_settings(**jwt_settings())
def test_staff_invitation_replay_expiry_concurrency_and_rollback() -> None:
    client, platform_access = platform_token("staff-race-platform@example.test")
    payload = provision(client, platform_access, code="STF-RC", owner_email="owner-rc@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    role_id = _roles(payload)["TRAINER"]
    mail.outbox.clear()
    invited = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(role_id, email="replay@example.test", staff_code="RP-01"),
        format="json",
        **headers,
    )
    assert invited.status_code == 201, invited.json()
    token = _token_from_mail()
    body = {
        "invitation_token": token,
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
    }
    assert client.post("/api/v1/staff/invitations/accept/", body, format="json").status_code == 200
    replay = client.post("/api/v1/staff/invitations/accept/", body, format="json")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "INVALID_INVITATION"
    assert StaffInvitation.objects.get(id=invited.json()["data"]["id"]).status == "ACCEPTED"

    mail.outbox.clear()
    expired_invite = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(role_id, email="expired@example.test", staff_code="EX-01"),
        format="json",
        **headers,
    )
    assert expired_invite.status_code == 201
    expired = StaffInvitation.objects.get(id=expired_invite.json()["data"]["id"])
    expired.expires_at = timezone.now() - timedelta(seconds=1)
    expired.save(update_fields=["expires_at", "updated_at"])
    expired_token = _token_from_mail()
    expired_accept = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": expired_token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert expired_accept.status_code == 401
    expired.refresh_from_db()
    assert expired.status == StaffInvitation.Status.PENDING

    existing = User.objects.create_user("existing.staff@example.test", PASSWORD, first_name="Kept")
    mail.outbox.clear()
    linked = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(
            role_id,
            email="existing.staff@example.test",
            first_name="Changed",
            staff_code="EXS-01",
        ),
        format="json",
        **headers,
    )
    fresh = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(role_id, email="brand.new@example.test", staff_code="NEW-01"),
        format="json",
        **headers,
    )
    assert linked.status_code == 201
    assert fresh.status_code == 201
    assert set(linked.json()["data"]) == set(fresh.json()["data"])
    existing.refresh_from_db()
    assert existing.first_name == "Kept"
    linked_token = mail.outbox[-2].body.split("token=", 1)[1].split()[0]
    fresh_token = mail.outbox[-1].body.split("token=", 1)[1].split()[0]
    wrong = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": linked_token,
            "password": "another correct horse battery",
            "password_confirmation": "another correct horse battery",
        },
        format="json",
    )
    assert wrong.status_code == 401
    assert (
        StaffInvitation.objects.get(normalized_email="existing.staff@example.test").status
        == StaffInvitation.Status.PENDING
    )
    correct = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": linked_token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert correct.status_code == 200, correct.json()
    assert existing.check_password(PASSWORD)

    admin = User.objects.create_superuser("platform-staff-target@example.test", PASSWORD)
    denied_admin = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(role_id, email=admin.email, staff_code="PLT-01"),
        format="json",
        **headers,
    )
    assert denied_admin.status_code == 409
    assert denied_admin.json()["error"]["code"] == "STAFF_WORKFLOW_CONFLICT"
    assert "platform" not in denied_admin.json()["error"]["message"].lower()

    mail.outbox.clear()
    with patch("apps.tenants.staff_services.send_mail", side_effect=OSError("smtp down")):
        failed = client.post(
            "/api/v1/staff/invitations/",
            _invite_body(role_id, email="rollback@example.test", staff_code="RB-01"),
            format="json",
            **headers,
        )
    assert failed.status_code == 500
    assert not StaffInvitation.objects.filter(normalized_email="rollback@example.test").exists()
    assert not StaffProfile.objects.filter(staff_code="RB-01", gym=gym).exists()
    assert mail.outbox == []

    gym.status = Gym.Status.SUSPENDED
    gym.save(update_fields=["status", "updated_at"])
    suspended_gym = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": fresh_token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert suspended_gym.status_code == 409
    assert (
        StaffInvitation.objects.get(normalized_email="brand.new@example.test").status
        == StaffInvitation.Status.PENDING
    )
    assert not User.objects.get(normalized_email="brand.new@example.test").has_usable_password()
    gym.status = Gym.Status.ACTIVE
    gym.save(update_fields=["status", "updated_at"])
    mail.outbox.clear()
    pending = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(role_id, email="race@example.test", staff_code="RACE-01"),
        format="json",
        **headers,
    )
    assert pending.status_code == 201
    race_token = _token_from_mail()
    barrier = Barrier(2)

    def accept_once() -> str:
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            accept_staff_invitation(
                plaintext_token=race_token,
                password=PASSWORD,
                confirmation={},
                request_id="req_staff_race",
            )
            return "accepted"
        except InvalidInvitation:
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(lambda _: accept_once(), range(2)))
    assert results == ["accepted", "rejected"]
    race_invitation = StaffInvitation.objects.get(normalized_email="race@example.test")
    assert race_invitation.status == StaffInvitation.Status.ACCEPTED
    assert (
        GymMembershipRole.objects.filter(
            gym_membership_id=race_invitation.gym_membership_id
        ).count()
        == 1
    )

    barrier = Barrier(2)

    def invite_once() -> str:
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            invite_staff(
                gym_id=gym.id,
                actor=owner.user,
                actor_membership_id=owner.id,
                staff_data={
                    "email": "duplicate-race@example.test",
                    "first_name": "Race",
                    "last_name": "Invite",
                    "staff_code": f"DUP-{uuid4().hex[:6]}",
                    "display_name": "Race Invite",
                    "joined_date": date(2026, 10, 2),
                },
                role_ids=[UUID(role_id)],
                request_id="req_staff_duplicate",
            )
            return "created"
        except StaffWorkflowConflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        invite_results = sorted(executor.map(lambda _: invite_once(), range(2)))
    assert invite_results == ["conflict", "created"]
    assert (
        StaffInvitation.objects.filter(
            gym=gym,
            normalized_email="duplicate-race@example.test",
            status=StaffInvitation.Status.PENDING,
        ).count()
        == 1
    )


@override_settings(**jwt_settings())
def test_staff_records_constraints_rls_and_invitation_boundaries() -> None:
    client, platform_access = platform_token("staff-rls-platform@example.test")
    payload = provision(client, platform_access, code="STF-RL", owner_email="owner-rl@example.test")
    other = provision(client, platform_access, code="STF-RM", owner_email="owner-rm@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    gym_b = Gym.objects.get(id=other["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    mail.outbox.clear()
    invited = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(_roles(payload)["TRAINER"], email="boundary@example.test", staff_code="BD-01"),
        format="json",
        **headers,
    )
    assert invited.status_code == 201
    staff_plain = _token_from_mail()
    invitation = StaffInvitation.objects.get(id=invited.json()["data"]["id"])
    invitation_role = StaffInvitationRole.objects.get(staff_invitation=invitation)
    owner_invitation, _created = invite_owner(
        gym_id=gym.id,
        actor=owner.user,
        actor_membership_id=owner.id,
        owner_data={
            "email": "extra-owner@example.test",
            "first_name": "Extra",
            "last_name": "Owner",
        },
        request_id="req_owner_boundary",
    )
    owner_plain = _token_from_mail()
    staff_on_owner = client.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": staff_plain,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    owner_on_staff = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": owner_plain,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert staff_on_owner.status_code == 401
    assert owner_on_staff.status_code == 401
    invitation.refresh_from_db()
    owner_invitation.refresh_from_db()
    assert invitation.status == StaffInvitation.Status.PENDING
    assert owner_invitation.status == StaffInvitation.Status.PENDING
    staff_hash = _hash_invitation_token(staff_plain)
    owner_hash = _hash_invitation_token(owner_plain)
    with connection.cursor() as cursor:
        cursor.execute("SELECT invitation_id FROM app_owner_invitation(%s)", [staff_hash])
        assert cursor.fetchone() is None
        cursor.execute("SELECT invitation_id FROM app_staff_invitation(%s)", [owner_hash])
        assert cursor.fetchone() is None
        cursor.execute("SELECT invitation_id FROM app_staff_invitation(%s)", [staff_hash])
        assert cursor.fetchone()[0] == invitation.id

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            invitation.token_hash = "c" * 64
            invitation.save(update_fields=["token_hash", "updated_at"])
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            invitation_role.delete()
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            StaffInvitationRole.objects.create(
                gym=gym,
                staff_invitation=invitation,
                role=Role.objects.get(gym=gym, key="OWNER"),
            )

    other_membership = GymMembership.objects.select_related("user").get(
        id=other["owner"]["gym_membership"]["id"]
    )

    def new_membership() -> GymMembership:
        user = User.objects.create_user(f"{uuid4()}@example.test", PASSWORD)
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            return GymMembership.objects.create(
                gym=gym,
                user=user,
                status=GymMembership.Status.INVITED,
                is_owner=False,
                joined_date=date(2026, 10, 1),
                invited_at=timezone.now(),
            )

    def create_profile(**overrides: object) -> None:
        values: dict[str, object] = {
            "gym": gym,
            "gym_membership": new_membership(),
            "staff_code": f"TMP-{uuid4().hex[:8]}",
            "display_name": "Temporary",
            "joined_date": date(2026, 10, 1),
            "default_commission_percent": Decimal("100.00"),
            "created_by_membership_id": owner.id,
            "updated_by_membership_id": owner.id,
        }
        values.update(overrides)
        with transaction.atomic():
            set_gym_context(gym_id=values["gym"].id)  # type: ignore[attr-defined]
            StaffProfile.objects.create(**values)

    with pytest.raises(IntegrityError):
        create_profile(default_commission_percent=Decimal("100.01"))
    with pytest.raises(IntegrityError):
        create_profile(default_commission_percent=Decimal("-0.01"))
    with pytest.raises(IntegrityError):
        create_profile(staff_code="")
    with pytest.raises(IntegrityError):
        create_profile(gym=gym, gym_membership=other_membership, staff_code="CROSS-01")
    with pytest.raises(IntegrityError):
        create_profile(
            status=StaffProfile.Status.TERMINATED, terminated_date=None, staff_code="TERM-01"
        )
    with pytest.raises(IntegrityError):
        create_profile(
            status=StaffProfile.Status.TERMINATED,
            terminated_date=date(2026, 9, 1),
            staff_code="TERM-02",
        )
    with pytest.raises(IntegrityError):
        create_profile(status=StaffProfile.Status.ARCHIVED, archived_at=None, staff_code="ARC-01")

    profile_id = invited.json()["data"]["staff"]["id"]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT count(*) FROM tenants_staff_profile WHERE id = %s",
                [profile_id],
            )
            assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tenants_staff_profile (
                      id, gym_id, gym_membership_id, staff_code, display_name, phone,
                      joined_date, status, default_commission_percent, employment_title,
                      created_at, updated_at
                    ) VALUES (
                      gen_random_uuid(), %s, %s, 'FORGED', 'Forged', '', CURRENT_DATE,
                      'ACTIVE', 0, '', now(), now()
                    )
                    """,
                    [gym.id, invitation.gym_membership_id],
                )


@override_settings(**jwt_settings())
def test_staff_list_cursor_pages_duplicate_names_and_rejects_bad_cursors() -> None:
    client, platform_access = platform_token("staff-page-platform@example.test")
    payload = provision(
        client, platform_access, code="STF-PG", owner_email="owner-page@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    role_id = _roles(payload)["RECEPTIONIST"]
    for index in range(3):
        invited = client.post(
            "/api/v1/staff/invitations/",
            _invite_body(
                role_id,
                email=f"alex-{index}@example.test",
                staff_code=f"AX-{index}",
                display_name="Alex Shared",
                first_name="Alex",
                last_name=f"Shared{index}",
            ),
            format="json",
            **headers,
        )
        assert invited.status_code == 201, invited.json()

    first = client.get("/api/v1/staff/", {"limit": 2}, **headers)
    assert first.status_code == 200, first.json()
    first_body = first.json()
    assert first_body["meta"]["has_more"] is True
    cursor = first_body["meta"]["next_after"]
    assert isinstance(cursor, str) and cursor
    assert [row["display_name"] for row in first_body["data"]] == ["Alex Shared", "Alex Shared"]

    second = client.get("/api/v1/staff/", {"limit": 2, "after": cursor}, **headers)
    assert second.status_code == 200, second.json()
    second_body = second.json()
    assert second_body["meta"] == {"has_more": False, "next_after": None}
    assert [row["display_name"] for row in second_body["data"]] == ["Alex Shared"]
    listed_ids = [row["id"] for row in first_body["data"] + second_body["data"]]
    assert len(listed_ids) == len(set(listed_ids)) == 3
    expected_ids = [
        str(staff_id)
        for staff_id in StaffProfile.objects.filter(
            gym=gym,
            gym_membership__is_owner=False,
        )
        .order_by("display_name", "id")
        .values_list("id", flat=True)
    ]
    assert listed_ids == expected_ids
    assert first_body["data"][1]["id"] != second_body["data"][0]["id"]

    substituted = client.get(
        "/api/v1/staff/",
        {"limit": 2, "after": cursor, "search": "nobody"},
        **headers,
    )
    status_substituted = client.get(
        "/api/v1/staff/",
        {"limit": 2, "after": cursor, "membership_status": "SUSPENDED"},
        **headers,
    )
    tampered = f"{cursor[:-1]}A" if not cursor.endswith("A") else f"{cursor[:-1]}B"
    forged = client.get("/api/v1/staff/", {"limit": 2, "after": tampered}, **headers)
    assert substituted.status_code == status_substituted.status_code == forged.status_code == 400
    for response in (substituted, status_substituted, forged):
        assert "Invalid cursor." in response.json()["error"]["fields"]["after"]
