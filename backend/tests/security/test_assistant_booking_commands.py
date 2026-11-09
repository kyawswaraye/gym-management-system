"""PostgreSQL security coverage for class booking assistant tools."""

from __future__ import annotations

import os
from datetime import date, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import transaction
from django.http import Http404
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.booking_tools import (
    attend_booking_command,
    booking_create_finance_body,
    cancel_booking_command,
    cancel_class_booking,
    execute_registered_tool,
    no_show_class_booking,
)
from apps.assistant.execution import ExecutionRejected
from apps.assistant.identity import execution_identity
from apps.assistant.models import Goal, LogicalClaim, WorkflowStep
from apps.classes.booking_services import attend_class_booking, create_class_booking
from apps.classes.exceptions import BookingStateConflict, ClassFull
from apps.classes.models import ClassBooking, ClassOccurrence
from apps.classes.serializers import ClassBookingWriteSerializer
from apps.classes.services import create_class_schedule, create_class_type
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.members.services import create_member
from apps.tenants.models import Gym, GymMembership, GymSettings, IdempotencyRecord
from apps.trainers.exceptions import PackageNotEligible
from apps.trainers.models import ServiceSessionLedger
from apps.trainers.package_services import grant_package_admin
from apps.trainers.services import create_service_package
from apps.users.models import User
from tests.security.test_assistant_schedule import _enable, _goal, _profile
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

_CREATE = "create_class_booking"
_ATTEND = "attend_class_booking"
_NO_SHOW = "mark_class_booking_no_show"
_CANCEL = "cancel_class_booking"
_BOOK = frozenset({"classes.book"})
_ATTENDANCE = frozenset({"classes.attendance"})


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


def _request(gym: Gym, actor: GymMembership, *, codes: frozenset[str]) -> Request:
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.user = actor.user
    request.gym = gym
    request.gym_membership = actor
    request.permission_codes = codes
    request.request_id = "req_booking_tool"
    return request


def _run(
    gym: Gym,
    actor: GymMembership,
    goal: Goal,
    tool_name: str,
    payload: dict[str, object],
    expected_version: int,
    *,
    codes: frozenset[str],
    confirmed: bool = True,
):
    return execute_registered_tool(
        request=_request(gym, actor, codes=codes),
        view=APIView(),
        tool_name=tool_name,
        goal=goal,
        step_id=_step(goal, tool_name).id,
        expected_version=expected_version,
        payload=payload,
        confirmed=confirmed,
    )


def _identity(goal: Goal, tool_name: str, payload: dict[str, object]) -> str:
    return execution_identity(
        gym_id=goal.gym_id,
        goal_id=goal.id,
        step_id=_step(goal, tool_name).id,
        tool_name=tool_name,
        attempt_number=_step(goal, tool_name).attempt_number,
        payload=payload,
    )


def _type(gym: Gym, actor: GymMembership):
    return create_class_type(
        gym=gym,
        actor=actor,
        code="YOGA",
        name="Yoga",
        description="",
        default_duration_minutes=45,
        default_capacity=8,
        request_id="req_type",
        ip_address=None,
        user_agent="",
    )


def _schedule(
    gym: Gym,
    actor: GymMembership,
    class_type_id: UUID,
    trainer_id: UUID,
    *,
    on: date,
    start: time,
    end: time,
    capacity: int,
    location: str,
):
    return create_class_schedule(
        gym=gym,
        actor=actor,
        class_type_id=class_type_id,
        trainer_staff_profile_id=trainer_id,
        weekdays=[on.weekday()],
        local_start_time=start,
        local_end_time=end,
        effective_from=on,
        effective_until=on,
        location=location,
        location_is_exclusive=False,
        capacity=capacity,
        request_id="req_schedule_setup",
        ip_address=None,
        user_agent="",
    )


def _occurrence(schedule_id: UUID) -> ClassOccurrence:
    return ClassOccurrence.objects.get(class_schedule_id=schedule_id)


def _member(gym: Gym, actor: GymMembership, first_name: str):
    return create_member(
        gym=gym,
        actor=actor,
        member_number="",
        first_name=first_name,
        last_name="Client",
        phone=None,
        email=None,
        gender=None,
        date_of_birth=None,
        address={},
        join_date=date(2026, 10, 1),
        emergency_contact_name="",
        emergency_contact_phone="",
        notes="",
        profile_photo_asset_id=None,
    )


def _package(gym: Gym, actor: GymMembership, class_type_id: UUID, code: str):
    return create_service_package(
        gym=gym,
        actor=actor,
        package_type="CLASS",
        code=code,
        name=code,
        session_count=2,
        validity_value=6,
        validity_unit="MONTH",
        price=Decimal("80.00"),
        class_type_id=class_type_id,
        request_id="req_package",
        ip_address=None,
        user_agent="",
    )


def _grant(gym: Gym, actor: GymMembership, member_id: UUID, package_id: UUID):
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        grant, _entry = grant_package_admin(
            gym=gym,
            actor=actor,
            member_id=member_id,
            service_package_id=package_id,
            business_date=date(2026, 10, 1),
            complimentary=True,
            reason="Booking coverage",
            request_id="req_grant",
            ip_address=None,
            user_agent="",
        )
    return grant


def _ledger(gym: Gym, package_id: UUID) -> list[tuple[str, int, UUID | None]]:
    return list(
        ServiceSessionLedger.objects.filter(
            gym=gym,
            member_service_package_id=package_id,
        )
        .order_by("created_at")
        .values_list("reason", "session_delta", "class_booking_id")
    )


def _create_payload(occurrence_id: UUID, member_id: UUID, package_id: UUID) -> dict[str, object]:
    return {
        "class_occurrence_id": str(occurrence_id).upper(),
        "member_id": str(member_id),
        "member_service_package_id": str(package_id),
    }


@override_settings(**jwt_settings())
def test_permission_feature_gym_and_route_stop_before_finance() -> None:
    client, token = platform_token("book1-platform@example.test")
    payload = provision(client, token, code="BKA1", owner_email="book1-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    create_payload = {
        "class_occurrence_id": str(uuid4()),
        "member_id": str(uuid4()),
        "member_service_package_id": str(uuid4()),
    }
    attend_payload = {"booking_id": str(uuid4())}
    cancel_payload = {"booking_id": str(uuid4()), "reason": "Rain"}
    for tool_name, command, codes in (
        (_CREATE, create_payload, frozenset()),
        (_CREATE, create_payload, _ATTENDANCE),
        (_ATTEND, attend_payload, _BOOK),
        (_NO_SHOW, attend_payload, _BOOK),
        (_CANCEL, cancel_payload, _ATTENDANCE),
    ):
        with pytest.raises(PermissionDenied):
            _run(gym, owner, goal, tool_name, command, 1, codes=codes)
    with pytest.raises(ValidationError):
        _run(
            gym,
            owner,
            goal,
            _CREATE,
            {"class_occurrence_id": str(uuid4())},
            1,
            codes=_BOOK,
        )
    with pytest.raises(ExecutionRejected) as manage_missing:
        _run(
            gym,
            owner,
            goal,
            _CANCEL,
            {"reason": "Rain"},
            1,
            codes=frozenset({"classes.manage"}),
        )
    assert manage_missing.value.code == "missing_fields"
    with pytest.raises(ExecutionRejected) as attend_missing:
        _run(gym, owner, goal, _ATTEND, {}, 1, codes=_ATTENDANCE)
    assert attend_missing.value.code == "missing_fields"

    bare_user = User.objects.create_user("book1-bare@example.test", "correct horse battery staple")
    bare = Gym.objects.create(code="BKAB", name="Bare", status=Gym.Status.ACTIVE)
    with transaction.atomic():
        set_gym_context(gym_id=bare.id)
        bare_actor = GymMembership.objects.create(
            gym=bare,
            user=bare_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date.today(),
            activated_at=timezone.now(),
        )
        GymSettings.objects.create(
            gym=bare,
            created_by_membership_id=bare_actor.id,
            updated_by_membership_id=bare_actor.id,
        )
    bare_actor.user = bare_user
    bare_goal = _goal(bare, bare_actor)
    with pytest.raises(PermissionDenied):
        _run(bare, bare_actor, bare_goal, _CREATE, create_payload, 1, codes=_BOOK)
    with pytest.raises(PermissionDenied):
        _run(bare, bare_actor, bare_goal, _ATTEND, attend_payload, 1, codes=_ATTENDANCE)
    with pytest.raises(ExecutionRejected) as mismatched:
        _run(
            gym,
            owner,
            goal,
            _CREATE,
            {**create_payload, "gym_id": str(uuid4())},
            1,
            codes=_BOOK,
        )
    assert mismatched.value.code == "gym_mismatch"
    with pytest.raises(ExecutionRejected) as unconfirmed:
        _run(
            gym,
            owner,
            goal,
            _CANCEL,
            {**cancel_payload, "confirmed": True},
            1,
            codes=_BOOK,
            confirmed=False,
        )
    assert unconfirmed.value.code == "confirmation_required"
    with pytest.raises(ExecutionRejected) as bad_route:
        _run(
            gym,
            owner,
            goal,
            _NO_SHOW,
            {"booking_id": "not-a-uuid"},
            1,
            codes=_ATTENDANCE,
        )
    assert bad_route.value.code == "finance_route"
    assert IdempotencyRecord.objects.filter(gym=gym).count() == 0
    assert IdempotencyRecord.objects.filter(gym=bare).count() == 0
    assert ClassBooking.objects.filter(gym=gym).count() == 0


@override_settings(**jwt_settings())
def test_create_keeps_sessions_and_separates_cached_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("book2-platform@example.test")
    payload = provision(client, token, code="BKA2", owner_email="book2-owner@example.test")
    other_payload = provision(client, token, code="BKA3", owner_email="book3-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    _enable(other, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="book2-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    open_schedule = _schedule(
        gym,
        owner,
        class_type.id,
        profile.id,
        on=date(2026, 11, 16),
        start=time(18, 0),
        end=time(18, 45),
        capacity=8,
        location="Studio",
    )
    full_schedule = _schedule(
        gym,
        owner,
        class_type.id,
        profile.id,
        on=date(2026, 11, 16),
        start=time(19, 0),
        end=time(19, 45),
        capacity=1,
        location="Hall",
    )
    occurrence = _occurrence(open_schedule.id)
    full_occurrence = _occurrence(full_schedule.id)
    member = _member(gym, owner, "Mina")
    second = _member(gym, owner, "Omar")
    package = _package(gym, owner, class_type.id, "CLS_BOOK")
    grant = _grant(gym, owner, member.id, package.id)
    second_grant = _grant(gym, owner, second.id, package.id)
    command = _create_payload(occurrence.id, member.id, grant.id)
    with pytest.raises(Http404):
        _run(
            gym,
            owner,
            goal,
            _CREATE,
            _create_payload(uuid4(), member.id, grant.id),
            1,
            codes=_BOOK,
        )
    assert ServiceSessionLedger.objects.filter(class_booking_id__isnull=False, gym=gym).count() == 0

    created = _run(gym, owner, goal, _CREATE, command, 1, codes=_BOOK)
    created_again = _run(gym, owner, goal, _CREATE, command, created.version, codes=_BOOK)
    booking = ClassBooking.objects.get(id=created.effect_reference)
    prepared = booking_create_finance_body(command)
    serializer = ClassBookingWriteSerializer(data=command)
    serializer.is_valid(raise_exception=True)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=created.execution_identity)
    expected_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route="/api/v1/class-bookings/",
        body=dict(serializer.validated_data),
    )
    assert created.outcome == "committed"
    assert created_again.outcome == "existing"
    assert created_again.effect_reference == created.effect_reference
    assert created.execution_identity == _identity(goal, _CREATE, prepared)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.http_method == "POST"
    assert stored.route == "/api/v1/class-bookings/"
    assert stored.actor_membership_id == owner.id
    assert stored.actor_member_id is None
    assert stored.request_hash == expected_hash
    assert booking.status == ClassBooking.Status.BOOKED
    assert booking.member_service_package_id == grant.id
    assert booking.unmetered is False
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]
    assert ClassBooking.objects.filter(gym=gym).count() == 1
    assert other.id

    calls = {"count": 0}
    real = create_class_booking

    def counting(**kwargs: object) -> ClassBooking:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.booking_tools.create_class_booking", counting)
    stale = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(full_occurrence.id, member.id, grant.id),
        1,
        codes=_BOOK,
    )
    assert stale.outcome == "stale_version"
    assert calls["count"] == 0
    goal.refresh_from_db()
    held = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(full_occurrence.id, member.id, grant.id),
        goal.version,
        codes=_BOOK,
    )
    assert held.outcome == "committed"
    assert calls["count"] == 1
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]
    full_command = _create_payload(full_occurrence.id, second.id, second_grant.id)
    with pytest.raises(ClassFull):
        _run(gym, owner, goal, _CREATE, full_command, held.version, codes=_BOOK)
    with pytest.raises(ClassFull):
        _run(gym, owner, goal, _CREATE, full_command, held.version, codes=_BOOK)
    assert calls["count"] == 3
    released = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _CREATE, booking_create_finance_body(full_command)),
    )
    assert released.state == IdempotencyRecord.State.IN_PROGRESS
    assert released.locked_until is not None
    assert released.locked_until <= timezone.now()
    assert ClassBooking.objects.filter(class_occurrence=full_occurrence).count() == 1
    assert _ledger(gym, second_grant.id) == [("GRANT", 2, None)]

    calls["count"] = 0
    bad_command = _create_payload(occurrence.id, second.id, uuid4())
    with pytest.raises(PackageNotEligible):
        _run(gym, owner, goal, _CREATE, bad_command, held.version, codes=_BOOK)
    with pytest.raises(ExecutionRejected) as replayed:
        _run(gym, owner, goal, _CREATE, bad_command, held.version, codes=_BOOK)
    assert replayed.value.code == "finance_failed"
    assert calls["count"] == 1
    failed = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(goal, _CREATE, booking_create_finance_body(bad_command)),
    )
    assert failed.state == IdempotencyRecord.State.FAILED
    failed_claim = LogicalClaim.objects.get(gym=gym, execution_identity=failed.idempotency_key)
    assert failed_claim.state == LogicalClaim.State.FAILED
    assert _ledger(gym, second_grant.id) == [("GRANT", 2, None)]


@override_settings(**jwt_settings())
def test_attend_consumes_one_session_and_caches_a_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = platform_token("book4-platform@example.test")
    payload = provision(client, token, code="BKA4", owner_email="book4-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="book4-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    future = _occurrence(
        _schedule(
            gym,
            owner,
            class_type.id,
            profile.id,
            on=date(2026, 11, 16),
            start=time(18, 0),
            end=time(18, 45),
            capacity=8,
            location="Studio",
        ).id
    )
    past = _occurrence(
        _schedule(
            gym,
            owner,
            class_type.id,
            profile.id,
            on=date(2026, 10, 12),
            start=time(18, 0),
            end=time(18, 45),
            capacity=8,
            location="Hall",
        ).id
    )
    member = _member(gym, owner, "Mina")
    package = _package(gym, owner, class_type.id, "CLS_ATT")
    grant = _grant(gym, owner, member.id, package.id)
    early = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(future.id, member.id, grant.id),
        1,
        codes=_BOOK,
    )
    ready = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(past.id, member.id, grant.id),
        early.version,
        codes=_BOOK,
    )
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]
    calls = {"count": 0}
    real = attend_class_booking

    def counting(**kwargs: object) -> ClassBooking:
        calls["count"] += 1
        return real(**kwargs)

    monkeypatch.setattr("apps.assistant.booking_tools.attend_class_booking", counting)
    early_command = {"booking_id": str(early.effect_reference).upper()}
    with pytest.raises(BookingStateConflict):
        _run(gym, owner, goal, _ATTEND, early_command, ready.version, codes=_ATTENDANCE)
    with pytest.raises(ExecutionRejected) as blocked:
        _run(gym, owner, goal, _ATTEND, early_command, ready.version, codes=_ATTENDANCE)
    assert blocked.value.code == "finance_failed"
    assert calls["count"] == 1
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]
    early_booking = ClassBooking.objects.get(id=early.effect_reference)
    assert early_booking.status == ClassBooking.Status.BOOKED

    command = {"booking_id": str(ready.effect_reference)}
    attended = _run(gym, owner, goal, _ATTEND, command, ready.version, codes=_ATTENDANCE)
    attended_again = _run(
        gym,
        owner,
        goal,
        _ATTEND,
        command,
        attended.version,
        codes=_ATTENDANCE,
    )
    assert attended.outcome == "committed"
    assert attended_again.outcome == "existing"
    assert attended.execution_identity == _identity(goal, _ATTEND, attend_booking_command(command))
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=attended.execution_identity)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.route == f"/api/v1/class-bookings/{ready.effect_reference}/attend/"
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=stored.route,
        body={},
    )
    assert calls["count"] == 2
    booking = ClassBooking.objects.get(id=ready.effect_reference)
    assert booking.status == ClassBooking.Status.ATTENDED
    assert _ledger(gym, grant.id) == [("GRANT", 2, None), ("ATTENDED", -1, booking.id)]

    other_goal = _goal(gym, owner)
    with pytest.raises(BookingStateConflict):
        _run(gym, owner, other_goal, _ATTEND, command, 1, codes=_ATTENDANCE)
    with pytest.raises(ExecutionRejected) as repeated:
        _run(gym, owner, other_goal, _ATTEND, command, 1, codes=_ATTENDANCE)
    assert repeated.value.code == "finance_failed"
    assert calls["count"] == 3
    assert _ledger(gym, grant.id) == [("GRANT", 2, None), ("ATTENDED", -1, booking.id)]


@override_settings(**jwt_settings())
def test_no_show_follows_the_session_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("book5-platform@example.test")
    payload = provision(client, token, code="BKA5", owner_email="book5-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="book5-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    consuming = _occurrence(
        _schedule(
            gym,
            owner,
            class_type.id,
            profile.id,
            on=date(2026, 10, 12),
            start=time(18, 0),
            end=time(18, 45),
            capacity=8,
            location="Studio",
        ).id
    )
    quiet = _occurrence(
        _schedule(
            gym,
            owner,
            class_type.id,
            profile.id,
            on=date(2026, 10, 19),
            start=time(18, 0),
            end=time(18, 45),
            capacity=8,
            location="Hall",
        ).id
    )
    member = _member(gym, owner, "Mina")
    package = _package(gym, owner, class_type.id, "CLS_NS")
    grant = _grant(gym, owner, member.id, package.id)
    first = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(consuming.id, member.id, grant.id),
        1,
        codes=_BOOK,
    )
    marked = _run(
        gym,
        owner,
        goal,
        _NO_SHOW,
        {"booking_id": str(first.effect_reference)},
        first.version,
        codes=_ATTENDANCE,
    )
    marked_again = _run(
        gym,
        owner,
        goal,
        _NO_SHOW,
        {"booking_id": str(first.effect_reference)},
        marked.version,
        codes=_ATTENDANCE,
    )
    booking = ClassBooking.objects.get(id=first.effect_reference)
    assert marked.outcome == "committed"
    assert marked_again.outcome == "existing"
    assert booking.status == ClassBooking.Status.NO_SHOW
    assert _ledger(gym, grant.id) == [("GRANT", 2, None), ("POLICY_NO_SHOW", -1, booking.id)]
    other_goal = _goal(gym, owner)
    calls = {"count": 0}

    def counting(**kwargs: object) -> ClassBooking:
        calls["count"] += 1
        return no_show_class_booking(**kwargs)

    monkeypatch.setattr("apps.assistant.booking_tools.no_show_class_booking", counting)
    with pytest.raises(BookingStateConflict):
        _run(
            gym,
            owner,
            other_goal,
            _NO_SHOW,
            {"booking_id": str(booking.id)},
            1,
            codes=_ATTENDANCE,
        )
    with pytest.raises(ExecutionRejected) as replayed:
        _run(
            gym,
            owner,
            other_goal,
            _NO_SHOW,
            {"booking_id": str(booking.id)},
            1,
            codes=_ATTENDANCE,
        )
    assert replayed.value.code == "finance_failed"
    assert calls["count"] == 1
    assert _ledger(gym, grant.id) == [("GRANT", 2, None), ("POLICY_NO_SHOW", -1, booking.id)]

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.filter(gym=gym).update(class_no_show_consumes_session=False)
    skipped = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(quiet.id, member.id, grant.id),
        marked.version,
        codes=_BOOK,
    )
    quiet_result = _run(
        gym,
        owner,
        goal,
        _NO_SHOW,
        {"booking_id": str(skipped.effect_reference)},
        skipped.version,
        codes=_ATTENDANCE,
    )
    assert quiet_result.outcome == "committed"
    quiet_booking = ClassBooking.objects.get(id=skipped.effect_reference)
    assert quiet_booking.status == ClassBooking.Status.NO_SHOW
    assert _ledger(gym, grant.id) == [("GRANT", 2, None), ("POLICY_NO_SHOW", -1, booking.id)]
    assert not ServiceSessionLedger.objects.filter(class_booking_id=quiet_booking.id).exists()


@override_settings(**jwt_settings())
def test_cancel_does_not_refund_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client, token = platform_token("book6-platform@example.test")
    payload = provision(client, token, code="BKA6", owner_email="book6-owner@example.test")
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable(gym, "pt")
    goal = _goal(gym, owner)
    trainer = add_staff(gym, email="book6-trainer@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="ADA", name="Ada")
    class_type = _type(gym, owner)
    future = _occurrence(
        _schedule(
            gym,
            owner,
            class_type.id,
            profile.id,
            on=date(2026, 11, 23),
            start=time(18, 0),
            end=time(18, 45),
            capacity=8,
            location="Studio",
        ).id
    )
    member = _member(gym, owner, "Mina")
    package = _package(gym, owner, class_type.id, "CLS_CAN")
    grant = _grant(gym, owner, member.id, package.id)
    created = _run(
        gym,
        owner,
        goal,
        _CREATE,
        _create_payload(future.id, member.id, grant.id),
        1,
        codes=frozenset({"classes.manage"}),
    )
    command = {"booking_id": str(created.effect_reference).upper(), "reason": "  Rain day  "}
    cancelled = _run(gym, owner, goal, _CANCEL, command, created.version, codes=_BOOK)
    cancelled_again = _run(gym, owner, goal, _CANCEL, command, cancelled.version, codes=_BOOK)
    booking = ClassBooking.objects.get(id=created.effect_reference)
    prepared = cancel_booking_command(command)
    stored = IdempotencyRecord.objects.get(gym=gym, idempotency_key=cancelled.execution_identity)
    assert cancelled.outcome == "committed"
    assert cancelled_again.outcome == "existing"
    assert cancelled.effect_reference == str(booking.id)
    assert cancelled.execution_identity == _identity(goal, _CANCEL, prepared)
    assert stored.state == IdempotencyRecord.State.COMPLETED
    assert stored.route == f"/api/v1/class-bookings/{booking.id}/cancel/"
    assert stored.actor_member_id is None
    assert stored.request_hash == canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=owner.id,
        http_method="POST",
        route=stored.route,
        body={"reason": "Rain day"},
    )
    assert booking.status == ClassBooking.Status.CANCELLED
    assert booking.reason == "Rain day"
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]

    calls = {"count": 0}

    def counting(**kwargs: object) -> ClassBooking:
        calls["count"] += 1
        return cancel_class_booking(**kwargs)

    monkeypatch.setattr("apps.assistant.booking_tools.cancel_class_booking", counting)
    other_goal = _goal(gym, owner)
    with pytest.raises(BookingStateConflict):
        _run(
            gym,
            owner,
            other_goal,
            _CANCEL,
            {"booking_id": str(booking.id), "reason": "Again"},
            1,
            codes=_BOOK,
        )
    with pytest.raises(ExecutionRejected) as replayed:
        _run(
            gym,
            owner,
            other_goal,
            _CANCEL,
            {"booking_id": str(booking.id), "reason": "Again"},
            1,
            codes=_BOOK,
        )
    assert replayed.value.code == "finance_failed"
    assert calls["count"] == 1
    booking.refresh_from_db()
    assert booking.status == ClassBooking.Status.CANCELLED
    assert booking.reason == "Rain day"
    assert _ledger(gym, grant.id) == [("GRANT", 2, None)]
    failed = IdempotencyRecord.objects.get(
        gym=gym,
        idempotency_key=_identity(
            other_goal,
            _CANCEL,
            cancel_booking_command({"booking_id": str(booking.id), "reason": "Again"}),
        ),
    )
    assert failed.state == IdempotencyRecord.State.FAILED
