"""Strict product, category, and stock-adjustment contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from django.db.models import Q, Sum
from rest_framework import serializers

from apps.cashbook.serializers import ExactMoneyField
from apps.common.codes import SKU_CODE, OptionalPatternField
from apps.inventory.models import Product, ProductCategory, StockMovement
from apps.inventory.normalization import (
    canonical_barcode,
    canonical_category_name,
    canonical_display_name,
)
from apps.inventory.quantities import InvalidQuantity, format_quantity, parse_quantity
from apps.members.payment_serializers import money_text
from apps.users.serializers import StrictSerializer

_CODE = r"^[A-Z][A-Z0-9_]{0,31}$"
_SKU = r"^[A-Z0-9][A-Z0-9_-]{0,31}$"
_UNIT = r"^[A-Z][A-Z0-9_]{0,15}$"
_MANUAL_TYPES = (
    "OPENING",
    "PURCHASE",
    "DAMAGE",
    "LOSS",
    "ADJUSTMENT",
    "CORRECTION",
)


class ExactQuantityField(serializers.DecimalField):
    """Accept an exact three-place decimal string and reject overprecision."""

    def __init__(self, *, nonzero: bool = False, **kwargs: Any) -> None:
        self.nonzero = nonzero
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 3)
        kwargs["coerce_to_string"] = True
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> Decimal:
        if not isinstance(data, str):
            raise serializers.ValidationError("Enter an exact decimal string.")
        try:
            parsed = parse_quantity(data, nonzero=self.nonzero)
        except InvalidQuantity as exc:
            raise serializers.ValidationError("Enter an exact decimal string.") from exc
        if self.min_value is not None and parsed < self.min_value:
            raise serializers.ValidationError("Enter a non-negative quantity.")
        return parsed

    def to_representation(self, value: Any) -> Any:
        if value is None:
            return None
        return format_quantity(parse_quantity(value))


class ProductCategoryWriteSerializer(StrictSerializer):
    code = OptionalPatternField(_CODE, max_length=32)
    name = serializers.CharField(max_length=120, trim_whitespace=True)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_name(self, value: str) -> str:
        try:
            display, _normalized = canonical_category_name(value)
        except ValueError as exc:
            raise serializers.ValidationError("Enter a category name.") from exc
        return display


class ProductWriteSerializer(StrictSerializer):
    sku = OptionalPatternField(SKU_CODE, max_length=32)
    barcode = serializers.CharField(required=False, allow_null=True, max_length=80)
    name = serializers.CharField(max_length=255, trim_whitespace=True)
    category_id = serializers.UUIDField()
    unit = serializers.RegexField(_UNIT, max_length=16)
    sale_price = ExactMoneyField(minimum=Decimal("0"))
    current_unit_cost = ExactMoneyField(minimum=Decimal("0"))
    default_unit_cost = ExactMoneyField(minimum=Decimal("0"))
    low_stock_threshold = ExactQuantityField(min_value=Decimal("0"))
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_name(self, value: str) -> str:
        return _display(value)

    def validate_barcode(self, value: str | None) -> str | None:
        return _barcode(value)


class ProductUpdateSerializer(StrictSerializer):
    sku = serializers.RegexField(_SKU, max_length=32, required=False)
    barcode = serializers.CharField(required=False, allow_null=True, max_length=80)
    name = serializers.CharField(max_length=255, trim_whitespace=True, required=False)
    category_id = serializers.UUIDField(required=False)
    unit = serializers.RegexField(_UNIT, max_length=16, required=False)
    sale_price = ExactMoneyField(minimum=Decimal("0"), required=False)
    current_unit_cost = ExactMoneyField(minimum=Decimal("0"), required=False)
    default_unit_cost = ExactMoneyField(minimum=Decimal("0"), required=False)
    low_stock_threshold = ExactQuantityField(required=False, min_value=Decimal("0"))
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("Enter a field to update.")
        return attrs

    def validate_name(self, value: str) -> str:
        return _display(value)

    def validate_barcode(self, value: str | None) -> str | None:
        return _barcode(value)


class StockAdjustSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    quantity = ExactQuantityField(nonzero=True)
    movement_type = serializers.ChoiceField(choices=_MANUAL_TYPES)
    business_date = serializers.DateField()
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)
    unit_cost = ExactMoneyField(minimum=Decimal("0"), required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        purchase = attrs.get("movement_type") == "PURCHASE"
        has_cost = "unit_cost" in attrs
        if purchase and not has_cost:
            raise serializers.ValidationError({"unit_cost": ["Enter the purchase unit cost."]})
        if not purchase and has_cost:
            raise serializers.ValidationError({"unit_cost": ["Only a purchase records a unit cost."]})
        return attrs

    def validate_reason(self, value: str) -> str:
        if value == "":
            raise serializers.ValidationError("Enter an explanation.")
        return value


class ProductCategoryListQuerySerializer(StrictSerializer):
    is_active = serializers.ChoiceField(choices=("true", "false"), required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)

    def validate_is_active(self, value: str) -> bool:
        return value == "true"


class ProductListQuerySerializer(StrictSerializer):
    category_id = serializers.UUIDField(required=False)
    is_active = serializers.ChoiceField(choices=("true", "false"), required=False)
    sku = serializers.RegexField(_SKU, max_length=32, required=False)
    barcode = serializers.CharField(required=False, max_length=80)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)

    def validate_is_active(self, value: str) -> bool:
        return value == "true"

    def validate_barcode(self, value: str) -> str:
        normalized = _barcode(value)
        if normalized is None:
            raise serializers.ValidationError("Enter a barcode of letters and digits.")
        return normalized


class LowStockQuerySerializer(StrictSerializer):
    category_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class StockMovementListQuerySerializer(StrictSerializer):
    product_id = serializers.UUIDField(required=False)
    movement_type = serializers.ChoiceField(choices=StockMovement.Type.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=25)
    after = serializers.CharField(required=False, allow_blank=False, max_length=2048)


class ProductCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    code = serializers.CharField()
    name = serializers.CharField()
    is_active = serializers.BooleanField()
    archived_at = serializers.DateTimeField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class ProductSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    sku = serializers.CharField()
    barcode = serializers.CharField(allow_null=True)
    name = serializers.CharField()
    category_id = serializers.UUIDField()
    unit = serializers.CharField()
    sale_price = serializers.CharField()
    current_unit_cost = serializers.CharField()
    default_unit_cost = serializers.CharField()
    low_stock_threshold = serializers.CharField()
    quantity_on_hand = serializers.CharField()
    sold_quantity = serializers.CharField()
    revenue = serializers.CharField()
    cost_of_goods_sold = serializers.CharField()
    gross_profit = serializers.CharField()
    is_active = serializers.BooleanField()
    archived_at = serializers.DateTimeField(allow_null=True)
    created_by_membership_id = serializers.UUIDField()
    updated_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class StockMovementSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    quantity = serializers.CharField()
    quantity_on_hand = serializers.CharField()
    movement_type = serializers.CharField()
    unit_cost_snapshot = serializers.CharField()
    reason = serializers.CharField()
    business_date = serializers.DateField()
    created_by_membership_id = serializers.UUIDField()
    created_at = serializers.DateTimeField()


class ListMetaSerializer(serializers.Serializer):
    has_more = serializers.BooleanField()
    next_after = serializers.CharField(allow_null=True)


class ProductCategoryEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProductCategorySerializer()
    message = serializers.CharField()


class ProductCategoryListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProductCategorySerializer(many=True)
    meta = ListMetaSerializer()
    message = serializers.CharField()


class ProductEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProductSerializer()
    message = serializers.CharField()


class ProductListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = ProductSerializer(many=True)
    meta = ListMetaSerializer()
    message = serializers.CharField()


class StockMovementEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = StockMovementSerializer()
    message = serializers.CharField()


class StockMovementListEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = StockMovementSerializer(many=True)
    meta = ListMetaSerializer()
    message = serializers.CharField()


class StockAdjustmentSerializer(serializers.Serializer):
    movement = StockMovementSerializer()
    product = ProductSerializer()


class StockAdjustmentEnvelopeSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    data = StockAdjustmentSerializer()
    message = serializers.CharField()


def category_payload(category: ProductCategory) -> dict[str, object]:
    return cast(dict[str, object], ProductCategorySerializer(category).data)


def product_payload(product: Product) -> dict[str, object]:
    on_hand = getattr(product, "quantity_on_hand", _ZERO_QTY)
    sold = getattr(product, "sold_quantity", _ZERO_QTY)
    revenue = getattr(product, "retail_revenue", _ZERO_MONEY)
    cogs = getattr(product, "retail_cogs", _ZERO_MONEY)
    profit = getattr(product, "retail_gross_profit", _ZERO_MONEY)
    body = {
        "id": product.id,
        "sku": product.sku,
        "barcode": product.barcode,
        "name": product.name,
        "category_id": product.category_id,
        "unit": product.unit,
        "sale_price": money_text(product.sale_price),
        "current_unit_cost": money_text(product.current_unit_cost),
        "default_unit_cost": money_text(product.default_unit_cost),
        "low_stock_threshold": format_quantity(product.low_stock_threshold),
        "quantity_on_hand": format_quantity(on_hand),
        "sold_quantity": format_quantity(sold),
        "revenue": money_text(revenue),
        "cost_of_goods_sold": money_text(cogs),
        "gross_profit": money_text(profit),
        "is_active": product.is_active,
        "archived_at": product.archived_at,
        "created_by_membership_id": product.created_by_membership_id,
        "updated_by_membership_id": product.updated_by_membership_id,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }
    return cast(dict[str, object], ProductSerializer(body).data)


def movement_payload(movement: StockMovement) -> dict[str, object]:
    on_hand = getattr(movement, "quantity_on_hand", None)
    if on_hand is None:
        on_hand = _balance_through(movement)
    body = {
        "id": movement.id,
        "product_id": movement.product_id,
        "quantity": format_quantity(movement.quantity),
        "quantity_on_hand": format_quantity(on_hand),
        "movement_type": movement.movement_type,
        "unit_cost_snapshot": money_text(movement.unit_cost_snapshot),
        "reason": movement.reason,
        "business_date": movement.business_date,
        "created_by_membership_id": movement.created_by_membership_id,
        "created_at": movement.created_at,
    }
    return cast(dict[str, object], StockMovementSerializer(body).data)


_ZERO_QTY = Decimal("0.000")
_ZERO_MONEY = Decimal("0.00")


def _balance_through(movement: StockMovement) -> Decimal:
    total = (
        StockMovement.objects.filter(gym_id=movement.gym_id, product_id=movement.product_id)
        .filter(
            Q(created_at__lt=movement.created_at)
            | Q(created_at=movement.created_at, id__lte=movement.id)
        )
        .aggregate(total=Sum("quantity"))["total"]
    )
    return _ZERO_QTY if total is None else total


def _display(value: str) -> str:
    try:
        return canonical_display_name(value)
    except ValueError as exc:
        raise serializers.ValidationError("Enter a name.") from exc


def _barcode(value: str | None) -> str | None:
    try:
        return canonical_barcode(value)
    except ValueError as exc:
        raise serializers.ValidationError("Enter a barcode of letters and digits.") from exc
