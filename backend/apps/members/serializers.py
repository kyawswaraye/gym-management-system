from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import cast

from rest_framework import serializers

from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.users.serializers import StrictSerializer


class MemberSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Member
        exclude = ("gym", "created_by_membership_id", "updated_by_membership_id")
        read_only_fields = ("id", "status", "archived_at", "created_at", "updated_at")


class MemberCreateSerializer(StrictSerializer):
    member_number = serializers.CharField(required=False, allow_blank=True, max_length=32)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    email = serializers.EmailField(required=False, allow_blank=True)
    gender = serializers.CharField(required=False, allow_blank=True, max_length=20)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    join_date = serializers.DateField()
    address = serializers.JSONField(required=False)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True)
    emergency_contact_phone = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class MembershipPlanSerializer(serializers.ModelSerializer):
    def validate_currency(self, value: str) -> str:
        if self.instance is None:
            return value
        gym = self.context.get("gym")
        expected_currency = gym.currency if gym is not None else self.instance.gym.currency
        if value != expected_currency:
            raise serializers.ValidationError("Currency must match the selected gym.")
        return value

    class Meta:
        model = MembershipPlan
        exclude = ("gym", "created_by_membership_id", "updated_by_membership_id")
        read_only_fields = ("id", "is_active", "archived_at", "created_at", "updated_at")


class MembershipPlanCreateSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True)
    duration_value = serializers.IntegerField(min_value=1)
    duration_unit = serializers.ChoiceField(choices=MembershipPlan.DurationUnit.choices)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, min_value=Decimal("0"))
    currency = serializers.CharField(min_length=3, max_length=3)


class MembershipSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source="member.display_name", read_only=True)
    paid_amount = serializers.SerializerMethodField()
    outstanding_amount = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        exclude = ("gym", "created_by_membership_id")
        read_only_fields = "__all__"

    def _paid(self, obj: Membership) -> Decimal:
        prefetched = getattr(obj, "_balance_payments", None)
        payments: Iterable[Payment]
        if prefetched is None:
            payments = obj.payments.filter(status__in=[Payment.Status.POSTED, Payment.Status.VOID])
        else:
            payments = cast(list[Payment], prefetched)
        paid = sum(
            (
                -payment.amount
                if payment.entry_type in [Payment.EntryType.REFUND, Payment.EntryType.REVERSAL]
                else payment.amount
                for payment in payments
            ),
            Decimal("0"),
        )
        return paid

    def get_paid_amount(self, obj: Membership) -> Decimal:
        return self._paid(obj)

    def get_outstanding_amount(self, obj: Membership) -> Decimal:
        return max(Decimal("0"), obj.final_amount - self._paid(obj))


class MembershipCreateSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    plan_id = serializers.UUIDField()
    start_date = serializers.DateField()
    discount_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, min_value=Decimal("0"), default=Decimal("0")
    )


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
