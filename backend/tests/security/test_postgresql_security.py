from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import TenantContextError, set_gym_context, set_identity_context
from apps.members.models import Member
from apps.tenants.models import Gym, GymMembership
from apps.users.models import GymSelectionTicket, RefreshSession, User
from apps.users.services import create_staff_session, rotate_refresh_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

TENANT_TABLES = {
    "tenants_gym_membership",
    "tenants_role",
    "tenants_role_permission",
    "tenants_gym_membership_role",
    "tenants_gym_feature_entitlement",
    "tenants_gym_settings",
    "members_member",
    "members_membership_plan",
    "members_membership",
    "members_payment",
    "members_receipt",
    "audit_audit_log",
}


def test_rls_inventory_is_enabled_forced_and_complete() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   pg_get_userbyid(c.relowner),
                   count(p.polname)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid = c.oid
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity, c.relowner
            """,
            [list(TENANT_TABLES)],
        )
        inventory = {row[0]: row[1:] for row in cursor.fetchall()}
    assert set(inventory) == TENANT_TABLES
    for enabled, forced, owner, policy_count in inventory.values():
        assert enabled is True
        assert forced is True
        assert owner != "gym_runtime"
        assert policy_count == 4


def test_runtime_role_is_unprivileged() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'gym_runtime'")
        assert cursor.fetchone() == (False, False)


def test_migration_runtime_and_bootstrap_roles_exist() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolname, rolsuper, rolbypassrls, rolcanlogin
            FROM pg_roles
            WHERE rolname IN ('gym_migration', 'gym_runtime', 'gym_auth_bootstrap')
            """
        )
        roles = {row[0]: row[1:] for row in cursor.fetchall()}
    assert roles["gym_migration"] == (False, False, True)
    assert roles["gym_runtime"] == (False, False, True)
    assert roles["gym_auth_bootstrap"] == (False, True, False)


def test_identity_context_fails_closed_outside_a_transaction() -> None:
    with pytest.raises(TenantContextError, match="active transaction"):
        set_identity_context(user_id=uuid4(), request_id="req_no_atomic")


def test_context_is_transaction_local_on_reused_connection() -> None:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.gym_id', %s, true)",
                ["00000000-0000-0000-0000-000000000001"],
            )
            cursor.execute("SELECT current_setting('app.gym_id', true)")
            assert cursor.fetchone()[0] == "00000000-0000-0000-0000-000000000001"
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.gym_id', true)")
        assert cursor.fetchone()[0] in ("", None)


def test_missing_context_exposes_no_tenant_rows() -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM tenants_gym_membership")
        assert cursor.fetchone()[0] == 0


def test_refresh_rotates_and_replay_revokes_family() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    user = User.objects.create_user("refresh@example.test", "correct horse battery staple")
    gym = Gym.objects.create(
        code="REFRESH-TEST",
        name="Refresh Test",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    with override_settings(
        REFRESH_TOKEN_PEPPER="test-pepper",
        JWT_PRIVATE_KEY=private_pem,
        JWT_PUBLIC_KEYS={"test-key": public_pem},
        JWT_KEY_ID="test-key",
    ):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = create_staff_session(user=user, membership=membership)
        replacement = rotate_refresh_token(original.refresh_token, request_id="req_rotation")
        with pytest.raises(AuthenticationFailed):
            rotate_refresh_token(original.refresh_token, request_id="req_replay")
    assert replacement.refresh_token != original.refresh_token
    assert not RefreshSession.objects.filter(
        user=user, revoked_at__isnull=True, family_id__isnull=False
    ).exists()


def test_gym_selection_ticket_is_single_use_and_hash_only() -> None:
    from apps.users.services import consume_gym_selection_ticket, issue_gym_selection_ticket

    user = User.objects.create_user("select@example.test", "correct horse battery staple")
    gym_a = Gym.objects.create(
        code="SEL-A",
        name="Select A",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )
    gym_b = Gym.objects.create(
        code="SEL-B",
        name="Select B",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        membership_a = GymMembership.objects.create(
            gym=gym_a,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        membership_b = GymMembership.objects.create(
            gym=gym_b,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    memberships = [
        {"gym_membership_id": str(membership_a.id), "gym_id": str(gym_a.id)},
        {"gym_membership_id": str(membership_b.id), "gym_id": str(gym_b.id)},
    ]
    with override_settings(
        REFRESH_TOKEN_PEPPER="test-pepper",
        JWT_PRIVATE_KEY=private_pem,
        JWT_PUBLIC_KEYS={"test-key": public_pem},
        JWT_KEY_ID="test-key",
    ):
        plaintext, _expires = issue_gym_selection_ticket(
            user=user, memberships=memberships, request_id="req_select"
        )
        ticket = GymSelectionTicket.objects.get(user=user, consumed_at__isnull=True)
        assert plaintext not in ticket.ticket_hash
        with pytest.raises(AuthenticationFailed):
            consume_gym_selection_ticket(
                plaintext,
                gym_membership_id=uuid4(),
                request_id="req_bad",
                membership_loader=lambda _user_id: memberships,
            )
        consume_gym_selection_ticket(
            plaintext,
            gym_membership_id=membership_a.id,
            request_id="req_ok",
            membership_loader=lambda _user_id: memberships,
        )
        with pytest.raises(AuthenticationFailed):
            consume_gym_selection_ticket(
                plaintext,
                gym_membership_id=membership_b.id,
                request_id="req_replay",
                membership_loader=lambda _user_id: memberships,
            )
    ticket.refresh_from_db()
    assert ticket.consumed_at is not None
    assert ticket.selected_gym_membership_id == membership_a.id


def test_runtime_cannot_read_or_insert_cross_gym_membership() -> None:
    user = User.objects.create_user("cross-gym@example.test", "correct horse battery staple")
    gym_a = Gym.objects.create(
        code="GYM-A",
        name="Gym A",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )
    gym_b = Gym.objects.create(
        code="GYM-B",
        name="Gym B",
        status=Gym.Status.ACTIVE,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        membership_b = GymMembership.objects.create(
            gym=gym_b,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(
            "SELECT count(*) FROM tenants_gym_membership WHERE id = %s", [membership_b.id]
        )
        assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError):
            cursor.execute(
                """
                INSERT INTO tenants_gym_membership
                  (id, gym_id, user_id, status, is_owner, authz_version, joined_date,
                   created_at, updated_at)
                VALUES (gen_random_uuid(), %s, %s, 'ACTIVE', false, 1, CURRENT_DATE, now(), now())
                """,
                [gym_b.id, user.id],
            )


def test_runtime_cannot_read_or_insert_cross_gym_member() -> None:
    gym_a = Gym.objects.create(code="MEM-A", name="Member Gym A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="MEM-B", name="Member Gym B", status=Gym.Status.ACTIVE)
    actor_id = uuid4()
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        member_b = Member.objects.create(
            gym=gym_b,
            member_number="MEM-000001",
            first_name="Tenant B",
            join_date=date.today(),
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM members_member WHERE id = %s", [member_b.id])
        assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError):
            cursor.execute(
                """
                INSERT INTO members_member
                  (id, gym_id, member_number, first_name, last_name, phone, email,
                   gender, join_date, address, emergency_contact_name,
                   emergency_contact_phone, notes, status, created_by_membership_id,
                   updated_by_membership_id, created_at, updated_at)
                VALUES
                  (gen_random_uuid(), %s, 'FORGED', 'Forged', '', '', '', '', CURRENT_DATE,
                   '{{}}', '', '', '', 'ACTIVE', %s, %s, now(), now())
                """,
                [gym_b.id, actor_id, actor_id],
            )
