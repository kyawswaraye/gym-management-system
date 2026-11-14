from __future__ import annotations

from datetime import date
from uuid import uuid4

from apps.assistant.executor import ReadCall
from apps.assistant.report_reads import get_gym_dashboard
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
