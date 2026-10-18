"""Retail sale contract checks that do not need PostgreSQL."""

import importlib
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db.migrations.loader import MigrationLoader

from apps.members.payment_serializers import PaymentWriteSerializer
from apps.sales.models import RetailReturn, RetailReturnLine
from apps.sales.pricing import allocate_cumulative_money, allocate_return_slice, extend_money
from apps.sales.serializers import (
    RetailSaleUpdateSerializer,
    RetailSaleWriteSerializer,
    ReturnRetailSaleSerializer,
    VoidRetailSaleSerializer,
)
from apps.sales.views import (
    RetailSaleApproveView,
    RetailSaleCancelView,
    RetailSaleDetailView,
    RetailSaleListCreateView,
    RetailSaleRejectView,
    RetailSaleReturnView,
    RetailSaleSubmitView,
    RetailSaleVoidView,
)

SALES_INITIAL = ("sales", "0001_retail_sale_foundation")
SALES_RLS = ("sales", "0002_rls_integrity_and_grants")
SALES_VOID = ("sales", "0003_retail_sale_void")
SALES_LEAF = ("sales", "0004_retail_return_foundation")


def test_retail_migration_graph_is_acyclic_and_explicit() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    loader.graph.ensure_not_cyclic()
    assert loader.graph.leaf_nodes("sales") == [SALES_LEAF]
    inventory_leaf = ("inventory", "0003_retail_sale_foundation")
    members_leaf = ("members", "0018_retail_sale_foundation")
    inventory_runtime = ("inventory", "0004_sale_return_runtime")
    inventory_return = ("inventory", "0005_retail_return_foundation")
    members_runtime = ("members", "0020_retail_product_reversal")
    members_refund = ("members", "0021_retail_product_refund")
    assert loader.graph.leaf_nodes("inventory") == [inventory_return]
    assert loader.graph.leaf_nodes("members") == [members_refund]
    initial = loader.disk_migrations[SALES_INITIAL].dependencies
    assert ("inventory", "0002_rls_integrity_and_grants") in initial
    assert ("members", "0017_payment_receipt_hardening") in initial
    assert SALES_RLS not in initial
    assert inventory_leaf not in initial
    assert members_leaf not in initial
    assert SALES_INITIAL in loader.disk_migrations[members_leaf].dependencies
    assert SALES_INITIAL in loader.disk_migrations[inventory_leaf].dependencies
    assert ("inventory", "0002_rls_integrity_and_grants") in loader.disk_migrations[
        inventory_leaf
    ].dependencies
    assert members_leaf not in loader.disk_migrations[inventory_leaf].dependencies
    assert inventory_leaf not in loader.disk_migrations[members_leaf].dependencies
    leaf = loader.disk_migrations[SALES_RLS].dependencies
    assert members_leaf in leaf
    assert inventory_leaf in leaf
    assert SALES_RLS not in loader.disk_migrations[inventory_leaf].dependencies
    assert SALES_RLS not in loader.disk_migrations[members_leaf].dependencies
    assert loader.disk_migrations[SALES_VOID].dependencies == [SALES_RLS]
    assert SALES_VOID in loader.disk_migrations[inventory_runtime].dependencies
    assert inventory_leaf in loader.disk_migrations[inventory_runtime].dependencies
    assert SALES_VOID in loader.disk_migrations[members_runtime].dependencies
    assert ("members", "0019_payment_receipt_runtime_reversal") in loader.disk_migrations[
        members_runtime
    ].dependencies
    plan = loader.graph.forwards_plan(SALES_RLS)
    assert plan.index(SALES_INITIAL) < plan.index(members_leaf) < plan.index(SALES_RLS)
    assert plan.index(SALES_INITIAL) < plan.index(inventory_leaf) < plan.index(SALES_RLS)
    runtime_plan = loader.graph.forwards_plan(inventory_runtime)
    assert runtime_plan.index(SALES_VOID) < runtime_plan.index(inventory_runtime)
    assert inventory_runtime not in loader.graph.forwards_plan(SALES_VOID)
    assert loader.disk_migrations[SALES_LEAF].dependencies == [
        ("cashbook", "0002_correction_runtime_reversal"),
        ("inventory", "0004_sale_return_runtime"),
        ("members", "0020_retail_product_reversal"),
        SALES_VOID,
        ("tenants", "0025_finance_permission_catalog"),
    ]
    assert loader.disk_migrations[inventory_return].dependencies == [
        inventory_runtime,
        SALES_LEAF,
    ]
    assert loader.disk_migrations[members_refund].dependencies == [
        members_runtime,
        SALES_LEAF,
    ]
    return_plan = loader.graph.forwards_plan(inventory_return)
    assert return_plan.index(SALES_LEAF) < return_plan.index(inventory_return)
    assert inventory_return not in loader.graph.forwards_plan(SALES_LEAF)
    assert members_refund not in loader.graph.forwards_plan(SALES_LEAF)


def test_quantity_times_money_rounds_half_up_to_minor_units() -> None:
    assert extend_money(Decimal("1.250"), Decimal("0.10")) == Decimal("0.13")
    assert extend_money(Decimal("1.250"), Decimal("10.00")) == Decimal("12.50")
    assert extend_money(Decimal("1.005"), Decimal("0.10")) == Decimal("0.10")
    assert extend_money(Decimal("0.001"), Decimal("0.01")) == Decimal("0.00")


def test_return_allocation_uses_half_up_and_keeps_the_final_remainder() -> None:
    whole = Decimal("3.000")
    total = Decimal("10.00")
    first = allocate_return_slice(total, Decimal("0"), Decimal("1.000"), whole)
    second = allocate_return_slice(total, Decimal("1.000"), Decimal("1.000"), whole)
    third = allocate_return_slice(total, Decimal("2.000"), Decimal("1.000"), whole)
    assert (first, second, third) == (Decimal("3.33"), Decimal("3.34"), Decimal("3.33"))
    assert first + second + third == total
    assert allocate_cumulative_money(total, whole, whole) == total

    discount = Decimal("0.01")
    slices = [
        allocate_return_slice(discount, Decimal("0"), Decimal("1"), whole),
        allocate_return_slice(discount, Decimal("1"), Decimal("1"), whole),
        allocate_return_slice(discount, Decimal("2"), Decimal("1"), whole),
    ]
    assert slices == [Decimal("0.00"), Decimal("0.01"), Decimal("0.00")]
    assert sum(slices, Decimal("0.00")) == discount

    revenue = Decimal("1.00")
    cost = Decimal("0.05")
    revenue_slices = []
    cost_slices = []
    prior = Decimal("0")
    for _step in range(3):
        revenue_slices.append(allocate_return_slice(revenue, prior, Decimal("1"), whole))
        cost_slices.append(allocate_return_slice(cost, prior, Decimal("1"), whole))
        prior += Decimal("1")
    profits = [left - right for left, right in zip(revenue_slices, cost_slices, strict=True)]
    assert profits == [Decimal("0.31"), Decimal("0.33"), Decimal("0.31")]
    assert sum(profits, Decimal("0.00")) == revenue - cost
    with pytest.raises(ValueError):
        allocate_cumulative_money(total, Decimal("4"), whole)
    with pytest.raises(ValueError):
        allocate_return_slice(total, Decimal("0"), Decimal("0"), whole)


def test_return_models_are_immutable_evidence_and_migrations_guard_them() -> None:
    stock_sql = importlib.import_module(
        "apps.inventory.migrations.0005_retail_return_foundation"
    ).FORWARD_SQL
    refund_sql = importlib.import_module(
        "apps.members.migrations.0021_retail_product_refund"
    ).FORWARD_SQL
    return_sql = importlib.import_module(
        "apps.sales.migrations.0004_retail_return_foundation"
    ).FORWARD_SQL
    assert RetailReturn._meta.db_table == "sales_retail_return"
    assert RetailReturnLine._meta.db_table == "sales_retail_return_line"
    assert "rret_settlement_ck" in {item.name for item in RetailReturn._meta.constraints}
    assert "rretline_one_movement_uniq" in {
        item.name for item in RetailReturnLine._meta.constraints
    }
    assert "ENABLE ROW LEVEL SECURITY" in return_sql
    assert "FORCE ROW LEVEL SECURITY" in return_sql
    assert "GRANT SELECT, INSERT ON TABLE sales_retail_return TO gym_runtime" in return_sql
    assert "GRANT SELECT, INSERT ON TABLE sales_retail_return_line TO gym_runtime" in return_sql
    assert "retail_return: returns are immutable" in return_sql
    assert "retail_return: money allocation does not match" in return_sql
    assert "retail_guard: a returned sale cannot be voided" in return_sql
    assert "app_allocate_cumulative_money" in return_sql
    assert "stock_one_return_line_uniq" not in stock_sql
    assert "stock_one_reversal_uniq" not in stock_sql
    assert "NEW.quantity <= line.quantity" in stock_sql
    assert "stock_guard: returned quantity exceeds the sale line" in stock_sql
    assert "stock_guard: a partial return requires return evidence" in stock_sql
    assert "a sale return requires the voided retail sale" in stock_sql
    assert "payment_guard: product refund requires the posted retail sale" in refund_sql
    assert "payment_guard: a product refund requires the retail return" in refund_sql
    assert "entry_type = 'REVERSAL'" in refund_sql


def test_retail_write_rejects_server_owned_fields_and_duplicate_lines() -> None:
    product_id = str(uuid4())
    rejected = RetailSaleWriteSerializer(
        data={
            "business_date": "2026-10-18",
            "payment_method": "CASH",
            "lines": [{"product_id": product_id, "quantity": "1.000", "discount": "0.00"}],
            "gym_id": str(uuid4()),
            "status": "POSTED",
            "currency": "MMK",
            "subtotal": "10.00",
            "total_amount": "10.00",
            "unit_cost": "4.00",
            "gross_profit": "6.00",
            "payment_id": str(uuid4()),
            "receipt_id": str(uuid4()),
            "created_by_membership_id": str(uuid4()),
        }
    )
    assert not rejected.is_valid()
    assert "gym_id" in rejected.errors
    assert "status" in rejected.errors
    assert "total_amount" in rejected.errors
    assert "payment_id" in rejected.errors

    duplicate = RetailSaleWriteSerializer(
        data={
            "business_date": "2026-10-18",
            "payment_method": "CARD",
            "lines": [
                {"product_id": product_id, "quantity": "2.000", "discount": "1.00"},
                {"product_id": product_id, "quantity": "1.000", "discount": "0.00"},
            ],
        }
    )
    assert not duplicate.is_valid()
    overprecise = RetailSaleWriteSerializer(
        data={
            "business_date": "2026-10-18",
            "payment_method": "CARD",
            "lines": [{"product_id": product_id, "quantity": "1.0001", "discount": "0.001"}],
        }
    )
    assert not overprecise.is_valid()
    ordered = RetailSaleWriteSerializer(
        data={
            "business_date": "2026-10-18",
            "payment_method": "CARD",
            "reference_number": "  REF  ",
            "lines": [
                {
                    "product_id": "00000000-0000-0000-0000-000000000002",
                    "quantity": "1",
                    "discount": "0.00",
                },
                {
                    "product_id": "00000000-0000-0000-0000-000000000001",
                    "quantity": "1.500",
                    "discount": "0.50",
                },
            ],
        }
    )
    assert ordered.is_valid(), ordered.errors
    assert [row["product_id"].hex for row in ordered.validated_data["lines"]] == [
        "00000000000000000000000000000001",
        "00000000000000000000000000000002",
    ]
    assert ordered.validated_data["lines"][0]["quantity"] == Decimal("1.500")
    assert ordered.validated_data["reference_number"] == "REF"
    assert not RetailSaleUpdateSerializer(data={}).is_valid()


def test_public_payment_still_rejects_product_purpose_and_retail_link() -> None:
    rejected = PaymentWriteSerializer(
        data={
            "purpose": "PRODUCT",
            "description": "Direct product draft",
            "amount": "10.00",
            "currency": "MMK",
            "method": "CASH",
            "business_date": "2026-10-18",
            "retail_sale_id": str(uuid4()),
        }
    )
    assert not rejected.is_valid()
    assert "purpose" in rejected.errors or "retail_sale_id" in rejected.errors


def test_retail_routes_use_finance_entitlement_and_exact_permissions() -> None:
    assert RetailSaleListCreateView.required_feature == "finance"
    assert RetailSaleDetailView.required_feature == "finance"
    assert RetailSaleSubmitView.required_permission == "retail.submit"
    assert RetailSaleApproveView.required_permission == "retail.approve"
    assert RetailSaleRejectView.required_permission == "retail.approve"
    assert RetailSaleCancelView.required_permission == "retail.update"
    assert RetailSaleVoidView.required_permission == "retail.void"
    assert RetailSaleVoidView.required_feature == "finance"
    assert RetailSaleReturnView.required_permission == "retail.void"
    assert RetailSaleReturnView.required_feature == "finance"


def test_retail_void_request_rejects_blank_reason_and_server_fields() -> None:
    blank = VoidRetailSaleSerializer(data={"reason": "  "})
    assert not blank.is_valid()
    assert "reason" in blank.errors
    extra = VoidRetailSaleSerializer(
        data={"reason": "Wrong sale", "status": "VOID", "payment_id": str(uuid4())}
    )
    assert not extra.is_valid()
    accepted = VoidRetailSaleSerializer(
        data={"reason": "  Customer changed their mind  ", "cash_session_id": None}
    )
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["reason"] == "Customer changed their mind"


def test_retail_return_request_sorts_unique_lines_and_rejects_server_fields() -> None:
    first = uuid4()
    second = uuid4()
    blank = ReturnRetailSaleSerializer(data={"reason": " ", "lines": []})
    assert not blank.is_valid()
    duplicate = ReturnRetailSaleSerializer(
        data={
            "reason": "Damaged",
            "lines": [
                {"line_id": str(first), "quantity": "1.000"},
                {"line_id": str(first), "quantity": "1.000"},
            ],
        }
    )
    assert not duplicate.is_valid()
    extra = ReturnRetailSaleSerializer(
        data={
            "reason": "Damaged",
            "refund_amount": "1.00",
            "lines": [{"line_id": str(first), "quantity": "1.000"}],
        }
    )
    assert not extra.is_valid()
    accepted = ReturnRetailSaleSerializer(
        data={
            "reason": "  Damaged bottle  ",
            "lines": [
                {"line_id": str(second), "quantity": "1.000"},
                {"line_id": str(first), "quantity": "2.500"},
            ],
        }
    )
    assert accepted.is_valid(), accepted.errors
    assert accepted.validated_data["reason"] == "Damaged bottle"
    assert [row["line_id"] for row in accepted.validated_data["lines"]] == sorted((first, second))
