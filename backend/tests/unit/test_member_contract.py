from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from rest_framework.exceptions import ValidationError

from apps.members.models import Member
from apps.members.normalization import MEMBER_GENDERS, canonical_address, normalize_member_phone
from apps.members.serializers import (
    MemberArchiveSerializer,
    MemberCreateSerializer,
    MemberListQuerySerializer,
    MemberSerializer,
    MemberUpdateSerializer,
)
from apps.members.services import (
    decode_member_cursor,
    encode_member_cursor,
    member_list_cursor_scope,
)

WRITE_FIELDS = {
    "member_number",
    "first_name",
    "last_name",
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
}
READ_FIELDS = WRITE_FIELDS | {
    "id",
    "normalized_full_name",
    "linked_user_id",
    "is_pt_client",
    "authz_version",
    "status",
    "archived_at",
    "created_at",
    "updated_at",
}
MEMBER_ID = "7df4f2d6-79d3-4fd1-b850-968142b1b6dd"
ADDRESS = {
    "address_line1": "1 Main",
    "address_line2": "",
    "city": "Yangon",
    "region": "",
    "postal_code": "",
    "country_code": "MM",
}


def _create_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "join_date": "2026-10-17",
    }
    body.update(overrides)
    return body


def test_member_genders_match_the_normalization_allowlist() -> None:
    assert set(Member.Gender.values) == MEMBER_GENDERS


def test_member_create_allowlist_rejects_server_fields() -> None:
    rejected = MemberCreateSerializer(
        data=_create_body(
            gym_id=MEMBER_ID,
            id=MEMBER_ID,
            normalized_full_name="ada lovelace",
            linked_user_id=MEMBER_ID,
            is_pt_client=True,
            status="ARCHIVED",
            archived_at="2026-10-17T00:00:00Z",
            authz_version=4,
            created_by_membership_id=MEMBER_ID,
            updated_by_membership_id=MEMBER_ID,
            created_at="2026-10-17T00:00:00Z",
            updated_at="2026-10-17T00:00:00Z",
        )
    )
    accepted = MemberCreateSerializer(
        data=_create_body(
            member_number="  ",
            phone=" +95 (9) 111-222 ",
            email="Ada@Example.TEST",
            gender="female",
            address={"city": " Yangon ", "country_code": "mm"},
        )
    )

    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "gym_id",
        "id",
        "normalized_full_name",
        "linked_user_id",
        "is_pt_client",
        "status",
        "archived_at",
        "authz_version",
        "created_by_membership_id",
        "updated_by_membership_id",
        "created_at",
        "updated_at",
    }
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["member_number"] == ""
    assert accepted.validated_data["first_name"] == "Ada"
    assert accepted.validated_data["phone"] == "+959111222"
    assert accepted.validated_data["email"] == "ada@example.test"
    assert accepted.validated_data["gender"] == "FEMALE"
    assert accepted.validated_data["address"]["city"] == "Yangon"
    assert accepted.validated_data["address"]["country_code"] == "MM"
    assert "status" not in accepted.validated_data
    assert set(WRITE_FIELDS) >= set(accepted.validated_data)


def test_member_update_rejects_identity_lifecycle_and_authz_fields() -> None:
    empty = MemberUpdateSerializer(data={})
    rejected = MemberUpdateSerializer(
        data={
            "first_name": "Ada",
            "member_number": "MEM-9",
            "gym_id": MEMBER_ID,
            "status": "ARCHIVED",
            "archived_at": "2026-10-17T00:00:00Z",
            "authz_version": 2,
            "linked_user_id": MEMBER_ID,
            "is_pt_client": True,
            "normalized_full_name": "ada",
            "created_by_membership_id": MEMBER_ID,
            "updated_by_membership_id": MEMBER_ID,
            "created_at": "2026-10-17T00:00:00Z",
            "updated_at": "2026-10-17T00:00:00Z",
        }
    )
    renamed = MemberUpdateSerializer(data={"first_name": "  Ada   Marie "})

    assert not empty.is_valid()
    assert not rejected.is_valid()
    assert set(rejected.errors) == {
        "member_number",
        "gym_id",
        "status",
        "archived_at",
        "authz_version",
        "linked_user_id",
        "is_pt_client",
        "normalized_full_name",
        "created_by_membership_id",
        "updated_by_membership_id",
        "created_at",
        "updated_at",
    }
    assert renamed.is_valid(), renamed.errors
    assert renamed.validated_data == {"first_name": "Ada Marie"}


def test_member_validation_normalizes_contacts_and_dates() -> None:
    blank = MemberCreateSerializer(data=_create_body(first_name=" ", last_name=" "))
    born_after = MemberCreateSerializer(
        data=_create_body(date_of_birth="2026-10-18", join_date="2026-10-17")
    )
    same_day = MemberCreateSerializer(
        data=_create_body(date_of_birth="2026-10-17", join_date="2026-10-17")
    )
    bad_phone = MemberCreateSerializer(data=_create_body(phone="95-call"))
    bad_address = MemberCreateSerializer(data=_create_body(address={"street": "Main"}))
    blank_phone = MemberCreateSerializer(data=_create_body(phone="", email=""))

    assert not blank.is_valid()
    assert set(blank.errors) >= {"first_name", "last_name"}
    assert not born_after.is_valid()
    assert "date_of_birth" in born_after.errors
    assert same_day.is_valid(), same_day.errors
    assert not bad_phone.is_valid()
    assert "phone" in bad_phone.errors
    assert not bad_address.is_valid()
    assert "address" in bad_address.errors
    assert blank_phone.is_valid(), blank_phone.errors
    assert blank_phone.validated_data["phone"] is None
    assert blank_phone.validated_data["email"] is None
    assert normalize_member_phone("+1 202 555 0100") == "+12025550100"
    assert canonical_address({})["country_code"] == ""


def test_member_archive_and_list_query_are_strict() -> None:
    assert MemberArchiveSerializer(data={}).is_valid()
    archive = MemberArchiveSerializer(data={"status": "ARCHIVED", "reason": "left"})
    query = MemberListQuerySerializer(data={"gym_id": MEMBER_ID, "bucket": "today"})
    accepted = MemberListQuerySerializer(
        data={"status": "ACTIVE", "search": " ada ", "is_pt_client": "false"}
    )
    pt_only = MemberListQuerySerializer(data={"is_pt_client": "true"})
    dates = MemberListQuerySerializer(data={"joined_from": "2026-10-18", "joined_to": "2026-10-17"})

    assert not archive.is_valid()
    assert set(archive.errors) == {"status", "reason"}
    assert not query.is_valid()
    assert set(query.errors) == {"gym_id", "bucket"}
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["limit"] == 25
    assert accepted.validated_data["search"] == "ada"
    assert accepted.validated_data["is_pt_client"] is False
    assert "after" not in accepted.validated_data
    assert pt_only.is_valid(), pt_only.errors
    assert pt_only.validated_data["is_pt_client"] is True
    assert not dates.is_valid()
    assert "joined_to" in dates.errors


def test_member_response_contract_hides_tenant_and_actor_ids() -> None:
    now = datetime(2026, 10, 17, tzinfo=UTC)
    payload = MemberSerializer(
        SimpleNamespace(
            id=uuid4(),
            member_number="MEM-000001",
            first_name="Ada",
            last_name="Lovelace",
            normalized_full_name="ada lovelace",
            phone=None,
            email="ada@example.test",
            gender="FEMALE",
            date_of_birth=date(1990, 1, 2),
            address=ADDRESS,
            join_date=date(2026, 10, 17),
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
            linked_user_id=None,
            authz_version=1,
            status="ACTIVE",
            archived_at=None,
            created_at=now,
            updated_at=now,
            gym_id=uuid4(),
            created_by_membership_id=uuid4(),
            updated_by_membership_id=uuid4(),
        )
    ).data

    assert set(payload) == READ_FIELDS
    assert payload["is_pt_client"] is False
    assert payload["authz_version"] == 1
    assert payload["linked_user_id"] is None
    assert payload["phone"] is None
    assert payload["address"]["city"] == "Yangon"
    assert "gym_id" not in payload
    assert "created_by_membership_id" not in payload


def test_member_cursor_is_bound_to_gym_and_filters() -> None:
    gym_id = uuid4()
    member_id = uuid4()
    scope = member_list_cursor_scope(
        gym_id=gym_id,
        status="ACTIVE",
        search="ada",
        joined_from=date(2026, 1, 1),
        joined_to=date(2026, 12, 31),
        is_pt_client=False,
    )
    token = encode_member_cursor(
        normalized_full_name="ada lovelace", member_id=member_id, scope=scope
    )

    assert decode_member_cursor(token, scope=scope) == ("ada lovelace", member_id)
    with pytest.raises(ValidationError):
        decode_member_cursor(
            token,
            scope=member_list_cursor_scope(
                gym_id=uuid4(),
                status="ACTIVE",
                search="ada",
                joined_from=date(2026, 1, 1),
                joined_to=date(2026, 12, 31),
                is_pt_client=False,
            ),
        )
    with pytest.raises(ValidationError):
        decode_member_cursor(
            token,
            scope=member_list_cursor_scope(
                gym_id=gym_id,
                status="",
                search="ada",
                joined_from=date(2026, 1, 1),
                joined_to=date(2026, 12, 31),
                is_pt_client=False,
            ),
        )
    with pytest.raises(ValidationError):
        decode_member_cursor(
            token,
            scope=member_list_cursor_scope(
                gym_id=gym_id,
                status="ACTIVE",
                search="ada",
                joined_from=date(2026, 1, 1),
                joined_to=date(2026, 12, 31),
                is_pt_client=True,
            ),
        )
