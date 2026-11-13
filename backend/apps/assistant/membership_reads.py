"""Membership reads through the existing membership list and trainer scope.

The session gym and actor are the only scope. Status and dates are the stored
values. These tools do not confirm, approve, or write an execution claim.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode, ReadCall, ToolContract, execute_read_tool
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.models import Member, Membership, MembershipPlan
from apps.members.payment_serializers import money_text
from apps.members.services import (
    gym_today,
    page_membership_expiry,
    page_membership_plans,
    page_memberships,
)
from apps.trainers.selectors import require_assigned_member, scope_to_assigned_members
from apps.users.serializers import StrictSerializer

SEARCH_MEMBERSHIPS = "search_memberships"
SEARCH_MEMBERSHIP_PLANS = "search_membership_plans"
SEARCH_MEMBERSHIP_EXPIRIES = "search_membership_expiries"
MEMBERSHIP_READ_TOOL_NAMES = (
    SEARCH_MEMBERSHIPS,
    SEARCH_MEMBERSHIP_PLANS,
    SEARCH_MEMBERSHIP_EXPIRIES,
)
_EXPIRY_BUCKETS = ("today", "expired", "within_3_days", "within_7_days", "within_30_days")


class MembershipReadQuerySerializer(StrictSerializer):
    membership_id = serializers.UUIDField(required=False)
    member_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=Membership.Status.choices, required=False)
    payment_status = serializers.ChoiceField(
        choices=Membership.PaymentStatus.choices,
        required=False,
    )
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


class MembershipExpiryQuerySerializer(StrictSerializer):
    bucket = serializers.ChoiceField(
        choices=[(item, item) for item in _EXPIRY_BUCKETS],
        required=False,
    )
    expiry_from = serializers.DateField(required=False)
    expiry_to = serializers.DateField(required=False)
    member_id = serializers.UUIDField(required=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


class MembershipPlanReadQuerySerializer(StrictSerializer):
    code = serializers.CharField(required=False, allow_blank=True, max_length=32)
    name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    eligible_for_sale = serializers.BooleanField(required=False, default=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=25, default=25)
    after = serializers.CharField(required=False, allow_blank=True, max_length=2048)


def membership_read_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.definition for tool in _READS.values())


def membership_read_tool_contracts() -> dict[str, ToolContract]:
    return {name: tool for name, tool in _READS.items()}


def execute_registered_read(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    payload: Mapping[str, object],
) -> Mapping[str, object]:
    """Run one registered membership read. The payload does not choose the gym."""

    contract = _READS.get(tool_name)
    if contract is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_read_tool(
        request=request,
        view=view,
        contract=contract,
        payload=payload,
        read=_READERS[tool_name],
    )


def search_memberships(call: ReadCall) -> dict[str, object]:
    """Return memberships this actor can see in the session gym."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _query(call.payload)
    member_id = query.get("member_id")
    if isinstance(member_id, UUID):
        require_assigned_member(
            gym_id=call.gym.id,
            actor=call.actor,
            member_id=member_id,
            as_of=gym_today(call.gym),
        )
        get_object_or_404(Member.objects.filter(gym=call.gym), id=member_id)
    queryset = _visible_memberships(call)
    membership_id = query.get("membership_id")
    if isinstance(membership_id, UUID):
        row = queryset.filter(id=membership_id).first()
        if row is None or (isinstance(member_id, UUID) and row.member_id != member_id):
            raise Http404
        if query.get("status") and row.status != query["status"]:
            raise Http404
        if query.get("payment_status") and row.payment_status != query["payment_status"]:
            raise Http404
        return {"results": [_row(row)], "has_more": False, "next_after": None}
    rows, meta = page_memberships(
        queryset,
        gym_id=call.gym.id,
        status=str(query.get("status", "")),
        member_id=member_id if isinstance(member_id, UUID) else None,
        payment_status=str(query.get("payment_status", "")),
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    return {
        "results": [_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_membership_expiries(call: ReadCall) -> dict[str, object]:
    """Return memberships by expiry date. A member can have more than one row."""

    if call.gym.id is None or call.actor.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    serializer = MembershipExpiryQuerySerializer(data=_without_gym(call.payload))
    serializer.is_valid(raise_exception=True)
    query = serializer.validated_data
    bucket = str(query.get("bucket") or "")
    start = query.get("expiry_from")
    end = query.get("expiry_to")
    if not bucket and not isinstance(start, date) and not isinstance(end, date):
        raise ExecutionRejected("Choose an expiry bucket or a date range.", code="missing_fields")
    queryset = _visible_memberships(call)
    member_id = query.get("member_id")
    if isinstance(member_id, UUID):
        queryset = queryset.filter(member_id=member_id)
    if bucket:
        rows, meta = page_membership_expiry(
            queryset,
            gym_id=call.gym.id,
            bucket=bucket,
            today=gym_today(call.gym),
            limit=int(query["limit"]),
            after=str(query.get("after") or ""),
        )
    else:
        if isinstance(start, date):
            queryset = queryset.filter(expiry_date__gte=start)
        if isinstance(end, date):
            queryset = queryset.filter(expiry_date__lte=end)
        rows = list(queryset.order_by("expiry_date", "id")[: int(query["limit"])])
        meta = {"has_more": False, "next_after": None}
    seen: dict[str, int] = {}
    rendered = [_expiry_row(row) for row in rows]
    for item in rendered:
        key = str(item["member_id"])
        seen[key] = seen.get(key, 0) + 1
    for item in rendered:
        if seen[str(item["member_id"])] > 1:
            item["duplicate_member"] = "yes"
    return {
        "results": rendered,
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
    }


def search_membership_plans(call: ReadCall) -> dict[str, object]:
    """Return this gym's plan catalog. A sale lists active plans only."""

    if call.gym.id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    query = _plan_query(call.payload)
    code = str(query.get("code") or "").strip()
    name = str(query.get("name") or "").strip()
    eligible = bool(query.get("eligible_for_sale"))
    queryset = MembershipPlan.objects.filter(gym=call.gym)
    if eligible:
        queryset = queryset.filter(is_active=True)
    if code:
        queryset = queryset.filter(code__iexact=code)
    if name:
        queryset = queryset.filter(name__iexact=name)
    rows, meta = page_membership_plans(
        queryset,
        gym_id=call.gym.id,
        limit=int(query["limit"]),
        after=str(query.get("after") or ""),
    )
    if eligible:
        rows = [row for row in rows if row.is_active]
    return {
        "results": [_plan_row(row) for row in rows],
        "has_more": bool(meta["has_more"]),
        "next_after": meta["next_after"],
        "ambiguous": bool(name) and len(rows) > 1,
    }


def _definition(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="memberships",
        operation="read",
        permissions=("memberships.view",),
        features=("memberships",),
    )


def _contract(definition: ToolDefinition, fields: tuple[FieldRequirement, ...]) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.READ,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _visible_memberships(call: ReadCall) -> Any:
    gym_id = call.gym.id
    if gym_id is None:
        raise ExecutionRejected("The assistant session is incomplete.", code="session")
    return scope_to_assigned_members(
        Membership.objects.filter(gym=call.gym).select_related("member"),
        gym_id=gym_id,
        actor=call.actor,
        as_of=gym_today(call.gym),
        member_ref="member_id",
    )


def _query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = MembershipReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _plan_query(payload: Mapping[str, object]) -> dict[str, Any]:
    serializer = MembershipPlanReadQuerySerializer(data=_without_gym(payload))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _plan_row(row: MembershipPlan) -> dict[str, object]:
    return {
        "id": str(row.id),
        "code": row.code,
        "name": row.name,
        "status": "ACTIVE" if row.is_active else "INACTIVE",
        "duration_value": row.duration_value,
        "duration_unit": row.duration_unit,
        "price": money_text(row.price),
        "currency": row.currency,
    }


def _expiry_row(row: Membership) -> dict[str, object]:
    item = _row(row)
    item["member_name"] = f"{row.member.first_name} {row.member.last_name}".strip()
    return item


def _row(row: Membership) -> dict[str, object]:
    return {
        "id": str(row.id),
        "member_id": str(row.member_id),
        "member_number": row.member.member_number,
        "plan_id": str(row.plan_id),
        "plan_code": row.plan_code,
        "plan_name": row.plan_name,
        "start_date": _date_text(row.start_date),
        "expiry_date": _date_text(row.expiry_date),
        "status": row.status,
        "payment_status": row.payment_status,
        "final_amount": money_text(row.final_amount),
        "currency": row.currency,
    }


def _date_text(value: date) -> str:
    return value.isoformat()


def _without_gym(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "gym_id"}


_READS = {
    SEARCH_MEMBERSHIPS: _contract(
        _definition(
            SEARCH_MEMBERSHIPS,
            "Search memberships in this gym. Return stored status, dates, and amount.",
        ),
        (
            FieldRequirement(
                "membership_id",
                "Exact membership id. Optional.",
                required=False,
            ),
            FieldRequirement(
                "member_id",
                "Member id returned by search_members. Optional.",
                required=False,
            ),
            FieldRequirement(
                "status",
                "PENDING, ACTIVE, EXPIRED, SUSPENDED, or CANCELLED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "payment_status",
                "UNPAID, PARTIAL, PAID, PARTIALLY_REFUNDED, or REFUNDED. Optional.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement(
                "after",
                "Cursor from the previous page. Optional.",
                required=False,
            ),
        ),
    ),
    SEARCH_MEMBERSHIP_PLANS: _contract(
        _definition(
            SEARCH_MEMBERSHIP_PLANS,
            "Search membership plans in this gym. Set eligible_for_sale true "
            "for a new sale so inactive plans are excluded. A name that matches "
            "more than one remaining plan is ambiguous.",
        ),
        (
            FieldRequirement("code", "Stored plan code. Optional.", required=False),
            FieldRequirement(
                "eligible_for_sale",
                "True when offering a plan for a new membership. Optional.",
                required=False,
            ),
            FieldRequirement(
                "name",
                "Stored plan name. Optional. Do not use a name when more than one plan has it.",
                required=False,
            ),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from the previous page. Optional.", required=False),
        ),
    ),
    SEARCH_MEMBERSHIP_EXPIRIES: _contract(
        _definition(
            SEARCH_MEMBERSHIP_EXPIRIES,
            "Search memberships expiring in this gym. One member can have more than one "
            "membership. This is the membership expiry date, not the member record status. "
            "Use bucket today, expired, within_3_days, within_7_days, or within_30_days, "
            "or set expiry_from and expiry_to.",
        ),
        (
            FieldRequirement(
                "bucket",
                "today, expired, within_3_days, within_7_days, or within_30_days. Optional.",
                required=False,
            ),
            FieldRequirement("expiry_from", "First expiry date. Optional.", required=False),
            FieldRequirement("expiry_to", "Last expiry date. Optional.", required=False),
            FieldRequirement("member_id", "Member id. Optional.", required=False),
            FieldRequirement(
                "limit",
                "Page size from 1 to 25. Optional.",
                required=False,
                kind="integer",
            ),
            FieldRequirement("after", "Cursor from a bucket page. Optional.", required=False),
        ),
    ),
}
_READERS = {
    SEARCH_MEMBERSHIPS: search_memberships,
    SEARCH_MEMBERSHIP_PLANS: search_membership_plans,
    SEARCH_MEMBERSHIP_EXPIRIES: search_membership_expiries,
}
