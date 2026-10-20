"""HTTP contracts for class types, schedules, and occurrences."""

from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from rest_framework import serializers

from apps.classes.models import ClassBooking, ClassOccurrence, ClassSchedule, ClassType
from apps.classes.recurrence import MAX_INCLUSIVE_DAYS
from apps.users.serializers import StrictSerializer

_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_SPACE = re.compile(r"\s+")


def _collapsed(value: str) -> str:
    return _SPACE.sub(" ", value.strip())


class ClassTypeWriteSerializer(StrictSerializer):
    code = serializers.RegexField(_CODE, max_length=32)
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    description = serializers.CharField(
        required=False, allow_blank=True, default="", trim_whitespace=True
    )
    default_duration_minutes = serializers.IntegerField(min_value=1)
    default_capacity = serializers.IntegerField(min_value=1)

    def validate_name(self, value: str) -> str:
        collapsed = _collapsed(value)
        if collapsed == "":
            raise serializers.ValidationError("Enter a class type name.")
        return collapsed

    def validate_description(self, value: str) -> str:
        return _collapsed(value)


class ClassTypePatchSerializer(StrictSerializer):
    name = serializers.CharField(required=False, max_length=120, trim_whitespace=True)
    description = serializers.CharField(required=False, allow_blank=True, trim_whitespace=True)
    default_duration_minutes = serializers.IntegerField(required=False, min_value=1)
    default_capacity = serializers.IntegerField(required=False, min_value=1)
    is_active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        collapsed = _collapsed(value)
        if collapsed == "":
            raise serializers.ValidationError("Enter a class type name.")
        return collapsed

    def validate_description(self, value: str) -> str:
        return _collapsed(value)


class ClassTypeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    default_duration_minutes = serializers.IntegerField()
    default_capacity = serializers.IntegerField()
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ClassListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class ClassTypeEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassTypeSerializer()
    message = serializers.CharField()


class ClassTypeListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassTypeSerializer(many=True)
    meta = ClassListMetaSerializer()
    message = serializers.CharField()


class ClassTypeListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")
    is_active = serializers.ChoiceField(choices=("true", "false"), required=False)

    def validate_is_active(self, value: str) -> bool:
        return value == "true"


def class_type_payload(row: ClassType) -> dict[str, object]:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "description": row.description,
        "default_duration_minutes": row.default_duration_minutes,
        "default_capacity": row.default_capacity,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ClassScheduleWriteSerializer(StrictSerializer):
    class_type_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        min_length=1,
        max_length=7,
    )
    local_start_time = serializers.TimeField()
    local_end_time = serializers.TimeField()
    effective_from = serializers.DateField()
    effective_until = serializers.DateField()
    location = serializers.CharField(max_length=120, trim_whitespace=True)
    location_is_exclusive = serializers.BooleanField()
    capacity = serializers.IntegerField(min_value=1)

    def validate_weekdays(self, value: list[int]) -> list[int]:
        unique = sorted(set(value))
        if len(unique) != len(value):
            raise serializers.ValidationError("Weekdays must be distinct.")
        return unique

    def validate_location(self, value: str) -> str:
        collapsed = _collapsed(value)
        if collapsed == "":
            raise serializers.ValidationError("Enter a location.")
        return collapsed

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start: time = attrs["local_start_time"]
        end: time = attrs["local_end_time"]
        if end <= start:
            raise serializers.ValidationError(
                {"local_end_time": ["End time must be after the start time on the same day."]}
            )
        first: date = attrs["effective_from"]
        last: date = attrs["effective_until"]
        if last < first:
            raise serializers.ValidationError(
                {"effective_until": ["The end date is before the start date."]}
            )
        if (last - first).days + 1 > MAX_INCLUSIVE_DAYS:
            raise serializers.ValidationError(
                {"effective_until": ["The inclusive range is at most 366 days."]}
            )
        return attrs


class ClassSchedulePatchSerializer(ClassScheduleWriteSerializer):
    class_type_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        min_length=1,
        max_length=7,
        required=False,
    )
    local_start_time = serializers.TimeField(required=False)
    local_end_time = serializers.TimeField(required=False)
    effective_from = serializers.DateField(required=False)
    effective_until = serializers.DateField(required=False)
    location = serializers.CharField(required=False, max_length=120, trim_whitespace=True)
    location_is_exclusive = serializers.BooleanField(required=False)
    capacity = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "local_start_time" in attrs and "local_end_time" in attrs:
            if attrs["local_end_time"] <= attrs["local_start_time"]:
                raise serializers.ValidationError(
                    {"local_end_time": ["End time must be after the start time on the same day."]}
                )
        if "effective_from" in attrs and "effective_until" in attrs:
            if attrs["effective_until"] < attrs["effective_from"]:
                raise serializers.ValidationError(
                    {"effective_until": ["The end date is before the start date."]}
                )
            span = (attrs["effective_until"] - attrs["effective_from"]).days + 1
            if span > MAX_INCLUSIVE_DAYS:
                raise serializers.ValidationError(
                    {"effective_until": ["The inclusive range is at most 366 days."]}
                )
        return attrs


class ClassScheduleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    class_type_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    weekdays = serializers.ListField(child=serializers.IntegerField())
    local_start_time = serializers.TimeField()
    local_end_time = serializers.TimeField()
    effective_from = serializers.DateField()
    effective_until = serializers.DateField()
    timezone = serializers.CharField()
    location = serializers.CharField()
    location_is_exclusive = serializers.BooleanField()
    capacity = serializers.IntegerField()
    status = serializers.CharField()
    skipped_local_dates = serializers.ListField(child=serializers.DateField())
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ClassScheduleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassScheduleSerializer()
    message = serializers.CharField()


class ClassScheduleListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassScheduleSerializer(many=True)
    meta = ClassListMetaSerializer()
    message = serializers.CharField()


class ClassScheduleListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")
    status = serializers.ChoiceField(choices=ClassSchedule.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    class_type_id = serializers.UUIDField(required=False)


def class_schedule_payload(row: ClassSchedule) -> dict[str, object]:
    return {
        "id": row.id,
        "class_type_id": row.class_type_id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "weekdays": list(row.weekdays),
        "local_start_time": row.local_start_time,
        "local_end_time": row.local_end_time,
        "effective_from": row.effective_from,
        "effective_until": row.effective_until,
        "timezone": row.timezone,
        "location": row.location,
        "location_is_exclusive": row.location_is_exclusive,
        "capacity": row.capacity,
        "status": row.status,
        "skipped_local_dates": list(row.skipped_local_dates),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ClassOccurrenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    class_schedule_id = serializers.UUIDField()
    class_type_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    location = serializers.CharField()
    location_is_exclusive = serializers.BooleanField()
    capacity = serializers.IntegerField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    local_date = serializers.DateField()
    status = serializers.CharField()
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    reason = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ClassOccurrenceEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassOccurrenceSerializer()
    message = serializers.CharField()


class ClassOccurrenceListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassOccurrenceSerializer(many=True)
    meta = ClassListMetaSerializer()
    message = serializers.CharField()


class ClassOccurrenceListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")
    status = serializers.ChoiceField(choices=ClassOccurrence.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    view = serializers.ChoiceField(choices=("calendar",), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        start = attrs.get("date_from")
        end = attrs.get("date_to")
        if start is not None and end is not None and end < start:
            raise serializers.ValidationError(
                {"date_to": ["The end date is before the start date."]}
            )
        if attrs.get("view") == "calendar":
            if start is None or end is None:
                raise serializers.ValidationError(
                    {"view": ["Calendar view requires date_from and date_to."]}
                )
            if (end - start).days + 1 > 42:
                raise serializers.ValidationError(
                    {"date_to": ["Calendar range is at most 42 days."]}
                )
        return attrs


class ClassOccurrenceCancelSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        collapsed = _collapsed(value)
        if collapsed == "":
            raise serializers.ValidationError("Enter a reason.")
        return collapsed


def class_occurrence_payload(row: ClassOccurrence) -> dict[str, object]:
    return {
        "id": row.id,
        "class_schedule_id": row.class_schedule_id,
        "class_type_id": row.class_type_id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "location": row.location,
        "location_is_exclusive": row.location_is_exclusive,
        "capacity": row.capacity,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "local_date": row.local_date,
        "status": row.status,
        "cancelled_at": row.cancelled_at,
        "cancelled_by_membership_id": row.cancelled_by_membership_id,
        "reason": row.reason,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ClassBookingWriteSerializer(StrictSerializer):
    class_occurrence_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    member_service_package_id = serializers.UUIDField(required=False, allow_null=True)
    unmetered = serializers.BooleanField(required=False, default=False)
    unmetered_reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_unmetered_reason(self, value: str) -> str:
        return _collapsed(value)


class ClassBookingCancelSerializer(StrictSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)

    def validate_reason(self, value: str) -> str:
        return _collapsed(value)


class ClassBookingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    class_occurrence_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    member_service_package_id = serializers.UUIDField(allow_null=True)
    unmetered = serializers.BooleanField()
    unmetered_reason = serializers.CharField()
    status = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    attended_at = serializers.DateTimeField(allow_null=True)
    attended_by_membership_id = serializers.UUIDField(allow_null=True)
    no_show_at = serializers.DateTimeField(allow_null=True)
    no_show_by_membership_id = serializers.UUIDField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    reason = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ClassBookingEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassBookingSerializer()
    message = serializers.CharField()


class ClassBookingListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ClassBookingSerializer(many=True)
    meta = ClassListMetaSerializer()
    message = serializers.CharField()


class ClassBookingListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")
    status = serializers.ChoiceField(choices=ClassBooking.Status.choices, required=False)
    member_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    view = serializers.ChoiceField(choices=["list", "calendar"], required=False, default="list")


def class_booking_payload(row: ClassBooking) -> dict[str, object]:
    occurrence = row.class_occurrence
    return {
        "id": row.id,
        "class_occurrence_id": row.class_occurrence_id,
        "member_id": row.member_id,
        "member_service_package_id": row.member_service_package_id,
        "unmetered": row.unmetered,
        "unmetered_reason": row.unmetered_reason,
        "status": row.status,
        "starts_at": occurrence.starts_at,
        "ends_at": occurrence.ends_at,
        "attended_at": row.attended_at,
        "attended_by_membership_id": row.attended_by_membership_id,
        "no_show_at": row.no_show_at,
        "no_show_by_membership_id": row.no_show_by_membership_id,
        "cancelled_at": row.cancelled_at,
        "cancelled_by_membership_id": row.cancelled_by_membership_id,
        "reason": row.reason,
        "created_by_membership_id": row.created_by_membership_id,
        "updated_by_membership_id": row.updated_by_membership_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
