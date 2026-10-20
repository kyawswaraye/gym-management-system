"""Member inbox serializers. Payloads and provider ids stay off the response."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from apps.announcements.serializers import (
    AnnouncementCollectionMetaSerializer,
    EmptyCommandSerializer,
)
from apps.users.serializers import StrictSerializer


def _reject_duplicate_query_values(data: Any) -> None:
    if not isinstance(data, Mapping):
        return
    getlist = getattr(data, "getlist", None)
    if not callable(getlist):
        return
    errors: dict[str, list[str]] = {}
    for field in sorted(set(data)):
        values = getlist(field)
        if isinstance(values, list) and len(values) > 1:
            errors[str(field)] = ["Duplicate value."]
    if errors:
        raise serializers.ValidationError(errors)


class MemberNotificationQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")

    def to_internal_value(self, data: Any) -> dict[str, object]:
        _reject_duplicate_query_values(data)
        return super().to_internal_value(data)


class MemberNotificationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=("MEMBERSHIP_EXPIRY", "ANNOUNCEMENT"))
    title = serializers.CharField()
    body = serializers.CharField()
    announcement_type = serializers.ChoiceField(
        choices=("EVENT", "PROMOTION", "GENERAL"),
        allow_null=True,
    )
    read_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class MemberNotificationEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberNotificationSerializer()
    message = serializers.CharField()


class MemberNotificationListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberNotificationSerializer(many=True)
    meta = AnnouncementCollectionMetaSerializer()
    message = serializers.CharField()


__all__ = [
    "EmptyCommandSerializer",
    "MemberNotificationEnvelopeSerializer",
    "MemberNotificationListEnvelopeSerializer",
    "MemberNotificationQuerySerializer",
    "MemberNotificationSerializer",
]
