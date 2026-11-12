"""Salary period and cash session reads stay on stored values and existing filters."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.operations_reads import (
    operations_read_tool_contracts,
    search_cash_sessions,
    search_salary_periods,
)
from apps.assistant.proposals import declaration
from apps.tenants.models import Gym, GymMembership

_PERIOD_KEYS = {
    "id",
    "period_start",
    "period_end",
    "business_date",
    "description",
    "status",
    "submitted_at",
    "approved_at",
    "rejected_at",
    "posted_at",
    "voided_at",
    "cancelled_at",
    "lines",
}
_LINE_KEYS = {
    "id",
    "staff_profile_id",
    "staff_code",
    "display_name",
    "amount",
    "currency",
    "payment_method",
}
_SESSION_KEYS = {
    "id",
    "business_date",
    "register_code",
    "currency",
    "status",
    "opening_cash",
    "cash_sales",
    "cash_expenses",
    "manual_net",
    "expected_closing",
    "actual_closing",
    "difference",
    "closed_at",
}
_HIDDEN = {
    "rejection_reason",
    "reason",
    "external_reference",
    "difference_reason",
    "opened_by_membership_id",
    "closed_by_membership_id",
    "created_by_membership_id",
    "phone",
    "reconciliation_status",
    "balance",
    "total_amount",
    "period_total",
}


class _Lines:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows

    def all(self) -> list[SimpleNamespace]:
        return self.rows


def _gym() -> Gym:
    return Gym(
        id=uuid4(),
        code="pay",
        name="Pay",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
        currency="MMK",
    )


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE),
        payload=payload,
        request_id="req_operations_read",
        ip_address=None,
        user_agent="",
    )


def _period() -> SimpleNamespace:
    profile_id = uuid4()
    line = SimpleNamespace(
        id=uuid4(),
        staff_profile_id=profile_id,
        staff_profile=SimpleNamespace(
            id=profile_id,
            staff_code="ST01",
            display_name="Nilar Tun",
            phone="+959000000099",
        ),
        amount=Decimal("250000.00"),
        currency="MMK",
        payment_method="BANK_TRANSFER",
        external_reference="ACCT-998877",
        description="Base pay",
        created_by_membership_id=uuid4(),
    )
    return SimpleNamespace(
        id=uuid4(),
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 10),
        business_date=date(2026, 11, 10),
        description="November pay",
        status="POSTED",
        submitted_at=None,
        approved_at=None,
        rejected_at=None,
        posted_at=datetime(2026, 11, 10, 9, 0, tzinfo=UTC),
        voided_at=None,
        cancelled_at=None,
        rejection_reason="secret-reason",
        reason="secret-reason",
        created_by_membership_id=uuid4(),
        lines=_Lines([line]),
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        business_date=date(2026, 11, 11),
        register_code="FRONT",
        currency="MMK",
        status="OPEN",
        opening_cash=Decimal("10.00"),
        cash_sales=Decimal("0.00"),
        cash_expenses=Decimal("0.00"),
        manual_net=Decimal("0.00"),
        expected_closing=Decimal("99.00"),
        actual_closing=None,
        difference=Decimal("3.00"),
        closed_at=None,
        difference_reason="secret-note",
        opened_by_membership_id=uuid4(),
        closed_by_membership_id=None,
    )


def test_operations_reads_advertise_only_supported_filters() -> None:
    periods = declaration(operations_read_tool_contracts()["search_salary_periods"])
    sessions = declaration(operations_read_tool_contracts()["search_cash_sessions"])
    period_parameters = periods["parameters"]
    session_parameters = sessions["parameters"]
    assert isinstance(period_parameters, dict)
    assert isinstance(session_parameters, dict)
    assert period_parameters["required"] == []
    assert session_parameters["required"] == []
    period_properties = period_parameters["properties"]
    session_properties = session_parameters["properties"]
    assert isinstance(period_properties, dict)
    assert isinstance(session_properties, dict)
    assert set(period_properties) == {
        "period_id",
        "status",
        "business_date",
        "limit",
        "after",
    }
    assert set(session_properties) == {
        "session_id",
        "status",
        "register_code",
        "business_date",
        "limit",
        "after",
    }
    assert "date_from" not in period_properties
    assert "date_to" not in session_properties
    period_contract = operations_read_tool_contracts()["search_salary_periods"]
    session_contract = operations_read_tool_contracts()["search_cash_sessions"]
    assert period_contract.mode == ExecutionMode.READ
    assert session_contract.mode == ExecutionMode.READ
    assert period_contract.finance_closed is True
    assert session_contract.finance_closed is True
    assert period_contract.definition.permissions == ("salaries.view",)
    assert session_contract.definition.permissions == ("cashbook.view",)
    assert period_contract.definition.features == ("finance",)
    assert session_contract.definition.features == ("finance",)
    assert period_contract.definition.module == "salaries"
    assert session_contract.definition.module == "cashbook"
    assert period_contract.definition.confirmation_required is False
    assert session_contract.definition.confirmation_required is False
    assert period_contract.definition.operation == "read"
    assert session_contract.definition.operation == "read"


def test_search_salary_periods_returns_stored_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _period()
    captured: dict[str, object] = {}
    other_gym = uuid4()

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.operations_reads.page_periods", page)
    monkeypatch.setattr(
        "apps.assistant.operations_reads._detailed_periods", lambda _gym, rows: rows
    )
    call = _call(
        gym,
        {
            "gym_id": str(other_gym),
            "business_date": "2026-11-10",
            "status": "POSTED",
            "limit": 2,
        },
    )
    result = search_salary_periods(call)
    assert captured["gym_id"] == gym.id
    assert captured["gym_id"] != other_gym
    assert captured["actor_membership_id"] == call.actor.id
    assert captured["business_date"] == date(2026, 11, 10)
    assert captured["status"] == "POSTED"
    assert captured["limit"] == 2
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["status"] == "POSTED"
    assert found[0]["business_date"] == "2026-11-10"
    assert found[0]["period_start"] == "2026-11-01"
    assert found[0]["period_end"] == "2026-11-10"
    assert found[0]["posted_at"] == "2026-11-10T09:00:00+00:00"
    assert "total_amount" not in found[0]
    line = found[0]["lines"][0]
    assert line["amount"] == "250000.00"
    assert line["staff_code"] == "ST01"
    assert line["display_name"] == "Nilar Tun"
    assert line["payment_method"] == "BANK_TRANSFER"
    assert set(found[0]) == _PERIOD_KEYS
    assert set(line) == _LINE_KEYS
    assert _HIDDEN.isdisjoint(found[0])
    assert _HIDDEN.isdisjoint(line)
    assert "ACCT-998877" not in str(found)
    assert "+959000000099" not in str(found)
    assert "secret-reason" not in str(found)


def test_salary_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"status": "SUBMITTED"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"period_id": "not-a-period"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"business_date": "yesterday"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"date_from": "2026-11-10"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"date_to": "2026-11-10"}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"limit": 26}))
    with pytest.raises(ValidationError):
        search_salary_periods(_call(gym, {"actor_membership_id": str(uuid4())}))


def test_salary_status_values_stay_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[object], dict[str, object]]:
        captured.update(kwargs)
        return [], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.operations_reads.page_periods", page)
    for status in ("DRAFT", "PENDING_APPROVAL", "REJECTED", "CANCELLED", "VOID"):
        result = search_salary_periods(_call(_gym(), {"status": status}))
        assert captured["status"] == status
        assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_salary_period_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(**_kwargs: object) -> SimpleNamespace:
        raise Http404

    monkeypatch.setattr("apps.assistant.operations_reads.get_period", missing)
    with pytest.raises(Http404):
        search_salary_periods(_call(_gym(), {"period_id": str(uuid4())}))


def test_salary_filter_mismatch_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _period()
    monkeypatch.setattr("apps.assistant.operations_reads.get_period", lambda **_kwargs: row)
    with pytest.raises(Http404):
        search_salary_periods(_call(_gym(), {"period_id": str(row.id), "status": "DRAFT"}))


def test_salary_page_can_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.operations_reads.page_periods",
        lambda **_kwargs: ([], {"has_more": False, "next_after": None}),
    )
    result = search_salary_periods(_call(_gym(), {"business_date": "2020-01-01"}))
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_salary_line_amount_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _period()
    row.lines.rows[0].amount = None
    monkeypatch.setattr(
        "apps.assistant.operations_reads.page_periods",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr(
        "apps.assistant.operations_reads._detailed_periods", lambda _gym, rows: rows
    )
    with pytest.raises(ExecutionRejected) as missing:
        search_salary_periods(_call(_gym(), {}))
    assert missing.value.code == "read_result"


def test_search_cash_sessions_returns_stored_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _session()
    captured: dict[str, object] = {}
    other_gym = uuid4()

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": True, "next_after": "cursor-1"}

    monkeypatch.setattr("apps.assistant.operations_reads.page_cash_sessions", page)
    monkeypatch.setattr(
        "apps.assistant.operations_reads._detailed_sessions", lambda _gym, rows: rows
    )
    call = _call(
        gym,
        {
            "gym_id": str(other_gym),
            "business_date": "2026-11-11",
            "status": "OPEN",
            "register_code": "FRONT",
            "limit": 1,
        },
    )
    result = search_cash_sessions(call)
    assert captured["gym_id"] == gym.id
    assert captured["actor"] is call.actor
    assert captured["status"] == "OPEN"
    assert captured["register_code"] == "FRONT"
    assert captured["business_date"] == date(2026, 11, 11)
    assert captured["limit"] == 1
    found = result["results"]
    assert isinstance(found, list)
    assert result["has_more"] is True
    assert result["next_after"] == "cursor-1"
    assert found[0]["status"] == "OPEN"
    assert found[0]["opening_cash"] == "10.00"
    assert found[0]["cash_sales"] == "0.00"
    assert found[0]["cash_expenses"] == "0.00"
    assert found[0]["manual_net"] == "0.00"
    assert found[0]["expected_closing"] == "99.00"
    assert found[0]["actual_closing"] is None
    assert found[0]["difference"] == "3.00"
    assert found[0]["closed_at"] is None
    assert set(found[0]) == _SESSION_KEYS
    assert _HIDDEN.isdisjoint(found[0])
    assert "secret-note" not in str(found)


def test_closed_cash_session_keeps_stored_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _session()
    row.status = "CLOSED"
    row.actual_closing = Decimal("12.00")
    row.difference = Decimal("2.00")
    row.closed_at = datetime(2026, 11, 11, 12, 30, tzinfo=UTC)
    monkeypatch.setattr(
        "apps.assistant.operations_reads.page_cash_sessions",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr(
        "apps.assistant.operations_reads._detailed_sessions", lambda _gym, rows: rows
    )
    result = search_cash_sessions(_call(_gym(), {"status": "CLOSED"}))
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["status"] == "CLOSED"
    assert found[0]["expected_closing"] == "99.00"
    assert found[0]["actual_closing"] == "12.00"
    assert found[0]["difference"] == "2.00"
    assert found[0]["closed_at"] == "2026-11-11T12:30:00+00:00"
    assert "reconciliation_status" not in found[0]


def test_cash_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"status": "RECONCILED"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"register_code": "front"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"register_code": "FRONT DESK"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"session_id": "not-a-session"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"business_date": "today"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"date_from": "2026-11-11"}))
    with pytest.raises(ValidationError):
        search_cash_sessions(_call(gym, {"limit": 0}))


def test_cash_void_status_is_a_real_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[object], dict[str, object]]:
        captured.update(kwargs)
        return [], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.operations_reads.page_cash_sessions", page)
    result = search_cash_sessions(_call(_gym(), {"status": "VOID"}))
    assert captured["status"] == "VOID"
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_cash_session_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(**_kwargs: object) -> SimpleNamespace:
        raise Http404

    monkeypatch.setattr("apps.assistant.operations_reads.get_visible_session", missing)
    with pytest.raises(Http404):
        search_cash_sessions(_call(_gym(), {"session_id": str(uuid4())}))


def test_cash_page_can_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.operations_reads.page_cash_sessions",
        lambda **_kwargs: ([], {"has_more": False, "next_after": None}),
    )
    result = search_cash_sessions(_call(_gym(), {"business_date": "2020-01-01"}))
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_expected_closing_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _session()
    row.expected_closing = None
    monkeypatch.setattr(
        "apps.assistant.operations_reads.page_cash_sessions",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr(
        "apps.assistant.operations_reads._detailed_sessions", lambda _gym, rows: rows
    )
    with pytest.raises(ExecutionRejected) as missing:
        search_cash_sessions(_call(_gym(), {}))
    assert missing.value.code == "read_result"
