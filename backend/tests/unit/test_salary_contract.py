from decimal import Decimal
from uuid import uuid4

from django.db.migrations.loader import MigrationLoader

from apps.salaries.serializers import (
    ApproveSalarySerializer,
    SalaryLinesReplaceSerializer,
    SalaryPeriodListQuerySerializer,
    SalaryPeriodUpdateSerializer,
    SalaryPeriodWriteSerializer,
)

EXPENSE_POSTING_COMMIT = ("expenses", "0003_expense_posting_commit_current_row")
SALARY_INITIAL = ("salaries", "0001_salary_period_and_line")
SALARY_LEAF = ("salaries", "0002_rls_integrity_and_grants")
SALARY_APP_PLAN = (
    ("expenses", "0001_expense_receipt_and_expense"),
    ("expenses", "0002_rls_integrity_and_grants"),
    EXPENSE_POSTING_COMMIT,
    SALARY_INITIAL,
    SALARY_LEAF,
)


def test_salary_leaf_requires_expense_posting_commit_migration() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)
    first_plan = loader.graph.forwards_plan(SALARY_LEAF)
    second_plan = loader.graph.forwards_plan(SALARY_LEAF)
    assert first_plan == second_plan
    salary_dependencies = [node for node in first_plan if node[0] in {"expenses", "salaries"}]
    assert tuple(salary_dependencies) == SALARY_APP_PLAN
    assert EXPENSE_POSTING_COMMIT in loader.disk_migrations[SALARY_INITIAL].dependencies
    assert loader.graph.leaf_nodes("salaries") == [SALARY_LEAF]
    assert loader.graph.leaf_nodes("expenses") == [("expenses", "0004_correction_runtime_reversal")]
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
