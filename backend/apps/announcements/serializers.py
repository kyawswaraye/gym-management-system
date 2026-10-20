"""Strict announcement request and response shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from apps.users.serializers import StrictSerializer

_TYPES = ("EVENT", "PROMOTION", "GENERAL")
_AUDIENCES = (
    "ALL_ACTIVE_MEMBERS",
    "PT_CLIENTS",
    "EXPIRING_WITHIN_7_DAYS",
    "EXPIRING_WITHIN_30_DAYS",
)
_STATUSES = ("DRAFT", "SCHEDULED", "PUBLISHED", "CANCELLED", "EXPIRED")


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


def _plain(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise serializers.ValidationError("This field contains unsupported characters.")
    return value


class AnnouncementWriteSerializer(StrictSerializer):
    type = serializers.ChoiceField(choices=_TYPES)
    title = serializers.CharField(min_length=1, max_length=120, trim_whitespace=True)
    body = serializers.CharField(min_length=1, max_length=4000, trim_whitespace=True)
    audience = serializers.ChoiceField(choices=_AUDIENCES)
    starts_at = serializers.DateTimeField(required=False, allow_null=True)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_title(self, value: str) -> str:
        return _plain(value)

    def validate_body(self, value: str) -> str:
        if any(
            (ord(character) < 32 and character not in "\n\r\t") or ord(character) == 127
            for character in value
        ):
            raise serializers.ValidationError("This field contains unsupported characters.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        starts_at = attrs.get("starts_at")
        ends_at = attrs.get("ends_at")
        if starts_at is not None and ends_at is not None and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"ends_at": ["End time must be after the start time."]}
            )
        return attrs


class AnnouncementPatchSerializer(AnnouncementWriteSerializer):
    type = serializers.ChoiceField(choices=_TYPES, required=False)
    title = serializers.CharField(
        min_length=1, max_length=120, trim_whitespace=True, required=False
    )
    body = serializers.CharField(
        min_length=1, max_length=4000, trim_whitespace=True, required=False
    )
    audience = serializers.ChoiceField(choices=_AUDIENCES, required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError(
                {"non_field_errors": ["At least one field is required."]}
            )
        return super().validate(attrs)


class EmptyCommandSerializer(StrictSerializer):
    def to_internal_value(self, data: Any) -> dict[str, object]:
        if data in (None, ""):
            data = {}
        return super().to_internal_value(data)


class AnnouncementListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=_STATUSES, required=False, allow_blank=False)
    type = serializers.ChoiceField(choices=_TYPES, required=False, allow_blank=False)
    audience = serializers.ChoiceField(choices=_AUDIENCES, required=False, allow_blank=False)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")

    def to_internal_value(self, data: Any) -> dict[str, object]:
        _reject_duplicate_query_values(data)
        return super().to_internal_value(data)


class AnnouncementSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=_TYPES)
    title = serializers.CharField()
    body = serializers.CharField()
    audience = serializers.ChoiceField(choices=_AUDIENCES)
    starts_at = serializers.DateTimeField(allow_null=True)
    ends_at = serializers.DateTimeField(allow_null=True)
    status = serializers.ChoiceField(choices=_STATUSES)
    published_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    expired_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class AnnouncementCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class AnnouncementEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = AnnouncementSerializer()
    message = serializers.CharField()


class AnnouncementListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = AnnouncementSerializer(many=True)
    meta = AnnouncementCollectionMetaSerializer()
    message = serializers.CharField()
