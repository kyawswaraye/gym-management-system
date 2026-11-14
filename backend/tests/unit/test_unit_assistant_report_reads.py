from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ReadCall
from apps.assistant.report_reads import get_gym_dashboard, get_gym_report, get_trainer_dashboard
from apps.tenants.models import Gym, GymMembership


def test_dashboard_read_returns_each_stored_revenue_category(monkeypatch) -> None:
    gym = Gym(
        id=uuid4(),
        code="dash",
        name="Dash",
        status=Gym.Status.ACTIVE,
        currency="THB",
        timezone="Asia/Bangkok",
    )
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE)
    captured: dict[str, object] = {}

    def dashboard(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "range": {"date_from": "2026-11-01", "date_to": "2026-11-14"},
            "timezone": "Asia/Bangkok",
            "currency": "THB",
            "operational": {
                "new_members": 6,
                "active_members": 10,
                "check_ins": 4,
                "expired_memberships": 0,
                "pending_approvals": 0,
            },
            "financial": {
                "new_member_revenue": "1500.00",
                "renewal_revenue": "0.00",
                "pt_revenue": "8000.00",
                "class_revenue": "0.00",
                "product_revenue": "0.00",
                "other_revenue": "0.00",
                "total_revenue": "9500.00",
                "total_expenses": "5825.00",
                "net_profit": "3675.00",
                "pending_payment_amount": "0.00",
                "pending_payment_member_count": 0,
                "revenue_sources_ranked": [{"source": "PT", "amount": "8000.00"}],
                "expenses_by_category": [{"system_key": "RENT", "amount": "5825.00"}],
                "top_product_items": [{"product_id": uuid4(), "name": "Desk Water"}],
            },
        }

    monkeypatch.setattr("apps.assistant.report_reads.deny_trainer_only", lambda **_kwargs: None)
    monkeypatch.setattr("apps.assistant.report_reads.gym_today", lambda _gym: date(2026, 11, 14))
    monkeypatch.setattr("apps.assistant.report_reads.tenant_dashboard", dashboard)

    result = get_gym_dashboard(
        ReadCall(
            gym=gym,
            actor=actor,
            payload={"date_from": "2026-11-01", "date_to": "2026-11-14"},
            request_id="req_dashboard",
            ip_address=None,
            user_agent="",
            permission_codes=frozenset({"reports.view", "reports.financial"}),
        )
    )

    financial = result["financial"]
    assert isinstance(financial, dict)
    assert financial["new_member_revenue"] == "1500.00"
    assert financial["pt_revenue"] == "8000.00"
    assert financial["class_revenue"] == "0.00"
    assert financial["total_revenue"] == "9500.00"
    assert financial["total_expenses"] == "5825.00"
    assert financial["net_profit"] == "3675.00"
    assert financial["revenue_sources_ranked"] == [{"source": "PT", "amount": "8000.00"}]
    assert captured["include_financial"] is True
    assert captured["date_from"] == date(2026, 11, 1)
    assert captured["date_to"] == date(2026, 11, 14)
    products = financial["top_product_items"]
    assert isinstance(products, list)
    assert isinstance(products[0]["product_id"], str)


def _call(payload: dict[str, object], codes: frozenset[str]) -> ReadCall:
    gym = Gym(
        id=uuid4(),
        code="rep",
        name="Report",
        status=Gym.Status.ACTIVE,
        currency="MMK",
        timezone="Asia/Yangon",
    )
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE)
    return ReadCall(
        gym=gym,
        actor=actor,
        payload=payload,
        request_id="req_report",
        ip_address=None,
        user_agent="",
        permission_codes=codes,
    )


def test_financial_report_omits_amounts_without_permission(monkeypatch) -> None:
    def fail(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("financial selector")

    monkeypatch.setattr("apps.assistant.report_reads.deny_trainer_only", lambda **_kwargs: None)
    monkeypatch.setattr("apps.assistant.report_reads.period_report", fail)
    result = get_gym_report(
        _call({"report": "period", "range": "today"}, frozenset({"reports.view"}))
    )
    assert result == {"report": "period", "financial": None, "omitted": "reports.financial"}


def test_period_report_uses_the_gym_and_drops_its_id(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def report(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        gym = kwargs["gym"]
        return {
            "gym": {"id": gym.id, "name": gym.name},
            "source_status_policy": {"revenue": "internal"},
            "range": {"date_from": "2026-11-14", "date_to": "2026-11-14"},
            "rows": [{"date": "2026-11-14", "total_revenue": "10.00"}],
            "totals": {"total_revenue": "10.00"},
        }

    monkeypatch.setattr("apps.assistant.report_reads.deny_trainer_only", lambda **_kwargs: None)
    monkeypatch.setattr("apps.assistant.report_reads.gym_today", lambda _gym: date(2026, 11, 14))
    monkeypatch.setattr("apps.assistant.report_reads.period_report", report)
    call = _call(
        {"report": "period", "date_from": "2026-11-14", "date_to": "2026-11-14"},
        frozenset({"reports.view", "reports.financial"}),
    )
    result = get_gym_report(call)
    assert captured["gym"] is call.gym
    assert captured["date_from"] == date(2026, 11, 14)
    assert "gym" not in result
    assert "source_status_policy" not in result
    assert result["report"] == "period"
    assert result["totals"] == {"total_revenue": "10.00"}


def test_a_report_longer_than_31_days_is_refused(monkeypatch) -> None:
    monkeypatch.setattr("apps.assistant.report_reads.deny_trainer_only", lambda **_kwargs: None)
    monkeypatch.setattr("apps.assistant.report_reads.gym_today", lambda _gym: date(2026, 11, 14))
    with pytest.raises(ExecutionRejected) as refused:
        get_gym_report(
            _call(
                {"report": "check_ins", "date_from": "2026-01-01", "date_to": "2026-02-15"},
                frozenset({"reports.view"}),
            )
        )
    assert refused.value.code == "range_too_long"


def test_trainer_dashboard_uses_the_signed_in_profile(monkeypatch) -> None:
    profile_id = uuid4()
    captured: dict[str, object] = {}

    def own(**kwargs: object) -> object:
        return profile_id

    def dashboard(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "trainer_staff_profile_id": kwargs["trainer_staff_profile_id"],
            "active_assigned_members": 2,
            "sessions": {"booked": 1},
            "upcoming_bookings": [],
        }

    monkeypatch.setattr(
        "apps.assistant.report_reads.require_own_active_trainer_profile_id",
        own,
    )
    monkeypatch.setattr("apps.assistant.report_reads.gym_today", lambda _gym: date(2026, 11, 14))
    monkeypatch.setattr("apps.assistant.report_reads.trainer_dashboard", dashboard)
    call = _call(
        {"view": "summary", "date_from": "2026-11-14", "date_to": "2026-11-14"},
        frozenset({"reports.view"}),
    )
    result = get_trainer_dashboard(call)
    assert captured["trainer_staff_profile_id"] == profile_id
    assert captured["gym"] is call.gym
    assert result["active_assigned_members"] == 2
    assert result["report"] == "trainer"
