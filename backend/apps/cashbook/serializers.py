from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.cashbook.models import CashSession, CashTransaction
from apps.common.finance.money import InvalidMoney, parse_money
from apps.users.serializers import StrictSerializer

_REGISTER = r"^[A-Z0-9_-]{1,32}$"


class ExactMoneyField(serializers.DecimalField):
    """Accept an exact decimal string and reject floats, integers, and exponents."""

    def __init__(self, *, minimum: Decimal, positive: bool = False, **kwargs: Any) -> None:
        self.minimum = minimum
        self.positive = positive
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", minimum)
        kwargs["coerce_to_string"] = True
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if not isinstance(data, str):
            raise serializers.ValidationError("Enter an exact decimal string.")
        try:
            parsed = parse_money(data)
        except InvalidMoney as exc:
            raise serializers.ValidationError("Enter an exact decimal string.") from exc
        if self.positive and parsed <= 0:
            raise serializers.ValidationError("Enter an amount greater than zero.")
        if parsed < self.minimum:
            raise serializers.ValidationError("Enter a non-negative amount.")
        return parsed

    def to_representation(self, value: Any) -> Any:
        if value is None:
            return None
        return format(parse_money(value), "f")


class OpenCashSessionSerializer(StrictSerializer):
    register_code = serializers.RegexField(_REGISTER, max_length=32)
    business_date = serializers.DateField()
    opening_cash = ExactMoneyField(minimum=Decimal("0"))


class AdjustCashSessionSerializer(StrictSerializer):
    direction = serializers.ChoiceField(choices=CashTransaction.Direction.choices)
    amount = ExactMoneyField(minimum=Decimal("0.01"), positive=True)
    currency = serializers.RegexField(r"^[A-Z]{3}$", max_length=3)
    business_date = serializers.DateField()
    description = serializers.CharField(max_length=255, allow_blank=True, trim_whitespace=True)
    reason = serializers.CharField(max_length=255, trim_whitespace=True)

    def validate_reason(self, value: str) -> str:
        if value == "":
            raise serializers.ValidationError("Enter a reason.")
        return value


class ReconcileCashSessionSerializer(StrictSerializer):
    actual_closing = ExactMoneyField(minimum=Decimal("0"))
    difference_reason = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default="",
        trim_whitespace=True,
    )


class CashSessionListQuerySerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=CashSession.Status.choices, required=False)
    register_code = serializers.RegexField(_REGISTER, max_length=32, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048, default="")


class CashTransactionListQuerySerializer(StrictSerializer):
    cash_session_id = serializers.UUIDField(required=False)
    business_date = serializers.DateField(required=False)
    source_type = serializers.ChoiceField(
        choices=CashTransaction.SourceType.choices,
        required=False,
    )
    direction = serializers.ChoiceField(choices=CashTransaction.Direction.choices, required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048, default="")


class CashSessionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    business_date = serializers.DateField()
    register_code = serializers.CharField()
    currency = serializers.CharField()
    opening_cash = ExactMoneyField(minimum=Decimal("0"), read_only=True)
    cash_sales = ExactMoneyField(minimum=Decimal("-9999999999999999.99"), read_only=True)
    cash_expenses = ExactMoneyField(minimum=Decimal("-9999999999999999.99"), read_only=True)
    manual_net = ExactMoneyField(minimum=Decimal("-9999999999999999.99"), read_only=True)
    expected_closing = ExactMoneyField(minimum=Decimal("-9999999999999999.99"), read_only=True)
    actual_closing = ExactMoneyField(
        minimum=Decimal("0"),
        read_only=True,
        allow_null=True,
    )
    difference = ExactMoneyField(
        minimum=Decimal("-9999999999999999.99"),
        read_only=True,
        allow_null=True,
    )
    difference_reason = serializers.CharField()
    status = serializers.CharField()
    opened_by_membership_id = serializers.UUIDField()
    closed_by_membership_id = serializers.UUIDField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class CashTransactionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    cash_session_id = serializers.UUIDField()
    direction = serializers.CharField()
    source_type = serializers.CharField()
    source_id = serializers.UUIDField(allow_null=True)
    amount = ExactMoneyField(minimum=Decimal("0.01"), read_only=True)
    currency = serializers.CharField()
    business_date = serializers.DateField()
    occurred_at = serializers.DateTimeField()
    description = serializers.CharField()
    reason = serializers.CharField()
    reversal_of_id = serializers.UUIDField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class CashCollectionMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class CashSessionEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CashSessionSerializer()
    message = serializers.CharField()


class CashCommandDataSerializer(serializers.Serializer):
    session = CashSessionSerializer()
    transactions = CashTransactionSerializer(many=True)


class CashCommandEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CashCommandDataSerializer()
    message = serializers.CharField()


class CashSessionListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CashSessionSerializer(many=True)
    meta = CashCollectionMetaSerializer()
    message = serializers.CharField()


class CashTransactionListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = CashTransactionSerializer(many=True)
    meta = CashCollectionMetaSerializer()
    message = serializers.CharField()


def money_text(value: Decimal) -> str:
    return format(parse_money(value), "f")


def session_payload(session: CashSession) -> dict[str, object]:
    return cast(dict[str, object], CashSessionSerializer(session).data)


def transaction_payload(transaction: CashTransaction) -> dict[str, object]:
    return cast(dict[str, object], CashTransactionSerializer(transaction).data)
