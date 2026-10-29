from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.common.codes import GYM_CODE, OptionalPatternField
from apps.tenants.models import Gym, GymLogo, SubscriptionPlan
from apps.tenants.validation import (
    SETTINGS_INTEGER_RANGES,
    normalize_currency,
    normalize_prefix,
    normalize_printer_footer,
    normalize_timezone,
    normalize_warning_days,
)
from apps.users.serializers import StrictSerializer, validate_new_password


class AdminGymInputSerializer(StrictSerializer):
    code = OptionalPatternField(GYM_CODE)
    name = serializers.CharField(max_length=150)
    legal_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    address_line1 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    address_line2 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    region = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    country_code = serializers.RegexField(
        r"^[A-Z]{2}$", required=False, allow_blank=True, max_length=2
    )
    timezone = serializers.CharField(max_length=64, default="Asia/Yangon")
    currency = serializers.RegexField(r"^[A-Z]{3}$", default="THB")
    subscription_plan_id = serializers.UUIDField()
    subscription_status = serializers.ChoiceField(
        choices=[Gym.SubscriptionStatus.TRIAL, Gym.SubscriptionStatus.ACTIVE],
        default=Gym.SubscriptionStatus.TRIAL,
    )
    subscription_starts_at = serializers.DateTimeField(required=False, allow_null=True)
    subscription_ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise serializers.ValidationError("Unsupported timezone.") from exc
        return value

    def validate_currency(self, value: str) -> str:
        return normalize_currency(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        starts_at = attrs.get("subscription_starts_at")
        ends_at = attrs.get("subscription_ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"subscription_ends_at": ["Must be after subscription_starts_at."]}
            )
        return attrs


class AdminOwnerInputSerializer(StrictSerializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    send_invitation = serializers.BooleanField(required=False, default=True)


class AdminGymProvisionRequestSerializer(StrictSerializer):
    gym = AdminGymInputSerializer()
    owner = AdminOwnerInputSerializer()


class AdminGymListQuerySerializer(StrictSerializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=100)
    status = serializers.ChoiceField(choices=list(Gym.Status), required=False)
    limit = serializers.IntegerField(default=25, min_value=1, max_value=100)
    after = serializers.CharField(required=False, max_length=512)


class AdminGymListItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    subscription_plan_id = serializers.UUIDField(allow_null=True)
    subscription_plan_name = serializers.CharField(allow_null=True)
    subscription_status = serializers.CharField()
    created_at = serializers.DateTimeField()


class AdminGymDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    legal_name = serializers.CharField()
    status = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField()
    address_line1 = serializers.CharField()
    address_line2 = serializers.CharField()
    city = serializers.CharField()
    region = serializers.CharField()
    postal_code = serializers.CharField()
    country_code = serializers.CharField()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    subscription_plan_id = serializers.UUIDField(allow_null=True)
    subscription_plan_name = serializers.CharField(allow_null=True)
    subscription_status = serializers.CharField()
    subscription_starts_at = serializers.DateTimeField(allow_null=True)
    subscription_ends_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class AdminGymPatchSerializer(StrictSerializer):
    name = serializers.CharField(required=False, max_length=150)
    legal_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    address_line1 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    address_line2 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    region = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    country_code = serializers.RegexField(
        r"^[A-Z]{2}$", required=False, allow_blank=True, max_length=2
    )
    timezone = serializers.CharField(required=False, max_length=64)
    currency = serializers.RegexField(r"^[A-Z]{3}$", required=False)
    subscription_plan_id = serializers.UUIDField(required=False)
    subscription_status = serializers.ChoiceField(
        choices=list(Gym.SubscriptionStatus),
        required=False,
    )
    subscription_starts_at = serializers.DateTimeField(required=False, allow_null=True)
    subscription_ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_timezone(self, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise serializers.ValidationError("Unsupported timezone.") from exc
        return value

    def validate_currency(self, value: str) -> str:
        return normalize_currency(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        starts_at = attrs.get("subscription_starts_at")
        ends_at = attrs.get("subscription_ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError(
                {"subscription_ends_at": ["Must be after subscription_starts_at."]}
            )
        return attrs


class AdminGymLifecycleInputSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=3, max_length=500, trim_whitespace=True)


class AdminGymDetailEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminGymDetailSerializer()
    message = serializers.CharField()


class OwnerInviteInputSerializer(StrictSerializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    send_invitation = serializers.BooleanField(default=True)

    def validate_send_invitation(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Email delivery is required until another secure channel is configured."
            )
        return value


class OwnerRevokeInputSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=3, max_length=500, trim_whitespace=True)


class OwnerAcceptInputSerializer(StrictSerializer):
    invitation_token = serializers.CharField(trim_whitespace=False, write_only=True)
    password = serializers.CharField(trim_whitespace=False, write_only=True)
    password_confirmation = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        validate_new_password(
            str(attrs["password"]),
            str(attrs["password_confirmation"]),
        )
        return attrs


class OwnerItemSerializer(serializers.Serializer):
    gym_membership_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()
    status = serializers.CharField()
    is_owner = serializers.BooleanField()
    authz_version = serializers.IntegerField()
    invited_at = serializers.DateTimeField(allow_null=True)
    activated_at = serializers.DateTimeField(allow_null=True)


class LoginAccountItemSerializer(OwnerItemSerializer):
    role_keys = serializers.ListField(child=serializers.CharField())


class LoginAccountListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = LoginAccountItemSerializer(many=True)
    message = serializers.CharField()


class OwnerInvitationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    gym_membership_id = serializers.UUIDField()
    email = serializers.EmailField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()


class OwnerListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = OwnerItemSerializer(many=True)
    message = serializers.CharField()


class OwnerItemEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = OwnerItemSerializer()
    message = serializers.CharField()


class OwnerInvitationEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = OwnerInvitationSerializer()
    message = serializers.CharField()


SUBSCRIPTION_LIST_STATUS_ALL = "ALL"
SUBSCRIPTION_LIST_STATUSES = ("ACTIVE", "RETIRED", SUBSCRIPTION_LIST_STATUS_ALL)


class AdminSubscriptionListQuerySerializer(StrictSerializer):
    """Optional catalog filter. Omitted status stays ACTIVE for provisioning."""

    status = serializers.ChoiceField(
        choices=SUBSCRIPTION_LIST_STATUSES,
        required=False,
        default="ACTIVE",
    )

    def to_internal_value(self, data: Any) -> dict[str, object]:
        # Query strings represent a blank parameter as "" and DRF otherwise
        # treats that as omitted. A present blank status is invalid.
        if isinstance(data, Mapping) and "status" in data and data.get("status") == "":
            raise serializers.ValidationError({"status": ["This value is not a valid choice."]})
        return super().to_internal_value(data)


class AdminSubscriptionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=SubscriptionPlan.Status.choices)
    price = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField()
    billing_interval = serializers.CharField()


class AdminSubscriptionDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    status = serializers.ChoiceField(choices=SubscriptionPlan.Status.choices)
    price = serializers.DecimalField(max_digits=18, decimal_places=2)
    currency = serializers.CharField()
    billing_interval = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


@extend_schema_field(
    {
        "type": "string",
        "format": "decimal",
        "pattern": r"^(?:0|[1-9]\d{0,15})(?:\.\d{1,2})?$",
    }
)
class NonNegativePriceField(serializers.DecimalField):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)


class AdminSubscriptionUpdateSerializer(StrictSerializer):
    name = serializers.CharField(required=False, allow_blank=False, max_length=100)
    status = serializers.ChoiceField(choices=SubscriptionPlan.Status.choices, required=False)
    price = NonNegativePriceField(required=False)
    billing_interval = serializers.RegexField(
        r"^[A-Z][A-Z0-9_]{0,19}$",
        required=False,
        max_length=20,
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class GymStatusCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField(min_value=0)
    PENDING = serializers.IntegerField(min_value=0)
    ACTIVE = serializers.IntegerField(min_value=0)
    SUSPENDED = serializers.IntegerField(min_value=0)
    CLOSED = serializers.IntegerField(min_value=0)


class GymSubscriptionStatusCountsSerializer(serializers.Serializer):
    TRIAL = serializers.IntegerField(min_value=0)
    ACTIVE = serializers.IntegerField(min_value=0)
    PAST_DUE = serializers.IntegerField(min_value=0)
    CANCELLED = serializers.IntegerField(min_value=0)


class SubscriptionPlanStatusCountsSerializer(serializers.Serializer):
    ACTIVE = serializers.IntegerField(min_value=0)
    RETIRED = serializers.IntegerField(min_value=0)


class AdminDashboardSerializer(serializers.Serializer):
    gym_statuses = GymStatusCountsSerializer()
    gym_subscription_statuses = GymSubscriptionStatusCountsSerializer()
    subscription_plan_statuses = SubscriptionPlanStatusCountsSerializer()


class AdminDashboardEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminDashboardSerializer()
    message = serializers.CharField()


class AdminProvisionedGymSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    legal_name = serializers.CharField()
    status = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField()
    address_line1 = serializers.CharField()
    address_line2 = serializers.CharField()
    city = serializers.CharField()
    region = serializers.CharField()
    postal_code = serializers.CharField()
    country_code = serializers.CharField()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    subscription_plan_id = serializers.UUIDField()
    subscription_status = serializers.CharField()
    subscription_starts_at = serializers.DateTimeField(allow_null=True)
    subscription_ends_at = serializers.DateTimeField(allow_null=True)


class AdminProvisionedUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField()


class AdminProvisionedMembershipSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    is_owner = serializers.BooleanField()


class AdminProvisionedOwnerSerializer(serializers.Serializer):
    user = AdminProvisionedUserSerializer()
    gym_membership = AdminProvisionedMembershipSerializer()


class AdminProvisionedRoleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    key = serializers.CharField()
    name = serializers.CharField()


class AdminProvisionedEntitlementSerializer(serializers.Serializer):
    feature = serializers.CharField()
    enabled = serializers.BooleanField()
    limit_value = serializers.IntegerField(allow_null=True)
    source = serializers.CharField()


class AdminProvisionedExpenseCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    system_key = serializers.CharField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()


class AdminGymProvisionDataSerializer(serializers.Serializer):
    gym = AdminProvisionedGymSerializer()
    owner = AdminProvisionedOwnerSerializer()
    roles = AdminProvisionedRoleSerializer(many=True)
    feature_entitlements = AdminProvisionedEntitlementSerializer(many=True)
    expense_categories = AdminProvisionedExpenseCategorySerializer(many=True)


class AdminGymProvisionEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminGymProvisionDataSerializer()
    message = serializers.CharField()


class AdminGymListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminGymListItemSerializer(many=True)
    meta = serializers.DictField()
    message = serializers.CharField()


class AdminSubscriptionListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminSubscriptionSerializer(many=True)
    message = serializers.CharField()


class AdminSubscriptionDetailEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = AdminSubscriptionDetailSerializer()
    message = serializers.CharField()


class GymProfileSerializer(serializers.Serializer):
    """Safe gym profile. Lifecycle and subscription are read-only for tenants."""

    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    legal_name = serializers.CharField()
    status = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField()
    address_line1 = serializers.CharField()
    address_line2 = serializers.CharField()
    city = serializers.CharField()
    region = serializers.CharField()
    postal_code = serializers.CharField()
    country_code = serializers.CharField()
    timezone = serializers.CharField()
    currency = serializers.CharField()
    subscription_plan_id = serializers.UUIDField(allow_null=True)
    subscription_plan_name = serializers.CharField(allow_null=True)
    subscription_status = serializers.CharField()
    subscription_starts_at = serializers.DateTimeField(allow_null=True)
    subscription_ends_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    has_logo = serializers.BooleanField()

    def to_representation(self, instance: Any) -> dict[str, Any]:
        if isinstance(instance, dict):
            return super().to_representation(instance)
        plan = instance.subscription_plan
        return super().to_representation(
            {
                "id": instance.id,
                "code": instance.code,
                "name": instance.name,
                "legal_name": instance.legal_name,
                "status": instance.status,
                "email": instance.email,
                "phone": instance.phone,
                "address_line1": instance.address_line1,
                "address_line2": instance.address_line2,
                "city": instance.city,
                "region": instance.region,
                "postal_code": instance.postal_code,
                "country_code": instance.country_code,
                "timezone": instance.timezone,
                "currency": instance.currency,
                "subscription_plan_id": instance.subscription_plan_id,
                "subscription_plan_name": None if plan is None else plan.name,
                "subscription_status": instance.subscription_status,
                "subscription_starts_at": instance.subscription_starts_at,
                "subscription_ends_at": instance.subscription_ends_at,
                "created_at": instance.created_at,
                "updated_at": instance.updated_at,
                "has_logo": (
                    GymLogo.objects.filter(pk=instance.id).exists()
                    if isinstance(instance, Gym)
                    else bool(getattr(instance, "has_logo", False))
                ),
            }
        )


class GymProfileUpdateSerializer(StrictSerializer):
    name = serializers.CharField(required=False, max_length=150)
    legal_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    address_line1 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    address_line2 = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    region = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    country_code = serializers.RegexField(
        r"^[A-Z]{2}$", required=False, allow_blank=True, max_length=2
    )
    timezone = serializers.CharField(required=False, max_length=64)
    currency = serializers.RegexField(r"^[A-Z]{3}$", required=False)

    def validate_timezone(self, value: str) -> str:
        return normalize_timezone(value)

    def validate_currency(self, value: str) -> str:
        return normalize_currency(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class GymProfileEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = GymProfileSerializer()
    message = serializers.CharField()


class GymSettingsSerializer(serializers.Serializer):
    gym_id = serializers.UUIDField()
    week_starts_on = serializers.IntegerField()
    allow_manual_member_numbers = serializers.BooleanField()
    member_number_prefix = serializers.CharField()
    receipt_number_prefix = serializers.CharField()
    check_in_duplicate_window_seconds = serializers.IntegerField()
    check_in_challenge_ttl_seconds = serializers.IntegerField()
    check_in_challenge_max_uses = serializers.IntegerField()
    membership_expiry_warning_days = serializers.ListField(child=serializers.IntegerField())
    membership_expiry_email_enabled = serializers.BooleanField()
    membership_expiry_push_enabled = serializers.BooleanField()
    class_cancellation_cutoff_minutes = serializers.IntegerField()
    class_no_show_consumes_session = serializers.BooleanField()
    require_separate_finance_approval = serializers.BooleanField()
    printer_enabled = serializers.BooleanField()
    printer_print_logo = serializers.BooleanField()
    printer_print_address = serializers.BooleanField()
    printer_print_phone = serializers.BooleanField()
    printer_footer = serializers.CharField()
    next_member_number = serializers.IntegerField()
    created_by_membership_id = serializers.UUIDField(allow_null=True)
    updated_by_membership_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class GymSettingsUpdateSerializer(StrictSerializer):
    week_starts_on = serializers.IntegerField(
        required=False,
        min_value=SETTINGS_INTEGER_RANGES["week_starts_on"][0],
        max_value=SETTINGS_INTEGER_RANGES["week_starts_on"][1],
    )
    allow_manual_member_numbers = serializers.BooleanField(required=False)
    member_number_prefix = serializers.CharField(required=False, max_length=20)
    receipt_number_prefix = serializers.CharField(required=False, max_length=20)
    check_in_duplicate_window_seconds = serializers.IntegerField(
        required=False,
        min_value=SETTINGS_INTEGER_RANGES["check_in_duplicate_window_seconds"][0],
        max_value=SETTINGS_INTEGER_RANGES["check_in_duplicate_window_seconds"][1],
    )
    check_in_challenge_ttl_seconds = serializers.IntegerField(
        required=False,
        min_value=SETTINGS_INTEGER_RANGES["check_in_challenge_ttl_seconds"][0],
        max_value=SETTINGS_INTEGER_RANGES["check_in_challenge_ttl_seconds"][1],
    )
    check_in_challenge_max_uses = serializers.IntegerField(
        required=False,
        min_value=SETTINGS_INTEGER_RANGES["check_in_challenge_max_uses"][0],
        max_value=SETTINGS_INTEGER_RANGES["check_in_challenge_max_uses"][1],
    )
    membership_expiry_warning_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=365),
        required=False,
        max_length=366,
    )
    membership_expiry_email_enabled = serializers.BooleanField(required=False)
    membership_expiry_push_enabled = serializers.BooleanField(required=False)
    class_cancellation_cutoff_minutes = serializers.IntegerField(
        required=False,
        min_value=SETTINGS_INTEGER_RANGES["class_cancellation_cutoff_minutes"][0],
        max_value=SETTINGS_INTEGER_RANGES["class_cancellation_cutoff_minutes"][1],
    )
    class_no_show_consumes_session = serializers.BooleanField(required=False)
    require_separate_finance_approval = serializers.BooleanField(required=False)
    printer_enabled = serializers.BooleanField(required=False)
    printer_print_logo = serializers.BooleanField(required=False)
    printer_print_address = serializers.BooleanField(required=False)
    printer_print_phone = serializers.BooleanField(required=False)
    printer_footer = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        max_length=120,
    )

    def validate_member_number_prefix(self, value: str) -> str:
        return normalize_prefix(value)

    def validate_receipt_number_prefix(self, value: str) -> str:
        return normalize_prefix(value)

    def validate_membership_expiry_warning_days(self, value: list[int]) -> list[int]:
        return normalize_warning_days(value)

    def validate_printer_footer(self, value: str) -> str:
        return normalize_printer_footer(value)

    def validate_require_separate_finance_approval(self, value: bool) -> bool:
        if value is not True:
            raise serializers.ValidationError(
                "Separate finance approval is required and cannot be disabled."
            )
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("At least one field is required.")
        return attrs


class GymSettingsEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    data = GymSettingsSerializer()
    message = serializers.CharField()
