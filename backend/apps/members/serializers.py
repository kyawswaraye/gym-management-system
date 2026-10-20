from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from django.utils import timezone
from rest_framework import serializers
from rest_framework.utils import html

from apps.members.checkin_services import aware_instant_text
from apps.members.models import CheckIn, Member, Membership, MembershipPlan, Payment
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


def member_is_pt_client(member: object) -> bool:
    """True when a same-gym active assignment covers the gym-local as-of date.

    List and detail reads annotate ``pt_client_badge`` so a page does not query
    once per member. A saved member without that annotation is checked once.
    """

    annotated = getattr(member, "pt_client_badge", None)
    if isinstance(annotated, bool):
        return annotated
    cached = getattr(member, "_pt_client_cached", None)
    if isinstance(cached, bool):
        return cached
    if not isinstance(member, Member) or member._state.adding or member.gym_id is None:
        return False
    from apps.members.services import gym_today
    from apps.trainers.selectors import member_has_active_assignment

    value = member_has_active_assignment(member, as_of=gym_today(member.gym))
    cast(Any, member)._pt_client_cached = value
    return value


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

    def validate_profile_photo_asset_id(self, value: object) -> None:
        if value is not None:
            raise serializers.ValidationError("Create the member before attaching a profile photo.")
        return None


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
    currently_checked_in = serializers.BooleanField()
    open_check_in_id = serializers.UUIDField(allow_null=True)
    last_check_in_at = serializers.DateTimeField(allow_null=True)
    last_check_out_at = serializers.DateTimeField(allow_null=True)


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


class OpaqueCursorField(serializers.CharField):
    """Reject a present blank cursor.

    QueryDict treats an optional non-blank CharField's empty value as omitted.
    A blank ``after`` is not an opaque cursor and must stay a 400.
    """

    def get_value(self, dictionary: Any) -> Any:
        if html.is_html_input(dictionary) and self.field_name in dictionary:
            value = dictionary[self.field_name]
            if isinstance(value, str) and (
                value == "" or (self.trim_whitespace and value.strip() == "")
            ):
                return ""
        return super().get_value(dictionary)


class MemberPaymentHistoryQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = OpaqueCursorField(required=False, allow_blank=False, max_length=2048)


class SameGymReceiptNumberField(serializers.CharField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs["allow_null"] = True
        kwargs["read_only"] = True
        kwargs["max_length"] = 64
        kwargs["source"] = "*"
        super().__init__(**kwargs)

    def to_representation(self, value: Payment) -> str | None:  # type: ignore[override]
        if "history_receipt_number" in value.__dict__:
            number = value.__dict__["history_receipt_number"]
            return number if isinstance(number, str) and number else None
        receipt = getattr(value, "receipt", None)
        if receipt is None or getattr(receipt, "gym_id", None) != value.gym_id:
            return None
        number = getattr(receipt, "receipt_number", None)
        if not isinstance(number, str):
            return None
        return number


class MemberPaymentHistoryItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    membership_id = serializers.UUIDField(allow_null=True)
    original_payment_id = serializers.UUIDField(allow_null=True)
    entry_type = serializers.ChoiceField(choices=Payment.EntryType.choices)
    purpose = serializers.ChoiceField(choices=Payment.Purpose.choices)
    description = serializers.CharField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True)
    currency = serializers.CharField(max_length=3)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    business_date = serializers.DateField()
    received_at = serializers.DateTimeField()
    status = serializers.ChoiceField(choices=Payment.Status.choices)
    receipt_number = SameGymReceiptNumberField()


class MemberPaymentHistoryMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class MemberPaymentHistoryEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = MemberPaymentHistoryItemSerializer(many=True)
    meta = MemberPaymentHistoryMetaSerializer()
    message = serializers.CharField()


class AwareDateTimeField(serializers.DateTimeField):
    def to_internal_value(self, value: Any) -> datetime:
        if isinstance(value, str) and not aware_instant_text(value):
            raise serializers.ValidationError("Enter a timezone-aware time.")
        parsed = super().to_internal_value(value)
        if timezone.is_naive(parsed):
            raise serializers.ValidationError("Enter a timezone-aware time.")
        return parsed


class CheckInCreateSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    occurred_at = AwareDateTimeField(required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class CheckInCheckoutSerializer(StrictSerializer):
    occurred_at = AwareDateTimeField(required=False)


class CheckInListQuerySerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False)
    open = serializers.ChoiceField(choices=("true", "false"), required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = OpaqueCursorField(required=False, allow_blank=False, max_length=2048)


class CheckInMemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class CheckInEligibilitySerializer(serializers.Serializer):
    eligible = serializers.BooleanField()
    backdated = serializers.BooleanField()
    membership_id = serializers.UUIDField()
    membership_status = serializers.CharField()
    plan_code = serializers.CharField()
    plan_name = serializers.CharField()
    start_date = serializers.DateField()
    expiry_date = serializers.DateField()
    evaluated_on = serializers.DateField()


class CheckInSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member = CheckInMemberSerializer()
    membership_id = serializers.UUIDField(allow_null=True)
    check_in_at = serializers.DateTimeField()
    check_out_at = serializers.DateTimeField(allow_null=True)
    method = serializers.ChoiceField(choices=CheckIn.Method.choices)
    status = serializers.SerializerMethodField()
    note = serializers.CharField()
    eligibility = CheckInEligibilitySerializer(source="eligibility_snapshot")

    def get_status(self, obj: CheckIn) -> str:
        return "CHECKED_OUT" if obj.check_out_at is not None else "OPEN"


class CheckInEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = CheckInSerializer()
    message = serializers.CharField()


class CheckInCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class CheckInListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = CheckInSerializer(many=True)
    meta = CheckInCollectionMetaSerializer()
    message = serializers.CharField()


class MemberCheckInScanSerializer(StrictSerializer):
    payload = serializers.CharField(trim_whitespace=False, max_length=128)


class TrainerCheckInScanSerializer(StrictSerializer):
    payload = serializers.CharField(trim_whitespace=False, max_length=128)
    member_id = serializers.UUIDField()


class CheckInChallengeCreateSerializer(StrictSerializer):
    display_location = serializers.CharField(max_length=80)


class CheckInChallengeIssuedSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    payload = serializers.CharField()
    display_location = serializers.CharField()
    issued_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    max_uses = serializers.IntegerField()
    status = serializers.CharField()


class CheckInChallengeEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = CheckInChallengeIssuedSerializer()
    message = serializers.CharField()
