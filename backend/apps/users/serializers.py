from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data: Any) -> dict[str, object]:
        unknown = set(data) - set(self.fields) if isinstance(data, Mapping) else set()
        if unknown:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown)}
            )
        return cast(dict[str, object], super().to_internal_value(data))


class MemberLoginInputSerializer(StrictSerializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    device_name = serializers.CharField(required=False, allow_blank=True, max_length=255)


class LoginInputSerializer(StrictSerializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    mfa_code = serializers.CharField(required=False, write_only=True)
    device_name = serializers.CharField(required=False, allow_blank=True, max_length=255)


class RefreshInputSerializer(StrictSerializer):
    refresh_token = serializers.CharField(trim_whitespace=False, write_only=True)


class LogoutInputSerializer(StrictSerializer):
    refresh_token = serializers.CharField(trim_whitespace=False, write_only=True)


class SelectGymInputSerializer(StrictSerializer):
    gym_membership_id = serializers.UUIDField()
    gym_selection_ticket = serializers.CharField(
        required=False, trim_whitespace=False, write_only=True
    )


class MFAChallengeInputSerializer(StrictSerializer):
    challenge_token = serializers.CharField(required=False, write_only=True)
    code = serializers.RegexField(r"^\d{6}$", write_only=True)


class MFAEnrollInputSerializer(StrictSerializer):
    current_password = serializers.CharField(trim_whitespace=False, write_only=True)


class MFAConfirmInputSerializer(StrictSerializer):
    enrollment_token = serializers.CharField(trim_whitespace=False, write_only=True)
    code = serializers.RegexField(r"^\d{6}$", write_only=True)
