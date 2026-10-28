from decimal import Decimal
from uuid import UUID, uuid4

from django.db.migrations.loader import MigrationLoader

from apps.salaries.serializers import (
    ApproveSalarySerializer,
    SalaryLinesReplaceSerializer,
    SalaryPeriodListQuerySerializer,
    SalaryPeriodUpdateSerializer,
    SalaryPeriodWriteSerializer,
    VoidSalarySerializer,
)

EXPENSE_POSTING_COMMIT = ("expenses", "0003_expense_posting_commit_current_row")
SALARY_INITIAL = ("salaries", "0001_salary_period_and_line")
SALARY_RLS = ("salaries", "0002_rls_integrity_and_grants")
SALARY_LEAF = ("salaries", "0003_salary_period_void")
EXPENSE_VOID_SET = ("expenses", "0005_salary_void_set")
EXPENSE_LEAF = ("expenses", "0006_owner_or_desk_may_approve_own_expense")
SALARY_APP_PLAN = (
    ("expenses", "0001_expense_receipt_and_expense"),
    ("expenses", "0002_rls_integrity_and_grants"),
    EXPENSE_POSTING_COMMIT,
    SALARY_INITIAL,
    SALARY_RLS,
)


def test_salary_leaf_requires_expense_posting_commit_migration() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    first_plan = loader.graph.forwards_plan(SALARY_RLS)
    second_plan = loader.graph.forwards_plan(SALARY_RLS)
    assert first_plan == second_plan
    salary_dependencies = [node for node in first_plan if node[0] in {"expenses", "salaries"}]
    assert tuple(salary_dependencies) == SALARY_APP_PLAN
    assert EXPENSE_POSTING_COMMIT in loader.disk_migrations[SALARY_INITIAL].dependencies
    assert loader.graph.leaf_nodes("salaries") == [SALARY_LEAF]
    assert loader.graph.leaf_nodes("expenses") == [EXPENSE_LEAF]
    assert SALARY_RLS in loader.disk_migrations[SALARY_LEAF].dependencies
    assert ("expenses", "0004_correction_runtime_reversal") in loader.disk_migrations[
        SALARY_LEAF
    ].dependencies
    assert SALARY_LEAF in loader.disk_migrations[EXPENSE_VOID_SET].dependencies
    assert ("expenses", "0004_correction_runtime_reversal") in loader.disk_migrations[
        EXPENSE_VOID_SET
    ].dependencies
    assert EXPENSE_VOID_SET in loader.disk_migrations[EXPENSE_LEAF].dependencies
    void_plan = loader.graph.forwards_plan(SALARY_LEAF)
    assert void_plan.index(("expenses", "0004_correction_runtime_reversal")) < void_plan.index(
        SALARY_LEAF
    )
    assert EXPENSE_LEAF not in void_plan
    expense_plan = loader.graph.forwards_plan(EXPENSE_LEAF)
    assert expense_plan.index(SALARY_LEAF) < expense_plan.index(EXPENSE_LEAF)
    assert SALARY_INITIAL in loader.graph.node_map[EXPENSE_POSTING_COMMIT].children
    loader.graph.ensure_not_cyclic()


def test_salary_write_rejects_mass_assignment_and_inexact_money() -> None:
    staff_id = str(uuid4())
    rejected = SalaryPeriodWriteSerializer(
        data={
            "period_start": "2026-10-01",
            "period_end": "2026-10-31",
            "business_date": "2026-10-18",
            "description": "October salaries",
            "gym_id": str(uuid4()),
            "status": "POSTED",
        }
    )
    assert not rejected.is_valid()
    assert "gym_id" in rejected.errors or "status" in rejected.errors

    reversed_dates = SalaryPeriodWriteSerializer(
        data={
            "period_start": "2026-10-31",
            "period_end": "2026-10-01",
            "business_date": "2026-10-18",
            "description": "October salaries",
        }
    )
    assert not reversed_dates.is_valid()
    assert "period_end" in reversed_dates.errors

    lines = SalaryLinesReplaceSerializer(
        data={
            "lines": [
                {
                    "staff_profile_id": staff_id,
                    "amount": "10.0",
                    "currency": "thb",
                    "payment_method": "CASH",
                    "description": "Front desk",
                    "expense_id": str(uuid4()),
                }
            ]
        }
    )
    assert not lines.is_valid()

    exact = SalaryLinesReplaceSerializer(
        data={
            "lines": [
                {
                    "staff_profile_id": staff_id,
                    "amount": "160000.00",
                    "currency": "THB",
                    "payment_method": "BANK_TRANSFER",
                    "description": "Front desk",
                    "external_reference": "SLIP-1",
                },
                {
                    "staff_profile_id": str(uuid4()),
                    "amount": "80000.00",
                    "currency": "THB",
                    "payment_method": "CASH",
                    "description": "Trainer",
                },
            ]
        }
    )
    assert exact.is_valid(), exact.errors
    assert {row["amount"] for row in exact.validated_data["lines"]} == {
        Decimal("80000.00"),
        Decimal("160000.00"),
    }
    staff_ids = [row["staff_profile_id"] for row in exact.validated_data["lines"]]
    assert staff_ids == sorted(staff_ids)


def test_salary_lines_reject_duplicate_staff_and_approval_maps_once() -> None:
    staff_id = str(uuid4())
    duplicate = SalaryLinesReplaceSerializer(
        data={
            "lines": [
                {
                    "staff_profile_id": staff_id,
                    "amount": "10.00",
                    "currency": "THB",
                    "payment_method": "CASH",
                    "description": "One",
                },
                {
                    "staff_profile_id": staff_id,
                    "amount": "12.00",
                    "currency": "THB",
                    "payment_method": "CASH",
                    "description": "Two",
                },
            ]
        }
    )
    assert not duplicate.is_valid()

    line_id = str(uuid4())
    mapped = ApproveSalarySerializer(
        data={
            "cash_sessions": [
                {"line_id": line_id, "cash_session_id": str(uuid4())},
                {"line_id": line_id, "cash_session_id": str(uuid4())},
            ],
            "status": "POSTED",
        }
    )
    assert not mapped.is_valid()
    assert "cash_sessions" in mapped.errors or "status" in mapped.errors

    empty = SalaryPeriodUpdateSerializer(data={})
    assert not empty.is_valid()
    query = SalaryPeriodListQuerySerializer(data={"limit": 101})
    assert not query.is_valid()
    bounded = SalaryPeriodListQuerySerializer(data={"status": "DRAFT", "limit": 25})
    assert bounded.is_valid(), bounded.errors


def test_salary_void_request_sorts_mappings_and_rejects_extras() -> None:
    first = UUID("00000000-0000-0000-0000-000000000001")
    second = UUID("00000000-0000-0000-0000-000000000002")
    blank = VoidSalarySerializer(data={"reason": "   ", "cash_sessions": []})
    assert not blank.is_valid()
    assert "reason" in blank.errors
    duplicate = VoidSalarySerializer(
        data={
            "reason": "Duplicate till",
            "cash_sessions": [
                {"line_id": str(first), "cash_session_id": str(uuid4())},
                {"line_id": str(first), "cash_session_id": str(uuid4())},
            ],
        }
    )
    assert not duplicate.is_valid()
    assert "cash_sessions" in duplicate.errors
    extra = VoidSalarySerializer(
        data={
            "reason": "Wrong field",
            "status": "VOID",
            "gym_id": str(uuid4()),
            "cash_sessions": [],
        }
    )
    assert not extra.is_valid()
    ordered = VoidSalarySerializer(
        data={
            "reason": "  Payroll correction  ",
            "cash_sessions": [
                {"line_id": str(second), "cash_session_id": str(uuid4())},
                {"line_id": str(first), "cash_session_id": str(uuid4())},
            ],
        }
    )
    assert ordered.is_valid(), ordered.errors
    assert ordered.validated_data["reason"] == "Payroll correction"
    assert [row["line_id"] for row in ordered.validated_data["cash_sessions"]] == [
        first,
        second,
    ]
