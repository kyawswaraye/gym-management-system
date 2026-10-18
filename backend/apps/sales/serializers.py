"""Strict retail-sale contracts.

Clients may send a member, business date, payment method, reference, and
product lines. Snapshots, totals, cost, profit, status, actors, and payment
or receipt links are rejected. Line lists are sorted by product before the
idempotency hash, so order is not part of the request identity. A sale accepts
at most 100 lines.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.cashbook.serializers import CashTransactionSerializer, ExactMoneyField
from apps.inventory.quantities import format_quantity
from apps.inventory.serializers import ExactQuantityField
from apps.members.models import Payment
from apps.members.payment_serializers import PaymentSerializer, ReceiptSerializer, money_text
from apps.sales.models import RetailSale, RetailSaleLine
from apps.users.serializers import StrictSerializer

MAX_LINES = 100


def _nonblank(value: str, message: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise serializers.ValidationError(message)
    return stripped


class RetailSaleLineWriteSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    quantity = ExactQuantityField(nonzero=True, min_value=Decimal("0.001"))
    discount = ExactMoneyField(minimum=Decimal("0"))


class RetailSaleWriteSerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False, allow_null=True)
    business_date = serializers.DateField()
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    reference_number = serializers.CharField(
        required=False, allow_blank=True, max_length=100, trim_whitespace=True
    )
    lines = RetailSaleLineWriteSerializer(many=True)

    def validate_reference_number(self, value: str) -> str:
        return value.strip()

    def validate_lines(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _lines(value)


class RetailSaleUpdateSerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False, allow_null=True)
    business_date = serializers.DateField(required=False)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    reference_number = serializers.CharField(
        required=False, allow_blank=True, max_length=100, trim_whitespace=True
    )
    lines = RetailSaleLineWriteSerializer(many=True, required=False)

    def validate_reference_number(self, value: str) -> str:
        return value.strip()

    def validate_lines(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _lines(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Enter a field to update.")
        return attrs


class EmptyCommandSerializer(StrictSerializer):
    pass


class ApproveRetailSaleSerializer(StrictSerializer):
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)


class RejectRetailSaleSerializer(StrictSerializer):
    rejection_reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_rejection_reason(self, value: str) -> str:
        return _nonblank(value, "Enter a rejection reason.")


class CancelRetailSaleSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _nonblank(value, "Enter a cancellation reason.")


class RetailSaleListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=RetailSale.Status.choices, required=False)
    business_date = serializers.DateField(required=False)
    member = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class RetailSaleLineSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    quantity = serializers.CharField()
    discount = serializers.CharField()
    sku = serializers.CharField()
    product_name = serializers.CharField()
    unit_price = serializers.CharField()
    unit_cost = serializers.CharField()
    line_revenue = serializers.CharField()
    cost_of_goods_sold = serializers.CharField()
    gross_profit = serializers.CharField()


class RetailSaleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField(allow_null=True)
    business_date = serializers.DateField()
    currency = serializers.CharField()
    payment_method = serializers.CharField()
    reference_number = serializers.CharField()
    status = serializers.CharField()
    subtotal = serializers.CharField()
    discount_total = serializers.CharField()
    total_amount = serializers.CharField()
    cogs_total = serializers.CharField()
    gross_profit_total = serializers.CharField()
    payment_id = serializers.UUIDField(allow_null=True)
    receipt_id = serializers.UUIDField(allow_null=True)
    rejection_reason = serializers.CharField()
    reason = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by_membership_id = serializers.UUIDField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by_membership_id = serializers.UUIDField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    rejected_by_membership_id = serializers.UUIDField(allow_null=True)
    posted_at = serializers.DateTimeField(allow_null=True)
    posted_by_membership_id = serializers.UUIDField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    void_business_date = serializers.DateField(allow_null=True)
    void_reason = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    lines = RetailSaleLineSerializer(many=True)


class RetailSaleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = RetailSaleSerializer()
    message = serializers.CharField()


class RetailSaleListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class RetailSaleListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = RetailSaleSerializer(many=True)
    meta = RetailSaleListMetaSerializer()
    message = serializers.CharField()


class RetailSaleApprovalSerializer(serializers.Serializer):
    retail_sale = RetailSaleSerializer()
    payment = PaymentSerializer()
    receipt = ReceiptSerializer()
    cash_transaction = CashTransactionSerializer(allow_null=True)


class RetailSaleApprovalEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = RetailSaleApprovalSerializer()
    message = serializers.CharField()


class VoidRetailSaleSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    business_date = serializers.DateField(required=False)
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_reason(self, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise serializers.ValidationError("Enter a reason.")
        return stripped


class RetailVoidStockMovementSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    retail_sale_line_id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    quantity = serializers.CharField()
    movement_type = serializers.CharField()
    unit_cost_snapshot = serializers.CharField()
    business_date = serializers.DateField()
    reverses_movement_id = serializers.UUIDField()


class RetailSaleVoidSerializer(serializers.Serializer):
    retail_sale = RetailSaleSerializer()
    original_payment = PaymentSerializer()
    reversal_payment = PaymentSerializer()
    original_receipt = ReceiptSerializer(allow_null=True)
    reversal_receipt = ReceiptSerializer()
    cash_transaction = CashTransactionSerializer(allow_null=True)
    stock_movements = RetailVoidStockMovementSerializer(many=True)


class RetailSaleVoidEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = RetailSaleVoidSerializer()
    message = serializers.CharField()


def sale_payload(sale: RetailSale) -> dict[str, object]:
    lines = list(sale.lines.order_by("product_id", "id"))
    body = {
        "id": sale.id,
        "member_id": sale.member_id,
        "business_date": sale.business_date,
        "currency": sale.currency,
        "payment_method": sale.payment_method,
        "reference_number": sale.reference_number,
        "status": sale.status,
        "subtotal": money_text(sale.subtotal),
        "discount_total": money_text(sale.discount_total),
        "total_amount": money_text(sale.total_amount),
        "cogs_total": money_text(sale.cogs_total),
        "gross_profit_total": money_text(sale.gross_profit_total),
        "payment_id": sale.payment_id,
        "receipt_id": sale.receipt_id,
        "rejection_reason": sale.rejection_reason,
        "reason": sale.reason,
        "submitted_at": sale.submitted_at,
        "submitted_by_membership_id": sale.submitted_by_membership_id,
        "approved_at": sale.approved_at,
        "approved_by_membership_id": sale.approved_by_membership_id,
        "rejected_at": sale.rejected_at,
        "rejected_by_membership_id": sale.rejected_by_membership_id,
        "posted_at": sale.posted_at,
        "posted_by_membership_id": sale.posted_by_membership_id,
        "cancelled_at": sale.cancelled_at,
        "cancelled_by_membership_id": sale.cancelled_by_membership_id,
        "voided_at": sale.voided_at,
        "voided_by_membership_id": sale.voided_by_membership_id,
        "void_business_date": sale.void_business_date,
        "void_reason": sale.void_reason,
        "created_by_membership_id": sale.created_by_membership_id,
        "updated_by_membership_id": sale.updated_by_membership_id,
        "created_at": sale.created_at,
        "updated_at": sale.updated_at,
        "lines": [_line_body(line) for line in lines],
    }
    return cast(dict[str, object], RetailSaleSerializer(body).data)


def _line_body(line: RetailSaleLine) -> dict[str, object]:
    return {
        "id": line.id,
        "product_id": line.product_id,
        "quantity": format_quantity(line.quantity),
        "discount": money_text(line.discount),
        "sku": line.sku_snapshot,
        "product_name": line.name_snapshot,
        "unit_price": money_text(line.unit_price_snapshot),
        "unit_cost": money_text(line.unit_cost_snapshot),
        "line_revenue": money_text(line.line_revenue),
        "cost_of_goods_sold": money_text(line.cost_of_goods_sold),
        "gross_profit": money_text(line.gross_profit),
    }


def _lines(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not value:
        raise serializers.ValidationError("Enter at least one product line.")
    if len(value) > MAX_LINES:
        raise serializers.ValidationError("A retail sale can contain at most 100 lines.")
    product_ids = [row["product_id"] for row in value]
    if len(product_ids) != len(set(product_ids)):
        raise serializers.ValidationError("Each product can appear once in the sale.")
    return sorted(value, key=lambda row: row["product_id"])
