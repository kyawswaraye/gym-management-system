from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from uuid import uuid4

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.cashbook.exceptions import CashSessionUnavailable
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import page_cash_sessions, page_cash_transactions
from apps.cashbook.services import (
    open_cash_session,
    reconcile_cash_session,
    record_cash_movement,
    record_manual_adjustment,
)
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.members.services import gym_today
from apps.tenants.models import (
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    Permission,
    Role,
    RolePermission,
)
from apps.users.member_sessions import create_member_session
from apps.users.models import User
from tests.security.test_member_auth import _linked_member
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _enable_finance(gym: Gym) -> None:
    feature, _created = Feature.objects.get_or_create(
        code="finance",
        defaults={"name": "Finance", "description": "Finance module"},
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


def _gym(code: str) -> tuple[Gym, GymMembership, dict[str, str]]:
    client, token = platform_token(f"{code.lower()}-platform@example.test")
    payload = provision(
        client,
        token,
        code=code,
        owner_email=f"{code.lower()}-owner@example.test",
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    _enable_finance(gym)
    return gym, owner, auth_headers(staff_token(owner))


def _open(
    api: APIClient,
    headers: dict[str, str],
    *,
    gym: Gym,
    register: str,
    opening: str = "100.00",
    key: str | None = None,
    request_id: str = "req_cash_open",
) -> object:
    return api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": register,
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": opening,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"cash-open-{uuid4().hex}",
        HTTP_X_REQUEST_ID=request_id,
        **headers,
    )


def _adjust(
    api: APIClient,
    headers: dict[str, str],
    *,
    gym: Gym,
    session_id: str,
    direction: str,
    amount: str,
    key: str | None = None,
    reason: str = "Counted till correction",
) -> object:
    return api.post(
        f"/api/v1/cash-sessions/{session_id}/adjustments/",
        {
            "direction": direction,
            "amount": amount,
            "currency": "THB",
            "business_date": gym_today(gym).isoformat(),
            "description": "Manual till movement",
            "reason": reason,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"cash-adjust-{uuid4().hex}",
        **headers,
    )


def _reconcile(
    api: APIClient,
    headers: dict[str, str],
    *,
    session_id: str,
    actual: str,
    reason: str = "",
    key: str | None = None,
    request_id: str = "req_cash_close",
) -> object:
    return api.post(
        f"/api/v1/cash-sessions/{session_id}/reconcile/",
        {"actual_closing": actual, "difference_reason": reason},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key or f"cash-close-{uuid4().hex}",
        HTTP_X_REQUEST_ID=request_id,
        **headers,
    )


@override_settings(**jwt_settings())
def test_cash_commands_replay_scope_and_roles() -> None:
    gym, _owner, owner_headers = _gym("CASH-A")
    other, _other_owner, other_headers = _gym("CASH-B")
    receptionist = add_staff(gym, email="cash-a-reception@example.test", role_key="RECEPTIONIST")
    other_reception = add_staff(
        gym, email="cash-a-reception-b@example.test", role_key="RECEPTIONIST"
    )
    manager = add_staff(gym, email="cash-a-manager@example.test", role_key="MANAGER")
    accountant = add_staff(gym, email="cash-a-accountant@example.test", role_key="ACCOUNTANT")
    trainer = add_staff(gym, email="cash-a-trainer@example.test", role_key="TRAINER")
    reception_headers = auth_headers(staff_token(receptionist))
    other_reception_headers = auth_headers(staff_token(other_reception))
    manager_headers = auth_headers(staff_token(manager))
    accountant_headers = auth_headers(staff_token(accountant))
    trainer_headers = auth_headers(staff_token(trainer))
    api = _client()

    assert api.get("/api/v1/cash-sessions/", **trainer_headers).status_code == 403
    denied_platform = api.get(
        "/api/v1/cash-sessions/",
        HTTP_AUTHORIZATION=f"Bearer {platform_token('cash-a-admin@example.test')[1]}",
    )
    assert denied_platform.status_code == 401
    _member_gym, _member_owner, member_user, member_id = _linked_member(
        "CASH-M", "cash-member@example.test"
    )
    member = Member.objects.get(id=member_id)
    member_token = create_member_session(
        user_id=member_user.id,
        member=member,
        device_name="cash-test",
        ip_address=None,
    ).access_token
    assert (
        api.get(
            "/api/v1/cash-sessions/",
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        ).status_code
        == 401
    )

    rejected = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "front",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "10.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="cash-open-invalid-01",
        **owner_headers,
    )
    assert rejected.status_code == 400
    assert "register_code" in rejected.json()["error"]["fields"]
    unknown = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "10.00",
            "status": "OPEN",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="cash-open-invalid-02",
        **owner_headers,
    )
    assert unknown.status_code == 400
    assert "status" in unknown.json()["error"]["fields"]

    opened = _open(api, owner_headers, gym=gym, register="FRONT", key="cash-open-front-001")
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    assert opened.json()["data"]["transactions"] == []
    assert opened.json()["data"]["session"]["currency"] == "THB"
    assert opened.json()["data"]["session"]["expected_closing"] == "100.00"
    replay = _open(api, owner_headers, gym=gym, register="FRONT", key="cash-open-front-001")
    assert replay.status_code == 201
    assert replay.content == opened.content
    mismatch = _open(
        api,
        owner_headers,
        gym=gym,
        register="FRONT",
        opening="90.00",
        key="cash-open-front-001",
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    conflict = _open(
        api,
        owner_headers,
        gym=gym,
        register="FRONT",
        key="cash-open-front-002",
        request_id="req_cash_conflict_01",
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "FINANCE_STATE_CONFLICT"
    assert conflict.json()["request_id"] == "req_cash_conflict_01"
    conflict_replay = _open(
        api,
        owner_headers,
        gym=gym,
        register="FRONT",
        key="cash-open-front-002",
        request_id="req_cash_conflict_later",
    )
    assert conflict_replay.status_code == 409
    assert conflict_replay.content == conflict.content
    assert b"req_cash_conflict_later" not in conflict_replay.content

    own = _open(api, reception_headers, gym=gym, register="REC-1", key="cash-open-rec-0001")
    assert own.status_code == 201, own.content
    own_id = own.json()["data"]["session"]["id"]
    other_open = _open(
        api, other_reception_headers, gym=gym, register="REC-2", key="cash-open-rec-0002"
    )
    assert other_open.status_code == 201, other_open.content
    other_id = other_open.json()["data"]["session"]["id"]
    listed = api.get("/api/v1/cash-sessions/", **reception_headers)
    assert listed.status_code == 200
    assert {row["id"] for row in listed.json()["data"]} == {own_id}
    hidden = api.get(f"/api/v1/cash-sessions/{other_id}/", **reception_headers)
    assert hidden.status_code == 404
    hidden_txns = api.get(
        f"/api/v1/cash-transactions/?cash_session_id={other_id}",
        **reception_headers,
    )
    assert hidden_txns.status_code == 404
    cross = api.get(f"/api/v1/cash-sessions/{own_id}/", **other_headers)
    assert cross.status_code == 404
    adjust_denied = _adjust(
        api, reception_headers, gym=gym, session_id=own_id, direction="IN", amount="1.00"
    )
    assert adjust_denied.status_code == 403
    owner_sees = api.get("/api/v1/cash-sessions/", **owner_headers)
    assert {own_id, other_id, session_id} <= {row["id"] for row in owner_sees.json()["data"]}
    assert api.get("/api/v1/cash-sessions/", **manager_headers).status_code == 200
    assert api.get(f"/api/v1/cash-sessions/{other_id}/", **accountant_headers).status_code == 200

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        custom = Role.objects.create(gym=gym, key="TILL_READER", name="Till reader")
        for code in ("cashbook.view", "cashbook.reconcile", "cashbook.adjust"):
            RolePermission.objects.create(
                gym=gym,
                role=custom,
                permission=Permission.objects.get(code=code),
            )
        reader_user = User.objects.create_user("cash-a-reader@example.test", PASSWORD)
        reader = GymMembership.objects.create(
            gym=gym,
            user=reader_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=gym_today(gym),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(gym=gym, gym_membership=reader, role=custom)
        GymMembershipRole.objects.create(gym=gym, gym_membership=receptionist, role=custom)
    reader_headers = auth_headers(staff_token(reader))
    broadened = auth_headers(staff_token(receptionist))
    assert {own_id, other_id, session_id} <= {
        row["id"] for row in api.get("/api/v1/cash-sessions/", **reader_headers).json()["data"]
    }
    still_own = api.get("/api/v1/cash-sessions/", **broadened)
    assert {row["id"] for row in still_own.json()["data"]} == {own_id}
    hidden_after_grant = api.get(f"/api/v1/cash-sessions/{other_id}/", **broadened)
    assert hidden_after_grant.status_code == 404
    hidden_reconcile = _reconcile(
        api, broadened, session_id=other_id, actual="100.00", key="cash-close-rec-other"
    )
    assert hidden_reconcile.status_code == 404
    for denied_headers in (manager_headers, accountant_headers, reader_headers):
        denied_adjust = _adjust(
            api,
            denied_headers,
            gym=gym,
            session_id=session_id,
            direction="IN",
            amount="1.00",
        )
        assert denied_adjust.status_code == 403, denied_adjust.content
        assert denied_adjust.json()["error"]["code"] == "PERMISSION_DENIED"
    reception_adjust = _adjust(
        api, broadened, gym=gym, session_id=own_id, direction="IN", amount="1.00"
    )
    assert reception_adjust.status_code == 403, reception_adjust.content
    assert reception_adjust.json()["error"]["code"] == "PERMISSION_DENIED"

    moved_in = _adjust(
        api,
        owner_headers,
        gym=gym,
        session_id=session_id,
        direction="IN",
        amount="10.10",
        key="cash-adjust-in-0001",
    )
    assert moved_in.status_code == 201, moved_in.content
    assert moved_in.json()["data"]["session"]["manual_net"] == "10.10"
    assert len(moved_in.json()["data"]["transactions"]) == 1
    same_adjust = _adjust(
        api,
        owner_headers,
        gym=gym,
        session_id=session_id,
        direction="IN",
        amount="10.10",
        key="cash-adjust-in-0001",
    )
    assert same_adjust.content == moved_in.content
    other_session_same_key = _adjust(
        api,
        owner_headers,
        gym=gym,
        session_id=own_id,
        direction="IN",
        amount="10.10",
        key="cash-adjust-in-0001",
    )
    assert other_session_same_key.status_code == 201, other_session_same_key.content
    assert other_session_same_key.json()["data"]["session"]["id"] == own_id
    moved_out = _adjust(
        api, owner_headers, gym=gym, session_id=session_id, direction="OUT", amount="0.20"
    )
    assert moved_out.status_code == 201, moved_out.content
    assert moved_out.json()["data"]["session"]["manual_net"] == "9.90"
    assert moved_out.json()["data"]["session"]["expected_closing"] == "109.90"
    wrong_currency = api.post(
        f"/api/v1/cash-sessions/{session_id}/adjustments/",
        {
            "direction": "IN",
            "amount": "1.00",
            "currency": "USD",
            "business_date": gym_today(gym).isoformat(),
            "description": "Wrong",
            "reason": "Wrong currency",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="cash-adjust-currency1",
        **owner_headers,
    )
    assert wrong_currency.status_code == 400
    missing_reason = _reconcile(
        api,
        owner_headers,
        session_id=session_id,
        actual="100.00",
        key="cash-close-reason-01",
        request_id="req_cash_reason_01",
    )
    assert missing_reason.status_code == 400
    retry_reason = _reconcile(
        api,
        owner_headers,
        session_id=session_id,
        actual="100.00",
        key="cash-close-reason-01",
        request_id="req_cash_reason_02",
    )
    assert retry_reason.status_code == 400
    assert retry_reason.json()["request_id"] == "req_cash_reason_02"
    closed = _reconcile(
        api,
        owner_headers,
        session_id=session_id,
        actual="100.00",
        reason="Drawer was short",
        key="cash-close-front-001",
    )
    assert closed.status_code == 200, closed.content
    assert closed.json()["data"]["transactions"] == []
    assert closed.json()["data"]["session"]["status"] == "CLOSED"
    assert closed.json()["data"]["session"]["difference"] == "-9.90"
    assert closed.json()["data"]["session"]["cash_sales"] == "0.00"
    late = _adjust(
        api, owner_headers, gym=gym, session_id=session_id, direction="IN", amount="1.00"
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "FINANCE_STATE_CONFLICT"
    own_close = _reconcile(
        api,
        reception_headers,
        session_id=own_id,
        actual="110.10",
        key="cash-close-rec-0001",
    )
    assert own_close.status_code == 200, own_close.content
    broadened_txns = api.get(
        f"/api/v1/cash-transactions/?cash_session_id={other_id}",
        **broadened,
    )
    assert broadened_txns.status_code == 404
    visible_txns = api.get(
        f"/api/v1/cash-transactions/?cash_session_id={session_id}",
        **owner_headers,
    )
    assert visible_txns.status_code == 200
    assert {row["direction"] for row in visible_txns.json()["data"]} == {"IN", "OUT"}
    filtered = api.get(
        "/api/v1/cash-sessions/?status=OPEN&register_code=REC-2",
        **owner_headers,
    )
    assert filtered.json()["data"][0]["id"] == other_id
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        actions = set(
            AuditLog.objects.filter(gym=gym, module="cashbook").values_list("action", flat=True)
        )
    assert actions == {
        "CASH_SESSION_OPENED",
        "CASH_MANUAL_ADJUSTMENT_RECORDED",
        "CASH_SESSION_RECONCILED",
    }
    assert other.id != gym.id


@override_settings(**jwt_settings())
def test_cash_database_guards_races_and_recompute() -> None:
    connections.close_all()
    gym, owner, owner_headers = _gym("CASH-C")
    other, other_owner, _other_headers = _gym("CASH-D")
    api = _client()
    opened = _open(api, owner_headers, gym=gym, register="MAIN", opening="50.00")
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    adjusted = _adjust(
        api, owner_headers, gym=gym, session_id=session_id, direction="IN", amount="2.50"
    )
    assert adjusted.status_code == 201, adjusted.content
    movement_id = adjusted.json()["data"]["transactions"][0]["id"]

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conname IN (
              'cashsession_opened_by_same_gym_fk',
              'cashsession_closed_by_same_gym_fk',
              'cashtxn_session_same_gym_fk',
              'cashtxn_actor_same_gym_fk',
              'cashtxn_reversal_same_gym_fk'
            )
            """
        )
        assert {row[0] for row in cursor.fetchall()} == {
            "cashsession_opened_by_same_gym_fk",
            "cashsession_closed_by_same_gym_fk",
            "cashtxn_session_same_gym_fk",
            "cashtxn_actor_same_gym_fk",
            "cashtxn_reversal_same_gym_fk",
        }

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', '', true)")
            cursor.execute("SELECT count(*) FROM cashbook_cash_session")
            assert cursor.fetchone() == (0,)
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(
                    """
                    INSERT INTO cashbook_cash_session (
                      id, created_at, updated_at, business_date, register_code, currency,
                      opening_cash, cash_sales, cash_expenses, manual_net, expected_closing,
                      difference_reason, status, gym_id, opened_by_membership_id
                    ) VALUES (
                      gen_random_uuid(), now(), now(), CURRENT_DATE, 'X', 'THB',
                      1, 0, 0, 0, 1, '', 'OPEN', %s, %s
                    )
                    """,
                    [gym.id, owner.id],
                )
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other.id)])
            cursor.execute("SELECT count(*) FROM cashbook_cash_session")
            assert cursor.fetchone() == (0,)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute(
                    "UPDATE cashbook_cash_transaction SET description = 'changed' WHERE id = %s",
                    [movement_id],
                )
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute("DELETE FROM cashbook_cash_session WHERE id = %s", [session_id])

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE cashbook_cash_transaction SET description = 'changed' WHERE id = %s",
                    [movement_id],
                )
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE cashbook_cash_session SET register_code = 'OTHER' WHERE id = %s",
                    [session_id],
                )
        with (
            pytest.raises(DatabaseError, match="cash session cannot be deleted"),
            transaction.atomic(),
        ):
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM cashbook_cash_session WHERE id = %s", [session_id])
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE cashbook_cash_session
                    SET opened_by_membership_id = %s
                    WHERE id = %s
                    """,
                    [other_owner.id, session_id],
                )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE cashbook_cash_session DISABLE TRIGGER cashsession_guard_trg"
            )
            cursor.execute(
                "ALTER TABLE cashbook_cash_session DISABLE TRIGGER cashsession_totals_commit_trg"
            )
            cursor.execute(
                """
                UPDATE cashbook_cash_session
                SET cash_sales = 999,
                    expected_closing = opening_cash + 999 - cash_expenses + manual_net
                WHERE id = %s
                """,
                [session_id],
            )
            cursor.execute(
                "ALTER TABLE cashbook_cash_session ENABLE TRIGGER cashsession_totals_commit_trg"
            )
            cursor.execute("ALTER TABLE cashbook_cash_session ENABLE TRIGGER cashsession_guard_trg")
    recomputed = _reconcile(
        api,
        owner_headers,
        session_id=session_id,
        actual="52.50",
        key="cash-close-recompute1",
    )
    assert recomputed.status_code == 200, recomputed.content
    assert recomputed.json()["data"]["session"]["cash_sales"] == "0.00"
    assert recomputed.json()["data"]["session"]["manual_net"] == "2.50"
    assert recomputed.json()["data"]["session"]["expected_closing"] == "52.50"
    assert recomputed.json()["data"]["session"]["difference"] == "0.00"

    with pytest.raises(RuntimeError, match="stop"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            open_cash_session(
                gym=gym,
                actor=owner,
                register_code="ROLLBACK",
                business_date=gym_today(gym),
                opening_cash=Decimal("1.00"),
                request_id="req_cash_rollback",
                ip_address=None,
                user_agent="",
            )
            raise RuntimeError("stop")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not CashSession.objects.filter(gym=gym, register_code="ROLLBACK").exists()
        assert not AuditLog.objects.filter(gym=gym, request_id="req_cash_rollback").exists()

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        before = len(
            page_cash_sessions(
                gym_id=gym.id,
                actor=owner,
                status="",
                register_code="",
                business_date=None,
                limit=1,
                after="",
            )[0]
        )
        with CaptureQueriesContext(connection) as small:
            page_cash_sessions(
                gym_id=gym.id,
                actor=owner,
                status="",
                register_code="",
                business_date=None,
                limit=1,
                after="",
            )
    for index in range(3):
        extra = _open(api, owner_headers, gym=gym, register=f"PAGE{index}", opening="1.00")
        assert extra.status_code == 201, extra.content
        closed = _reconcile(
            api,
            owner_headers,
            session_id=extra.json()["data"]["session"]["id"],
            actual="1.00",
        )
        assert closed.status_code == 200, closed.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        with CaptureQueriesContext(connection) as large:
            page_cash_sessions(
                gym_id=gym.id,
                actor=owner,
                status="",
                register_code="",
                business_date=None,
                limit=1,
                after="",
            )
    assert before >= 0
    assert len(large.captured_queries) == len(small.captured_queries) <= 3

    race_gym, race_owner, race_headers = _gym("CASH-R")
    race_api = _client()
    barrier = Barrier(2, timeout=20)

    def open_once(key: str) -> int:
        connections.close_all()
        client = _client()
        try:
            barrier.wait()
            response = _open(client, race_headers, gym=race_gym, register="RACE", key=key)
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(
            future.result()
            for future in (
                pool.submit(open_once, "cash-race-open-0001"),
                pool.submit(open_once, "cash-race-open-0002"),
            )
        )
    assert statuses == [201, 409]
    with transaction.atomic():
        set_gym_context(gym_id=race_gym.id)
        race_session = CashSession.objects.get(gym=race_gym, register_code="RACE", status="OPEN")
    same_key = Barrier(2, timeout=20)

    def open_same() -> str:
        connections.close_all()
        client = _client()
        try:
            same_key.wait()
            response = _open(
                client,
                race_headers,
                gym=race_gym,
                register="TWIN",
                key="cash-race-same-0001",
            )
            for _attempt in range(20):
                if response.status_code != 409:
                    break
                if response.json()["error"]["code"] != "IDEMPOTENCY_IN_PROGRESS":
                    break
                time.sleep(0.05)
                response = _open(
                    client,
                    race_headers,
                    gym=race_gym,
                    register="TWIN",
                    key="cash-race-same-0001",
                )
            assert response.status_code == 201, response.content
            return response.json()["data"]["session"]["id"]
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        twin_ids = {future.result() for future in (pool.submit(open_same), pool.submit(open_same))}
    assert len(twin_ids) == 1

    close_barrier = Barrier(2, timeout=20)

    def close_once(key: str) -> int:
        connections.close_all()
        client = _client()
        try:
            close_barrier.wait()
            response = _reconcile(
                client,
                race_headers,
                session_id=str(race_session.id),
                actual="100.00",
                key=key,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        close_statuses = sorted(
            future.result()
            for future in (
                pool.submit(close_once, "cash-race-close-001"),
                pool.submit(close_once, "cash-race-close-002"),
            )
        )
    assert close_statuses == [200, 409]
    with transaction.atomic():
        set_gym_context(gym_id=race_gym.id)
        assert CashSession.objects.get(id=race_session.id).status == "CLOSED"

    fresh = _open(race_api, race_headers, gym=race_gym, register="RACE", opening="20.00")
    assert fresh.status_code == 201, fresh.content
    fresh_id = fresh.json()["data"]["session"]["id"]
    hold = Event()
    release = Event()

    def adjust_under_lock() -> str:
        connections.close_all()
        try:
            with transaction.atomic():
                set_gym_context(gym_id=race_gym.id)
                CashSession.objects.select_for_update().get(id=fresh_id)
                hold.set()
                assert release.wait(10)
                record_manual_adjustment(
                    gym=race_gym,
                    actor=race_owner,
                    session_id=fresh_id,
                    direction="IN",
                    amount=Decimal("3.00"),
                    currency="THB",
                    business_date=gym_today(race_gym),
                    description="Race",
                    reason="Included before close",
                    request_id="req_cash_race_adjust",
                    ip_address=None,
                    user_agent="",
                )
            return "adjusted"
        except CashSessionUnavailable:
            return "rejected"
        finally:
            connections.close_all()

    def close_after_lock() -> str:
        connections.close_all()
        try:
            assert hold.wait(10)
            with transaction.atomic():
                set_gym_context(gym_id=race_gym.id)
                reconcile_cash_session(
                    gym=race_gym,
                    actor=race_owner,
                    session_id=fresh_id,
                    actual_closing=Decimal("23.00"),
                    difference_reason="",
                    request_id="req_cash_race_close",
                    ip_address=None,
                    user_agent="",
                )
            return "closed"
        except CashSessionUnavailable:
            return "rejected"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        adjust_future = pool.submit(adjust_under_lock)
        close_future = pool.submit(close_after_lock)
        assert hold.wait(10)
        release.set()
        assert adjust_future.result() == "adjusted"
        assert close_future.result() == "closed"
    with transaction.atomic():
        set_gym_context(gym_id=race_gym.id)
        closed_session = CashSession.objects.get(id=fresh_id)
        movements = list(CashTransaction.objects.filter(cash_session=closed_session))
    assert closed_session.status == "CLOSED"
    assert closed_session.manual_net == Decimal("3.00")
    assert closed_session.expected_closing == Decimal("23.00")
    assert len(movements) == 1
    assert movements[0].occurred_at <= closed_session.closed_at


def _throttle_framework(rate: str) -> dict[str, object]:
    framework = {**settings.REST_FRAMEWORK}
    framework["DEFAULT_THROTTLE_RATES"] = {
        **settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "cashbook_command": rate,
    }
    return framework


def _payment(
    *,
    gym: Gym,
    actor: GymMembership,
    session_id: object,
    amount: str,
    source_id: object,
) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        record_cash_movement(
            gym=gym,
            actor=actor,
            session_id=session_id,  # type: ignore[arg-type]
            direction=CashTransaction.Direction.IN,
            source_type=CashTransaction.SourceType.PAYMENT,
            source_id=source_id,  # type: ignore[arg-type]
            amount=Decimal(amount),
            currency=gym.currency,
            business_date=gym_today(gym),
            description="Posted payment",
            reason="",
            reversal_of_id=None,
        )


@override_settings(**jwt_settings())
def test_cash_adjustment_authority_and_live_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    gym, owner, owner_headers = _gym("CASH-AUTH")
    api = _client()
    with pytest.raises(RuntimeError, match="active transaction"):
        record_cash_movement(
            gym=gym,
            actor=owner,
            session_id=uuid4(),
            direction="IN",
            source_type="MANUAL",
            source_id=None,
            amount=Decimal("1.00"),
            currency="THB",
            business_date=gym_today(gym),
            description="",
            reason="Outside a transaction",
            reversal_of_id=None,
        )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        opener_role = Role.objects.create(gym=gym, key="CASH_OPENER", name="Cash opener")
        RolePermission.objects.create(
            gym=gym,
            role=opener_role,
            permission=Permission.objects.get(code="cashbook.open"),
        )
        opener_user = User.objects.create_user("cash-auth-opener@example.test", PASSWORD)
        opener = GymMembership.objects.create(
            gym=gym,
            user=opener_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=gym_today(gym),
            activated_at=timezone.now(),
        )
        GymMembershipRole.objects.create(gym=gym, gym_membership=opener, role=opener_role)
    opener_headers = auth_headers(staff_token(opener))
    from apps.cashbook import services as cash_services

    original = cash_services.revalidate_cash_actor
    mode = {"value": ""}

    def revoke_then_revalidate(**kwargs: object) -> tuple[Gym, GymMembership]:
        current = mode["value"]
        mode["value"] = ""
        if current == "permission":
            RolePermission.objects.filter(
                gym_id=gym.id,
                role_id=opener_role.id,
                permission__code="cashbook.open",
            ).delete()
        elif current == "membership":
            GymMembership.objects.filter(id=opener.id).update(status=GymMembership.Status.SUSPENDED)
        elif current == "entitlement":
            GymFeatureEntitlement.objects.filter(gym_id=gym.id, feature__code="finance").update(
                enabled=False
            )
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cash_services, "revalidate_cash_actor", revoke_then_revalidate)
    for current, register in (
        ("permission", "LIVE1"),
        ("membership", "LIVE2"),
        ("entitlement", "LIVE3"),
    ):
        mode["value"] = current
        key = f"cash-live-{current}-001"
        denied = _open(
            api,
            opener_headers,
            gym=gym,
            register=register,
            key=key,
            request_id=f"req_cash_live_{current}",
        )
        assert denied.status_code == 403, denied.content
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            assert not CashSession.objects.filter(gym=gym, register_code=register).exists()
            assert not AuditLog.objects.filter(
                gym=gym, request_id=f"req_cash_live_{current}"
            ).exists()
            claim = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
            assert claim.state == IdempotencyRecord.State.IN_PROGRESS
            assert claim.response_status is None
        opened = _open(
            api,
            opener_headers,
            gym=gym,
            register=register,
            key=key,
            request_id=f"req_cash_live_{current}_retry",
        )
        assert opened.status_code == 201, opened.content

    cache.clear()
    with override_settings(REST_FRAMEWORK=_throttle_framework("1/60")):
        api_settings.reload()
        first = _open(
            api,
            owner_headers,
            gym=gym,
            register="THROTTLE",
            key="cash-throttle-open01",
            request_id="req_cash_throttle_1",
        )
        assert first.status_code == 201, first.content
        limited = _open(
            api,
            owner_headers,
            gym=gym,
            register="THROTTLE",
            key="cash-throttle-open02",
            request_id="req_cash_throttle_2",
        )
    api_settings.reload()
    cache.clear()
    assert limited.status_code == 429, limited.content
    envelope = limited.json()
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "THROTTLED"
    assert envelope["error"]["message"]
    assert envelope["error"]["fields"] == {}
    assert envelope["request_id"] == "req_cash_throttle_2"


@override_settings(**jwt_settings())
def test_cash_movement_lock_commit_totals_and_list_queries() -> None:
    gym, owner, owner_headers = _gym("CASH-MOVE")
    receptionist = add_staff(gym, email="cash-move-reception@example.test", role_key="RECEPTIONIST")
    reception_headers = auth_headers(staff_token(receptionist))
    api = _client()
    opened = _open(api, owner_headers, gym=gym, register="MOVE", opening="10.00")
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    own = _open(api, reception_headers, gym=gym, register="MOVE-R", opening="5.00")
    assert own.status_code == 201, own.content
    own_id = own.json()["data"]["session"]["id"]

    with pytest.raises(DatabaseError, match="cashtxn_shape_ck"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute(
                    """
                    INSERT INTO cashbook_cash_transaction (
                      id, created_at, updated_at, direction, source_type, source_id, amount,
                      currency, business_date, occurred_at, description, reason,
                      cash_session_id, created_by_membership_id, gym_id
                    )
                    SELECT gen_random_uuid(), now(), now(), 'IN', 'PAYMENT', NULL, 1.00,
                           currency, business_date, now(), '', '', id, %s, gym_id
                    FROM cashbook_cash_session
                    WHERE id = %s
                    """,
                    [owner.id, session_id],
                )
    with pytest.raises(DatabaseError, match="totals do not match"):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute(
                    """
                    INSERT INTO cashbook_cash_transaction (
                      id, created_at, updated_at, direction, source_type, source_id, amount,
                      currency, business_date, occurred_at, description, reason,
                      cash_session_id, created_by_membership_id, gym_id
                    )
                    SELECT gen_random_uuid(), now(), now(), 'IN', 'PAYMENT', gen_random_uuid(),
                           4.00, currency, business_date, now(), '', '', id, %s, gym_id
                    FROM cashbook_cash_session
                    WHERE id = %s
                    """,
                    [owner.id, session_id],
                )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert CashTransaction.objects.filter(cash_session_id=session_id).count() == 0
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
            cursor.execute(
                """
                INSERT INTO cashbook_cash_transaction (
                  id, created_at, updated_at, direction, source_type, source_id, amount,
                  currency, business_date, occurred_at, description, reason,
                  cash_session_id, created_by_membership_id, gym_id
                )
                SELECT gen_random_uuid(), now(), now(), 'IN', 'PAYMENT', gen_random_uuid(),
                       4.00, currency, business_date, now(), '', '', id, %s, gym_id
                FROM cashbook_cash_session
                WHERE id = %s
                """,
                [owner.id, session_id],
            )
            cursor.execute(
                """
                UPDATE cashbook_cash_session
                SET cash_sales = 4.00,
                    expected_closing = opening_cash + 4.00 - cash_expenses + manual_net
                WHERE id = %s
                """,
                [session_id],
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        synced = CashSession.objects.get(id=session_id)
    assert synced.cash_sales == Decimal("4.00")
    assert synced.expected_closing == Decimal("14.00")

    barrier = Barrier(2, timeout=20)

    def pay(amount: str) -> None:
        connections.close_all()
        try:
            barrier.wait()
            _payment(
                gym=gym,
                actor=owner,
                session_id=session_id,
                amount=amount,
                source_id=uuid4(),
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pay, "1.10"), pool.submit(pay, "2.20")]
        for future in futures:
            future.result()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        combined = CashSession.objects.get(id=session_id)
    assert combined.cash_sales == Decimal("7.30")
    assert combined.cash_expenses == Decimal("0.00")
    assert combined.manual_net == Decimal("0.00")
    assert combined.expected_closing == Decimal("17.30")

    held = Event()
    helper_entered = Event()

    def close_while_helper_waits() -> str:
        connections.close_all()
        try:
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                CashSession.objects.select_for_update().get(id=own_id)
                held.set()
                assert helper_entered.wait(10)
                time.sleep(0.5)
                reconcile_cash_session(
                    gym=gym,
                    actor=receptionist,
                    session_id=own_id,
                    actual_closing=Decimal("5.00"),
                    difference_reason="",
                    request_id="req_cash_helper_close",
                    ip_address=None,
                    user_agent="",
                )
            return "closed"
        finally:
            connections.close_all()

    def helper_after_close_starts() -> str:
        connections.close_all()
        try:
            assert held.wait(10)
            helper_entered.set()
            try:
                _payment(
                    gym=gym,
                    actor=owner,
                    session_id=own_id,
                    amount="1.00",
                    source_id=uuid4(),
                )
            except CashSessionUnavailable:
                return "rejected"
            except Exception as exc:
                return f"error:{type(exc).__name__}:{exc}"
            return "moved"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        close_future = pool.submit(close_while_helper_waits)
        helper_future = pool.submit(helper_after_close_starts)
        close_result = close_future.result()
        helper_result = helper_future.result()
    assert (close_result, helper_result) == ("closed", "rejected"), (close_result, helper_result)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        closed_own = CashSession.objects.get(id=own_id)
        assert closed_own.status == "CLOSED"
        assert closed_own.cash_sales == Decimal("0.00")
        assert CashTransaction.objects.filter(cash_session_id=own_id).count() == 0

    def transaction_queries(**filters: object) -> int:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with CaptureQueriesContext(connection) as captured:
                page_cash_transactions(
                    gym_id=gym.id,
                    actor=filters["actor"],  # type: ignore[arg-type]
                    cash_session_id=filters.get("cash_session_id"),  # type: ignore[arg-type]
                    business_date=filters.get("business_date"),  # type: ignore[arg-type]
                    source_type=str(filters.get("source_type", "")),
                    direction=str(filters.get("direction", "")),
                    limit=10,
                    after="",
                )
        return len(captured.captured_queries)

    owner_filters = {
        "actor": owner,
        "cash_session_id": combined.id,
        "business_date": gym_today(gym),
        "source_type": "PAYMENT",
        "direction": "IN",
    }
    reception_filters = {"actor": receptionist}
    before_owner = transaction_queries(**owner_filters)
    before_reception = transaction_queries(**reception_filters)
    for _index in range(3):
        _payment(
            gym=gym,
            actor=owner,
            session_id=session_id,
            amount="0.10",
            source_id=uuid4(),
        )
    after_owner = transaction_queries(**owner_filters)
    after_reception = transaction_queries(**reception_filters)
    assert before_owner == after_owner <= 8
    assert before_reception == after_reception <= 8
