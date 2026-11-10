from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import BOOKING_TOOL_NAMES
from apps.assistant.execution import ExecutionRejected
from apps.assistant.models import Goal
from apps.assistant.pt_tools import (
    ATTEND_BOOKING_ROUTE,
    CANCEL_BOOKING_ROUTE,
    CANCEL_SALE_ROUTE,
    COMMISSION_ROUTE,
    CREATE_BOOKING_ROUTE,
    CREATE_SALE_ROUTE,
    GRANT_ROUTE,
    LEDGER_ROUTE,
    NO_SHOW_BOOKING_ROUTE,
    PT_TOOL_NAMES,
    SUBMIT_SALE_ROUTE,
    UPDATE_SALE_ROUTE,
    VOID_SALE_ROUTE,
    adjust_package_ledger_command,
    approve_pt_sale_command,
    attend_pt_booking_command,
    cancel_pt_booking_command,
    cancel_pt_sale_command,
    create_pt_booking_command,
    create_pt_sale_body,
    create_pt_sale_command,
    execute_registered_tool,
    grant_member_package_command,
    pt_tool_contracts,
    pt_tool_definitions,
    record_pt_commission_payment_command,
    reject_pt_sale_command,
    submit_pt_sale_command,
    update_pt_sale_command,
    void_pt_sale_body,
    void_pt_sale_command,
)
from apps.assistant.registry import production_registry
from apps.assistant.salary_tools import SALARY_TOOL_NAMES
from apps.assistant.training_tools import TRAINING_TOOL_NAMES
from apps.common.finance.idempotency import canonical_request_hash
from apps.tenants.models import Gym, GymMembership
from apps.trainers.pt_commission_services import command_route as commission_command_route
from apps.trainers.pt_sale_services import command_route as sale_command_route
from apps.trainers.serializers import (
    LedgerAdjustSerializer,
    MemberPackageGrantSerializer,
    PTBookingCancelSerializer,
    PTBookingWriteSerializer,
    PTCommissionPaymentWriteSerializer,
    PTSaleCancelSerializer,
    PTSaleUpdateSerializer,
    PTSaleVoidSerializer,
    PTSaleWriteSerializer,
)
from apps.users.models import User

_NAMES = (
    "adjust_package_ledger",
    "approve_pt_sale",
    "attend_pt_booking",
    "cancel_pt_booking",
    "cancel_pt_sale",
    "create_pt_booking",
    "create_pt_sale",
    "grant_member_package",
    "mark_pt_booking_no_show",
    "record_pt_commission_payment",
    "reject_pt_sale",
    "submit_pt_sale",
    "update_pt_sale",
    "void_pt_sale",
)
_CODES = frozenset(
    {
        "pt.sales.create",
        "pt.sales.update",
        "pt.sales.submit",
        "pt.sales.void",
        "pt.commissions.pay",
        "pt.sessions.adjust",
        "pt.schedule.manage",
    }
)


def _ordered_pair() -> tuple[str, str]:
    earlier = uuid4()
    later = uuid4()
    if earlier > later:
        earlier, later = later, earlier
    return str(earlier), str(later)


def _sale_raw() -> dict[str, object]:
    return {
        "member_id": str(uuid4()),
        "trainer_staff_profile_id": str(uuid4()),
        "service_date": "2026-11-10",
        "description": "  Private   session ",
        "gross_fee": "100.00",
        "currency": "THB",
        "payment": {
            "description": "  Session   charge ",
            "amount": "100.00",
            "method": "BANK_TRANSFER",
            "business_date": "2026-11-10",
        },
    }


def _session(*, codes: frozenset[str] | None = None) -> tuple[Request, Goal]:
    gym = Gym(id=uuid4(), code="pt-unit", name="PT", status=Gym.Status.ACTIVE)
    actor = GymMembership(id=uuid4(), gym=gym, status=GymMembership.Status.ACTIVE, is_owner=True)
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = User(email="desk@example.test")
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset() if codes is None else codes
    return request, Goal(id=uuid4(), gym=gym, version=1)


def _assert_same_hash(
    left: dict[str, object],
    right: dict[str, object],
    *,
    method: str,
    route: str,
) -> None:
    gym_id = uuid4()
    actor_id = uuid4()
    assert canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=left,
    ) == canonical_request_hash(
        gym_id=gym_id,
        actor_membership_id=actor_id,
        http_method=method,
        route=route,
        body=right,
    )


def _hashed(command: dict[str, object], *route_keys: str) -> dict[str, object]:
    return {key: value for key, value in command.items() if key not in route_keys}


def test_pt_tools_keep_trainer_finance_open() -> None:
    contracts = pt_tool_contracts()
    definitions = {tool.name: tool for tool in pt_tool_definitions()}
    names = production_registry().names()
    assert tuple(sorted(definitions)) == PT_TOOL_NAMES == _NAMES
    assert "approve_pt_sale" in names
    assert "reject_pt_sale" in names
    assert set(PT_TOOL_NAMES).isdisjoint(SALARY_TOOL_NAMES)
    assert set(PT_TOOL_NAMES).isdisjoint(BOOKING_TOOL_NAMES)
    assert set(PT_TOOL_NAMES).isdisjoint(TRAINING_TOOL_NAMES)
    assert set(PT_TOOL_NAMES) <= set(names)
    created = contracts["create_pt_sale"]
    assert created.http_method == "POST"
    assert created.route == CREATE_SALE_ROUTE == sale_command_route(sale_id=None, action="")
    assert created.route_fields == ()
    assert contracts["update_pt_sale"].http_method == "PATCH"
    assert contracts["update_pt_sale"].route == UPDATE_SALE_ROUTE
    assert contracts["update_pt_sale"].route_fields == ("pt_sale_id",)
    assert contracts["cancel_pt_sale"].route == CANCEL_SALE_ROUTE
    assert contracts["submit_pt_sale"].route == SUBMIT_SALE_ROUTE
    assert contracts["void_pt_sale"].route == VOID_SALE_ROUTE
    assert contracts["record_pt_commission_payment"].route == COMMISSION_ROUTE
    assert contracts["grant_member_package"].route == GRANT_ROUTE
    assert contracts["adjust_package_ledger"].route == LEDGER_ROUTE
    assert contracts["create_pt_booking"].route == CREATE_BOOKING_ROUTE
    assert contracts["attend_pt_booking"].route == ATTEND_BOOKING_ROUTE
    assert contracts["mark_pt_booking_no_show"].route == NO_SHOW_BOOKING_ROUTE
    assert contracts["cancel_pt_booking"].route == CANCEL_BOOKING_ROUTE
    assert created.definition.permissions == ("pt.sales.create",)
    assert contracts["update_pt_sale"].definition.permissions == ("pt.sales.update",)
    assert contracts["cancel_pt_sale"].definition.permissions == ("pt.sales.update",)
    assert contracts["submit_pt_sale"].definition.permissions == ("pt.sales.submit",)
    assert contracts["void_pt_sale"].definition.permissions == ("pt.sales.void",)
    assert contracts["record_pt_commission_payment"].definition.permissions == (
        "pt.commissions.pay",
    )
    assert contracts["grant_member_package"].definition.permissions == ("pt.sessions.adjust",)
    assert contracts["adjust_package_ledger"].definition.permissions == ("pt.sessions.adjust",)
    assert contracts["create_pt_booking"].definition.permissions == ("pt.schedule.manage",)
    for contract in contracts.values():
        assert contract.mode == "finance_idempotent"
        assert contract.finance_closed is False
        assert contract.permission_mode == "all"
        assert contract.definition.features == ("pt",)
        assert contract.definition.operation == "write"
        assert contract.definition.confirmation_required is True
        approval = contract.definition.name in {"approve_pt_sale", "reject_pt_sale"}
        assert contract.definition.approval_required is approval
        assert contract.confirmation_fields == (("record_id", "amount") if approval else ())
        assert contract.definition.external_side_effects == ()
        assert contract.definition.module == "trainers"


def test_finance_bodies_match_the_view_request_hash() -> None:
    created_raw = _sale_raw()
    created_serializer = PTSaleWriteSerializer(data=created_raw)
    created_serializer.is_valid(raise_exception=True)
    created_body = create_pt_sale_body(created_raw)
    assert create_pt_sale_command(created_raw) == created_body
    assert created_body["description"] == "Private   session"
    payment = created_body["payment"]
    assert isinstance(payment, dict)
    assert payment["description"] == "Session charge"
    assert "reference_number" not in payment
    assert "service_package_id" not in created_body
    _assert_same_hash(
        created_body,
        dict(created_serializer.validated_data),
        method="POST",
        route=CREATE_SALE_ROUTE,
    )

    sale_id = uuid4()
    update_raw = {"pt_sale_id": str(sale_id).upper(), "description": "  Desk session  "}
    update_command = update_pt_sale_command(update_raw)
    update_serializer = PTSaleUpdateSerializer(data={"description": "  Desk session  "})
    update_serializer.is_valid(raise_exception=True)
    update_route = sale_command_route(sale_id=sale_id, action="")
    assert update_command["pt_sale_id"] == str(sale_id)
    assert _hashed(update_command, "pt_sale_id") == {"description": "Desk session"}
    _assert_same_hash(
        _hashed(update_command, "pt_sale_id"),
        dict(update_serializer.validated_data),
        method="PATCH",
        route=update_route,
    )

    submit = submit_pt_sale_command({"pt_sale_id": str(sale_id)})
    assert _hashed(submit, "pt_sale_id") == {}
    _assert_same_hash(
        {},
        {},
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="submit"),
    )

    cancel_command = cancel_pt_sale_command({"pt_sale_id": str(sale_id), "reason": "  Duplicate  "})
    cancel_serializer = PTSaleCancelSerializer(data={"reason": "  Duplicate  "})
    cancel_serializer.is_valid(raise_exception=True)
    assert cancel_command["reason"] == "Duplicate"
    _assert_same_hash(
        _hashed(cancel_command, "pt_sale_id"),
        dict(cancel_serializer.validated_data),
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="cancel"),
    )

    earlier, later = _ordered_pair()
    void_raw = {
        "reason": "  Wrong sale  ",
        "business_date": "2026-11-10",
        "payout_reversals": [
            {"commission_payment_id": later, "cash_session_id": None},
            {"commission_payment_id": earlier.upper(), "cash_session_id": None},
        ],
    }
    void_serializer = PTSaleVoidSerializer(data=void_raw)
    void_serializer.is_valid(raise_exception=True)
    void_fields = void_serializer.validated_data
    void_reversals = sorted(
        void_fields["payout_reversals"],
        key=lambda row: str(row["commission_payment_id"]),
    )
    void_view = {
        "reason": void_fields["reason"],
        "business_date": void_fields["business_date"].isoformat(),
        "cash_session_id": None,
        "payout_reversals": [
            {
                "commission_payment_id": str(row["commission_payment_id"]),
                "cash_session_id": None,
            }
            for row in void_reversals
        ],
    }
    void_body = void_pt_sale_body(void_raw)
    void_command = void_pt_sale_command({**void_raw, "pt_sale_id": str(sale_id).upper()})
    stored_reversals = list(void_body["payout_reversals"])
    assert void_body["reason"] == "Wrong sale"
    assert void_body["cash_session_id"] is None
    assert stored_reversals[0]["commission_payment_id"] == earlier
    assert _hashed(void_command, "pt_sale_id") == void_body
    _assert_same_hash(
        void_body,
        void_view,
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="void"),
    )

    commission_id = uuid4()
    commission_raw = {
        "commission_id": str(commission_id).upper(),
        "amount": "10.00",
        "business_date": "2026-11-10",
        "payment_method": "BANK_TRANSFER",
    }
    commission_command = record_pt_commission_payment_command(commission_raw)
    commission_serializer = PTCommissionPaymentWriteSerializer(
        data={
            "amount": "10.00",
            "business_date": "2026-11-10",
            "payment_method": "BANK_TRANSFER",
        }
    )
    commission_serializer.is_valid(raise_exception=True)
    commission_fields = commission_serializer.validated_data
    commission_view = {
        "amount": commission_fields["amount"],
        "business_date": commission_fields["business_date"],
        "payment_method": commission_fields["payment_method"],
        "cash_session_id": commission_fields.get("cash_session_id"),
        "reference_number": commission_fields.get("reference_number", ""),
        "policy_note": commission_fields.get("policy_note", ""),
    }
    commission_body = _hashed(commission_command, "commission_id")
    assert commission_command["commission_id"] == str(commission_id)
    assert commission_body["cash_session_id"] is None
    assert commission_body["reference_number"] == ""
    assert commission_body["policy_note"] == ""
    assert commission_command_route(commission_id=commission_id) == (
        f"/api/v1/pt-commissions/{commission_id}/record-payment/"
    )
    _assert_same_hash(
        commission_body,
        commission_view,
        method="POST",
        route=commission_command_route(commission_id=commission_id),
    )

    grant_raw = {
        "member_id": str(uuid4()),
        "service_package_id": str(uuid4()),
        "business_date": "2026-11-09",
        "complimentary": False,
        "reason": "  Courtesy  ",
    }
    grant_serializer = MemberPackageGrantSerializer(data=grant_raw)
    grant_serializer.is_valid(raise_exception=True)
    grant_command = grant_member_package_command(grant_raw)
    assert grant_command["reason"] == "Courtesy"
    assert grant_command["complimentary"] is False
    _assert_same_hash(
        grant_command,
        dict(grant_serializer.validated_data),
        method="POST",
        route=GRANT_ROUTE,
    )

    package_id = uuid4()
    entry_id = uuid4()
    ledger_raw = {
        "member_service_package_id": str(package_id).upper(),
        "reverses_entry_id": str(entry_id),
        "reason": "  Restore session  ",
    }
    ledger_serializer = LedgerAdjustSerializer(
        data={"reverses_entry_id": str(entry_id), "reason": "  Restore session  "}
    )
    ledger_serializer.is_valid(raise_exception=True)
    ledger_command = adjust_package_ledger_command(ledger_raw)
    assert ledger_command["member_service_package_id"] == str(package_id)
    _assert_same_hash(
        _hashed(ledger_command, "member_service_package_id"),
        dict(ledger_serializer.validated_data),
        method="POST",
        route=f"/api/v1/member-service-packages/{package_id}/ledger/adjust/",
    )

    booking_raw = {
        "trainer_staff_profile_id": str(uuid4()),
        "member_id": str(uuid4()),
        "starts_at": "2026-11-10T09:00:00+07:00",
        "ends_at": "2026-11-10T10:00:00+07:00",
    }
    booking_serializer = PTBookingWriteSerializer(data=booking_raw)
    booking_serializer.is_valid(raise_exception=True)
    booking_command = create_pt_booking_command(booking_raw)
    assert booking_command["unmetered"] is False
    assert booking_command["unmetered_reason"] == ""
    assert "member_service_package_id" not in booking_command
    _assert_same_hash(
        booking_command,
        dict(booking_serializer.validated_data),
        method="POST",
        route=CREATE_BOOKING_ROUTE,
    )

    booking_id = uuid4()
    attend = attend_pt_booking_command({"pt_booking_id": str(booking_id)})
    assert _hashed(attend, "pt_booking_id") == {}
    cancel_booking = cancel_pt_booking_command({"pt_booking_id": str(booking_id).upper()})
    cancel_booking_serializer = PTBookingCancelSerializer(data={})
    cancel_booking_serializer.is_valid(raise_exception=True)
    assert cancel_booking["pt_booking_id"] == str(booking_id)
    assert cancel_booking["reason"] == ""
    _assert_same_hash(
        _hashed(cancel_booking, "pt_booking_id"),
        dict(cancel_booking_serializer.validated_data),
        method="POST",
        route=f"/api/v1/pt-bookings/{booking_id}/cancel/",
    )


def test_permission_and_gym_stop_before_a_pt_service() -> None:
    request, goal = _session()
    sale_id = str(uuid4())
    payloads = (
        ("create_pt_sale", _sale_raw()),
        ("update_pt_sale", {"pt_sale_id": sale_id, "description": "Desk session"}),
        ("submit_pt_sale", {"pt_sale_id": sale_id}),
        ("cancel_pt_sale", {"pt_sale_id": sale_id, "reason": "Duplicate"}),
        (
            "void_pt_sale",
            {
                "pt_sale_id": sale_id,
                "reason": "Wrong sale",
                "business_date": "2026-11-10",
                "payout_reversals": [],
            },
        ),
        (
            "record_pt_commission_payment",
            {
                "commission_id": str(uuid4()),
                "amount": "10.00",
                "business_date": "2026-11-10",
                "payment_method": "BANK_TRANSFER",
            },
        ),
        (
            "grant_member_package",
            {
                "member_id": str(uuid4()),
                "service_package_id": str(uuid4()),
                "business_date": "2026-11-09",
                "complimentary": True,
                "reason": "Courtesy",
            },
        ),
        (
            "create_pt_booking",
            {
                "trainer_staff_profile_id": str(uuid4()),
                "member_id": str(uuid4()),
                "starts_at": "2026-11-10T09:00:00+07:00",
                "ends_at": "2026-11-10T10:00:00+07:00",
            },
        ),
        ("attend_pt_booking", {"pt_booking_id": str(uuid4())}),
    )
    for tool_name, payload in payloads:
        with pytest.raises(PermissionDenied):
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload=payload,
                confirmed=True,
            )
    request.permission_codes = _CODES
    for tool_name, payload in payloads:
        with pytest.raises(ExecutionRejected) as mismatched:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={**payload, "gym_id": str(uuid4())},
                confirmed=True,
            )
        assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ValidationError):
        execute_registered_tool(
            request=request,
            view=APIView(),
            tool_name="create_pt_sale",
            goal=goal,
            step_id=uuid4(),
            expected_version=1,
            payload={**_sale_raw(), "gross_fee": "100.001"},
            confirmed=True,
        )
    for tool_name in ("print_pt_sale", "download_pt_sale"):
        with pytest.raises(ExecutionRejected) as unknown:
            execute_registered_tool(
                request=request,
                view=APIView(),
                tool_name=tool_name,
                goal=goal,
                step_id=uuid4(),
                expected_version=1,
                payload={"pt_sale_id": sale_id},
                confirmed=True,
            )
        assert unknown.value.code == "unknown_tool"


def test_approval_confirmation_stays_out_of_the_view_hash() -> None:
    sale_id = uuid4()
    session_id = uuid4()
    approved = approve_pt_sale_command(
        {
            "pt_sale_id": str(sale_id).upper(),
            "cash_session_id": str(session_id),
            "record_id": str(sale_id),
            "amount": "100.0",
            "confirmed": True,
        }
    )
    assert approved["pt_sale_id"] == str(sale_id)
    assert approved["amount"] == "100.0"
    assert "confirmed" not in approved
    _assert_same_hash(
        _hashed(approved, "pt_sale_id", "record_id", "amount"),
        {"cash_session_id": session_id},
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="approve"),
    )
    omitted = approve_pt_sale_command(
        {"pt_sale_id": str(sale_id), "record_id": str(sale_id), "amount": "100.00"}
    )
    _assert_same_hash(
        _hashed(omitted, "pt_sale_id", "record_id", "amount"),
        {"cash_session_id": None},
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="approve"),
    )
    rejected = reject_pt_sale_command(
        {
            "pt_sale_id": str(sale_id),
            "rejection_reason": "  Duplicate  ",
            "record_id": str(sale_id),
            "amount": "100.00",
            "confirmed": True,
        }
    )
    assert "pt_sale_id" in rejected
    _assert_same_hash(
        _hashed(rejected, "pt_sale_id", "record_id", "amount"),
        {"rejection_reason": "Duplicate"},
        method="POST",
        route=sale_command_route(sale_id=sale_id, action="reject"),
    )
