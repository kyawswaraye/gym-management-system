"""Serializers for the member-self API.

Responses omit staff-only fields. Writes accept only the documented allowlist.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, cast

from rest_framework import serializers

from apps.members.models import Member, Membership, MembershipPlan
from apps.members.normalization import normalize_member_email, normalize_member_name
from apps.members.serializers import (
    CheckInCollectionMetaSerializer,
    CheckInSerializer,
    CheckInSummarySerializer,
    MemberAddressSerializer,
    MemberGenderField,
    MemberPaymentHistoryItemSerializer,
    MemberPaymentHistoryMetaSerializer,
    OpaqueCursorField,
    _optional_phone,
)
from apps.trainers.models import MemberServicePackage
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


class MemberSelfProfileSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_null=True)
    email = serializers.EmailField(allow_null=True)
    gender = serializers.ChoiceField(choices=Member.Gender.choices, allow_null=True)
    date_of_birth = serializers.DateField(allow_null=True)
    address = MemberAddressSerializer()
    join_date = serializers.DateField()
    emergency_contact_name = serializers.CharField(allow_blank=True)
    emergency_contact_phone = serializers.CharField(allow_blank=True)
    profile_photo_asset_id = serializers.UUIDField(allow_null=True)
    is_pt_client = serializers.SerializerMethodField()
    status = serializers.ChoiceField(choices=Member.Status.choices)
    archived_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_is_pt_client(self, obj: object) -> bool:
        from apps.members.serializers import member_is_pt_client

        return member_is_pt_client(obj)


class MemberSelfUpdateSerializer(StrictSerializer):
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    gender = MemberGenderField()
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    address = MemberAddressSerializer(required=False, allow_null=True)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    emergency_contact_phone = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=32
    )

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError(
                {"non_field_errors": ["This request must change at least one field."]}
            )
        return attrs

    def validate_phone(self, value: str | None) -> str | None:
        return _optional_phone(value)

    def validate_email(self, value: str | None) -> str | None:
        return normalize_member_email(value)

    def validate_emergency_contact_name(self, value: str) -> str:
        return normalize_member_name(value)

    def validate_emergency_contact_phone(self, value: str | None) -> str:
        return _optional_phone(value) or ""

    def validate_address(self, value: dict[str, str] | None) -> dict[str, str] | None:
        return value


class MemberSelfMembershipSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()
    plan_code = serializers.CharField()
    plan_name = serializers.CharField()
    duration_value = serializers.IntegerField()
    duration_unit = serializers.ChoiceField(choices=MembershipPlan.DurationUnit.choices)
    start_date = serializers.DateField()
    expiry_date = serializers.DateField()
    list_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    final_amount = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField()
    payment_status = serializers.ChoiceField(choices=Membership.PaymentStatus.choices)
    status = serializers.ChoiceField(choices=Membership.Status.choices)
    note = serializers.CharField(allow_blank=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancellation_reason = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class MemberSelfPaymentSummarySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    currency = serializers.CharField(max_length=3)
    total_charged = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    total_refunded = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    net_paid = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    outstanding_membership_balance = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )


class MemberSelfDashboardSerializer(serializers.Serializer):
    member = MemberSelfProfileSerializer()
    current_membership = MemberSelfMembershipSerializer(allow_null=True)
    days_remaining = serializers.IntegerField(allow_null=True, min_value=0)
    is_pt_client = serializers.SerializerMethodField()
    membership_history = MemberSelfMembershipSerializer(many=True)
    membership_history_has_more = serializers.BooleanField()
    payment_summary = MemberSelfPaymentSummarySerializer()
    check_in_summary = CheckInSummarySerializer()

    def get_is_pt_client(self, obj: dict[str, object]) -> bool:
        from apps.members.serializers import member_is_pt_client

        return member_is_pt_client(obj["member"])


class MemberSelfEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSelfProfileSerializer()
    message = serializers.CharField()


class MemberSelfDashboardEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSelfDashboardSerializer()
    message = serializers.CharField()


class MemberSelfMembershipEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSelfMembershipSerializer(allow_null=True)
    message = serializers.CharField()


class MemberSelfEmptyQuerySerializer(StrictSerializer):
    def to_internal_value(self, data: Any) -> dict[str, object]:
        _reject_duplicate_query_values(data)
        return super().to_internal_value(data)


class MemberSelfPageQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = OpaqueCursorField(required=False, allow_blank=False, max_length=2048)

    def to_internal_value(self, data: Any) -> dict[str, object]:
        _reject_duplicate_query_values(data)
        return super().to_internal_value(data)


class MemberSelfPackageQuerySerializer(MemberSelfPageQuerySerializer):
    status = serializers.ChoiceField(choices=MemberServicePackage.Status.choices, required=False)
    package_type = serializers.ChoiceField(choices=("PT", "CLASS"), required=False)


class MemberSelfPaymentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberPaymentHistoryItemSerializer(many=True)
    meta = MemberPaymentHistoryMetaSerializer()
    message = serializers.CharField()


class MemberSelfCheckInEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = CheckInSerializer(many=True)
    meta = CheckInCollectionMetaSerializer()
    message = serializers.CharField()


class MemberScheduleBookingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=("PT", "CLASS"))
    status = serializers.CharField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    trainer_display_name = serializers.CharField()
    class_name = serializers.CharField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    member_service_package_id = serializers.UUIDField(allow_null=True)
    unmetered = serializers.BooleanField()


class MemberScheduleOccurrenceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    class_name = serializers.CharField()
    trainer_display_name = serializers.CharField()
    location = serializers.CharField(allow_null=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    capacity = serializers.IntegerField()
    booked_count = serializers.IntegerField()
    member_booking_id = serializers.UUIDField(allow_null=True)


class MemberScheduleSerializer(serializers.Serializer):
    bookings = MemberScheduleBookingSerializer(many=True)
    occurrences = MemberScheduleOccurrenceSerializer(many=True)


class MemberScheduleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberScheduleSerializer()
    message = serializers.CharField()


class MemberScheduleQuerySerializer(StrictSerializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()

    def to_internal_value(self, data: Any) -> dict[str, object]:
        _reject_duplicate_query_values(data)
        return super().to_internal_value(data)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        start = cast(date, attrs["date_from"])
        end = cast(date, attrs["date_to"])
        span = (end - start).days + 1
        if span < 1 or span > 42:
            raise serializers.ValidationError(
                {"date_to": ["Enter an ordered range of 1 through 42 days."]}
            )
        return attrs


class MemberClassBookingWriteSerializer(StrictSerializer):
    occurrence_id = serializers.UUIDField()
    member_service_package_id = serializers.UUIDField()


class MemberClassBookingEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberScheduleBookingSerializer()
    message = serializers.CharField()


class MemberSelfPackageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    package_type = serializers.CharField()
    package_code = serializers.CharField()
    package_name = serializers.CharField()
    status = serializers.CharField()
    granted_sessions = serializers.IntegerField()
    remaining_sessions = serializers.IntegerField()
    valid_from = serializers.DateField()
    valid_until = serializers.DateField()
    complimentary = serializers.BooleanField()

    def to_representation(self, instance: object) -> dict[str, object]:
        from apps.trainers.serializers import member_package_payload

        payload = member_package_payload(instance)  # type: ignore[arg-type]
        return {
            "id": payload["id"],
            "package_type": payload["package_type"],
            "package_code": payload["package_code"],
            "package_name": payload["package_name"],
            "status": payload["status"],
            "granted_sessions": payload["granted_sessions"],
            "remaining_sessions": payload["remaining_sessions"],
            "valid_from": payload["valid_from"],
            "valid_until": payload["valid_until"],
            "complimentary": payload["complimentary"],
        }


class MemberSelfPackageEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSelfPackageSerializer(many=True)
    meta = MemberPaymentHistoryMetaSerializer()
    message = serializers.CharField()
