from __future__ import annotations

import re
from typing import Any

from rest_framework import serializers

from apps.tenants.delegation import PROTECTED_ROLE_KEYS
from apps.users.serializers import StrictSerializer

_ROLE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


def _role_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise serializers.ValidationError("This field may not be blank.")
    return name


def _role_key(value: str) -> str:
    if not _ROLE_KEY.fullmatch(value):
        raise serializers.ValidationError("Enter an uppercase role key.")
    if value in PROTECTED_ROLE_KEYS:
        raise serializers.ValidationError("This role key is reserved.")
    return value


class RoleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    is_active = serializers.BooleanField()
    is_protected = serializers.BooleanField()
    is_template = serializers.BooleanField()
    source_template_id = serializers.UUIDField(allow_null=True)
    source_template_version = serializers.IntegerField(allow_null=True, min_value=1)
    permission_codes = serializers.ListField(child=serializers.CharField())
    permission_count = serializers.IntegerField(min_value=0)
    assignment_count = serializers.IntegerField(min_value=0)
    created_by_membership_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class RoleCreateSerializer(StrictSerializer):
    key = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, max_length=255)
    is_active = serializers.BooleanField(required=False)

    def validate_key(self, value: str) -> str:
        return _role_key(value)

    def validate_name(self, value: str) -> str:
        return _role_name(value)

    def validate_description(self, value: str) -> str:
        return value.strip()


class RoleUpdateSerializer(StrictSerializer):
    key = serializers.CharField(required=False, max_length=32)
    name = serializers.CharField(required=False, max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, max_length=255)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one role field is required.")
        return attrs

    def validate_key(self, value: str) -> str:
        return _role_key(value)

    def validate_name(self, value: str) -> str:
        return _role_name(value)

    def validate_description(self, value: str) -> str:
        return value.strip()


class RolePermissionReplaceSerializer(StrictSerializer):
    permission_codes = serializers.ListField(
        child=serializers.CharField(max_length=100),
        allow_empty=True,
        max_length=200,
    )

    def validate_permission_codes(self, value: list[Any]) -> list[str]:
        codes = [str(code).strip() for code in value]
        if any(not code for code in codes):
            raise serializers.ValidationError("Permission codes must not be blank.")
        if len(codes) != len(set(codes)):
            raise serializers.ValidationError("Permission codes must be unique.")
        return codes


class MembershipRoleReplaceSerializer(StrictSerializer):
    role_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=20)

    def validate_role_ids(self, value: list[Any]) -> list[Any]:
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Role ids must be unique.")
        return value


class MembershipRoleSerializer(serializers.Serializer):
    gym_membership_id = serializers.UUIDField()
    roles = RoleSerializer(many=True)
    authz_version = serializers.IntegerField(min_value=1)


class PermissionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    module = serializers.CharField()
    description = serializers.CharField()
    risk_level = serializers.CharField()
    is_active = serializers.BooleanField()


class RoleArchiveSerializer(StrictSerializer):
    """Archive accepts no client fields."""


class CollectionQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)


class CollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class RoleListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = RoleSerializer(many=True)
    meta = CollectionMetaSerializer()
    message = serializers.CharField()


class RoleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = RoleSerializer()
    message = serializers.CharField()


class PermissionListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = PermissionSerializer(many=True)
    meta = CollectionMetaSerializer()
    message = serializers.CharField()


class MembershipRoleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MembershipRoleSerializer()
    message = serializers.CharField()
