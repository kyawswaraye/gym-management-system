from __future__ import annotations

import importlib
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import URLPattern, URLResolver, get_resolver

from apps.files.models import FileAsset
from apps.tenants.models import StaffProfile

BACKEND_ROOT = Path(__file__).resolve().parents[2]
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
}
EXPECTED_INDEXES = {
    "fileasset_gym_purpose_idx": ("gym", "purpose"),
    "fileasset_gym_owner_idx": ("gym", "owner_type", "owner_id"),
    "fileasset_gym_scan_idx": ("gym", "scan_status"),
    "fileasset_gym_retention_idx": ("gym", "retention_status"),
    "fileasset_gym_uploader_idx": ("gym", "uploaded_by_membership_id"),
}


def test_file_asset_model_matches_the_metadata_contract() -> None:
    assert "apps.files" in settings.INSTALLED_APPS
    assert FileAsset._meta.db_table == "files_file_asset"
    assert FileAsset._meta.get_field("gym").remote_field.on_delete is models.RESTRICT
    assert isinstance(FileAsset._meta.get_field("byte_size"), models.BigIntegerField)
    assert FileAsset._meta.get_field("sha256").max_length == 64
    object_key = FileAsset._meta.get_field("object_key")
    assert object_key.max_length == 73
    assert object_key.deconstruct()[3].get("db_index", False) is False
    assert FileAsset._meta.get_field("uploaded_by_membership_id").null is False
    assert not FileAsset._meta.get_field("uploaded_by_membership_id").is_relation
    assert FileAsset._meta.get_field("owner_type").null is True
    assert FileAsset._meta.get_field("owner_id").null is True
    assert FileAsset._meta.get_field("deleted_at").null is True
    assert FileAsset._meta.get_field("scan_status").default == FileAsset.ScanStatus.PENDING
    assert FileAsset._meta.get_field("retention_status").default == FileAsset.RetentionStatus.ACTIVE
    assert {constraint.name for constraint in FileAsset._meta.constraints} == EXPECTED_CONSTRAINTS
    indexes = {index.name: tuple(index.fields) for index in FileAsset._meta.indexes}
    assert indexes == EXPECTED_INDEXES
    assert all(fields[0] == "gym" for fields in indexes.values())
    assert "signed_url" not in {field.name for field in FileAsset._meta.fields}


def test_staff_photo_stays_an_unlinked_identifier() -> None:
    photo = StaffProfile._meta.get_field("profile_photo_asset_id")
    description = StaffProfile.__doc__ or ""

    assert isinstance(photo, models.UUIDField)
    assert photo.null is True
    assert not photo.is_relation
    assert "foundational ``FileAsset`` table exists" in description
    assert "does not accept or return" in description
    assert "same gym" in description
    assert "CLEAN" in description


def test_file_security_migration_forces_rls_without_runtime_delete() -> None:
    migration = importlib.import_module("apps.files.migrations.0002_rls_integrity_and_grants")
    forward = migration.FORWARD_SQL
    reverse = migration.REVERSE_SQL

    assert migration.Migration.dependencies == [("files", "0001_initial")]
    assert "ENABLE ROW LEVEL SECURITY" in forward
    assert "FORCE ROW LEVEL SECURITY" in forward
    assert "FOR SELECT USING (gym_id = app_current_gym_id())" in forward
    assert "FOR INSERT WITH CHECK (gym_id = app_current_gym_id())" in forward
    assert "FOR UPDATE USING (gym_id = app_current_gym_id())" in forward
    assert "WITH CHECK (gym_id = app_current_gym_id())" in forward
    assert "FOR DELETE USING (gym_id = app_current_gym_id())" in forward
    assert "file_asset_uploader_same_gym_fk" in forward
    assert "REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT" in forward
    assert "fileasset_object_key_gym_prefix_ck" in forward
    assert "split_part(object_key, '/', 1) = gym_id::text" in forward
    assert [line.strip() for line in forward.splitlines() if line.strip().startswith("GRANT ")] == [
        "GRANT SELECT, INSERT, UPDATE ON files_file_asset TO gym_runtime;"
    ]
    assert "GRANT DELETE" not in forward
    assert "REVOKE ALL ON files_file_asset FROM gym_runtime" in reverse
    assert "DROP CONSTRAINT IF EXISTS file_asset_uploader_same_gym_fk" in reverse
    assert "NO FORCE ROW LEVEL SECURITY" in reverse
    assert "DISABLE ROW LEVEL SECURITY" in reverse


def test_file_migrations_are_linear_and_follow_the_tenants_leaf() -> None:
    nodes = _migration_nodes()
    initial = nodes[("files", "0001_initial")]
    security = nodes[("files", "0002_rls_integrity_and_grants")]

    assert initial == [("tenants", "0020_retire_platform_settings_manage")]
    assert security == [("files", "0001_initial")]
    assert _leaves(nodes)["files"] == [("files", "0002_rls_integrity_and_grants")]
    assert _leaves(nodes)["tenants"] == [("tenants", "0021_security_definer_least_privilege")]
    assert all(len(leaves) == 1 for leaves in _leaves(nodes).values())


def test_no_file_route_or_storage_secret_is_introduced() -> None:
    paths = _paths()
    files_root = BACKEND_ROOT / "apps" / "files"
    source = "\n".join(path.read_text() for path in files_root.rglob("*.py")).lower()
    urls = (BACKEND_ROOT / "config" / "urls.py").read_text()

    assert paths
    assert not any("files" in path for path in paths)
    assert "apps.files" not in urls
    assert not (files_root / "urls.py").exists()
    assert not (files_root / "views.py").exists()
    for token in ("minio", "aws_secret", "aws_access", "presign", "signed_url", "boto"):
        assert token not in source


def _migration_nodes() -> dict[tuple[str, str], list[tuple[str, str]]]:
    nodes: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for path in (BACKEND_ROOT / "apps").glob("*/migrations/0*.py"):
        app = path.parents[1].name
        module = importlib.import_module(f"apps.{app}.migrations.{path.stem}")
        nodes[(app, path.stem)] = list(module.Migration.dependencies)
    return nodes


def _leaves(
    nodes: dict[tuple[str, str], list[tuple[str, str]]],
) -> dict[str, list[tuple[str, str]]]:
    by_app: dict[str, list[tuple[str, str]]] = {}
    depended_on: set[tuple[str, str]] = set()
    for node, dependencies in nodes.items():
        by_app.setdefault(node[0], []).append(node)
        for dependency in dependencies:
            if dependency[0] == node[0]:
                depended_on.add(dependency)
    return {
        app: sorted(node for node in app_nodes if node not in depended_on)
        for app, app_nodes in by_app.items()
    }


def _paths() -> list[str]:
    found: list[str] = []

    def walk(patterns: list[URLPattern | URLResolver], prefix: str) -> None:
        for pattern in patterns:
            path = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, path)
            else:
                found.append(path)

    walk(get_resolver().url_patterns, "")
    return found
