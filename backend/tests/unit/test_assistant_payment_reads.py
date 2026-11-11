from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404

from apps.assistant.executor import ApprovalSnapshot, ExecutionMode, ReadCall
from apps.assistant.payment_reads import (
    PAYMENT_READ_TOOL_NAMES,
    get_payment,
    payment_read_tool_contracts,
    search_payments,
)
from apps.assistant.payment_tools import PAYMENT_TOOL_NAMES, payment_tool_contracts
from apps.assistant.registry import closed_production_names, production_registry
from apps.tenants.models import Gym, GymMembership

_SEARCH_KEYS = {
    "id",
    "status",
    "amount",
    "currency",
    "method",
    "purpose",
    "description",
    "business_date",
    "member_id",
}
_DETAIL_KEYS = {
    "record_id",
    "amount",
    "status",
    "currency",
    "method",
    "purpose",
    "description",
    "business_date",
    "member_id",
}
_HIDDEN = {
    "phone",
    "email",
    "contact",
    "member",
    "receipt",
    "receipt_body",
    "receipt_html",
    "reference_number",
}


def _gym() -> Gym:
    return Gym(id=uuid4(), code="reads", name="Reads", status=Gym.Status.ACTIVE, currency="MMK")


def _actor(gym: Gym) -> GymMembership:
    return GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE)


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=_actor(gym),
        payload=payload,
        request_id="req_payment_read",
        ip_address=None,
        user_agent="",
        permission_codes=frozenset({"payments.view"}),
    )


def _payment(payment_id: object, amount: str = "99.00") -> SimpleNamespace:
    return SimpleNamespace(
        id=payment_id,
        status="DRAFT",
        amount=Decimal(amount),
        currency="MMK",
        method="CASH",
        purpose="OTHER",
        description="Day pass",
        business_date=date(2026, 11, 10),
        member_id=None,
        phone="09123456789",
        email="member@example.test",
        reference_number="secret-ref",
        receipt_body="<receipt>secret</receipt>",
    )


def test_payment_reads_register_without_changing_approval_tools() -> None:
    names = production_registry().names()
    assert names == closed_production_names()
    assert set(PAYMENT_READ_TOOL_NAMES) <= set(names)
    assert set(PAYMENT_READ_TOOL_NAMES).isdisjoint(PAYMENT_TOOL_NAMES)
    for name in PAYMENT_READ_TOOL_NAMES:
        tool = production_registry().get(name)
        contract = payment_read_tool_contracts()[name]
        assert tool.operation == "read"
        assert tool.module == "payments"
        assert tool.permissions == ("payments.view",)
        assert tool.features == ("finance",)
        assert tool.confirmation_required is False
        assert tool.approval_required is False
        assert tool.external_side_effects == ()
        assert contract.mode == ExecutionMode.READ
        assert contract.finance_closed is True
    approve = payment_tool_contracts()["approve_payment"]
    reject = payment_tool_contracts()["reject_payment"]
    assert approve.definition.operation == "write"
    assert approve.definition.approval_required is True
    assert approve.definition.permissions == ("payments.approve",)
    assert approve.confirmation_fields == ("record_id", "amount")
    assert approve.mode == ExecutionMode.FINANCE_IDEMPOTENT
    assert reject.definition.operation == "write"
    assert reject.definition.approval_required is True
    assert reject.definition.permissions == ("payments.approve",)
    assert reject.confirmation_fields == ("record_id", "amount")
    assert "search_payments" not in payment_tool_contracts()
    assert "get_payment" not in payment_tool_contracts()


def test_search_uses_the_session_and_omits_contact_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    call = _call(gym, {"gym_id": str(uuid4()), "status": "DRAFT", "limit": 25})
    visible = _payment(uuid4(), "40.00")
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [visible], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.payment_reads.page_payments", page)
    result = search_payments(call)
    assert captured["gym_id"] == gym.id
    assert captured["actor_membership_id"] == call.actor.id
    assert captured["status"] == "DRAFT"
    assert captured["limit"] == 25
    rows = result["results"]
    assert isinstance(rows, list)
    assert rows == [
        {
            "id": str(visible.id),
            "status": "DRAFT",
            "amount": "40.00",
            "currency": "MMK",
            "method": "CASH",
            "purpose": "OTHER",
            "description": "Day pass",
            "business_date": "2026-11-10",
            "member_id": None,
        }
    ]
    assert set(rows[0]) == _SEARCH_KEYS
    assert _HIDDEN.isdisjoint(rows[0])


def test_get_payment_returns_the_snapshot_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    payment_id = uuid4()
    row = _payment(payment_id, "99.00")
    monkeypatch.setattr("apps.assistant.payment_reads.load_payment", lambda **_kwargs: row)
    monkeypatch.setattr(
        "apps.assistant.payment_reads.payment_snapshot",
        lambda _gym_arg, raw_id: ApprovalSnapshot(record_id=str(raw_id), amount="10.50"),
    )
    result = get_payment(_call(gym, {"payment_id": str(payment_id)}))
    assert result["record_id"] == str(payment_id)
    assert result["amount"] == "10.50"
    assert set(result) == _DETAIL_KEYS
    assert _HIDDEN.isdisjoint(result)


def test_get_payment_hides_an_invalid_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_kwargs: object) -> SimpleNamespace:
        raise AssertionError("selector ran")

    monkeypatch.setattr("apps.assistant.payment_reads.load_payment", fail)
    with pytest.raises(Http404):
        get_payment(_call(_gym(), {"payment_id": "not-a-payment"}))
