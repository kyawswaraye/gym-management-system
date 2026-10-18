from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.cashbook.models import CashSession, CashTransaction
from apps.common.db.tenant_context import set_gym_context
from apps.common.finance.idempotency import canonical_request_hash
from apps.members import payment_services
from apps.members.models import Member, Payment, Receipt
from apps.members.payment_services import command_route
from apps.members.services import gym_today
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    IdempotencyRecord,
    TenantSequence,
)
from tests.security.test_payments import (
    _client,
    _draft,
    _enable,
    _gym,
    _member,
    _membership,
    _name,
    _receipt_for,
)
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
    staff_token,
)

_KEEP = object()

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _submit(api, headers, payment_id: str) -> object:
    return api.post(
        f"/api/v1/payments/{payment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-submit-{uuid4().hex}",
        **headers,
    )


def _approve(api, headers, payment_id: str, *, key: str, body: dict | None = None) -> object:
    return api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        body or {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **headers,
    )


def _pending_bank(api, headers, gym, member_id: str, membership_id: str, amount: str) -> str:
    created = _draft(
        api,
        headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method="BANK_TRANSFER",
        amount=amount,
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    submitted = _submit(api, headers, payment_id)
    assert submitted.status_code == 200, submitted.content
    return str(payment_id)


@override_settings(**jwt_settings())
def test_approval_sod_snapshots_lists_and_idempotency() -> None:
    api, gym, owner, headers = _gym("PAYCTL")
    receptionist = add_staff(gym, email="payctl-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="payctl-manager@example.test", role_key="MANAGER")
    other_manager = add_staff(gym, email="payctl-manager-2@example.test", role_key="MANAGER")
    accountant = add_staff(gym, email="payctl-accountant@example.test", role_key="ACCOUNTANT")
    _name(manager, "Mina", "Manager")
    _name(other_manager, "Omar", "Manager")
    _name(owner, "Olivia", "Owner")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)

    created = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        method="BANK_TRANSFER",
        amount="10.00",
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    updated = api.patch(
        f"/api/v1/payments/{payment_id}/",
        {"description": "Corrected dues"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-update-{uuid4().hex}",
        **reception_headers,
    )
    assert updated.status_code == 200, updated.content
    assert updated.json()["data"]["description"] == "Corrected dues"
    missing_key = api.patch(
        f"/api/v1/payments/{payment_id}/",
        {"description": "Again"},
        format="json",
        **reception_headers,
    )
    assert missing_key.status_code == 400
    submitted = _submit(api, manager_headers, payment_id)
    assert submitted.status_code == 200, submitted.content

    accountant_denied = _approve(
        api,
        auth_headers(staff_token(accountant)),
        payment_id,
        key=f"pay-acct-{uuid4().hex}",
    )
    assert accountant_denied.status_code == 403
    submitter_denied = _approve(
        api, manager_headers, payment_id, key=f"pay-submitter-{uuid4().hex}"
    )
    assert submitter_denied.status_code == 403
    owner_self = _pending_bank(api, headers, gym, member_id, membership_id, "5.00")
    self_denied = _approve(api, headers, owner_self, key=f"pay-owner-self-{uuid4().hex}")
    assert self_denied.status_code == 403

    stale_id = _pending_bank(api, headers, gym, member_id, membership_id, "5.00")
    stale_key = "pay-control-stale-001"
    stale_route = command_route(payment_id=UUID(stale_id), action="approve")
    stale_hash = canonical_request_hash(
        gym_id=gym.id,
        actor_membership_id=other_manager.id,
        http_method="POST",
        route=stale_route,
        body={"payment_id": stale_id, "cash_session_id": None},
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        claim = IdempotencyRecord.objects.create(
            gym=gym,
            actor_membership_id=other_manager.id,
            http_method="POST",
            route=stale_route,
            idempotency_key=stale_key,
            request_hash=stale_hash,
            state=IdempotencyRecord.State.IN_PROGRESS,
            locked_until=timezone.now() + timedelta(minutes=5),
            expires_at=timezone.now() + timedelta(hours=1),
            created_by_membership_id=other_manager.id,
        )
    busy = _approve(api, auth_headers(staff_token(other_manager)), stale_id, key=stale_key)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        claim.locked_until = timezone.now() - timedelta(seconds=5)
        claim.save(update_fields=["locked_until", "updated_at"])
    reclaimed = _approve(api, auth_headers(staff_token(other_manager)), stale_id, key=stale_key)
    assert reclaimed.status_code == 200, reclaimed.content

    key = "pay-control-approve-01"
    mismatched_body = {"note": "changed"}
    approved = _approve(api, auth_headers(staff_token(other_manager)), payment_id, key=key)
    assert approved.status_code == 200, approved.content
    replay = _approve(api, auth_headers(staff_token(other_manager)), payment_id, key=key)
    assert replay.content == approved.content
    mismatch = api.post(
        f"/api/v1/payments/{payment_id}/approve/",
        {"cash_session_id": str(uuid4())},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **auth_headers(staff_token(other_manager)),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    other_actor = _approve(api, manager_headers, payment_id, key=key)
    assert other_actor.status_code == 409
    assert other_actor.json()["error"]["code"] == "FINANCE_STATE_CONFLICT"
    assert mismatched_body["note"] == "changed"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        receipt = Receipt.objects.get(payment_id=payment_id)
        original_name = receipt.member_name
        original_gym = receipt.gym_name
        Member.objects.filter(id=member_id).update(first_name="Changed")
        Gym.objects.filter(id=gym.id).update(name="Changed Gym")
        receipt.refresh_from_db()
        assert receipt.member_name == original_name
        assert receipt.gym_name == original_gym
    download = api.get(f"/api/v1/receipts/{receipt.id}/download/", **headers)
    assert download.status_code == 200
    assert original_name.encode() in download.content
    assert b"Changed Gym" not in download.content

    second = _pending_bank(api, headers, gym, member_id, membership_id, "15.00")
    second_approved = _approve(api, manager_headers, second, key=f"pay-second-{uuid4().hex}")
    assert second_approved.status_code == 200, second_approved.content
    with CaptureQueriesContext(connection) as first_list:
        listed = api.get("/api/v1/payments/?limit=1", **headers)
    with CaptureQueriesContext(connection) as second_list:
        followed = api.get(
            f"/api/v1/payments/?limit=1&after={listed.json()['meta']['next_after']}",
            **headers,
        )
    assert listed.status_code == 200, listed.content
    assert followed.status_code == 200, followed.content
    assert listed.json()["meta"]["has_more"] is True
    assert len(first_list) == len(second_list)
    stolen = api.get(
        f"/api/v1/payments/?limit=1&after={listed.json()['meta']['next_after']}",
        **manager_headers,
    )
    assert stolen.status_code == 400
    with CaptureQueriesContext(connection) as receipt_list:
        receipts = api.get("/api/v1/receipts/?limit=1", **headers)
    with CaptureQueriesContext(connection) as receipt_again:
        receipts_again = api.get("/api/v1/receipts/?limit=2", **headers)
    assert receipts.status_code == 200, receipts.content
    assert receipts_again.status_code == 200, receipts_again.content
    assert len(receipt_list) == len(receipt_again)
    extra = api.get("/api/v1/sales/daily/", **headers)
    assert extra.status_code == 400
    unknown = api.get(
        f"/api/v1/sales/daily/?date={gym_today(gym).isoformat()}&gym_id={gym.id}",
        **headers,
    )
    assert unknown.status_code == 400


@override_settings(**jwt_settings())
def test_live_recheck_and_rollback_do_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    api, gym, owner, headers = _gym("PAYLIVE")
    manager = add_staff(gym, email="paylive-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    _name(owner, "Olivia", "Owner")
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    original = payment_services.revalidate_cash_actor
    mode = {"value": ""}

    def revoke_then_revalidate(**kwargs: object) -> tuple[Gym, GymMembership]:
        current = mode["value"]
        mode["value"] = ""
        if current == "permission":
            GymMembershipRole.objects.filter(gym_membership_id=manager.id).delete()
        elif current == "membership":
            GymMembership.objects.filter(id=manager.id).update(
                status=GymMembership.Status.SUSPENDED
            )
        elif current == "entitlement":
            GymFeatureEntitlement.objects.filter(gym_id=gym.id, feature__code="finance").update(
                enabled=False
            )
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(payment_services, "revalidate_cash_actor", revoke_then_revalidate)
    for current in ("permission", "membership", "entitlement"):
        payment_id = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
        mode["value"] = current
        key = f"pay-live-{current}-01"
        denied = _approve(
            api,
            manager_headers,
            payment_id,
            key=key,
        )
        assert denied.status_code == 403, denied.content
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            assert Payment.objects.get(id=payment_id).status == "PENDING_APPROVAL"
            assert not Receipt.objects.filter(payment_id=payment_id).exists()
            claim = IdempotencyRecord.objects.get(gym=gym, idempotency_key=key)
            assert claim.state == IdempotencyRecord.State.IN_PROGRESS
        approved = _approve(api, manager_headers, payment_id, key=key)
        assert approved.status_code == 200, approved.content

    payment_id = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        before_value = TenantSequence.objects.get(
            gym=gym, sequence_key__startswith="RECEIPT:"
        ).next_value
    issuer = payment_services._issue_receipt

    def fail_after_number(**kwargs: object) -> None:
        raise ValidationError({"issuer": ["stop"]})

    monkeypatch.setattr(payment_services, "_issue_receipt", fail_after_number)
    key = "pay-live-rollback-01"
    failed = _approve(api, manager_headers, payment_id, key=key)
    assert failed.status_code == 400, failed.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Payment.objects.get(id=payment_id).status == "PENDING_APPROVAL"
        assert not Receipt.objects.filter(gym=gym, payment_id=payment_id).exists()
        assert (
            TenantSequence.objects.get(gym=gym, sequence_key__startswith="RECEIPT:").next_value
            == before_value
        )
        assert not AuditLog.objects.filter(
            gym=gym, action="PAYMENT_APPROVED_POSTED", target_id=payment_id
        ).exists()
    monkeypatch.setattr(payment_services, "_issue_receipt", issuer)
    posted = _approve(api, manager_headers, payment_id, key=key)
    assert posted.status_code == 200, posted.content
    assert posted.json()["data"]["receipt"]["receipt_number"].endswith(f"-{before_value:06d}")


@override_settings(**jwt_settings())
def test_concurrent_approval_allocation_and_overpay() -> None:
    api, gym, owner, headers = _gym("PAYRACE")
    manager = add_staff(gym, email="payrace-manager@example.test", role_key="MANAGER")
    other = add_staff(gym, email="payrace-manager-2@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    _name(other, "Omar", "Manager")
    _name(owner, "Olivia", "Owner")
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    shared = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    left = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    right = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    barrier = Barrier(4, timeout=20)

    def approve(actor_headers: dict[str, str], payment_id: str, key: str) -> int:
        connections.close_all()
        client = _client()
        try:
            barrier.wait()
            response = _approve(client, actor_headers, payment_id, key=key)
            return response.status_code
        finally:
            connections.close_all()

    manager_headers = auth_headers(staff_token(manager))
    other_headers = auth_headers(staff_token(other))
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = [
            future.result()
            for future in (
                pool.submit(approve, manager_headers, shared, "pay-race-shared-0001"),
                pool.submit(approve, other_headers, shared, "pay-race-shared-0002"),
                pool.submit(approve, manager_headers, left, "pay-race-left-00001"),
                pool.submit(approve, other_headers, right, "pay-race-right-0001"),
            )
        ]
    assert sorted(statuses) == [200, 200, 200, 409]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Payment.objects.get(id=shared).status == "POSTED"
        numbers = list(
            Receipt.objects.filter(gym=gym, status="ISSUED")
            .order_by("sequence_value")
            .values_list("receipt_number", "sequence_value")
        )
    assert len(numbers) == 3
    assert [value for _number, value in numbers] == [1, 2, 3]

    heavy_a = _pending_bank(api, headers, gym, member_id, membership_id, "60.00")
    heavy_b = _pending_bank(api, headers, gym, member_id, membership_id, "60.00")
    over_barrier = Barrier(2, timeout=20)

    def approve_heavy(payment_id: str, key: str) -> int:
        connections.close_all()
        client = _client()
        try:
            over_barrier.wait()
            response = _approve(client, manager_headers, payment_id, key=key)
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        heavy = sorted(
            future.result()
            for future in (
                pool.submit(approve_heavy, heavy_a, "pay-race-heavy-0001"),
                pool.submit(approve_heavy, heavy_b, "pay-race-heavy-0002"),
            )
        )
    assert heavy == [200, 409]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        posted = Payment.objects.filter(gym=gym, status="POSTED", membership_id=membership_id)
        assert sum(row.amount for row in posted) <= Decimal("100.00")


@override_settings(**jwt_settings())
def test_cash_close_race_and_database_guards() -> None:
    api, gym, owner, headers = _gym("PAYDB")
    other_api, other_gym, other_owner, _other_headers = _gym("PAYDB2")
    _enable(other_gym, "finance")
    receptionist = add_staff(gym, email="paydb-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paydb-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    _name(owner, "Olivia", "Owner")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="20.00",
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    assert _submit(api, reception_headers, payment_id).status_code == 200
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "20.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-db-open-{uuid4().hex}",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    bare = _approve(api, manager_headers, payment_id, key="pay-db-nocash-0001")
    assert bare.status_code == 400, bare.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        bare_claim = IdempotencyRecord.objects.get(gym=gym, idempotency_key="pay-db-nocash-0001")
        assert bare_claim.state == IdempotencyRecord.State.IN_PROGRESS
        assert bare_claim.response_status is None
    repaired = _approve(
        api,
        manager_headers,
        payment_id,
        key="pay-db-withcash-0001",
        body={"cash_session_id": session_id},
    )
    assert repaired.status_code == 200, repaired.content

    second = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="5.00",
    )
    second_id = second.json()["data"]["id"]
    assert _submit(api, reception_headers, second_id).status_code == 200
    barrier = Barrier(2, timeout=20)

    def approve_cash() -> int:
        connections.close_all()
        client = _client()
        try:
            barrier.wait()
            response = _approve(
                client,
                manager_headers,
                second_id,
                key="pay-db-race-approve1",
                body={"cash_session_id": session_id},
            )
            return response.status_code
        finally:
            connections.close_all()

    def close_session() -> int:
        connections.close_all()
        client = _client()
        try:
            barrier.wait()
            response = client.post(
                f"/api/v1/cash-sessions/{session_id}/reconcile/",
                {"actual_closing": "20.00", "difference_reason": ""},
                format="json",
                HTTP_IDEMPOTENCY_KEY="pay-db-race-close-01",
                **reception_headers,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        approve_future = pool.submit(approve_cash)
        close_future = pool.submit(close_session)
        approve_status = approve_future.result()
        close_status = close_future.result()
    assert approve_status in {200, 400, 409}
    assert close_status in {200, 400, 409}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.get(id=second_id)
        movements = CashTransaction.objects.filter(
            gym=gym,
            source_type="PAYMENT",
            source_id=second_id,
        ).count()
        receipts = Receipt.objects.filter(payment_id=second_id, status="ISSUED").count()
        if payment.status == "POSTED":
            assert movements == 1
            assert receipts == 1
        else:
            assert payment.status == "PENDING_APPROVAL"
            assert movements == 0
            assert receipts == 0

    moment = timezone.now()
    with pytest.raises(DatabaseError), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Payment.objects.create(
            gym=gym,
            entry_type=Payment.EntryType.CHARGE,
            purpose=Payment.Purpose.OTHER,
            description="Unlinked cash",
            amount=Decimal("1.00"),
            currency=gym.currency,
            method=Payment.Method.CASH,
            business_date=gym_today(gym),
            received_at=moment,
            status=Payment.Status.POSTED,
            submitted_at=moment,
            submitted_by_membership_id=owner.id,
            approved_at=moment,
            approved_by_membership_id=manager.id,
            posted_at=moment,
            posted_by_membership_id=manager.id,
            created_by_membership_id=owner.id,
            updated_by_membership_id=manager.id,
        )
    with pytest.raises(DatabaseError, match="legacy cash marker"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
            cursor.execute(
                """
                INSERT INTO members_payment (
                  id, created_at, updated_at, description, amount, currency, method,
                  business_date, received_at, status, entry_type, purpose,
                  legacy_unlinked_cash, gym_id, created_by_membership_id,
                  updated_by_membership_id
                ) VALUES (
                  gen_random_uuid(), now(), now(), 'marker', 1, %s, 'CASH',
                  CURRENT_DATE, now(), 'DRAFT', 'CHARGE', 'OTHER', true, %s, %s, %s
                )
                """,
                [gym.currency, gym.id, owner.id, owner.id],
            )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        posted = Payment.objects.get(id=payment_id)
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            Payment.objects.filter(id=posted.id).update(amount=Decimal("99.00"))
        issued = Receipt.objects.get(payment_id=posted.id)
        with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
            Receipt.objects.filter(id=issued.id).update(receipt_number="CHANGED")
        with pytest.raises(DatabaseError, match="cannot be deleted"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM members_receipt WHERE id = %s", [issued.id])
    with pytest.raises(DatabaseError, match="payment_created_by_same_gym_fk"), transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Payment.objects.create(
            gym=gym,
            entry_type=Payment.EntryType.CHARGE,
            purpose=Payment.Purpose.OTHER,
            description="Cross gym actor",
            amount=Decimal("1.00"),
            currency=gym.currency,
            method=Payment.Method.BANK_TRANSFER,
            business_date=gym_today(gym),
            received_at=moment,
            status=Payment.Status.DRAFT,
            created_by_membership_id=other_owner.id,
            updated_by_membership_id=owner.id,
        )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conname IN (
              'payment_cash_session_same_gym_fk',
              'payment_cash_txn_same_gym_fk',
              'receipt_created_by_same_gym_fk'
            )
            """
        )
        names = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE indexname = 'receipt_one_issued_per_payment'
            """
        )
        names.add(cursor.fetchone()[0])
        assert names == {
            "payment_cash_session_same_gym_fk",
            "payment_cash_txn_same_gym_fk",
            "receipt_created_by_same_gym_fk",
            "receipt_one_issued_per_payment",
        }
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.gym_id', '', true)")
            cursor.execute("SELECT count(*) FROM members_payment")
            assert cursor.fetchone() == (0,)
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(other_gym.id)])
            cursor.execute("SELECT count(*) FROM members_payment")
            assert cursor.fetchone() == (0,)
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                cursor.execute("DELETE FROM members_payment WHERE id = %s", [payment_id])
    assert other_api is not None


def _runtime_insert(
    gym: Gym,
    owner: GymMembership,
    *,
    status: str = "DRAFT",
    purpose: str = "OTHER",
    currency: str | None = None,
    member_id: object = None,
    membership_id: object = None,
    created_by: GymMembership | None = None,
    created_scope: bool = False,
    submitted_scope: bool = False,
) -> UUID:
    payment_id = uuid4()
    moment = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL ROLE gym_runtime")
        cursor.execute(
            """
            INSERT INTO members_payment (
              id, created_at, updated_at, description, amount, currency, method,
              business_date, received_at, status, entry_type, purpose, gym_id,
              member_id, membership_id,
              created_by_membership_id, updated_by_membership_id,
              reference_number, rejection_reason, cancellation_reason, void_reason,
              legacy_unlinked_cash, created_with_reception_scope,
              submitted_with_reception_scope
            ) VALUES (
              %s, %s, %s, 'Direct', 1.00, %s, 'BANK_TRANSFER',
              %s, %s, %s, 'CHARGE', %s, %s,
              %s, %s,
              %s, %s,
              '', '', '', '',
              false, %s, %s
            )
            """,
            [
                payment_id,
                moment,
                moment,
                currency or gym.currency,
                gym_today(gym),
                moment,
                status,
                purpose,
                gym.id,
                member_id,
                membership_id,
                (created_by or owner).id,
                owner.id,
                created_scope,
                submitted_scope,
            ],
        )
    return payment_id


@override_settings(**jwt_settings())
def test_runtime_inserts_start_as_drafts_with_source_rules() -> None:
    api, gym, owner, headers = _gym("PAYINS")
    member_id = _member(api, headers, gym)
    with pytest.raises(DatabaseError, match="runtime inserts must start as drafts"):
        with transaction.atomic():
            _runtime_insert(gym, owner, status="POSTED")
    with pytest.raises(DatabaseError, match="currency must match the gym"):
        with transaction.atomic():
            _runtime_insert(gym, owner, currency="USD")
    with pytest.raises(DatabaseError, match="membership purpose requires the member sale"):
        with transaction.atomic():
            _runtime_insert(gym, owner, purpose="MEMBERSHIP", member_id=member_id)
    with pytest.raises(DatabaseError, match="other purpose has no membership"):
        with transaction.atomic():
            _runtime_insert(gym, owner, purpose="OTHER", membership_id=uuid4())
    with transaction.atomic():
        drafted = _runtime_insert(gym, owner)
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM members_payment WHERE id = %s", [drafted])
            assert cursor.fetchone() == ("DRAFT",)


def _posted_correction(
    *,
    gym: Gym,
    original: Payment,
    entry_type: str,
    amount: Decimal,
    business_date: object,
    owner_id: object,
    submitter_id: object,
    approver_id: object,
    sequence_value: int,
    purpose: str | None = None,
    member_id: object = _KEEP,
    membership_id: object = _KEEP,
) -> Payment:
    moment = timezone.now()
    correction = Payment.objects.create(
        gym=gym,
        member_id=original.member_id if member_id is _KEEP else member_id,
        membership_id=original.membership_id if membership_id is _KEEP else membership_id,
        original_payment=original,
        entry_type=entry_type,
        purpose=original.purpose if purpose is None else purpose,
        description="Correction",
        amount=amount,
        currency=gym.currency,
        method=Payment.Method.BANK_TRANSFER,
        business_date=business_date,
        received_at=moment,
        status=Payment.Status.POSTED,
        submitted_at=moment,
        submitted_by_membership_id=submitter_id,
        approved_at=moment,
        approved_by_membership_id=approver_id,
        posted_at=moment,
        posted_by_membership_id=approver_id,
        created_by_membership_id=owner_id,
        updated_by_membership_id=approver_id,
        submitted_with_reception_scope=True,
    )
    _receipt_for(
        correction,
        number=f"LEDGER-{sequence_value}",
        sequence_value=sequence_value,
        actor_id=approver_id,
    )
    return correction


@override_settings(**jwt_settings())
def test_reversal_linkage_is_reciprocal_and_reserved() -> None:
    api, gym, owner, headers = _gym("PAYREV")
    receptionist = add_staff(gym, email="payrev-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="payrev-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    first = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    second = _pending_bank(api, headers, gym, member_id, membership_id, "12.00")
    assert _approve(api, manager_headers, first, key="pay-rev-approve-0001").status_code == 200
    assert _approve(api, manager_headers, second, key="pay-rev-approve-0002").status_code == 200
    today = gym_today(gym)

    with pytest.raises(DatabaseError, match="payment_reversal"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = Payment.objects.get(id=first)
            _posted_correction(
                gym=gym,
                original=original,
                entry_type=Payment.EntryType.REVERSAL,
                amount=original.amount,
                business_date=today,
                owner_id=owner.id,
                submitter_id=receptionist.id,
                approver_id=manager.id,
                sequence_value=1,
            )

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original = Payment.objects.select_for_update().get(id=first)
        reversal = _posted_correction(
            gym=gym,
            original=original,
            entry_type=Payment.EntryType.REVERSAL,
            amount=original.amount,
            business_date=today,
            owner_id=owner.id,
            submitter_id=receptionist.id,
            approver_id=manager.id,
            sequence_value=2,
        )
        moment = timezone.now()
        Receipt.objects.filter(payment=original, status=Receipt.Status.ISSUED).update(
            status=Receipt.Status.VOID,
            voided_at=moment,
            voided_by_membership_id=manager.id,
            void_reason="reversal",
        )
        original.status = Payment.Status.VOID
        original.voided_at = moment
        original.voided_by_membership_id = manager.id
        original.void_reason = "reversal"
        original.reversal_payment = reversal
        original.save(
            update_fields=[
                "status",
                "voided_at",
                "voided_by_membership_id",
                "void_reason",
                "reversal_payment",
                "updated_at",
            ]
        )
    original.refresh_from_db()
    reversal.refresh_from_db()
    assert original.reversal_payment_id == reversal.id
    assert reversal.original_payment_id == original.id

    with pytest.raises(DatabaseError, match="payment_one_reversal_per_original_idx"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            _posted_correction(
                gym=gym,
                original=original,
                entry_type=Payment.EntryType.REVERSAL,
                amount=original.amount,
                business_date=today,
                owner_id=owner.id,
                submitter_id=receptionist.id,
                approver_id=manager.id,
                sequence_value=3,
            )

    with pytest.raises(DatabaseError, match="reversal link does not match"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            other = Payment.objects.select_for_update().get(id=second)
            moment = timezone.now()
            Receipt.objects.filter(payment=other, status=Receipt.Status.ISSUED).update(
                status=Receipt.Status.VOID,
                voided_at=moment,
                voided_by_membership_id=manager.id,
                void_reason="mismatch",
            )
            other.status = Payment.Status.VOID
            other.voided_at = moment
            other.voided_by_membership_id = manager.id
            other.void_reason = "mismatch"
            other.reversal_payment = reversal
            other.save(
                update_fields=[
                    "status",
                    "voided_at",
                    "voided_by_membership_id",
                    "void_reason",
                    "reversal_payment",
                    "updated_at",
                ]
            )

    def runtime_void_without_reversal(*, session_setting: bool) -> None:
        with pytest.raises(DatabaseError, match="void requires a reversal"):
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL ROLE gym_runtime")
                    if session_setting:
                        cursor.execute(
                            "SELECT set_config('app.payment_internal_correction', 'on', true)"
                        )
                    cursor.execute(
                        """
                        UPDATE members_payment
                        SET status = 'VOID',
                            voided_at = now(),
                            voided_by_membership_id = %s,
                            void_reason = 'reserved',
                            updated_at = now()
                        WHERE id = %s
                        """,
                        [manager.id, second],
                    )

    runtime_void_without_reversal(session_setting=False)
    runtime_void_without_reversal(session_setting=True)
    untouched = Payment.objects.get(id=second)
    assert untouched.status == "POSTED"
    assert untouched.reversal_payment_id is None

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        other = Payment.objects.get(id=second)
        reserved = _posted_correction(
            gym=gym,
            original=other,
            entry_type=Payment.EntryType.REVERSAL,
            amount=other.amount,
            business_date=today,
            owner_id=owner.id,
            submitter_id=receptionist.id,
            approver_id=manager.id,
            sequence_value=4,
        )
        moment = timezone.now()
        Receipt.objects.filter(payment=other, status=Receipt.Status.ISSUED).update(
            status=Receipt.Status.VOID,
            voided_at=moment,
            voided_by_membership_id=manager.id,
            void_reason="reserved",
        )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute("SELECT set_config('app.payment_internal_correction', 'on', true)")
            cursor.execute(
                """
                UPDATE members_payment
                SET status = 'VOID',
                    reversal_payment_id = %s,
                    voided_at = now(),
                    voided_by_membership_id = %s,
                    void_reason = 'reserved',
                    updated_at = now()
                WHERE id = %s
                """,
                [reserved.id, manager.id, other.id],
            )
    completed = Payment.objects.get(id=second)
    assert completed.status == "VOID"
    assert completed.reversal_payment_id == reserved.id


def _void_linked(
    *,
    gym: Gym,
    original: Payment,
    reversal: Payment,
    actor_id: object,
) -> None:
    moment = timezone.now()
    Receipt.objects.filter(payment=original, status=Receipt.Status.ISSUED).update(
        status=Receipt.Status.VOID,
        voided_at=moment,
        voided_by_membership_id=actor_id,
        void_reason="reversal",
    )
    original.status = Payment.Status.VOID
    original.voided_at = moment
    original.voided_by_membership_id = actor_id
    original.void_reason = "reversal"
    original.reversal_payment = reversal
    original.save(
        update_fields=[
            "status",
            "voided_at",
            "voided_by_membership_id",
            "void_reason",
            "reversal_payment",
            "updated_at",
        ]
    )


@override_settings(**jwt_settings())
def test_reversal_must_match_amount_member_and_purpose() -> None:
    api, gym, owner, headers = _gym("PAYMAT")
    receptionist = add_staff(gym, email="paymat-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paymat-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    other_member = _member(api, headers, gym)
    other_membership = _membership(api, headers, gym, other_member, code="PAYPLAN-B")
    amount_id = _pending_bank(api, headers, gym, member_id, membership_id, "10.00")
    purpose_id = _pending_bank(api, headers, gym, member_id, membership_id, "11.00")
    member_id_payment = _pending_bank(api, headers, gym, member_id, membership_id, "12.00")
    for index, payment_id in enumerate((amount_id, purpose_id, member_id_payment), start=1):
        assert (
            _approve(
                api, manager_headers, payment_id, key=f"pay-match-approve-{index:04d}"
            ).status_code
            == 200
        )
    today = gym_today(gym)
    common = {
        "gym": gym,
        "entry_type": Payment.EntryType.REVERSAL,
        "business_date": today,
        "owner_id": owner.id,
        "submitter_id": receptionist.id,
        "approver_id": manager.id,
    }

    with pytest.raises(DatabaseError, match="does not match"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = Payment.objects.select_for_update().get(id=amount_id)
            reversal = _posted_correction(
                **common,
                original=original,
                amount=Decimal("1.00"),
                sequence_value=1,
            )
            _void_linked(gym=gym, original=original, reversal=reversal, actor_id=manager.id)

    with pytest.raises(DatabaseError, match="does not match"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = Payment.objects.select_for_update().get(id=purpose_id)
            reversal = _posted_correction(
                **common,
                original=original,
                amount=original.amount,
                purpose=Payment.Purpose.OTHER,
                membership_id=None,
                sequence_value=2,
            )
            _void_linked(gym=gym, original=original, reversal=reversal, actor_id=manager.id)

    with pytest.raises(DatabaseError, match="does not match"):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = Payment.objects.select_for_update().get(id=member_id_payment)
            reversal = _posted_correction(
                **common,
                original=original,
                amount=original.amount,
                member_id=other_member,
                membership_id=other_membership,
                sequence_value=3,
            )
            _void_linked(gym=gym, original=original, reversal=reversal, actor_id=manager.id)

    kept = Payment.objects.get(id=amount_id)
    assert kept.status == "POSTED"
    assert kept.currency == gym.currency


@override_settings(**jwt_settings())
def test_cash_reversal_requires_an_out_movement() -> None:
    api, gym, owner, headers = _gym("PAYOUT")
    receptionist = add_staff(gym, email="payout-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="payout-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="20.00",
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/payments/{payment_id}/submit/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="pay-out-submit-00001",
            **reception_headers,
        ).status_code
        == 200
    )
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {
            "register_code": "FRONT",
            "business_date": gym_today(gym).isoformat(),
            "opening_cash": "50.00",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="pay-out-open-0000001",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    session_id = opened.json()["data"]["session"]["id"]
    approved = _approve(
        api,
        manager_headers,
        payment_id,
        key="pay-out-approve-0001",
        body={"cash_session_id": session_id},
    )
    assert approved.status_code == 200, approved.content

    def attempt(direction: str, sequence_value: int) -> None:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            original = Payment.objects.select_for_update().get(id=payment_id)
            reversal_id = uuid4()
            moment = timezone.now()
            movement = CashTransaction.objects.create(
                gym=gym,
                cash_session_id=session_id,
                direction=direction,
                source_type=CashTransaction.SourceType.PAYMENT,
                source_id=reversal_id,
                amount=original.amount,
                currency=gym.currency,
                business_date=original.business_date,
                occurred_at=moment,
                description="Cash reversal",
                reason="reversal" if direction == CashTransaction.Direction.OUT else "",
                reversal_of_id=(
                    original.cash_transaction_id
                    if direction == CashTransaction.Direction.OUT
                    else None
                ),
                created_by_membership_id=manager.id,
            )
            session = CashSession.objects.select_for_update().get(id=session_id)
            signed = (
                movement.amount if direction == CashTransaction.Direction.IN else -movement.amount
            )
            session.cash_sales += signed
            session.expected_closing = (
                session.opening_cash
                + session.cash_sales
                - session.cash_expenses
                + session.manual_net
            )
            session.save(update_fields=["cash_sales", "expected_closing", "updated_at"])
            reversal = Payment.objects.create(
                id=reversal_id,
                gym=gym,
                member_id=original.member_id,
                membership_id=original.membership_id,
                original_payment=original,
                entry_type=Payment.EntryType.REVERSAL,
                purpose=original.purpose,
                description="Cash reversal",
                amount=original.amount,
                currency=original.currency,
                method=Payment.Method.CASH,
                business_date=original.business_date,
                received_at=moment,
                status=Payment.Status.POSTED,
                cash_session_id=session_id,
                cash_transaction=movement,
                submitted_at=moment,
                submitted_by_membership_id=receptionist.id,
                submitted_with_reception_scope=True,
                approved_at=moment,
                approved_by_membership_id=manager.id,
                posted_at=moment,
                posted_by_membership_id=manager.id,
                created_by_membership_id=owner.id,
                updated_by_membership_id=manager.id,
            )
            _receipt_for(
                reversal,
                number=f"LEDGER-OUT-{sequence_value}",
                sequence_value=sequence_value,
                actor_id=manager.id,
            )
            _void_linked(gym=gym, original=original, reversal=reversal, actor_id=manager.id)

    with pytest.raises(DatabaseError, match="cash movement mismatch"):
        attempt(CashTransaction.Direction.IN, 1)
    attempt(CashTransaction.Direction.OUT, 2)
    original = Payment.objects.get(id=payment_id)
    assert original.status == "VOID"
    reversal = Payment.objects.get(original_payment_id=payment_id, entry_type="REVERSAL")
    assert reversal.cash_transaction_id is not None
    movement = CashTransaction.objects.get(id=reversal.cash_transaction_id)
    assert movement.direction == "OUT"
    assert movement.amount == original.amount


@override_settings(**jwt_settings())
def test_runtime_cannot_forge_reception_scope() -> None:
    _api, gym, owner, _headers = _gym("PAYFLAG")
    receptionist = add_staff(gym, email="payflag-reception@example.test", role_key="RECEPTIONIST")
    with pytest.raises(DatabaseError, match="does not match the role"):
        with transaction.atomic():
            _runtime_insert(gym, owner, created_by=receptionist, created_scope=False)
    with pytest.raises(DatabaseError, match="does not match the role"):
        with transaction.atomic():
            _runtime_insert(gym, owner, created_scope=True)
    with transaction.atomic():
        accepted = _runtime_insert(gym, owner, created_by=receptionist, created_scope=True)
    stored = Payment.objects.get(id=accepted)
    assert stored.created_with_reception_scope is True
    with transaction.atomic():
        drafted = _runtime_insert(gym, owner)

    def submit(actor_id: object, *, reception_scope: bool) -> None:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
                cursor.execute("SET LOCAL ROLE gym_runtime")
                cursor.execute(
                    """
                    UPDATE members_payment
                    SET status = 'PENDING_APPROVAL',
                        submitted_at = now(),
                        submitted_by_membership_id = %s,
                        submitted_with_reception_scope = %s,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    [actor_id, reception_scope, drafted],
                )

    with pytest.raises(DatabaseError, match="does not match the role"):
        submit(receptionist.id, reception_scope=False)
    with pytest.raises(DatabaseError, match="does not match the role"):
        submit(owner.id, reception_scope=True)
    submit(owner.id, reception_scope=False)
    pending = Payment.objects.get(id=drafted)
    assert pending.status == "PENDING_APPROVAL"
    assert pending.submitted_with_reception_scope is False


@override_settings(**jwt_settings())
def test_reception_check_rejects_another_gym() -> None:
    _api_a, gym_a, _owner_a, _headers_a = _gym("PAYISOA")
    _api_b, gym_b, owner_b, _headers_b = _gym("PAYISOB")
    receptionist_b = add_staff(
        gym_b, email="payisob-reception@example.test", role_key="RECEPTIONIST"
    )

    def ask(gym_id: object, membership_id: object) -> bool:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_a.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT public.app_has_protected_reception(%s, %s)",
                [gym_id, membership_id],
            )
            row = cursor.fetchone()
        assert row is not None
        return bool(row[0])

    with pytest.raises(DatabaseError, match="outside the active gym") as caught:
        with transaction.atomic():
            ask(gym_b.id, receptionist_b.id)
    cause = caught.value.__cause__
    assert cause is not None and cause.diag is not None
    assert cause.diag.message_primary == "payment_guard: reception check is outside the active gym"
    with pytest.raises(DatabaseError, match="outside the active gym"):
        with transaction.atomic():
            ask(None, receptionist_b.id)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_b.id)])
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "SELECT public.app_has_protected_reception(%s, %s)",
                [gym_b.id, receptionist_b.id],
            )
            assert cursor.fetchone() == (True,)
            cursor.execute(
                "SELECT public.app_has_protected_reception(%s, %s)",
                [gym_b.id, owner_b.id],
            )
            assert cursor.fetchone() == (False,)
    with pytest.raises(DatabaseError, match="outside the active gym"):
        with transaction.atomic():
            set_gym_context(gym_id=gym_a.id)
            Payment.objects.create(
                gym=gym_b,
                entry_type=Payment.EntryType.CHARGE,
                purpose=Payment.Purpose.OTHER,
                description="Cross gym flag",
                amount=Decimal("1.00"),
                currency=gym_b.currency,
                method=Payment.Method.BANK_TRANSFER,
                business_date=gym_today(gym_b),
                received_at=timezone.now(),
                status=Payment.Status.DRAFT,
                created_with_reception_scope=False,
                created_by_membership_id=receptionist_b.id,
                updated_by_membership_id=receptionist_b.id,
            )
    assert not Payment.objects.filter(gym_id=gym_b.id).exists()


@override_settings(**jwt_settings())
def test_reception_till_survives_role_removal() -> None:
    api, gym, _owner, headers = _gym("PAYTILL")
    receptionist = add_staff(gym, email="paytill-reception@example.test", role_key="RECEPTIONIST")
    manager = add_staff(gym, email="paytill-manager@example.test", role_key="MANAGER")
    _name(manager, "Mina", "Manager")
    reception_headers = auth_headers(staff_token(receptionist))
    manager_headers = auth_headers(staff_token(manager))
    member_id = _member(api, headers, gym)
    membership_id = _membership(api, headers, gym, member_id)
    created = _draft(
        api,
        reception_headers,
        gym=gym,
        member_id=member_id,
        membership_id=membership_id,
        amount="20.00",
    )
    assert created.status_code == 201, created.content
    payment_id = created.json()["data"]["id"]
    assert _submit(api, reception_headers, payment_id).status_code == 200
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.get(id=payment_id)
        assert payment.created_with_reception_scope is True
        assert payment.submitted_with_reception_scope is True
    today = gym_today(gym).isoformat()
    opened = api.post(
        "/api/v1/cash-sessions/open/",
        {"register_code": "FRONT", "business_date": today, "opening_cash": "20.00"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-till-open-{uuid4().hex}",
        **reception_headers,
    )
    assert opened.status_code == 201, opened.content
    own_session = opened.json()["data"]["session"]["id"]
    other_opened = api.post(
        "/api/v1/cash-sessions/open/",
        {"register_code": "OFFICE", "business_date": today, "opening_cash": "20.00"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"pay-till-other-{uuid4().hex}",
        **manager_headers,
    )
    assert other_opened.status_code == 201, other_opened.content
    other_session = other_opened.json()["data"]["session"]["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymMembershipRole.objects.filter(
            gym_id=gym.id,
            gym_membership_id=receptionist.id,
            role__key="RECEPTIONIST",
        ).delete()
    denied = _approve(
        api,
        manager_headers,
        payment_id,
        key="pay-till-wrong-0001",
        body={"cash_session_id": other_session},
    )
    assert denied.status_code == 400, denied.content
    posted = _approve(
        api,
        manager_headers,
        payment_id,
        key="pay-till-right-0001",
        body={"cash_session_id": own_session},
    )
    assert posted.status_code == 200, posted.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        payment = Payment.objects.get(id=payment_id)
        assert payment.created_with_reception_scope is True
        assert payment.submitted_with_reception_scope is True
