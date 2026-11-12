from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404

from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.membership_reads import (
    membership_read_tool_contracts,
    search_membership_plans,
    search_memberships,
)
from apps.assistant.package_reads import package_read_tool_contracts, search_pt_packages
from apps.assistant.proposals import declaration
from apps.members.models import MembershipPlan
from apps.tenants.models import Gym, GymMembership

_MEMBERSHIP_KEYS = {
    "id",
    "member_id",
    "member_number",
    "plan_id",
    "plan_code",
    "plan_name",
    "start_date",
    "expiry_date",
    "status",
    "payment_status",
    "final_amount",
    "currency",
}
_PACKAGE_KEYS = {
    "id",
    "member_id",
    "member_name",
    "package_code",
    "package_name",
    "package_type",
    "status",
    "granted_sessions",
    "remaining_sessions",
    "valid_from",
    "valid_until",
}
_HIDDEN = {
    "note",
    "cancellation_reason",
    "phone",
    "email",
    "payment_id",
    "created_by_membership_id",
    "amount_snapshot",
}


class _Query:
    def __init__(self, row: SimpleNamespace | None = None) -> None:
        self.row = row
        self.filters: list[dict[str, object]] = []

    def filter(self, **kwargs: object) -> _Query:
        self.filters.append(kwargs)
        return self

    def first(self) -> SimpleNamespace | None:
        wanted = self.filters[-1].get("id") if self.filters else None
        if self.row is not None and wanted == self.row.id:
            return self.row
        return None


def _gym() -> Gym:
    return Gym(id=uuid4(), code="catalog", name="Catalog", status=Gym.Status.ACTIVE)


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE),
        payload=payload,
        request_id="req_catalog_read",
        ip_address=None,
        user_agent="",
    )


def _membership(membership_id: object, member_id: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=membership_id,
        member_id=member_id,
        member=SimpleNamespace(member_number="MEM-000004"),
        plan_id=uuid4(),
        plan_code="M1",
        plan_name="Monthly",
        start_date=date(2026, 11, 1),
        expiry_date=date(2026, 11, 30),
        status="ACTIVE",
        payment_status="PAID",
        final_amount=Decimal("50000.00"),
        currency="MMK",
        note="secret-note",
        cancellation_reason="secret reason",
    )


def test_catalog_reads_are_optional_and_bounded() -> None:
    membership = declaration(membership_read_tool_contracts()["search_memberships"])
    package = declaration(package_read_tool_contracts()["search_pt_packages"])
    membership_parameters = membership["parameters"]
    package_parameters = package["parameters"]
    assert isinstance(membership_parameters, dict)
    assert isinstance(package_parameters, dict)
    assert membership_parameters["required"] == []
    assert package_parameters["required"] == []
    membership_properties = membership_parameters["properties"]
    package_properties = package_parameters["properties"]
    assert isinstance(membership_properties, dict)
    assert isinstance(package_properties, dict)
    assert set(membership_properties) == {
        "membership_id",
        "member_id",
        "status",
        "payment_status",
        "limit",
        "after",
    }
    assert set(package_properties) == {
        "package_id",
        "member_id",
        "status",
        "package_type",
        "limit",
        "after",
    }
    assert membership_read_tool_contracts()["search_memberships"].mode == ExecutionMode.READ
    assert package_read_tool_contracts()["search_pt_packages"].finance_closed is False


def test_search_memberships_returns_stored_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym = _gym()
    member_id = uuid4()
    row = _membership(uuid4(), member_id)
    captured: dict[str, object] = {}

    def page(queryset: object, **kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured["queryset"] = queryset
        captured.update(kwargs)
        return [row], {"has_more": False, "next_after": None}

    visible = _Query(row)
    monkeypatch.setattr(
        "apps.assistant.membership_reads._visible_memberships",
        lambda _call: visible,
    )
    monkeypatch.setattr("apps.assistant.membership_reads.page_memberships", page)
    monkeypatch.setattr(
        "apps.assistant.membership_reads.require_assigned_member",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("apps.assistant.membership_reads.get_object_or_404", lambda *_a, **_k: row)
    result = search_memberships(_call(gym, {"member_id": str(member_id), "status": "ACTIVE"}))
    assert captured["gym_id"] == gym.id
    assert captured["member_id"] == member_id
    assert captured["status"] == "ACTIVE"
    found = result["results"]
    assert isinstance(found, list)
    assert found[0]["status"] == "ACTIVE"
    assert found[0]["expiry_date"] == "2026-11-30"
    assert found[0]["final_amount"] == "50000.00"
    assert set(found[0]) == _MEMBERSHIP_KEYS
    assert _HIDDEN.isdisjoint(found[0])


def test_membership_id_miss_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "apps.assistant.membership_reads._visible_memberships",
        lambda _call: _Query(None),
    )
    with pytest.raises(Http404):
        search_memberships(_call(_gym(), {"membership_id": str(uuid4())}))


def test_search_pt_packages_uses_the_ledger_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    member_id = uuid4()
    package_id = uuid4()
    captured: dict[str, object] = {}

    def page(**kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [SimpleNamespace(id=package_id)], {"has_more": True, "next_after": "cursor"}

    def payload(row: SimpleNamespace) -> dict[str, object]:
        return {
            "id": row.id,
            "member_id": member_id,
            "member_name": "MEM-000004 · Hla Win",
            "package_code": "PT3",
            "package_name": "Three sessions",
            "package_type": "PT",
            "status": "ACTIVE",
            "granted_sessions": 3,
            "remaining_sessions": 2,
            "valid_from": date(2026, 11, 1),
            "valid_until": date(2026, 12, 1),
            "payment_id": uuid4(),
            "amount_snapshot": "100.00",
            "created_by_membership_id": uuid4(),
        }

    monkeypatch.setattr("apps.assistant.package_reads.page_member_packages", page)
    monkeypatch.setattr("apps.assistant.package_reads.member_package_payload", payload)
    result = search_pt_packages(_call(gym, {"member_id": str(member_id), "package_type": "PT"}))
    assert captured["gym"] == gym
    assert captured["member_id"] == member_id
    assert captured["package_type"] == "PT"
    rows = result["results"]
    assert isinstance(rows, list)
    assert rows[0]["remaining_sessions"] == 2
    assert rows[0]["status"] == "ACTIVE"
    assert rows[0]["valid_until"] == "2026-12-01"
    assert result["has_more"] is True
    assert set(rows[0]) == _PACKAGE_KEYS
    assert _HIDDEN.isdisjoint(rows[0])


def test_search_membership_plans_keeps_inactive_plans_and_duplicate_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym = _gym()
    active = SimpleNamespace(
        id=uuid4(),
        code="YOGA",
        name="Yoga",
        is_active=True,
        duration_value=1,
        duration_unit="MONTH",
        price=Decimal("50000.00"),
        currency="THB",
        description="secret-description",
    )
    inactive = SimpleNamespace(
        id=uuid4(),
        code="YOGA_2",
        name="Yoga",
        is_active=False,
        duration_value=1,
        duration_unit="MONTH",
        price=Decimal("50000.00"),
        currency="THB",
        description="hidden-description",
    )

    class _Plans:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []

        def filter(self, **kwargs: object) -> _Plans:
            self.filters.append(kwargs)
            return self

    plans = _Plans()
    captured: dict[str, object] = {}

    def page(queryset: object, **kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured["queryset"] = queryset
        captured.update(kwargs)
        return [active, inactive], {"has_more": False, "next_after": None}

    monkeypatch.setattr(MembershipPlan, "objects", plans)
    monkeypatch.setattr("apps.assistant.membership_reads.page_membership_plans", page)
    result = search_membership_plans(_call(gym, {"name": "Yoga", "limit": 25}))
    rows = result["results"]
    assert isinstance(rows, list)
    assert plans.filters[0]["gym"] == gym
    assert {"name__iexact": "Yoga"} in plans.filters
    assert captured["gym_id"] == gym.id
    assert result["ambiguous"] is True
    assert {row["code"] for row in rows} == {"YOGA", "YOGA_2"}
    assert rows[1]["status"] == "INACTIVE"
    assert "description" not in rows[0]
    assert "secret-description" not in str(result)
    assert "phone" not in rows[0]
    contract = membership_read_tool_contracts()["search_membership_plans"]
    assert contract.mode == ExecutionMode.READ
    assert contract.definition.permissions == ("memberships.view",)


def test_sale_plan_search_drops_inactive_plans(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    active = SimpleNamespace(
        id=uuid4(),
        code="YOGA",
        name="Yoga",
        is_active=True,
        duration_value=1,
        duration_unit="MONTH",
        price=Decimal("50000.00"),
        currency="THB",
        description="secret-description",
    )
    inactive = SimpleNamespace(
        id=uuid4(),
        code="YOGA_2",
        name="Yoga",
        is_active=False,
        duration_value=1,
        duration_unit="MONTH",
        price=Decimal("40000.00"),
        currency="THB",
        description="hidden",
    )

    class _Plans:
        def __init__(self) -> None:
            self.filters: list[dict[str, object]] = []

        def filter(self, **kwargs: object) -> _Plans:
            self.filters.append(kwargs)
            return self

    plans = _Plans()

    def page(queryset: object, **kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        del queryset, kwargs
        return [active, inactive], {"has_more": False, "next_after": None}

    monkeypatch.setattr(MembershipPlan, "objects", plans)
    monkeypatch.setattr("apps.assistant.membership_reads.page_membership_plans", page)
    result = search_membership_plans(
        _call(gym, {"name": "Yoga", "eligible_for_sale": True, "limit": 25})
    )
    rows = result["results"]
    assert isinstance(rows, list)
    assert {"is_active": True} in plans.filters
    assert [row["code"] for row in rows] == ["YOGA"]
    assert result["ambiguous"] is False
