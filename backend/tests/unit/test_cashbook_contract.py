from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.serializers import (
    AdjustCashSessionSerializer,
    OpenCashSessionSerializer,
    ReconcileCashSessionSerializer,
)
from apps.cashbook.services import command_route
from apps.cashbook.views import (
    AdjustCashSessionView,
    CashSessionDetailView,
    CashSessionListView,
    CashTransactionListView,
    OpenCashSessionView,
    ReconcileCashSessionView,
)
from apps.common.finance.idempotency import canonical_request_hash
from tests.unit.test_openapi_contract import PUBLISHED_PATHS

CASH_PATHS = {
    "/api/v1/cash-sessions/",
    "/api/v1/cash-sessions/open/",
    "/api/v1/cash-sessions/{id}/",
    "/api/v1/cash-sessions/{id}/adjustments/",
    "/api/v1/cash-sessions/{id}/reconcile/",
    "/api/v1/cash-transactions/",
}


def test_cash_models_are_tenant_scoped_and_constrained() -> None:
    session_indexes = {index.name: tuple(index.fields) for index in CashSession._meta.indexes}
    transaction_indexes = {
        index.name: tuple(index.fields) for index in CashTransaction._meta.indexes
    }
    session_constraints = {item.name for item in CashSession._meta.constraints}
    transaction_constraints = {item.name for item in CashTransaction._meta.constraints}

    assert CashSession._meta.db_table == "cashbook_cash_session"
    assert CashTransaction._meta.db_table == "cashbook_cash_transaction"
    assert all(fields[0] == "gym" for fields in session_indexes.values())
    assert all(fields[0] == "gym" for fields in transaction_indexes.values())
    assert session_indexes["cashsession_gym_status_idx"] == ("gym", "status")
    assert session_indexes["cashsession_gym_date_idx"] == ("gym", "business_date")
    assert session_indexes["cashsession_gym_register_idx"] == ("gym", "register_code")
    assert "cashsession_open_register_uniq" in session_constraints
    assert "cashsession_expected_ck" in session_constraints
    assert "cashsession_evidence_ck" in session_constraints
    assert "cashtxn_source_uniq" in transaction_constraints
    assert "cashtxn_reversal_uniq" in transaction_constraints
    assert "cashtxn_shape_ck" in transaction_constraints
    assert CashSession.Status.VOID == "VOID"
    assert CashTransaction._meta.get_field("amount").decimal_places == 2


def test_cash_serializers_reject_unknown_server_fields_and_inexact_money() -> None:
    with pytest.raises(ValidationError) as unknown:
        OpenCashSessionSerializer(
            data={
                "register_code": "FRONT",
                "business_date": "2026-10-18",
                "opening_cash": "10.00",
                "status": "OPEN",
            }
        ).is_valid(raise_exception=True)
    assert "status" in unknown.value.detail
    with pytest.raises(ValidationError):
        OpenCashSessionSerializer(
            data={"register_code": "front", "business_date": "2026-10-18", "opening_cash": "10.00"}
        ).is_valid(raise_exception=True)
    with pytest.raises(ValidationError):
        OpenCashSessionSerializer(
            data={"register_code": "FRONT", "business_date": "2026-10-18", "opening_cash": 10.5}
        ).is_valid(raise_exception=True)
    opened = OpenCashSessionSerializer(
        data={"register_code": "FRONT_1", "business_date": "2026-10-18", "opening_cash": "10.50"}
    )
    assert opened.is_valid(), opened.errors
    assert opened.validated_data["opening_cash"] == Decimal("10.50")
    with pytest.raises(ValidationError) as unknown_adjustment:
        AdjustCashSessionSerializer(
            data={
                "direction": "IN",
                "amount": "1.00",
                "currency": "THB",
                "business_date": "2026-10-18",
                "description": "Till",
                "reason": "Counted",
                "source_type": "MANUAL",
            }
        ).is_valid(raise_exception=True)
    assert "source_type" in unknown_adjustment.value.detail
    with pytest.raises(ValidationError) as blank_reason:
        AdjustCashSessionSerializer(
            data={
                "direction": "IN",
                "amount": "1.00",
                "currency": "THB",
                "business_date": "2026-10-18",
                "description": "Till",
                "reason": "   ",
            }
        ).is_valid(raise_exception=True)
    assert "reason" in blank_reason.value.detail
    reconciled = ReconcileCashSessionSerializer(
        data={"actual_closing": "10.00", "gym_id": str(uuid4())}
    )
    assert reconciled.is_valid() is False
    assert "gym_id" in reconciled.errors


def test_cash_routes_permissions_and_idempotency_boundary() -> None:
    assert OpenCashSessionView.required_permission == "cashbook.open"
    assert OpenCashSessionView.required_feature == "finance"
    assert AdjustCashSessionView.required_permission == "cashbook.adjust"
    assert ReconcileCashSessionView.required_permission == "cashbook.reconcile"
    assert CashSessionListView.required_permission == "cashbook.view"
    assert CashSessionDetailView.required_permission == "cashbook.view"
    assert CashTransactionListView.required_permission == "cashbook.view"
    assert "default" in OpenCashSessionView.dispatch._non_atomic_requests
    assert "default" in AdjustCashSessionView.dispatch._non_atomic_requests
    assert "default" in ReconcileCashSessionView.dispatch._non_atomic_requests
    assert not hasattr(CashSessionListView.dispatch, "_non_atomic_requests")
    assert reverse("cash-sessions-open") == "/api/v1/cash-sessions/open/"
    assert reverse("cash-transactions") == "/api/v1/cash-transactions/"
    assert CASH_PATHS <= PUBLISHED_PATHS
    session_id = uuid4()
    gym_id = uuid4()
    actor_id = uuid4()
    first = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=command_route(session_id=session_id, action="adjustments"),
        body={"cash_session_id": str(session_id), "amount": "1.00"},
    )
    second = canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method="POST",
        route=command_route(session_id=uuid4(), action="adjustments"),
        body={"cash_session_id": str(uuid4()), "amount": "1.00"},
    )
    assert str(session_id) in command_route(session_id=session_id, action="reconcile")
    assert first != second
