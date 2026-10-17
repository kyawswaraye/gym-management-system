from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from rest_framework import serializers

from apps.audit.models import Outcome
from apps.audit.services import TENANT_AUDIT_SCOPE_GYM, TENANT_AUDIT_SCOPE_SELF
from apps.users.serializers import StrictSerializer


class PlatformAuditQuerySerializer(serializers.Serializer):
    target_gym_id = serializers.UUIDField(required=False)
    action = serializers.CharField(required=False, max_length=100)
    outcome = serializers.ChoiceField(required=False, choices=Outcome.values)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: ["Unknown field."] for key in sorted(unknown)})
        return cast(dict[str, Any], super().to_internal_value(data))


class PlatformAuditItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    actor_user_id = serializers.UUIDField()
    actor_email = serializers.EmailField()
    target_gym_id = serializers.UUIDField(allow_null=True)
    target_gym_code = serializers.CharField(allow_null=True)
    target_type = serializers.CharField()
    target_id = serializers.UUIDField(allow_null=True)
    action = serializers.CharField()
    module = serializers.CharField()
    outcome = serializers.CharField()
    old_values = serializers.JSONField()
    new_values = serializers.JSONField()
    request_id = serializers.CharField()
    occurred_at = serializers.DateTimeField()


class PlatformAuditMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class PlatformAuditListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = PlatformAuditItemSerializer(many=True)
    meta = PlatformAuditMetaSerializer()
    message = serializers.CharField()


class TenantAuditQuerySerializer(StrictSerializer):
    action = serializers.CharField(required=False, max_length=100)
    module = serializers.CharField(required=False, max_length=50)
    outcome = serializers.ChoiceField(required=False, choices=Outcome.values)
    target_type = serializers.CharField(required=False, max_length=100)
    target_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)

    def to_internal_value(self, data: Any) -> dict[str, Any]:
        errors: dict[str, list[str]] = {}
        if isinstance(data, Mapping):
            for field in sorted(set(data) - set(self.fields)):
                errors[str(field)] = ["This field is not allowed."]
            getlist = getattr(data, "getlist", None)
            if callable(getlist):
                for field in sorted(set(data)):
                    values = getlist(field)
                    if isinstance(values, list) and len(values) > 1:
                        errors.setdefault(str(field), []).append("Duplicate value.")
        if errors:
            raise serializers.ValidationError(errors)
        return cast(dict[str, Any], super().to_internal_value(data))


class TenantAuditItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    actor_membership_id = serializers.UUIDField(allow_null=True)
    impersonator_membership_id = serializers.UUIDField(allow_null=True)
    action = serializers.CharField()
    module = serializers.CharField()
    target_type = serializers.CharField()
    target_id = serializers.UUIDField()
    old_values = serializers.JSONField()
    new_values = serializers.JSONField()
    request_id = serializers.CharField()
    outcome = serializers.CharField()
    occurred_at = serializers.DateTimeField()


class TenantAuditMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)
    scope = serializers.ChoiceField(
        choices=(
            (TENANT_AUDIT_SCOPE_GYM, TENANT_AUDIT_SCOPE_GYM),
            (TENANT_AUDIT_SCOPE_SELF, TENANT_AUDIT_SCOPE_SELF),
        )
    )


class TenantAuditListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = TenantAuditItemSerializer(many=True)
    meta = TenantAuditMetaSerializer()
    message = serializers.CharField()
