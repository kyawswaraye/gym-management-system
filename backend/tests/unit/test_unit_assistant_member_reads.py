from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.http import Http404

from apps.assistant.dispatch import tool_contracts
from apps.assistant.executor import ExecutionMode, ReadCall
from apps.assistant.member_reads import (
    MEMBER_READ_TOOL_NAMES,
    get_member,
    member_read_tool_contracts,
    search_members,
)
from apps.assistant.member_tools import MEMBER_TOOL_NAMES
from apps.assistant.prerequisites import missing_fields
from apps.assistant.proposals import declaration
from apps.assistant.registry import closed_production_names, production_registry
from apps.tenants.models import Gym, GymMembership

_ROW_KEYS = {
    "id",
    "member_number",
    "first_name",
    "last_name",
    "status",
    "join_date",
    "is_pt_client",
}
_HIDDEN = {
    "phone",
    "email",
    "address",
    "date_of_birth",
    "notes",
    "emergency_contact_name",
    "emergency_contact_phone",
    "linked_user_id",
    "authz_version",
    "normalized_full_name",
}


class _Query:
    def __init__(self, row: SimpleNamespace | None = None) -> None:
        self.filters: list[dict[str, object]] = []
        self.row = row

    def filter(self, **kwargs: object) -> _Query:
        self.filters.append(kwargs)
        return self

    def first(self) -> SimpleNamespace | None:
        if not self.filters:
            return self.row
        wanted = self.filters[-1].get("id")
        if self.row is not None and wanted == self.row.id:
            return self.row
        return None


def _gym() -> Gym:
    return Gym(id=uuid4(), code="mreads", name="Reads", status=Gym.Status.ACTIVE, currency="MMK")


def _actor(gym: Gym) -> GymMembership:
    return GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE)


def _call(gym: Gym, payload: dict[str, object]) -> ReadCall:
    return ReadCall(
        gym=gym,
        actor=_actor(gym),
        payload=payload,
        request_id="req_member_read",
        ip_address=None,
        user_agent="",
        permission_codes=frozenset({"members.view"}),
    )


def _member(
    member_id: object,
    *,
    first_name: str = "Aung",
    last_name: str = "Aung",
    number: str = "MEM-000004",
    normalized: str = "aung aung",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=member_id,
        member_number=number,
        first_name=first_name,
        last_name=last_name,
        normalized_full_name=normalized,
        status="ACTIVE",
        join_date=date(2026, 11, 1),
        pt_client_badge=False,
        phone="+959111111111",
        email="secret@example.test",
        address={"line_1": "secret street"},
        date_of_birth=date(1990, 1, 1),
        notes="secret-note",
        emergency_contact_name="Secret",
        emergency_contact_phone="+959222222222",
        linked_user_id=uuid4(),
        authz_version=4,
    )


def test_member_reads_register_without_changing_write_tools() -> None:
    names = production_registry().names()
    assert names == closed_production_names()
    assert set(MEMBER_READ_TOOL_NAMES) <= set(names)
    assert set(MEMBER_READ_TOOL_NAMES).isdisjoint(MEMBER_TOOL_NAMES)
    assert set(MEMBER_READ_TOOL_NAMES) <= set(tool_contracts())
    writes = [name for name in names if production_registry().get(name).operation == "write"]
    reads = [name for name in names if production_registry().get(name).operation == "read"]
    assert len(writes) == 98
    assert set(reads) == {
        "get_member",
        "get_payment",
        "search_members",
        "search_memberships",
        "search_membership_plans",
        "search_payments",
        "search_pt_packages",
        "search_class_occurrences",
        "search_bookings",
        "search_sales",
        "search_expenses",
        "search_salary_periods",
        "search_cash_sessions",
        "search_pt_commissions",
        "search_announcements",
        "get_gym_dashboard",
        "search_membership_expiries",
        "search_service_packages",
        "search_class_types",
        "search_class_schedules",
        "search_check_ins",
        "search_products",
        "search_stock_movements",
        "search_low_stock",
        "search_product_sales",
        "search_pt_sales",
        "search_pt_bookings",
        "search_pt_trainer_sales",
        "search_trainers",
        "search_trainer_assignments",
        "search_staff",
        "search_roles",
        "search_permissions",
        "search_staff_access",
    }
    for name in MEMBER_READ_TOOL_NAMES:
        tool = production_registry().get(name)
        contract = member_read_tool_contracts()[name]
        assert tool.operation == "read"
        assert tool.module == "members"
        assert tool.permissions == ("members.view",)
        assert tool.features == ("members",)
        assert tool.confirmation_required is False
        assert tool.approval_required is False
        assert tool.external_side_effects == ()
        assert contract.mode == ExecutionMode.READ
        assert contract.finance_closed is False
        assert name not in MEMBER_TOOL_NAMES


def test_member_read_schema_advertises_only_supported_fields() -> None:
    search = declaration(member_read_tool_contracts()["search_members"])
    search_parameters = search["parameters"]
    assert isinstance(search_parameters, dict)
    properties = search_parameters["properties"]
    assert isinstance(properties, dict)
    assert search_parameters["required"] == []
    assert set(properties) == {"search", "member_number", "status", "limit", "after"}
    assert properties["limit"]["type"] == "integer"
    assert properties["search"]["type"] == "string"
    detail = declaration(member_read_tool_contracts()["get_member"])
    detail_parameters = detail["parameters"]
    assert isinstance(detail_parameters, dict)
    assert detail_parameters["required"] == ["member_id"]
    assert missing_fields(member_read_tool_contracts()["search_members"].prerequisite, {}) == ()
    assert missing_fields(member_read_tool_contracts()["get_member"].prerequisite, {}) == (
        "member_id",
    )


def test_search_returns_every_name_match_without_contact_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym = _gym()
    call = _call(gym, {"gym_id": str(gym.id), "search": "Aung", "limit": 25})
    first = _member(uuid4(), number="MEM-000004")
    second = _member(uuid4(), number="MEM-000009")
    captured: dict[str, object] = {}
    visible = _Query()

    def page(queryset: object, **kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured["queryset"] = queryset
        captured.update(kwargs)
        return [first, second], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.member_reads._visible_members", lambda _call: visible)
    monkeypatch.setattr("apps.assistant.member_reads.page_members", page)
    monkeypatch.setattr("apps.assistant.member_reads.gym_today", lambda _gym: date(2026, 11, 11))
    result = search_members(call)
    assert captured["gym_id"] == gym.id
    assert captured["search"] == "Aung"
    assert captured["limit"] == 25
    assert captured["queryset"] is visible
    rows = result["results"]
    assert isinstance(rows, list)
    assert [row["id"] for row in rows] == [str(first.id), str(second.id)]
    assert [row["member_number"] for row in rows] == ["MEM-000004", "MEM-000009"]
    assert result["ambiguous"] is True
    assert _HIDDEN.isdisjoint(rows[0])
    assert set(rows[0]) == _ROW_KEYS


def test_exact_member_number_and_burmese_digits_narrow_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym = _gym()
    visible = _Query()
    captured: dict[str, object] = {}

    def page(queryset: object, **kwargs: object) -> tuple[list[SimpleNamespace], dict[str, object]]:
        captured.update(kwargs)
        return [_member(uuid4())], {"has_more": False, "next_after": None}

    monkeypatch.setattr("apps.assistant.member_reads._visible_members", lambda _call: visible)
    monkeypatch.setattr("apps.assistant.member_reads.page_members", page)
    monkeypatch.setattr("apps.assistant.member_reads.gym_today", lambda _gym: date(2026, 11, 11))
    result = search_members(_call(gym, {"member_number": "MEM-၀၀၀၀၀၄", "search": "Aung Aung"}))
    assert visible.filters == [{"member_number": "MEM-000004"}]
    assert captured["search"] == "Aung Aung"
    assert result["ambiguous"] is False


def test_get_member_returns_one_operational_row(monkeypatch: pytest.MonkeyPatch) -> None:
    gym = _gym()
    member_id = uuid4()
    row = _member(member_id, number="MEM-000004")
    monkeypatch.setattr(
        "apps.assistant.member_reads._visible_members",
        lambda _call: _Query(row),
    )
    result = get_member(_call(gym, {"member_id": str(member_id)}))
    assert result["id"] == str(member_id)
    assert result["member_number"] == "MEM-000004"
    assert result["is_pt_client"] is False
    assert set(result) == _ROW_KEYS
    assert _HIDDEN.isdisjoint(result)


def test_get_member_hides_an_invalid_or_invisible_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_call: ReadCall) -> _Query:
        raise AssertionError("selector ran")

    monkeypatch.setattr("apps.assistant.member_reads._visible_members", fail)
    with pytest.raises(Http404):
        get_member(_call(_gym(), {"member_id": "not-a-member"}))

    monkeypatch.setattr(
        "apps.assistant.member_reads._visible_members",
        lambda _call: _Query(None),
    )
    with pytest.raises(Http404):
        get_member(_call(_gym(), {"member_id": str(uuid4())}))
