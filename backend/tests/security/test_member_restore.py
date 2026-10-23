from __future__ import annotations

import os

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import restore_member
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
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


def _stored(gym_id: object, member_id: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return Member.objects.get(id=member_id)


def _audits(gym_id: object, member_id: str) -> list[AuditLog]:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return list(
            AuditLog.objects.filter(action="MEMBER_RESTORED", target_id=member_id).order_by(
                "occurred_at", "id"
            )
        )


def _restore(api: APIClient, member_id: str, headers: dict[str, str], body: object | None = None):
    return api.post(
        f"/api/v1/members/{member_id}/restore/",
        {} if body is None else body,
        format="json",
        **headers,
    )


@override_settings(**jwt_settings())
def test_member_restore_is_permissioned_idempotent_and_audited() -> None:
    client, platform_access = platform_token("restore-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="RSTR-A", owner_email="restore-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="RSTR-B", owner_email="restore-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    manager = add_staff(gym_a, email="restore-manager-a@example.test", role_key="MANAGER")
    receptionist = add_staff(
        gym_a, email="restore-reception-a@example.test", role_key="RECEPTIONIST"
    )
    accountant = add_staff(gym_a, email="restore-accountant-a@example.test", role_key="ACCOUNTANT")
    trainer = add_staff(gym_a, email="restore-trainer-a@example.test", role_key="TRAINER")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    manager_headers = auth_headers(staff_token(manager))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    api = _client()

    created = api.post(
        "/api/v1/members/",
        {"first_name": "Ada", "last_name": "Lovelace", "join_date": "2026-10-01"},
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    member_id = created.json()["data"]["id"]
    created_at = created.json()["data"]["updated_at"]
    assert created.json()["data"]["status"] == "ACTIVE"
    assert created.json()["data"]["authz_version"] == 1

    idle = _restore(api, member_id, owner_headers)
    assert idle.status_code == 200, idle.json()
    assert idle.json()["message"] == "Member restored"
    assert idle.json()["data"]["status"] == "ACTIVE"
    assert idle.json()["data"]["archived_at"] is None
    assert idle.json()["data"]["authz_version"] == 1
    assert idle.json()["data"]["updated_at"] == created_at
    assert "updated_by_membership_id" not in idle.json()["data"]
    assert "gym_id" not in idle.json()["data"]
    stored = _stored(gym_a.id, member_id)
    assert stored.authz_version == 1
    assert stored.updated_by_membership_id == owner_a.id
    assert _audits(gym_a.id, member_id) == []

    archived = api.post(f"/api/v1/members/{member_id}/archive/", {}, format="json", **owner_headers)
    assert archived.status_code == 200, archived.json()
    archived_updated_at = archived.json()["data"]["updated_at"]
    assert archived.json()["data"]["authz_version"] == 2
    assert archived.json()["data"]["archived_at"]
    stored = _stored(gym_a.id, member_id)
    assert stored.status == Member.Status.ARCHIVED
    assert stored.archived_at is not None
    archived_stamp = stored.archived_at.isoformat()
    assert stored.updated_by_membership_id == owner_a.id

    for headers in (reception_headers, accountant_headers, trainer_headers):
        denied = _restore(api, member_id, headers)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    stored = _stored(gym_a.id, member_id)
    assert stored.status == Member.Status.ARCHIVED
    assert stored.authz_version == 2
    assert stored.archived_at is not None
    assert _audits(gym_a.id, member_id) == []

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        archive_only = Role.objects.create(
            gym=gym_a,
            key="ARCHIVER",
            name="Archiver",
            created_by_membership_id=owner_a.id,
        )
        RolePermission.objects.create(
            gym=gym_a,
            role=archive_only,
            permission=Permission.objects.get(code="members.archive"),
            created_by_membership_id=owner_a.id,
        )
        GymMembershipRole.objects.create(
            gym=gym_a,
            gym_membership=receptionist,
            role=archive_only,
            created_by_membership_id=owner_a.id,
        )
    archive_only_headers = auth_headers(staff_token(receptionist))
    still_denied = _restore(api, member_id, archive_only_headers)
    assert still_denied.status_code == 403
    assert still_denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert _stored(gym_a.id, member_id).status == Member.Status.ARCHIVED

    rejected = _restore(
        api,
        member_id,
        manager_headers,
        {"status": "ACTIVE", "gym_id": str(gym_b.id)},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"]["fields"]["status"] == ["This field is not allowed."]
    assert rejected.json()["error"]["fields"]["gym_id"] == ["This field is not allowed."]
    assert _stored(gym_a.id, member_id).authz_version == 2

    hidden = _restore(api, member_id, other_headers)
    assert hidden.status_code == 404
    assert "Ada" not in hidden.content.decode()
    stored = _stored(gym_a.id, member_id)
    assert stored.status == Member.Status.ARCHIVED
    assert stored.gym_id == gym_a.id

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="members").update(
            enabled=False
        )
    blocked = _restore(api, member_id, manager_headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "PERMISSION_DENIED"
    assert _stored(gym_a.id, member_id).status == Member.Status.ARCHIVED
    assert _audits(gym_a.id, member_id) == []
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        GymFeatureEntitlement.objects.filter(gym=gym_a, feature__code="members").update(
            enabled=True
        )

    restored = _restore(api, member_id, manager_headers)
    assert restored.status_code == 200, restored.json()
    assert restored.json()["message"] == "Member restored"
    assert restored.json()["data"]["status"] == "ACTIVE"
    assert restored.json()["data"]["archived_at"] is None
    assert restored.json()["data"]["authz_version"] == 3
    assert restored.json()["data"]["updated_at"] != archived_updated_at
    stored = _stored(gym_a.id, member_id)
    assert stored.status == Member.Status.ACTIVE
    assert stored.archived_at is None
    assert stored.authz_version == 3
    assert stored.updated_by_membership_id == manager.id
    restored_updated_at = stored.updated_at
    audits = _audits(gym_a.id, member_id)
    assert len(audits) == 1
    assert audits[0].actor_membership_id == manager.id
    assert audits[0].module == "members"
    assert audits[0].target_type == "Member"
    assert audits[0].old_values["status"] == "ARCHIVED"
    assert audits[0].old_values["archived_at"] == archived_stamp
    assert audits[0].old_values["authz_version"] == 2
    assert audits[0].new_values == {"status": "ACTIVE", "archived_at": None, "authz_version": 3}

    repeated = _restore(api, member_id, owner_headers)
    assert repeated.status_code == 200, repeated.json()
    assert repeated.json()["data"]["status"] == "ACTIVE"
    assert repeated.json()["data"]["archived_at"] is None
    assert repeated.json()["data"]["authz_version"] == 3
    assert repeated.json()["data"]["updated_at"] == restored.json()["data"]["updated_at"]
    stored = _stored(gym_a.id, member_id)
    assert stored.updated_at == restored_updated_at
    assert stored.updated_by_membership_id == manager.id
    assert stored.authz_version == 3
    assert len(_audits(gym_a.id, member_id)) == 1

    unchanged = restore_member(
        gym=gym_a,
        actor=owner_a,
        member_id=stored.id,
        request_id="restore-noop",
        ip_address=None,
        user_agent="restore-test",
    )
    assert unchanged.status == Member.Status.ACTIVE
    assert unchanged.archived_at is None
    assert unchanged.authz_version == 3
    assert unchanged.updated_by_membership_id == manager.id
    assert len(_audits(gym_a.id, member_id)) == 1

    archived_again = api.post(
        f"/api/v1/members/{member_id}/archive/", {}, format="json", **owner_headers
    )
    assert archived_again.status_code == 200, archived_again.json()
    assert archived_again.json()["data"]["authz_version"] == 4
    service_restored = restore_member(
        gym=gym_a,
        actor=owner_a,
        member_id=stored.id,
        request_id="restore-service",
        ip_address="127.0.0.1",
        user_agent="restore-test",
    )
    assert service_restored.status == Member.Status.ACTIVE
    assert service_restored.archived_at is None
    assert service_restored.authz_version == 5
    assert service_restored.updated_by_membership_id == owner_a.id
    audits = _audits(gym_a.id, member_id)
    assert len(audits) == 2
    assert audits[1].request_id == "restore-service"
    assert audits[1].actor_membership_id == owner_a.id
    assert audits[1].old_values["authz_version"] == 4
    assert audits[1].new_values["authz_version"] == 5
    second = restore_member(
        gym=gym_a,
        actor=owner_a,
        member_id=stored.id,
        request_id="restore-service-repeat",
        ip_address=None,
        user_agent="restore-test",
    )
    assert second.authz_version == 5
    assert second.updated_at == service_restored.updated_at
    assert len(_audits(gym_a.id, member_id)) == 2
    assert Member.objects.filter(id=member_id).exists()


@override_settings(**jwt_settings())
def test_trainer_only_member_restore_does_not_write() -> None:
    client, platform_access = platform_token("restore-trainer-platform@example.test")
    payload = provision(
        client,
        platform_access,
        code="RSTR-T",
        owner_email="restore-trainer-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym, email="restore-floor@example.test", role_key="TRAINER")
    owner_headers = auth_headers(staff_token(owner))
    api = _client()
    created = api.post(
        "/api/v1/members/",
        {"first_name": "Nila", "last_name": "Client", "join_date": "2026-10-01"},
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    member_id = created.json()["data"]["id"]
    archived = api.post(f"/api/v1/members/{member_id}/archive/", {}, format="json", **owner_headers)
    assert archived.status_code == 200, archived.json()
    archived_version = archived.json()["data"]["authz_version"]

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        floor = Role.objects.create(
            gym=gym,
            key="FLOOR",
            name="Floor Coach",
            created_by_membership_id=owner.id,
        )
        RolePermission.objects.create(
            gym=gym,
            role=floor,
            permission=Permission.objects.get(code="members.restore"),
            created_by_membership_id=owner.id,
        )
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=trainer,
            role=floor,
            created_by_membership_id=owner.id,
        )
        trainer.refresh_from_db()
    granted = _restore(api, member_id, auth_headers(staff_token(trainer)))
    assert granted.status_code == 404
    assert "Nila" not in granted.content.decode()
    stored = _stored(gym.id, member_id)
    assert stored.status == Member.Status.ARCHIVED
    assert stored.archived_at is not None
    assert stored.authz_version == archived_version
    assert stored.updated_by_membership_id == owner.id
    assert _audits(gym.id, member_id) == []
