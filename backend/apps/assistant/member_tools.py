"""Member and membership tools. Each one calls the existing service.

The handlers are database-only. Member numbers are allocated inside
``create_member``. Suspend and resume do not receive a new expiry. A profile
photo on create is rejected by the existing serializer before the insert.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.serializers import Serializer
from rest_framework.views import APIView

from apps.assistant.execution import ExecutionRejected, ExecutionResult
from apps.assistant.executor import ExecutionMode, ToolCall, ToolContract, execute_confirmed_tool
from apps.assistant.models import Goal
from apps.assistant.prerequisites import FieldRequirement, Prerequisite
from apps.assistant.registry import ToolDefinition
from apps.members.models import Member, MembershipPlan
from apps.members.serializers import (
    MemberArchiveSerializer,
    MemberCreateSerializer,
    MemberRestoreSerializer,
    MembershipCancelSerializer,
    MembershipCommandSerializer,
    MembershipCreateSerializer,
    MembershipPlanArchiveSerializer,
    MembershipPlanCreateSerializer,
    MembershipPlanUpdateSerializer,
    MembershipUpdateSerializer,
    MemberUpdateSerializer,
)
from apps.members.services import (
    archive_member,
    archive_plan,
    cancel_membership,
    create_member,
    create_membership,
    create_plan,
    gym_today,
    restore_member,
    resume_membership,
    suspend_membership,
    update_member,
    update_membership_note,
    update_plan,
)
from apps.trainers.selectors import require_assigned_member, require_visible_membership

_MEMBER_FIELDS = (
    FieldRequirement("first_name", "First name"),
    FieldRequirement("last_name", "Last name"),
    FieldRequirement("join_date", "Join date"),
)
_PLAN_FIELDS = (
    FieldRequirement("name", "Plan name"),
    FieldRequirement("duration_value", "Duration"),
    FieldRequirement("duration_unit", "Duration unit"),
    FieldRequirement("price", "Price"),
)


@dataclass(frozen=True)
class RegisteredTool:
    contract: ToolContract
    effect: Any


def _definition(
    name: str,
    description: str,
    permissions: tuple[str, ...],
    features: tuple[str, ...],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        module="members" if features == ("members",) else "memberships",
        operation="write",
        permissions=permissions,
        features=features,
        confirmation_required=True,
    )


def _contract(
    definition: ToolDefinition,
    fields: tuple[FieldRequirement, ...],
) -> ToolContract:
    return ToolContract(
        definition=definition,
        mode=ExecutionMode.SAME_TRANSACTION,
        prerequisite=Prerequisite(
            code=definition.name,
            description=definition.description,
            fields=fields,
        ),
    )


def _body(payload: Mapping[str, object], *omit: str) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key not in omit}


def _validated(
    serializer_type: type[Serializer[Any]],
    data: Mapping[str, object],
) -> dict[str, Any]:
    serializer = serializer_type(data=data)
    serializer.is_valid(raise_exception=True)
    return cast(dict[str, Any], serializer.validated_data)


def _identifier(payload: Mapping[str, object], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValidationError({key: ["Enter a valid identifier."]})
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValidationError({key: ["Enter a valid identifier."]}) from exc


def _create_member(call: ToolCall) -> str:
    payload = _validated(MemberCreateSerializer, call.payload)
    member = create_member(
        gym=call.gym,
        actor=call.actor,
        member_number=str(payload.get("member_number", "")),
        first_name=str(payload["first_name"]),
        last_name=str(payload["last_name"]),
        phone=payload.get("phone"),
        email=payload.get("email"),
        gender=payload.get("gender"),
        date_of_birth=payload.get("date_of_birth"),
        address=dict(payload.get("address") or {}),
        join_date=payload["join_date"],
        emergency_contact_name=str(payload.get("emergency_contact_name", "")),
        emergency_contact_phone=str(payload.get("emergency_contact_phone", "")),
        notes=str(payload.get("notes", "")),
        profile_photo_asset_id=payload.get("profile_photo_asset_id"),
    )
    return str(member.id)


def _update_member(call: ToolCall) -> str:
    member_id = _identifier(call.payload, "member_id")
    member = update_member(
        gym=call.gym,
        actor=call.actor,
        member_id=member_id,
        changes=_validated(MemberUpdateSerializer, _body(call.payload, "member_id")),
    )
    return str(member.id)


def _archive_member(call: ToolCall) -> str:
    member_id = _identifier(call.payload, "member_id")
    _validated(MemberArchiveSerializer, _body(call.payload, "member_id"))
    member = archive_member(gym=call.gym, actor=call.actor, member_id=member_id)
    return str(member.id)


def _restore_member(call: ToolCall) -> str:
    member_id = _identifier(call.payload, "member_id")
    _validated(MemberRestoreSerializer, _body(call.payload, "member_id"))
    member = restore_member(
        gym=call.gym,
        actor=call.actor,
        member_id=member_id,
        request_id=call.request_id,
        ip_address=call.ip_address,
        user_agent=call.user_agent,
    )
    return str(member.id)


def _create_plan(call: ToolCall) -> str:
    payload = _validated(MembershipPlanCreateSerializer, call.payload)
    price = payload["price"]
    if not isinstance(price, Decimal):
        raise ValidationError({"price": ["Enter a price."]})
    plan = create_plan(
        gym=call.gym,
        actor=call.actor,
        code=str(payload["code"]),
        name=str(payload["name"]),
        description=str(payload.get("description", "")),
        duration_value=int(payload["duration_value"]),
        duration_unit=str(payload["duration_unit"]),
        price=price,
    )
    return str(plan.id)


def _update_plan(call: ToolCall) -> str:
    plan_id = _identifier(call.payload, "plan_id")
    plan = update_plan(
        gym=call.gym,
        actor=call.actor,
        plan_id=plan_id,
        changes=_validated(MembershipPlanUpdateSerializer, _body(call.payload, "plan_id")),
    )
    return str(plan.id)


def _archive_plan(call: ToolCall) -> str:
    plan_id = _identifier(call.payload, "plan_id")
    _validated(MembershipPlanArchiveSerializer, _body(call.payload, "plan_id"))
    plan = archive_plan(gym=call.gym, actor=call.actor, plan_id=plan_id)
    return str(plan.id)


def _create_membership(call: ToolCall) -> str:
    payload = _validated(MembershipCreateSerializer, call.payload)
    require_assigned_member(
        gym_id=call.gym.id,
        actor=call.actor,
        member_id=payload["member_id"],
        as_of=gym_today(call.gym),
    )
    member = get_object_or_404(Member, gym=call.gym, id=payload["member_id"])
    plan = get_object_or_404(MembershipPlan, gym=call.gym, id=payload["plan_id"])
    discount = payload["discount_amount"]
    if not isinstance(discount, Decimal):
        raise ValidationError({"discount_amount": ["Enter a discount."]})
    membership = create_membership(
        gym=call.gym,
        actor=call.actor,
        member=member,
        plan=plan,
        start_date=payload["start_date"],
        discount_amount=discount,
        note=str(payload.get("note", "")),
    )
    return str(membership.id)


def _update_membership_note(call: ToolCall) -> str:
    membership_id = _identifier(call.payload, "membership_id")
    payload = _validated(MembershipUpdateSerializer, _body(call.payload, "membership_id"))
    require_visible_membership(gym=call.gym, actor=call.actor, membership_id=membership_id)
    membership = update_membership_note(
        gym=call.gym,
        membership_id=membership_id,
        note=str(payload["note"]),
    )
    return str(membership.id)


def _suspend_membership(call: ToolCall) -> str:
    membership_id = _identifier(call.payload, "membership_id")
    _validated(MembershipCommandSerializer, _body(call.payload, "membership_id"))
    require_visible_membership(gym=call.gym, actor=call.actor, membership_id=membership_id)
    membership = suspend_membership(gym=call.gym, membership_id=membership_id)
    return str(membership.id)


def _resume_membership(call: ToolCall) -> str:
    membership_id = _identifier(call.payload, "membership_id")
    _validated(MembershipCommandSerializer, _body(call.payload, "membership_id"))
    require_visible_membership(gym=call.gym, actor=call.actor, membership_id=membership_id)
    membership = resume_membership(gym=call.gym, membership_id=membership_id)
    return str(membership.id)


def _cancel_membership(call: ToolCall) -> str:
    membership_id = _identifier(call.payload, "membership_id")
    payload = _validated(MembershipCancelSerializer, _body(call.payload, "membership_id"))
    require_visible_membership(gym=call.gym, actor=call.actor, membership_id=membership_id)
    membership = cancel_membership(
        gym=call.gym,
        actor=call.actor,
        membership_id=membership_id,
        reason=str(payload["reason"]),
    )
    return str(membership.id)


def _tools() -> tuple[RegisteredTool, ...]:
    members = ("members",)
    plans = ("memberships",)
    specs: tuple[tuple[ToolDefinition, tuple[FieldRequirement, ...], Any], ...] = (
        (
            _definition("create_member", "Create a member", ("members.create",), members),
            _MEMBER_FIELDS,
            _create_member,
        ),
        (
            _definition("update_member", "Update a member", ("members.update",), members),
            (FieldRequirement("member_id", "Member"),),
            _update_member,
        ),
        (
            _definition("archive_member", "Archive a member", ("members.archive",), members),
            (FieldRequirement("member_id", "Member"),),
            _archive_member,
        ),
        (
            _definition("restore_member", "Restore a member", ("members.restore",), members),
            (FieldRequirement("member_id", "Member"),),
            _restore_member,
        ),
        (
            _definition("create_plan", "Create a membership plan", ("memberships.create",), plans),
            _PLAN_FIELDS,
            _create_plan,
        ),
        (
            _definition("update_plan", "Update a membership plan", ("memberships.update",), plans),
            (FieldRequirement("plan_id", "Plan"),),
            _update_plan,
        ),
        (
            _definition(
                "archive_plan",
                "Archive a membership plan",
                ("memberships.update",),
                plans,
            ),
            (FieldRequirement("plan_id", "Plan"),),
            _archive_plan,
        ),
        (
            _definition(
                "create_membership",
                "Create a membership",
                ("memberships.create",),
                plans,
            ),
            (
                FieldRequirement("member_id", "Member"),
                FieldRequirement("plan_id", "Plan"),
                FieldRequirement("start_date", "Start date"),
            ),
            _create_membership,
        ),
        (
            _definition(
                "update_membership_note",
                "Update a membership note",
                ("memberships.update",),
                plans,
            ),
            (FieldRequirement("membership_id", "Membership"),),
            _update_membership_note,
        ),
        (
            _definition(
                "suspend_membership",
                "Suspend a membership",
                ("memberships.update",),
                plans,
            ),
            (FieldRequirement("membership_id", "Membership"),),
            _suspend_membership,
        ),
        (
            _definition(
                "resume_membership",
                "Resume a membership",
                ("memberships.update",),
                plans,
            ),
            (FieldRequirement("membership_id", "Membership"),),
            _resume_membership,
        ),
        (
            _definition(
                "cancel_membership",
                "Cancel a membership",
                ("memberships.cancel",),
                plans,
            ),
            (
                FieldRequirement("membership_id", "Membership"),
                FieldRequirement("reason", "Cancellation reason"),
            ),
            _cancel_membership,
        ),
    )
    return tuple(
        RegisteredTool(contract=_contract(definition, fields), effect=effect)
        for definition, fields, effect in specs
    )


_REGISTERED = {tool.contract.definition.name: tool for tool in _tools()}
MEMBER_TOOL_NAMES = tuple(sorted(_REGISTERED))


def member_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(tool.contract.definition for tool in _REGISTERED.values())


def execute_registered_tool(
    *,
    request: Request,
    view: APIView,
    tool_name: str,
    goal: Goal,
    step_id: UUID,
    expected_version: int,
    payload: Mapping[str, object],
    confirmed: bool,
) -> ExecutionResult:
    """Run one registered member or membership tool as the signed-in staff member."""

    tool = _REGISTERED.get(tool_name)
    if tool is None:
        raise ExecutionRejected("This tool is not registered.", code="unknown_tool")
    return execute_confirmed_tool(
        request=request,
        view=view,
        contract=tool.contract,
        goal=goal,
        step_id=step_id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
        effect=tool.effect,
    )
