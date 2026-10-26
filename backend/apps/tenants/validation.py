from __future__ import annotations

import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework import serializers

GYM_PROFILE_FIELDS = (
    "name",
    "legal_name",
    "email",
    "phone",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country_code",
    "timezone",
    "currency",
)
SETTINGS_MUTABLE_FIELDS = (
    "week_starts_on",
    "allow_manual_member_numbers",
    "member_number_prefix",
    "receipt_number_prefix",
    "check_in_duplicate_window_seconds",
    "check_in_challenge_ttl_seconds",
    "check_in_challenge_max_uses",
    "membership_expiry_warning_days",
    "membership_expiry_email_enabled",
    "membership_expiry_push_enabled",
    "class_cancellation_cutoff_minutes",
    "class_no_show_consumes_session",
    "require_separate_finance_approval",
)
SETTINGS_BOOLEAN_FIELDS = frozenset(
    {
        "allow_manual_member_numbers",
        "membership_expiry_email_enabled",
        "membership_expiry_push_enabled",
        "class_no_show_consumes_session",
        "require_separate_finance_approval",
    }
)
SETTINGS_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "week_starts_on": (0, 6),
    "check_in_duplicate_window_seconds": (0, 86_400),
    "check_in_challenge_ttl_seconds": (30, 600),
    "check_in_challenge_max_uses": (1, 100),
    "class_cancellation_cutoff_minutes": (0, 2_147_483_647),
}
PROFILE_MAX_LENGTHS = {
    "name": 150,
    "legal_name": 200,
    "email": 254,
    "phone": 32,
    "address_line1": 255,
    "address_line2": 255,
    "city": 100,
    "region": 100,
    "postal_code": 20,
    "country_code": 2,
    "timezone": 64,
    "currency": 3,
}
_TIMEZONE_ALIASES = {"Asia/Rangoon": "Asia/Yangon"}
_PREFIX_RE = re.compile(r"^[A-Z0-9-]{1,20}$")
_WARNING_DAY_LIMIT = 366


def normalize_timezone(value: str) -> str:
    canonical = _TIMEZONE_ALIASES.get(value, value)
    try:
        ZoneInfo(canonical)
    except ZoneInfoNotFoundError as exc:
        raise serializers.ValidationError("Unsupported timezone.") from exc
    return canonical


ALLOWED_GYM_CURRENCIES = frozenset({"THB", "MMK"})


def normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if currency not in ALLOWED_GYM_CURRENCIES:
        raise serializers.ValidationError("Choose THB or MMK.")
    return currency


def normalize_prefix(value: str) -> str:
    prefix = value.strip()
    if _PREFIX_RE.fullmatch(prefix) is None:
        raise serializers.ValidationError("Use 1 to 20 uppercase letters, digits, or hyphens.")
    return prefix


def normalize_warning_days(value: Any) -> list[int]:
    if not isinstance(value, list) or len(value) > _WARNING_DAY_LIMIT:
        raise serializers.ValidationError("Provide at most 366 warning days.")
    days: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 365:
            raise serializers.ValidationError("Each warning day must be an integer from 0 to 365.")
        days.append(item)
    if len(set(days)) != len(days):
        raise serializers.ValidationError("Warning days must be unique.")
    return sorted(days)


def normalize_settings_value(field: str, value: Any) -> Any:
    if field in SETTINGS_INTEGER_RANGES:
        minimum, maximum = SETTINGS_INTEGER_RANGES[field]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise serializers.ValidationError(f"Enter a value from {minimum} to {maximum}.")
        return value
    if field in SETTINGS_BOOLEAN_FIELDS:
        if type(value) is not bool:
            raise serializers.ValidationError("Enter a true or false value.")
        if field == "require_separate_finance_approval" and value is not True:
            raise serializers.ValidationError(
                "Separate finance approval is required and cannot be disabled."
            )
        return value
    if field in {"member_number_prefix", "receipt_number_prefix"}:
        return normalize_prefix(str(value))
    if field == "membership_expiry_warning_days":
        return normalize_warning_days(value)
    raise serializers.ValidationError("This field is not allowed.")


def _field_error(field: str, exc: serializers.ValidationError) -> serializers.ValidationError:
    return serializers.ValidationError({field: exc.detail})


def prepare_profile_changes(data: dict[str, Any]) -> dict[str, str]:
    """Keep only tenant-owned profile fields and normalize them."""

    prepared: dict[str, str] = {}
    for field in GYM_PROFILE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str):
            raise serializers.ValidationError({field: ["Enter a valid value."]})
        value = value.strip()
        if len(value) > PROFILE_MAX_LENGTHS[field]:
            limit = PROFILE_MAX_LENGTHS[field]
            raise serializers.ValidationError(
                {field: [f"Ensure this field has no more than {limit} characters."]}
            )
        if field == "name" and not value:
            raise serializers.ValidationError({field: ["This field may not be blank."]})
        if field == "timezone":
            try:
                value = normalize_timezone(value)
            except serializers.ValidationError as exc:
                raise _field_error(field, exc) from exc
        elif field == "currency":
            try:
                value = normalize_currency(value)
            except serializers.ValidationError as exc:
                raise _field_error(field, exc) from exc
        elif field == "country_code" and value:
            value = value.upper()
            if re.fullmatch(r"[A-Z]{2}", value) is None:
                raise serializers.ValidationError({field: ["Enter a two-letter country code."]})
        elif field == "email" and value:
            try:
                validate_email(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({field: list(exc.messages)}) from exc
        prepared[field] = value
    return prepared


def prepare_settings_changes(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only typed operational settings and reject policy bypasses."""

    prepared: dict[str, Any] = {}
    for field in SETTINGS_MUTABLE_FIELDS:
        if field not in data:
            continue
        try:
            prepared[field] = normalize_settings_value(field, data[field])
        except serializers.ValidationError as exc:
            raise _field_error(field, exc) from exc
    return prepared
