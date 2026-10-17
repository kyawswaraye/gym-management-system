from __future__ import annotations

import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.members.models import Member
from apps.members.services import create_member
from apps.tenants.models import Gym, GymMembership, GymSettings
from apps.users.models import User
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

READ_FIELDS = {
    "id",
    "member_number",
    "first_name",
    "last_name",
    "normalized_full_name",
    "phone",
    "email",
    "gender",
    "date_of_birth",
    "address",
    "join_date",
    "emergency_contact_name",
    "emergency_contact_phone",
    "notes",
    "profile_photo_asset_id",
    "linked_user_id",
    "is_pt_client",
    "authz_version",
    "status",
    "archived_at",
    "created_at",
    "updated_at",
}


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "join_date": "2026-10-01",
    }
    body.update(overrides)
    return body


def _stored(gym_id: object, member_id: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return Member.objects.get(id=member_id)


def _settings(gym: Gym) -> GymSettings:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return GymSettings.objects.get(gym=gym)


def _asset(gym: Gym, membership_id: object, **overrides: object) -> FileAsset:
    values: dict[str, object] = {
        "gym": gym,
        "object_key": f"{gym.id}/{uuid4()}",
        "original_name": "photo.jpg",
        "content_type": "image/jpeg",
        "byte_size": 128,
        "sha256": "ab" * 32,
        "purpose": "PROFILE_PHOTO",
        "scan_status": FileAsset.ScanStatus.CLEAN,
        "retention_status": FileAsset.RetentionStatus.ACTIVE,
        "uploaded_by_membership_id": membership_id,
    }
    values.update(overrides)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return FileAsset.objects.create(**values)


@override_settings(**jwt_settings())
def test_member_api_is_scoped_normalized_and_archive_is_idempotent() -> None:
    client, platform_access = platform_token("member-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="MEMB-A", owner_email="member-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="MEMB-B", owner_email="member-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.select_related("user").get(
        id=gym_b_payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym_a, email="member-trainer-a@example.test", role_key="TRAINER")
    receptionist = add_staff(
        gym_a, email="member-reception-a@example.test", role_key="RECEPTIONIST"
    )
    accountant = add_staff(gym_a, email="member-accountant-a@example.test", role_key="ACCOUNTANT")
    owner_headers = auth_headers(staff_token(owner_a))
    other_headers = auth_headers(staff_token(owner_b))
    trainer_headers = auth_headers(staff_token(trainer))
    reception_headers = auth_headers(staff_token(receptionist))
    accountant_headers = auth_headers(staff_token(accountant))
    api = _client()
    original_next = _settings(gym_a).next_member_number

    denied = api.post("/api/v1/members/", _body(), format="json", **trainer_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    manual = api.post(
        "/api/v1/members/",
        _body(member_number="MEM-9"),
        format="json",
        **owner_headers,
    )
    assert manual.status_code == 400, manual.json()
    assert "member_number" in manual.json()["error"]["fields"]
    assert _settings(gym_a).next_member_number == original_next

    forged = api.post(
        "/api/v1/members/",
        _body(
            gym_id=str(gym_b.id),
            status="ARCHIVED",
            authz_version=9,
            linked_user_id=str(owner_b.user_id),
        ),
        format="json",
        **owner_headers,
    )
    assert forged.status_code == 400, forged.json()
    assert set(forged.json()["error"]["fields"]) >= {
        "gym_id",
        "status",
        "authz_version",
        "linked_user_id",
    }

    invalid_date = api.post(
        "/api/v1/members/",
        _body(date_of_birth="2026-10-02", join_date="2026-10-01"),
        format="json",
        **owner_headers,
    )
    assert invalid_date.status_code == 400
    assert "date_of_birth" in invalid_date.json()["error"]["fields"]

    created = api.post(
        "/api/v1/members/",
        _body(
            first_name="  Ada   Marie ",
            last_name=" Lovelace ",
            phone=" +95 (9) 111-222 ",
            email="Ada@Example.TEST",
            gender="female",
            date_of_birth="1990-01-02",
            address={"city": " Yangon ", "country_code": "mm"},
            join_date="2026-10-01",
        ),
        format="json",
        **owner_headers,
    )
    assert created.status_code == 201, created.json()
    member = created.json()["data"]
    assert set(member) == READ_FIELDS
    assert member["member_number"] == f"MEM-{original_next:06d}"
    assert member["first_name"] == "Ada Marie"
    assert member["last_name"] == "Lovelace"
    assert member["normalized_full_name"] == "ada marie lovelace"
    assert member["phone"] == "+959111222"
    assert member["email"] == "ada@example.test"
    assert member["gender"] == "FEMALE"
    assert member["address"]["city"] == "Yangon"
    assert member["address"]["country_code"] == "MM"
    assert member["is_pt_client"] is False
    assert member["linked_user_id"] is None
    assert member["authz_version"] == 1
    assert member["status"] == "ACTIVE"
    assert member["archived_at"] is None
    assert "gym_id" not in member
    assert _settings(gym_a).next_member_number == original_next + 1
    member_id = member["id"]

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        settings_row = GymSettings.objects.select_for_update().get(gym=gym_a)
        settings_row.allow_manual_member_numbers = True
        settings_row.save(update_fields=["allow_manual_member_numbers", "updated_at"])
    explicit = api.post(
        "/api/v1/members/",
        _body(
            member_number="MEM-000010",
            first_name="Mia",
            last_name="Zee",
            join_date="2026-09-01",
        ),
        format="json",
        **owner_headers,
    )
    assert explicit.status_code == 201, explicit.json()
    assert explicit.json()["data"]["member_number"] == "MEM-000010"
    assert _settings(gym_a).next_member_number == 11
    duplicate = api.post(
        "/api/v1/members/",
        _body(member_number="MEM-000010", first_name="Other", last_name="Person"),
        format="json",
        **owner_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "MEMBER_CONFLICT"
    assert "Ada" not in duplicate.json()["error"]["message"]
    assert "id" not in duplicate.json()["error"]
    assert _settings(gym_a).next_member_number == 11

    older = api.post(
        "/api/v1/members/",
        _body(first_name="Zoe", last_name="Young", join_date="2026-08-01"),
        format="json",
        **reception_headers,
    )
    assert older.status_code == 201, older.json()
    assert older.json()["data"]["member_number"] == "MEM-000011"

    listed = api.get(
        "/api/v1/members/?gym_id=" + str(gym_b.id),
        **owner_headers,
        HTTP_X_GYM_ID=str(gym_b.id),
    )
    assert listed.status_code == 400
    assert "gym_id" in listed.json()["error"]["fields"]
    own_list = api.get("/api/v1/members/?limit=1", **owner_headers, HTTP_X_GYM_ID=str(gym_b.id))
    assert own_list.status_code == 200, own_list.json()
    assert [row["normalized_full_name"] for row in own_list.json()["data"]] == [
        "ada marie lovelace"
    ]
    assert own_list.json()["meta"]["has_more"] is True
    cursor = own_list.json()["meta"]["next_after"]
    next_page = api.get(f"/api/v1/members/?limit=1&after={cursor}", **owner_headers)
    assert next_page.status_code == 200, next_page.json()
    assert next_page.json()["data"][0]["first_name"] == "Mia"
    stolen_cursor = api.get(f"/api/v1/members/?after={cursor}", **other_headers)
    assert stolen_cursor.status_code == 400
    assert "after" in stolen_cursor.json()["error"]["fields"]
    filtered_cursor = api.get(f"/api/v1/members/?after={cursor}&status=ARCHIVED", **owner_headers)
    assert filtered_cursor.status_code == 400
    found = api.get("/api/v1/members/?search=ADA", **owner_headers)
    assert [row["id"] for row in found.json()["data"]] == [member_id]
    phone_search = api.get("/api/v1/members/?search=%2B95%209-111", **owner_headers)
    assert [row["id"] for row in phone_search.json()["data"]] == [member_id]
    joined = api.get(
        "/api/v1/members/?joined_from=2026-09-15&joined_to=2026-10-31", **owner_headers
    )
    assert [row["first_name"] for row in joined.json()["data"]] == ["Ada Marie"]
    pt_clients = api.get("/api/v1/members/?is_pt_client=true", **owner_headers)
    assert pt_clients.status_code == 200
    assert pt_clients.json()["data"] == []
    assert pt_clients.json()["meta"] == {"has_more": False, "next_after": None}
    not_pt = api.get("/api/v1/members/?is_pt_client=false", **owner_headers)
    assert len(not_pt.json()["data"]) == 3
    assert api.get("/api/v1/members/", **other_headers).json()["data"] == []
    assert api.get("/api/v1/members/", **accountant_headers).status_code == 200

    hidden = api.get(f"/api/v1/members/{member_id}/", **other_headers)
    assert hidden.status_code == 404
    hidden_update = api.patch(
        f"/api/v1/members/{member_id}/",
        {"first_name": "Stolen"},
        format="json",
        **other_headers,
    )
    assert hidden_update.status_code == 404
    hidden_archive = api.post(
        f"/api/v1/members/{member_id}/archive/", {}, format="json", **other_headers
    )
    assert hidden_archive.status_code == 404
    assert _stored(gym_a.id, member_id).first_name == "Ada Marie"

    accountant_update = api.patch(
        f"/api/v1/members/{member_id}/",
        {"notes": "no"},
        format="json",
        **accountant_headers,
    )
    assert accountant_update.status_code == 403
    lifecycle = api.patch(
        f"/api/v1/members/{member_id}/",
        {"status": "ARCHIVED", "authz_version": 4, "first_name": "Nope"},
        format="json",
        **owner_headers,
    )
    assert lifecycle.status_code == 400
    assert set(lifecycle.json()["error"]["fields"]) >= {"status", "authz_version"}
    assert _stored(gym_a.id, member_id).status == "ACTIVE"
    updated = api.patch(
        f"/api/v1/members/{member_id}/",
        {"notes": "Floor access"},
        format="json",
        **trainer_headers,
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["notes"] == "Floor access"
    assert updated.json()["data"]["authz_version"] == 1
    reception_archive = api.post(
        f"/api/v1/members/{member_id}/archive/", {}, format="json", **reception_headers
    )
    trainer_archive = api.post(
        f"/api/v1/members/{member_id}/archive/", {}, format="json", **trainer_headers
    )
    assert reception_archive.status_code == trainer_archive.status_code == 403

    first_archive = api.post(
        f"/api/v1/members/{member_id}/archive/", {}, format="json", **owner_headers
    )
    assert first_archive.status_code == 200, first_archive.json()
    assert first_archive.json()["data"]["status"] == "ARCHIVED"
    assert first_archive.json()["data"]["authz_version"] == 2
    archived_at = first_archive.json()["data"]["archived_at"]
    updated_at = first_archive.json()["data"]["updated_at"]
    stored = _stored(gym_a.id, member_id)
    assert stored.archived_at is not None
    assert stored.authz_version == 2
    original_archived_at = stored.archived_at
    original_updated_at = stored.updated_at
    repeated = api.post(f"/api/v1/members/{member_id}/archive/", {}, format="json", **owner_headers)
    assert repeated.status_code == 200, repeated.json()
    assert repeated.json()["data"]["archived_at"] == archived_at
    assert repeated.json()["data"]["updated_at"] == updated_at
    assert repeated.json()["data"]["authz_version"] == 2
    stored = _stored(gym_a.id, member_id)
    assert stored.archived_at == original_archived_at
    assert stored.updated_at == original_updated_at
    assert stored.authz_version == 2
    assert Member.objects.filter(id=member_id).exists()


@override_settings(**jwt_settings())
def test_member_profile_photo_must_be_same_gym_and_clean() -> None:
    client, platform_access = platform_token("member-photo-platform@example.test")
    gym_a_payload = provision(
        client, platform_access, code="PHOTO-A", owner_email="photo-owner-a@example.test"
    )
    gym_b_payload = provision(
        client, platform_access, code="PHOTO-B", owner_email="photo-owner-b@example.test"
    )
    gym_a = Gym.objects.get(id=gym_a_payload["gym"]["id"])
    gym_b = Gym.objects.get(id=gym_b_payload["gym"]["id"])
    owner_a = GymMembership.objects.select_related("user").get(
        id=gym_a_payload["owner"]["gym_membership"]["id"]
    )
    owner_b = GymMembership.objects.get(id=gym_b_payload["owner"]["gym_membership"]["id"])
    headers = auth_headers(staff_token(owner_a))
    api = _client()
    clean = _asset(gym_a, owner_a.id)
    pending = _asset(gym_a, owner_a.id, scan_status=FileAsset.ScanStatus.PENDING)
    quarantined = _asset(gym_a, owner_a.id, retention_status=FileAsset.RetentionStatus.QUARANTINED)
    deleted = _asset(
        gym_a,
        owner_a.id,
        retention_status=FileAsset.RetentionStatus.DELETED,
        deleted_at=timezone.now(),
    )
    pending_delete = _asset(
        gym_a, owner_a.id, retention_status=FileAsset.RetentionStatus.DELETE_PENDING
    )
    other = _asset(gym_b, owner_b.id)
    message = "Select a clean profile photo from this gym."

    created = api.post(
        "/api/v1/members/",
        _body(profile_photo_asset_id=str(clean.id)),
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.json()
    member_id = created.json()["data"]["id"]
    assert created.json()["data"]["profile_photo_asset_id"] == str(clean.id)
    rendered = created.content.decode()
    assert "object_key" not in rendered
    assert clean.sha256 not in rendered

    for asset in (pending, quarantined, deleted, pending_delete, other):
        rejected = api.patch(
            f"/api/v1/members/{member_id}/",
            {"profile_photo_asset_id": str(asset.id)},
            format="json",
            **headers,
        )
        assert rejected.status_code == 400, rejected.json()
        assert rejected.json()["error"]["fields"]["profile_photo_asset_id"] == [message]
        assert _stored(gym_a.id, member_id).profile_photo_asset_id == clean.id

    detached = api.patch(
        f"/api/v1/members/{member_id}/",
        {"profile_photo_asset_id": None},
        format="json",
        **headers,
    )
    assert detached.status_code == 200, detached.json()
    assert detached.json()["data"]["profile_photo_asset_id"] is None
    assert _stored(gym_a.id, member_id).profile_photo_asset_id is None

    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        with pytest.raises(IntegrityError, match="member_profile_photo_same_gym_fk"):
            Member.objects.create(
                gym=gym_a,
                member_number="PHOTO-FK",
                first_name="Photo",
                last_name="Foreign",
                join_date=date.today(),
                profile_photo_asset_id=other.id,
                created_by_membership_id=owner_a.id,
                updated_by_membership_id=owner_a.id,
            )


@override_settings(**jwt_settings())
def test_generated_member_numbers_stay_unique_under_concurrency() -> None:
    client, platform_access = platform_token("member-race-platform@example.test")
    payload = provision(
        client, platform_access, code="MEM-RACE", owner_email="member-race-owner@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.get(id=payload["owner"]["gym_membership"]["id"])
    barrier = Barrier(2, timeout=20)
    failures: list[str] = []

    def allocate() -> str:
        connections.close_all()
        try:
            barrier.wait()
            gym_row = Gym.objects.get(id=gym.id)
            with transaction.atomic():
                set_gym_context(gym_id=gym_row.id)
                actor = GymMembership.objects.get(id=owner.id)
            member = create_member(
                gym=gym_row,
                actor=actor,
                member_number="",
                first_name="Race",
                last_name="Member",
                phone=None,
                email=None,
                gender=None,
                date_of_birth=None,
                address={},
                join_date=date.today(),
                emergency_contact_name="",
                emergency_contact_phone="",
                notes="",
                profile_photo_asset_id=None,
            )
            return member.member_number
        except Exception:
            failures.append(traceback.format_exc())
            barrier.abort()
            raise
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(allocate) for _ in range(2)]
    numbers = set()
    raised: list[str] = []
    for future in futures:
        try:
            numbers.add(future.result())
        except Exception as exc:
            raised.append(repr(exc))
    assert not failures, failures
    assert not raised, raised

    assert numbers == {"MEM-000001", "MEM-000002"}
    assert _settings(gym).next_member_number == 3


def test_member_profile_foundation_constraints_and_indexes() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'members_member'::regclass
            """
        )
        names = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT indexname, indexdef FROM pg_indexes
            WHERE tablename = 'members_member'
            """
        )
        indexes = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'members_member'
              AND column_name IN (
                'normalized_full_name', 'phone', 'email', 'gender',
                'profile_photo_asset_id', 'linked_user_id', 'authz_version'
              )
            """
        )
        columns = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT conname, confdeltype
            FROM pg_constraint
            WHERE conrelid = 'members_member'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (linked_user_id)%'
            """
        )
        linked_user_fk = cursor.fetchone()
    assert linked_user_fk is not None
    delete_action = linked_user_fk[1]

    assert {
        "member_gym_number_uniq",
        "member_authz_version_positive_ck",
        "member_birth_not_after_join_ck",
        "member_archive_state_ck",
        "member_phone_normalized_ck",
        "member_email_normalized_ck",
        "member_gender_normalized_ck",
        "member_normalized_name_nonblank_ck",
        "member_address_shape_ck",
        "member_profile_photo_same_gym_fk",
        "member_linked_user_fk",
    } <= names
    assert "DESC" in indexes["member_gym_joined_idx"]
    assert "normalized_full_name" in indexes["member_gym_name_idx"]
    assert "linked_user_id IS NOT NULL" in indexes["member_gym_linked_user_uniq"]
    assert columns["normalized_full_name"][0] == "NO"
    assert columns["phone"][0] == "YES"
    assert columns["email"][0] == "YES"
    assert columns["gender"][0] == "YES"
    assert columns["profile_photo_asset_id"][0] == "YES"
    assert columns["linked_user_id"][0] == "YES"
    assert columns["authz_version"][0] == "NO"
    assert str(columns["authz_version"][1]).startswith("1")
    assert delete_action == "r"

    gym = Gym.objects.create(code="MEM-CK", name="Member Checks", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with pytest.raises(IntegrityError, match="member_authz_version_positive_ck"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO members_member (
                      id, gym_id, member_number, first_name, last_name, normalized_full_name,
                      join_date, address, emergency_contact_name, emergency_contact_phone,
                      notes, authz_version, status, created_by_membership_id,
                      updated_by_membership_id, created_at, updated_at
                    ) VALUES (
                      gen_random_uuid(), %s, 'CK-1', 'A', '', 'a',
                      CURRENT_DATE, '{}'::jsonb, '', '', '', 0, 'ACTIVE',
                      gen_random_uuid(), gen_random_uuid(), now(), now()
                    )
                    """,
                    [gym.id],
                )
        with pytest.raises(IntegrityError, match="member_birth_not_after_join_ck"):
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO members_member (
                      id, gym_id, member_number, first_name, last_name, normalized_full_name,
                      date_of_birth, join_date, address, emergency_contact_name,
                      emergency_contact_phone, notes, authz_version, status,
                      created_by_membership_id, updated_by_membership_id, created_at, updated_at
                    ) VALUES (
                      gen_random_uuid(), %s, 'CK-2', 'A', '', 'a',
                      CURRENT_DATE + 1, CURRENT_DATE, '{}'::jsonb, '', '', '', 1, 'ACTIVE',
                      gen_random_uuid(), gen_random_uuid(), now(), now()
                    )
                    """,
                    [gym.id],
                )


def test_member_save_normalizes_profile_fields() -> None:
    user = User.objects.create_user("member-backfill@example.test", "correct horse battery staple")
    gym = Gym.objects.create(code="MEM-BACK", name="Member Backfill", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=gym,
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )
        member = Member.objects.create(
            gym=gym,
            member_number="MEM-000001",
            first_name="  Backfill  ",
            last_name=" Member ",
            phone="09-111",
            email="Backfill@Example.TEST",
            gender="male",
            join_date=date.today() - timedelta(days=1),
            created_by_membership_id=membership.id,
            updated_by_membership_id=membership.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member.refresh_from_db()
    assert member.first_name == "Backfill"
    assert member.normalized_full_name == "backfill member"
    assert member.phone == "09111"
    assert member.email == "backfill@example.test"
    assert member.gender == "MALE"
    assert member.authz_version == 1
    assert member.address["country_code"] == ""
