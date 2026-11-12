"""PostgreSQL coverage for member read tools."""

from __future__ import annotations

import json
import os
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.dispatch import execute_offered_read
from apps.assistant.execution import ExecutionRejected
from apps.assistant.member_reads import execute_registered_read
from apps.assistant.models import Goal, LogicalClaim, ToolExecution
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import create_member, gym_today
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership, IdempotencyRecord
from apps.trainers.services import create_assignment
from tests.security.test_assistant_schedule import _goal, _profile
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

_HIDDEN = (
    "+959111111111",
    "secret@example.test",
    "secret-note",
    "secret street",
    "emergency-secret",
)


def _request(
    gym: Gym,
    actor: GymMembership,
    codes: frozenset[str] = frozenset({"members.view"}),
) -> Request:
    request = Request(APIRequestFactory().get("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_member_read"
    return request


def _read(gym: Gym, actor: GymMembership, tool_name: str, payload: dict[str, object]):
    return execute_registered_read(
        request=_request(gym, actor),
        view=APIView(),
        tool_name=tool_name,
        payload=payload,
    )


def _owner(code: str, email: str) -> tuple[Gym, GymMembership]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(client, token, code=code, owner_email=email)
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    return gym, owner


def _person(
    gym: Gym,
    actor: GymMembership,
    first_name: str,
    last_name: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    notes: str = "",
) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return create_member(
            gym=gym,
            actor=actor,
            member_number="",
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            gender=None,
            date_of_birth=None,
            address={"address_line1": "secret street"} if notes else {},
            join_date=gym_today(gym),
            emergency_contact_name="emergency-secret" if notes else "",
            emergency_contact_phone="",
            notes=notes,
            profile_photo_asset_id=None,
        )


def _counts(gym: Gym, goal: Goal) -> tuple[int, int, int, int, int, int]:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return (
            Member.objects.filter(gym=gym).count(),
            ToolExecution.objects.filter(gym=gym).count(),
            IdempotencyRecord.objects.filter(gym=gym).count(),
            LogicalClaim.objects.filter(gym=gym).count(),
            AuditLog.objects.filter(gym=gym).count(),
            Goal.objects.get(id=goal.id).version,
        )


def _ids(page: dict[str, object]) -> set[str]:
    rows = page["results"]
    assert isinstance(rows, list)
    return {str(row["id"]) for row in rows}


@override_settings(**jwt_settings())
def test_member_reads_stay_in_scope_and_write_nothing() -> None:
    gym, owner = _owner("MA01", "ma01-owner@example.test")
    other_gym, other_owner = _owner("MA02", "ma02-owner@example.test")
    first = _person(
        gym,
        owner,
        "Aung",
        "Aung",
        phone="+959111111111",
        email="secret@example.test",
        notes="secret-note",
    )
    second = _person(gym, owner, "Aung", "Aung")
    other_name = _person(gym, owner, "Hla", "Win")
    foreign = _person(other_gym, other_owner, "Aung", "Aung")
    trainer = add_staff(gym, email="ma01-trainer@example.test", role_key="TRAINER")
    accountant = add_staff(gym, email="ma01-accountant@example.test", role_key="ACCOUNTANT")
    profile = _profile(gym, trainer, code="MAT", name="Maung")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        create_assignment(
            gym=gym,
            actor=owner,
            trainer_staff_profile_id=profile.id,
            member_id=first.id,
            effective_from=gym_today(gym),
            effective_until=None,
            reason="Program",
            request_id="req_member_read",
            ip_address=None,
            user_agent="",
        )
    goal = _goal(gym, owner)
    before = _counts(gym, goal)

    page = _read(gym, owner, "search_members", {"search": "Aung Aung", "limit": 25})
    assert _ids(page) == {str(first.id), str(second.id)}
    assert str(foreign.id) not in _ids(page)
    assert page["ambiguous"] is True
    encoded = json.dumps(page)
    for hidden in _HIDDEN:
        assert hidden not in encoded
    rows = page["results"]
    assert isinstance(rows, list)
    assert {row["member_number"] for row in rows} == {first.member_number, second.member_number}
    assert len({row["id"] for row in rows}) == 2

    exact = _read(gym, owner, "search_members", {"member_number": first.member_number})
    assert _ids(exact) == {str(first.id)}
    assert exact["ambiguous"] is False

    burmese = _read(
        gym,
        owner,
        "search_members",
        {"member_number": first.member_number.translate(str.maketrans("0123456789", "၀၁၂၃၄၅၆၇၈၉"))},
    )
    assert _ids(burmese) == {str(first.id)}

    empty = _read(gym, owner, "search_members", {"search": "Nobody Here"})
    assert empty["results"] == []
    assert empty["ambiguous"] is False
    assert empty["has_more"] is False

    detail = _read(gym, owner, "get_member", {"member_id": str(first.id)})
    assert detail["id"] == str(first.id)
    assert detail["member_number"] == first.member_number
    assert detail["status"] == Member.Status.ACTIVE
    assert detail["is_pt_client"] is True
    assert all(hidden not in json.dumps(detail) for hidden in _HIDDEN)
    plain = _read(gym, owner, "get_member", {"member_id": str(other_name.id)})
    assert plain["is_pt_client"] is False

    routed = execute_offered_read(
        request=_request(gym, owner),
        view=APIView(),
        tool_name="search_members",
        payload={"search": "Hla Win"},
    )
    assert _ids(routed) == {str(other_name.id)}

    with pytest.raises(ExecutionRejected) as missing:
        _read(gym, owner, "get_member", {})
    assert missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as invalid:
        _read(gym, owner, "get_member", {"member_id": "not-a-member"})
    assert invalid.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as foreign_detail:
        _read(gym, owner, "get_member", {"member_id": str(foreign.id)})
    assert foreign_detail.value.code == "not_found"
    with pytest.raises(ExecutionRejected) as absent:
        _read(gym, owner, "get_member", {"member_id": str(uuid4())})
    assert absent.value.code == "not_found"

    seen: set[str] = set()
    cursor: object = None
    for _page_number in range(5):
        payload: dict[str, object] = {"limit": 1}
        if isinstance(cursor, str):
            payload["after"] = cursor
        cursor_page = _read(gym, owner, "search_members", payload)
        found = _ids(cursor_page)
        assert len(found) == 1
        assert found.isdisjoint(seen)
        seen |= found
        if cursor_page["has_more"] is False:
            break
        cursor = cursor_page["next_after"]
        assert isinstance(cursor, str)
    assert seen == {str(first.id), str(second.id), str(other_name.id)}
    with pytest.raises(ValidationError):
        _read(other_gym, other_owner, "search_members", {"limit": 1, "after": cursor})

    trainer_page = _read(gym, trainer, "search_members", {"search": "Aung Aung"})
    assert _ids(trainer_page) == {str(first.id)}
    trainer_detail = _read(gym, trainer, "get_member", {"member_id": str(first.id)})
    assert trainer_detail["id"] == str(first.id)
    with pytest.raises(ExecutionRejected) as hidden_member:
        _read(gym, trainer, "get_member", {"member_id": str(second.id)})
    assert hidden_member.value.code == "not_found"

    with pytest.raises(PermissionDenied):
        execute_registered_read(
            request=_request(gym, accountant, frozenset({"gym.view"})),
            view=APIView(),
            tool_name="search_members",
            payload={},
        )
    with pytest.raises(ValidationError):
        _read(gym, owner, "search_members", {"status": "SLEEPING"})
    with pytest.raises(ValidationError):
        _read(gym, owner, "search_members", {"email": "secret@example.test"})
    with pytest.raises(ExecutionRejected) as limited:
        _read(gym, owner, "search_members", {"limit": 26})
    assert limited.value.code == "invalid_limit"
    with pytest.raises(ExecutionRejected) as zero:
        _read(gym, owner, "search_members", {"limit": 0})
    assert zero.value.code == "invalid_limit"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="members").update(enabled=False)
    with pytest.raises(PermissionDenied):
        _read(gym, owner, "search_members", {})

    assert _counts(gym, goal)[1:] == before[1:]
    assert _counts(gym, goal)[0] == before[0]
    assert isinstance(first.id, UUID)
