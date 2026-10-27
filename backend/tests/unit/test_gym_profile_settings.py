from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from rest_framework.permissions import IsAuthenticated

from apps.audit.services import redact_audit_values
from apps.tenants.serializers import (
    GymProfileSerializer,
    GymProfileUpdateSerializer,
    GymSettingsUpdateSerializer,
)
from apps.tenants.validation import (
    GYM_PROFILE_FIELDS,
    SETTINGS_MUTABLE_FIELDS,
    prepare_settings_changes,
)
from apps.tenants.views import GymProfileView, GymSettingsView
from apps.users.permissions import IsActiveOwner, RequiresPermission


def test_profile_update_rejects_lifecycle_subscription_and_unknown_fields() -> None:
    serializer = GymProfileUpdateSerializer(
        data={
            "name": "Updated Gym",
            "code": "FORBIDDEN",
            "status": "SUSPENDED",
            "id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
            "gym_id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
            "subscription_status": "CANCELLED",
            "subscription_plan_id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
            "subscription_plan_name": "Trial",
            "subscription_starts_at": "2026-10-17T00:00:00Z",
            "subscription_ends_at": "2026-11-17T00:00:00Z",
            "created_at": "2026-10-17T00:00:00Z",
            "updated_at": "2026-10-17T00:00:00Z",
        }
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {
        "code",
        "status",
        "id",
        "gym_id",
        "subscription_status",
        "subscription_plan_id",
        "subscription_plan_name",
        "subscription_starts_at",
        "subscription_ends_at",
        "created_at",
        "updated_at",
    }


def test_profile_update_normalizes_timezone_and_rejects_invalid_values() -> None:
    rangoon = GymProfileUpdateSerializer(data={"timezone": "Asia/Rangoon"})
    assert rangoon.is_valid(), rangoon.errors
    assert rangoon.validated_data["timezone"] == "Asia/Yangon"

    invalid = GymProfileUpdateSerializer(
        data={"timezone": "Not/AZone", "currency": "usd", "country_code": "th"}
    )
    assert not invalid.is_valid()
    assert set(invalid.errors) >= {"timezone", "currency", "country_code"}

    empty = GymProfileUpdateSerializer(data={})
    assert not empty.is_valid()


def test_profile_allowlist_matches_tenant_owned_fields() -> None:
    assert set(GymProfileUpdateSerializer().fields) == set(GYM_PROFILE_FIELDS)


def test_profile_response_includes_read_only_lifecycle_and_subscription() -> None:
    created_at = datetime(2026, 9, 1, tzinfo=UTC)
    updated_at = datetime(2026, 9, 2, tzinfo=UTC)
    plan_id = UUID("39f07d2b-bbd8-43d2-9c18-e4cbf3af131e")
    gym = SimpleNamespace(
        id=UUID("f52030c1-18af-4e18-a1ec-50997d892ef4"),
        code="SET-A",
        name="SET-A Gym",
        legal_name="",
        status="ACTIVE",
        email="desk@example.test",
        phone="",
        address_line1="",
        address_line2="",
        city="Yangon",
        region="",
        postal_code="",
        country_code="MM",
        timezone="Asia/Yangon",
        currency="THB",
        subscription_plan_id=plan_id,
        subscription_plan=SimpleNamespace(name="Trial"),
        subscription_status="TRIAL",
        subscription_starts_at=None,
        subscription_ends_at=None,
        created_at=created_at,
        updated_at=updated_at,
    )

    data = GymProfileSerializer(gym).data

    assert set(data) == set(GYM_PROFILE_FIELDS) | {
        "id",
        "code",
        "status",
        "subscription_plan_id",
        "subscription_plan_name",
        "subscription_status",
        "subscription_starts_at",
        "subscription_ends_at",
        "created_at",
        "updated_at",
        "has_logo",
    }
    assert data["has_logo"] is False
    assert data["code"] == "SET-A"
    assert data["status"] == "ACTIVE"
    assert data["subscription_plan_id"] == str(plan_id)
    assert data["subscription_plan_name"] == "Trial"
    assert data["subscription_status"] == "TRIAL"
    assert data["subscription_starts_at"] is None
    assert data["subscription_ends_at"] is None
    assert set(GymProfileUpdateSerializer().fields).isdisjoint(set(data) - set(GYM_PROFILE_FIELDS))


def test_settings_update_rejects_read_only_and_unknown_fields() -> None:
    serializer = GymSettingsUpdateSerializer(
        data={
            "week_starts_on": 0,
            "gym_id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
            "next_member_number": 9,
            "updated_by_membership_id": "1d44221a-b537-4f0c-8128-4dc13fca6595",
            "created_at": "2026-10-17T00:00:00Z",
            "theme": "dark",
        }
    )

    assert not serializer.is_valid()
    assert set(serializer.errors) == {
        "gym_id",
        "next_member_number",
        "updated_by_membership_id",
        "created_at",
        "theme",
    }


def test_settings_update_validates_ranges_prefixes_and_warning_days() -> None:
    valid = GymSettingsUpdateSerializer(
        data={
            "week_starts_on": 0,
            "member_number_prefix": "MEM-1",
            "receipt_number_prefix": "RCPT",
            "check_in_duplicate_window_seconds": 86_400,
            "check_in_challenge_ttl_seconds": 30,
            "check_in_challenge_max_uses": 100,
            "class_cancellation_cutoff_minutes": 0,
            "membership_expiry_warning_days": [30, 0, 7],
        }
    )
    assert valid.is_valid(), valid.errors
    assert valid.validated_data["membership_expiry_warning_days"] == [0, 7, 30]

    invalid = GymSettingsUpdateSerializer(
        data={
            "week_starts_on": 7,
            "member_number_prefix": "mem",
            "receipt_number_prefix": "BAD PREFIX",
            "check_in_duplicate_window_seconds": 86_401,
            "check_in_challenge_ttl_seconds": 29,
            "check_in_challenge_max_uses": 0,
            "class_cancellation_cutoff_minutes": -1,
            "membership_expiry_warning_days": [1, 1, -3, 400],
            "require_separate_finance_approval": False,
        }
    )
    assert not invalid.is_valid()
    assert set(invalid.errors) == {
        "week_starts_on",
        "member_number_prefix",
        "receipt_number_prefix",
        "check_in_duplicate_window_seconds",
        "check_in_challenge_ttl_seconds",
        "check_in_challenge_max_uses",
        "class_cancellation_cutoff_minutes",
        "membership_expiry_warning_days",
        "require_separate_finance_approval",
    }


def test_settings_allowlist_matches_operational_contract() -> None:
    assert set(GymSettingsUpdateSerializer().fields) == set(SETTINGS_MUTABLE_FIELDS)


def test_finance_approval_cannot_be_disabled() -> None:
    disabled = GymSettingsUpdateSerializer(data={"require_separate_finance_approval": False})
    assert not disabled.is_valid()
    assert "require_separate_finance_approval" in disabled.errors

    enabled = GymSettingsUpdateSerializer(data={"require_separate_finance_approval": True})
    assert enabled.is_valid(), enabled.errors


def test_audit_redaction_removes_credential_keys_only() -> None:
    redacted = redact_audit_values(
        {
            "name": "North Gym",
            "email": "desk@example.test",
            "password": "secret",
            "nested": {"refresh_token": "abc", "timezone": "Asia/Yangon"},
        }
    )

    assert redacted["name"] == "North Gym"
    assert redacted["email"] == "desk@example.test"
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["refresh_token"] == "[REDACTED]"
    assert redacted["nested"]["timezone"] == "Asia/Yangon"


def test_logo_upload_accepts_png_and_rejects_a_mismatched_file() -> None:
    from apps.tenants.logo import read_logo_upload

    png = b"\x89PNG\r\n\x1a\n" + b"logo"
    stored, content_type = read_logo_upload(
        SimpleNamespace(content_type="image/png", read=lambda _limit: png)
    )
    assert stored == png
    assert content_type == "image/png"

    try:
        read_logo_upload(
            SimpleNamespace(content_type="image/png", read=lambda _limit: b"not-a-png")
        )
    except Exception as exc:
        assert "logo" in getattr(exc, "detail", {})
    else:
        raise AssertionError("mismatched logo was accepted")


def test_gym_logo_permissions_follow_profile_policy() -> None:
    from apps.tenants.views import GymLogoView

    logo_read = GymLogoView()
    logo_read.request = SimpleNamespace(method="GET")
    logo_read.get_permissions()
    assert logo_read.required_permission == "gym.view"

    logo_write = GymLogoView()
    logo_write.request = SimpleNamespace(method="PUT")
    logo_write.get_permissions()
    assert logo_write.required_permission == "gym.update"


def test_gym_and_settings_permissions_follow_owner_policy() -> None:
    gym_read = GymProfileView()
    gym_read.request = SimpleNamespace(method="GET")
    gym_read.get_permissions()
    assert gym_read.required_permission == "gym.view"

    gym_write = GymProfileView()
    gym_write.request = SimpleNamespace(method="PATCH")
    gym_write.get_permissions()
    assert gym_write.required_permission == "gym.update"

    settings_read = GymSettingsView()
    settings_read.request = SimpleNamespace(method="GET")
    assert [type(item) for item in settings_read.get_permissions()] == [
        IsAuthenticated,
        RequiresPermission,
    ]
    assert settings_read.required_permission == "settings.view"

    settings_write = GymSettingsView()
    settings_write.request = SimpleNamespace(method="PATCH")
    assert [type(item) for item in settings_write.get_permissions()] == [
        IsAuthenticated,
        RequiresPermission,
        IsActiveOwner,
    ]
    assert settings_write.required_permission == "settings.manage"


def test_printer_footer_accepts_three_lines_and_rejects_limits() -> None:
    footer = "ကျေးဇူးတင်ပါသည်\nThank you\nSee you soon"
    accepted = GymSettingsUpdateSerializer(
        data={
            "printer_enabled": True,
            "printer_print_logo": False,
            "printer_print_address": True,
            "printer_print_phone": True,
            "printer_footer": footer + "\n",
        }
    )
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["printer_footer"] == footer + "\n"
    assert accepted.validated_data["printer_enabled"] is True

    windows = GymSettingsUpdateSerializer(data={"printer_footer": "Line 1\r\nLine 2"})
    assert windows.is_valid(), windows.errors
    assert windows.validated_data["printer_footer"] == "Line 1\nLine 2"

    too_long = GymSettingsUpdateSerializer(data={"printer_footer": "က" * 121})
    assert not too_long.is_valid()
    assert "printer_footer" in too_long.errors

    four_lines = GymSettingsUpdateSerializer(data={"printer_footer": "a\nb\nc\nd"})
    assert not four_lines.is_valid()
    assert "printer_footer" in four_lines.errors

    extra_break = GymSettingsUpdateSerializer(data={"printer_footer": "a\nb\nc\n\n"})
    assert not extra_break.is_valid()
    assert "printer_footer" in extra_break.errors

    fuzzy = GymSettingsUpdateSerializer(data={"printer_enabled": "maybe"})
    assert not fuzzy.is_valid()
    assert "printer_enabled" in fuzzy.errors
    unknown = GymSettingsUpdateSerializer(data={"printer_paper": "80mm"})
    assert not unknown.is_valid()
    assert "printer_paper" in unknown.errors

    try:
        prepare_settings_changes({"printer_enabled": 1})
    except Exception as exc:
        assert "printer_enabled" in getattr(exc, "detail", {})
    else:
        raise AssertionError("integer printer toggle was accepted")
