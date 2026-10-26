"""Strict expense-category and expense contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.cashbook.serializers import (
    CashSessionSerializer,
    CashTransactionSerializer,
    ExactMoneyField,
)
from apps.common.codes import OptionalPatternField
from apps.expenses.models import Expense
from apps.members.models import Payment
from apps.members.payment_serializers import money_text
from apps.tenants.models import ExpenseCategory
from apps.users.serializers import StrictSerializer

_CODE = r"^[A-Z][A-Z0-9_]{0,31}$"


class ExpenseCategoryWriteSerializer(StrictSerializer):
    code = OptionalPatternField(_CODE)
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    display_order = serializers.IntegerField(required=False, min_value=0, max_value=32767)

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a category name.")
        return value.strip()


class ExpenseCategoryUpdateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=120, trim_whitespace=True, required=False)
    display_order = serializers.IntegerField(required=False, min_value=0, max_value=32767)
    is_active = serializers.BooleanField(required=False)

    def validate_name(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a category name.")
        return value.strip()


class ExpenseWriteSerializer(StrictSerializer):
    category_id = serializers.UUIDField()
    business_date = serializers.DateField()
    description = serializers.CharField(max_length=255, trim_whitespace=True)
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True)
    currency = serializers.CharField(min_length=3, max_length=3)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    receipt_file_asset_id = serializers.UUIDField(required=False, allow_null=True)
    policy_note = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, trim_whitespace=True
    )

    def validate_description(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a description.")
        return value.strip()

    def validate_currency(self, value: str) -> str:
        if not value.isascii() or not value.isalpha() or value != value.upper():
            raise serializers.ValidationError("Enter a canonical currency code.")
        return value

    def validate_policy_note(self, value: str) -> str:
        return value.strip()


class ExpenseUpdateSerializer(ExpenseWriteSerializer):
    category_id = serializers.UUIDField(required=False)
    business_date = serializers.DateField(required=False)
    description = serializers.CharField(max_length=255, trim_whitespace=True, required=False)
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True, required=False)
    currency = serializers.CharField(min_length=3, max_length=3, required=False)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)


class EmptyCommandSerializer(StrictSerializer):
    pass


class ApproveExpenseSerializer(StrictSerializer):
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)


class RejectExpenseSerializer(StrictSerializer):
    rejection_reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_rejection_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a rejection reason.")
        return value.strip()


class VoidExpenseSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    business_date = serializers.DateField(required=False)
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a reason.")
        return value.strip()


class CancelExpenseSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("Enter a cancellation reason.")
        return value.strip()


class ExpenseListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=Expense.Status.choices, required=False)
    category = serializers.UUIDField(required=False)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class ExpenseCategoryListQuerySerializer(StrictSerializer):
    is_active = serializers.ChoiceField(choices=("true", "false"), required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)

    def validate_is_active(self, value: str) -> bool:
        return value == "true"


class ExpenseCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    system_key = serializers.CharField(allow_null=True)
    display_order = serializers.IntegerField()
    is_active = serializers.BooleanField()
    archived_at = serializers.DateTimeField(allow_null=True)
    created_by_membership_id = serializers.UUIDField(allow_null=True)
    updated_by_membership_id = serializers.UUIDField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ExpenseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    category_id = serializers.UUIDField()
    business_date = serializers.DateField()
    description = serializers.CharField()
    amount = ExactMoneyField(minimum=Decimal("0.01"), read_only=True)
    currency = serializers.CharField()
    payment_method = serializers.CharField()
    receipt_file_asset_id = serializers.UUIDField(allow_null=True)
    policy_note = serializers.CharField()
    status = serializers.CharField()
    reversal_expense_id = serializers.UUIDField(allow_null=True)
    original_expense_id = serializers.UUIDField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by_membership_id = serializers.UUIDField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by_membership_id = serializers.UUIDField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    rejected_by_membership_id = serializers.UUIDField(allow_null=True)
    rejection_reason = serializers.CharField()
    posted_at = serializers.DateTimeField(allow_null=True)
    posted_by_membership_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    reason = serializers.CharField()
    cash_session_id = serializers.UUIDField(allow_null=True)
    cash_transaction_id = serializers.UUIDField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ExpenseEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseSerializer()
    message = serializers.CharField()


class ExpenseListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class ExpenseListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseSerializer(many=True)
    meta = ExpenseListMetaSerializer()
    message = serializers.CharField()


class ExpenseCategoryEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseCategorySerializer()
    message = serializers.CharField()


class ExpenseCategoryListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseCategorySerializer(many=True)
    meta = ExpenseListMetaSerializer()
    message = serializers.CharField()


class ExpenseApprovalSerializer(serializers.Serializer):
    expense = ExpenseSerializer()
    cash_session = CashSessionSerializer(allow_null=True)
    cash_transaction = CashTransactionSerializer(allow_null=True)


class ExpenseVoidSerializer(serializers.Serializer):
    original_expense = ExpenseSerializer()
    reversal_expense = ExpenseSerializer()
    cash_session = CashSessionSerializer(allow_null=True)
    cash_transaction = CashTransactionSerializer(allow_null=True)


class ExpenseVoidEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseVoidSerializer()
    message = serializers.CharField()


class ExpenseApprovalEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ExpenseApprovalSerializer()
    message = serializers.CharField()


def category_payload(category: ExpenseCategory) -> dict[str, object]:
    return cast(dict[str, object], ExpenseCategorySerializer(category).data)


def expense_payload(expense: Expense) -> dict[str, object]:
    return cast(dict[str, object], ExpenseSerializer(expense).data)


def money_amount(value: Decimal) -> str:
    return money_text(value)


def merged_expense_fields(expense: Expense, fields: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "category_id": expense.category_id,
        "business_date": expense.business_date,
        "description": expense.description,
        "amount": expense.amount,
        "currency": expense.currency,
        "payment_method": expense.payment_method,
        "receipt_file_asset_id": expense.receipt_file_asset_id,
        "policy_note": expense.policy_note,
    }
    merged.update(fields)
    return merged
