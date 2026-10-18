from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.db.backends.utils import CursorWrapper
from django.utils import timezone

from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.tenants.models import Gym, GymMembership
from apps.users.models import User

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

EXPECTED_CONSTRAINTS = {
    "fileasset_gym_id_uniq",
    "fileasset_gym_object_key_uniq",
    "fileasset_metadata_nonblank_ck",
    "fileasset_content_type_ck",
    "fileasset_object_key_format_ck",
    "fileasset_byte_size_nonnegative_ck",
    "fileasset_sha256_format_ck",
    "fileasset_scan_status_ck",
    "fileasset_owner_pair_ck",
    "fileasset_retention_status_ck",
    "fileasset_retention_deleted_ck",
    "fileasset_object_key_gym_prefix_ck",
    "file_asset_uploader_same_gym_fk",
    "fileasset_upload_sha256_ck",
    "fileasset_rejection_code_ck",
    "fileasset_scan_claim_ck",
    "fileasset_profile_photo_ck",
    "fileasset_expense_receipt_ck",
}


def test_file_asset_migration_state_constraints_indexes_rls_and_grants() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT name FROM django_migrations
            WHERE app = 'files'
            ORDER BY name
            """
        )
        applied = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner)
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'files_file_asset'
            """
        )
        enabled, forced, owner = cursor.fetchone()
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'files_file_asset'::regclass
            """
        )
        constraints = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT i.indisprimary, lead_column.attname
            FROM pg_index i
            JOIN pg_class t ON t.oid = i.indrelid
            JOIN pg_attribute lead_column
              ON lead_column.attrelid = t.oid
             AND lead_column.attnum = (i.indkey::smallint[])[
               array_lower(i.indkey::smallint[], 1)
             ]
            WHERE t.relname = 'files_file_asset'
            """
        )
        index_leads = cursor.fetchall()
        cursor.execute(
            """
            SELECT polcmd::text,
                   pg_get_expr(polqual, polrelid),
                   pg_get_expr(polwithcheck, polrelid)
            FROM pg_policy
            WHERE polrelid = 'files_file_asset'::regclass
            """
        )
        policies = cursor.fetchall()
        cursor.execute(
            """
            SELECT
              has_table_privilege('gym_runtime', 'files_file_asset', 'SELECT'),
              has_table_privilege('gym_runtime', 'files_file_asset', 'INSERT'),
              has_table_privilege('gym_runtime', 'files_file_asset', 'UPDATE'),
              has_table_privilege('gym_runtime', 'files_file_asset', 'DELETE')
            """
        )
        privileges = cursor.fetchone()

    assert applied == [
        "0001_initial",
        "0002_rls_integrity_and_grants",
        "0003_profile_photo_contract",
        "0004_expense_receipt_and_expense",
    ]
    assert (enabled, forced) == (True, True)
    assert owner != "gym_runtime"
    assert EXPECTED_CONSTRAINTS <= constraints
    assert index_leads
    assert {column for _primary, column in index_leads if column != "id"} == {"gym_id"}
    assert "object_key" not in {column for _primary, column in index_leads}
    assert any(primary and column == "id" for primary, column in index_leads)
    commands = {command for command, _using, _check in policies}
    assert commands == {"r", "a", "w", "d"}
    for command, using_expr, check_expr in policies:
        if command in {"r", "w", "d"}:
            assert using_expr is not None
            assert "app_current_gym_id()" in using_expr
        if command in {"a", "w"}:
            assert check_expr is not None
            assert "app_current_gym_id()" in check_expr
    assert privileges == (True, True, True, False)


def test_same_gym_uploader_succeeds_and_cross_gym_uploader_is_rejected() -> None:
    gym_a = Gym.objects.create(code="FILE-FK-A", name="File FK A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="FILE-FK-B", name="File FK B", status=Gym.Status.ACTIVE)
    membership_a = _membership(gym_a, "file-fk-a@example.test")
    membership_b = _membership(gym_b, "file-fk-b@example.test")

    asset = _create_asset(gym_a, membership_a.id)
    assert asset.scan_status == FileAsset.ScanStatus.PENDING
    assert asset.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert asset.deleted_at is None

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(
            """
            INSERT INTO files_file_asset (
              id, gym_id, object_key, original_name, content_type, byte_size, sha256,
              purpose, scan_status, uploaded_by_membership_id, retention_status,
              created_at, updated_at
            ) VALUES (
              gen_random_uuid(), %s, %s, 'note.txt', 'text/plain', 4, %s,
              'NOTE', 'PENDING', %s, 'ACTIVE', now(), now()
            )
            """,
            [gym_a.id, f"{gym_a.id}/{uuid4()}", "cd" * 32, membership_a.id],
        )
        cursor.execute("SELECT count(*) FROM files_file_asset WHERE gym_id = %s", [gym_a.id])
        assert cursor.fetchone()[0] == 2

    with pytest.raises(IntegrityError, match="file_asset_uploader_same_gym_fk"):
        _create_asset(gym_a, membership_b.id)

    with pytest.raises(IntegrityError, match="file_asset_uploader_same_gym_fk"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("GRANT INSERT ON files_file_asset TO gym_migration")
            cursor.execute("GRANT EXECUTE ON FUNCTION app_current_gym_id() TO gym_migration")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("SET LOCAL ROLE gym_migration")
            cursor.execute(
                """
                INSERT INTO files_file_asset (
                  id, gym_id, object_key, original_name, content_type, byte_size, sha256,
                  purpose, scan_status, uploaded_by_membership_id, retention_status,
                  created_at, updated_at
                ) VALUES (
                  gen_random_uuid(), %s, %s, 'forged.txt', 'text/plain', 4, %s,
                  'NOTE', 'PENDING', %s, 'ACTIVE', now(), now()
                )
                """,
                [gym_a.id, f"{gym_a.id}/{uuid4()}", "ef" * 32, membership_b.id],
            )


def test_runtime_cannot_select_update_or_insert_another_gyms_asset() -> None:
    gym_a = Gym.objects.create(code="FILE-ISO-A", name="File Iso A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="FILE-ISO-B", name="File Iso B", status=Gym.Status.ACTIVE)
    membership_a = _membership(gym_a, "file-iso-a@example.test")
    membership_b = _membership(gym_b, "file-iso-b@example.test")
    asset_b = _create_asset(gym_b, membership_b.id, original_name="secret.jpg")

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM files_file_asset WHERE id = %s", [asset_b.id])
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "UPDATE files_file_asset SET original_name = 'stolen.jpg' WHERE id = %s",
            [asset_b.id],
        )
        assert cursor.rowcount == 0

    asset_b.refresh_from_db()
    assert asset_b.original_name == "secret.jpg"

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                """
                INSERT INTO files_file_asset (
                  id, gym_id, object_key, original_name, content_type, byte_size, sha256,
                  purpose, scan_status, uploaded_by_membership_id, retention_status,
                  created_at, updated_at
                ) VALUES (
                  gen_random_uuid(), %s, %s, 'forged.jpg', 'image/jpeg', 8, %s,
                  'PROFILE_PHOTO', 'PENDING', %s, 'ACTIVE', now(), now()
                )
                """,
                [gym_b.id, f"{gym_b.id}/{uuid4()}", "11" * 32, membership_b.id],
            )

    asset_a = _create_asset(gym_a, membership_a.id)
    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "UPDATE files_file_asset SET gym_id = %s WHERE id = %s",
                [gym_b.id, asset_a.id],
            )
    asset_a.refresh_from_db()
    assert asset_a.gym_id == gym_a.id


def test_missing_and_malformed_gym_context_fail_closed() -> None:
    gym = Gym.objects.create(code="FILE-CTX", name="File Context", status=Gym.Status.ACTIVE)
    membership = _membership(gym, "file-ctx@example.test")
    asset = _create_asset(gym, membership.id)

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM files_file_asset WHERE id = %s", [asset.id])
        assert cursor.fetchone()[0] == 0

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            _insert_runtime_asset(cursor, gym.id, membership.id)

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', '', true)")
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM files_file_asset")
        assert cursor.fetchone()[0] == 0

    with pytest.raises(DatabaseError):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', 'not-a-uuid', true)")
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT count(*) FROM files_file_asset")


def test_file_asset_checks_reject_invalid_metadata() -> None:
    gym_a = Gym.objects.create(code="FILE-CK-A", name="File Check A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="FILE-CK-B", name="File Check B", status=Gym.Status.ACTIVE)
    membership = _membership(gym_a, "file-ck@example.test")
    owner_id = uuid4()
    deleted_at = timezone.now()

    zero = _create_asset(gym_a, membership.id, byte_size=0)
    owned = _create_asset(
        gym_a,
        membership.id,
        owner_type="STAFF_PROFILE",
        owner_id=owner_id,
    )
    deleted = _create_asset(
        gym_a,
        membership.id,
        retention_status=FileAsset.RetentionStatus.DELETED,
        deleted_at=deleted_at,
    )
    assert (zero.byte_size, owned.owner_id, deleted.deleted_at) == (0, owner_id, deleted_at)

    invalid: list[dict[str, object]] = [
        {"sha256": "AB" * 32},
        {"sha256": "abc"},
        {"sha256": "g" * 64},
        {"byte_size": -1},
        {"owner_type": "STAFF_PROFILE", "owner_id": None},
        {"owner_type": None, "owner_id": uuid4()},
        {"owner_type": "   ", "owner_id": uuid4()},
        {"retention_status": FileAsset.RetentionStatus.DELETED, "deleted_at": None},
        {
            "retention_status": FileAsset.RetentionStatus.ACTIVE,
            "deleted_at": deleted_at,
        },
        {
            "retention_status": FileAsset.RetentionStatus.DELETE_PENDING,
            "deleted_at": deleted_at,
        },
        {"original_name": "   "},
        {"original_name": "path/photo.jpg"},
        {"purpose": " "},
        {"content_type": "image/JPEG"},
        {"object_key": f"{gym_b.id}/{uuid4()}"},
        {"object_key": f"{gym_a.id}/{uuid4()}".upper()},
    ]
    for overrides in invalid:
        with pytest.raises(IntegrityError):
            _create_asset(gym_a, membership.id, **overrides)

    existing = _create_asset(gym_a, membership.id)
    with pytest.raises(IntegrityError, match="fileasset_gym_object_key_uniq"):
        _create_asset(gym_a, membership.id, object_key=existing.object_key)


def test_runtime_delete_is_denied() -> None:
    gym = Gym.objects.create(code="FILE-DEL", name="File Delete", status=Gym.Status.ACTIVE)
    membership = _membership(gym, "file-del@example.test")
    asset = _create_asset(gym, membership.id)

    with pytest.raises(DatabaseError, match="permission denied"):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("DELETE FROM files_file_asset WHERE id = %s", [asset.id])

    assert FileAsset.objects.filter(id=asset.id).exists()


def test_sequential_and_pooled_context_switches_do_not_leak() -> None:
    gym_a = Gym.objects.create(code="FILE-POOL-A", name="File Pool A", status=Gym.Status.ACTIVE)
    gym_b = Gym.objects.create(code="FILE-POOL-B", name="File Pool B", status=Gym.Status.ACTIVE)
    membership_a = _membership(gym_a, "file-pool-a@example.test")
    membership_b = _membership(gym_b, "file-pool-b@example.test")
    asset_a = _create_asset(gym_a, membership_a.id)
    asset_b = _create_asset(gym_b, membership_b.id)

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT id::text FROM files_file_asset")
        assert [row[0] for row in cursor.fetchall()] == [str(asset_a.id)]

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT id::text FROM files_file_asset")
        assert [row[0] for row in cursor.fetchall()] == [str(asset_b.id)]

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.gym_id', true)")
        assert cursor.fetchone()[0] in ("", None)
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM files_file_asset")
        assert cursor.fetchone()[0] == 0

    barrier = Barrier(2, timeout=10)

    def visible(gym_id: UUID, own_id: UUID, other_id: UUID) -> list[str]:
        connections.close_all()
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                barrier.wait()
                cursor.execute(
                    "SELECT id::text FROM files_file_asset WHERE id IN (%s, %s) ORDER BY id",
                    sorted((own_id, other_id)),
                )
                return [row[0] for row in cursor.fetchall()]
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(visible, gym_a.id, asset_a.id, asset_b.id)
        future_b = pool.submit(visible, gym_b.id, asset_b.id, asset_a.id)
        assert future_a.result() == [str(asset_a.id)]
        assert future_b.result() == [str(asset_b.id)]


def _membership(gym: Gym, email: str) -> GymMembership:
    user = User.objects.create_user(email, "correct horse battery staple")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )


def _create_asset(gym: Gym, membership_id: UUID, **overrides: object) -> FileAsset:
    values: dict[str, object] = {
        "gym": gym,
        "object_key": f"{gym.id}/{uuid4()}",
        "original_name": "photo.jpg",
        "content_type": "image/jpeg",
        "byte_size": 128,
        "sha256": "ab" * 32,
        "purpose": "DOCUMENT",
        "uploaded_by_membership_id": membership_id,
    }
    values.update(overrides)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return FileAsset.objects.create(**values)


def _insert_runtime_asset(cursor: CursorWrapper, gym_id: UUID, membership_id: UUID) -> None:
    cursor.execute(
        """
        INSERT INTO files_file_asset (
          id, gym_id, object_key, original_name, content_type, byte_size, sha256,
          purpose, scan_status, uploaded_by_membership_id, retention_status,
          created_at, updated_at
        ) VALUES (
          gen_random_uuid(), %s, %s, 'note.txt', 'text/plain', 4, %s,
          'NOTE', 'PENDING', %s, 'ACTIVE', now(), now()
        )
        """,
        [gym_id, f"{gym_id}/{uuid4()}", "cd" * 32, membership_id],
    )
