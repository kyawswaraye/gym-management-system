"""PostgreSQL proof that every assistant tool stays inside the session gym.

Reads and writes share one executor. A payload gym id, a foreign goal, a
foreign record id, and a cursor minted for another gym cannot select or change
that gym's rows. Confirmation and rejection use the stored proposal's gym and
actor. Execution identities include the server gym, and the stored claim
constraints include the gym as well.
"""

from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.dispatch import execute_confirmed_write, execute_offered_read, tool_contracts
from apps.assistant.execution import ExecutionRejected
from apps.assistant.executor import ExecutionMode
from apps.assistant.identity import execution_identity
from apps.assistant.knowledge import assistant_context
from apps.assistant.lookups import resolve_references
from apps.assistant.models import Goal, LogicalClaim, ToolExecution, WorkflowStep
from apps.assistant.records import open_confirmation, open_conversation, open_staff_goal
from apps.assistant.registry import production_registry
from apps.assistant.turns import confirm_step, reject_step
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Payment
from apps.members.services import create_member, gym_today
from apps.tenants.models import Gym, GymMembership, IdempotencyRecord
from tests.security.test_assistant_schedule import _enable, _profile
from tests.security.test_tenant_gym_settings import (
    add_staff,
    jwt_settings,
    platform_token,
    provision,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _owner(code: str, email: str) -> tuple[Gym, GymMembership]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(client, token, code=code, owner_email=email)
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    return gym, owner


def _request(gym: Gym, actor: GymMembership) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = frozenset()
    request.request_id = "req_tenant_isolation"
    return request


def _person(gym: Gym, actor: GymMembership, first_name: str, last_name: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_member(
            gym=gym,
            actor=actor,
            member_number="",
            first_name=first_name,
            last_name=last_name,
            phone=None,
            email=None,
            gender=None,
            date_of_birth=None,
            address={},
            join_date=gym_today(gym),
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
        )


def _goal(gym: Gym, actor: GymMembership) -> Goal:
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent="Tenant isolation",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return goal


def _step(goal: Goal, tool_name: str) -> WorkflowStep:
    found = WorkflowStep.objects.filter(goal=goal, tool_name=tool_name).first()
    if found is not None:
        return found
    with transaction.atomic():
        set_gym_context(gym_id=goal.gym_id)
        return WorkflowStep.objects.create(
            gym_id=goal.gym_id,
            workflow=goal.workflow,
            goal=goal,
            position=WorkflowStep.objects.filter(goal=goal).count() + 1,
            tool_name=tool_name,
        )


def _rows(gym: Gym) -> tuple[int, int, int, int, int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            Member.objects.filter(gym=gym).count(),
            Payment.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
        )


def _ids(page: object) -> set[str]:
    assert isinstance(page, dict)
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows if isinstance(row, dict)}


def _constraint_fields(model: type, name: str) -> tuple[str, ...]:
    for constraint in model._meta.constraints:
        if constraint.name == name:
            return tuple(constraint.fields)
    raise AssertionError(name)


@override_settings(**jwt_settings())
def test_every_assistant_tool_rejects_another_gym() -> None:
    gym, owner = _owner("AUD1", "aud1-owner@example.test")
    other, other_owner = _owner("AUD2", "aud2-owner@example.test")
    for current in (gym, other):
        _enable(current, "members")
        _enable(current, "finance")
    local_hla = _person(gym, owner, "Hla", "Win")
    foreign_hla = _person(other, other_owner, "Hla", "Win")
    _person(gym, owner, "Nya", "Localmember")
    _person(other, other_owner, "Nya", "Foreignmember")
    local_trainer = add_staff(gym, email="aud1-trainer@example.test", role_key="TRAINER")
    foreign_trainer = add_staff(other, email="aud2-trainer@example.test", role_key="TRAINER")
    local_staff = _profile(gym, local_trainer, code="ZED", name="Localstaff")
    foreign_staff = _profile(other, foreign_trainer, code="ZED", name="Foreignstaff")
    goal = _goal(gym, owner)
    foreign_goal = _goal(other, other_owner)
    registry = production_registry()
    contracts = tool_contracts()
    tools = [registry.get(name) for name in registry.names()]
    reads = [tool for tool in tools if tool.operation == "read"]
    writes = [tool for tool in tools if tool.operation == "write"]
    assert len(tools) == 114
    assert len(reads) == 16
    assert len(writes) == 98
    assert set(contracts) == {tool.name for tool in tools}
    assert all(tool.confirmation_required for tool in writes)
    assert all(not tool.confirmation_required and not tool.approval_required for tool in reads)
    assert all(contracts[tool.name].mode == ExecutionMode.READ for tool in reads)
    assert all(contracts[tool.name].mode != ExecutionMode.READ for tool in writes)

    before = (_rows(gym), _rows(other))
    request = _request(gym, owner)
    for tool in reads:
        with pytest.raises(ExecutionRejected) as mismatch:
            execute_offered_read(
                request=request,
                view=APIView(),
                tool_name=tool.name,
                payload={"gym_id": str(other.id)},
            )
        assert mismatch.value.code == "gym_mismatch", tool.name
    assert request.gym.id == gym.id
    assert (_rows(gym), _rows(other)) == before

    for tool in writes:
        with pytest.raises(ExecutionRejected) as mismatch:
            execute_confirmed_write(
                request=request,
                view=APIView(),
                tool_name=tool.name,
                goal=foreign_goal,
                step_id=uuid4(),
                expected_version=1,
                payload={"gym_id": str(other.id)},
                confirmed=False,
            )
        assert mismatch.value.code == "gym_mismatch", tool.name
    assert request.gym.id == gym.id
    assert (_rows(gym), _rows(other)) == before
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        assert Goal.objects.get(id=foreign_goal.id).version == 1

    own_page = execute_offered_read(
        request=request,
        view=APIView(),
        tool_name="search_members",
        payload={"search": "Hla Win", "limit": 25},
    )
    assert _ids(own_page) == {str(local_hla.id)}
    other_page = execute_offered_read(
        request=_request(other, other_owner),
        view=APIView(),
        tool_name="search_members",
        payload={"search": "Hla Win", "limit": 25},
    )
    assert _ids(other_page) == {str(foreign_hla.id)}
    with pytest.raises(ExecutionRejected) as foreign_detail:
        execute_offered_read(
            request=request,
            view=APIView(),
            tool_name="get_member",
            payload={"member_id": str(foreign_hla.id)},
        )
    with pytest.raises(ExecutionRejected) as missing_detail:
        execute_offered_read(
            request=request,
            view=APIView(),
            tool_name="get_member",
            payload={"member_id": str(uuid4())},
        )
    assert foreign_detail.value.code == missing_detail.value.code == "not_found"
    assert str(foreign_detail.value) == str(missing_detail.value) == "The record was not found."
    first_page = execute_offered_read(
        request=request,
        view=APIView(),
        tool_name="search_members",
        payload={"limit": 1},
    )
    assert isinstance(first_page, dict)
    cursor = first_page["next_after"]
    assert isinstance(cursor, str) and cursor
    with pytest.raises(ValidationError):
        execute_offered_read(
            request=_request(other, other_owner),
            view=APIView(),
            tool_name="search_members",
            payload={"limit": 1, "after": cursor},
        )
    assert str(local_hla.id) not in _ids(other_page)

    brief = assistant_context(gym, frozenset(), True)
    other_brief = assistant_context(other, frozenset(), True)
    assert "Localmember" in brief
    assert "Localstaff" in brief
    assert "Foreignmember" not in brief
    assert "Foreignstaff" not in brief
    assert "Foreignmember" in other_brief
    assert "Localmember" not in other_brief
    conversation = open_conversation(gym=gym, actor=owner, title="Names")
    _resolved, _labels, failure = resolve_references(
        gym,
        conversation,
        {"trainer_staff_profile_id": str(foreign_staff.id)},
    )
    assert failure == {"error": "not_found", "field": "trainer_staff_profile_id"}
    assert str(foreign_staff.id) != str(local_staff.id)

    with pytest.raises(Http404):
        execute_confirmed_write(
            request=request,
            view=APIView(),
            tool_name="update_member",
            goal=goal,
            step_id=_step(goal, "update_member").id,
            expected_version=1,
            payload={"member_id": str(foreign_hla.id), "last_name": "Hacked"},
            confirmed=True,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        foreign_hla.refresh_from_db()
    assert foreign_hla.last_name == "Win"

    with pytest.raises(ValidationError) as foreign_member:
        execute_confirmed_write(
            request=request,
            view=APIView(),
            tool_name="create_payment",
            goal=goal,
            step_id=_step(goal, "create_payment").id,
            expected_version=1,
            payload={
                "purpose": "OTHER",
                "description": "Day pass",
                "amount": "40.00",
                "currency": gym.currency,
                "method": "BANK_TRANSFER",
                "business_date": gym_today(gym).isoformat(),
                "member_id": str(foreign_hla.id),
            },
            confirmed=True,
        )
    assert "member_id" in foreign_member.value.detail
    created = execute_confirmed_write(
        request=_request(other, other_owner),
        view=APIView(),
        tool_name="create_payment",
        goal=foreign_goal,
        step_id=_step(foreign_goal, "create_payment").id,
        expected_version=1,
        payload={
            "purpose": "OTHER",
            "description": "Day pass",
            "amount": "40.00",
            "currency": other.currency,
            "method": "BANK_TRANSFER",
            "business_date": gym_today(other).isoformat(),
        },
        confirmed=True,
    )
    with pytest.raises(Http404):
        execute_confirmed_write(
            request=request,
            view=APIView(),
            tool_name="update_payment",
            goal=goal,
            step_id=_step(goal, "update_payment").id,
            expected_version=1,
            payload={"payment_id": created.effect_reference, "description": "Hacked"},
            confirmed=True,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        stored = Payment.objects.get(id=created.effect_reference)
    assert stored.description == "Day pass"
    assert stored.gym_id == other.id
    assert _rows(gym)[1] == 0

    waiting = open_confirmation(
        gym=other,
        actor=other_owner,
        conversation=open_conversation(gym=other, actor=other_owner, title="Confirm"),
        intent="Update a member",
        tool_name="update_member",
        required_input={
            "actor_membership_id": str(other_owner.id),
            "payload": {"member_id": str(foreign_hla.id)},
        },
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert waiting is not None
    _waiting_goal, waiting_step = waiting
    manager = add_staff(other, email="aud2-manager@example.test", role_key="MANAGER")
    for actor_request in (_request(gym, owner), _request(other, manager)):
        with pytest.raises(Http404):
            confirm_step(request=actor_request, view=APIView(), step_id=waiting_step.id)
        with pytest.raises(Http404):
            reject_step(request=actor_request, view=APIView(), step_id=waiting_step.id)
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        waiting_step.refresh_from_db()
    assert waiting_step.status == WorkflowStep.Status.WAITING_CONFIRMATION

    material = {"member_id": str(foreign_hla.id), "last_name": "Hacked"}
    own_identity = execution_identity(
        gym_id=gym.id,
        goal_id=goal.id,
        step_id=_step(goal, "update_member").id,
        tool_name="update_member",
        attempt_number=1,
        payload=material,
    )
    other_identity = execution_identity(
        gym_id=other.id,
        goal_id=goal.id,
        step_id=_step(goal, "update_member").id,
        tool_name="update_member",
        attempt_number=1,
        payload=material,
    )
    assert own_identity != other_identity
    assert "gym" in _constraint_fields(ToolExecution, "assistant_execution_identity_uniq")
    assert "gym" in _constraint_fields(LogicalClaim, "assistant_logical_identity_uniq")
    assert "gym" in _constraint_fields(IdempotencyRecord, "idem_scope_key_uniq")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rejected_keys = set(
            IdempotencyRecord.objects.filter(gym=gym).values_list("idempotency_key", flat=True)
        )
        rejected_claims = set(
            LogicalClaim.objects.filter(gym=gym).values_list("execution_identity", flat=True)
        )
        assert ToolExecution.objects.filter(gym=gym).count() == 0
        assert (
            IdempotencyRecord.objects.filter(gym=gym)
            .exclude(
                state=IdempotencyRecord.State.IN_PROGRESS,
                resource_id=None,
            )
            .count()
            == 0
        )
        assert LogicalClaim.objects.filter(gym=gym, state=LogicalClaim.State.COMPLETED).count() == 0
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        stored_keys = set(
            IdempotencyRecord.objects.filter(gym=other).values_list("idempotency_key", flat=True)
        )
        stored_claims = set(
            LogicalClaim.objects.filter(gym=other).values_list("execution_identity", flat=True)
        )
    assert rejected_keys
    assert stored_keys
    assert rejected_keys.isdisjoint(stored_keys)
    assert rejected_claims.isdisjoint(stored_claims)
