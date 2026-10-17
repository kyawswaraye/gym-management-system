from __future__ import annotations

import hashlib
import importlib
import os
from datetime import date, timedelta

import pytest
from django.apps import apps as django_apps
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import CheckInChallenge
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymSettings,
    Permission,
    Role,
    RolePermission,
    SystemRoleTemplate,
    SystemRoleTemplatePermission,
)
from apps.users.models import User
from tests.security.test_check_ins import _client
from tests.security.test_memberships import _enable_memberships
from tests.security.test_tenant_gym_settings import (
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


def _challenge_grants(role: Role) -> int:
    return RolePermission.objects.filter(
        role=role, permission__code="checkins.challenge.issue"
    ).count()


def test_check_in_challenge_issue_backfill_survives_a_historical_reseed() -> None:
    """0003 removes later grants. 0022 must put them back on protected roles only."""

    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    backfill = importlib.import_module("apps.tenants.migrations.0022_check_in_challenge_permission")
    seed.seed(django_apps, None)
    assert "checkins.challenge.issue" not in seed.PERMISSION_CODES
    assert not SystemRoleTemplatePermission.objects.filter(
        permission__code="checkins.challenge.issue"
    ).exists()

    user = User.objects.create_user("challenge-backfill@example.test", PASSWORD)
    gym = Gym.objects.create(code="CHAL-A", name="Challenge A", status=Gym.Status.ACTIVE)
    other = Gym.objects.create(code="CHAL-B", name="Challenge B", status=Gym.Status.ACTIVE)
    templates = {
        key: SystemRoleTemplate.objects.get(key=key)
        for key in ("OWNER", "MANAGER", "RECEPTIONIST", "TRAINER", "ACCOUNTANT")
    }
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 17),
        )
        roles = {
            key: Role.objects.create(
                gym=gym,
                key=key,
                name=key.title(),
                source_template=templates[key],
                source_template_version=templates[key].version,
                created_by_membership_id=membership.id,
            )
            for key in templates
        }
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
            joined_date=date(2026, 10, 17),
        )
        named_manager = Role.objects.create(gym=other, key="MANAGER", name="Manager")

    backfill.grant_check_in_challenge_issue(django_apps, None)
    assert Permission.objects.filter(code="checkins.challenge.issue").count() == 1
    for key in ("OWNER", "MANAGER"):
        assert (
            SystemRoleTemplatePermission.objects.filter(
                system_role_template=templates[key],
                permission__code="checkins.challenge.issue",
            ).count()
            == 1
        )
        assert _challenge_grants(roles[key]) == 1
    for key in ("RECEPTIONIST", "TRAINER", "ACCOUNTANT"):
        assert not SystemRoleTemplatePermission.objects.filter(
            system_role_template=templates[key],
            permission__code="checkins.challenge.issue",
        ).exists()
        assert _challenge_grants(roles[key]) == 0
    assert _challenge_grants(custom) == 0
    assert _challenge_grants(named_manager) == 0

    backfill.grant_check_in_challenge_issue(django_apps, None)
    assert Permission.objects.filter(code="checkins.challenge.issue").count() == 1
    assert _challenge_grants(roles["OWNER"]) == 1
    assert _challenge_grants(roles["MANAGER"]) == 1
    assert (
        SystemRoleTemplatePermission.objects.filter(
            permission__code="checkins.challenge.issue"
        ).count()
        == 2
    )


def _issue(api: APIClient, headers: dict[str, str], location: str = "Front desk") -> object:
    return api.post(
        "/api/v1/check-in-challenges/",
        {"display_location": location},
        format="json",
        **headers,
    )


@override_settings(**jwt_settings())
def test_staff_challenge_is_hashed_rotated_and_not_a_scan() -> None:
    client, platform_access = platform_token("challenge-platform@example.test")
    payload = provision(
        client, platform_access, code="QR-A", owner_email="challenge-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="QR-B", owner_email="challenge-owner-b@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym, email="challenge-trainer@example.test", role_key="TRAINER")
    receptionist = add_staff(gym, email="challenge-reception@example.test", role_key="RECEPTIONIST")
    accountant = add_staff(gym, email="challenge-accountant@example.test", role_key="ACCOUNTANT")
    owner_headers = auth_headers(staff_token(owner))
    trainer_headers = auth_headers(staff_token(trainer))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    _enable_memberships(gym)
    api = _client()

    assert _issue(api, accountant_headers).status_code == 403
    assert _issue(api, reception_headers).status_code == 403
    assert _issue(api, trainer_headers).status_code == 403
    denied_device = api.post(
        "/api/v1/check-in-challenges/",
        {"display_location": "Front desk", "display_device_id": str(owner.id)},
        format="json",
        **owner_headers,
    )
    assert denied_device.status_code == 400
    idempotent = api.post(
        "/api/v1/check-in-challenges/",
        {"display_location": "Front desk"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="challenge-key-1234567890",
        **owner_headers,
    )
    assert idempotent.status_code == 400
    assert (
        api.post("/api/v1/check-ins/scan/", {}, format="json", **owner_headers).status_code == 404
    )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(
            check_in_challenge_ttl_seconds=45,
            check_in_challenge_max_uses=2,
        )
    issued = _issue(api, owner_headers, "  Front   desk  ")
    assert issued.status_code == 201, issued.json()
    body = issued.json()["data"]
    assert set(body) == {
        "id",
        "payload",
        "display_location",
        "issued_at",
        "expires_at",
        "max_uses",
        "status",
    }
    assert body["display_location"] == "Front desk"
    assert body["status"] == "ACTIVE"
    assert body["max_uses"] == 2
    assert body["payload"].startswith("gms-checkin:v1:")
    assert "gym" not in body["payload"]
    digest = hashlib.sha256(body["payload"].encode("utf-8")).hexdigest()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = CheckInChallenge.objects.get(id=body["id"])
        assert row.challenge_hash == digest
        assert row.display_device_id is None
        assert row.issued_by_membership_id == owner.id
        assert row.expires_at - row.issued_at == timedelta(seconds=45)
        assert body["payload"] not in row.challenge_hash
    with transaction.atomic(), connection.cursor() as cursor:
        set_gym_context(gym_id=other.id)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        assert CheckInChallenge.objects.count() == 0

    rotated = _issue(api, owner_headers)
    assert rotated.status_code == 201, rotated.json()
    assert rotated.json()["data"]["payload"] != body["payload"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        previous = CheckInChallenge.objects.get(id=body["id"])
        assert previous.status == CheckInChallenge.Status.REVOKED
        assert CheckInChallenge.objects.filter(status=CheckInChallenge.Status.ACTIVE).count() == 1
        audits = list(
            AuditLog.objects.filter(target_id__in=[body["id"], rotated.json()["data"]["id"]])
        )
    actions = {audit.action for audit in audits}
    assert actions == {"CHECK_IN_CHALLENGE_ISSUED", "CHECK_IN_CHALLENGE_REVOKED"}
    rendered = str([audit.new_values for audit in audits])
    assert body["payload"] not in rendered
    assert digest not in rendered

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        permission = Permission.objects.get(code="checkins.challenge.issue")
        role = Role.objects.get(gym=gym, key="TRAINER")
        RolePermission.objects.create(
            gym=gym,
            role=role,
            permission=permission,
            created_by_membership_id=owner.id,
        )
    granted_trainer = auth_headers(staff_token(trainer))
    assert _issue(api, granted_trainer).status_code == 404

    with pytest.raises(DatabaseError, match="immutable"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            CheckInChallenge.objects.filter(id=rotated.json()["data"]["id"]).update(
                challenge_hash="a" * 64
            )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'members_check_in_challenge', 'SELECT'),
              has_table_privilege('gym_runtime', 'members_check_in_challenge', 'INSERT'),
              has_table_privilege('gym_runtime', 'members_check_in_challenge', 'UPDATE'),
              has_table_privilege('gym_runtime', 'members_check_in_challenge', 'DELETE'),
              relrowsecurity,
              relforcerowsecurity
            FROM pg_class
            WHERE relname = 'members_check_in_challenge'
            """
        )
        assert cursor.fetchone() == (True, True, True, False, True, True)
