"""Member device request and response shapes. The token is write-only."""

from __future__ import annotations

import re
from typing import Any

from rest_framework import serializers

from apps.users.serializers import StrictSerializer

_TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,4096}$")
_VERSION_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,32}$")
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")


def _token(value: str) -> str:
    if _TOKEN_RE.fullmatch(value) is None:
        raise serializers.ValidationError("Enter a valid push token.")
    return value


def _version(value: str) -> str:
    if _VERSION_RE.fullmatch(value) is None:
        raise serializers.ValidationError("Enter a valid version.")
    return value


def _name(value: str) -> str:
    if _NAME_RE.fullmatch(value) is None:
        raise serializers.ValidationError("Enter a valid device name.")
    return value


class MemberDeviceWriteSerializer(StrictSerializer):
    platform = serializers.ChoiceField(choices=("ANDROID", "IOS"))
    installation_id = serializers.UUIDField()
    push_consent = serializers.BooleanField()
    push_token = serializers.CharField(
        required=False,
        allow_null=True,
        trim_whitespace=False,
        max_length=4096,
    )
    app_version = serializers.CharField(min_length=1, max_length=32, trim_whitespace=True)
    os_version = serializers.CharField(min_length=1, max_length=32, trim_whitespace=True)
    device_name = serializers.CharField(
        required=False,
        allow_null=True,
        min_length=1,
        max_length=64,
        trim_whitespace=True,
    )

    def validate_push_token(self, value: str | None) -> str | None:
        if value is None:
            return None
        return _token(value)

    def validate_app_version(self, value: str) -> str:
        return _version(value)

    def validate_os_version(self, value: str) -> str:
        return _version(value)

    def validate_device_name(self, value: str | None) -> str | None:
        if value is None:
            return None
        return _name(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        consent = bool(attrs["push_consent"])
        if consent and not attrs.get("push_token"):
            raise serializers.ValidationError(
                {"push_token": ["A push token is required when consent is on."]}
            )
        if not consent and attrs.get("push_token"):
            raise serializers.ValidationError(
                {"push_token": ["Omit the push token when consent is off."]}
            )
        return attrs


class MemberDevicePatchSerializer(StrictSerializer):
    push_consent = serializers.BooleanField(required=False)
    push_token = serializers.CharField(
        required=False,
        allow_null=True,
        trim_whitespace=False,
        max_length=4096,
    )
    app_version = serializers.CharField(
        required=False, min_length=1, max_length=32, trim_whitespace=True
    )
    os_version = serializers.CharField(
        required=False, min_length=1, max_length=32, trim_whitespace=True
    )
    device_name = serializers.CharField(
        required=False,
        allow_null=True,
        min_length=1,
        max_length=64,
        trim_whitespace=True,
    )

    def validate_push_token(self, value: str | None) -> str | None:
        if value is None:
            return None
        return _token(value)

    def validate_app_version(self, value: str) -> str:
        return _version(value)

    def validate_os_version(self, value: str) -> str:
        return _version(value)

    def validate_device_name(self, value: str | None) -> str | None:
        if value is None:
            return None
        return _name(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError(
                {"non_field_errors": ["At least one field is required."]}
            )
        consent = attrs.get("push_consent")
        token = attrs.get("push_token")
        if consent is True and "push_token" in attrs and not token:
            raise serializers.ValidationError(
                {"push_token": ["A push token is required when consent is on."]}
            )
        if consent is False and token:
            raise serializers.ValidationError(
                {"push_token": ["Omit the push token when consent is off."]}
            )
        if consent is None and "push_token" in attrs and token is None:
            raise serializers.ValidationError(
                {"push_token": ["A push token cannot be cleared without turning consent off."]}
            )
        return attrs


class MemberDeviceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    platform = serializers.ChoiceField(choices=("ANDROID", "IOS"))
    installation_id = serializers.UUIDField()
    push_consent = serializers.BooleanField()
    push_token_present = serializers.BooleanField()
    app_version = serializers.CharField()
    os_version = serializers.CharField()
    device_name = serializers.CharField(allow_null=True)
    last_seen_at = serializers.DateTimeField()
    revoked_at = serializers.DateTimeField(allow_null=True)


class MemberDeviceEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberDeviceSerializer()
    message = serializers.CharField()
