"""Tenant-scoped catalog and stock reads.

Quantity on hand is the sum of same-gym movements, including a full void's
positive sale return. Sold quantity, revenue, cost of goods sold, and gross
profit count posted and voided sale lines, then subtract each full void once.
Draft, pending, rejected, and cancelled sales contribute nothing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.inventory.models import Product, ProductCategory, StockMovement
from apps.sales.models import RetailSaleLine

_CATEGORY_SALT = "product-category-list"
_PRODUCT_SALT = "product-list"
_LOW_STOCK_SALT = "product-low-stock"
_MOVEMENT_SALT = "stock-movement-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_ZERO = Decimal("0.000")
_ZERO_MONEY = Decimal("0.00")
_QTY_FIELD: DecimalField[Decimal, Decimal] = DecimalField(max_digits=18, decimal_places=3)
_MONEY_FIELD: DecimalField[Decimal, Decimal] = DecimalField(max_digits=18, decimal_places=2)


def with_quantity_on_hand(queryset: QuerySet[Product], *, gym_id: UUID) -> QuerySet[Product]:
    return queryset.annotate(
        quantity_on_hand=Coalesce(
            Sum("stock_movements__quantity", filter=Q(stock_movements__gym_id=gym_id)),
            Value(_ZERO),
            output_field=_QTY_FIELD,
        ),
        sold_quantity=_posted_sum("quantity", gym_id=gym_id, output=_QTY_FIELD, zero=_ZERO),
        retail_revenue=_posted_sum(
            "line_revenue", gym_id=gym_id, output=_MONEY_FIELD, zero=_ZERO_MONEY
        ),
        retail_cogs=_posted_sum(
            "cost_of_goods_sold", gym_id=gym_id, output=_MONEY_FIELD, zero=_ZERO_MONEY
        ),
        retail_gross_profit=_posted_sum(
            "gross_profit", gym_id=gym_id, output=_MONEY_FIELD, zero=_ZERO_MONEY
        ),
    )


def _line_sum(
    field: str, *, gym_id: UUID, statuses: tuple[str, ...], output: DecimalField[Decimal, Decimal]
) -> Subquery:
    rows = (
        RetailSaleLine.objects.filter(
            gym_id=gym_id,
            product_id=OuterRef("pk"),
            sale__gym_id=gym_id,
            sale__status__in=statuses,
        )
        .values("product_id")
        .annotate(total=Sum(field))
        .values("total")
    )
    return Subquery(rows, output_field=output)


def _posted_sum(
    field: str, *, gym_id: UUID, output: DecimalField[Decimal, Decimal], zero: Decimal
) -> ExpressionWrapper[Any]:
    """Posted and void originals, minus each full void exactly once."""

    original = Coalesce(
        _line_sum(field, gym_id=gym_id, statuses=("POSTED", "VOID"), output=output),
        Value(zero, output_field=output),
        output_field=output,
    )
    returned = Coalesce(
        _line_sum(field, gym_id=gym_id, statuses=("VOID",), output=output),
        Value(zero, output_field=output),
        output_field=output,
    )
    return ExpressionWrapper(original - returned, output_field=output)


def get_category(*, gym_id: UUID, category_id: UUID) -> ProductCategory:
    category = ProductCategory.objects.filter(gym_id=gym_id, id=category_id).first()
    if category is None:
        raise Http404
    return category


def get_product(*, gym_id: UUID, product_id: UUID) -> Product:
    product = with_quantity_on_hand(
        Product.objects.filter(gym_id=gym_id, id=product_id),
        gym_id=gym_id,
    ).first()
    if product is None:
        raise Http404
    return product


def page_categories(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    is_active: bool | None,
    limit: int,
    after: str,
) -> tuple[list[ProductCategory], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "is_active": "" if is_active is None else str(is_active),
        "limit": str(limit),
        "ordering": "code ASC, id ASC",
    }
    queryset = ProductCategory.objects.filter(gym_id=gym_id)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    queryset = queryset.order_by("code", "id")
    return _page_code(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_CATEGORY_SALT,
        code_attr="code",
    )


def page_products(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    category_id: UUID | None,
    is_active: bool | None,
    sku: str,
    barcode: str,
    limit: int,
    after: str,
) -> tuple[list[Product], dict[str, Any]]:
    if category_id is not None:
        get_category(gym_id=gym_id, category_id=category_id)
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "category_id": "" if category_id is None else str(category_id),
        "is_active": "" if is_active is None else str(is_active),
        "sku": sku,
        "barcode": barcode,
        "limit": str(limit),
        "ordering": "sku ASC, id ASC",
    }
    queryset = Product.objects.filter(gym_id=gym_id)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    if sku:
        queryset = queryset.filter(sku=sku)
    if barcode:
        queryset = queryset.filter(barcode=barcode)
    queryset = with_quantity_on_hand(queryset, gym_id=gym_id).order_by("sku", "id")
    return _page_code(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_PRODUCT_SALT,
        code_attr="sku",
    )


def page_low_stock(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    category_id: UUID | None,
    limit: int,
    after: str,
) -> tuple[list[Product], dict[str, Any]]:
    if category_id is not None:
        get_category(gym_id=gym_id, category_id=category_id)
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "category_id": "" if category_id is None else str(category_id),
        "active": "true",
        "limit": str(limit),
        "ordering": "sku ASC, id ASC",
    }
    queryset = Product.objects.filter(gym_id=gym_id, is_active=True)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)
    queryset = (
        with_quantity_on_hand(queryset, gym_id=gym_id)
        .filter(quantity_on_hand__lte=F("low_stock_threshold"))  # type: ignore[misc]
        .order_by("sku", "id")
    )
    return _page_code(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_LOW_STOCK_SALT,
        code_attr="sku",
    )


def page_movements(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    product_id: UUID | None,
    movement_type: str,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[StockMovement], dict[str, Any]]:
    if product_id is not None and not Product.objects.filter(gym_id=gym_id, id=product_id).exists():
        raise Http404
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "product_id": "" if product_id is None else str(product_id),
        "movement_type": movement_type,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "created_at DESC, id DESC",
    }
    queryset = StockMovement.objects.filter(gym_id=gym_id)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)
    if movement_type:
        queryset = queryset.filter(movement_type=movement_type)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-created_at", "-id")
    return _page_time(queryset, after=after, limit=limit, scope=scope, salt=_MOVEMENT_SALT)


def _page_code(
    queryset: QuerySet[Any],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
    code_attr: str,
) -> tuple[list[Any], dict[str, Any]]:
    if after:
        loaded = _decode(after, scope=scope, salt=salt)
        code = str(loaded[f"position_{code_attr}"])
        row_id = uuid.UUID(str(loaded["id"]))
        queryset = queryset.filter(
            Q(**{f"{code_attr}__gt": code}) | Q(**{code_attr: code, "id__gt": row_id})
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {
                f"position_{code_attr}": str(getattr(last, code_attr)),
                "id": str(last.id),
                **scope,
            },
            salt=salt,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


def _page_time(
    queryset: QuerySet[Any],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
) -> tuple[list[Any], dict[str, Any]]:
    if after:
        moment, row_id = _decode_time(after, scope=scope, salt=salt)
        queryset = queryset.filter(Q(created_at__lt=moment) | Q(created_at=moment, id__lt=row_id))
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=salt,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


def _decode_time(token: str, *, scope: dict[str, str], salt: str) -> tuple[datetime, UUID]:
    loaded = _decode(token, scope=scope, salt=salt)
    raw_time = loaded.get("created_at")
    if not isinstance(raw_time, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    try:
        moment = datetime.fromisoformat(raw_time)
        row_id = uuid.UUID(str(loaded["id"]))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    if moment.tzinfo is None:
        raise ValidationError({"after": ["Invalid cursor."]})
    return moment, row_id


def _decode(token: str, *, scope: dict[str, str], salt: str) -> dict[str, Any]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
    except (signing.BadSignature, signing.SignatureExpired) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return loaded
