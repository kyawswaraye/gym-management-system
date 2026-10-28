"""Strict salary-period contracts.

Create, update, and line replacement require ``Idempotency-Key``, matching the
Increment 4 command policy. That is stricter than the minimum submit/approve/
reject/cancel/void requirement. Nested command hashes include the period id.
Line and cash-mapping lists are sorted before hashing so order is not part of
the request identity.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.cashbook.serializers import CashTransactionSerializer, ExactMoneyField
from apps.expenses.serializers import ExpenseSerializer
from apps.members.models import Payment
from apps.members.payment_serializers import money_text
from apps.salaries.models import SalaryPeriod, StaffSalaryLine
from apps.tenants.models import StaffProfile
from apps.users.serializers import StrictSerializer

_MAX_LINES = 200


def _currency(value: str) -> str:
    if not value.isascii() or not value.isalpha() or value != value.upper():
        raise serializers.ValidationError("Enter a canonical currency code.")
    return value


def _nonblank(value: str, message: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise serializers.ValidationError(message)
    return stripped


class SalaryPeriodWriteSerializer(StrictSerializer):
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    business_date = serializers.DateField()
    description = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_description(self, value: str) -> str:
        return _nonblank(value, "Enter a description.")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["period_end"] < attrs["period_start"]:
            raise serializers.ValidationError(
                {"period_end": ["Period end must be on or after period start."]}
            )
        return attrs


class SalaryPeriodUpdateSerializer(StrictSerializer):
    period_start = serializers.DateField(required=False)
    period_end = serializers.DateField(required=False)
    business_date = serializers.DateField(required=False)
    description = serializers.CharField(max_length=255, trim_whitespace=True, required=False)

    def validate_description(self, value: str) -> str:
        return _nonblank(value, "Enter a description.")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Enter a field to update.")
        return attrs


class SalaryLineWriteSerializer(StrictSerializer):
    staff_profile_id = serializers.UUIDField()
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True)
    currency = serializers.CharField(min_length=3, max_length=3)
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    external_reference = serializers.CharField(
        required=False, allow_blank=True, max_length=128, trim_whitespace=True
    )
    description = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_currency(self, value: str) -> str:
        return _currency(value)

    def validate_description(self, value: str) -> str:
        return _nonblank(value, "Enter a description.")

    def validate_external_reference(self, value: str) -> str:
        return value.strip()


class SalaryLinesReplaceSerializer(StrictSerializer):
    lines = SalaryLineWriteSerializer(many=True, allow_empty=True)

    def validate_lines(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > _MAX_LINES:
            raise serializers.ValidationError("A salary period can contain at most 200 lines.")
        staff_ids = [row["staff_profile_id"] for row in value]
        if len(staff_ids) != len(set(staff_ids)):
            raise serializers.ValidationError("Each staff member can appear once in the period.")
        return sorted(value, key=lambda row: row["staff_profile_id"])


class EmptyCommandSerializer(StrictSerializer):
    pass


class SalaryCashMappingSerializer(StrictSerializer):
    line_id = serializers.UUIDField()
    cash_session_id = serializers.UUIDField()


class ApproveSalarySerializer(StrictSerializer):
    cash_sessions = SalaryCashMappingSerializer(many=True, required=False)

    def validate_cash_sessions(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > _MAX_LINES:
            raise serializers.ValidationError("Too many cash-session mappings.")
        line_ids = [row["line_id"] for row in value]
        if len(line_ids) != len(set(line_ids)):
            raise serializers.ValidationError("Each salary line can be mapped once.")
        return sorted(value, key=lambda row: row["line_id"])


class RejectSalarySerializer(StrictSerializer):
    rejection_reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_rejection_reason(self, value: str) -> str:
        return _nonblank(value, "Enter a rejection reason.")


class CancelSalarySerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _nonblank(value, "Enter a cancellation reason.")


class VoidSalarySerializer(StrictSerializer):
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    business_date = serializers.DateField(required=False)
    cash_sessions = SalaryCashMappingSerializer(many=True, required=False)

    def validate_reason(self, value: str) -> str:
        return _nonblank(value, "Enter a reason.")

    def validate_cash_sessions(self, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > _MAX_LINES:
            raise serializers.ValidationError("Too many cash-session mappings.")
        line_ids = [row["line_id"] for row in value]
        if len(line_ids) != len(set(line_ids)):
            raise serializers.ValidationError("Each salary line can be mapped once.")
        return sorted(value, key=lambda row: row["line_id"])


class SalaryPeriodListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=SalaryPeriod.Status.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class StaffPayeeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    staff_code = serializers.CharField()
    display_name = serializers.CharField()


class StaffSalaryLineSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    staff_profile_id = serializers.UUIDField()
    staff = StaffPayeeSerializer()
    amount = ExactMoneyField(minimum=Decimal("0.01"), read_only=True)
    currency = serializers.CharField()
    payment_method = serializers.CharField()
    external_reference = serializers.CharField()
    cash_session_id = serializers.UUIDField(allow_null=True)
    expense_id = serializers.UUIDField(allow_null=True)
    description = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class SalaryPeriodSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    business_date = serializers.DateField()
    description = serializers.CharField()
    status = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by_membership_id = serializers.UUIDField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by_membership_id = serializers.UUIDField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    rejected_by_membership_id = serializers.UUIDField(allow_null=True)
    rejection_reason = serializers.CharField()
    posted_at = serializers.DateTimeField(allow_null=True)
    posted_by_membership_id = serializers.UUIDField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    void_business_date = serializers.DateField(allow_null=True)
    reason = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    lines = StaffSalaryLineSerializer(many=True)


class SalaryPeriodEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = SalaryPeriodSerializer()
    message = serializers.CharField()


class SalaryListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class SalaryPeriodListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = SalaryPeriodSerializer(many=True)
    meta = SalaryListMetaSerializer()
    message = serializers.CharField()


class SalaryApprovalSerializer(serializers.Serializer):
    salary_period = SalaryPeriodSerializer()
    expenses = ExpenseSerializer(many=True)
    cash_transactions = CashTransactionSerializer(many=True)


class SalaryApprovalEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = SalaryApprovalSerializer()
    message = serializers.CharField()


class SalaryLineReversalSerializer(serializers.Serializer):
    line_id = serializers.UUIDField()
    reversal_expense_id = serializers.UUIDField()
    reversal_expense = ExpenseSerializer()
    cash_transaction = CashTransactionSerializer(allow_null=True)


class SalaryVoidSerializer(serializers.Serializer):
    salary_period = SalaryPeriodSerializer()
    reversals = SalaryLineReversalSerializer(many=True)


class SalaryVoidEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = SalaryVoidSerializer()
    message = serializers.CharField()


def money_amount(value: Decimal) -> str:
    return money_text(value)


def line_payload(line: StaffSalaryLine) -> dict[str, object]:
    staff = line.staff_profile
    body = {
        "id": line.id,
        "staff_profile_id": line.staff_profile_id,
        "staff": _payee(staff),
        "amount": line.amount,
        "currency": line.currency,
        "payment_method": line.payment_method,
        "external_reference": line.external_reference,
        "cash_session_id": line.cash_session_id,
        "expense_id": line.expense_id,
        "description": line.description,
        "created_by_membership_id": line.created_by_membership_id,
        "updated_by_membership_id": line.updated_by_membership_id,
        "created_at": line.created_at,
        "updated_at": line.updated_at,
    }
    return cast(dict[str, object], StaffSalaryLineSerializer(body).data)


def period_payload(period: SalaryPeriod) -> dict[str, object]:
    # Must run while app.gym_id is set. Salary command views use
    # non_atomic_requests, so callers serialize inside their gym-context
    # transaction. After replace_lines clears the prefetch cache, this hits the
    # database and returns the lines just written.
    lines = list(period.lines.select_related("staff_profile").order_by("staff_profile_id", "id"))
    body = {
        "id": period.id,
        "period_start": period.period_start,
        "period_end": period.period_end,
        "business_date": period.business_date,
        "description": period.description,
        "status": period.status,
        "submitted_at": period.submitted_at,
        "submitted_by_membership_id": period.submitted_by_membership_id,
        "approved_at": period.approved_at,
        "approved_by_membership_id": period.approved_by_membership_id,
        "rejected_at": period.rejected_at,
        "rejected_by_membership_id": period.rejected_by_membership_id,
        "rejection_reason": period.rejection_reason,
        "posted_at": period.posted_at,
        "posted_by_membership_id": period.posted_by_membership_id,
        "cancelled_at": period.cancelled_at,
        "cancelled_by_membership_id": period.cancelled_by_membership_id,
        "voided_at": period.voided_at,
        "voided_by_membership_id": period.voided_by_membership_id,
        "void_business_date": period.void_business_date,
        "reason": period.reason,
        "created_by_membership_id": period.created_by_membership_id,
        "updated_by_membership_id": period.updated_by_membership_id,
        "created_at": period.created_at,
        "updated_at": period.updated_at,
        "lines": [
            {
                "id": line.id,
                "staff_profile_id": line.staff_profile_id,
                "staff": _payee(line.staff_profile),
                "amount": line.amount,
                "currency": line.currency,
                "payment_method": line.payment_method,
                "external_reference": line.external_reference,
                "cash_session_id": line.cash_session_id,
                "expense_id": line.expense_id,
                "description": line.description,
                "created_by_membership_id": line.created_by_membership_id,
                "updated_by_membership_id": line.updated_by_membership_id,
                "created_at": line.created_at,
                "updated_at": line.updated_at,
            }
            for line in lines
        ],
    }
    return cast(dict[str, object], SalaryPeriodSerializer(body).data)


def _payee(staff: StaffProfile) -> dict[str, object]:
    return {
        "id": staff.id,
        "staff_code": staff.staff_code,
        "display_name": staff.display_name,
    }


def merged_period_fields(period: SalaryPeriod, fields: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "period_start": period.period_start,
        "period_end": period.period_end,
        "business_date": period.business_date,
        "description": period.description,
    }
    merged.update(fields)
    return merged
