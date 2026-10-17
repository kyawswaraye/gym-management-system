from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.apps import apps as django_apps
from django.core import mail
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.delegation import HARD_RESERVED_APPROVALS
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
    StaffInvitation,
    SystemRoleTemplate,
    SystemRoleTemplatePermission,
)
from apps.tenants.role_services import replace_role_permissions
from apps.users.models import User
from tests.security.test_staff_lifecycle import _invite_body, _token_from_mail
from tests.security.test_tenant_gym_settings import (
    PASSWORD,
    add_staff,
    auth_headers,
    ensure_authorization_catalog,
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


def _roles(payload: dict[str, object]) -> dict[str, str]:
    return {row["key"]: row["id"] for row in payload["roles"]}  # type: ignore[index]


@override_settings(**jwt_settings())
def test_role_permissions_delegation_and_invalidation() -> None:
    client, platform_access = platform_token("roles-platform@example.test")
    payload = provision(client, platform_access, code="ROL-A", owner_email="owner-rol@example.test")
    other = provision(client, platform_access, code="ROL-B", owner_email="owner-rol-b@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym, email="manager-rol@example.test", role_key="MANAGER")
    peer = add_staff(gym, email="peer-rol@example.test", role_key="MANAGER")
    receptionist = add_staff(gym, email="reception-rol@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="accountant-rol@example.test", role_key="ACCOUNTANT")
    owner_headers = auth_headers(staff_token(owner))
    manager_headers = auth_headers(staff_token(manager))
    other_headers = auth_headers(staff_token(other_owner))
    accountant_headers = auth_headers(staff_token(accountant))
    reception_headers = auth_headers(staff_token(receptionist))
    roles = _roles(payload)
    stale_reception = staff_token(receptionist)
    before_version = receptionist.authz_version

    assert client.get("/api/v1/roles/", **reception_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/roles/",
            {"key": "FLOOR", "name": "Floor"},
            format="json",
            **accountant_headers,
        ).status_code
        == 403
    )
    catalog = client.get("/api/v1/permissions/?limit=100", **accountant_headers)
    assert catalog.status_code == 200, catalog.json()
    codes = [row["code"] for row in catalog.json()["data"]]
    assert codes
    assert all(not code.startswith("platform.") for code in codes)
    assert "members.view" in codes

    forged = client.post(
        "/api/v1/roles/",
        {
            "key": "FLOOR",
            "name": "Floor",
            "gym_id": str(other_gym.id),
            "is_owner": True,
            "source_template_id": str(uuid4()),
            "permission_codes": ["payments.approve"],
            "created_by_membership_id": str(owner.id),
        },
        format="json",
        **owner_headers,
    )
    assert forged.status_code == 400
    assert "gym_id" in forged.json()["error"]["fields"]
    created = client.post(
        "/api/v1/roles/",
        {"key": "FLOOR", "name": "Floor", "description": "Desk"},
        format="json",
        **manager_headers,
    )
    assert created.status_code == 201, created.json()
    role_id = created.json()["data"]["id"]
    assert created.json()["data"]["is_protected"] is False
    assert created.json()["data"]["permission_codes"] == []
    assert created.json()["data"]["created_by_membership_id"] == str(manager.id)
    assert AuditLog.objects.filter(action="ROLE_CREATED", target_id=role_id).exists()

    hidden = client.get(f"/api/v1/roles/{role_id}/", **other_headers)
    assert hidden.status_code == 404
    detail = client.get(f"/api/v1/roles/{role_id}/", **accountant_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["key"] == "FLOOR"

    denied_codes = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["payments.approve"]},
        format="json",
        **manager_headers,
    )
    assert denied_codes.status_code == 403
    assert denied_codes.json()["error"]["code"] == "DELEGATION_DENIED"
    settings_denied = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["settings.manage"]},
        format="json",
        **owner_headers,
    )
    assert settings_denied.status_code == 403
    platform_denied = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["platform.gyms.view"]},
        format="json",
        **owner_headers,
    )
    assert platform_denied.status_code == 422
    assert platform_denied.json()["error"]["code"] == "ROLE_PERMISSION_INVALID"
    retired_platform = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["platform.settings.manage"]},
        format="json",
        **owner_headers,
    )
    assert retired_platform.status_code == 422
    assert retired_platform.json()["error"]["code"] == "ROLE_PERMISSION_INVALID"
    unknown = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["not.a.permission"]},
        format="json",
        **owner_headers,
    )
    assert unknown.status_code == 422
    duplicate = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["members.view", "members.view"]},
        format="json",
        **owner_headers,
    )
    assert duplicate.status_code == 400
    granted = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["members.view"]},
        format="json",
        **manager_headers,
    )
    assert granted.status_code == 200, granted.json()
    assert granted.json()["data"]["permission_codes"] == ["members.view"]
    assert AuditLog.objects.filter(action="ROLE_PERMISSIONS_REPLACED", target_id=role_id).exists()

    protected = client.patch(
        f"/api/v1/roles/{roles['MANAGER']}/",
        {"name": "Boss"},
        format="json",
        **manager_headers,
    )
    assert protected.status_code == 403
    owner_rename = client.patch(
        f"/api/v1/roles/{roles['MANAGER']}/",
        {"name": "Boss"},
        format="json",
        **owner_headers,
    )
    assert owner_rename.status_code == 409
    owner_permissions = client.get(f"/api/v1/roles/{roles['OWNER']}/", **owner_headers).json()[
        "data"
    ]["permission_codes"]
    owner_change = client.put(
        f"/api/v1/roles/{roles['OWNER']}/permissions/",
        {"permission_codes": owner_permissions},
        format="json",
        **owner_headers,
    )
    assert owner_change.status_code == 409
    manager_permissions = client.get(f"/api/v1/roles/{roles['MANAGER']}/", **owner_headers).json()[
        "data"
    ]["permission_codes"]
    assert HARD_RESERVED_APPROVALS <= set(manager_permissions)
    without_approval = [code for code in manager_permissions if code != "payments.approve"]
    missing_approval = client.put(
        f"/api/v1/roles/{roles['MANAGER']}/permissions/",
        {"permission_codes": without_approval},
        format="json",
        **owner_headers,
    )
    assert missing_approval.status_code == 403
    manager_archive = client.post(
        f"/api/v1/roles/{roles['RECEPTIONIST']}/archive/", **manager_headers
    )
    assert manager_archive.status_code == 403
    owner_archive = client.post(
        f"/api/v1/roles/{roles['RECEPTIONIST']}/archive/", format="json", **owner_headers
    )
    assert owner_archive.status_code == 409

    peer_denied = client.put(
        f"/api/v1/gym-memberships/{peer.id}/roles/",
        {"role_ids": [role_id]},
        format="json",
        **manager_headers,
    )
    assert peer_denied.status_code == 403
    owner_target = client.put(
        f"/api/v1/gym-memberships/{owner.id}/roles/",
        {"role_ids": [role_id]},
        format="json",
        **owner_headers,
    )
    assert owner_target.status_code == 404
    foreign = client.put(
        f"/api/v1/gym-memberships/{receptionist.id}/roles/",
        {"role_ids": [_roles(other)["RECEPTIONIST"]]},
        format="json",
        **owner_headers,
    )
    assert foreign.status_code == 422
    owner_role = client.put(
        f"/api/v1/gym-memberships/{receptionist.id}/roles/",
        {"role_ids": [roles["OWNER"]]},
        format="json",
        **owner_headers,
    )
    assert owner_role.status_code == 422
    assigned = client.put(
        f"/api/v1/gym-memberships/{receptionist.id}/roles/",
        {"role_ids": [role_id]},
        format="json",
        **manager_headers,
    )
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["data"]["authz_version"] == before_version + 1
    assert assigned.json()["data"]["roles"][0]["id"] == role_id
    assert client.get("/api/v1/gym/", **auth_headers(stale_reception)).status_code == 401
    same = client.put(
        f"/api/v1/gym-memberships/{receptionist.id}/roles/",
        {"role_ids": [role_id]},
        format="json",
        **manager_headers,
    )
    assert same.status_code == 200
    assert same.json()["data"]["authz_version"] == before_version + 1
    narrowed = [code for code in manager_permissions if code != "reports.export"]
    narrowed_response = client.put(
        f"/api/v1/roles/{roles['MANAGER']}/permissions/",
        {"permission_codes": narrowed},
        format="json",
        **owner_headers,
    )
    assert narrowed_response.status_code == 200, narrowed_response.json()
    outside = client.put(
        f"/api/v1/roles/{role_id}/permissions/",
        {"permission_codes": ["reports.export"]},
        format="json",
        **owner_headers,
    )
    assert outside.status_code == 403

    archived = client.post(f"/api/v1/roles/{role_id}/archive/", format="json", **owner_headers)
    assert archived.status_code == 200, archived.json()
    assert archived.json()["data"]["is_active"] is False
    inactive = client.put(
        f"/api/v1/gym-memberships/{receptionist.id}/roles/",
        {"role_ids": [role_id]},
        format="json",
        **owner_headers,
    )
    assert inactive.status_code == 422
    mail.outbox.clear()
    invite_role = client.post(
        "/api/v1/roles/",
        {"key": "GUIDE", "name": "Guide"},
        format="json",
        **owner_headers,
    )
    assert invite_role.status_code == 201, invite_role.json()
    guide_id = invite_role.json()["data"]["id"]
    client.put(
        f"/api/v1/roles/{guide_id}/permissions/",
        {"permission_codes": ["members.view"]},
        format="json",
        **owner_headers,
    )
    invited = client.post(
        "/api/v1/staff/invitations/",
        _invite_body(guide_id, email="guide@example.test", staff_code="GD-01"),
        format="json",
        **owner_headers,
    )
    assert invited.status_code == 201, invited.json()
    token = _token_from_mail()
    client.post(f"/api/v1/roles/{guide_id}/archive/", format="json", **owner_headers)
    accepted = client.post(
        "/api/v1/staff/invitations/accept/",
        {
            "invitation_token": token,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert accepted.status_code == 401
    invitation = StaffInvitation.objects.get(id=invited.json()["data"]["id"])
    assert invitation.status == StaffInvitation.Status.PENDING

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            rolled = client.post(
                "/api/v1/roles/",
                {"key": "TEMP", "name": "Temporary"},
                format="json",
                **owner_headers,
            )
            assert rolled.status_code == 201, rolled.json()
            raise RuntimeError("rollback")
    assert not Role.objects.filter(gym=gym, key="TEMP").exists()
    assert not AuditLog.objects.filter(
        gym=gym, action="ROLE_CREATED", new_values__key="TEMP"
    ).exists()


@override_settings(**jwt_settings())
def test_role_permission_and_assignment_versions_are_race_safe() -> None:
    client, platform_access = platform_token("roles-race-platform@example.test")
    payload = provision(
        client, platform_access, code="ROL-R", owner_email="owner-race@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    created = client.post(
        "/api/v1/roles/",
        {"key": "FLOOR", "name": "Floor"},
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.json()
    role_id = UUID(created.json()["data"]["id"])
    assert (
        client.put(
            f"/api/v1/roles/{role_id}/permissions/",
            {"permission_codes": ["members.view"]},
            format="json",
            **headers,
        ).status_code
        == 200
    )
    holder = add_staff(gym, email="holder-rol@example.test", role_key="FLOOR")
    start = holder.authz_version
    barrier = Barrier(2)

    def replace(codes: list[str]) -> str:
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            replace_role_permissions(
                gym_id=gym.id,
                role_id=role_id,
                actor=owner.user,
                actor_membership_id=owner.id,
                expected_authz_version=owner.authz_version,
                permission_codes=codes,
                request_id="req_role_race",
            )
            return "replaced"
        except Exception as exc:
            return exc.__class__.__name__
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(
            executor.map(
                replace,
                (["members.create"], ["staff.view"]),
            )
        )
    assert results == ["replaced", "replaced"], results
    holder.refresh_from_db()
    assert holder.authz_version == start + 2
    final_codes = set(
        RolePermission.objects.filter(role_id=role_id).values_list("permission__code", flat=True)
    )
    assert final_codes in ({"members.create"}, {"staff.view"})


def test_role_rls_triggers_grants_and_constraints() -> None:
    ensure_authorization_catalog()
    user = User.objects.create_user("role-constraint@example.test", PASSWORD)
    other_user = User.objects.create_user("role-constraint-b@example.test", PASSWORD)
    gym_a = Gym.objects.create(code="ROL-C", name="Role A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="ROL-D", name="Role B", status=Gym.Status.ACTIVE)
    template = SystemRoleTemplate.objects.get(key="OWNER")
    permission = Permission.objects.get(code="members.view")
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        membership_a = GymMembership.objects.create(
            gym=gym_a,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 1),
        )
        staff = GymMembership.objects.create(
            gym=gym_a,
            user=other_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
        )
        owner_role = Role.objects.create(
            gym=gym_a,
            key="OWNER",
            name="Owner",
            source_template=template,
            source_template_version=template.version,
            created_by_membership_id=membership_a.id,
        )
        RolePermission.objects.create(
            gym=gym_a,
            role=owner_role,
            permission=permission,
            created_by_membership_id=membership_a.id,
        )
        GymMembershipRole.objects.create(
            gym=gym_a,
            gym_membership=membership_a,
            role=owner_role,
            created_by_membership_id=membership_a.id,
        )
        custom = Role.objects.create(
            gym=gym_a,
            key="FLOOR",
            name="Floor",
            created_by_membership_id=membership_a.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        membership_b = GymMembership.objects.create(
            gym=gym_b,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
        )
        role_b = Role.objects.create(gym=gym_b, key="FLOOR", name="Other Floor")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            Role.objects.create(
                gym=gym_a,
                key="GUIDE",
                name="Floor",
                created_by_membership_id=membership_a.id,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            Role.objects.create(
                gym=gym_a,
                key="GUIDE",
                name="Guide",
                source_template=template,
                created_by_membership_id=membership_a.id,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            RolePermission.objects.create(
                gym=gym_a,
                role=role_b,
                permission=permission,
                created_by_membership_id=membership_a.id,
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            Role.objects.create(
                gym=gym_a,
                key="GUIDE",
                name="Guide",
                created_by_membership_id=membership_b.id,
            )

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        with pytest.raises(DatabaseError, match="role_guard"):
            with transaction.atomic():
                owner_role.name = "Renamed"
                owner_role.save(update_fields=["name", "updated_at"])
        with pytest.raises(DatabaseError, match="role_guard"):
            with transaction.atomic():
                RolePermission.objects.create(
                    gym=gym_a,
                    role=owner_role,
                    permission=Permission.objects.get(code="platform.gyms.view"),
                    created_by_membership_id=membership_a.id,
                )
        with pytest.raises(DatabaseError, match="role_guard"):
            with transaction.atomic():
                GymMembershipRole.objects.create(
                    gym=gym_a,
                    gym_membership=staff,
                    role=owner_role,
                    created_by_membership_id=membership_a.id,
                )
        with pytest.raises(DatabaseError, match="role_guard"):
            with transaction.atomic():
                GymMembershipRole.objects.filter(
                    gym_membership=membership_a, role=owner_role
                ).delete()

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        custom.is_active = False
        custom.save(update_fields=["is_active", "updated_at"])
        Role.objects.create(
            gym=gym_a,
            key="GUIDE",
            name="Floor",
            created_by_membership_id=membership_a.id,
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'tenants_role', 'SELECT'),
              has_table_privilege('gym_runtime', 'tenants_role', 'INSERT'),
              has_table_privilege('gym_runtime', 'tenants_role', 'UPDATE'),
              has_table_privilege('gym_runtime', 'tenants_role', 'DELETE'),
              has_table_privilege('gym_runtime', 'tenants_role_permission', 'DELETE'),
              has_table_privilege('gym_runtime', 'tenants_gym_membership_role', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False, True, True)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM tenants_role WHERE id = %s", [str(owner_role.id)])
        assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                cursor.execute(
                    """
                INSERT INTO tenants_role (
                  id, gym_id, key, name, normalized_name, description, is_active,
                  created_at, updated_at
                ) VALUES (
                  gen_random_uuid(), %s, 'FORGED', 'Forged', 'FORGED', '', true, now(), now()
                )
                """,
                    [str(gym_a.id)],
                )


def _retail_grants(role: Role) -> int:
    return RolePermission.objects.filter(role=role, permission__code="retail.approve").count()


def test_retail_approve_backfill_is_the_clean_install_baseline() -> None:
    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    backfill = importlib.import_module("apps.tenants.migrations.0016_manager_retail_approve")
    seed.seed(django_apps, None)
    assert "retail.approve" not in seed.PERMISSION_CODES
    assert not SystemRoleTemplatePermission.objects.filter(
        permission__code="retail.approve"
    ).exists()

    user = User.objects.create_user("retail-backfill@example.test", PASSWORD)
    gym = Gym.objects.create(code="RET-A", name="Retail A", status=Gym.Status.ACTIVE)
    other = Gym.objects.create(code="RET-B", name="Retail B", status=Gym.Status.ACTIVE)
    owner_template = SystemRoleTemplate.objects.get(key="OWNER")
    manager_template = SystemRoleTemplate.objects.get(key="MANAGER")
    receptionist_template = SystemRoleTemplate.objects.get(key="RECEPTIONIST")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 1),
        )
        owner_role = Role.objects.create(
            gym=gym,
            key="OWNER",
            name="Owner",
            source_template=owner_template,
            source_template_version=owner_template.version,
            created_by_membership_id=membership.id,
        )
        manager_role = Role.objects.create(
            gym=gym,
            key="MANAGER",
            name="Manager",
            source_template=manager_template,
            source_template_version=manager_template.version,
            created_by_membership_id=membership.id,
        )
        receptionist_role = Role.objects.create(
            gym=gym,
            key="RECEPTIONIST",
            name="Receptionist",
            source_template=receptionist_template,
            source_template_version=receptionist_template.version,
            created_by_membership_id=membership.id,
        )
        custom = Role.objects.create(
            gym=gym,
            key="LEAD",
            name="Desk",
            created_by_membership_id=membership.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        GymMembership.objects.create(
            gym=other,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
        )
        named_manager = Role.objects.create(gym=other, key="MANAGER", name="Manager")

    backfill.grant_manager_retail_approve(django_apps, None)
    assert Permission.objects.filter(code="retail.approve").count() == 1
    assert (
        SystemRoleTemplatePermission.objects.filter(
            system_role_template=owner_template, permission__code="retail.approve"
        ).count()
        == 1
    )
    assert (
        SystemRoleTemplatePermission.objects.filter(
            system_role_template=manager_template, permission__code="retail.approve"
        ).count()
        == 1
    )
    assert not SystemRoleTemplatePermission.objects.filter(
        system_role_template=receptionist_template, permission__code="retail.approve"
    ).exists()
    assert _retail_grants(owner_role) == 1
    assert _retail_grants(manager_role) == 1
    assert _retail_grants(receptionist_role) == 0
    assert _retail_grants(custom) == 0
    assert _retail_grants(named_manager) == 0

    backfill.grant_manager_retail_approve(django_apps, None)
    assert Permission.objects.filter(code="retail.approve").count() == 1
    assert _retail_grants(owner_role) == 1
    assert _retail_grants(manager_role) == 1
    assert (
        SystemRoleTemplatePermission.objects.filter(permission__code="retail.approve").count() == 2
    )


def _insert_role_permission(cursor, gym_id: object, role_id: object, permission_id: object) -> None:
    cursor.execute(
        """
        INSERT INTO tenants_role_permission (
          id, gym_id, role_id, permission_id, created_at, updated_at
        ) VALUES (gen_random_uuid(), %s, %s, %s, now(), now())
        """,
        [str(gym_id), str(role_id), str(permission_id)],
    )


def test_runtime_role_permission_trigger_blocks_escalation() -> None:
    ensure_authorization_catalog()
    user = User.objects.create_user("role-trigger@example.test", PASSWORD)
    gym = Gym.objects.create(code="TRG-A", name="Trigger A", status=Gym.Status.ACTIVE)
    other = Gym.objects.create(code="TRG-B", name="Trigger B", status=Gym.Status.ACTIVE)
    owner_template = SystemRoleTemplate.objects.get(key="OWNER")
    manager_template = SystemRoleTemplate.objects.get(key="MANAGER")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 1),
        )
        owner_role = Role.objects.create(
            gym=gym,
            key="OWNER",
            name="Owner",
            source_template=owner_template,
            source_template_version=owner_template.version,
            created_by_membership_id=membership.id,
        )
        manager_role = Role.objects.create(
            gym=gym,
            key="MANAGER",
            name="Manager",
            source_template=manager_template,
            source_template_version=manager_template.version,
            created_by_membership_id=membership.id,
        )
        custom = Role.objects.create(
            gym=gym,
            key="FLOOR",
            name="Desk",
            created_by_membership_id=membership.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        GymMembership.objects.create(
            gym=other,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
        )
        fake_manager = Role.objects.create(gym=other, key="MANAGER", name="Manager")
    permissions = {
        row.code: row.id
        for row in Permission.objects.filter(
            code__in=[
                "members.view",
                "settings.manage",
                "payments.approve",
                "platform.gyms.view",
            ]
        )
    }

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        _insert_role_permission(cursor, gym.id, custom.id, permissions["members.view"])
        cursor.execute(
            """
            DELETE FROM tenants_role_permission
            WHERE gym_id = %s AND role_id = %s AND permission_id = %s
            """,
            [str(gym.id), str(custom.id), str(permissions["members.view"])],
        )
        assert cursor.rowcount == 1
        _insert_role_permission(cursor, gym.id, manager_role.id, permissions["payments.approve"])
        _insert_role_permission(cursor, gym.id, owner_role.id, permissions["settings.manage"])
        with pytest.raises(DatabaseError, match="reserved approvals"):
            with transaction.atomic():
                cursor.execute(
                    """
                    DELETE FROM tenants_role_permission
                    WHERE gym_id = %s AND role_id = %s AND permission_id = %s
                    """,
                    [str(gym.id), str(manager_role.id), str(permissions["payments.approve"])],
                )
        with pytest.raises(DatabaseError, match="immutable"):
            with transaction.atomic():
                cursor.execute(
                    """
                    DELETE FROM tenants_role_permission
                    WHERE gym_id = %s AND role_id = %s AND permission_id = %s
                    """,
                    [str(gym.id), str(owner_role.id), str(permissions["settings.manage"])],
                )
        for role_id, code, message in (
            (custom.id, "settings.manage", "settings.manage"),
            (manager_role.id, "settings.manage", "settings.manage"),
            (custom.id, "payments.approve", "hard-reserved"),
            (custom.id, "platform.gyms.view", "platform"),
            (owner_role.id, "platform.gyms.view", "platform"),
        ):
            with pytest.raises(DatabaseError, match=message):
                with transaction.atomic():
                    _insert_role_permission(cursor, gym.id, role_id, permissions[code])

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        with pytest.raises(DatabaseError, match="hard-reserved"):
            with transaction.atomic():
                _insert_role_permission(
                    cursor, other.id, fake_manager.id, permissions["payments.approve"]
                )

    assert not RolePermission.objects.filter(
        role=custom, permission_id=permissions["members.view"]
    ).exists()
    assert RolePermission.objects.filter(
        role=manager_role, permission_id=permissions["payments.approve"]
    ).exists()
    assert RolePermission.objects.filter(
        role=owner_role, permission_id=permissions["settings.manage"]
    ).exists()
    assert not RolePermission.objects.filter(
        role__in=[custom, manager_role, owner_role, fake_manager],
        permission_id=permissions["platform.gyms.view"],
    ).exists()
    assert not RolePermission.objects.filter(
        role__in=[custom, fake_manager], permission_id=permissions["payments.approve"]
    ).exists()
