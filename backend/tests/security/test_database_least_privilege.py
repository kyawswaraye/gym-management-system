from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Protocol

import psycopg
import pytest
from django.conf import settings
from django.db import DatabaseError, connection, transaction

from apps.common.api.exceptions import AuthenticationFailed
from apps.users.mfa import (
    _totp,
    consume_mfa_challenge,
    create_mfa_challenge,
    encrypt_secret,
    generate_totp_secret,
    matching_totp_step,
)
from apps.users.models import User

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

DIRECT_FUNCTIONS = frozenset(
    {
        "app_bump_password_reset_authz(uuid)",
        "app_check_in_device(text)",
        "app_linked_members()",
        "app_member_email_candidates(text)",
        "app_owner_invitation(text)",
        "app_staff_invitation(text)",
        "app_staff_memberships()",
    }
)
TRIGGER_FUNCTIONS = frozenset(
    {
        "app_protect_membership_role_owner()",
        "app_protect_owner_role_permissions()",
        "enforce_invitation_role_owner_boundary()",
    }
)
ROW_SECURITY_FUNCTIONS = DIRECT_FUNCTIONS | TRIGGER_FUNCTIONS
BOOTSTRAP_TABLES = frozenset(
    {
        "tenants_gym",
        "tenants_gym_membership",
        "tenants_permission",
        "tenants_role",
        "tenants_staff_invitation",
        "tenants_staff_invitation_role",
        "tenants_system_role_template",
        "tenants_system_role_template_permission",
    }
)
AUTHZ_BUMP_COLUMNS = (
    "authz_version",
    "updated_at",
)
MFA_UPDATE_COLUMNS = (
    "mfa_enabled",
    "mfa_last_used_step",
    "mfa_secret_encrypted",
    "updated_at",
)
IDENTITY_FUNCTIONS = (
    "public.app_create_invited_identity(text,text,text,text)",
    "public.app_accept_invitation_password(text,text,timestamptz)",
    "public.app_confirm_invited_profile(text,text,text,text,timestamptz)",
    "public.app_accept_password_reset(text,text,timestamptz)",
)
IDENTITY_TRIGGER_FUNCTIONS = (
    "public.app_guard_runtime_user_insert()",
    "public.app_guard_runtime_user_update()",
)
CLEAN_DATABASE = "gym_platform_clean_migrate"


class CatalogCursor(Protocol):
    def execute(self, query: str, params: object = None) -> object: ...

    def fetchall(self) -> list[tuple[object, ...]]: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


def _bare_signature(signature: str) -> str:
    name, rest = signature.split("(", 1)
    if "." in name:
        name = name.rsplit(".", 1)[1]
    return f"{name}({rest}"


def _assert_least_privilege(cursor: CatalogCursor) -> None:
    cursor.execute(
        """
        SELECT rolsuper, rolbypassrls, rolcanlogin, rolinherit
        FROM pg_roles
        WHERE rolname = 'gym_auth_bootstrap'
        """
    )
    assert cursor.fetchone() == (False, True, False, False)

    cursor.execute(
        """
        SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proconfig @> ARRAY['row_security=off']
        ORDER BY 1
        """
    )
    functions = {
        _bare_signature(str(signature)): (owner, security_definer, set(config or []))
        for signature, owner, security_definer, config in cursor.fetchall()
    }
    assert set(functions) == ROW_SECURITY_FUNCTIONS
    for owner, security_definer, config in functions.values():
        assert owner == "gym_auth_bootstrap"
        assert security_definer is True
        assert config == {"search_path=pg_catalog, public", "row_security=off"}

    for signature in ROW_SECURITY_FUNCTIONS:
        cursor.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE'), "
            "has_function_privilege('gym_runtime', %s, 'EXECUTE'), "
            "has_function_privilege('gym_migration', %s, 'EXECUTE')",
            [signature, signature, signature],
        )
        public_execute, runtime_execute, migration_execute = cursor.fetchone()
        assert public_execute is False
        assert migration_execute is False
        assert runtime_execute is (signature in DIRECT_FUNCTIONS)

    cursor.execute(
        """
        SELECT c.relname,
               has_table_privilege('gym_auth_bootstrap', c.oid, 'SELECT'),
               has_table_privilege('gym_auth_bootstrap', c.oid, 'INSERT'),
               has_table_privilege('gym_auth_bootstrap', c.oid, 'UPDATE'),
               has_table_privilege('gym_auth_bootstrap', c.oid, 'DELETE')
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
        """
    )
    readable: set[str] = set()
    for table, select, insert, update, delete in cursor.fetchall():
        assert insert is False
        assert update is False
        assert delete is False
        if select:
            readable.add(str(table))
    assert readable == BOOTSTRAP_TABLES
    cursor.execute(
        """
        SELECT has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'id', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'gym_id', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'linked_user_id', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'status', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'authz_version', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'first_name', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'email', 'SELECT'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'authz_version', 'UPDATE'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'updated_at', 'UPDATE'
               ),
               has_column_privilege(
                 'gym_auth_bootstrap', 'members_member', 'first_name', 'UPDATE'
               ),
               has_table_privilege('gym_auth_bootstrap', 'members_member', 'SELECT'),
               has_table_privilege('gym_auth_bootstrap', 'members_member', 'INSERT'),
               has_table_privilege('gym_auth_bootstrap', 'members_member', 'UPDATE'),
               has_table_privilege('gym_auth_bootstrap', 'members_member', 'DELETE')
        """
    )
    assert cursor.fetchone() == (
        True,
        True,
        True,
        True,
        True,
        False,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    )
    for table, forbidden_column in (
        ("members_member", "first_name"),
        ("tenants_gym_membership", "user_id"),
    ):
        for column in AUTHZ_BUMP_COLUMNS:
            cursor.execute(
                "SELECT has_column_privilege('gym_auth_bootstrap', %s, %s, 'UPDATE')",
                [table, column],
            )
            assert cursor.fetchone() == (True,)
        cursor.execute(
            "SELECT has_column_privilege('gym_auth_bootstrap', %s, %s, 'UPDATE')",
            [table, forbidden_column],
        )
        assert cursor.fetchone() == (False,)

    cursor.execute(
        """
        SELECT has_table_privilege('gym_runtime', 'users_user', 'SELECT'),
               has_table_privilege('gym_runtime', 'users_user', 'INSERT'),
               has_table_privilege('gym_runtime', 'users_user', 'UPDATE'),
               has_table_privilege('gym_runtime', 'users_user', 'DELETE')
        """
    )
    assert cursor.fetchone() == (True, False, False, False)
    cursor.execute(
        """
        SELECT a.attname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'users_user'
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND has_column_privilege('gym_runtime', a.attrelid, a.attnum, 'UPDATE')
        ORDER BY a.attname
        """
    )
    assert tuple(row[0] for row in cursor.fetchall()) == MFA_UPDATE_COLUMNS
    cursor.execute(
        """
        SELECT pg_get_userbyid(c.relowner)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'users_user'
        """
    )
    table_owner = cursor.fetchone()
    assert table_owner is not None
    owner_name = str(table_owner[0])
    assert owner_name not in {"gym_runtime", "gym_auth_bootstrap"}
    for signature in IDENTITY_FUNCTIONS:
        cursor.execute(
            """
            SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig
            FROM pg_proc p
            WHERE p.oid = %s::regprocedure
            """,
            [signature],
        )
        function_owner, security_definer, config = cursor.fetchone()
        assert function_owner == owner_name
        assert security_definer is True
        assert set(config or []) == {"search_path=pg_catalog, public", "row_security=on"}
        cursor.execute(
            "SELECT has_function_privilege('public', %s, 'EXECUTE'), "
            "has_function_privilege('gym_runtime', %s, 'EXECUTE'), "
            "has_function_privilege('gym_auth_bootstrap', %s, 'EXECUTE')",
            [signature, signature, signature],
        )
        assert cursor.fetchone() == (False, True, False)
    cursor.execute(
        """
        SELECT proargnames
        FROM pg_proc
        WHERE oid = 'public.app_create_invited_identity(text,text,text,text)'::regprocedure
        """
    )
    assert cursor.fetchone() == (["p_email", "p_first_name", "p_last_name", "p_phone"],)
    cursor.execute(
        """
        SELECT proargnames
        FROM pg_proc
        WHERE oid = 'public.app_accept_invitation_password(text,text,timestamptz)'::regprocedure
        """
    )
    assert cursor.fetchone() == (["p_token_hash", "p_password_hash", "p_changed_at"],)
    cursor.execute(
        """
        SELECT proargnames
        FROM pg_proc
        WHERE oid = 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure
        """
    )
    assert cursor.fetchone() == (["p_token_hash", "p_password_hash", "p_changed_at"],)
    cursor.execute(
        """
        SELECT pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig,
               has_function_privilege('public', p.oid, 'EXECUTE'),
               has_function_privilege('gym_runtime', p.oid, 'EXECUTE')
        FROM pg_proc p
        WHERE p.oid = 'public.app_bump_password_reset_authz(uuid)'::regprocedure
        """
    )
    bump_owner, bump_definer, bump_config, public_exec, runtime_exec = cursor.fetchone()
    assert bump_owner == "gym_auth_bootstrap"
    assert bump_definer is True
    assert "row_security=off" in set(bump_config or [])
    assert public_exec is False
    assert runtime_exec is True
    for signature in IDENTITY_TRIGGER_FUNCTIONS:
        cursor.execute(
            """
            SELECT p.prosecdef,
                   has_function_privilege('public', p.oid, 'EXECUTE'),
                   has_function_privilege('gym_runtime', p.oid, 'EXECUTE')
            FROM pg_proc p
            WHERE p.oid = %s::regprocedure
            """,
            [signature],
        )
        assert cursor.fetchone() == (False, False, False)
    cursor.execute(
        """
        SELECT tgname, tgenabled, tgtype
        FROM pg_trigger
        WHERE tgrelid = 'public.users_user'::regclass
          AND NOT tgisinternal
          AND tgname IN ('guard_runtime_user_insert', 'guard_runtime_user_update')
        ORDER BY tgname
        """
    )
    triggers = {name: (enabled, tgtype) for name, enabled, tgtype in cursor.fetchall()}
    assert triggers["guard_runtime_user_insert"][0] == "O"
    assert triggers["guard_runtime_user_insert"][1] & 23 == 7
    assert triggers["guard_runtime_user_update"][0] == "O"
    assert triggers["guard_runtime_user_update"][1] & 23 == 19
    cursor.execute(
        """
        SELECT has_table_privilege('gym_runtime', 'users_user_groups', 'INSERT'),
               has_table_privilege('gym_runtime', 'users_user_user_permissions', 'INSERT')
        """
    )
    assert cursor.fetchone() == (False, False)


def test_finance_foundation_runtime_grants_exclude_delete() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'SELECT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'INSERT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'UPDATE'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_idempotency_record', 'DELETE'
              ),
              has_table_privilege(
                'gym_auth_bootstrap', 'tenants_idempotency_record', 'SELECT'
              ),
              has_table_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'DELETE'
              ),
              has_column_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'next_value', 'UPDATE'
              ),
              has_column_privilege(
                'gym_runtime', 'tenants_tenant_sequence', 'prefix', 'UPDATE'
              )
            """
        )
        assert cursor.fetchone() == (True, True, True, False, False, False, True, False)


def test_payment_runtime_grants_exclude_delete() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'members_payment', 'SELECT'),
              has_table_privilege('gym_runtime', 'members_payment', 'INSERT'),
              has_table_privilege('gym_runtime', 'members_payment', 'UPDATE'),
              has_table_privilege('gym_runtime', 'members_payment', 'DELETE'),
              has_table_privilege('gym_runtime', 'members_receipt', 'SELECT'),
              has_table_privilege('gym_runtime', 'members_receipt', 'INSERT'),
              has_table_privilege('gym_runtime', 'members_receipt', 'UPDATE'),
              has_table_privilege('gym_runtime', 'members_receipt', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False, True, True, True, False)


def test_cashbook_runtime_grants_are_append_only_for_movements() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'cashbook_cash_session', 'SELECT'),
              has_table_privilege('gym_runtime', 'cashbook_cash_session', 'INSERT'),
              has_table_privilege('gym_runtime', 'cashbook_cash_session', 'UPDATE'),
              has_table_privilege('gym_runtime', 'cashbook_cash_session', 'DELETE'),
              has_table_privilege('gym_runtime', 'cashbook_cash_transaction', 'SELECT'),
              has_table_privilege('gym_runtime', 'cashbook_cash_transaction', 'INSERT'),
              has_table_privilege('gym_runtime', 'cashbook_cash_transaction', 'UPDATE'),
              has_table_privilege('gym_runtime', 'cashbook_cash_transaction', 'DELETE')
            """
        )
        assert cursor.fetchone() == (True, True, True, False, True, True, False, False)


def test_security_definer_catalog_matches_least_privilege() -> None:
    with connection.cursor() as cursor:
        _assert_least_privilege(cursor)


def test_runtime_can_call_lookup_functions_but_not_trigger_functions() -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM app_staff_memberships()")
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM app_owner_invitation(%s)", ["missing-token"])
        assert cursor.fetchone() == (0,)
        cursor.execute("SELECT count(*) FROM app_staff_invitation(%s)", ["missing-token"])
        assert cursor.fetchone() == (0,)
        with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
            cursor.execute("SELECT app_protect_owner_role_permissions()")


def test_runtime_login_cannot_assume_bootstrap_role() -> None:
    password = os.environ.get("RUNTIME_DB_PASSWORD") or os.environ.get(
        "POSTGRES_RUNTIME_PASSWORD", ""
    )
    assert password, "RUNTIME_DB_PASSWORD is required"
    with psycopg.connect(
        host=connection.settings_dict["HOST"] or "localhost",
        port=connection.settings_dict["PORT"] or 5432,
        user="gym_runtime",
        password=password,
        dbname=connection.settings_dict["NAME"],
        autocommit=True,
    ) as runtime:
        with pytest.raises(psycopg.Error, match="permission denied"):
            runtime.execute("SET ROLE gym_auth_bootstrap")


def test_runtime_mfa_challenge_updates_watermark_and_rejects_replay() -> None:
    secret = generate_totp_secret()
    user = User.objects.create_user(
        "mfa-runtime-watermark@example.test",
        "correct horse battery staple",
    )
    user.mfa_enabled = True
    user.mfa_secret_encrypted = encrypt_secret(secret)
    user.mfa_last_used_step = None
    user.save(
        update_fields=[
            "mfa_enabled",
            "mfa_secret_encrypted",
            "mfa_last_used_step",
            "updated_at",
        ]
    )
    code = _totp(secret, int(time.time()) // 30)
    token, _expires_at = create_mfa_challenge(
        user=user,
        device_name="runtime-test",
        ip_address="127.0.0.1",
        user_agent="pytest",
        request_id="req_mfa_runtime",
    )

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        consume_mfa_challenge(token=token, code=code)

    user.refresh_from_db()
    matched_step = matching_totp_step(secret, code)
    assert matched_step is not None
    assert user.mfa_last_used_step == matched_step
    watermark = matched_step + 5
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        User.objects.filter(pk=user.pk).update(mfa_last_used_step=watermark)

    replay_token, _expires_at = create_mfa_challenge(
        user=user,
        device_name="runtime-test",
        ip_address="127.0.0.1",
        user_agent="pytest",
        request_id="req_mfa_replay",
    )
    with pytest.raises(AuthenticationFailed):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            consume_mfa_challenge(token=replay_token, code=code)

    user.refresh_from_db()
    assert user.mfa_last_used_step == watermark


def test_runtime_cannot_update_password_or_platform_flags() -> None:
    user = User.objects.create_user(
        "mfa-runtime-denied@example.test",
        "correct horse battery staple",
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        for statement in (
            "UPDATE users_user SET password = %s WHERE id = %s",
            "UPDATE users_user SET is_platform_admin = true WHERE id = %s",
            "UPDATE users_user SET is_superuser = true WHERE id = %s",
            "UPDATE users_user SET is_active = false WHERE id = %s",
            "UPDATE users_user SET mfa_last_used_step = 1, password = %s WHERE id = %s",
        ):
            with pytest.raises(DatabaseError, match="permission denied"):
                with transaction.atomic():
                    if statement.count("%s") == 2:
                        cursor.execute(statement, ["not-granted", user.id])
                    else:
                        cursor.execute(statement, [user.id])


def _admin_connect(dbname: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(
        host=connection.settings_dict["HOST"] or "localhost",
        port=connection.settings_dict["PORT"] or 5432,
        user=connection.settings_dict["USER"],
        password=connection.settings_dict["PASSWORD"],
        dbname=dbname,
        autocommit=True,
    )


def _drop_clean_database(admin: psycopg.Connection[tuple[object, ...]]) -> None:
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        [CLEAN_DATABASE],
    )
    admin.execute(f"DROP DATABASE IF EXISTS {CLEAN_DATABASE}")


def _migrate(database_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    password = os.environ.get("MIGRATION_DB_PASSWORD") or os.environ.get(
        "POSTGRES_MIGRATION_PASSWORD", ""
    )
    assert password, "MIGRATION_DB_PASSWORD is required for the clean migration proof"
    env = os.environ.copy()
    env.pop("PGOPTIONS", None)
    env.pop("TEST_DB_USER", None)
    env.update(
        {
            "DATABASE_ROLE_MODE": "MIGRATION",
            "DJANGO_SETTINGS_MODULE": "config.settings.local",
            "DB_NAME": database_name,
            "DB_HOST": str(connection.settings_dict["HOST"] or "localhost"),
            "DB_PORT": str(connection.settings_dict["PORT"] or 5432),
            "MIGRATION_DB_USER": "gym_migration",
            "MIGRATION_DB_PASSWORD": password,
        }
    )
    assert "check_function_bodies" not in env.get("PGOPTIONS", "")
    return subprocess.run(
        [sys.executable, "manage.py", "migrate", *args, "--noinput"],
        cwd=settings.BASE_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _require_success(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}"
        raise AssertionError(output[-4000:])


@pytest.fixture
def clean_database() -> Iterator[str]:
    admin = _admin_connect("postgres")
    try:
        _drop_clean_database(admin)
        admin.execute(f"CREATE DATABASE {CLEAN_DATABASE} OWNER gym_migration")
        with _admin_connect(CLEAN_DATABASE) as prepared:
            prepared.execute("ALTER SCHEMA public OWNER TO gym_migration")
            prepared.execute("GRANT USAGE ON SCHEMA public TO gym_runtime, gym_migration")
            prepared.execute("GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap")
            prepared.execute(
                f"GRANT CONNECT ON DATABASE {CLEAN_DATABASE} TO gym_runtime, gym_migration"
            )
        yield CLEAN_DATABASE
    finally:
        _drop_clean_database(admin)
        admin.close()


def test_clean_database_migrates_as_gym_migration(clean_database: str) -> None:
    _require_success(_migrate(clean_database))
    _require_success(_migrate(clean_database, "tenants", "0020"))
    _require_success(_migrate(clean_database, "users", "0004"))
    _require_success(_migrate(clean_database))
    with _admin_connect(clean_database) as proof, proof.cursor() as cursor:
        _assert_least_privilege(cursor)
