"""PT commission reads stay on stored values and existing filters."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.assistant.commission_reads import (
    commission_read_tool_contracts,
    search_pt_commissions,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.proposals import declaration
from apps.tenants.models import Gym, GymMembership

_COMMISSION_KEYS = {
    "id",
    "pt_sale_id",
    "service_date",
    "trainer_staff_profile_id",
    "staff_code",
    "display_name",
    "commission_percent_snapshot",
    "commission_amount",
    "paid_amount",
    "outstanding_amount",
    "currency",
    "status",
    "paid_at",
    "payments",
}
_PAYMENT_KEYS = {
    "id",
    "amount",
    "business_date",
    "payment_method",
    "status",
    "expense_id",
    "cash_session_id",
}
_HIDDEN = {
    "policy_note",
    "void_reason",
    "reference_number",
    "phone",
    "created_by_membership_id",
    "gross_fee_snapshot",
    "gym_share",
}


def _gym() -> Gym:
    return Gym(
        id=uuid4(),
        code="com",
        name="Commission",
        status=Gym.Status.ACTIVE,
        timezone="Asia/Yangon",
        currency="MMK",
    )


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE),
        payload=payload,
        request_id="req_commission_read",
        ip_address=None,
        user_agent="",
        permission_codes=frozenset({"pt.commissions.view"}),
    )


def _commission() -> SimpleNamespace:
    profile_id = uuid4()
    payment = SimpleNamespace(
        id=uuid4(),
        amount=Decimal("1.00"),
        business_date=date(2026, 11, 11),
        payment_method="BANK_TRANSFER",
        status="POSTED",
        expense_id=uuid4(),
        cash_session_id=None,
        policy_note="secret-policy",
        void_reason="secret-void",
        reference_number="ACCT-19",
        created_by_membership_id=uuid4(),
    )
    return SimpleNamespace(
        id=uuid4(),
        pt_sale_id=uuid4(),
        pt_sale=SimpleNamespace(service_date=date(2026, 11, 10)),
        trainer_staff_profile_id=profile_id,
        trainer_staff_profile=SimpleNamespace(
            id=profile_id,
            staff_code="ADA",
            display_name="Ada Coach",
            phone="+959000000099",
        ),
        commission_percent_snapshot=Decimal("33.33"),
        commission_amount=Decimal("3.33"),
        paid_amount=Decimal("9.00"),
        outstanding_amount=Decimal("2.33"),
        gross_fee_snapshot=Decimal("10.00"),
        gym_share=Decimal("6.67"),
        currency="MMK",
        status="PARTIALLY_PAID",
        paid_at=None,
        created_by_membership_id=uuid4(),
        payment_rows=[payment],
    )


def test_commission_read_advertises_only_supported_filters() -> None:
    declared = declaration(commission_read_tool_contracts()["search_pt_commissions"])
    parameters = declared["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["required"] == []
    properties = parameters["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {
        "commission_id",
        "status",
        "trainer_staff_profile_id",
        "service_date_from",
        "service_date_to",
        "limit",
        "after",
    }
    assert "business_date" not in properties
    assert "entry_type" not in properties
    contract = commission_read_tool_contracts()["search_pt_commissions"]
    assert contract.mode == ExecutionMode.READ
    assert contract.finance_closed is False
    assert contract.definition.permissions == ("pt.commissions.view",)
    assert contract.definition.features == ("pt",)
    assert contract.definition.module == "trainers"
    assert contract.definition.confirmation_required is False
    assert contract.definition.operation == "read"


def test_search_pt_commissions_returns_stored_payouts(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    row = _commission()
    captured: dict[str, object] = {}
    other_gym = uuid4()

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [row], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.commission_reads.page_pt_commissions", page)
    monkeypatch.setattr("apps.assistant.commission_reads._detailed", lambda _gym, rows: rows)
    monkeypatch.setattr(
        "apps.assistant.commission_reads.commission_payments",
        lambda item: item.payment_rows,
    )
    call = _call(
        gym,
        {
            "gym_id": str(other_gym),
            "status": "PARTIALLY_PAID",
            "service_date_from": "2026-11-10",
            "service_date_to": "2026-11-10",
            "trainer_staff_profile_id": str(row.trainer_staff_profile_id),
            "limit": 2,
        },
    )
    result = search_pt_commissions(call)
    assert captured["gym"] is gym
    assert captured["actor"] is call.actor
    assert captured["status"] == "PARTIALLY_PAID"
    assert captured["service_date_from"] == date(2026, 11, 10)
    assert captured["service_date_to"] == date(2026, 11, 10)
    assert captured["limit"] == 2
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["status"] == "PARTIALLY_PAID"
    assert found[0]["service_date"] == "2026-11-10"
    assert found[0]["paid_amount"] == "9.00"
    assert found[0]["outstanding_amount"] == "2.33"
    assert found[0]["commission_amount"] == "3.33"
    assert found[0]["staff_code"] == "ADA"
    payment = found[0]["payments"][0]
    assert payment["amount"] == "1.00"
    assert payment["business_date"] == "2026-11-11"
    assert payment["status"] == "POSTED"
    assert payment["cash_session_id"] is None
    assert set(found[0]) == _COMMISSION_KEYS
    assert set(payment) == _PAYMENT_KEYS
    assert _HIDDEN.isdisjoint(found[0])
    assert _HIDDEN.isdisjoint(payment)
    assert "secret-policy" not in str(found)
    assert "ACCT-19" not in str(found)
    assert "+959000000099" not in str(found)


def test_commission_view_requires_the_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_kwargs: object) -> tuple[list[object], dict[str, object]]:
        raise AssertionError("selector ran")

    monkeypatch.setattr("apps.assistant.commission_reads.page_pt_commissions", fail)
    call = _call(_gym(), {})
    denied = ReadCall(
        gym=call.gym,
        actor=call.actor,
        payload=call.payload,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
        permission_codes=frozenset(),
    )
    with pytest.raises(PermissionDenied):
        search_pt_commissions(denied)


def test_commission_filters_reject_unknown_values() -> None:
    gym = _gym()
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"status": "SLEEPING"}))
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"status": "SUBMITTED"}))
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"commission_id": "not-a-commission"}))
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"business_date": "2026-11-10"}))
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"service_date_from": "yesterday"}))
    with pytest.raises(ValidationError):
        search_pt_commissions(
            _call(gym, {"service_date_from": "2026-11-11", "service_date_to": "2026-11-10"})
        )
    with pytest.raises(ValidationError):
        search_pt_commissions(_call(gym, {"limit": 26}))


def test_commission_status_values_stay_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[object], dict[str, object]]:
        captured.update(kwargs)
        return [], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.commission_reads.page_pt_commissions", page)
    for status in ("ACCRUED", "PAID", "VOID"):
        result = search_pt_commissions(_call(_gym(), {"status": status}))
        assert captured["status"] == status
        assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_commission_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(**_kwargs: object) -> SimpleNamespace:
        raise Http404

    monkeypatch.setattr("apps.assistant.commission_reads.get_visible_commission", missing)
    with pytest.raises(Http404):
        search_pt_commissions(_call(_gym(), {"commission_id": str(uuid4())}))


def test_commission_page_can_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.commission_reads.page_pt_commissions",
        lambda **_kwargs: ([], {"has_more": False, "next_after": None}),
    )
    result = search_pt_commissions(_call(_gym(), {"service_date_from": "2020-01-01"}))
    assert result == {"results": [], "has_more": False, "next_after": None}


def test_missing_commission_amount_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _commission()
    row.commission_amount = None
    monkeypatch.setattr(
        "apps.assistant.commission_reads.page_pt_commissions",
        lambda **_kwargs: ([row], {"has_more": False, "next_after": None}),
    )
    monkeypatch.setattr("apps.assistant.commission_reads._detailed", lambda _gym, rows: rows)
    with pytest.raises(ExecutionRejected) as missing:
        search_pt_commissions(_call(_gym(), {}))
    assert missing.value.code == "read_result"
