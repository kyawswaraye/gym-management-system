"""Member-self request allowlists that do not need a database."""

from __future__ import annotations

from rest_framework.test import APIRequestFactory

from apps.members.self_serializers import (
    MemberClassBookingWriteSerializer,
    MemberScheduleQuerySerializer,
    MemberSelfUpdateSerializer,
)
from apps.members.self_views import MemberGymLogoView


def test_profile_patch_rejects_fields_outside_the_allowlist() -> None:
    rejected = MemberSelfUpdateSerializer(
        data={"phone": "+66812345678", "first_name": "Ada", "status": "ARCHIVED", "gym_id": "x"}
    )
    assert rejected.is_valid() is False
    assert "first_name" in rejected.errors
    assert "status" in rejected.errors
    assert "gym_id" in rejected.errors

    empty = MemberSelfUpdateSerializer(data={})
    assert empty.is_valid() is False

    accepted = MemberSelfUpdateSerializer(data={"phone": None, "gender": "MALE", "address": None})
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["phone"] is None
    assert accepted.validated_data["address"] is None


def test_class_booking_body_is_occurrence_and_package_only() -> None:
    tampered = MemberClassBookingWriteSerializer(
        data={
            "occurrence_id": "11111111-1111-1111-1111-111111111111",
            "member_service_package_id": "22222222-2222-2222-2222-222222222222",
            "member_id": "33333333-3333-3333-3333-333333333333",
            "unmetered": True,
        }
    )
    assert tampered.is_valid() is False
    assert "member_id" in tampered.errors
    assert "unmetered" in tampered.errors


def test_schedule_range_is_ordered_and_at_most_42_days() -> None:
    missing = MemberScheduleQuerySerializer(data={})
    assert missing.is_valid() is False
    reversed_range = MemberScheduleQuerySerializer(
        data={"date_from": "2026-11-10", "date_to": "2026-11-01"}
    )
    assert reversed_range.is_valid() is False
    too_long = MemberScheduleQuerySerializer(
        data={"date_from": "2026-11-01", "date_to": "2026-12-13"}
    )
    assert too_long.is_valid() is False
    extra = MemberScheduleQuerySerializer(
        data={"date_from": "2026-11-01", "date_to": "2026-11-02", "member_id": "nope"}
    )
    assert extra.is_valid() is False
    accepted = MemberScheduleQuerySerializer(
        data={"date_from": "2026-11-02", "date_to": "2026-12-13"}
    )
    assert accepted.is_valid(), accepted.errors


def test_member_logo_negotiates_an_image_accept_header() -> None:
    view = MemberGymLogoView()
    request = view.initialize_request(
        APIRequestFactory().get(
            "/api/v1/member/gym/logo/",
            HTTP_ACCEPT="image/jpeg, image/png, image/webp",
        )
    )
    _renderer, media_type = view.get_content_negotiator().select_renderer(
        request, view.get_renderers()
    )
    assert media_type.startswith("image/")
