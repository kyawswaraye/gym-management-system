"""Explicit trainer and assignment HTTP contracts."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Sum
from rest_framework import serializers

from apps.cashbook.serializers import CashSessionSerializer, CashTransactionSerializer
from apps.expenses.serializers import ExpenseSerializer
from apps.members.models import Member, Payment
from apps.members.payment_serializers import PaymentSerializer, ReceiptSerializer, payment_payload
from apps.tenants.models import StaffProfile
from apps.trainers.models import (
    MemberServicePackage,
    PTBooking,
    PTCommission,
    PTCommissionPayment,
    PTSale,
    ServicePackage,
    ServiceSessionLedger,
    TrainerAssignment,
)
from apps.trainers.pt_sale_services import preview_sale_amounts
from apps.trainers.selectors import gym_local_date
from apps.users.serializers import StrictSerializer

_PACKAGE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")

_REASON_LIMIT = 500


def _required_reason(value: str) -> str:
    if value == "":
        raise serializers.ValidationError("Enter a reason.")
    return value


class TrainerIdentitySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    staff_code = serializers.CharField()
    display_name = serializers.CharField()


class AssignmentMemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class TrainerAssignmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    effective_from = serializers.DateField()
    effective_until = serializers.DateField(allow_null=True)
    status = serializers.ChoiceField(choices=TrainerAssignment.Status.choices)
    ended_at = serializers.DateTimeField(allow_null=True)
    reason = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    trainer = TrainerIdentitySerializer()
    member = AssignmentMemberSerializer()


class AssignedMemberSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    assignment_id = serializers.UUIDField()
    effective_from = serializers.DateField()
    effective_until = serializers.DateField(allow_null=True)


class TrainerListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class TrainerListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TrainerIdentitySerializer(many=True)
    meta = TrainerListMetaSerializer()
    message = serializers.CharField()


class AssignedMemberListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = AssignedMemberSerializer(many=True)
    meta = TrainerListMetaSerializer()
    message = serializers.CharField()


class TrainerAssignmentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TrainerAssignmentSerializer()
    message = serializers.CharField()


class TrainerAssignmentListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = TrainerAssignmentSerializer(many=True)
    meta = TrainerListMetaSerializer()
    message = serializers.CharField()


class PageQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")


class AssignmentListQuerySerializer(PageQuerySerializer):
    status = serializers.ChoiceField(choices=TrainerAssignment.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)


class AssignmentCreateSerializer(StrictSerializer):
    trainer_staff_profile_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    effective_from = serializers.DateField()
    effective_until = serializers.DateField(required=False, allow_null=True, default=None)
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=_REASON_LIMIT, default="", trim_whitespace=True
    )


class AssignmentEndSerializer(StrictSerializer):
    effective_until = serializers.DateField()
    reason = serializers.CharField(max_length=_REASON_LIMIT, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _required_reason(value)


class AssignmentCancelSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=_REASON_LIMIT, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _required_reason(value)


def trainer_payload(profile: StaffProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "staff_code": profile.staff_code,
        "display_name": profile.display_name,
    }


def _member_payload(member: Member) -> dict[str, object]:
    return {
        "id": member.id,
        "member_number": member.member_number,
        "first_name": member.first_name,
        "last_name": member.last_name,
    }


def assignment_payload(row: TrainerAssignment) -> dict[str, object]:
    return {
        "id": row.id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "member_id": row.member_id,
        "effective_from": row.effective_from,
        "effective_until": row.effective_until,
        "status": row.status,
        "ended_at": row.ended_at,
        "reason": row.reason,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "trainer": trainer_payload(row.trainer_staff_profile),
        "member": _member_payload(row.member),
    }


class PackagePriceField(serializers.DecimalField):
    """Accept an exact nonnegative decimal and reject extra fractional digits."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0"))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if isinstance(data, float):
            raise serializers.ValidationError("Price must be a decimal string.")
        if isinstance(data, str) and "e" in data.lower():
            raise serializers.ValidationError("Price must be a decimal string.")
        if isinstance(data, str) and "." in data and len(data.split(".", 1)[1]) > 2:
            raise serializers.ValidationError(
                "Ensure that there are no more than 2 decimal places."
            )
        return super().to_internal_value(data)


class ServicePackageWriteSerializer(StrictSerializer):
    type = serializers.ChoiceField(choices=ServicePackage.PackageType.choices)
    code = serializers.RegexField(_PACKAGE_CODE, max_length=32)
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    session_count = serializers.IntegerField(min_value=1)
    validity_value = serializers.IntegerField(min_value=1)
    validity_unit = serializers.ChoiceField(choices=ServicePackage.ValidityUnit.choices)
    price = PackagePriceField()
    class_type_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        package_type = attrs.get("type")
        class_type_id = attrs.get("class_type_id")
        if package_type == ServicePackage.PackageType.PT and class_type_id is not None:
            raise serializers.ValidationError(
                {"class_type_id": ["A PT package cannot name a class type."]}
            )
        if package_type == ServicePackage.PackageType.CLASS and class_type_id is None:
            raise serializers.ValidationError(
                {"class_type_id": ["A class package requires a class type."]}
            )
        return attrs


class ServicePackageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.CharField()
    code = serializers.CharField()
    name = serializers.CharField()
    session_count = serializers.IntegerField()
    validity_value = serializers.IntegerField()
    validity_unit = serializers.CharField()
    price = serializers.CharField()
    currency = serializers.CharField()
    class_type_id = serializers.UUIDField(allow_null=True)
    is_active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ServicePackageListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class ServicePackageEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ServicePackageSerializer()
    message = serializers.CharField()


class ServicePackageListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ServicePackageSerializer(many=True)
    meta = ServicePackageListMetaSerializer()
    message = serializers.CharField()


class ServicePackageListQuerySerializer(PageQuerySerializer):
    type = serializers.ChoiceField(choices=ServicePackage.PackageType.choices, required=False)
    is_active = serializers.ChoiceField(choices=("true", "false"), required=False)

    def validate_is_active(self, value: str) -> bool:
        return value == "true"


def service_package_payload(row: ServicePackage) -> dict[str, object]:
    return {
        "id": row.id,
        "type": row.type,
        "code": row.code,
        "name": row.name,
        "session_count": row.session_count,
        "validity_value": row.validity_value,
        "validity_unit": row.validity_unit,
        "price": f"{row.price:.2f}",
        "currency": row.currency,
        "class_type_id": row.class_type_id,
        "is_active": row.is_active,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SaleFeeField(serializers.DecimalField):
    """Accept an exact positive decimal string and reject extra fractional digits."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0.01"))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if isinstance(data, float):
            raise serializers.ValidationError("Fee must be a decimal string.")
        if isinstance(data, str) and "e" in data.lower():
            raise serializers.ValidationError("Fee must be a decimal string.")
        if isinstance(data, str) and "." in data and len(data.split(".", 1)[1]) > 2:
            raise serializers.ValidationError(
                "Ensure that there are no more than 2 decimal places."
            )
        return super().to_internal_value(data)


class PTSalePaymentDraftSerializer(StrictSerializer):
    """Client fields for the optional charge owned by a draft sale.

    Purpose, member, membership, currency, source, status, and actors are not
    accepted. The sale supplies them.
    """

    description = serializers.CharField(max_length=255, trim_whitespace=True)
    amount = SaleFeeField()
    method = serializers.ChoiceField(
        choices=("CASH", "BANK_TRANSFER", "CARD", "MOBILE_PAYMENT", "OTHER")
    )
    business_date = serializers.DateField()
    received_at = serializers.DateTimeField(required=False)
    reference_number = serializers.CharField(
        required=False, allow_blank=True, max_length=100, trim_whitespace=True
    )

    def validate_description(self, value: str) -> str:
        collapsed = " ".join(value.split())
        if collapsed == "":
            raise serializers.ValidationError("Enter a description.")
        return collapsed


class PTSaleWriteSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    service_date = serializers.DateField()
    description = serializers.CharField(max_length=500, trim_whitespace=True)
    gross_fee = SaleFeeField()
    currency = serializers.RegexField(r"^[A-Z]{3}$", max_length=3)
    payment = PTSalePaymentDraftSerializer(required=False, allow_null=True)
    service_package_id = serializers.UUIDField(required=False, allow_null=True)


class PTSaleUpdateSerializer(StrictSerializer):
    member_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    service_date = serializers.DateField(required=False)
    description = serializers.CharField(max_length=500, trim_whitespace=True, required=False)
    gross_fee = SaleFeeField(required=False)
    currency = serializers.RegexField(r"^[A-Z]{3}$", max_length=3, required=False)
    payment = PTSalePaymentDraftSerializer(required=False, allow_null=True)
    service_package_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Enter a field to update.")
        return attrs


class PTSaleCancelSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, trim_whitespace=True)


class PTSaleRejectSerializer(StrictSerializer):
    rejection_reason = serializers.CharField(max_length=500, trim_whitespace=True)


class PTSaleApproveSerializer(StrictSerializer):
    cash_session_id = serializers.UUIDField(required=False, allow_null=True)


class PTSalePayoutReversalSerializer(StrictSerializer):
    commission_payment_id = serializers.UUIDField()
    cash_session_id = serializers.UUIDField(allow_null=True)


class PTSaleVoidSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, trim_whitespace=True)
    business_date = serializers.DateField()
    cash_session_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    payout_reversals = PTSalePayoutReversalSerializer(many=True)

    def validate_reason(self, value: str) -> str:
        collapsed = " ".join(value.split())
        if collapsed == "":
            raise serializers.ValidationError("Enter a reason.")
        return collapsed


class PTSaleSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    service_date = serializers.DateField()
    description = serializers.CharField()
    gross_fee = serializers.CharField()
    currency = serializers.CharField()
    commission_percent_snapshot = serializers.CharField()
    commission_amount = serializers.CharField()
    gym_share = serializers.CharField()
    paid_amount = serializers.CharField()
    outstanding_amount = serializers.CharField()
    status = serializers.CharField()
    payment_id = serializers.UUIDField(allow_null=True)
    payment = PaymentSerializer(allow_null=True)
    service_package_id = serializers.UUIDField(allow_null=True)
    package_price_snapshot = serializers.CharField(allow_null=True)
    member_service_package_id = serializers.UUIDField(allow_null=True)
    submitted_at = serializers.DateTimeField(allow_null=True)
    submitted_by_membership_id = serializers.UUIDField(allow_null=True)
    approved_at = serializers.DateTimeField(allow_null=True)
    approved_by_membership_id = serializers.UUIDField(allow_null=True)
    rejected_at = serializers.DateTimeField(allow_null=True)
    rejected_by_membership_id = serializers.UUIDField(allow_null=True)
    rejection_reason = serializers.CharField()
    posted_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    cancelled_by_membership_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    reversal_pt_sale_id = serializers.UUIDField(allow_null=True)
    reason = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PTCommissionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    pt_sale_id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    gross_fee_snapshot = serializers.CharField()
    commission_percent_snapshot = serializers.CharField()
    commission_amount = serializers.CharField()
    gym_share = serializers.CharField()
    paid_amount = serializers.CharField()
    outstanding_amount = serializers.CharField()
    currency = serializers.CharField()
    status = serializers.CharField()
    paid_at = serializers.DateTimeField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class CommissionAmountField(serializers.DecimalField):
    """Accept an exact positive decimal string and reject extra fractional digits."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", Decimal("0.01"))
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if isinstance(data, float) or isinstance(data, int):
            raise serializers.ValidationError("Amount must be a decimal string.")
        if isinstance(data, str) and "e" in data.lower():
            raise serializers.ValidationError("Amount must be a decimal string.")
        if isinstance(data, str) and "." in data and len(data.split(".", 1)[1]) > 2:
            raise serializers.ValidationError(
                "Ensure that there are no more than 2 decimal places."
            )
        return super().to_internal_value(data)


class PTCommissionPaymentWriteSerializer(StrictSerializer):
    amount = CommissionAmountField()
    business_date = serializers.DateField()
    payment_method = serializers.ChoiceField(choices=Payment.Method.choices)
    cash_session_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    reference_number = serializers.CharField(
        required=False, allow_blank=True, max_length=100, default="", trim_whitespace=True
    )
    policy_note = serializers.CharField(
        required=False, allow_blank=True, max_length=2000, default="", trim_whitespace=True
    )


class PTCommissionListQuerySerializer(PageQuerySerializer):
    status = serializers.ChoiceField(choices=PTCommission.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    service_date_from = serializers.DateField(required=False)
    service_date_to = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        service_date_from = attrs.get("service_date_from")
        service_date_to = attrs.get("service_date_to")
        if (
            service_date_from is not None
            and service_date_to is not None
            and service_date_to < service_date_from
        ):
            raise serializers.ValidationError(
                {"service_date_to": ["Must be on or after service_date_from."]}
            )
        return attrs


class PTCommissionPaymentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    amount = serializers.CharField()
    business_date = serializers.DateField()
    payment_method = serializers.CharField()
    reference_number = serializers.CharField()
    policy_note = serializers.CharField()
    status = serializers.CharField()
    expense_id = serializers.UUIDField()
    cash_session_id = serializers.UUIDField(allow_null=True)
    cash_transaction_id = serializers.UUIDField(allow_null=True)
    reversal_expense_id = serializers.UUIDField(allow_null=True)
    reversal_cash_transaction_id = serializers.UUIDField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    voided_by_membership_id = serializers.UUIDField(allow_null=True)
    void_reason = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class PTCommissionDetailSerializer(PTCommissionSerializer):
    payments = PTCommissionPaymentSerializer(many=True)


class PTCommissionListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTCommissionSerializer(many=True)
    meta = TrainerListMetaSerializer()
    message = serializers.CharField()


class PTCommissionDetailEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTCommissionDetailSerializer()
    message = serializers.CharField()


class PTCommissionPaymentResultSerializer(serializers.Serializer):
    commission_before = PTCommissionSerializer()
    commission_after = PTCommissionSerializer()
    payment = PTCommissionPaymentSerializer()
    expense = ExpenseSerializer()
    cash_session = CashSessionSerializer(allow_null=True)
    cash_transaction = CashTransactionSerializer(allow_null=True)


class PTCommissionPaymentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTCommissionPaymentResultSerializer()
    message = serializers.CharField()


class MemberServicePackageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    service_package_id = serializers.UUIDField()
    source = serializers.CharField()
    pt_sale_id = serializers.UUIDField(allow_null=True)
    payment_id = serializers.UUIDField(allow_null=True)
    package_type = serializers.CharField()
    package_code = serializers.CharField()
    package_name = serializers.CharField()
    granted_sessions = serializers.IntegerField()
    used_sessions = serializers.IntegerField()
    remaining_sessions = serializers.IntegerField()
    amount_snapshot = serializers.CharField()
    currency = serializers.CharField()
    validity_value = serializers.IntegerField()
    validity_unit = serializers.CharField()
    valid_from = serializers.DateField()
    valid_until = serializers.DateField()
    complimentary = serializers.BooleanField()
    status = serializers.CharField()
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ServiceSessionLedgerSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    member_service_package_id = serializers.UUIDField()
    session_delta = serializers.IntegerField()
    reason = serializers.CharField()
    pt_booking_id = serializers.UUIDField(allow_null=True)
    class_booking_id = serializers.UUIDField(allow_null=True)
    reverses_entry_id = serializers.UUIDField(allow_null=True)
    occurred_at = serializers.DateTimeField()
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class MemberPackageGrantResultSerializer(serializers.Serializer):
    member_service_package = MemberServicePackageSerializer()
    ledger_entry = ServiceSessionLedgerSerializer()


class MemberPackageGrantEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberPackageGrantResultSerializer()
    message = serializers.CharField()


class MemberPackageEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberServicePackageSerializer()
    message = serializers.CharField()


class MemberPackageListQuerySerializer(PageQuerySerializer):
    member_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(
        choices=MemberServicePackage.Status.choices,
        required=False,
    )
    package_type = serializers.ChoiceField(
        choices=ServicePackage.PackageType.choices,
        required=False,
    )


class MemberPackageGrantSerializer(StrictSerializer):
    member_id = serializers.UUIDField()
    service_package_id = serializers.UUIDField()
    business_date = serializers.DateField()
    complimentary = serializers.BooleanField()
    reason = serializers.CharField(max_length=_REASON_LIMIT, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _required_reason(value)


class LedgerAdjustSerializer(StrictSerializer):
    reverses_entry_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=_REASON_LIMIT, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        return _required_reason(value)


class PTSaleApprovalSerializer(serializers.Serializer):
    sale = PTSaleSerializer()
    payment = PaymentSerializer(allow_null=True)
    receipt = ReceiptSerializer(allow_null=True)
    cash_session = CashSessionSerializer(allow_null=True)
    cash_transaction = CashTransactionSerializer(allow_null=True)
    commission = PTCommissionSerializer()
    member_service_package = MemberServicePackageSerializer(allow_null=True)


class PTSaleApprovalEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTSaleApprovalSerializer()
    message = serializers.CharField()


class PTSaleVoidPaymentSerializer(serializers.Serializer):
    original = PaymentSerializer()
    correction = PaymentSerializer()
    original_receipt = ReceiptSerializer(allow_null=True)
    correction_receipt = ReceiptSerializer()
    cash_transaction = CashTransactionSerializer(allow_null=True)


class PTSaleVoidSerializerResult(serializers.Serializer):
    sale = PTSaleSerializer()
    commission = PTCommissionSerializer()
    payouts = PTCommissionPaymentSerializer(many=True)
    payment = PTSaleVoidPaymentSerializer(allow_null=True)
    member_service_package = MemberServicePackageSerializer(allow_null=True)
    ledger_entry = ServiceSessionLedgerSerializer(allow_null=True)


class PTSaleVoidEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTSaleVoidSerializerResult()
    message = serializers.CharField()


class PTSaleListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class MemberPackageListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = MemberServicePackageSerializer(many=True)
    meta = PTSaleListMetaSerializer()
    message = serializers.CharField()


class LedgerListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ServiceSessionLedgerSerializer(many=True)
    meta = PTSaleListMetaSerializer()
    message = serializers.CharField()


class PTSaleEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTSaleSerializer()
    message = serializers.CharField()


class PTSaleListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTSaleSerializer(many=True)
    meta = PTSaleListMetaSerializer()
    message = serializers.CharField()


class PTSaleListQuerySerializer(PageQuerySerializer):
    status = serializers.ChoiceField(choices=PTSale.Status.choices, required=False)
    member_id = serializers.UUIDField(required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    service_date_from = serializers.DateField(required=False)
    service_date_to = serializers.DateField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        service_date_from = attrs.get("service_date_from")
        service_date_to = attrs.get("service_date_to")
        if (
            service_date_from is not None
            and service_date_to is not None
            and service_date_to < service_date_from
        ):
            raise serializers.ValidationError(
                {"service_date_to": ["Must be on or after service_date_from."]}
            )
        return attrs


def commission_payload(row: PTCommission) -> dict[str, object]:
    return {
        "id": row.id,
        "pt_sale_id": row.pt_sale_id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "gross_fee_snapshot": f"{row.gross_fee_snapshot:.2f}",
        "commission_percent_snapshot": f"{row.commission_percent_snapshot:.2f}",
        "commission_amount": f"{row.commission_amount:.2f}",
        "gym_share": f"{row.gym_share:.2f}",
        "paid_amount": f"{row.paid_amount:.2f}",
        "outstanding_amount": f"{row.outstanding_amount:.2f}",
        "currency": row.currency,
        "status": row.status,
        "paid_at": row.paid_at,
        "created_by_membership_id": row.created_by_membership_id,
        "created_at": row.created_at,
    }


def commission_payment_payload(row: PTCommissionPayment) -> dict[str, object]:
    return {
        "id": row.id,
        "amount": f"{row.amount:.2f}",
        "business_date": row.business_date,
        "payment_method": row.payment_method,
        "reference_number": row.reference_number,
        "policy_note": row.policy_note,
        "status": row.status,
        "expense_id": row.expense_id,
        "cash_session_id": row.cash_session_id,
        "cash_transaction_id": row.cash_transaction_id,
        "reversal_expense_id": row.reversal_expense_id,
        "reversal_cash_transaction_id": row.reversal_cash_transaction_id,
        "voided_at": row.voided_at,
        "voided_by_membership_id": row.voided_by_membership_id,
        "void_reason": row.void_reason,
        "created_by_membership_id": row.created_by_membership_id,
        "created_at": row.created_at,
    }


def pt_sale_payload(row: PTSale) -> dict[str, object]:
    commission, gym_share, paid, outstanding = preview_sale_amounts(
        gross_fee=row.gross_fee,
        percent=row.commission_percent_snapshot,
    )
    return {
        "id": row.id,
        "member_id": row.member_id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "service_date": row.service_date,
        "description": row.description,
        "gross_fee": f"{row.gross_fee:.2f}",
        "currency": row.currency,
        "commission_percent_snapshot": f"{row.commission_percent_snapshot:.2f}",
        "commission_amount": f"{commission:.2f}",
        "gym_share": f"{gym_share:.2f}",
        "paid_amount": f"{paid:.2f}",
        "outstanding_amount": f"{outstanding:.2f}",
        "status": row.status,
        "payment_id": row.payment_id,
        "payment": None if row.payment is None else payment_payload(row.payment),
        "service_package_id": row.service_package_id,
        "package_price_snapshot": _money(row.package_price_snapshot),
        "member_service_package_id": _granted_package_id(row),
        "submitted_at": row.submitted_at,
        "submitted_by_membership_id": row.submitted_by_membership_id,
        "approved_at": row.approved_at,
        "approved_by_membership_id": row.approved_by_membership_id,
        "rejected_at": row.rejected_at,
        "rejected_by_membership_id": row.rejected_by_membership_id,
        "rejection_reason": row.rejection_reason,
        "posted_at": row.posted_at,
        "cancelled_at": row.cancelled_at,
        "cancelled_by_membership_id": row.cancelled_by_membership_id,
        "voided_at": row.voided_at,
        "voided_by_membership_id": row.voided_by_membership_id,
        "reversal_pt_sale_id": row.reversal_pt_sale_id,
        "reason": row.reason,
        "created_by_membership_id": row.created_by_membership_id,
        "updated_by_membership_id": row.updated_by_membership_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"


def _granted_package_id(row: PTSale) -> object:
    if "granted_package_id" in row.__dict__:
        return row.__dict__["granted_package_id"]
    return (
        MemberServicePackage.objects.filter(gym_id=row.gym_id, pt_sale_id=row.id)
        .values_list("id", flat=True)
        .first()
    )


def member_package_payload(row: MemberServicePackage) -> dict[str, object]:
    if "remaining_sessions" in row.__dict__:
        remaining = int(row.__dict__["remaining_sessions"])
    else:
        total = row.ledger_entries.aggregate(total=Sum("session_delta"))["total"]
        remaining = 0 if total is None else int(total)
    if "effective_status" in row.__dict__:
        status = str(row.__dict__["effective_status"])
    else:
        status = _derived_package_status(row, remaining=remaining, today=gym_local_date(row.gym))
    granted = row.granted_sessions
    return {
        "id": row.id,
        "member_id": row.member_id,
        "service_package_id": row.service_package_id,
        "source": row.source,
        "pt_sale_id": row.pt_sale_id,
        "payment_id": row.payment_id,
        "package_type": row.package_type,
        "package_code": row.package_code,
        "package_name": row.package_name,
        "granted_sessions": granted,
        "used_sessions": granted - remaining,
        "remaining_sessions": remaining,
        "amount_snapshot": f"{row.amount_snapshot:.2f}",
        "currency": row.currency,
        "validity_value": row.validity_value,
        "validity_unit": row.validity_unit,
        "valid_from": row.valid_from,
        "valid_until": row.valid_until,
        "complimentary": row.complimentary,
        "status": status,
        "created_by_membership_id": row.created_by_membership_id,
        "updated_by_membership_id": row.updated_by_membership_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def ledger_entry_payload(row: ServiceSessionLedger) -> dict[str, object]:
    return {
        "id": row.id,
        "member_service_package_id": row.member_service_package_id,
        "session_delta": row.session_delta,
        "reason": row.reason,
        "pt_booking_id": row.pt_booking_id,
        "class_booking_id": row.class_booking_id,
        "reverses_entry_id": row.reverses_entry_id,
        "occurred_at": row.occurred_at,
        "created_by_membership_id": row.created_by_membership_id,
        "created_at": row.created_at,
    }


class PTBookingWriteSerializer(StrictSerializer):
    trainer_staff_profile_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    member_service_package_id = serializers.UUIDField(required=False, allow_null=True)
    unmetered = serializers.BooleanField(required=False, default=False)
    unmetered_reason = serializers.CharField(required=False, allow_blank=True, default="")


class PTBookingCancelSerializer(StrictSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="", max_length=500)


class PTBookingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    trainer_staff_profile_id = serializers.UUIDField()
    member_id = serializers.UUIDField()
    member_service_package_id = serializers.UUIDField(allow_null=True)
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    unmetered = serializers.BooleanField()
    unmetered_reason = serializers.CharField()
    status = serializers.CharField()
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


class PTBookingEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTBookingSerializer()
    message = serializers.CharField()


class PTBookingListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class PTBookingListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = PTBookingSerializer(many=True)
    meta = PTBookingListMetaSerializer()
    message = serializers.CharField()


class PTBookingListQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096, default="")
    status = serializers.ChoiceField(choices=PTBooking.Status.choices, required=False)
    trainer_staff_profile_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    view = serializers.ChoiceField(choices=["list", "calendar"], required=False, default="list")


def pt_booking_payload(row: PTBooking) -> dict[str, object]:
    return {
        "id": row.id,
        "trainer_staff_profile_id": row.trainer_staff_profile_id,
        "member_id": row.member_id,
        "member_service_package_id": row.member_service_package_id,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "unmetered": row.unmetered,
        "unmetered_reason": row.unmetered_reason,
        "status": row.status,
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


def _derived_package_status(
    row: MemberServicePackage,
    *,
    remaining: int,
    today: date,
) -> str:
    if row.status == MemberServicePackage.Status.VOID:
        return MemberServicePackage.Status.VOID
    if remaining <= 0:
        return MemberServicePackage.Status.EXHAUSTED
    if today > row.valid_until:
        return MemberServicePackage.Status.EXPIRED
    return MemberServicePackage.Status.ACTIVE


def assigned_member_payload(row: TrainerAssignment) -> dict[str, object]:
    member = row.member
    return {
        "id": member.id,
        "member_number": member.member_number,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "assignment_id": row.id,
        "effective_from": row.effective_from,
        "effective_until": row.effective_until,
    }
