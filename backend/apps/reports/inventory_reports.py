"""Product sales, stock movement, and inventory reports.

Product sales reuse the period-report correction dates. Posted and void sale
lines add on the sale business date. A full void subtracts on the void
business date. A partial return subtracts on the return business date. Cost,
revenue, and profit come from the immutable line snapshots.

Stock rows keep the stored signed quantity on ``business_date``. Inventory is
the current catalog plus the current sum of same-gym movements.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Count, DecimalField, F, OuterRef, Q, QuerySet, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError

from apps.inventory.models import Product, StockMovement
from apps.inventory.quantities import format_quantity
from apps.members.payment_serializers import money_text
from apps.reports.ranges import iter_dates
from apps.sales.models import RetailReturnLine, RetailSale, RetailSaleLine
from apps.tenants.models import Gym

PRODUCT_SALES_QUERY_COUNT = 8
STOCK_MOVEMENT_QUERY_COUNT = 1
INVENTORY_QUERY_COUNT = 3
PRODUCT_SALES_SOURCE_STATUS_POLICY = (
    "posted_and_void_sale_lines_on_business_date__full_void_on_void_business_date__"
    "partial_returns_on_return_business_date__immutable_line_snapshots__"
    "not_current_product_cost"
)
STOCK_MOVEMENT_SOURCE_STATUS_POLICY = (
    "stock_movement_business_date_and_stored_signed_quantity__"
    "opening_purchase_sale_sale_return_damage_loss_adjustment_correction__"
    "not_rewritten_absolute_quantity"
)
INVENTORY_SOURCE_STATUS_POLICY = (
    "current_catalog_and_current_same_gym_movement_sum_at_generation__"
    "stock_quantity_lte_threshold_is_low_stock__includes_archived_products__"
    "not_historical_point_in_time"
)

_ZERO = Decimal("0.00")
_ZERO_QTY = Decimal("0.000")
_MONEY: DecimalField[Any, Any] = DecimalField(max_digits=18, decimal_places=2)
_QTY: DecimalField[Any, Any] = DecimalField(max_digits=18, decimal_places=3)
_ORDERING = "sku ASC, id ASC"
_CURSOR_MAX_AGE = 24 * 60 * 60
_PRODUCT_SALES_SALT = "report-product-sales"
_INVENTORY_SALT = "report-inventory"
_SALE_STATUSES = (RetailSale.Status.POSTED, RetailSale.Status.VOID)
_STOCK_FIELDS = (
    "opening_quantity",
    "purchase_quantity",
    "sale_quantity",
    "sale_return_quantity",
    "damage_quantity",
    "loss_quantity",
    "adjustment_quantity",
    "correction_quantity",
)
_STOCK_TYPES = (
    ("opening_quantity", StockMovement.Type.OPENING),
    ("purchase_quantity", StockMovement.Type.PURCHASE),
    ("sale_quantity", StockMovement.Type.SALE),
    ("sale_return_quantity", StockMovement.Type.SALE_RETURN),
    ("damage_quantity", StockMovement.Type.DAMAGE),
    ("loss_quantity", StockMovement.Type.LOSS),
    ("adjustment_quantity", StockMovement.Type.ADJUSTMENT),
    ("correction_quantity", StockMovement.Type.CORRECTION),
)


@dataclass
class _Metric:
    quantity: Decimal = _ZERO_QTY
    revenue: Decimal = _ZERO
    cogs: Decimal = _ZERO
    profit: Decimal = _ZERO

    def add(
        self,
        *,
        quantity: Decimal,
        revenue: Decimal,
        cogs: Decimal,
        profit: Decimal,
        sign: int,
    ) -> None:
        factor = Decimal(sign)
        self.quantity += quantity * factor
        self.revenue += revenue * factor
        self.cogs += cogs * factor
        self.profit += profit * factor


def product_sales_export(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Return every catalog product for the range, using the paged report totals.

    One catalog read, three grouped snapshot queries, three range totals, and
    one catalog count. That is the same eight queries as one page. The catalog
    read is bounded by the products in this gym.
    """

    products = list(Product.objects.filter(gym_id=gym.id).order_by("sku", "id"))
    metrics = {product.id: _Metric() for product in products}
    _collect_product_metrics(
        gym_id=gym.id,
        date_from=date_from,
        date_to=date_to,
        product_ids=None,
        metrics=metrics,
        catalog=True,
    )
    totals = _Metric()
    _collect_product_metrics(
        gym_id=gym.id,
        date_from=date_from,
        date_to=date_to,
        product_ids=None,
        metrics=None,
        totals=totals,
    )
    counts = _product_counts(gym.id)
    return _product_sales_payload(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        rows=[_product_sales_row(product, metrics[product.id]) for product in products],
        totals=totals,
        counts=counts,
    )


def product_sales_report(
    *,
    gym: Gym,
    actor_membership_id: UUID,
    date_from: date,
    date_to: date,
    limit: int,
    after: str,
    generated_at: datetime,
) -> dict[str, object]:
    """Page the catalog and total every product in the selected range."""

    scope = _scope(
        route="product-sales",
        gym_id=gym.id,
        actor_membership_id=actor_membership_id,
        limit=limit,
        filters={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
    )
    page, meta = _page_products(
        Product.objects.filter(gym_id=gym.id),
        after=after,
        limit=limit,
        scope=scope,
        salt=_PRODUCT_SALES_SALT,
    )
    page_ids = [product.id for product in page]
    metrics = {product_id: _Metric() for product_id in page_ids}
    _collect_product_metrics(
        gym_id=gym.id,
        date_from=date_from,
        date_to=date_to,
        product_ids=page_ids,
        metrics=metrics,
    )
    totals = _Metric()
    _collect_product_metrics(
        gym_id=gym.id,
        date_from=date_from,
        date_to=date_to,
        product_ids=None,
        metrics=None,
        totals=totals,
    )
    payload = _product_sales_payload(
        gym=gym,
        date_from=date_from,
        date_to=date_to,
        generated_at=generated_at,
        rows=[_product_sales_row(product, metrics[product.id]) for product in page],
        totals=totals,
        counts=_product_counts(gym.id),
    )
    payload["meta"] = meta
    return payload


def stock_movement_report(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
) -> dict[str, object]:
    """Zero-fill one signed quantity row for every date in the range."""

    grouped = _stock_days(gym_id=gym.id, date_from=date_from, date_to=date_to)
    rows = [_stock_row(day, grouped.get(day)) for day in iter_dates(date_from, date_to)]
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "range": {"date_from": date_from, "date_to": date_to},
        "generated_at": generated_at,
        "source_status_policy": STOCK_MOVEMENT_SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": _stock_totals(rows),
    }


def inventory_export(
    *,
    gym: Gym,
    generated_at: datetime,
) -> dict[str, object]:
    """Return the whole current catalog and the same inventory totals.

    One catalog read, one grouped movement sum, and one totals query. That is
    the same three queries as one page, bounded by this gym's products.
    """

    products = list(
        Product.objects.filter(gym_id=gym.id).select_related("category").order_by("sku", "id")
    )
    quantities = _page_stock(gym_id=gym.id, product_ids=[], catalog=True)
    return _inventory_payload(
        gym=gym,
        generated_at=generated_at,
        rows=[
            _inventory_row(product, quantities.get(product.id, _ZERO_QTY)) for product in products
        ],
        totals=_inventory_totals(gym_id=gym.id),
    )


def inventory_report(
    *,
    gym: Gym,
    actor_membership_id: UUID,
    limit: int,
    after: str,
    generated_at: datetime,
) -> dict[str, object]:
    """Page the current catalog and sum current same-gym movements."""

    scope = _scope(
        route="inventory",
        gym_id=gym.id,
        actor_membership_id=actor_membership_id,
        limit=limit,
        filters={},
    )
    page, meta = _page_products(
        Product.objects.filter(gym_id=gym.id).select_related("category"),
        after=after,
        limit=limit,
        scope=scope,
        salt=_INVENTORY_SALT,
    )
    quantities = _page_stock(gym_id=gym.id, product_ids=[product.id for product in page])
    payload = _inventory_payload(
        gym=gym,
        generated_at=generated_at,
        rows=[_inventory_row(product, quantities.get(product.id, _ZERO_QTY)) for product in page],
        totals=_inventory_totals(gym_id=gym.id),
    )
    payload["meta"] = meta
    return payload


def _product_counts(gym_id: UUID) -> dict[str, int]:
    counts = Product.objects.filter(gym_id=gym_id).aggregate(
        product_count=Count("id"),
        active_product_count=Count("id", filter=Q(is_active=True)),
    )
    return {
        "product_count": _count(counts["product_count"]),
        "active_product_count": _count(counts["active_product_count"]),
    }


def _product_sales_payload(
    *,
    gym: Gym,
    date_from: date,
    date_to: date,
    generated_at: datetime,
    rows: list[dict[str, object]],
    totals: _Metric,
    counts: dict[str, int],
) -> dict[str, object]:
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "currency": gym.currency,
        "range": {"date_from": date_from, "date_to": date_to},
        "generated_at": generated_at,
        "source_status_policy": PRODUCT_SALES_SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": {
            "quantity_sold": format_quantity(_quantity(totals.quantity)),
            "revenue": money_text(_money(totals.revenue)),
            "cogs": money_text(_money(totals.cogs)),
            "gross_profit": money_text(_money(totals.profit)),
            "product_count": counts["product_count"],
            "active_product_count": counts["active_product_count"],
        },
    }


def _inventory_payload(
    *,
    gym: Gym,
    generated_at: datetime,
    rows: list[dict[str, object]],
    totals: dict[str, object],
) -> dict[str, object]:
    return {
        "gym": {"id": gym.id, "code": gym.code, "name": gym.name},
        "timezone": gym.timezone,
        "generated_at": generated_at,
        "source_status_policy": INVENTORY_SOURCE_STATUS_POLICY,
        "rows": rows,
        "totals": totals,
    }


def _collect_product_metrics(
    *,
    gym_id: UUID,
    date_from: date,
    date_to: date,
    product_ids: list[UUID] | None,
    metrics: dict[UUID, _Metric] | None,
    totals: _Metric | None = None,
    catalog: bool = False,
) -> None:
    """Add sale, void, and return snapshots.

    ``product_ids`` limits the three grouped queries to one page. ``None``
    totals the whole range. ``catalog`` groups every same-gym product without
    a page filter. An empty page still evaluates the grouped queries.
    """

    for sign, rows in (
        (
            1,
            _sale_groups(
                gym_id,
                date_from,
                date_to,
                product_ids,
                correction=False,
                catalog=catalog,
            ),
        ),
        (
            -1,
            _sale_groups(
                gym_id,
                date_from,
                date_to,
                product_ids,
                correction=True,
                catalog=catalog,
            ),
        ),
        (-1, _return_groups(gym_id, date_from, date_to, product_ids, catalog=catalog)),
    ):
        for row in rows:
            product_id = row.get("product_id")
            quantity = _decimal(row.get("quantity"), _ZERO_QTY)
            revenue = _decimal(row.get("revenue"), _ZERO)
            cogs = _decimal(row.get("cogs"), _ZERO)
            profit = _decimal(row.get("profit"), _ZERO)
            if totals is not None:
                totals.add(
                    quantity=quantity,
                    revenue=revenue,
                    cogs=cogs,
                    profit=profit,
                    sign=sign,
                )
            if metrics is not None and isinstance(product_id, UUID) and product_id in metrics:
                metrics[product_id].add(
                    quantity=quantity,
                    revenue=revenue,
                    cogs=cogs,
                    profit=profit,
                    sign=sign,
                )


def _sale_groups(
    gym_id: UUID,
    date_from: date,
    date_to: date,
    product_ids: list[UUID] | None,
    *,
    correction: bool,
    catalog: bool = False,
) -> list[dict[str, object]]:
    queryset = RetailSaleLine.objects.filter(
        gym_id=gym_id,
        sale__gym_id=gym_id,
        product__gym_id=gym_id,
    )
    if correction:
        queryset = queryset.filter(
            sale__status=RetailSale.Status.VOID,
            sale__void_business_date__gte=date_from,
            sale__void_business_date__lte=date_to,
        )
    else:
        queryset = queryset.filter(
            sale__status__in=_SALE_STATUSES,
            sale__business_date__gte=date_from,
            sale__business_date__lte=date_to,
        )
    if not catalog:
        queryset = _limit_products(queryset, product_ids, "product_id")
    grouped = (
        queryset.order_by()
        .values("product_id")
        .annotate(
            quantity=Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY),
            revenue=Coalesce(Sum("line_revenue"), Value(_ZERO), output_field=_MONEY),
            cogs=Coalesce(Sum("cost_of_goods_sold"), Value(_ZERO), output_field=_MONEY),
            profit=Coalesce(Sum("gross_profit"), Value(_ZERO), output_field=_MONEY),
        )
    )
    if product_ids is None and not catalog:
        stats = queryset.aggregate(
            quantity=Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY),
            revenue=Coalesce(Sum("line_revenue"), Value(_ZERO), output_field=_MONEY),
            cogs=Coalesce(Sum("cost_of_goods_sold"), Value(_ZERO), output_field=_MONEY),
            profit=Coalesce(Sum("gross_profit"), Value(_ZERO), output_field=_MONEY),
        )
        return [stats]
    return list(grouped)


def _return_groups(
    gym_id: UUID,
    date_from: date,
    date_to: date,
    product_ids: list[UUID] | None,
    *,
    catalog: bool = False,
) -> list[dict[str, object]]:
    queryset = RetailReturnLine.objects.filter(
        gym_id=gym_id,
        retail_return__gym_id=gym_id,
        sale_line__gym_id=gym_id,
        sale_line__product__gym_id=gym_id,
        retail_return__business_date__gte=date_from,
        retail_return__business_date__lte=date_to,
    )
    if not catalog:
        queryset = _limit_products(queryset, product_ids, "sale_line__product_id")
    if product_ids is None and not catalog:
        return [
            queryset.aggregate(
                quantity=Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY),
                revenue=Coalesce(Sum("refund_revenue"), Value(_ZERO), output_field=_MONEY),
                cogs=Coalesce(Sum("cost_of_goods_sold"), Value(_ZERO), output_field=_MONEY),
                profit=Coalesce(Sum("gross_profit"), Value(_ZERO), output_field=_MONEY),
            )
        ]
    return [
        {
            "product_id": row["sale_line__product_id"],
            "quantity": row["quantity"],
            "revenue": row["revenue"],
            "cogs": row["cogs"],
            "profit": row["profit"],
        }
        for row in queryset.order_by()
        .values("sale_line__product_id")
        .annotate(
            quantity=Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY),
            revenue=Coalesce(Sum("refund_revenue"), Value(_ZERO), output_field=_MONEY),
            cogs=Coalesce(Sum("cost_of_goods_sold"), Value(_ZERO), output_field=_MONEY),
            profit=Coalesce(Sum("gross_profit"), Value(_ZERO), output_field=_MONEY),
        )
    ]


def _limit_products(
    queryset: QuerySet[Any],
    product_ids: list[UUID] | None,
    field: str,
) -> QuerySet[Any]:
    if product_ids is None:
        return queryset
    return queryset.filter(**{f"{field}__in": product_ids})


def _stock_days(*, gym_id: UUID, date_from: date, date_to: date) -> dict[date, dict[str, Decimal]]:
    annotations: dict[str, Coalesce] = {
        name: Coalesce(
            Sum("quantity", filter=Q(movement_type=movement_type)),
            Value(_ZERO_QTY),
            output_field=_QTY,
        )
        for name, movement_type in _STOCK_TYPES
    }
    annotations["net_quantity"] = Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY)
    grouped = (
        StockMovement.objects.filter(
            gym_id=gym_id,
            product__gym_id=gym_id,
            business_date__gte=date_from,
            business_date__lte=date_to,
        )
        .order_by()
        .values("business_date")
        .annotate(**annotations)
    )
    days: dict[date, dict[str, Decimal]] = {}
    for row in grouped:
        business_date = row["business_date"]
        if not isinstance(business_date, date):
            continue
        days[business_date] = {
            name: _quantity(row[name]) for name in (*_STOCK_FIELDS, "net_quantity")
        }
    return days


def _stock_row(day: date, values: dict[str, Decimal] | None) -> dict[str, object]:
    source = values or {name: _ZERO_QTY for name in (*_STOCK_FIELDS, "net_quantity")}
    row: dict[str, object] = {"date": day}
    for name in (*_STOCK_FIELDS, "net_quantity"):
        row[name] = format_quantity(_quantity(source.get(name, _ZERO_QTY)))
    return row


def _stock_totals(rows: list[dict[str, object]]) -> dict[str, str]:
    totals = {name: _ZERO_QTY for name in (*_STOCK_FIELDS, "net_quantity")}
    for row in rows:
        for name in totals:
            raw = row[name]
            if isinstance(raw, str):
                totals[name] += Decimal(raw)
    return {name: format_quantity(_quantity(value)) for name, value in totals.items()}


def _page_stock(
    *,
    gym_id: UUID,
    product_ids: list[UUID],
    catalog: bool = False,
) -> dict[UUID, Decimal]:
    queryset = StockMovement.objects.filter(gym_id=gym_id, product__gym_id=gym_id)
    if not catalog:
        queryset = queryset.filter(product_id__in=product_ids)
    grouped = (
        queryset.order_by()
        .values("product_id")
        .annotate(total=Coalesce(Sum("quantity"), Value(_ZERO_QTY), output_field=_QTY))
    )
    return {
        row["product_id"]: _quantity(row["total"])
        for row in grouped
        if isinstance(row["product_id"], UUID)
    }


def _inventory_totals(*, gym_id: UUID) -> dict[str, object]:
    movement_total = (
        StockMovement.objects.filter(gym_id=OuterRef("gym_id"), product_id=OuterRef("pk"))
        .values("product_id")
        .annotate(total=Sum("quantity"))
        .values("total")[:1]
    )
    annotated = Product.objects.filter(gym_id=gym_id).annotate(
        stock_quantity=Coalesce(
            Subquery(movement_total, output_field=_QTY),
            Value(_ZERO_QTY),
            output_field=_QTY,
        )
    )
    stats = annotated.aggregate(
        product_count=Count("id"),
        active_product_count=Count("id", filter=Q(is_active=True)),
        low_stock_product_count=Count(
            "id",
            filter=Q(stock_quantity__lte=F("low_stock_threshold")),
        ),
        total_stock_quantity=Coalesce(
            Sum("stock_quantity"),
            Value(_ZERO_QTY),
            output_field=_QTY,
        ),
    )
    return {
        "product_count": _count(stats["product_count"]),
        "active_product_count": _count(stats["active_product_count"]),
        "low_stock_product_count": _count(stats["low_stock_product_count"]),
        "total_stock_quantity": format_quantity(_quantity(stats["total_stock_quantity"])),
    }


def _product_sales_row(product: Product, metric: _Metric) -> dict[str, object]:
    return {
        "product_id": product.id,
        "sku": product.sku,
        "name": product.name,
        "is_active": product.is_active,
        "quantity_sold": format_quantity(_quantity(metric.quantity)),
        "revenue": money_text(_money(metric.revenue)),
        "cogs": money_text(_money(metric.cogs)),
        "gross_profit": money_text(_money(metric.profit)),
    }


def _inventory_row(product: Product, stock: Decimal) -> dict[str, object]:
    quantity = _quantity(stock)
    threshold = _quantity(product.low_stock_threshold)
    return {
        "product_id": product.id,
        "category_code": product.category.code,
        "category_name": product.category.name,
        "sku": product.sku,
        "name": product.name,
        "unit": product.unit,
        "is_active": product.is_active,
        "stock_quantity": format_quantity(quantity),
        "low_stock_threshold": format_quantity(threshold),
        "is_low_stock": quantity <= threshold,
    }


def _page_products(
    queryset: QuerySet[Product],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
) -> tuple[list[Product], dict[str, object]]:
    if after:
        sku, row_id = _decode_position(after, scope=scope, salt=salt)
        queryset = queryset.filter(Q(sku__gt=sku) | Q(sku=sku, id__gt=row_id))
    rows = list(queryset.order_by("sku", "id")[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {"position_sku": last.sku, "id": str(last.id), **scope},
            salt=salt,
            compress=True,
        )
    return page, {"next_after": next_after, "has_more": has_more}


def _scope(
    *,
    route: str,
    gym_id: UUID,
    actor_membership_id: UUID,
    limit: int,
    filters: dict[str, str],
) -> dict[str, str]:
    scope = {
        "route": route,
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "limit": str(limit),
        "ordering": _ORDERING,
    }
    scope.update(filters)
    return scope


def _decode_position(token: str, *, scope: dict[str, str], salt: str) -> tuple[str, UUID]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict) or any(
            loaded.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        sku = loaded.get("position_sku")
        raw_id = loaded.get("id")
        if not isinstance(sku, str) or not isinstance(raw_id, str):
            raise signing.BadSignature
        return sku, UUID(raw_id)
    except (signing.BadSignature, signing.SignatureExpired, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc


def _money(value: Decimal) -> Decimal:
    quantized = value.quantize(_ZERO)
    if quantized == 0:
        return _ZERO
    return quantized


def _quantity(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        return _ZERO_QTY
    quantized = value.quantize(_ZERO_QTY)
    if quantized == 0:
        return _ZERO_QTY
    return quantized


def _decimal(value: object, zero: Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return zero


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
