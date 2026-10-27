"""Retail sale contract checks that do not need PostgreSQL."""

import importlib
from decimal import Decimal
from pathlib import Path
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
    members_pt_link = ("members", "0022_pt_sale_payment_link")
    members_pt_guard = ("members", "0023_pt_payment_guard")
    members_pt_void = ("members", "0024_pt_sale_payment_void")
    members_phase7 = ("members", "0025_phase7_notifications")
    members_device_guard = ("members", "0026_member_device_rls_and_guards")
    members_owner_approval = ("members", "0027_owner_may_approve_own_payment")
    members_desk_approval = ("members", "0028_desk_may_approve_own_payment")
    trainers_sale = ("trainers", "0005_pt_sale")
    trainers_sale_rls = ("trainers", "0006_pt_sale_rls_and_grants")
    trainers_pt_link = ("trainers", "0007_pt_sale_payment_link")
    trainers_pt_guard = ("trainers", "0008_pt_sale_workflow_guards")
    trainers_pt_payout = ("trainers", "0009_pt_commission_payment")
    trainers_pt_payout_guard = ("trainers", "0010_pt_commission_payment_guards")
    trainers_pt_aggregate = ("trainers", "0011_pt_commission_aggregate_guard")
    trainers_package = ("trainers", "0012_pt_sale_package_and_ledger")
    trainers_package_guard = ("trainers", "0013_member_package_ledger_guards")
    trainers_void_fields = ("trainers", "0014_pt_sale_void_fields")
    trainers_void_guards = ("trainers", "0015_pt_sale_void_guards")
    trainers_refund_link = ("trainers", "0016_pt_sale_refund_reversal_link")
    trainers_class_link = ("trainers", "0017_class_schedule_catalog")
    trainers_pt_booking = ("trainers", "0018_booking_foundation")
    trainers_booking_link = ("trainers", "0019_booking_ledger_links")
    trainers_payout_window = ("trainers", "0021_pt_commission_payout_window")
    classes_type_rls = ("classes", "0002_class_type_rls_and_grants")
    classes_guards = ("classes", "0004_class_schedule_guards")
    classes_booking = ("classes", "0006_class_booking_guards")
    classes_terminal = ("classes", "0008_member_self_actors")
    assert loader.graph.leaf_nodes("inventory") == [inventory_return]
    assert loader.graph.leaf_nodes("members") == [members_desk_approval]
    assert loader.graph.leaf_nodes("trainers") == [trainers_payout_window]
    assert loader.graph.leaf_nodes("classes") == [classes_terminal]
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
    assert loader.disk_migrations[trainers_sale].dependencies == [
        members_refund,
        ("tenants", "0026_pt_schedule_permission_catalog"),
        ("trainers", "0004_service_package_rls_and_grants"),
    ]
    assert loader.disk_migrations[trainers_sale_rls].dependencies == [trainers_sale]
    assert loader.disk_migrations[members_pt_link].dependencies == [
        ("cashbook", "0002_correction_runtime_reversal"),
        members_refund,
        SALES_LEAF,
        ("tenants", "0026_pt_schedule_permission_catalog"),
        trainers_sale_rls,
    ]
    assert loader.disk_migrations[trainers_pt_link].dependencies == [
        members_pt_link,
        ("tenants", "0026_pt_schedule_permission_catalog"),
        trainers_sale_rls,
    ]
    assert loader.disk_migrations[members_pt_guard].dependencies == [
        members_pt_link,
        trainers_pt_link,
    ]
    assert loader.disk_migrations[trainers_pt_guard].dependencies == [
        trainers_pt_link,
        members_pt_guard,
    ]
    assert loader.disk_migrations[trainers_pt_payout].dependencies == [
        ("cashbook", "0002_correction_runtime_reversal"),
        ("expenses", "0005_salary_void_set"),
        ("tenants", "0026_pt_schedule_permission_catalog"),
        trainers_pt_guard,
    ]
    assert loader.disk_migrations[trainers_pt_payout_guard].dependencies == [trainers_pt_payout]
    assert loader.disk_migrations[trainers_pt_aggregate].dependencies == [trainers_pt_payout_guard]
    assert loader.disk_migrations[trainers_package].dependencies == [
        members_pt_guard,
        ("tenants", "0027_pt_commission_pay_reservation"),
        trainers_pt_aggregate,
    ]
    assert loader.disk_migrations[trainers_package_guard].dependencies == [trainers_package]
    assert loader.disk_migrations[trainers_void_fields].dependencies == [
        members_pt_guard,
        ("tenants", "0027_pt_commission_pay_reservation"),
        trainers_package_guard,
    ]
    assert loader.disk_migrations[trainers_void_guards].dependencies == [trainers_void_fields]
    assert loader.disk_migrations[trainers_refund_link].dependencies == [
        ("tenants", "0027_pt_commission_pay_reservation"),
        trainers_void_guards,
    ]
    assert loader.disk_migrations[trainers_class_link].dependencies == [
        classes_type_rls,
        ("tenants", "0028_class_permission_catalog"),
        trainers_refund_link,
    ]
    assert loader.disk_migrations[classes_guards].dependencies == [
        ("classes", "0003_class_schedule_and_occurrence"),
    ]
    assert loader.disk_migrations[members_pt_void].dependencies == [members_pt_guard]
    assert members_pt_void in loader.disk_migrations[members_phase7].dependencies
    assert loader.disk_migrations[members_device_guard].dependencies == [members_phase7]
    assert loader.disk_migrations[members_owner_approval].dependencies == [members_device_guard]
    assert loader.disk_migrations[members_desk_approval].dependencies == [members_owner_approval]
    payout_source = (
        Path(__file__).resolve().parents[2]
        / "apps/trainers/migrations/0009_pt_commission_payment.py"
    ).read_text()
    assert "commission_expense_id IS NOT NULL" in payout_source
    assert "reversal_commission_id IS NOT NULL" in payout_source
    assert "legacy expense links are not null" in payout_source
    pt_plan = loader.graph.forwards_plan(trainers_refund_link)
    assert pt_plan.index(members_refund) < pt_plan.index(trainers_sale)
    assert pt_plan.index(trainers_sale) < pt_plan.index(trainers_sale_rls)
    assert pt_plan.index(trainers_sale_rls) < pt_plan.index(members_pt_link)
    assert pt_plan.index(SALES_LEAF) < pt_plan.index(members_pt_link)
    assert pt_plan.index(members_pt_link) < pt_plan.index(trainers_pt_link)
    assert pt_plan.index(trainers_pt_link) < pt_plan.index(members_pt_guard)
    assert pt_plan.index(members_pt_guard) < pt_plan.index(trainers_pt_guard)
    assert pt_plan.index(trainers_pt_guard) < pt_plan.index(trainers_pt_payout)
    assert pt_plan.index(trainers_pt_payout) < pt_plan.index(trainers_pt_payout_guard)
    assert pt_plan.index(trainers_pt_payout_guard) < pt_plan.index(trainers_pt_aggregate)
    assert pt_plan.index(trainers_pt_aggregate) < pt_plan.index(trainers_package)
    assert pt_plan.index(trainers_package) < pt_plan.index(trainers_package_guard)
    assert pt_plan.index(trainers_package_guard) < pt_plan.index(trainers_void_fields)
    assert pt_plan.index(trainers_void_fields) < pt_plan.index(trainers_void_guards)
    assert pt_plan.index(trainers_void_guards) < pt_plan.index(trainers_refund_link)
    class_plan = loader.graph.forwards_plan(trainers_class_link)
    assert class_plan.index(trainers_refund_link) < class_plan.index(trainers_class_link)
    assert class_plan.index(classes_type_rls) < class_plan.index(trainers_class_link)
    assert ("classes", "0004_class_schedule_guards") not in class_plan
    booking_plan = loader.graph.forwards_plan(trainers_booking_link)
    assert booking_plan.index(trainers_pt_booking) < booking_plan.index(classes_booking)
    assert booking_plan.index(classes_booking) < booking_plan.index(trainers_booking_link)
    assert ("classes", "0005_booking_foundation") not in loader.graph.forwards_plan(
        trainers_pt_booking
    )
    assert classes_booking not in loader.graph.forwards_plan(members_pt_void)
    assert trainers_package not in loader.graph.forwards_plan(members_pt_guard)
    assert trainers_void_guards not in loader.graph.forwards_plan(members_pt_void)
    assert trainers_refund_link not in loader.graph.forwards_plan(members_pt_void)
    assert trainers_class_link not in loader.graph.forwards_plan(members_pt_void)
    assert classes_guards not in loader.graph.forwards_plan(members_pt_void)
    assert trainers_package_guard not in loader.graph.forwards_plan(
        ("tenants", "0027_pt_commission_pay_reservation")
    )
    assert trainers_sale not in loader.graph.forwards_plan(members_refund)
    assert members_pt_link not in loader.graph.forwards_plan(trainers_sale_rls)
    assert trainers_pt_link not in loader.graph.forwards_plan(members_pt_link)
    assert members_pt_guard not in loader.graph.forwards_plan(trainers_pt_link)
    assert trainers_pt_guard not in loader.graph.forwards_plan(members_pt_guard)


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
