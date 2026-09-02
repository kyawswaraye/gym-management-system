from __future__ import annotations

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {field: ["This field is not allowed."] for field in sorted(unknown)}
            )
        return attrs


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
