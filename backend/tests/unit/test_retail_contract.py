"""Retail sale contract checks that do not need PostgreSQL."""

from decimal import Decimal
from uuid import uuid4

from django.db.migrations.loader import MigrationLoader

from apps.members.payment_serializers import PaymentWriteSerializer
from apps.sales.pricing import extend_money
from apps.sales.serializers import RetailSaleUpdateSerializer, RetailSaleWriteSerializer
from apps.sales.views import (
    RetailSaleApproveView,
    RetailSaleCancelView,
    RetailSaleDetailView,
    RetailSaleListCreateView,
    RetailSaleRejectView,
    RetailSaleSubmitView,
)

SALES_INITIAL = ("sales", "0001_retail_sale_foundation")
SALES_LEAF = ("sales", "0002_rls_integrity_and_grants")


def test_retail_migration_graph_is_acyclic_and_explicit() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    loader.graph.ensure_not_cyclic()
    assert loader.graph.leaf_nodes("sales") == [SALES_LEAF]
    assert loader.graph.leaf_nodes("members") == [
        ("members", "0019_payment_receipt_runtime_reversal")
    ]
    inventory_leaf = ("inventory", "0003_retail_sale_foundation")
    members_leaf = ("members", "0018_retail_sale_foundation")
    assert loader.graph.leaf_nodes("inventory") == [inventory_leaf]
    initial = loader.disk_migrations[SALES_INITIAL].dependencies
    assert ("inventory", "0002_rls_integrity_and_grants") in initial
    assert ("members", "0017_payment_receipt_hardening") in initial
    assert ("sales", "0002_rls_integrity_and_grants") not in initial
    assert inventory_leaf not in initial
    assert members_leaf not in initial
    assert SALES_INITIAL in loader.disk_migrations[members_leaf].dependencies
    assert SALES_INITIAL in loader.disk_migrations[inventory_leaf].dependencies
    assert ("inventory", "0002_rls_integrity_and_grants") in loader.disk_migrations[
        inventory_leaf
    ].dependencies
    assert members_leaf not in loader.disk_migrations[inventory_leaf].dependencies
    assert inventory_leaf not in loader.disk_migrations[members_leaf].dependencies
    leaf = loader.disk_migrations[SALES_LEAF].dependencies
    assert members_leaf in leaf
    assert inventory_leaf in leaf
    assert SALES_LEAF not in loader.disk_migrations[inventory_leaf].dependencies
    assert SALES_LEAF not in loader.disk_migrations[members_leaf].dependencies
    plan = loader.graph.forwards_plan(SALES_LEAF)
    assert plan.index(SALES_INITIAL) < plan.index(members_leaf) < plan.index(SALES_LEAF)
    assert plan.index(SALES_INITIAL) < plan.index(inventory_leaf) < plan.index(SALES_LEAF)


def test_quantity_times_money_rounds_half_up_to_minor_units() -> None:
    assert extend_money(Decimal("1.250"), Decimal("0.10")) == Decimal("0.13")
    assert extend_money(Decimal("1.250"), Decimal("10.00")) == Decimal("12.50")
    assert extend_money(Decimal("1.005"), Decimal("0.10")) == Decimal("0.10")
    assert extend_money(Decimal("0.001"), Decimal("0.01")) == Decimal("0.00")


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
