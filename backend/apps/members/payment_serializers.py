"""Strict payment, receipt, and daily-sales contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from django.utils import timezone
from rest_framework import serializers

from apps.cashbook.serializers import (
    CashSessionSerializer,
    CashTransactionSerializer,
    ExactMoneyField,
)
from apps.members.models import Payment, Receipt
from apps.users.serializers import StrictSerializer

_CREATABLE_PURPOSES = {Payment.Purpose.MEMBERSHIP, Payment.Purpose.OTHER}


class PaymentWriteSerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False, allow_null=True)
    membership_id = serializers.UUIDField(required=False, allow_null=True)
    purpose = serializers.ChoiceField(choices=Payment.Purpose.choices)
    description = serializers.CharField(max_length=255, trim_whitespace=True)
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True)
    currency = serializers.CharField(min_length=3, max_length=3)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    business_date = serializers.DateField()
    received_at = serializers.DateTimeField(required=False)
    reference_number = serializers.CharField(required=False, allow_blank=True, max_length=100)

    def validate_purpose(self, value: str) -> str:
        if value not in _CREATABLE_PURPOSES:
            raise serializers.ValidationError("This payment purpose is not available yet.")
        return value

    def validate_description(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a description.")
        return value.strip()

    def validate_currency(self, value: str) -> str:
        if not value.isascii() or not value.isalpha() or value != value.upper():
            raise serializers.ValidationError("Enter a canonical currency code.")
        return value

    def validate_received_at(self, value: datetime) -> datetime:
        if timezone.is_naive(value):
            raise serializers.ValidationError("Use a timezone-aware time.")
        if value > timezone.now():
            raise serializers.ValidationError("Received time cannot be in the future.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        purpose = attrs.get("purpose")
        membership_id = attrs.get("membership_id")
        member_id = attrs.get("member_id")
        if purpose == Payment.Purpose.MEMBERSHIP and (member_id is None or membership_id is None):
            raise serializers.ValidationError(
                {
                    "membership_id": ["A membership payment requires the member and membership."],
                }
            )
        if purpose == Payment.Purpose.OTHER and membership_id is not None:
            raise serializers.ValidationError(
                {"membership_id": ["An other payment has no source reference."]}
            )
        return attrs


PaymentCreateSerializer = PaymentWriteSerializer


class PaymentUpdateSerializer(PaymentWriteSerializer):
    purpose = serializers.ChoiceField(choices=Payment.Purpose.choices, required=False)
    description = serializers.CharField(max_length=255, trim_whitespace=True, required=False)
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True, required=False)
    currency = serializers.CharField(min_length=3, max_length=3, required=False)
    method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    business_date = serializers.DateField(required=False)


class EmptyCommandSerializer(StrictSerializer):
    pass


class ApprovePaymentSerializer(StrictSerializer):
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)


class RejectPaymentSerializer(StrictSerializer):
    rejection_reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_rejection_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a rejection reason.")
        return value.strip()


class VoidPaymentSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    business_date = serializers.DateField(required=False)
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a reason.")
        return value.strip()


class RefundPaymentSerializer(StrictSerializer):
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True)
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    business_date = serializers.DateField()
    reference_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a reason.")
        return value.strip()


class VoidReceiptSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a reason.")
        return value.strip()


class CancelPaymentSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a cancellation reason.")
        return value.strip()


class PaymentListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=Payment.Status.choices, required=False)
    member = serializers.UUIDField(required=False)
    membership = serializers.UUIDField(required=False)
    purpose = serializers.ChoiceField(choices=Payment.Purpose.choices, required=False)
    method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class ReceiptListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=Receipt.Status.choices, required=False)
    payment = serializers.UUIDField(required=False)
    member = serializers.UUIDField(required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class DailySalesQuerySerializer(StrictSerializer):
    date = serializers.DateField()


class PaymentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField(allow_null=True)
    membership_id = serializers.UUIDField(allow_null=True)
    original_payment_id = serializers.UUIDField(allow_null=True)
    reversal_payment_id = serializers.UUIDField(allow_null=True)
    entry_type = serializers.CharField()
    purpose = serializers.CharField()
    description = serializers.CharField()
    amount = ExactMoneyField(minimum=Decimal("0.01"), read_only=True)
    currency = serializers.CharField()
    method = serializers.CharField()
    business_date = serializers.DateField()
    received_at = serializers.DateTimeField()
    reference_number = serializers.CharField()
    status = serializers.CharField()
    rejection_reason = serializers.CharField()
    cancellation_reason = serializers.CharField()
    void_reason = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by_membership_id = serializers.UUIDField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by_membership_id = serializers.UUIDField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    rejected_by_membership_id = serializers.UUIDField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    posted_at = serializers.DateTimeField(allow_null=True)
    posted_by_membership_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    cash_session_id = serializers.UUIDField(allow_null=True)
    cash_transaction_id = serializers.UUIDField(allow_null=True)
    retail_sale_id = serializers.UUIDField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ReceiptSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    payment_id = serializers.UUIDField()
    receipt_number = serializers.CharField()
    issued_at = serializers.DateTimeField()
    gym_code = serializers.CharField()
    gym_name = serializers.CharField()
    gym_address = serializers.CharField()
    member_number = serializers.CharField()
    member_name = serializers.CharField()
    description = serializers.CharField()
    amount = ExactMoneyField(minimum=Decimal("0.01"), read_only=True)
    currency = serializers.CharField()
    method = serializers.CharField()
    business_date = serializers.DateField()
    issuer_name = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class MembershipSettlementSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    payment_status = serializers.CharField()
    paid_amount = serializers.CharField()
    balance_amount = serializers.CharField()


class PaymentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PaymentSerializer()
    message = serializers.CharField()


class PaymentListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class PaymentListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PaymentSerializer(many=True)
    meta = PaymentListMetaSerializer()
    message = serializers.CharField()


class ReceiptEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ReceiptSerializer()
    message = serializers.CharField()


class ReceiptListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ReceiptSerializer(many=True)
    meta = PaymentListMetaSerializer()
    message = serializers.CharField()


class PaymentApprovalSerializer(serializers.Serializer):
    payment = PaymentSerializer()
    receipt = ReceiptSerializer()
    cash_session = CashSessionSerializer(allow_null=True)
    cash_transaction = CashTransactionSerializer(allow_null=True)
    membership_settlement = MembershipSettlementSerializer(allow_null=True)


class PaymentCorrectionSerializer(serializers.Serializer):
    original_payment = PaymentSerializer()
    correction_payment = PaymentSerializer()
    original_receipt = ReceiptSerializer(allow_null=True)
    correction_receipt = ReceiptSerializer()
    cash_transactions = CashTransactionSerializer(many=True)
    refundable_amount = serializers.CharField()
    membership_settlement = MembershipSettlementSerializer(allow_null=True)


class PaymentCorrectionEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PaymentCorrectionSerializer()
    message = serializers.CharField()


class PaymentApprovalEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PaymentApprovalSerializer()
    message = serializers.CharField()


class MethodTotalSerializer(serializers.Serializer):
    method = serializers.CharField()
    total = serializers.CharField()


class DailySalesSerializer(serializers.Serializer):
    date = serializers.DateField()
    currency = serializers.CharField()
    gross_sales = serializers.CharField()
    refunds = serializers.CharField()
    reversals = serializers.CharField()
    net_total = serializers.CharField()
    by_payment_method = MethodTotalSerializer(many=True)
    new_member_total = serializers.CharField()
    renewal_total = serializers.CharField()
    pt_total = serializers.CharField()
    class_total = serializers.CharField()
    product_total = serializers.CharField()
    other_total = serializers.CharField()
    product_quantity = serializers.CharField()
    product_revenue = serializers.CharField()
    product_cogs = serializers.CharField()
    product_gross_profit = serializers.CharField()


class DailySalesEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = DailySalesSerializer()
    message = serializers.CharField()


def money_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def payment_payload(payment: Payment) -> dict[str, object]:
    return cast(dict[str, object], PaymentSerializer(payment).data)


def receipt_payload(receipt: Receipt) -> dict[str, object]:
    return cast(dict[str, object], ReceiptSerializer(receipt).data)


def settlement_payload(
    *,
    membership_id: object,
    payment_status: str,
    paid_amount: Decimal,
    balance_amount: Decimal,
) -> dict[str, object]:
    return {
        "membership_id": str(membership_id),
        "payment_status": payment_status,
        "paid_amount": money_text(paid_amount),
        "balance_amount": money_text(balance_amount),
    }
