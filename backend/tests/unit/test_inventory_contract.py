from decimal import Decimal
from uuid import uuid4

from django.db.migrations.loader import MigrationLoader

from apps.inventory.quantities import InvalidQuantity, parse_quantity
from apps.inventory.serializers import (
    ProductCategoryWriteSerializer,
    ProductListQuerySerializer,
    ProductUpdateSerializer,
    ProductWriteSerializer,
    StockAdjustSerializer,
    StockMovementListQuerySerializer,
)
from apps.inventory.views import (
    ProductCategoryListCreateView,
    ProductDetailView,
    ProductListCreateView,
    ProductLowStockView,
    StockAdjustView,
    StockMovementListView,
)

INVENTORY_INITIAL = ("inventory", "0001_product_catalog_and_stock")
INVENTORY_LEAF = ("inventory", "0003_retail_sale_foundation")


def test_inventory_leaf_depends_on_the_finance_permission_catalog() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    plan = loader.graph.forwards_plan(INVENTORY_LEAF)
    assert plan == loader.graph.forwards_plan(INVENTORY_LEAF)
    assert ("tenants", "0025_finance_permission_catalog") in loader.disk_migrations[
        INVENTORY_INITIAL
    ].dependencies
    assert ("inventory", "0002_rls_integrity_and_grants") in loader.graph.forwards_plan(
        INVENTORY_LEAF
    )
    assert (
        INVENTORY_INITIAL
        in loader.disk_migrations[("inventory", "0002_rls_integrity_and_grants")].dependencies
    )
    assert loader.graph.leaf_nodes("inventory") == [INVENTORY_LEAF]
    loader.graph.ensure_not_cyclic()


def test_quantity_scale_rejects_float_exponent_and_overprecision() -> None:
    assert parse_quantity("1.5") == Decimal("1.500")
    assert parse_quantity("-2") == Decimal("-2.000")
    assert parse_quantity("0.000") == Decimal("0.000")
    for value in (1.25, float("nan"), float("inf"), "1e2", "NaN", "1.2345", "01.000", True, None):
        try:
            parse_quantity(value)
        except InvalidQuantity:
            continue
        raise AssertionError(value)
    try:
        parse_quantity("0.000", nonzero=True)
    except InvalidQuantity:
        return
    raise AssertionError("zero")


def test_catalog_write_rejects_mass_assignment_and_inexact_amounts() -> None:
    rejected = ProductWriteSerializer(
        data={
            "sku": "WATER-1",
            "name": "Water",
            "category_id": str(uuid4()),
            "unit": "BTL",
            "sale_price": "25.00",
            "current_unit_cost": "10.00",
            "default_unit_cost": "10.00",
            "low_stock_threshold": "2.000",
            "gym_id": str(uuid4()),
            "quantity_on_hand": "9.000",
            "sold_quantity": "1.000",
            "revenue": "25.00",
            "cost_of_goods_sold": "10.00",
            "gross_profit": "15.00",
            "created_by_membership_id": str(uuid4()),
        }
    )
    assert not rejected.is_valid()
    assert "gym_id" in rejected.errors
    assert "quantity_on_hand" in rejected.errors

    inexact = ProductWriteSerializer(
        data={
            "sku": "water-1",
            "name": "Water",
            "category_id": str(uuid4()),
            "unit": "btl",
            "sale_price": "25.555",
            "current_unit_cost": "1e1",
            "default_unit_cost": "10.00",
            "low_stock_threshold": "-1.000",
        }
    )
    assert not inexact.is_valid()

    category = ProductCategoryWriteSerializer(
        data={"code": "drinks", "name": "Drinks", "gym_id": str(uuid4())}
    )
    assert not category.is_valid()
    empty = ProductUpdateSerializer(data={})
    assert not empty.is_valid()
    forged = StockAdjustSerializer(
        data={
            "product_id": str(uuid4()),
            "quantity": "-1.000",
            "movement_type": "SALE",
            "business_date": "2026-10-18",
            "reason": "Forged sale",
            "reverses_movement_id": str(uuid4()),
            "unit_cost_snapshot": "4.00",
            "retail_sale_line_id": str(uuid4()),
        }
    )
    assert not forged.is_valid()
    assert "movement_type" in forged.errors or "reverses_movement_id" in forged.errors


def test_manual_adjustment_accepts_exact_signed_quantity() -> None:
    body = {
        "sku": "WATER-1",
        "barcode": "590 1234",
        "name": "  Spring   Water ",
        "category_id": str(uuid4()),
        "unit": "BTL",
        "sale_price": "25.00",
        "current_unit_cost": "10.50",
        "default_unit_cost": "10.00",
        "low_stock_threshold": "2",
    }
    created = ProductWriteSerializer(data=body)
    assert created.is_valid(), created.errors
    assert created.validated_data["barcode"] == "5901234"
    assert created.validated_data["name"] == "Spring Water"
    assert created.validated_data["low_stock_threshold"] == Decimal("2.000")

    adjusted = StockAdjustSerializer(
        data={
            "product_id": str(uuid4()),
            "quantity": "-1.5",
            "movement_type": "ADJUSTMENT",
            "business_date": "2026-10-18",
            "reason": "Count correction",
        }
    )
    assert adjusted.is_valid(), adjusted.errors
    assert adjusted.validated_data["quantity"] == Decimal("-1.500")
    query = ProductListQuerySerializer(data={"limit": 101, "sku": "WATER-1"})
    assert not query.is_valid()
    movements = StockMovementListQuerySerializer(data={"movement_type": "SALE", "limit": 25})
    assert movements.is_valid(), movements.errors


def test_inventory_routes_use_finance_entitlement_and_exact_permissions() -> None:
    assert ProductCategoryListCreateView.required_feature == "finance"
    assert ProductListCreateView.required_feature == "finance"
    assert ProductDetailView.required_feature == "finance"
    assert ProductLowStockView.required_feature == "finance"
    assert ProductLowStockView.required_permission == "inventory.view"
    assert StockMovementListView.required_permission == "inventory.view"
    assert StockAdjustView.required_permission == "inventory.adjust"
    assert StockAdjustView.required_feature == "finance"
