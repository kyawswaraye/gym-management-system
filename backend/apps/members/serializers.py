from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.members.normalization import (
    ADDRESS_KEYS,
    address_for_response,
    canonical_address,
    normalize_member_email,
    normalize_member_gender,
    normalize_member_name,
    normalize_member_phone,
)
from apps.users.serializers import StrictSerializer

_PHONE_ERROR = "Enter a phone number using digits and an optional leading +."


def _member_name(value: str) -> str:
    cleaned = normalize_member_name(value)
    if not cleaned:
        raise serializers.ValidationError("This field may not be blank.")
    return cleaned


def member_is_pt_client(_member: object) -> bool:
    """False until Phase 5 derives the badge from an active trainer assignment."""

    return False


def _optional_phone(value: str | None) -> str | None:
    try:
        return normalize_member_phone(value)
    except ValueError as exc:
        raise serializers.ValidationError(_PHONE_ERROR) from exc


class MemberGenderField(serializers.ChoiceField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("choices", Member.Gender.choices)
        kwargs.setdefault("allow_null", True)
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, str):
            try:
                data = normalize_member_gender(data)
            except ValueError as exc:
                raise serializers.ValidationError("Select a valid gender.") from exc
        if data is None and self.allow_null:
            return None
        return super().to_internal_value(data)


class MemberAddressSerializer(StrictSerializer):
    address_line1 = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    address_line2 = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=255
    )
    city = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    region = serializers.CharField(required=False, allow_blank=True, default="", max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=20)
    country_code = serializers.CharField(required=False, allow_blank=True, default="", max_length=2)

    def validate(self, attrs: dict[str, object]) -> dict[str, str]:
        attrs = super().validate(attrs)
        payload = {key: "" for key in ADDRESS_KEYS}
        payload.update({key: value for key, value in attrs.items() if isinstance(value, str)})
        try:
            return canonical_address(payload)
        except ValueError as exc:
            message = str(exc)
            field = "country_code" if "country" in message else "non_field_errors"
            raise serializers.ValidationError({field: [message]}) from exc

    def to_representation(self, instance: object) -> dict[str, str]:
        return address_for_response(instance)


class MemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField(allow_blank=True)
    normalized_full_name = serializers.CharField()
    phone = serializers.CharField(allow_null=True)
    email = serializers.EmailField(allow_null=True)
    gender = serializers.ChoiceField(choices=Member.Gender.choices, allow_null=True)
    date_of_birth = serializers.DateField(allow_null=True)
    address = MemberAddressSerializer()
    join_date = serializers.DateField()
    emergency_contact_name = serializers.CharField(allow_blank=True)
    emergency_contact_phone = serializers.CharField(allow_blank=True)
    notes = serializers.CharField(allow_blank=True)
    profile_photo_asset_id = serializers.UUIDField(allow_null=True)
    linked_user_id = serializers.UUIDField(allow_null=True)
    is_pt_client = serializers.SerializerMethodField()
    authz_version = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Member.Status.choices)
    archived_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_is_pt_client(self, obj: object) -> bool:
        return member_is_pt_client(obj)


class MemberCreateSerializer(StrictSerializer):
    member_number = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=32
    )
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    gender = MemberGenderField()
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    address = MemberAddressSerializer(required=False)
    join_date = serializers.DateField()
    emergency_contact_name = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=150
    )
    emergency_contact_phone = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=32
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)
    profile_photo_asset_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        date_of_birth = cast(date | None, attrs.get("date_of_birth"))
        join_date = cast(date, attrs["join_date"])
        if date_of_birth is not None and date_of_birth > join_date:
            raise serializers.ValidationError(
                {"date_of_birth": ["Date of birth cannot be after the join date."]}
            )
        return attrs

    def validate_member_number(self, value: str) -> str:
        return value.strip()

    def validate_first_name(self, value: str) -> str:
        return _member_name(value)

    def validate_last_name(self, value: str) -> str:
        return _member_name(value)

    def validate_phone(self, value: str | None) -> str | None:
        return _optional_phone(value)

    def validate_email(self, value: str | None) -> str | None:
        return normalize_member_email(value)

    def validate_emergency_contact_name(self, value: str) -> str:
        return normalize_member_name(value)

    def validate_emergency_contact_phone(self, value: str) -> str:
        return _optional_phone(value) or ""

    def validate_notes(self, value: str) -> str:
        return value.strip()


class MemberUpdateSerializer(StrictSerializer):
    first_name = serializers.CharField(required=False, max_length=100)
    last_name = serializers.CharField(required=False, max_length=100)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    gender = MemberGenderField()
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    address = MemberAddressSerializer(required=False)
    join_date = serializers.DateField(required=False)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    emergency_contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    profile_photo_asset_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        date_of_birth = cast(date | None, attrs.get("date_of_birth"))
        join_date = cast(date | None, attrs.get("join_date"))
        if date_of_birth is not None and join_date is not None and date_of_birth > join_date:
            raise serializers.ValidationError(
                {"date_of_birth": ["Date of birth cannot be after the join date."]}
            )
        return attrs

    def validate_first_name(self, value: str) -> str:
        return _member_name(value)

    def validate_last_name(self, value: str) -> str:
        return _member_name(value)

    def validate_phone(self, value: str | None) -> str | None:
        return _optional_phone(value)

    def validate_email(self, value: str | None) -> str | None:
        return normalize_member_email(value)

    def validate_emergency_contact_name(self, value: str) -> str:
        return normalize_member_name(value)

    def validate_emergency_contact_phone(self, value: str) -> str:
        return _optional_phone(value) or ""

    def validate_notes(self, value: str) -> str:
        return value.strip()


class MemberArchiveSerializer(StrictSerializer):
    """Archive accepts no client fields."""


class MemberListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=Member.Status.choices, required=False)
    search = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        trim_whitespace=True,
    )
    joined_from = serializers.DateField(required=False)
    joined_to = serializers.DateField(required=False)
    is_pt_client = serializers.BooleanField(required=False, allow_null=True)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        joined_from = cast(date | None, attrs.get("joined_from"))
        joined_to = cast(date | None, attrs.get("joined_to"))
        if joined_from and joined_to and joined_from > joined_to:
            raise serializers.ValidationError({"joined_to": ["Must be on or after joined_from."]})
        return attrs


class MemberCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class MemberEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSerializer()
    message = serializers.CharField()


class MemberListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSerializer(many=True)
    meta = MemberCollectionMetaSerializer()
    message = serializers.CharField()


def _required_text(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise serializers.ValidationError("This field may not be blank.")
    return cleaned


def _plan_code(value: str) -> str:
    code = _required_text(value)
    if any(character.isspace() for character in code):
        raise serializers.ValidationError("Enter a code without spaces.")
    return code


class PlanPriceField(serializers.DecimalField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if isinstance(data, float):
            raise serializers.ValidationError("Price must be a decimal string.")
        if isinstance(data, str) and "." in data and len(data.split(".", 1)[1]) > 2:
            raise serializers.ValidationError(
                "Ensure that there are no more than 2 decimal places."
            )
        return super().to_internal_value(data)


class MembershipPlanSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    duration_value = serializers.IntegerField(min_value=1)
    duration_unit = serializers.ChoiceField(choices=MembershipPlan.DurationUnit.choices)
    price = PlanPriceField()
    currency = serializers.CharField()
    is_active = serializers.BooleanField()
    archived_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class MembershipPlanCreateSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=4000
    )
    duration_value = serializers.IntegerField(min_value=1, max_value=2147483647)
    duration_unit = serializers.ChoiceField(choices=MembershipPlan.DurationUnit.choices)
    price = PlanPriceField()

    def validate_code(self, value: str) -> str:
        return _plan_code(value)

    def validate_name(self, value: str) -> str:
        return _required_text(value)

    def validate_description(self, value: str) -> str:
        return value.strip()


class MembershipPlanUpdateSerializer(StrictSerializer):
    code = serializers.CharField(required=False, max_length=32)
    name = serializers.CharField(required=False, max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    duration_value = serializers.IntegerField(required=False, min_value=1, max_value=2147483647)
    duration_unit = serializers.ChoiceField(
        choices=MembershipPlan.DurationUnit.choices, required=False
    )
    price = PlanPriceField(required=False)

    def validate(self, attrs: dict[str, object]) -> dict[str, object]:
        attrs = super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs

    def validate_code(self, value: str) -> str:
        return _plan_code(value)

    def validate_name(self, value: str) -> str:
        return _required_text(value)

    def validate_description(self, value: str) -> str:
        return value.strip()


class MembershipPlanArchiveSerializer(StrictSerializer):
    """Archive accepts no client fields."""


class MembershipPlanListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, max_length=2048)


class MembershipPlanCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class MembershipPlanEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MembershipPlanSerializer()
    message = serializers.CharField()


class MembershipPlanListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MembershipPlanSerializer(many=True)
    meta = MembershipPlanCollectionMetaSerializer()
    message = serializers.CharField()


class ExactMoneyField(serializers.DecimalField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if isinstance(data, float):
            raise serializers.ValidationError("Amount must be a decimal string.")
        if isinstance(data, str) and "." in data and len(data.split(".", 1)[1]) > 2:
            raise serializers.ValidationError(
                "Ensure that there are no more than 2 decimal places."
            )
        return super().to_internal_value(data)


class MembershipSerializer(serializers.Serializer):
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
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    cancellation_reason = serializers.CharField(allow_blank=True)
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class MembershipCreateSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()
    start_date = serializers.DateField()
    discount_amount = ExactMoneyField(required=False, default=Decimal("0"))
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)

    def validate_note(self, value: str) -> str:
        return value.strip()


class MembershipUpdateSerializer(StrictSerializer):
    note = serializers.CharField(allow_blank=True, max_length=4000)

    def validate_note(self, value: str) -> str:
        return value.strip()


class MembershipCancelSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500)

    def validate_reason(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class MembershipCommandSerializer(StrictSerializer):
    """Suspend and resume accept no client fields."""


class MembershipListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=Membership.Status.choices, required=False)
    member_id = serializers.UUIDField(required=False)
    payment_status = serializers.ChoiceField(
        choices=Membership.PaymentStatus.choices, required=False
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class MembershipExpiryQuerySerializer(StrictSerializer):
    bucket = serializers.ChoiceField(
        choices=(
            "expired",
            "today",
            "within_3_days",
            "within_7_days",
            "within_30_days",
        )
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class MembershipCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class MembershipEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MembershipSerializer()
    message = serializers.CharField()


class MembershipListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MembershipSerializer(many=True)
    meta = MembershipCollectionMetaSerializer()
    message = serializers.CharField()


class MemberSummaryQuerySerializer(StrictSerializer):
    """Member summary accepts no query parameters."""


class PaymentSummarySerializer(serializers.Serializer):
    available = serializers.BooleanField()
    currency = serializers.CharField(max_length=3)
    total_charged = serializers.DecimalField(max_digits=18, decimal_places=2)
    total_refunded = serializers.DecimalField(max_digits=18, decimal_places=2)
    net_paid = serializers.DecimalField(max_digits=18, decimal_places=2)
    outstanding_membership_balance = serializers.DecimalField(max_digits=18, decimal_places=2)


class CheckInSummarySerializer(serializers.Serializer):
    available = serializers.BooleanField()


class MemberSummarySerializer(serializers.Serializer):
    member = MemberSerializer()
    current_membership = MembershipSerializer(allow_null=True)
    days_remaining = serializers.IntegerField(allow_null=True, min_value=0)
    is_pt_client = serializers.SerializerMethodField()
    membership_history = MembershipSerializer(many=True)
    membership_history_has_more = serializers.BooleanField()
    payment_summary = PaymentSummarySerializer()
    check_in_summary = CheckInSummarySerializer()

    def get_is_pt_client(self, obj: dict[str, object]) -> bool:
        return member_is_pt_client(obj["member"])


class MemberSummaryEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberSummarySerializer()
    message = serializers.CharField()


class ReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500)


class RefundSerializer(ReasonSerializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))


class PaymentSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.display_name", read_only=True)
    receipt_number = serializers.CharField(source="receipt.receipt_number", read_only=True)

    class Meta:
        model = Payment
        exclude = ("gym",)
        read_only_fields = "__all__"


class PaymentCreateSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    membership_id = serializers.UUIDField(required=False, allow_null=True)
    purpose = serializers.ChoiceField(choices=Payment.Purpose.choices)
    description = serializers.CharField(max_length=255)
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(min_length=3, max_length=3)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    business_date = serializers.DateField()
    received_at = serializers.DateTimeField()
    reference_number = serializers.CharField(required=False, allow_blank=True, max_length=100)


class ReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receipt
        exclude = ("gym", "created_by_membership_id")
        read_only_fields = "__all__"
