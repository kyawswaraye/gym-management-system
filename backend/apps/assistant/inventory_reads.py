"""Product, stock, and posted retail-quantity reads.

On-hand quantity is the stored movement total. Low stock uses each product's
configured threshold. There is no separate invented cutoff.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db.models import Sum
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.inventory.models import Product, StockMovement
from apps.inventory.quantities import format_quantity
from apps.inventory.selectors import page_low_stock, page_movements, page_products
from apps.members.payment_serializers import money_text
from apps.sales.models import RetailSale, RetailSaleLine
from apps.users.serializers import StrictSerializer

SEARCH_PRODUCTS = "search_products"
SEARCH_STOCK_MOVEMENTS = "search_stock_movements"
SEARCH_LOW_STOCK = "search_low_stock"
SEARCH_PRODUCT_SALES = "search_product_sales"
INVENTORY_READ_TOOL_NAMES = (
    SEARCH_PRODUCTS,
    SEARCH_STOCK_MOVEMENTS,
    SEARCH_LOW_STOCK,
    SEARCH_PRODUCT_SALES,
)


class ProductReadQuerySerializer(StrictSerializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=255)
    sku = serializers.CharField(required=False, allow_blank=True, max_length=32)
    is_active = serializers.BooleanField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class MovementReadQuerySerializer(StrictSerializer):
    product_id = serializers.UUIDField(required=False)
    movement_type = serializers.ChoiceField(choices=StockMovement.Type.choices, required=False)
    business_date = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class LowStockReadQuerySerializer(StrictSerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=4096)


class ProductSaleReadQuerySerializer(StrictSerializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)


def inventory_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def inventory_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_products(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(ProductReadQuerySerializer, call.payload)
    active = query.get("is_active")
    rows, meta = page_products(
        gym_id=call.gym.id,
        actor_membership_id=call.actor.id,
        category_id=None,
        is_active=active if isinstance(active, bool) else None,
        sku=str(query.get("sku") or ""),
        barcode="",
        search=str(query.get("search") or ""),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _product_row)


def search_stock_movements(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(MovementReadQuerySerializer, call.payload)
    product_id = query.get("product_id")
    business_date = query.get("business_date")
    rows, meta = page_movements(
        gym_id=call.gym.id,
        actor_membership_id=call.actor.id,
        product_id=product_id if isinstance(product_id, UUID) else None,
        movement_type=str(query.get("movement_type") or ""),
        business_date=business_date if isinstance(business_date, date) else None,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    names = {
        str(row.id): row.name
        for row in Product.objects.filter(
            gym_id=call.gym.id,
            id__in=[item.product_id for item in rows],
        )
    }
    rendered = []
    for row in rows:
        rendered.append(
            {
                "id": str(row.id),
                "product_id": str(row.product_id),
                "product_name": names.get(str(row.product_id), ""),
                "movement_type": row.movement_type,
                "quantity": format_quantity(row.quantity),
                "business_date": row.business_date.isoformat(),
            }
        )
    return {
        "results": rendered,
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_low_stock(call: ReadCall) -> dict[str, object]:
    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(LowStockReadQuerySerializer, call.payload)
    rows, meta = page_low_stock(
        gym_id=call.gym.id,
        actor_membership_id=call.actor.id,
        category_id=None,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return _page(rows, meta, _product_row)


def search_product_sales(call: ReadCall) -> dict[str, object]:
    """Rank posted retail lines. Drafts and voids are not sales."""

    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(ProductSaleReadQuerySerializer, call.payload)
    start = query.get("date_from")
    end = query.get("date_to")
    queryset = RetailSaleLine.objects.filter(
        gym_id=call.gym.id,
        sale__gym_id=call.gym.id,
        sale__status=RetailSale.Status.POSTED,
    )
    if isinstance(start, date):
        queryset = queryset.filter(sale__business_date__gte=start)
    if isinstance(end, date):
        queryset = queryset.filter(sale__business_date__lte=end)
    grouped = (
        queryset.values("sku_snapshot", "name_snapshot")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_revenue"))
        .order_by("-quantity", "name_snapshot")[: int(query["limit"])]
    )
    return {
        "results": [
            {
                "sku": row["sku_snapshot"],
                "product_name": row["name_snapshot"],
                "quantity": format_quantity(row["quantity"] or Decimal("0")),
                "revenue": money_text(row["revenue"] or Decimal("0")),
            }
            for row in grouped
        ],
        "has_more": False,
        "next_after": None,
    }


def _product_row(row: Product) -> dict[str, object]:
    quantity = getattr(row, "quantity_on_hand", Decimal("0"))
    return {
        "id": str(row.id),
        "sku": row.sku,
        "name": row.name,
        "unit": row.unit,
        "sale_price": money_text(row.sale_price),
        "quantity_on_hand": format_quantity(quantity),
        "low_stock_threshold": format_quantity(row.low_stock_threshold),
        "status": "ACTIVE" if row.is_active else "INACTIVE",
    }


def _page(rows: list[Any], meta: Mapping[str, Any], render: Any) -> dict[str, object]:
    return {
        "results": [render(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def _definition(name: str, description: str, permission: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="inventory",
        operation="read",
        permissions=(permission,),
        features=("finance",),
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _query(kind: Any, payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = kind(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_PAGE = (
    FieldRequirement("limit", "Page size from 1 to 25. Optional.", required=False, kind="integer"),
    FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
)
_READS = {
    SEARCH_PRODUCTS: _contract(
        _definition(
            SEARCH_PRODUCTS,
            "Search products in this gym. Includes the stored on-hand quantity and "
            "the product's own low-stock threshold.",
            "inventory.view",
        ),
        (
            FieldRequirement("search", "Name, SKU, or barcode text. Optional.", required=False),
            FieldRequirement("sku", "Exact SKU. Optional.", required=False),
            FieldRequirement("is_active", "True for active products. Optional.", required=False),
            *_PAGE,
        ),
    ),
    SEARCH_STOCK_MOVEMENTS: _contract(
        _definition(
            SEARCH_STOCK_MOVEMENTS,
            "Search stock movements in this gym. Types include purchase, sale, "
            "adjustment, damage, and loss.",
            "inventory.view",
        ),
        (
            FieldRequirement("product_id", "Product id. Optional.", required=False),
            FieldRequirement("movement_type", "Stored movement type. Optional.", required=False),
            FieldRequirement("business_date", "Gym business date. Optional.", required=False),
            *_PAGE,
        ),
    ),
    SEARCH_LOW_STOCK: _contract(
        _definition(
            SEARCH_LOW_STOCK,
            "Search low-stock products in this gym. A product is low when on-hand "
            "quantity is at or below its stored threshold.",
            "inventory.view",
        ),
        _PAGE,
    ),
    SEARCH_PRODUCT_SALES: _contract(
        _definition(
            SEARCH_PRODUCT_SALES,
            "Rank posted retail product sales in this gym by quantity. "
            "Drafts and voids are excluded.",
            "retail.view",
        ),
        (
            FieldRequirement("date_from", "First business date. Optional.", required=False),
            FieldRequirement("date_to", "Last business date. Optional.", required=False),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
        ),
    ),
}
_READERS = {
    SEARCH_PRODUCTS: search_products,
    SEARCH_STOCK_MOVEMENTS: search_stock_movements,
    SEARCH_LOW_STOCK: search_low_stock,
    SEARCH_PRODUCT_SALES: search_product_sales,
}
