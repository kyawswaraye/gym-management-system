from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.apps import apps as django_apps
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone

from apps.common.api.exceptions import AuthenticationFailed
from apps.common.db.tenant_context import TenantContextError, set_gym_context, set_identity_context
from apps.members.models import Member
from apps.tenants.models import (
    ExpenseCategory,
    Gym,
    GymMembership,
    GymSettings,
    Role,
    StaffInvitation,
    StaffInvitationRole,
    TenantSequence,
)
from apps.tenants.services import EXPENSE_CATEGORY_CATALOG
from apps.users.models import GymSelectionTicket, RefreshSession, User
from apps.users.services import create_staff_session, rotate_refresh_token

_backfill = importlib.import_module("apps.tenants.migrations.0011_backfill_gym_prerequisites")
PROTECTED_EXPENSE_CATEGORIES = _backfill.PROTECTED_EXPENSE_CATEGORIES
backfill_gym_prerequisites = _backfill.backfill_gym_prerequisites

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
    "tenants_expense_category",
    "tenants_tenant_sequence",
    "tenants_idempotency_record",
    "members_member",
    "members_membership_plan",
    "members_membership",
    "members_payment",
    "members_receipt",
    "members_check_in",
    "members_check_in_challenge",
    "members_check_in_device",
    "notifications_notification",
    "files_file_asset",
    "audit_audit_log",
    "tenants_staff_invitation",
    "tenants_staff_invitation_role",
    "tenants_staff_profile",
    "cashbook_cash_session",
    "cashbook_cash_transaction",
    "expenses_expense",
    "salaries_salary_period",
    "salaries_staff_salary_line",
    "inventory_product_category",
    "inventory_product",
    "inventory_stock_movement",
    "sales_retail_sale",
    "sales_retail_sale_line",
}
POLICY_COMMANDS = {"r", "a", "w", "d"}


def _covered_policy_commands(policies: list[tuple[str, str | None, str | None]]) -> set[str]:
    covered: set[str] = set()
    for command, using_expr, check_expr in policies:
        commands = POLICY_COMMANDS if command == "*" else {command}
        assert commands <= POLICY_COMMANDS
        for item in commands:
            if item in {"r", "w", "d"} or command == "*":
                assert using_expr is not None
                assert "app_current_gym_id()" in using_expr
            if item in {"a", "w"} or command == "*":
                assert check_expr is not None
                assert "app_current_gym_id()" in check_expr
        covered.update(commands)
    return covered


def test_rls_inventory_is_enabled_forced_and_complete() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   pg_get_userbyid(c.relowner)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            """,
            [list(TENANT_TABLES)],
        )
        inventory = {row[0]: row[1:] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT c.relname, p.polcmd::text,
                   pg_get_expr(p.polqual, p.polrelid),
                   pg_get_expr(p.polwithcheck, p.polrelid)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_policy p ON p.polrelid = c.oid
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            """,
            [list(TENANT_TABLES)],
        )
        policies: dict[str, list[tuple[str, str | None, str | None]]] = {
            table: [] for table in TENANT_TABLES
        }
        for table, command, using_expr, check_expr in cursor.fetchall():
            policies[table].append((command, using_expr, check_expr))
        cursor.execute(
            """
            SELECT c.relname,
                   a.attnotnull,
                   EXISTS (
                     SELECT 1 FROM pg_constraint con
                     WHERE con.conrelid = c.oid
                       AND con.contype = 'u'
                       AND con.conkey = ARRAY[
                         (
                           SELECT attnum FROM pg_attribute
                           WHERE attrelid = c.oid AND attname = 'gym_id'
                             AND NOT attisdropped
                         ),
                         (
                           SELECT attnum FROM pg_attribute
                           WHERE attrelid = c.oid AND attname = 'id'
                             AND NOT attisdropped
                         )
                       ]::smallint[]
                   ),
                   EXISTS (
                     SELECT 1
                     FROM pg_index i
                     JOIN pg_attribute lead_column
                       ON lead_column.attrelid = c.oid
                      AND lead_column.attnum = (i.indkey::smallint[])[
                        array_lower(i.indkey::smallint[], 1)
                      ]
                     WHERE i.indrelid = c.oid
                       AND lead_column.attname = 'gym_id'
                   )
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a
              ON a.attrelid = c.oid AND a.attname = 'gym_id' AND NOT a.attisdropped
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
            """,
            [list(TENANT_TABLES)],
        )
        structure = {row[0]: row[1:] for row in cursor.fetchall()}
    assert set(inventory) == TENANT_TABLES
    for enabled, forced, owner in inventory.values():
        assert enabled is True
        assert forced is True
        assert owner != "gym_runtime"
    for table in TENANT_TABLES:
        assert _covered_policy_commands(policies[table]) == POLICY_COMMANDS
        not_null, gym_identity, tenant_index = structure[table]
        assert not_null is True
        assert gym_identity is True
        assert tenant_index is True


def test_runtime_invitation_access_is_limited_to_tenant_writes() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'tenants_staff_invitation', 'SELECT'),
              has_table_privilege('gym_runtime', 'tenants_staff_invitation', 'INSERT'),
              has_table_privilege('gym_runtime', 'tenants_staff_invitation', 'UPDATE'),
              has_table_privilege('gym_runtime', 'tenants_staff_invitation', 'DELETE'),
              has_table_privilege(
                'gym_runtime', 'tenants_staff_invitation_role', 'SELECT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_staff_invitation_role', 'INSERT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_staff_invitation_role', 'UPDATE'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_staff_invitation_role', 'DELETE'
              ),
              has_function_privilege(
                'gym_runtime', 'app_owner_invitation(text)', 'EXECUTE'
              )
            """
        )
        privileges = cursor.fetchone()
    assert privileges == (True, True, True, False, True, True, True, False, True)


def test_runtime_staff_profile_access_excludes_delete() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'tenants_staff_profile', 'SELECT'),
              has_table_privilege('gym_runtime', 'tenants_staff_profile', 'INSERT'),
              has_table_privilege('gym_runtime', 'tenants_staff_profile', 'UPDATE'),
              has_table_privilege('gym_runtime', 'tenants_staff_profile', 'DELETE'),
              has_function_privilege(
                'gym_runtime', 'app_staff_invitation(text)', 'EXECUTE'
              )
            """
        )
        privileges = cursor.fetchone()
    assert privileges == (True, True, True, False, True)


def test_runtime_has_only_required_provisioning_table_privileges() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'tenants_gym', 'INSERT'),
              has_table_privilege('gym_runtime', 'users_user', 'INSERT'),
              has_table_privilege(
                'gym_runtime', 'tenants_gym_provisioning_request', 'SELECT'
              ) AND has_table_privilege(
                'gym_runtime', 'tenants_gym_provisioning_request', 'INSERT'
              ) AND has_table_privilege(
                'gym_runtime', 'tenants_gym_provisioning_request', 'UPDATE'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'SELECT'
              ) AND has_table_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'INSERT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'DELETE'
              ),
              has_column_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'next_value', 'UPDATE'
              ) AND has_column_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'updated_at', 'UPDATE'
              ),
              has_column_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'prefix', 'UPDATE'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'SELECT'
              ) AND has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'INSERT'
              ) AND has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'UPDATE'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'DELETE'
              )
            """
        )
        (
            gym_insert,
            user_insert,
            idempotency_access,
            sequence_access,
            sequence_delete,
            sequence_value_update,
            sequence_prefix_update,
            finance_idempotency_access,
            finance_idempotency_delete,
        ) = cursor.fetchone()
    assert gym_insert is True
    assert user_insert is False
    assert idempotency_access is True
    assert sequence_access is True
    assert sequence_delete is False
    assert sequence_value_update is True
    assert sequence_prefix_update is False
    assert finance_idempotency_access is True
    assert finance_idempotency_delete is False


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


def test_context_is_cleared_after_transaction_rollback() -> None:
    gym_id = uuid4()
    with pytest.raises(RuntimeError, match="force rollback"):
        with transaction.atomic():
            set_gym_context(gym_id=gym_id)
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('app.gym_id', true)")
                assert cursor.fetchone()[0] == str(gym_id)
            raise RuntimeError("force rollback")

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.gym_id', true)")
        assert cursor.fetchone()[0] in ("", None)


def test_nested_transaction_rollback_restores_outer_context() -> None:
    outer_gym_id = uuid4()
    inner_gym_id = uuid4()
    with transaction.atomic():
        set_gym_context(gym_id=outer_gym_id)
        with pytest.raises(RuntimeError, match="rollback savepoint"):
            with transaction.atomic():
                set_gym_context(gym_id=inner_gym_id)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('app.gym_id', true)")
                    assert cursor.fetchone()[0] == str(inner_gym_id)
                raise RuntimeError("rollback savepoint")

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.gym_id', true)")
            assert cursor.fetchone()[0] == str(outer_gym_id)

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.gym_id', true)")
        assert cursor.fetchone()[0] in ("", None)


def test_concurrent_different_gym_contexts_never_cross() -> None:
    user = User.objects.create_user(
        "concurrent-context@example.test",
        "correct horse battery staple",
    )
    gym_a = Gym.objects.create(code="CTX-A", name="Context A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="CTX-B", name="Context B", status=Gym.Status.ACTIVE)
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

    barrier = Barrier(2, timeout=10)

    def visible_memberships(gym_id: UUID) -> list[str]:
        connections.close_all()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                set_gym_context(gym_id=gym_id)
                cursor.execute("SET LOCAL ROLE gym_runtime")
                barrier.wait()
                cursor.execute(
                    """
                    SELECT id
                    FROM tenants_gym_membership
                    WHERE id IN (%s, %s)
                    ORDER BY id
                    """,
                    [membership_a.id, membership_b.id],
                )
                return [str(row[0]) for row in cursor.fetchall()]
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(visible_memberships, gym_a.id)
        future_b = pool.submit(visible_memberships, gym_b.id)
        visible_a = future_a.result()
        visible_b = future_b.result()

    assert visible_a == [str(membership_a.id)]
    assert visible_b == [str(membership_b.id)]


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
                  (id, gym_id, member_number, first_name, last_name, normalized_full_name,
                   phone, email, gender, join_date, address, emergency_contact_name,
                   emergency_contact_phone, notes, authz_version, status,
                   created_by_membership_id, updated_by_membership_id, created_at, updated_at)
                VALUES
                  (gen_random_uuid(), %s, 'FORGED', 'Forged', 'Member', 'forged member',
                   NULL, NULL, NULL, CURRENT_DATE, '{}'::jsonb, '', '', '', 1, 'ACTIVE',
                   %s, %s, now(), now())
                """,
                [gym_b.id, actor_id, actor_id],
            )


def test_runtime_cannot_read_or_insert_cross_gym_invitation() -> None:
    user = User.objects.create_user("invite-cross@example.test", "correct horse battery staple")
    gym_a = Gym.objects.create(code="INV-A", name="Invite A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="INV-B", name="Invite B", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        membership_b = GymMembership.objects.create(
            gym=gym_b,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        role_b = Role.objects.create(gym=gym_b, key="OWNER", name="Owner")
        invitation_b = StaffInvitation.objects.create(
            gym=gym_b,
            gym_membership=membership_b,
            email="invitee@example.test",
            normalized_email="invitee@example.test",
            token_hash="b" * 64,
            expires_at=timezone.now() + timedelta(days=1),
        )
        invitation_role_b = StaffInvitationRole.objects.create(
            gym=gym_b,
            staff_invitation=invitation_b,
            role=role_b,
        )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(
            "SELECT count(*) FROM tenants_staff_invitation WHERE id = %s",
            [invitation_b.id],
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM tenants_staff_invitation_role WHERE id = %s",
            [invitation_role_b.id],
        )
        assert cursor.fetchone()[0] == 0
        with pytest.raises(DatabaseError):
            cursor.execute(
                """
                INSERT INTO tenants_staff_invitation
                  (id, gym_id, gym_membership_id, email, normalized_email, token_hash,
                   expires_at, status, revocation_reason, created_at, updated_at)
                VALUES
                  (gen_random_uuid(), %s, %s, 'forged@example.test', 'forged@example.test',
                   %s, now() + interval '1 day', 'PENDING', '', now(), now())
                """,
                [gym_b.id, membership_b.id, "c" * 64],
            )


def test_invitation_membership_must_belong_to_the_same_gym() -> None:
    user = User.objects.create_user("invite-fk@example.test", "correct horse battery staple")
    gym_a = Gym.objects.create(code="INV-FK-A", name="Invite FK A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="INV-FK-B", name="Invite FK B", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        membership_a = GymMembership.objects.create(
            gym=gym_a,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            set_gym_context(gym_id=gym_b.id)
            StaffInvitation.objects.create(
                gym=gym_b,
                gym_membership=membership_a,
                email="mismatch@example.test",
                normalized_email="mismatch@example.test",
                token_hash="d" * 64,
                expires_at=timezone.now() + timedelta(days=1),
            )


def test_existing_gym_backfill_is_idempotent_and_tenant_scoped() -> None:
    assert PROTECTED_EXPENSE_CATEGORIES == EXPENSE_CATEGORY_CATALOG
    bare = Gym.objects.create(
        code="LEGACY-1",
        name="Legacy Gym",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
    )
    invalid_prefix = Gym.objects.create(
        code="legacy_gym",
        name="Invalid Prefix Gym",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
    )
    partial = Gym.objects.create(
        code="PARTIAL-02",
        name="Partial Gym",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
    )
    with transaction.atomic():
        set_gym_context(gym_id=partial.id)
        GymSettings.objects.create(gym=partial, receipt_number_prefix="PARTIAL-02")
        ExpenseCategory.objects.create(
            gym=partial,
            code="RENT",
            name="Local rent",
            normalized_name="LOCAL RENT",
            display_order=3,
            is_active=True,
        )

    backfill_gym_prerequisites(django_apps, SimpleNamespace(connection=connection))

    year = datetime.now(ZoneInfo("Asia/Yangon")).year
    bare_settings = GymSettings.objects.get(gym=bare)
    assert bare_settings.receipt_number_prefix == "LEGACY-1"
    invalid_settings = GymSettings.objects.get(gym=invalid_prefix)
    assert invalid_settings.receipt_number_prefix == "RECEIPT"
    assert bare_settings.membership_expiry_warning_days == [0, 3, 7, 30]
    bare_categories = ExpenseCategory.objects.filter(gym=bare)
    assert set(bare_categories.values_list("system_key", flat=True)) == {
        code for code, _name in PROTECTED_EXPENSE_CATEGORIES
    }
    assert set(bare_categories.values_list("is_active", flat=True)) == {True}
    bare_sequence = TenantSequence.objects.get(gym=bare, sequence_key=f"RECEIPT:{year}")
    assert (bare_sequence.prefix, bare_sequence.next_value, bare_sequence.padding) == (
        "LEGACY-1",
        1,
        6,
    )
    assert (
        TenantSequence.objects.get(
            gym=invalid_prefix,
            sequence_key=f"RECEIPT:{year}",
        ).prefix
        == "RECEIPT"
    )

    localized = ExpenseCategory.objects.get(gym=partial, system_key="RENT")
    assert localized.name == "Local rent"
    assert localized.is_active is True
    assert ExpenseCategory.objects.filter(gym=partial).count() == len(PROTECTED_EXPENSE_CATEGORIES)
    partial_sequence = TenantSequence.objects.get(gym=partial, sequence_key=f"RECEIPT:{year}")
    assert partial_sequence.prefix == "PARTIAL-02"
    for _ in range(7):
        partial_sequence.next_value += 1
        partial_sequence.save(update_fields=["next_value", "updated_at"])

    backfill_gym_prerequisites(django_apps, SimpleNamespace(connection=connection))

    partial_sequence.refresh_from_db()
    localized.refresh_from_db()
    assert partial_sequence.next_value == 8
    assert localized.name == "Local rent"
    assert GymSettings.objects.filter(gym=bare).count() == 1
    assert TenantSequence.objects.filter(gym=bare, sequence_key=f"RECEIPT:{year}").count() == 1

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(
            "SELECT count(*) FROM tenants_gym_settings WHERE id = %s",
            [bare_settings.id],
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(bare.id)])
        cursor.execute(
            "SELECT count(*) FROM tenants_expense_category WHERE gym_id = %s",
            [bare.id],
        )
        assert cursor.fetchone()[0] == len(PROTECTED_EXPENSE_CATEGORIES)
        cursor.execute(
            "SELECT count(*) FROM tenants_expense_category WHERE gym_id = %s",
            [partial.id],
        )
        assert cursor.fetchone()[0] == 0
