from __future__ import annotations

import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps as django_apps
from django.db import (
    DatabaseError,
    IntegrityError,
    OperationalError,
    connection,
    connections,
    transaction,
)
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError
from rest_framework.renderers import JSONRenderer

from apps.common.api.exceptions import api_exception_handler
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.exceptions import (
    FinanceStateConflict,
    IdempotencyInProgress,
    IdempotencyKeyExpired,
    IdempotencyResponseTooLarge,
)
from apps.common.finance.idempotency import (
    IdempotentResponse,
    canonical_request_hash,
    run_idempotent,
)
from apps.tenants.exceptions import IdempotencyKeyReused
from apps.tenants.models import (
    ExpenseCategory,
    Gym,
    GymMembership,
    GymSettings,
    IdempotencyRecord,
    Permission,
    Role,
    RolePermission,
    SystemRoleTemplate,
    TenantSequence,
)
from apps.tenants.sequence_services import (
    SequenceAllocationError,
    allocate_formatted_number,
    ensure_protected_finance_seeds,
)
from apps.users.models import User

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

ROUTE = "/api/v1/payments/"
KEY = "finance-command-key"
MARKER = b'{"marker":"PLAINTEXT-MARKER-finance"}'


def _gym(code: str) -> Gym:
    return Gym.objects.create(
        code=code, name=code, status=Gym.Status.ACTIVE, timezone="Asia/Yangon"
    )


def _membership(gym: Gym, email: str) -> GymMembership:
    user = User.objects.create_user(email, "correct horse battery staple")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 18),
            activated_at=timezone.now(),
        )


def _expire(record_id: object) -> None:
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE tenants_idempotency_record DISABLE TRIGGER idem_protect_trg")
        try:
            cursor.execute(
                """
                UPDATE tenants_idempotency_record
                SET created_at = NOW() - INTERVAL '2 days',
                    expires_at = NOW() - INTERVAL '1 hour',
                    updated_at = NOW()
                WHERE id = %s
                """,
                [record_id],
            )
        finally:
            cursor.execute("ALTER TABLE tenants_idempotency_record ENABLE TRIGGER idem_protect_trg")


def _sequence(gym: Gym, *, prefix: str = "FIN") -> None:
    year = timezone.now().year
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.create(gym=gym, receipt_number_prefix=prefix)
        TenantSequence.objects.create(
            gym=gym,
            sequence_key=f"RECEIPT:{year}",
            prefix=prefix,
            next_value=1,
            padding=6,
        )


def _run(
    gym: Gym,
    membership: GymMembership | None,
    *,
    key: str = KEY,
    body: dict[str, object] | None = None,
    mutate,
    request: object | None = None,
):
    return run_idempotent(
        gym_id=gym.id,
        actor_membership_id=None if membership is None else membership.id,
        http_method="POST",
        route=ROUTE,
        idempotency_key=key,
        body={"amount": "10.00"} if body is None else body,
        mutate=mutate,
        created_by_membership_id=None if membership is None else membership.id,
        request=request,
    )


def test_idempotency_is_tenant_scoped_and_rejects_cross_gym_actors() -> None:
    gym_a = _gym("FIN-A")
    gym_b = _gym("FIN-B")
    actor_a = _membership(gym_a, "finance-a@example.test")
    actor_b = _membership(gym_b, "finance-b@example.test")
    _run(gym_a, actor_a, mutate=lambda: IdempotentResponse(status=201, body=MARKER))
    _run(
        gym_b,
        actor_b,
        key="finance-command-keyb",
        mutate=lambda: IdempotentResponse(status=201, body=b'{"gym":"b"}'),
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute("SELECT count(*) FROM tenants_idempotency_record")
        assert cursor.fetchone() == (0,)
        with pytest.raises(DatabaseError, match="row-level security"), transaction.atomic():
            cursor.execute(
                """
                INSERT INTO tenants_idempotency_record (
                    id, gym_id, http_method, route, idempotency_key, request_hash,
                    state, locked_until, expires_at, created_at, updated_at
                ) VALUES (
                    %s, %s, 'POST', %s, %s, %s, 'IN_PROGRESS', NOW() + INTERVAL '30 seconds',
                    NOW() + INTERVAL '1 day', NOW(), NOW()
                )
                """,
                [str(uuid4()), str(gym_a.id), ROUTE, "missing-context-key", "a" * 64],
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT gym_id::text FROM tenants_idempotency_record")
            assert {row[0] for row in cursor.fetchall()} == {str(gym_a.id)}
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=gym_a.id)
        IdempotencyRecord.objects.create(
            gym=gym_a,
            actor_membership_id=actor_b.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="cross-gym-actor-01",
            request_hash="b" * 64,
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=24),
        )


def test_stored_response_hides_plaintext_and_rejects_malformed_evidence() -> None:
    gym = _gym("FIN-C")
    actor = _membership(gym, "finance-c@example.test")
    result = _run(gym, actor, mutate=lambda: IdempotentResponse(status=201, body=MARKER))
    assert result.replayed is False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record = IdempotencyRecord.objects.get(gym=gym)
    assert MARKER.decode() not in record.response_body_ciphertext
    assert "PLAINTEXT-MARKER-finance" not in (record.response_body_ciphertext or "")
    replay = _run(gym, actor, mutate=lambda: IdempotentResponse(status=500, body=b"second"))
    assert replay.replayed is True
    assert replay.body == MARKER
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="short",
            request_hash="c" * 64,
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=24),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="malformed-hash-key1",
            request_hash="NOT-A-HASH",
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=24),
        )


def test_same_key_replays_failure_and_rejects_a_different_hash() -> None:
    gym = _gym("FIN-D")
    actor = _membership(gym, "finance-d@example.test")

    def fail() -> IdempotentResponse:
        raise IdempotencyResponseTooLarge()

    with pytest.raises(IdempotencyResponseTooLarge):
        _run(gym, actor, mutate=fail)
    replay = _run(gym, actor, mutate=lambda: IdempotentResponse(status=201, body=b'{"no":1}'))
    assert replay.replayed is True
    assert replay.status == 422
    expected = JSONRenderer().render(api_exception_handler(IdempotencyResponseTooLarge(), {}).data)
    assert replay.body == expected
    with pytest.raises(IdempotencyKeyReused):
        _run(gym, actor, body={"amount": "11.00"}, mutate=lambda: IdempotentResponse(201, b"x"))


def test_admission_failures_release_the_claim_and_domain_failures_replay() -> None:
    gym = _gym("FIN-P")
    actor = _membership(gym, "finance-p@example.test")
    request = SimpleNamespace(request_id="req_finance_domain_01")

    def raise_validation() -> IdempotentResponse:
        raise ValidationError({"amount": ["Invalid."]})

    def raise_denied() -> IdempotentResponse:
        raise PermissionDenied()

    def raise_throttled() -> IdempotentResponse:
        raise Throttled(wait=1)

    admissions = (
        ("admission-validation1", ValidationError, raise_validation),
        ("admission-permission1", PermissionDenied, raise_denied),
        ("admission-throttled01", Throttled, raise_throttled),
    )
    for key, error, mutate in admissions:
        with pytest.raises(error):
            _run(gym, actor, key=key, mutate=mutate)
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            record = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
        assert record.state == IdempotencyRecord.State.IN_PROGRESS
        assert record.response_body_ciphertext is None
        assert record.locked_until is not None
        assert record.locked_until <= timezone.now()
        retried = _run(
            gym,
            actor,
            key=key,
            mutate=lambda: IdempotentResponse(status=201, body=b'{"retried":true}'),
        )
        assert retried.replayed is False
        assert retried.body == b'{"retried":true}'

    def raise_conflict() -> IdempotentResponse:
        raise FinanceStateConflict()

    with pytest.raises(FinanceStateConflict):
        _run(gym, actor, key="domain-conflict-001", request=request, mutate=raise_conflict)
    replay = _run(
        gym,
        actor,
        key="domain-conflict-001",
        request=SimpleNamespace(request_id="req_later_should_not_appear"),
        mutate=lambda: IdempotentResponse(status=201, body=b'{"no":1}'),
    )
    expected = JSONRenderer().render(
        api_exception_handler(FinanceStateConflict(), {"request": request}).data
    )
    assert replay.replayed is True
    assert replay.status == 409
    assert replay.body == expected
    assert b"req_finance_domain_01" in replay.body
    assert b"req_unknown" not in replay.body

    def raise_too_large() -> IdempotentResponse:
        raise IdempotencyResponseTooLarge()

    with pytest.raises(IdempotencyResponseTooLarge):
        _run(gym, actor, key="domain-too-large-01", request=request, mutate=raise_too_large)
    large_replay = _run(
        gym,
        actor,
        key="domain-too-large-01",
        mutate=lambda: IdempotentResponse(status=201, body=b'{"no":1}'),
    )
    large_expected = JSONRenderer().render(
        api_exception_handler(IdempotencyResponseTooLarge(), {"request": request}).data
    )
    assert large_replay.replayed is True
    assert large_replay.status == 422
    assert large_replay.body == large_expected


def test_returned_non_success_releases_instead_of_committing() -> None:
    gym = _gym("FIN-Q")
    actor = _membership(gym, "finance-q@example.test")
    _sequence(gym)

    def return_status(status: int) -> IdempotentResponse:
        allocate_formatted_number(gym_id=gym.id, sequence_key=f"RECEIPT:{timezone.now().year}")
        return IdempotentResponse(status=status, body=b'{"status":true}')

    for status, key in ((500, "callback-status-500"), (409, "callback-status-409")):
        with pytest.raises(RuntimeError, match="2xx"):
            _run(gym, actor, key=key, mutate=lambda status=status: return_status(status))
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            record = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
            sequence = TenantSequence.objects.get(gym=gym)
        assert record.state == IdempotencyRecord.State.IN_PROGRESS
        assert record.response_body_ciphertext is None
        assert record.locked_until is not None
        assert record.locked_until <= timezone.now()
        assert sequence.next_value == 1
        retried = _run(
            gym,
            actor,
            key=key,
            mutate=lambda: IdempotentResponse(status=201, body=b'{"ok":1}'),
        )
        assert retried.replayed is False
        assert retried.body == b'{"ok":1}'
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE tenants_idempotency_record DISABLE TRIGGER idem_protect_trg")
        try:
            with pytest.raises(DatabaseError, match="idem_status_ck"), transaction.atomic():
                cursor.execute(
                    """
                    UPDATE tenants_idempotency_record
                    SET response_status = 500
                    WHERE gym_id = %s AND idempotency_key = 'callback-status-500'
                    """,
                    [gym.id],
                )
        finally:
            cursor.execute("ALTER TABLE tenants_idempotency_record ENABLE TRIGGER idem_protect_trg")


def test_live_claim_conflicts_and_stale_claim_can_be_reclaimed() -> None:
    gym = _gym("FIN-E")
    actor = _membership(gym, "finance-e@example.test")
    now = timezone.now()
    live_body: dict[str, object] = {}
    live_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=actor.id,
        http_method="POST",
        route=ROUTE,
        body=live_body,
    )
    stale_body: dict[str, object] = {"stale": True}
    stale_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=actor.id,
        http_method="POST",
        route=ROUTE,
        body=stale_body,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        live = IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="live-claim-key-001",
            request_hash=live_hash,
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=now + timedelta(seconds=30),
            expires_at=now + timedelta(hours=24),
            created_by_membership_id=actor.id,
        )
        IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="stale-claim-key-01",
            request_hash=stale_hash,
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=now - timedelta(seconds=5),
            expires_at=now + timedelta(hours=24),
            created_by_membership_id=actor.id,
        )
    with pytest.raises(IdempotencyInProgress):
        run_idempotent(
            gym_id=gym.id,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="live-claim-key-001",
            body=live_body,
            mutate=lambda: IdempotentResponse(status=201, body=b'{"live":1}'),
        )
    with pytest.raises(IdempotencyKeyReused):
        run_idempotent(
            gym_id=gym.id,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="live-claim-key-001",
            body={"other": True},
            mutate=lambda: IdempotentResponse(status=201, body=b'{"other":1}'),
        )
    result = run_idempotent(
        gym_id=gym.id,
        actor_membership_id=actor.id,
        http_method="POST",
        route=ROUTE,
        idempotency_key="stale-claim-key-01",
        body=stale_body,
        mutate=lambda: IdempotentResponse(status=201, body=b'{"stale":true}'),
    )
    assert result.replayed is False
    assert result.body == b'{"stale":true}'
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            IdempotencyRecord.objects.get(id=live.id).state == IdempotencyRecord.State.IN_PROGRESS
        )


def test_concurrent_duplicate_claim_reports_in_progress() -> None:
    gym = _gym("FIN-F")
    actor = _membership(gym, "finance-f@example.test")
    ready = Event()
    release = Event()
    attempted = Event()

    def mutate() -> IdempotentResponse:
        ready.set()
        assert release.wait(10)
        return IdempotentResponse(status=201, body=b'{"once":true}')

    def first() -> str:
        connections.close_all()
        try:
            result = _run(gym, actor, key="concurrent-claim-01", mutate=mutate)
            return "replayed" if result.replayed else "completed"
        finally:
            connections.close_all()

    def second() -> str:
        connections.close_all()
        try:
            assert ready.wait(10)
            _run(
                gym, actor, key="concurrent-claim-01", mutate=lambda: IdempotentResponse(201, b"x")
            )
            return "completed"
        except IdempotencyInProgress:
            return "busy"
        finally:
            attempted.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        assert attempted.wait(10)
        release.set()
        assert second_future.result(timeout=10) == "busy"
        assert first_future.result(timeout=10) == "completed"
    replay = _run(
        gym,
        actor,
        key="concurrent-claim-01",
        mutate=lambda: IdempotentResponse(status=500, body=b"nope"),
    )
    assert replay.replayed is True
    assert replay.body == b'{"once":true}'


def test_unexpected_failure_rolls_back_and_releases_the_claim() -> None:
    gym = _gym("FIN-G")
    actor = _membership(gym, "finance-g@example.test")
    _sequence(gym)
    year = timezone.now().year
    sequence_key = f"RECEIPT:{year}"

    def mutate() -> IdempotentResponse:
        allocate_formatted_number(gym_id=gym.id, sequence_key=sequence_key)
        raise RuntimeError("boom-secret-body")

    with pytest.raises(RuntimeError, match="boom-secret-body"):
        _run(gym, actor, key="rollback-command-01", mutate=mutate)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=sequence_key)
        record = IdempotencyRecord.objects.get(gym=gym, idempotency_key="rollback-command-01")
    assert sequence.next_value == 1
    assert record.state == IdempotencyRecord.State.IN_PROGRESS
    assert record.response_body_ciphertext is None
    assert record.locked_until is not None
    assert record.locked_until <= timezone.now()
    retry = _run(
        gym,
        actor,
        key="rollback-command-01",
        mutate=lambda: IdempotentResponse(status=201, body=b'{"again":1}'),
    )
    assert retry.replayed is False
    assert retry.body == b'{"again":1}'


def test_transient_database_error_retries_with_the_same_key() -> None:
    gym = _gym("FIN-K")
    actor = _membership(gym, "finance-k@example.test")
    _sequence(gym, prefix="FK")
    year = timezone.now().year
    sequence_key = f"RECEIPT:{year}"

    def fail() -> IdempotentResponse:
        allocate_formatted_number(gym_id=gym.id, sequence_key=sequence_key)
        raise OperationalError("deadlock detected")

    with pytest.raises(OperationalError, match="deadlock detected"):
        _run(gym, actor, key="deadlock-retry-key1", mutate=fail)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert TenantSequence.objects.get(gym=gym, sequence_key=sequence_key).next_value == 1
        record = IdempotencyRecord.objects.get(gym=gym, idempotency_key="deadlock-retry-key1")
    assert record.state == IdempotencyRecord.State.IN_PROGRESS
    assert record.response_body_ciphertext is None

    def succeed() -> IdempotentResponse:
        number = allocate_formatted_number(gym_id=gym.id, sequence_key=sequence_key)
        return IdempotentResponse(status=201, body=number.encode())

    result = _run(gym, actor, key="deadlock-retry-key1", mutate=succeed)
    assert result.replayed is False
    assert result.body == f"FK-{year}-000001".encode()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=sequence_key)
        record = IdempotencyRecord.objects.get(gym=gym, idempotency_key="deadlock-retry-key1")
    assert sequence.next_value == 2
    assert record.state == IdempotencyRecord.State.COMPLETED


def test_oversized_response_is_not_stored() -> None:
    gym = _gym("FIN-H")
    actor = _membership(gym, "finance-h@example.test")
    payload = b"x" * (64 * 1024 + 1)
    with pytest.raises(IdempotencyResponseTooLarge):
        _run(
            gym,
            actor,
            key="oversized-response-1",
            mutate=lambda: IdempotentResponse(status=201, body=payload),
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record = IdempotencyRecord.objects.get(gym=gym)
    assert record.state == IdempotencyRecord.State.FAILED
    assert record.response_body_bytes is not None
    assert record.response_body_bytes < 64 * 1024
    replay = _run(
        gym,
        actor,
        key="oversized-response-1",
        mutate=lambda: IdempotentResponse(status=201, body=payload),
    )
    assert replay.replayed is True
    assert payload not in replay.body


def test_expired_completed_failed_and_in_progress_claims_are_not_replayed() -> None:
    gym = _gym("FIN-L")
    actor = _membership(gym, "finance-l@example.test")
    completed = _run(
        gym,
        actor,
        key="expired-completed-01",
        mutate=lambda: IdempotentResponse(status=201, body=b'{"done":true}'),
    )
    assert completed.replayed is False
    with pytest.raises(IdempotencyResponseTooLarge):
        _run(
            gym,
            actor,
            key="expired-failed-key01",
            mutate=lambda: IdempotentResponse(status=201, body=b"x" * (64 * 1024 + 1)),
        )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        pending = IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=actor.id,
            http_method="POST",
            route=ROUTE,
            idempotency_key="expired-progress-001",
            request_hash=canonical_request_hash(
                gym_id=gym.id,
                actor_membership_id=actor.id,
                http_method="POST",
                route=ROUTE,
                body={},
            ),
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(seconds=30),
            expires_at=timezone.now() + timedelta(hours=24),
            created_by_membership_id=actor.id,
        )
        completed_row = IdempotencyRecord.objects.get(
            gym=gym, idempotency_key="expired-completed-01"
        )
        failed_row = IdempotencyRecord.objects.get(gym=gym, idempotency_key="expired-failed-key01")
    _expire(completed_row.id)
    _expire(failed_row.id)
    _expire(pending.id)
    calls = {"n": 0}

    def count() -> IdempotentResponse:
        calls["n"] += 1
        return IdempotentResponse(status=201, body=b'{"late":1}')

    for key, body in (
        ("expired-completed-01", {}),
        ("expired-failed-key01", {}),
        ("expired-progress-001", {}),
        ("expired-completed-01", {"amount": "2.00"}),
    ):
        with pytest.raises(IdempotencyKeyExpired):
            _run(gym, actor, key=key, body=body, mutate=count)
    assert calls["n"] == 0
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            IdempotencyRecord.objects.get(id=completed_row.id).state
            == IdempotencyRecord.State.COMPLETED
        )
        assert (
            IdempotencyRecord.objects.get(id=failed_row.id).state == IdempotencyRecord.State.FAILED
        )
        assert (
            IdempotencyRecord.objects.get(id=pending.id).state
            == IdempotencyRecord.State.IN_PROGRESS
        )


def test_stale_reclaim_cannot_double_commit() -> None:
    gym = _gym("FIN-M")
    actor = _membership(gym, "finance-m@example.test")
    _sequence(gym, prefix="FM")
    year = timezone.now().year
    sequence_key = f"RECEIPT:{year}"
    key = "stale-fence-key-001"
    started = Event()
    release = Event()

    def mutate_a() -> IdempotentResponse:
        started.set()
        assert release.wait(10)
        allocate_formatted_number(gym_id=gym.id, sequence_key=sequence_key)
        return IdempotentResponse(status=201, body=b'{"owner":"A"}')

    def run_a() -> str:
        connections.close_all()
        try:
            _run(gym, actor, key=key, mutate=mutate_a)
        except IdempotencyInProgress:
            return "fenced"
        else:
            return "committed"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_a)
        assert started.wait(10)
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            record = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
            record.locked_until = timezone.now() - timedelta(seconds=5)
            record.save(update_fields=["locked_until", "updated_at"])

        def mutate_b() -> IdempotentResponse:
            number = allocate_formatted_number(gym_id=gym.id, sequence_key=sequence_key)
            return IdempotentResponse(status=201, body=number.encode())

        result_b = _run(gym, actor, key=key, mutate=mutate_b)
        release.set()
        assert future.result(timeout=10) == "fenced"
    assert result_b.replayed is False
    assert result_b.body == f"FM-{year}-000001".encode()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=sequence_key)
    assert record.state == IdempotencyRecord.State.COMPLETED
    assert sequence.next_value == 2
    assert IdempotencyRecord.objects.filter(gym=gym, idempotency_key=key).count() == 1


def test_ciphertext_over_fernet_bound_is_rejected() -> None:
    gym = _gym("FIN-N")
    actor = _membership(gym, "finance-n@example.test")
    _run(gym, actor, mutate=lambda: IdempotentResponse(status=201, body=b'{"ok":1}'))
    token = "gAAAAA" + ("A" * (87481 - 6))
    assert len(token.encode()) == 87481
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record = IdempotencyRecord.objects.get(gym=gym)
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE tenants_idempotency_record DISABLE TRIGGER idem_protect_trg")
        try:
            with (
                pytest.raises(DatabaseError, match="idem_ciphertext_len_ck"),
                transaction.atomic(),
            ):
                cursor.execute(
                    """
                    UPDATE tenants_idempotency_record
                    SET response_body_ciphertext = %s
                    WHERE id = %s
                    """,
                    [token, record.id],
                )
        finally:
            cursor.execute("ALTER TABLE tenants_idempotency_record ENABLE TRIGGER idem_protect_trg")


def test_sequence_allocation_is_transactional_and_contiguous() -> None:
    gym = _gym("FIN-I")
    _sequence(gym, prefix="W1")
    year = timezone.now().year
    key = f"RECEIPT:{year}"
    with pytest.raises(SequenceAllocationError):
        allocate_formatted_number(gym_id=gym.id, sequence_key=key)
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            assert allocate_formatted_number(gym_id=gym.id, sequence_key=key) == f"W1-{year}-000001"
            raise RuntimeError("rollback")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert TenantSequence.objects.get(gym=gym, sequence_key=key).next_value == 1

    def allocate_one() -> str:
        connections.close_all()
        try:
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                return allocate_formatted_number(gym_id=gym.id, sequence_key=key)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=16) as pool:
        numbers = [future.result() for future in [pool.submit(allocate_one) for _ in range(100)]]
    expected = [f"W1-{year}-{value:06d}" for value in range(1, 101)]
    assert sorted(numbers) == expected
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=key)
        assert sequence.next_value == 101
        sequence.prefix = "CHANGED"
        with (
            pytest.raises(DatabaseError, match="tenant sequence identity is immutable"),
            transaction.atomic(),
        ):
            sequence.save(update_fields=["prefix", "updated_at"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=key)
        sequence.next_value = sequence.next_value + 2
        with (
            pytest.raises(DatabaseError, match="next_value must increase by one"),
            transaction.atomic(),
        ):
            sequence.save(update_fields=["next_value", "updated_at"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute(
                    "UPDATE tenants_tenant_sequence SET prefix = 'NOPE' WHERE gym_id = %s",
                    [gym.id],
                )
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute(
                    "DELETE FROM tenants_tenant_sequence WHERE gym_id = %s",
                    [gym.id],
                )
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute(
                    "DELETE FROM tenants_idempotency_record WHERE gym_id = %s",
                    [gym.id],
                )


def test_protected_categories_cannot_change_identity_or_be_deleted() -> None:
    gym = _gym("FIN-J")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymSettings.objects.create(gym=gym, receipt_number_prefix="FIN-J")
        ExpenseCategory.objects.create(
            gym=gym,
            code="RENT",
            name="Local rent",
            normalized_name="LOCAL RENT",
            display_order=3,
            is_active=True,
        )
        ensure_protected_finance_seeds(gym=gym)
        year = timezone.now().year
        allocate_formatted_number(gym_id=gym.id, sequence_key=f"RECEIPT:{year}")
        ensure_protected_finance_seeds(gym=gym)
        rent = ExpenseCategory.objects.get(gym=gym, system_key="RENT")
        sequence = TenantSequence.objects.get(gym=gym, sequence_key=f"RECEIPT:{year}")
        assert rent.name == "Local rent"
        assert sequence.next_value == 2
        assert ExpenseCategory.objects.filter(gym=gym).count() == 10
        rent.code = "CHANGED"
        with (
            pytest.raises(DatabaseError, match="protected expense category"),
            transaction.atomic(),
        ):
            rent.save(update_fields=["code", "updated_at"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rent = ExpenseCategory.objects.get(gym=gym, system_key="RENT")
        with pytest.raises(DatabaseError, match="cannot be deleted"), transaction.atomic():
            rent.delete()
        custom = ExpenseCategory.objects.create(
            gym=gym,
            code="SNACKS",
            name="Snacks",
            normalized_name="SNACKS",
            display_order=20,
            is_active=True,
        )
        custom.delete()
        assert not ExpenseCategory.objects.filter(gym=gym, code="SNACKS").exists()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        ensure_protected_finance_seeds(gym=gym)
        assert (
            TenantSequence.objects.get(
                gym=gym, sequence_key=f"RECEIPT:{timezone.now().year}"
            ).next_value
            == 2
        )


def test_finance_permission_upgrade_preserves_custom_roles() -> None:
    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    upgrade = importlib.import_module("apps.tenants.migrations.0025_finance_permission_catalog")
    seed.seed(django_apps, None)
    user = User.objects.create_user("finance-roles@example.test", "correct horse battery staple")
    gym = _gym("FIN-K")
    other = _gym("FIN-L")
    templates = {
        key: SystemRoleTemplate.objects.get(key=key)
        for key in ("OWNER", "MANAGER", "RECEPTIONIST", "ACCOUNTANT", "TRAINER")
    }
    assert all(template.version == 1 for template in templates.values())
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=True,
            joined_date=date(2026, 10, 18),
        )
        roles = {
            key: Role.objects.create(
                gym=gym,
                key=key,
                name=key.title(),
                source_template=templates[key],
                source_template_version=templates[key].version,
                created_by_membership_id=membership.id,
            )
            for key in templates
        }
        custom = Role.objects.create(
            gym=gym,
            key="LEAD",
            name="Desk",
            created_by_membership_id=membership.id,
        )
        RolePermission.objects.create(
            gym=gym,
            role=custom,
            permission=Permission.objects.get(code="expenses.view"),
            created_by_membership_id=membership.id,
        )
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        Role.objects.create(gym=other, key="MANAGER", name="Custom Manager")
    trainer_version = templates["TRAINER"].version
    retail_approve_count = Permission.objects.filter(code="retail.approve").count()
    upgrade.grant_finance_foundation_permissions(django_apps, None)
    upgrade.grant_finance_foundation_permissions(django_apps, None)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        owner = SystemRoleTemplate.objects.get(key="OWNER")
        assert owner.version == 2
        assert SystemRoleTemplate.objects.get(key="TRAINER").version == trainer_version
        assert Role.objects.get(id=roles["OWNER"].id).source_template_version == 2
        assert Role.objects.get(id=roles["RECEPTIONIST"].id).source_template_version == 2
        assert _codes(roles["OWNER"]) >= {
            "inventory.view",
            "expense_categories.manage",
            "retail.void",
        }
        assert "expense_categories.manage" in _codes(roles["MANAGER"])
        assert "expense_categories.manage" in _codes(roles["ACCOUNTANT"])
        assert "inventory.manage" not in _codes(roles["ACCOUNTANT"])
        assert "expense_categories.manage" not in _codes(roles["RECEPTIONIST"])
        assert "inventory.adjust" not in _codes(roles["RECEPTIONIST"])
        assert "retail.void" not in _codes(roles["RECEPTIONIST"])
        assert "expense_categories.manage" not in _codes(roles["TRAINER"])
        assert "inventory.view" not in _codes(roles["TRAINER"])
        assert _codes(custom) == {"expenses.view"}
        assert Permission.objects.filter(code="retail.approve").count() == retail_approve_count
        assert Permission.objects.filter(code="expense_categories.manage").count() == 1
        assert Permission.objects.filter(code="inventory.view").count() == 1
    with transaction.atomic():
        set_gym_context(gym_id=other.id)
        named = Role.objects.get(gym=other, key="MANAGER")
        assert named.source_template_id is None
        assert _codes(named) == set()


def _codes(role: Role) -> set[str]:
    return set(RolePermission.objects.filter(role=role).values_list("permission__code", flat=True))
