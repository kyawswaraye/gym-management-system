from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiJsonRenderer

PUBLISHED_PATHS = {
    "/api/v1/admin/audit-logs/",
    "/api/v1/audit-logs/",
    "/api/v1/admin/dashboard/",
    "/api/v1/dashboard/",
    "/api/v1/reports/daily/",
    "/api/v1/reports/daily/export/",
    "/api/v1/reports/monthly/",
    "/api/v1/reports/monthly/export/",
    "/api/v1/reports/cash-book/",
    "/api/v1/reports/cash-book/export/",
    "/api/v1/reports/profit-loss/",
    "/api/v1/reports/profit-loss/export/",
    "/api/v1/reports/membership-expiry/",
    "/api/v1/reports/membership-expiry/export/",
    "/api/v1/reports/check-ins/",
    "/api/v1/reports/check-ins/export/",
    "/api/v1/reports/pt-sessions/",
    "/api/v1/reports/pt-sessions/export/",
    "/api/v1/reports/classes/",
    "/api/v1/reports/classes/export/",
    "/api/v1/reports/product-sales/",
    "/api/v1/reports/product-sales/export/",
    "/api/v1/reports/stock-movements/",
    "/api/v1/reports/stock-movements/export/",
    "/api/v1/reports/inventory/",
    "/api/v1/reports/inventory/export/",
    "/api/v1/admin/gyms/",
    "/api/v1/admin/gyms/{gym_id}/",
    "/api/v1/admin/gyms/{gym_id}/activate/",
    "/api/v1/admin/gyms/{gym_id}/login-accounts/",
    "/api/v1/admin/gyms/{gym_id}/owners/",
    "/api/v1/admin/gyms/{gym_id}/suspend/",
    "/api/v1/admin/subscriptions/",
    "/api/v1/admin/subscriptions/{id}/",
    "/api/v1/auth/gyms/",
    "/api/v1/auth/login/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/logout-all/",
    "/api/v1/auth/me/",
    "/api/v1/auth/mfa/challenge/",
    "/api/v1/auth/mfa/enroll/",
    "/api/v1/auth/mfa/enroll/confirm/",
    "/api/v1/auth/password/forgot/",
    "/api/v1/auth/password/reset/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/select-gym/",
    "/api/v1/gym/",
    "/api/v1/gym/logo/",
    "/api/v1/gym-memberships/{id}/roles/",
    "/api/v1/member-auth/google/",
    "/api/v1/member-auth/login/",
    "/api/v1/member-auth/logout/",
    "/api/v1/member-auth/refresh/",
    "/api/v1/member/check-ins/scan/",
    "/api/v1/member/me/",
    "/api/v1/member/dashboard/",
    "/api/v1/member/gym/logo/",
    "/api/v1/member/membership/",
    "/api/v1/member/payments/",
    "/api/v1/member/check-ins/",
    "/api/v1/member/service-packages/",
    "/api/v1/member/schedule/",
    "/api/v1/member/class-bookings/",
    "/api/v1/member/files/upload-intents/",
    "/api/v1/member/files/{id}/complete/",
    "/api/v1/member/files/{id}/download-url/",
    "/api/v1/member/files/{id}/",
    "/api/v1/trainer/check-ins/scan/",
    "/api/v1/trainer/dashboard/",
    "/api/v1/trainer/reports/sessions/",
    "/api/v1/members/",
    "/api/v1/members/{id}/",
    "/api/v1/members/{id}/archive/",
    "/api/v1/members/{id}/restore/",
    "/api/v1/members/{id}/check-ins/",
    "/api/v1/members/{id}/payments/",
    "/api/v1/members/{id}/summary/",
    "/api/v1/check-ins/",
    "/api/v1/check-ins/{id}/",
    "/api/v1/check-ins/{id}/check-out/",
    "/api/v1/check-in-challenges/",
    "/api/v1/check-in-display-devices/",
    "/api/v1/check-in-display-devices/{id}/revoke/",
    "/api/v1/check-in-display-devices/{id}/rotate-credential/",
    "/api/v1/device/check-in-challenges/",
    "/api/v1/check-in-integration-devices/",
    "/api/v1/check-in-integration-devices/{id}/revoke/",
    "/api/v1/check-in-integration-devices/{id}/rotate-credential/",
    "/api/v1/device/check-ins/",
    "/api/v1/membership-plans/",
    "/api/v1/membership-plans/{id}/",
    "/api/v1/membership-plans/{id}/archive/",
    "/api/v1/memberships/",
    "/api/v1/memberships/expiry/",
    "/api/v1/memberships/{id}/",
    "/api/v1/memberships/{id}/cancel/",
    "/api/v1/memberships/{id}/resume/",
    "/api/v1/memberships/{id}/suspend/",
    "/api/v1/health/",
    "/api/v1/permissions/",
    "/api/v1/readiness/",
    "/api/v1/owners/",
    "/api/v1/settings/",
    "/api/v1/owners/invitations/",
    "/api/v1/owners/invitations/accept/",
    "/api/v1/owners/{gym_membership_id}/cancel-invitation/",
    "/api/v1/owners/{gym_membership_id}/revoke/",
    "/api/v1/roles/",
    "/api/v1/roles/{id}/",
    "/api/v1/roles/{id}/archive/",
    "/api/v1/roles/{id}/permissions/",
    "/api/v1/staff/",
    "/api/v1/staff/invitations/",
    "/api/v1/staff/invitations/accept/",
    "/api/v1/staff/{id}/",
    "/api/v1/staff/{id}/activate/",
    "/api/v1/staff/{id}/suspend/",
    "/api/v1/files/upload-intents/",
    "/api/v1/files/{id}/",
    "/api/v1/files/{id}/complete/",
    "/api/v1/files/{id}/download-url/",
    "/api/v1/cash-sessions/",
    "/api/v1/cash-sessions/open/",
    "/api/v1/cash-sessions/{id}/",
    "/api/v1/cash-sessions/{id}/adjustments/",
    "/api/v1/cash-sessions/{id}/reconcile/",
    "/api/v1/cash-transactions/",
    "/api/v1/payments/",
    "/api/v1/payments/{payment_id}/",
    "/api/v1/payments/{payment_id}/submit/",
    "/api/v1/payments/{payment_id}/approve/",
    "/api/v1/payments/{payment_id}/reject/",
    "/api/v1/payments/{payment_id}/cancel/",
    "/api/v1/payments/{payment_id}/void/",
    "/api/v1/payments/{payment_id}/refund/",
    "/api/v1/receipts/",
    "/api/v1/receipts/print-context/",
    "/api/v1/receipts/{receipt_id}/",
    "/api/v1/receipts/{receipt_id}/print/",
    "/api/v1/receipts/{receipt_id}/void/",
    "/api/v1/receipts/{receipt_id}/download/",
    "/api/v1/sales/daily/",
    "/api/v1/expense-categories/",
    "/api/v1/expense-categories/{category_id}/",
    "/api/v1/expenses/",
    "/api/v1/expenses/{expense_id}/",
    "/api/v1/expenses/{expense_id}/submit/",
    "/api/v1/expenses/{expense_id}/approve/",
    "/api/v1/expenses/{expense_id}/reject/",
    "/api/v1/expenses/{expense_id}/cancel/",
    "/api/v1/expenses/{expense_id}/void/",
    "/api/v1/salary-periods/",
    "/api/v1/salary-periods/{period_id}/",
    "/api/v1/salary-periods/{period_id}/lines/",
    "/api/v1/salary-periods/{period_id}/submit/",
    "/api/v1/salary-periods/{period_id}/approve/",
    "/api/v1/salary-periods/{period_id}/reject/",
    "/api/v1/salary-periods/{period_id}/cancel/",
    "/api/v1/salary-periods/{period_id}/void/",
    "/api/v1/product-categories/",
    "/api/v1/products/",
    "/api/v1/products/low-stock/",
    "/api/v1/products/{product_id}/",
    "/api/v1/stock-movements/",
    "/api/v1/stock-movements/adjust/",
    "/api/v1/retail-sales/",
    "/api/v1/retail-sales/{sale_id}/",
    "/api/v1/retail-sales/{sale_id}/submit/",
    "/api/v1/retail-sales/{sale_id}/approve/",
    "/api/v1/retail-sales/{sale_id}/reject/",
    "/api/v1/retail-sales/{sale_id}/cancel/",
    "/api/v1/retail-sales/{sale_id}/void/",
    "/api/v1/retail-sales/{sale_id}/return/",
    "/api/v1/trainers/",
    "/api/v1/trainers/{id}/assigned-members/",
    "/api/v1/trainer-assignments/",
    "/api/v1/trainer-assignments/{id}/",
    "/api/v1/trainer-assignments/{id}/end/",
    "/api/v1/trainer-assignments/{id}/cancel/",
    "/api/v1/service-packages/",
    "/api/v1/pt-sales/",
    "/api/v1/pt-sales/{id}/",
    "/api/v1/pt-sales/{id}/cancel/",
    "/api/v1/pt-sales/{id}/submit/",
    "/api/v1/pt-sales/{id}/approve/",
    "/api/v1/pt-sales/{id}/reject/",
    "/api/v1/pt-sales/{id}/void/",
    "/api/v1/pt-commissions/",
    "/api/v1/pt-commissions/{id}/",
    "/api/v1/pt-commissions/{id}/record-payment/",
    "/api/v1/member-service-packages/",
    "/api/v1/member-service-packages/{id}/",
    "/api/v1/member-service-packages/{id}/ledger/",
    "/api/v1/member-service-packages/{id}/ledger/adjust/",
    "/api/v1/class-types/",
    "/api/v1/class-types/{id}/",
    "/api/v1/class-schedules/",
    "/api/v1/class-schedules/{id}/",
    "/api/v1/class-occurrences/",
    "/api/v1/class-occurrences/{id}/cancel/",
    "/api/v1/class-bookings/",
    "/api/v1/class-bookings/{id}/attend/",
    "/api/v1/class-bookings/{id}/no-show/",
    "/api/v1/class-bookings/{id}/cancel/",
    "/api/v1/pt-bookings/",
    "/api/v1/pt-bookings/{id}/",
    "/api/v1/pt-bookings/{id}/attend/",
    "/api/v1/pt-bookings/{id}/no-show/",
    "/api/v1/pt-bookings/{id}/cancel/",
    "/api/v1/announcements/",
    "/api/v1/announcements/{id}/",
    "/api/v1/announcements/{id}/publish/",
    "/api/v1/announcements/{id}/cancel/",
    "/api/v1/member/notifications/",
    "/api/v1/member/notifications/{id}/read/",
    "/api/v1/member/devices/",
    "/api/v1/member/devices/{id}/",
}
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.v1.json"


def generated_schema() -> dict[str, Any]:
    schema = SchemaGenerator().get_schema(request=None, public=True)
    return json.loads(OpenApiJsonRenderer().render(schema))


def test_committed_openapi_contract_has_no_drift() -> None:
    generated = generated_schema()
    committed = json.loads(CONTRACT_PATH.read_text())
    assert generated == committed


def test_openapi_has_no_retired_platform_settings_route() -> None:
    document = generated_schema()
    rendered = json.dumps(document)
    assert "platform.settings.manage" not in rendered
    assert "/api/v1/admin/settings/" not in document["paths"]
    assert "/api/v1/platform/settings/" not in document["paths"]
    assert not any("platform" in path and "settings" in path for path in document["paths"])


def test_openapi_contract_publishes_approved_routes_only() -> None:
    schema = generated_schema()
    assert set(schema["paths"]) == PUBLISHED_PATHS
    assert "staffBearerAuth" in schema["components"]["securitySchemes"]
    assert "staffOrPlatformBearerAuth" in schema["components"]["securitySchemes"]
    assert "/api/v1/members/" in schema["paths"]
    assert "/api/v1/members/{id}/" in schema["paths"]
    assert "/api/v1/members/{id}/archive/" in schema["paths"]
    assert "/api/v1/members/{id}/restore/" in schema["paths"]
    unpublished = (
        "/api/v1/check-ins/scan/",
        "/api/v1/notifications/",
        "/api/v1/product-categories/{id}/",
        "/api/v1/stock-movements/{id}/",
    )
    assert "/api/v1/members/{id}/summary/" in schema["paths"]
    assert "/api/v1/members/{id}/payments/" in schema["paths"]
    assert "/api/v1/members/{id}/check-ins/" in schema["paths"]
    assert "/api/v1/check-ins/" in schema["paths"]
    assert all(path not in schema["paths"] for path in unpublished)
    assert not any(path.startswith(unpublished) for path in schema["paths"])
    assert "/api/v1/payments/" in schema["paths"]
    assert "/api/v1/receipts/" in schema["paths"]
    assert "/api/v1/sales/daily/" in schema["paths"]
    assert "/api/v1/expense-categories/" in schema["paths"]
    assert "/api/v1/expenses/" in schema["paths"]
    assert "/api/v1/expenses/{expense_id}/approve/" in schema["paths"]
    assert "/api/v1/dashboard/" in schema["paths"]


def test_openapi_documents_tenant_dashboard() -> None:
    document = generated_schema()
    operation = document["paths"]["/api/v1/dashboard/"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
    assert set(parameters) == {"range", "date_from", "date_to"}
    envelope = _resolve_schema(
        document,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )
    data = _resolve_schema(document, envelope["properties"]["data"])
    assert set(data["properties"]) == {
        "range",
        "timezone",
        "currency",
        "operational",
        "financial",
    }
    operational = _resolve_schema(document, data["properties"]["operational"])
    financial_schema = data["properties"]["financial"]
    assert financial_schema.get("nullable") is True or any(
        item.get("type") == "null" for item in financial_schema.get("anyOf", [])
    )
    financial_ref = financial_schema
    if "allOf" in financial_schema:
        financial_ref = next(item for item in financial_schema["allOf"] if "$ref" in item)
    elif "anyOf" in financial_schema:
        financial_ref = next(item for item in financial_schema["anyOf"] if "$ref" in item)
    financial = _resolve_schema(document, financial_ref)
    assert set(operational["properties"]) == {
        "active_members",
        "new_members",
        "expired_memberships",
        "check_ins",
        "unique_attendees",
        "currently_checked_in",
        "expiring_within_3_days",
        "expiring_within_7_days",
        "expiring_within_30_days",
        "pending_approvals",
    }
    assert set(financial["properties"]) == {
        "total_revenue",
        "new_member_revenue",
        "renewal_revenue",
        "pt_revenue",
        "class_revenue",
        "product_revenue",
        "other_revenue",
        "pending_payment_member_count",
        "pending_payment_amount",
        "revenue_sources_ranked",
        "top_product_items",
        "total_expenses",
        "expenses_by_category",
        "staff_salary_expense",
        "pt_commission_expense",
        "net_profit",
    }


def test_openapi_documents_period_reports() -> None:
    document = generated_schema()
    daily = document["paths"]["/api/v1/reports/daily/"]["get"]
    monthly = document["paths"]["/api/v1/reports/monthly/"]["get"]
    daily_parameters = {parameter["name"]: parameter for parameter in daily["parameters"]}
    monthly_parameters = {parameter["name"]: parameter for parameter in monthly["parameters"]}
    assert set(daily_parameters) == {"date"}
    assert daily_parameters["date"]["required"] is True
    assert set(monthly_parameters) == {"year", "month"}
    assert monthly_parameters["year"]["required"] is True
    assert monthly_parameters["month"]["required"] is True
    for operation in (daily, monthly):
        envelope = _resolve_schema(
            document,
            operation["responses"]["200"]["content"]["application/json"]["schema"],
        )
        data = _resolve_schema(document, envelope["properties"]["data"])
        assert set(data["properties"]) == {
            "gym",
            "timezone",
            "currency",
            "range",
            "generated_at",
            "source_status_policy",
            "rows",
            "totals",
        }
        policy = _resolve_schema(document, data["properties"]["source_status_policy"])
        assert set(policy["properties"]) == {"revenue", "expenses", "operations"}
        row_schema = data["properties"]["rows"]
        row = _resolve_schema(document, row_schema["items"])
        totals = _resolve_schema(document, data["properties"]["totals"])
        assert "product_quantity" in row["properties"]
        assert "net_profit" in row["properties"]
        assert "unique_attendees" in totals["properties"]
        assert "staff_salary_expense" in totals["properties"]
        assert "pt_commission_expense" in totals["properties"]
        assert "expenses_by_category" in totals["properties"]
        assert "date" not in totals["properties"]


def test_openapi_documents_cash_book_and_profit_loss() -> None:
    document = generated_schema()
    cash = document["paths"]["/api/v1/reports/cash-book/"]["get"]
    profit = document["paths"]["/api/v1/reports/profit-loss/"]["get"]
    for operation in (cash, profit):
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert set(parameters) == {"date_from", "date_to"}
        assert parameters["date_from"]["required"] is True
        assert parameters["date_to"]["required"] is True
        envelope = _resolve_schema(
            document,
            operation["responses"]["200"]["content"]["application/json"]["schema"],
        )
        data = _resolve_schema(document, envelope["properties"]["data"])
        assert set(data["properties"]) == {
            "gym",
            "timezone",
            "currency",
            "range",
            "generated_at",
            "source_status_policy",
            "rows",
            "totals",
        }
    cash_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            cash["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    cash_row = _resolve_schema(document, cash_data["properties"]["rows"]["items"])
    cash_totals = _resolve_schema(document, cash_data["properties"]["totals"])
    assert "void_session_count" in cash_row["properties"]
    assert "net_cash_flow" in cash_row["properties"]
    assert "reversal_out" in cash_totals["properties"]
    assert "date" not in cash_totals["properties"]
    profit_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            profit["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    profit_row = _resolve_schema(document, profit_data["properties"]["rows"]["items"])
    profit_totals = _resolve_schema(document, profit_data["properties"]["totals"])
    assert "class_revenue" in profit_row["properties"]
    assert "product_quantity" not in profit_row["properties"]
    assert "net_profit" in profit_row["properties"]
    assert "staff_salary_expense" in profit_totals["properties"]
    assert "expenses_by_category" in profit_totals["properties"]
    assert "date" not in profit_totals["properties"]


def test_openapi_documents_operational_reports() -> None:
    document = generated_schema()
    expiry = document["paths"]["/api/v1/reports/membership-expiry/"]["get"]
    visits = document["paths"]["/api/v1/reports/check-ins/"]["get"]
    for operation in (expiry, visits):
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert set(parameters) == {"date_from", "date_to"}
        assert parameters["date_from"]["required"] is True
        assert parameters["date_to"]["required"] is True
        envelope = _resolve_schema(
            document,
            operation["responses"]["200"]["content"]["application/json"]["schema"],
        )
        data = _resolve_schema(document, envelope["properties"]["data"])
        assert set(data["properties"]) == {
            "gym",
            "timezone",
            "range",
            "generated_at",
            "source_status_policy",
            "rows",
            "totals",
        }
        assert "currency" not in data["properties"]
    expiry_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            expiry["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    expiry_row = _resolve_schema(document, expiry_data["properties"]["rows"]["items"])
    expiry_totals = _resolve_schema(document, expiry_data["properties"]["totals"])
    assert set(expiry_row["properties"]) == {
        "date",
        "expiring_memberships",
        "unique_members",
        "active_memberships",
        "suspended_memberships",
        "expired_memberships",
    }
    assert set(expiry_totals["properties"]) == set(expiry_row["properties"]) - {"date"}
    visit_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            visits["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    visit_row = _resolve_schema(document, visit_data["properties"]["rows"]["items"])
    visit_totals = _resolve_schema(document, visit_data["properties"]["totals"])
    assert set(visit_row["properties"]) == {
        "date",
        "check_ins",
        "unique_attendees",
        "manual_check_ins",
        "qr_check_ins",
        "nfc_check_ins",
        "biometric_check_ins",
        "check_outs",
        "open_visits_from_day",
    }
    assert set(visit_totals["properties"]) == set(visit_row["properties"]) - {"date"} | {
        "currently_checked_in",
    }


def test_openapi_documents_session_reports() -> None:
    document = generated_schema()
    sessions = document["paths"]["/api/v1/reports/pt-sessions/"]["get"]
    classes = document["paths"]["/api/v1/reports/classes/"]["get"]
    for operation in (sessions, classes):
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert set(parameters) == {"date_from", "date_to"}
        assert parameters["date_from"]["required"] is True
        assert parameters["date_to"]["required"] is True
        envelope = _resolve_schema(
            document,
            operation["responses"]["200"]["content"]["application/json"]["schema"],
        )
        data = _resolve_schema(document, envelope["properties"]["data"])
        assert set(data["properties"]) == {
            "gym",
            "timezone",
            "range",
            "generated_at",
            "source_status_policy",
            "rows",
            "totals",
        }
        assert "currency" not in data["properties"]
    session_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            sessions["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    session_row = _resolve_schema(document, session_data["properties"]["rows"]["items"])
    session_totals = _resolve_schema(document, session_data["properties"]["totals"])
    assert set(session_row["properties"]) == {
        "date",
        "bookings",
        "unique_members",
        "unique_trainers",
        "booked_sessions",
        "attended_sessions",
        "no_show_sessions",
        "cancelled_sessions",
        "metered_sessions",
        "unmetered_sessions",
    }
    assert set(session_totals["properties"]) == set(session_row["properties"]) - {"date"}
    class_data = _resolve_schema(
        document,
        _resolve_schema(
            document,
            classes["responses"]["200"]["content"]["application/json"]["schema"],
        )["properties"]["data"],
    )
    class_row = _resolve_schema(document, class_data["properties"]["rows"]["items"])
    class_totals = _resolve_schema(document, class_data["properties"]["totals"])
    assert set(class_row["properties"]) == {
        "date",
        "occurrences",
        "scheduled_occurrences",
        "completed_occurrences",
        "cancelled_occurrences",
        "available_capacity",
        "bookings",
        "unique_members",
        "booked_slots",
        "attended_slots",
        "no_show_slots",
        "cancelled_slots",
        "reserved_slots",
        "remaining_capacity",
    }
    assert "unique_trainers" not in class_row["properties"]
    assert set(class_totals["properties"]) == set(class_row["properties"]) - {"date"} | {
        "unique_trainers",
    }
    for schema in (session_row, session_totals, class_row, class_totals):
        assert "currency" not in schema["properties"]
        assert "member_id" not in schema["properties"]
        assert "email" not in schema["properties"]


def test_openapi_documents_trainer_reports() -> None:
    document = generated_schema()
    dashboard = document["paths"]["/api/v1/trainer/dashboard/"]["get"]
    sessions = document["paths"]["/api/v1/trainer/reports/sessions/"]["get"]
    assert dashboard["operationId"] == "trainer_dashboard_retrieve"
    assert sessions["operationId"] == "trainer_session_report_retrieve"
    dashboard_parameters = {item["name"]: item for item in dashboard["parameters"]}
    session_parameters = {item["name"]: item for item in sessions["parameters"]}
    assert set(dashboard_parameters) == {"range", "date_from", "date_to"}
    assert dashboard_parameters["range"].get("required", False) is False
    assert dashboard_parameters["date_from"].get("required", False) is False
    assert dashboard_parameters["date_to"].get("required", False) is False
    assert set(session_parameters) == {"date_from", "date_to"}
    assert session_parameters["date_from"]["required"] is True
    assert session_parameters["date_to"]["required"] is True
    for operation in (dashboard, sessions):
        assert set(operation["responses"]) >= {"200", "400", "401", "403", "404"}
    dashboard_data = _report_data(document, dashboard)
    session_data = _report_data(document, sessions)
    assert set(dashboard_data["properties"]) == {
        "timezone",
        "today",
        "range",
        "generated_at",
        "trainer_staff_profile_id",
        "active_assigned_members",
        "sessions",
        "upcoming_bookings",
    }
    assert set(session_data["properties"]) == {
        "gym",
        "timezone",
        "range",
        "generated_at",
        "source_status_policy",
        "rows",
        "totals",
    }
    counts = _resolve_schema(document, dashboard_data["properties"]["sessions"])
    upcoming = _resolve_schema(document, dashboard_data["properties"]["upcoming_bookings"]["items"])
    session_row = _resolve_schema(document, session_data["properties"]["rows"]["items"])
    session_totals = _resolve_schema(document, session_data["properties"]["totals"])
    assert set(counts["properties"]) == {
        "bookings",
        "unique_members",
        "booked_sessions",
        "attended_sessions",
        "no_show_sessions",
        "cancelled_sessions",
    }
    assert set(upcoming["properties"]) == {
        "id",
        "member_id",
        "member_number",
        "full_name",
        "starts_at",
        "ends_at",
    }
    assert set(session_row["properties"]) == set(counts["properties"]) | {"date"}
    assert set(session_totals["properties"]) == set(counts["properties"])
    for schema in (dashboard_data, counts, upcoming, session_data, session_row, session_totals):
        assert "currency" not in schema["properties"]
        assert "email" not in schema["properties"]
        assert "phone" not in schema["properties"]
        assert "price" not in schema["properties"]
        assert "amount" not in schema["properties"]
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/trainer/dashboard/"' in generated
    assert '"/api/v1/trainer/reports/sessions/"' in generated
    assert "active_assigned_members" in generated
    assert "upcoming_bookings" in generated
    assert "trainer_staff_profile_id" in generated


def test_openapi_documents_inventory_reports() -> None:
    document = generated_schema()
    sales = document["paths"]["/api/v1/reports/product-sales/"]["get"]
    stock = document["paths"]["/api/v1/reports/stock-movements/"]["get"]
    inventory = document["paths"]["/api/v1/reports/inventory/"]["get"]
    sales_parameters = {item["name"]: item for item in sales["parameters"]}
    stock_parameters = {item["name"]: item for item in stock["parameters"]}
    inventory_parameters = {item["name"]: item for item in inventory["parameters"]}
    assert set(sales_parameters) == {"date_from", "date_to", "limit", "after"}
    assert sales_parameters["date_from"]["required"] is True
    assert sales_parameters["date_to"]["required"] is True
    assert sales_parameters["limit"].get("required", False) is False
    assert sales_parameters["after"].get("required", False) is False
    assert set(stock_parameters) == {"date_from", "date_to"}
    assert set(inventory_parameters) == {"limit", "after"}
    assert inventory_parameters["limit"].get("required", False) is False
    assert inventory_parameters["after"].get("required", False) is False
    sales_data = _report_data(document, sales)
    stock_data = _report_data(document, stock)
    inventory_data = _report_data(document, inventory)
    assert set(sales_data["properties"]) == {
        "gym",
        "timezone",
        "currency",
        "range",
        "generated_at",
        "source_status_policy",
        "rows",
        "totals",
        "meta",
    }
    assert set(stock_data["properties"]) == {
        "gym",
        "timezone",
        "range",
        "generated_at",
        "source_status_policy",
        "rows",
        "totals",
    }
    assert "currency" not in stock_data["properties"]
    assert "meta" not in stock_data["properties"]
    assert set(inventory_data["properties"]) == {
        "gym",
        "timezone",
        "generated_at",
        "source_status_policy",
        "rows",
        "totals",
        "meta",
    }
    assert "currency" not in inventory_data["properties"]
    assert "range" not in inventory_data["properties"]
    sales_row = _resolve_schema(document, sales_data["properties"]["rows"]["items"])
    sales_totals = _resolve_schema(document, sales_data["properties"]["totals"])
    assert set(sales_row["properties"]) == {
        "product_id",
        "sku",
        "name",
        "is_active",
        "quantity_sold",
        "revenue",
        "cogs",
        "gross_profit",
    }
    assert set(sales_totals["properties"]) == {
        "quantity_sold",
        "revenue",
        "cogs",
        "gross_profit",
        "product_count",
        "active_product_count",
    }
    stock_row = _resolve_schema(document, stock_data["properties"]["rows"]["items"])
    stock_totals = _resolve_schema(document, stock_data["properties"]["totals"])
    assert set(stock_row["properties"]) == {
        "date",
        "opening_quantity",
        "purchase_quantity",
        "sale_quantity",
        "sale_return_quantity",
        "damage_quantity",
        "loss_quantity",
        "adjustment_quantity",
        "correction_quantity",
        "net_quantity",
    }
    assert set(stock_totals["properties"]) == set(stock_row["properties"]) - {"date"}
    inventory_row = _resolve_schema(document, inventory_data["properties"]["rows"]["items"])
    inventory_totals = _resolve_schema(document, inventory_data["properties"]["totals"])
    assert set(inventory_row["properties"]) == {
        "product_id",
        "category_code",
        "category_name",
        "sku",
        "name",
        "unit",
        "is_active",
        "stock_quantity",
        "low_stock_threshold",
        "is_low_stock",
    }
    assert set(inventory_totals["properties"]) == {
        "product_count",
        "active_product_count",
        "low_stock_product_count",
        "total_stock_quantity",
    }
    for schema in (sales_row, sales_totals, stock_row, inventory_row, inventory_totals):
        assert "email" not in schema["properties"]
        assert "member_id" not in schema["properties"]
        assert "current_unit_cost" not in schema["properties"]


def test_openapi_documents_synchronous_report_exports() -> None:
    document = generated_schema()
    assert "/api/v1/reports/exports/" not in document["paths"]
    assert "/api/v1/trainer/dashboard/export/" not in document["paths"]
    assert "/api/v1/trainer/reports/sessions/export/" not in document["paths"]
    expected = {
        "daily": {"date"},
        "monthly": {"year", "month"},
        "cash-book": {"date_from", "date_to"},
        "profit-loss": {"date_from", "date_to"},
        "membership-expiry": {"date_from", "date_to"},
        "check-ins": {"date_from", "date_to"},
        "pt-sessions": {"date_from", "date_to"},
        "classes": {"date_from", "date_to"},
        "product-sales": {"date_from", "date_to"},
        "stock-movements": {"date_from", "date_to"},
        "inventory": set(),
    }
    for kind, names in expected.items():
        path = f"/api/v1/reports/{kind}/export/"
        operation = document["paths"][path]["get"]
        assert set(document["paths"][path]) == {"get"}
        parameters = {item["name"] for item in operation.get("parameters", [])}
        assert parameters == names
        assert "text/csv" in operation["responses"]["200"]["content"]
        assert "application/json" not in operation["responses"]["200"]["content"]
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/reports/daily/export/"' in generated
    assert '"/api/v1/reports/inventory/export/"' in generated
    assert '"/api/v1/reports/exports/"' not in generated


def _report_data(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    envelope = _resolve_schema(
        document,
        operation["responses"]["200"]["content"]["application/json"]["schema"],
    )
    return _resolve_schema(document, envelope["properties"]["data"])


def test_openapi_documents_membership_plans() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/membership-plans/"]
    detail = document["paths"]["/api/v1/membership-plans/{id}/"]
    archive = document["paths"]["/api/v1/membership-plans/{id}/archive/"]["post"]
    schemas = document["components"]["schemas"]
    create = schemas["MembershipPlanCreateRequest"]["properties"]
    update = schemas["PatchedMembershipPlanUpdateRequest"]["properties"]
    plan = schemas["MembershipPlan"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    meta = schemas["MembershipPlanListEnvelope"]["properties"]["meta"]
    meta_schema = schemas[meta["$ref"].rsplit("/", 1)[-1]] if "$ref" in meta else meta
    writable = {"code", "name", "description", "duration_value", "duration_unit", "price"}

    assert set(create) == writable
    assert set(update) == writable
    assert set(plan) == writable | {
        "id",
        "currency",
        "is_active",
        "archived_at",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in plan
    assert set(query) == {"after", "limit"}
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["minimum"] == 1
    assert query["limit"]["schema"]["maximum"] == 100
    assert set(meta_schema["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        archive,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert (
        archive.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        == {}
    )


def test_openapi_documents_memberships() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/memberships/"]
    detail = document["paths"]["/api/v1/memberships/{id}/"]
    cancel = document["paths"]["/api/v1/memberships/{id}/cancel/"]["post"]
    suspend = document["paths"]["/api/v1/memberships/{id}/suspend/"]["post"]
    resume = document["paths"]["/api/v1/memberships/{id}/resume/"]["post"]
    expiry = document["paths"]["/api/v1/memberships/expiry/"]["get"]
    schemas = document["components"]["schemas"]
    create = schemas["MembershipCreateRequest"]["properties"]
    update = schemas["PatchedMembershipUpdateRequest"]["properties"]
    cancel_body = schemas["MembershipCancelRequest"]["properties"]
    membership = schemas["Membership"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    expiry_query = {parameter["name"]: parameter for parameter in expiry["parameters"]}
    meta = _resolve_schema(document, schemas["MembershipListEnvelope"]["properties"]["meta"])
    writable = {"member_id", "plan_id", "start_date", "discount_amount", "note"}

    assert set(create) == writable
    assert set(update) == {"note"}
    assert set(cancel_body) == {"reason"}
    assert set(membership) == writable | {
        "id",
        "plan_code",
        "plan_name",
        "duration_value",
        "duration_unit",
        "expiry_date",
        "list_price",
        "final_amount",
        "currency",
        "payment_status",
        "status",
        "cancelled_at",
        "cancelled_by_membership_id",
        "cancellation_reason",
        "created_by_membership_id",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in membership
    assert "revenue_source" not in membership
    assert "paid_amount" not in membership
    assert set(query) == {"after", "limit", "member_id", "payment_status", "status"}
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["maximum"] == 100
    assert set(expiry_query) == {"after", "bucket", "limit"}
    bucket = expiry_query["bucket"]["schema"]
    assert set(bucket["enum"]) == {
        "expired",
        "today",
        "within_3_days",
        "within_7_days",
        "within_30_days",
    }
    assert set(meta["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        cancel,
        suspend,
        resume,
        expiry,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in collection["get"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert "409" in cancel["responses"]
    assert (
        suspend.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("properties", {})
        == {}
    )
    assert "{action}" not in "".join(document["paths"])


def _resolve_schema(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_openapi_documents_members() -> None:
    document = generated_schema()
    collection = document["paths"]["/api/v1/members/"]
    detail = document["paths"]["/api/v1/members/{id}/"]
    archive = document["paths"]["/api/v1/members/{id}/archive/"]["post"]
    restore = document["paths"]["/api/v1/members/{id}/restore/"]["post"]
    schemas = document["components"]["schemas"]
    create = schemas["MemberCreateRequest"]["properties"]
    update = schemas["PatchedMemberUpdateRequest"]["properties"]
    member = schemas["Member"]["properties"]
    query = {parameter["name"]: parameter for parameter in collection["get"]["parameters"]}
    meta = _resolve_schema(document, schemas["MemberListEnvelope"]["properties"]["meta"])
    address = _resolve_schema(document, member["address"])
    gender = schemas["GenderEnum"]
    writable = {
        "member_number",
        "first_name",
        "last_name",
        "phone",
        "email",
        "gender",
        "date_of_birth",
        "address",
        "join_date",
        "emergency_contact_name",
        "emergency_contact_phone",
        "notes",
        "profile_photo_asset_id",
    }

    assert set(create) == writable
    assert set(update) == writable - {"member_number"}
    assert set(member) == writable | {
        "id",
        "normalized_full_name",
        "linked_user_id",
        "is_pt_client",
        "authz_version",
        "status",
        "archived_at",
        "created_at",
        "updated_at",
    }
    assert "gym_id" not in member
    assert "created_by_membership_id" not in member
    assert "updated_by_membership_id" not in member
    assert member["is_pt_client"]["type"] == "boolean"
    assert member["phone"]["nullable"] is True
    assert member["linked_user_id"]["nullable"] is True
    assert set(address["properties"]) == {
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
    }
    assert set(gender["enum"]) == {"FEMALE", "MALE", "OTHER", "UNSPECIFIED"}
    assert set(query) == {
        "after",
        "is_pt_client",
        "joined_from",
        "joined_to",
        "limit",
        "search",
        "status",
    }
    assert query["limit"]["schema"]["default"] == 25
    assert query["limit"]["schema"]["minimum"] == 1
    assert query["limit"]["schema"]["maximum"] == 100
    assert query["search"]["schema"]["maxLength"] == 100
    assert set(meta["properties"]) == {"has_more", "next_after"}
    for operation in (
        collection["get"],
        collection["post"],
        detail["get"],
        detail["patch"],
        archive,
        restore,
    ):
        assert {"staffBearerAuth": []} in operation["security"]
    assert "201" in collection["post"]["responses"]
    assert "409" in collection["post"]["responses"]
    assert "404" in detail["get"]["responses"]
    assert "404" in archive["responses"]
    assert "404" in restore["responses"]
    assert archive["operationId"] == "members_archive"
    assert restore["operationId"] == "members_restore"
    assert "get" not in document["paths"]["/api/v1/members/{id}/restore/"]
    for command in (archive, restore):
        assert (
            command.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
            .get("properties", {})
            == {}
        )
    rendered_members = json.dumps(
        {
            "collection": collection,
            "detail": detail,
            "archive": archive,
            "member": member,
        }
    )
    assert "object_key" not in rendered_members


def _schema_component(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    for item in schema.get("allOf", []):
        if isinstance(item, dict) and "$ref" in item:
            return document["components"]["schemas"][item["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_openapi_documents_member_summary() -> None:
    document = generated_schema()
    operation = document["paths"]["/api/v1/members/{id}/summary/"]
    assert set(operation) == {"get"}
    summary = operation["get"]
    parameters = summary.get("parameters", [])
    assert [parameter["name"] for parameter in parameters] == ["id"]
    assert parameters[0]["in"] == "path"
    assert not any(parameter["in"] == "query" for parameter in parameters)
    assert {"staffBearerAuth": []} in summary["security"]
    assert set(summary["responses"]) >= {"200", "400", "401", "403", "404"}
    schemas = document["components"]["schemas"]
    envelope = schemas["MemberSummaryEnvelope"]["properties"]
    assert set(envelope) == {"success", "data", "message"}
    data = _schema_component(document, envelope["data"])
    assert set(data["properties"]) == {
        "member",
        "current_membership",
        "days_remaining",
        "is_pt_client",
        "membership_history",
        "membership_history_has_more",
        "payment_summary",
        "check_in_summary",
    }
    assert data["properties"]["current_membership"].get("nullable") is True
    assert data["properties"]["days_remaining"]["nullable"] is True
    assert data["properties"]["days_remaining"]["minimum"] == 0
    assert data["properties"]["is_pt_client"]["type"] == "boolean"
    member = _schema_component(document, data["properties"]["member"])
    assert set(member["properties"]) == set(schemas["Member"]["properties"])
    current = _schema_component(document, data["properties"]["current_membership"])
    assert set(current["properties"]) == set(schemas["Membership"]["properties"])
    history_item = _schema_component(document, data["properties"]["membership_history"]["items"])
    assert set(history_item["properties"]) == set(schemas["Membership"]["properties"])
    payment = _schema_component(document, data["properties"]["payment_summary"])
    assert set(payment["properties"]) == {
        "available",
        "currency",
        "total_charged",
        "total_refunded",
        "net_paid",
        "outstanding_membership_balance",
    }
    for name in ("total_charged", "total_refunded", "net_paid", "outstanding_membership_balance"):
        assert payment["properties"][name]["type"] == "string"
        assert payment["properties"][name]["format"] == "decimal"
    check_in = _schema_component(document, data["properties"]["check_in_summary"])
    assert set(check_in["properties"]) == {
        "available",
        "currently_checked_in",
        "open_check_in_id",
        "last_check_in_at",
        "last_check_out_at",
    }
    assert check_in["properties"]["open_check_in_id"]["nullable"] is True
    assert check_in["properties"]["last_check_in_at"]["nullable"] is True
    assert check_in["properties"]["last_check_out_at"]["nullable"] is True
    rendered = json.dumps(payment)
    for secret in (
        "receipt",
        "reference",
        "description",
        "method",
        "entry_type",
        "received_at",
        "business_date",
        "approved_by",
        "actor",
    ):
        assert secret not in rendered
    assert "payment_history" not in data["properties"]
    assert "check_in_history" not in data["properties"]


def test_openapi_documents_member_payment_history() -> None:
    document = generated_schema()
    operation = document["paths"]["/api/v1/members/{id}/payments/"]
    assert set(operation) == {"get"}
    history = operation["get"]
    parameters = {parameter["name"]: parameter for parameter in history["parameters"]}
    schemas = document["components"]["schemas"]
    envelope = schemas["MemberPaymentHistoryEnvelope"]["properties"]
    item = _schema_component(document, envelope["data"]["items"])
    meta = _schema_component(document, envelope["meta"])
    status_schema = _schema_component(document, item["properties"]["status"])

    assert set(parameters) == {"after", "id", "limit"}
    assert parameters["id"]["in"] == "path"
    assert parameters["limit"]["in"] == "query"
    assert parameters["after"]["in"] == "query"
    assert parameters["limit"]["schema"]["default"] == 25
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["after"].get("required", False) is False
    assert parameters["after"]["schema"]["maxLength"] == 2048
    assert "gym_id" not in parameters
    assert {"staffBearerAuth": []} in history["security"]
    assert set(history["responses"]) >= {"200", "400", "401", "403", "404"}
    assert set(envelope) == {"success", "data", "meta", "message"}
    assert set(item["properties"]) == {
        "id",
        "membership_id",
        "original_payment_id",
        "entry_type",
        "purpose",
        "description",
        "amount",
        "currency",
        "method",
        "business_date",
        "received_at",
        "status",
        "receipt_number",
    }
    for name in ("membership_id", "original_payment_id", "receipt_number"):
        assert item["properties"][name]["nullable"] is True
    assert item["properties"]["amount"]["type"] == "string"
    assert item["properties"]["amount"]["format"] == "decimal"
    assert set(status_schema["enum"]) == {
        "DRAFT",
        "PENDING_APPROVAL",
        "POSTED",
        "REJECTED",
        "CANCELLED",
        "VOID",
    }
    assert set(meta["properties"]) == {"has_more", "next_after"}
    assert meta["properties"]["next_after"]["nullable"] is True
    assert set(item["properties"]).isdisjoint(
        {
            "gym",
            "gym_id",
            "member",
            "member_id",
            "member_name",
            "reference_number",
            "submitted_at",
            "submitted_by_membership_id",
            "approved_at",
            "approved_by_membership_id",
            "rejected_at",
            "rejected_by_membership_id",
            "rejection_reason",
            "cancelled_at",
            "cancellation_reason",
            "voided_at",
            "void_reason",
            "created_by_membership_id",
            "updated_by_membership_id",
            "created_at",
            "updated_at",
            "receipt",
        }
    )


def test_openapi_documents_bff_staff_and_platform_authentication_boundaries() -> None:
    document = generated_schema()
    login = document["paths"]["/api/v1/auth/login/"]["post"]
    me = document["paths"]["/api/v1/auth/me/"]["get"]

    assert any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in login["parameters"])
    assert login.get("security", []) == []
    assert {"staffOrPlatformBearerAuth": []} in me["security"]
    assert document["components"]["securitySchemes"]["staffBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert document["components"]["securitySchemes"]["staffOrPlatformBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }


def test_openapi_documents_member_auth_without_a_selector_or_bff_gate() -> None:
    document = generated_schema()
    login = document["paths"]["/api/v1/member-auth/login/"]["post"]
    google = document["paths"]["/api/v1/member-auth/google/"]["post"]
    refresh = document["paths"]["/api/v1/member-auth/refresh/"]["post"]
    logout = document["paths"]["/api/v1/member-auth/logout/"]["post"]
    parameters = login.get("parameters", [])
    assert not any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in parameters)
    assert login.get("security", []) == []
    assert refresh.get("security", []) == []
    assert logout.get("security", []) == []
    body = login["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in body:
        body = document["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    assert set(body["properties"]) == {"email", "password", "device_name"}
    assert "member_id" not in body["properties"]
    assert "gym_id" not in body["properties"]
    google_parameters = google.get("parameters", [])
    assert not any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in google_parameters)
    assert google.get("security", []) == []
    google_body = google["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in google_body:
        google_body = document["components"]["schemas"][google_body["$ref"].rsplit("/", 1)[-1]]
    assert set(google_body["properties"]) == {"id_token", "device_name"}
    assert "email" not in google_body["properties"]
    assert "password" not in google_body["properties"]
    assert "member_id" not in google_body["properties"]
    assert "gym_id" not in google_body["properties"]
    assert "200" in google["responses"]
    assert "403" in google["responses"]
    assert "200" in login["responses"]
    assert "200" in refresh["responses"]
    assert "204" in logout["responses"]
    assert "/api/v1/member/me/" in document["paths"]
    me = document["paths"]["/api/v1/member/me/"]["get"]
    assert {"memberBearerAuth": []} in me["security"]
    parameters = me.get("parameters", [])
    assert not any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in parameters)
    assert "/api/v1/member/notifications/" in document["paths"]
    assert "/api/v1/member/devices/" in document["paths"]
    assert "/api/v1/notifications/" not in document["paths"]
    assert "/api/v1/check-ins/scan/" not in document["paths"]


def test_openapi_documents_display_devices_without_a_sample_secret() -> None:
    document = generated_schema()
    rendered = json.dumps(
        {
            "devices": document["paths"]["/api/v1/check-in-display-devices/"],
            "rotate": document["paths"]["/api/v1/check-in-display-devices/{id}/rotate-credential/"],
            "revoke": document["paths"]["/api/v1/check-in-display-devices/{id}/revoke/"],
            "issue": document["paths"]["/api/v1/device/check-in-challenges/"],
        }
    )
    assert "gms-device:" not in rendered
    assert "pbkdf2" not in rendered
    listing = document["paths"]["/api/v1/check-in-display-devices/"]
    assert {"staffBearerAuth": []} in listing["get"]["security"]
    assert {"staffBearerAuth": []} in listing["post"]["security"]
    enroll = _schema(
        document, listing["post"]["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(enroll["properties"]) == {"label", "location"}
    issue = document["paths"]["/api/v1/device/check-in-challenges/"]["post"]
    assert issue.get("security", []) == []
    assert "requestBody" not in issue
    assert any(parameter["name"] == "Authorization" for parameter in issue["parameters"])
    assert not any(
        parameter["name"] == "X-Gym-BFF-Secret" for parameter in issue.get("parameters", [])
    )


def test_openapi_documents_device_ingestion_without_a_secret() -> None:
    document = generated_schema()
    rendered = json.dumps(
        {
            "devices": document["paths"]["/api/v1/check-in-integration-devices/"],
            "rotate": document["paths"][
                "/api/v1/check-in-integration-devices/{id}/rotate-credential/"
            ],
            "revoke": document["paths"]["/api/v1/check-in-integration-devices/{id}/revoke/"],
            "ingest": document["paths"]["/api/v1/device/check-ins/"],
        }
    )
    assert "gms-device:" not in rendered
    assert "pbkdf2" not in rendered
    assert "credential_hash" not in rendered
    listing = document["paths"]["/api/v1/check-in-integration-devices/"]
    assert {"staffBearerAuth": []} in listing["get"]["security"]
    assert {"staffBearerAuth": []} in listing["post"]["security"]
    enroll = _schema(
        document, listing["post"]["requestBody"]["content"]["application/json"]["schema"]
    )
    assert set(enroll["properties"]) == {"label", "location", "kind"}
    device = document["components"]["schemas"]["DisplayDevice"]
    assert "kind" in device["properties"]
    assert "credential_hash" not in device["properties"]
    ingest = document["paths"]["/api/v1/device/check-ins/"]["post"]
    assert ingest.get("security", []) == []
    assert not any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in ingest["parameters"])
    assert not any(parameter["name"] == "Idempotency-Key" for parameter in ingest["parameters"])
    body = _schema(document, ingest["requestBody"]["content"]["application/json"]["schema"])
    assert set(body["properties"]) == {"member_id", "event_id"}
    result = document["components"]["schemas"]["DeviceIngestResult"]
    assert set(result["properties"]) == {"id", "member_id", "method", "check_in_at", "status"}


def _schema(document: dict[str, object], body: dict[str, object]) -> dict[str, object]:
    if "$ref" not in body:
        return body
    name = str(body["$ref"]).rsplit("/", 1)[-1]
    schemas = document["components"]["schemas"]
    assert isinstance(schemas, dict)
    resolved = schemas[name]
    assert isinstance(resolved, dict)
    return resolved


def test_openapi_documents_member_scan_payload_only() -> None:
    document = generated_schema()
    assert "/api/v1/check-ins/scan/" not in document["paths"]
    operation = document["paths"]["/api/v1/member/check-ins/scan/"]["post"]
    parameters = operation.get("parameters", [])
    assert not any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in parameters)
    assert not any(parameter["name"] == "Idempotency-Key" for parameter in parameters)
    assert {"memberBearerAuth": []} in operation["security"]
    assert "staffBearerAuth" not in {next(iter(item)) for item in operation["security"]}
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in body:
        body = document["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    assert set(body["properties"]) == {"payload"}
    assert body.get("additionalProperties") is not True
    for rejected in (
        "member_id",
        "gym_id",
        "membership_id",
        "method",
        "occurred_at",
        "device_id",
        "source_metadata",
        "status",
    ):
        assert rejected not in body["properties"]
    assert "201" in operation["responses"]
    assert "409" in operation["responses"]
    assert document["components"]["securitySchemes"]["memberBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }


def test_openapi_documents_trainer_scan_without_idempotency_or_generic_route() -> None:
    document = generated_schema()
    assert "/api/v1/check-ins/scan/" not in document["paths"]
    operation = document["paths"]["/api/v1/trainer/check-ins/scan/"]["post"]
    parameters = operation.get("parameters", [])
    assert not any(parameter["name"] == "Idempotency-Key" for parameter in parameters)
    assert {"staffBearerAuth": []} in operation["security"]
    assert "memberBearerAuth" not in {next(iter(item)) for item in operation["security"]}
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in body:
        body = document["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]
    assert set(body["properties"]) == {"payload", "member_id"}
    assert body.get("additionalProperties") is not True
    for rejected in (
        "gym_id",
        "membership_id",
        "method",
        "occurred_at",
        "device_id",
        "source_metadata",
        "status",
        "recorded_by_membership_id",
        "created_by_membership_id",
        "idempotency_key",
    ):
        assert rejected not in body["properties"]
    assert "201" in operation["responses"]
    assert "400" in operation["responses"]
    assert "403" in operation["responses"]
    assert "404" in operation["responses"]
    assert "409" in operation["responses"]
    assert "429" in operation["responses"]


def test_openapi_documents_platform_provisioning_idempotency() -> None:
    document = generated_schema()
    create = document["paths"]["/api/v1/admin/gyms/"]["post"]
    header = next(
        parameter for parameter in create["parameters"] if parameter["name"] == "Idempotency-Key"
    )

    assert header["required"] is True
    assert {"platformBearerAuth": []} in create["security"]
    assert "201" in create["responses"]
    assert "409" in create["responses"]
    assert "422" in create["responses"]
    owner_input = document["components"]["schemas"]["AdminOwnerInputRequest"]["properties"]
    assert owner_input["send_invitation"]["default"] is True


def test_openapi_documents_tenant_audit_reads() -> None:
    document = generated_schema()
    audit = document["paths"]["/api/v1/audit-logs/"]["get"]
    schemas = document["components"]["schemas"]
    parameters = {parameter["name"]: parameter for parameter in audit["parameters"]}
    item = schemas["TenantAuditItem"]["properties"]
    meta = schemas["TenantAuditMeta"]["properties"]
    scope = meta["scope"]
    if "$ref" in scope:
        scope = schemas[scope["$ref"].rsplit("/", 1)[-1]]
    outcome = parameters["outcome"]["schema"]
    if "$ref" in outcome:
        outcome = schemas[outcome["$ref"].rsplit("/", 1)[-1]]

    assert {"staffBearerAuth": []} in audit["security"]
    assert audit["security"] == [{"staffBearerAuth": []}]
    assert set(parameters) == {
        "action",
        "after",
        "limit",
        "module",
        "outcome",
        "target_id",
        "target_type",
    }
    assert parameters["limit"]["schema"]["default"] == 25
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    assert parameters["action"]["schema"]["maxLength"] == 100
    assert parameters["module"]["schema"]["maxLength"] == 50
    assert parameters["target_type"]["schema"]["maxLength"] == 100
    assert parameters["after"]["schema"]["maxLength"] == 2048
    assert parameters["target_id"]["schema"]["format"] == "uuid"
    assert set(outcome["enum"]) == {"SUCCESS", "DENIED", "ERROR"}
    assert set(item) == {
        "id",
        "actor_membership_id",
        "impersonator_membership_id",
        "action",
        "module",
        "target_type",
        "target_id",
        "old_values",
        "new_values",
        "request_id",
        "outcome",
        "occurred_at",
    }
    assert item["actor_membership_id"]["nullable"] is True
    assert item["impersonator_membership_id"]["nullable"] is True
    assert "gym_id" not in item
    assert "actor_email" not in item
    assert "ip_address" not in item
    assert "user_agent" not in item
    assert set(meta) == {"has_more", "next_after", "scope"}
    assert set(scope["enum"]) == {"GYM", "SELF"}
    assert "200" in audit["responses"]
    assert "400" in audit["responses"]
    assert "401" in audit["responses"]
    assert "403" in audit["responses"]


def test_openapi_documents_gym_lifecycle_and_platform_audit_security() -> None:
    document = generated_schema()
    detail = document["paths"]["/api/v1/admin/gyms/{gym_id}/"]
    activate = document["paths"]["/api/v1/admin/gyms/{gym_id}/activate/"]["post"]
    suspend = document["paths"]["/api/v1/admin/gyms/{gym_id}/suspend/"]["post"]
    audit = document["paths"]["/api/v1/admin/audit-logs/"]["get"]

    assert {"platformBearerAuth": []} in detail["get"]["security"]
    assert {"platformBearerAuth": []} in detail["patch"]["security"]
    assert {"platformBearerAuth": []} in activate["security"]
    assert {"platformBearerAuth": []} in suspend["security"]
    assert {"platformBearerAuth": []} in audit["security"]
    assert "422" in activate["responses"]
    assert "422" in suspend["responses"]


def test_openapi_documents_staff_and_roles() -> None:
    document = generated_schema()
    invite = document["paths"]["/api/v1/staff/invitations/"]["post"]
    accept = document["paths"]["/api/v1/staff/invitations/accept/"]["post"]
    update = document["paths"]["/api/v1/staff/{id}/"]["patch"]
    roles = document["paths"]["/api/v1/roles/"]
    detail = document["paths"]["/api/v1/roles/{id}/"]
    archive = document["paths"]["/api/v1/roles/{id}/archive/"]["post"]
    permissions = document["paths"]["/api/v1/permissions/"]["get"]
    replace = document["paths"]["/api/v1/roles/{id}/permissions/"]["put"]
    membership = document["paths"]["/api/v1/gym-memberships/{id}/roles/"]
    assert {"staffBearerAuth": []} in update["security"]
    schemas = document["components"]["schemas"]
    invite_properties = schemas["StaffInviteInputRequest"]["properties"]
    update_properties = schemas["PatchedStaffUpdateRequest"]["properties"]

    assert {"staffBearerAuth": []} in invite["security"]
    assert {"staffBearerAuth": []} in roles["get"]["security"]
    assert {"staffBearerAuth": []} in roles["post"]["security"]
    assert {"staffBearerAuth": []} in detail["get"]["security"]
    assert {"staffBearerAuth": []} in detail["patch"]["security"]
    assert {"staffBearerAuth": []} in archive["security"]
    assert {"staffBearerAuth": []} in permissions["security"]
    assert {"staffBearerAuth": []} in replace["security"]
    assert {"staffBearerAuth": []} in membership["get"]["security"]
    assert {"staffBearerAuth": []} in membership["put"]["security"]
    assert accept.get("security", []) == []
    assert any(parameter["name"] == "X-Gym-BFF-Secret" for parameter in accept["parameters"])
    assert "role_ids" in invite_properties
    assert set(invite_properties).isdisjoint(
        {
            "gym_id",
            "is_owner",
            "status",
            "authz_version",
            "invitation_token",
            "profile_photo_asset_id",
        }
    )
    assert set(update_properties) == {
        "staff_code",
        "display_name",
        "phone",
        "joined_date",
        "employment_title",
        "default_commission_percent",
    }
    assert "put" not in document["paths"]["/api/v1/staff/{id}/"]
    assert "RoleChoice" not in schemas
    assert "RoleChoiceListEnvelope" not in schemas
    staff_list = document["paths"]["/api/v1/staff/"]["get"]
    parameters = {parameter["name"]: parameter for parameter in staff_list["parameters"]}
    assert set(parameters) == {"after", "limit", "membership_status", "search", "status"}
    assert parameters["limit"]["schema"]["default"] == 25
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 100
    meta = schemas["StaffListEnvelope"]["properties"]["meta"]
    meta_schema = schemas[meta["$ref"].rsplit("/", 1)[-1]] if "$ref" in meta else meta
    assert set(meta_schema["properties"]) == {"has_more", "next_after"}

    for operation in (roles["get"], permissions):
        query = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert set(query) == {"after", "limit"}
        assert query["limit"]["schema"]["default"] == 25
        assert query["limit"]["schema"]["minimum"] == 1
        assert query["limit"]["schema"]["maximum"] == 100

    create_properties = schemas["RoleCreateRequest"]["properties"]
    update_role_properties = schemas["PatchedRoleUpdateRequest"]["properties"]
    permission_properties = schemas["RolePermissionReplaceRequest"]["properties"]
    membership_properties = schemas["MembershipRoleReplaceRequest"]["properties"]
    role_properties = schemas["Role"]["properties"]
    assert set(create_properties) == {"key", "name", "description", "is_active"}
    assert set(update_role_properties) == {"key", "name", "description", "is_active"}
    assert set(permission_properties) == {"permission_codes"}
    assert set(membership_properties) == {"role_ids"}
    assert {
        "is_protected",
        "is_template",
        "permission_codes",
        "permission_count",
        "assignment_count",
    } <= set(role_properties)
    assert set(schemas["MembershipRole"]["properties"]) == {
        "gym_membership_id",
        "roles",
        "authz_version",
    }
    assert "post" not in document["paths"]["/api/v1/roles/{id}/"]
    assert "get" not in document["paths"]["/api/v1/roles/{id}/archive/"]
    assert "post" not in document["paths"]["/api/v1/roles/{id}/permissions/"]


def test_openapi_documents_tenant_gym_profile_and_settings() -> None:
    document = generated_schema()
    profile = document["paths"]["/api/v1/gym/"]
    settings = document["paths"]["/api/v1/settings/"]
    schemas = document["components"]["schemas"]

    assert {"staffBearerAuth": []} in profile["get"]["security"]
    assert {"staffBearerAuth": []} in profile["patch"]["security"]
    assert {"staffBearerAuth": []} in settings["get"]["security"]
    assert {"staffBearerAuth": []} in settings["patch"]["security"]
    assert "400" in profile["patch"]["responses"]
    assert "400" in settings["patch"]["responses"]

    profile_read = schemas["GymProfile"]["properties"]
    assert {
        "id",
        "code",
        "name",
        "legal_name",
        "status",
        "timezone",
        "currency",
        "subscription_plan_id",
        "subscription_plan_name",
        "subscription_status",
        "subscription_starts_at",
        "subscription_ends_at",
        "created_at",
        "updated_at",
    } <= set(profile_read)
    assert profile_read["subscription_plan_id"]["nullable"] is True
    assert profile_read["subscription_plan_name"]["nullable"] is True
    assert profile_read["subscription_starts_at"]["nullable"] is True
    assert profile_read["subscription_ends_at"]["nullable"] is True

    profile_write = schemas["PatchedGymProfileUpdateRequest"]["properties"]
    assert {
        "name",
        "legal_name",
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
        "timezone",
        "currency",
    } <= set(profile_write)
    assert set(profile_write).isdisjoint(
        {
            "id",
            "code",
            "status",
            "gym_id",
            "subscription_plan_id",
            "subscription_plan_name",
            "subscription_status",
            "subscription_starts_at",
            "subscription_ends_at",
            "created_at",
            "updated_at",
        }
    )

    settings_write = schemas["PatchedGymSettingsUpdateRequest"]["properties"]
    assert "require_separate_finance_approval" in settings_write
    assert set(settings_write).isdisjoint(
        {
            "gym_id",
            "next_member_number",
            "created_by_membership_id",
            "updated_by_membership_id",
            "created_at",
            "updated_at",
        }
    )
    settings_read = schemas["GymSettings"]["properties"]
    assert "next_member_number" in settings_read
    assert "require_separate_finance_approval" in settings_read
    printer_fields = {
        "printer_enabled",
        "printer_print_logo",
        "printer_print_address",
        "printer_print_phone",
        "printer_footer",
    }
    assert printer_fields <= set(settings_write)
    assert printer_fields <= set(settings_read)


def test_openapi_documents_platform_dashboard_and_subscription_update() -> None:
    document = generated_schema()
    dashboard = document["paths"]["/api/v1/admin/dashboard/"]["get"]
    subscriptions = document["paths"]["/api/v1/admin/subscriptions/"]["get"]
    update = document["paths"]["/api/v1/admin/subscriptions/{id}/"]["patch"]
    schemas = document["components"]["schemas"]

    assert {"platformBearerAuth": []} in dashboard["security"]
    assert {"platformBearerAuth": []} in subscriptions["security"]
    assert {"platformBearerAuth": []} in update["security"]
    assert "409" in update["responses"]
    assert "404" in update["responses"]
    assert "patch" not in document["paths"]["/api/v1/admin/subscriptions/"]
    assert "get" not in document["paths"]["/api/v1/admin/subscriptions/{id}/"]

    dashboard_properties = schemas["AdminDashboard"]["properties"]
    assert set(dashboard_properties) == {
        "gym_statuses",
        "gym_subscription_statuses",
        "subscription_plan_statuses",
    }
    assert set(schemas["GymStatusCounts"]["properties"]) == {
        "total",
        "PENDING",
        "ACTIVE",
        "SUSPENDED",
        "CLOSED",
    }
    assert set(schemas["GymSubscriptionStatusCounts"]["properties"]) == {
        "TRIAL",
        "ACTIVE",
        "PAST_DUE",
        "CANCELLED",
    }
    assert set(schemas["SubscriptionPlanStatusCounts"]["properties"]) == {"ACTIVE", "RETIRED"}

    parameters = {parameter["name"]: parameter for parameter in subscriptions["parameters"]}
    status_parameter = parameters["status"]["schema"]
    if "$ref" in status_parameter:
        status_parameter = schemas[status_parameter["$ref"].rsplit("/", 1)[-1]]
    list_status = schemas["AdminSubscription"]["properties"]["status"]
    if "$ref" in list_status:
        list_status = schemas[list_status["$ref"].rsplit("/", 1)[-1]]

    assert set(parameters) == {"status"}
    assert parameters["status"].get("required", False) is False
    assert status_parameter["default"] == "ACTIVE"
    assert set(status_parameter["enum"]) == {"ACTIVE", "RETIRED", "ALL"}
    assert "400" in subscriptions["responses"]
    list_properties = schemas["AdminSubscription"]["properties"]
    assert set(list_properties) == {
        "id",
        "code",
        "name",
        "status",
        "price",
        "currency",
        "billing_interval",
    }
    assert set(list_status["enum"]) == {"ACTIVE", "RETIRED"}
    detail_properties = schemas["AdminSubscriptionDetail"]["properties"]
    assert set(detail_properties) == {
        "id",
        "code",
        "name",
        "status",
        "price",
        "currency",
        "billing_interval",
        "created_at",
        "updated_at",
    }
    update_properties = schemas["PatchedAdminSubscriptionUpdateRequest"]["properties"]
    status_schema = schemas[update_properties["status"]["$ref"].rsplit("/", 1)[-1]]
    assert set(update_properties) == {"name", "status", "price", "billing_interval"}
    assert set(status_schema["enum"]) == {"ACTIVE", "RETIRED"}


def test_openapi_documents_complete_retry_after() -> None:
    document = generated_schema()
    complete = document["paths"]["/api/v1/files/{id}/complete/"]["post"]
    header = complete["responses"]["409"]["headers"]["Retry-After"]
    assert header["schema"]["type"] == "integer"
    assert header.get("required", False) is False
    assert "FILE_SCAN_IN_PROGRESS" in header["description"]
    names = [parameter.get("name") for parameter in complete.get("parameters", [])]
    assert "Retry-After" not in names


def test_openapi_documents_payment_commands_and_generated_types() -> None:
    document = generated_schema()
    paths = document["paths"]
    writes = (
        (paths["/api/v1/payments/"]["post"], "201"),
        (paths["/api/v1/payments/{payment_id}/"]["patch"], "200"),
        (paths["/api/v1/payments/{payment_id}/submit/"]["post"], "200"),
        (paths["/api/v1/payments/{payment_id}/approve/"]["post"], "200"),
        (paths["/api/v1/payments/{payment_id}/reject/"]["post"], "200"),
        (paths["/api/v1/payments/{payment_id}/cancel/"]["post"], "200"),
        (paths["/api/v1/payments/{payment_id}/void/"]["post"], "200"),
        (paths["/api/v1/payments/{payment_id}/refund/"]["post"], "200"),
        (paths["/api/v1/receipts/{receipt_id}/void/"]["post"], "200"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "400" in operation["responses"]
        assert "409" in operation["responses"]
    assert "Idempotency-Key" not in {
        parameter["name"] for parameter in paths["/api/v1/payments/"]["get"].get("parameters", [])
    }
    assert "200" in paths["/api/v1/receipts/{receipt_id}/download/"]["get"]["responses"]
    assert "200" in paths["/api/v1/receipts/print-context/"]["get"]["responses"]
    assert "200" in paths["/api/v1/receipts/{receipt_id}/print/"]["get"]["responses"]
    assert "post" not in paths["/api/v1/receipts/print-context/"]
    assert "post" not in paths["/api/v1/receipts/{receipt_id}/print/"]
    sales = paths["/api/v1/sales/daily/"]["get"]
    assert any(parameter["name"] == "date" for parameter in sales["parameters"])
    assert "200" in sales["responses"]
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/payments/{payment_id}/approve/"' in generated
    assert '"/api/v1/payments/{payment_id}/void/"' in generated
    assert '"/api/v1/receipts/{receipt_id}/void/"' in generated
    assert '"/api/v1/receipts/print-context/"' in generated
    assert '"/api/v1/receipts/{receipt_id}/print/"' in generated
    assert '"/api/v1/sales/daily/"' in generated


def test_openapi_documents_expense_commands() -> None:
    document = generated_schema()
    paths = document["paths"]
    writes = (
        (paths["/api/v1/expense-categories/"]["post"], "201"),
        (paths["/api/v1/expense-categories/{category_id}/"]["patch"], "200"),
        (paths["/api/v1/expenses/"]["post"], "201"),
        (paths["/api/v1/expenses/{expense_id}/"]["patch"], "200"),
        (paths["/api/v1/expenses/{expense_id}/submit/"]["post"], "200"),
        (paths["/api/v1/expenses/{expense_id}/approve/"]["post"], "200"),
        (paths["/api/v1/expenses/{expense_id}/reject/"]["post"], "200"),
        (paths["/api/v1/expenses/{expense_id}/cancel/"]["post"], "200"),
        (paths["/api/v1/expenses/{expense_id}/void/"]["post"], "200"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "409" in operation["responses"]
    assert "Idempotency-Key" not in {
        parameter["name"] for parameter in paths["/api/v1/expenses/"]["get"].get("parameters", [])
    }
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/expenses/{expense_id}/approve/"' in generated
    assert '"/api/v1/expenses/{expense_id}/void/"' in generated
    assert '"/api/v1/expense-categories/"' in generated


def test_openapi_documents_salary_commands() -> None:
    document = generated_schema()
    paths = document["paths"]
    assert "/api/v1/salary-periods/{period_id}/void/" in paths
    writes = (
        (paths["/api/v1/salary-periods/"]["post"], "201"),
        (paths["/api/v1/salary-periods/{period_id}/"]["patch"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/lines/"]["put"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/submit/"]["post"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/approve/"]["post"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/reject/"]["post"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/cancel/"]["post"], "200"),
        (paths["/api/v1/salary-periods/{period_id}/void/"]["post"], "200"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "409" in operation["responses"]
    assert "Idempotency-Key" not in {
        parameter["name"]
        for parameter in paths["/api/v1/salary-periods/"]["get"].get("parameters", [])
    }
    approve = paths["/api/v1/salary-periods/{period_id}/approve/"]["post"]
    assert "422" in approve["responses"]
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/salary-periods/{period_id}/approve/"' in generated
    assert '"/api/v1/salary-periods/{period_id}/void/"' in generated
    assert '"/api/v1/salary-periods/{period_id}/lines/"' in generated


def test_openapi_documents_inventory_commands() -> None:
    document = generated_schema()
    paths = document["paths"]
    assert "/api/v1/retail-sales/{sale_id}/void/" in paths
    assert "/api/v1/retail-sales/{sale_id}/return/" in paths
    assert "/api/v1/product-categories/{id}/" not in paths
    writes = (
        (paths["/api/v1/product-categories/"]["post"], "201"),
        (paths["/api/v1/products/"]["post"], "201"),
        (paths["/api/v1/products/{product_id}/"]["patch"], "200"),
        (paths["/api/v1/stock-movements/adjust/"]["post"], "201"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "409" in operation["responses"]
    for path in (
        "/api/v1/product-categories/",
        "/api/v1/products/",
        "/api/v1/products/low-stock/",
        "/api/v1/stock-movements/",
    ):
        assert "Idempotency-Key" not in {
            parameter["name"] for parameter in paths[path]["get"].get("parameters", [])
        }
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/stock-movements/adjust/"' in generated
    assert '"/api/v1/products/low-stock/"' in generated


def test_openapi_documents_retail_commands() -> None:
    document = generated_schema()
    paths = document["paths"]
    writes = (
        (paths["/api/v1/retail-sales/"]["post"], "201"),
        (paths["/api/v1/retail-sales/{sale_id}/"]["patch"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/submit/"]["post"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/approve/"]["post"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/reject/"]["post"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/cancel/"]["post"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/void/"]["post"], "200"),
        (paths["/api/v1/retail-sales/{sale_id}/return/"]["post"], "200"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "409" in operation["responses"]
    assert "Idempotency-Key" not in {
        parameter["name"]
        for parameter in paths["/api/v1/retail-sales/"]["get"].get("parameters", [])
    }
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/retail-sales/{sale_id}/approve/"' in generated
    assert '"/api/v1/retail-sales/{sale_id}/void/"' in generated


def test_openapi_documents_class_schedule_idempotency() -> None:
    document = generated_schema()
    paths = document["paths"]
    writes = (
        (paths["/api/v1/class-schedules/"]["post"], "201"),
        (paths["/api/v1/class-schedules/{id}/"]["patch"], "200"),
        (paths["/api/v1/class-occurrences/{id}/cancel/"]["post"], "200"),
        (paths["/api/v1/class-bookings/"]["post"], "201"),
        (paths["/api/v1/class-bookings/{id}/attend/"]["post"], "200"),
        (paths["/api/v1/class-bookings/{id}/no-show/"]["post"], "200"),
        (paths["/api/v1/class-bookings/{id}/cancel/"]["post"], "200"),
        (paths["/api/v1/pt-bookings/"]["post"], "201"),
        (paths["/api/v1/pt-bookings/{id}/attend/"]["post"], "200"),
        (paths["/api/v1/pt-bookings/{id}/no-show/"]["post"], "200"),
        (paths["/api/v1/pt-bookings/{id}/cancel/"]["post"], "200"),
    )
    for operation, success in writes:
        header = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        )
        assert header["required"] is True
        assert success in operation["responses"]
        assert "400" in operation["responses"]
        assert "409" in operation["responses"]
    for path, method in (
        ("/api/v1/class-schedules/", "get"),
        ("/api/v1/class-types/", "get"),
        ("/api/v1/class-types/", "post"),
        ("/api/v1/class-types/{id}/", "patch"),
        ("/api/v1/class-occurrences/", "get"),
    ):
        assert "Idempotency-Key" not in {
            parameter["name"] for parameter in paths[path][method].get("parameters", [])
        }
    generated = (CONTRACT_PATH.parents[1] / "web/src/types/api.generated.ts").read_text()
    assert '"/api/v1/class-schedules/"' in generated
    assert '"/api/v1/class-schedules/{id}/"' in generated
