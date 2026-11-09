from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.announcements.exceptions import AnnouncementStateConflict
from apps.announcements.models import Announcement
from apps.assistant.activity_tools import activity_tool_definitions, execute_registered_tool
from apps.assistant.member_tools import execute_registered_tool as execute_member_tool
from apps.assistant.models import Goal, ToolExecution, WorkflowStep
from apps.assistant.records import open_staff_goal
from apps.common.api.exceptions import RequestRejected
from apps.common.db.tenant_context import set_gym_context
from apps.members.exceptions import CheckInIneligible
from apps.members.models import CheckIn, Member
from apps.members.services import gym_today
from apps.notifications.models import Notification, PushAttempt
from apps.tenants.models import Feature, Gym, GymFeatureEntitlement, GymMembership, GymSettings
from apps.users.models import User
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


def _staff(code: str) -> tuple[Gym, GymMembership]:
    user = User.objects.create_user(f"{code}@example.test", "correct horse battery staple")
    gym = Gym.objects.create(code=code, name=code, status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        actor = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=gym,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    actor.user = user
    return gym, actor


def _enable(gym: Gym, code: str) -> None:
    feature, _created = Feature.objects.get_or_create(
        code=code,
        defaults={"name": code, "description": code},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.update_or_create(
            gym=gym,
            feature=feature,
            defaults={
                "enabled": True,
                "source": GymFeatureEntitlement.Source.OVERRIDE,
                "effective_from": None,
                "effective_until": None,
            },
        )


def _goal(gym: Gym, actor: GymMembership, intent: str) -> Goal:
    _conversation, goal, _step = open_staff_goal(
        gym=gym,
        actor=actor,
        intent=intent,
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


def _codes(tool_name: str) -> frozenset[str]:
    for tool in activity_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _request(
    gym: Gym,
    actor: GymMembership,
    tool_name: str,
    *,
    codes: frozenset[str] | None = None,
) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = _codes(tool_name) if codes is None else codes
    request.request_id = "req_activity_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str] | None = None,
):
    return execute_registered_tool(
        request=_request(gym, actor, tool_name, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=True,
    )


def _member_codes(tool_name: str) -> frozenset[str]:
    from apps.assistant.member_tools import member_tool_definitions

    for tool in member_tool_definitions():
        if tool.name == tool_name:
            return frozenset(tool.permissions)
    raise AssertionError(tool_name)


def _member(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
):
    request = _request(gym, actor, tool_name, codes=_member_codes(tool_name))
    return execute_member_tool(
        request=request,
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=True,
    )


def _announcement_payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "GENERAL",
        "title": "Holiday hours",
        "body": "The desk closes at six.",
        "audience": "ALL_ACTIVE_MEMBERS",
    }
    payload.update(extra)
    return payload


def _executions(goal: Goal, tool_name: str) -> int:
    return ToolExecution.objects.filter(goal=goal, tool_name=tool_name).count()


def test_announcement_publish_writes_notification_rows_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_transport() -> None:
        raise AssertionError("push delivery was started")

    monkeypatch.setattr("apps.notifications.transport.get_transport", reject_transport)
    monkeypatch.setattr("apps.notifications.push_services.get_transport", reject_transport)
    gym, actor = _staff("ann1")
    _enable(gym, "members")
    goal = _goal(gym, actor, "Announcements")
    member = _member(
        gym,
        actor,
        goal,
        "create_member",
        {"first_name": "Maung", "last_name": "Aye", "join_date": "2020-01-01"},
        1,
    )
    with pytest.raises(RequestRejected):
        _run(
            gym,
            actor,
            goal,
            "create_announcement",
            _announcement_payload(ends_at="2000-01-01T00:00:00Z"),
            member.version,
        )
    assert Announcement.objects.filter(gym=gym).count() == 0
    assert _executions(goal, "create_announcement") == 0

    created = _run(gym, actor, goal, "create_announcement", _announcement_payload(), member.version)
    replay = _run(gym, actor, goal, "create_announcement", _announcement_payload(), member.version)
    updated = _run(
        gym,
        actor,
        goal,
        "update_announcement",
        {"announcement_id": created.effect_reference, "title": "Updated hours"},
        created.version,
    )
    published = _run(
        gym,
        actor,
        goal,
        "publish_announcement",
        {"announcement_id": created.effect_reference},
        updated.version,
    )
    row = Announcement.objects.get(id=created.effect_reference)
    notes = Notification.objects.filter(announcement=row)
    assert created.outcome == "committed"
    assert replay.outcome == "existing"
    assert published.outcome == "committed"
    assert row.status == Announcement.Status.PUBLISHED
    assert row.title == "Updated hours"
    assert notes.count() == 2
    assert notes.get(channel=Notification.Channel.IN_APP).status == Notification.Status.SENT
    assert notes.get(channel=Notification.Channel.PUSH).status == Notification.Status.PENDING
    assert PushAttempt.objects.filter(notification__announcement=row).count() == 0
    assert _executions(goal, "create_announcement") == 1

    with pytest.raises(AnnouncementStateConflict):
        _run(
            gym,
            actor,
            goal,
            "update_announcement",
            {"announcement_id": created.effect_reference, "title": "After publish"},
            published.version,
        )
    row.refresh_from_db()
    assert row.title == "Updated hours"
    assert _executions(goal, "update_announcement") == 1

    cancelled = _run(
        gym,
        actor,
        goal,
        "cancel_announcement",
        {"announcement_id": created.effect_reference},
        published.version,
    )
    cancelled_again = _run(
        gym,
        actor,
        goal,
        "cancel_announcement",
        {"announcement_id": created.effect_reference},
        published.version,
    )
    row.refresh_from_db()
    assert cancelled.outcome == "committed"
    assert cancelled_again.outcome == "existing"
    assert row.status == Announcement.Status.CANCELLED
    assert notes.get(channel=Notification.Channel.PUSH).status == Notification.Status.CANCELLED

    bare, bare_actor = _staff("ann2")
    bare_goal = _goal(bare, bare_actor, "Announcements")
    with pytest.raises(PermissionDenied):
        _run(
            bare,
            bare_actor,
            bare_goal,
            "create_announcement",
            _announcement_payload(),
            1,
        )
    assert Announcement.objects.filter(gym=bare).count() == 0


@override_settings(**jwt_settings())
def test_manual_check_in_uses_the_execution_identity_once() -> None:
    client, token = platform_token("chk1-platform@example.test")
    payload = provision(client, token, code="CHK1", owner_email="chk1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    trainer = add_staff(gym, email="chk1-trainer@example.test", role_key="TRAINER")
    _enable(gym, "members")
    _enable(gym, "memberships")
    goal = _goal(gym, owner, "Check-ins")
    member = _member(
        gym,
        owner,
        goal,
        "create_member",
        {"first_name": "Hla", "last_name": "Aye", "join_date": "2020-01-01"},
        1,
    )
    with pytest.raises(CheckInIneligible):
        _run(
            gym,
            owner,
            goal,
            "create_manual_check_in",
            {"member_id": member.effect_reference, "note": "Front desk"},
            member.version,
        )
    assert CheckIn.objects.filter(gym=gym).count() == 0
    assert _executions(goal, "create_manual_check_in") == 0

    plan = _member(
        gym,
        owner,
        goal,
        "create_plan",
        {
            "name": "Monthly",
            "duration_value": 30,
            "duration_unit": "DAY",
            "price": "1000.00",
        },
        member.version,
    )
    membership = _member(
        gym,
        owner,
        goal,
        "create_membership",
        {
            "member_id": member.effect_reference,
            "plan_id": plan.effect_reference,
            "start_date": gym_today(gym).isoformat(),
        },
        plan.version,
    )
    with pytest.raises(Http404):
        _run(
            gym,
            trainer,
            goal,
            "create_manual_check_in",
            {"member_id": member.effect_reference},
            membership.version,
        )
    assert CheckIn.objects.filter(gym=gym).count() == 0

    occurred_at = (timezone.now() - timedelta(minutes=30)).isoformat()
    with pytest.raises(PermissionDenied):
        _run(
            gym,
            owner,
            goal,
            "create_manual_check_in",
            {"member_id": member.effect_reference, "occurred_at": occurred_at},
            membership.version,
        )
    assert CheckIn.objects.filter(gym=gym).count() == 0

    check_in_payload = {"member_id": member.effect_reference, "note": "Front desk"}
    recorded = _run(
        gym,
        owner,
        goal,
        "create_manual_check_in",
        check_in_payload,
        membership.version,
    )
    recorded_again = _run(
        gym,
        owner,
        goal,
        "create_manual_check_in",
        check_in_payload,
        membership.version,
    )
    visit = CheckIn.objects.get(gym=gym)
    claim = ToolExecution.objects.get(goal=goal, tool_name="create_manual_check_in")
    assert recorded.outcome == "committed"
    assert recorded_again.outcome == "existing"
    assert recorded_again.effect_reference == str(visit.id)
    assert visit.idempotency_key == claim.execution_identity
    assert visit.idempotency_key == recorded.execution_identity
    assert visit.member_id == Member.objects.get(id=member.effect_reference).id
    assert visit.check_out_at is None

    other_gym, other_owner = _staff("chk2")
    _enable(other_gym, "members")
    _enable(other_gym, "memberships")
    other_goal = _goal(other_gym, other_owner, "Check-ins")
    with pytest.raises(Http404):
        _run(
            other_gym,
            other_owner,
            other_goal,
            "check_out_check_in",
            {"check_in_id": str(visit.id)},
            1,
        )
    visit.refresh_from_db()
    assert visit.check_out_at is None
    assert CheckIn.objects.filter(gym=other_gym).count() == 0

    with pytest.raises(Http404):
        _run(
            gym,
            trainer,
            goal,
            "check_out_check_in",
            {"check_in_id": str(visit.id)},
            recorded.version,
            codes=frozenset({"checkins.update"}),
        )
    visit.refresh_from_db()
    assert visit.check_out_at is None

    checked_out = _run(
        gym,
        owner,
        goal,
        "check_out_check_in",
        {"check_in_id": str(visit.id)},
        recorded.version,
    )
    checked_out_again = _run(
        gym,
        owner,
        goal,
        "check_out_check_in",
        {"check_in_id": str(visit.id)},
        recorded.version,
    )
    visit.refresh_from_db()
    assert checked_out.outcome == "committed"
    assert checked_out_again.outcome == "existing"
    assert visit.check_out_at is not None
    assert CheckIn.objects.filter(gym=gym).count() == 1
    assert _executions(goal, "check_out_check_in") == 1
