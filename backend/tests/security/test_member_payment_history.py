from __future__ import annotations

import importlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Payment, Receipt
from apps.members.services import encode_member_payment_cursor, member_payment_history_scope
from apps.tenants.models import (
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymMembershipRole,
    Permission,
    Role,
    RolePermission,
)
from apps.users.models import User
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
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

SAFE_FIELDS = {
    "id",
    "membership_id",
    "original_payment_id",
    "entry_type",
    "purpose",
    "description",
    "amount",
    "currency",
    "method",
    "business_date",
    "received_at",
    "status",
    "receipt_number",
}
TIED = datetime(2024, 6, 2, 12, 0, tzinfo=UTC)
NEWER = datetime(2024, 6, 3, 8, 0, tzinfo=UTC)
HIGH_ID = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
MID_ID = UUID("88888888-8888-4888-8888-888888888888")
LOW_ID = UUID("11111111-1111-4111-8111-111111111111")
OLDER_ID = UUID("22222222-2222-4222-8222-222222222222")


def _restore_trial_features() -> None:
    """Reapply the trial-plan features after a transactional flush.

    ``ensure_authorization_catalog`` restores only the members feature. A prior
    PostgreSQL test flushes migration-seeded plan rows, so finance must be
    present before this history read is provisioned.
    """

    seed = importlib.import_module(
        "apps.tenants.migrations.0008_provisioning_defaults_and_security"
    )
    seed.seed_trial_plan(django_apps, None)


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _cursor_query(limit: int, cursor: str) -> str:
    return f"?limit={limit}&after={quote(cursor, safe='')}"


def _history(api: APIClient, member_id: object, headers: dict[str, str], query: str = ""):
    return api.get(f"/api/v1/members/{member_id}/payments/{query}", **headers)


def _staff(gym: Gym, owner: GymMembership, email: str, key: str, codes: tuple[str, ...]):
    user = User.objects.create_user(email, PASSWORD)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.create(
            gym=gym,
            user=user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=date(2026, 10, 1),
            activated_at=timezone.now(),
        )
        role = Role.objects.create(
            gym=gym,
            key=key,
            name=key.title(),
            created_by_membership_id=owner.id,
        )
        for code in codes:
            RolePermission.objects.create(
                gym=gym,
                role=role,
                permission=Permission.objects.get(code=code),
                created_by_membership_id=owner.id,
            )
        GymMembershipRole.objects.create(
            gym=gym,
            gym_membership=membership,
            role=role,
            created_by_membership_id=owner.id,
        )
    membership.user = user
    return membership


def _member(gym: Gym, actor_id: object, *, number: str, first_name: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.create(
            gym=gym,
            member_number=number,
            first_name=first_name,
            last_name="Payer",
            join_date=date(2020, 1, 1),
            created_by_membership_id=actor_id,
            updated_by_membership_id=actor_id,
        )


def _payment(
    gym: Gym,
    actor_id: object,
    *,
    member: Member,
    status: str,
    amount: str = "10.00",
    entry_type: str = Payment.EntryType.CHARGE,
    received_at: datetime | None = None,
    payment_id: UUID | None = None,
    original: Payment | None = None,
    membership_id: UUID | None = None,
    reference: str = "REF-SECRET",
    reason: str = "REASON-SECRET",
) -> Payment:
    values: dict[str, object] = {
        "gym": gym,
        "member": member,
        "entry_type": entry_type,
        "purpose": Payment.Purpose.MEMBERSHIP,
        "description": f"{status} dues",
        "amount": Decimal(amount),
        "currency": gym.currency,
        "method": Payment.Method.CASH,
        "business_date": date(2024, 6, 2),
        "received_at": received_at or TIED,
        "reference_number": reference,
        "status": status,
        "rejection_reason": reason,
        "void_reason": reason,
        "cancellation_reason": reason,
        "created_by_membership_id": actor_id,
        "updated_by_membership_id": actor_id,
        "submitted_by_membership_id": actor_id,
        "approved_by_membership_id": actor_id,
    }
    if payment_id is not None:
        values["id"] = payment_id
    if original is not None:
        values["original_payment"] = original
    if membership_id is not None:
        values["membership_id"] = membership_id
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Payment.objects.create(**values)


def _queries_include(captured: list[dict[str, object]], table: str) -> bool:
    return any(table in str(query["sql"]).lower() for query in captured)


@override_settings(**jwt_settings())
def test_member_payment_history_authorization_entitlements_and_filters() -> None:
    _restore_trial_features()
    client, platform_access = platform_token("payhist-platform@example.test")
    payload = provision(
        client, platform_access, code="PAY-A", owner_email="payhist-owner-a@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PAY-B", owner_email="payhist-owner-b@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    other_owner = GymMembership.objects.select_related("user").get(
        id=other_payload["owner"]["gym_membership"]["id"]
    )
    members_only = _staff(gym, owner, "payhist-members@example.test", "MEMONLY", ("members.view",))
    payments_only = _staff(
        gym, owner, "payhist-payments@example.test", "PAYONLY", ("payments.view",)
    )
    neither = _staff(gym, owner, "payhist-neither@example.test", "NONE", ("gym.view",))
    both = _staff(
        gym,
        owner,
        "payhist-both@example.test",
        "BOTH",
        ("members.view", "payments.view"),
    )
    owner_headers = auth_headers(staff_token(owner))
    other_headers = auth_headers(staff_token(other_owner))
    both_headers = auth_headers(staff_token(both))
    api = _client()
    member = _member(gym, owner.id, number="PAY-000001", first_name="Hidden")
    _payment(gym, owner.id, member=member, status=Payment.Status.POSTED, amount="15.50")

    anonymous = _client().get(f"/api/v1/members/{member.id}/payments/")
    assert anonymous.status_code == 403
    assert anonymous.json()["error"]["code"] == "NOT_AUTHENTICATED"

    platform = _history(api, member.id, auth_headers(platform_access))
    assert platform.status_code == 401
    assert platform.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    matrix = (
        (members_only, 403),
        (payments_only, 403),
        (neither, 403),
        (both, 200),
        (owner, 200),
    )
    for staff, expected in matrix:
        response = _history(api, member.id, auth_headers(staff_token(staff)))
        assert response.status_code == expected, response.content

    missing = uuid4()
    with CaptureQueriesContext(connection) as denied_queries:
        denied = _history(api, missing, auth_headers(staff_token(members_only)))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert not _queries_include(list(denied_queries), "members_member")
    assert not _queries_include(list(denied_queries), "members_payment")

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        RolePermission.objects.filter(
            gym=gym,
            role__key="BOTH",
            permission__code="payments.view",
        ).delete()
    with CaptureQueriesContext(connection) as live_queries:
        live_denied = _history(api, member.id, both_headers)
    assert live_denied.status_code == 403
    assert not _queries_include(list(live_queries), "members_member")

    hidden = _history(api, member.id, other_headers)
    assert hidden.status_code == 404
    assert "Hidden" not in hidden.content.decode()
    assert str(gym.id) not in hidden.content.decode()

    unknown = _history(api, uuid4(), owner_headers)
    assert unknown.status_code == 404

    for query, field in (
        (f"?gym_id={gym.id}", "gym_id"),
        ("?status=POSTED", "status"),
        (f"?member_id={member.id}", "member_id"),
        ("?foo=1", "foo"),
        ("?limit=101", "limit"),
        ("?limit=0", "limit"),
    ):
        rejected = _history(api, member.id, owner_headers, query)
        assert rejected.status_code == 400, rejected.content
        assert rejected.json()["success"] is False
        assert field in rejected.json()["error"]["fields"]

    blank_after = api.get(
        f"/api/v1/members/{member.id}/payments/",
        {"after": ""},
        **owner_headers,
    )
    assert blank_after.status_code == 400, blank_after.content
    assert "after" in blank_after.json()["error"]["fields"]

    def set_entitlement(code: str, **changes: object) -> None:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            GymFeatureEntitlement.objects.filter(gym=gym, feature__code=code).update(**changes)

    set_entitlement("finance", enabled=False)
    with CaptureQueriesContext(connection) as finance_queries:
        finance_denied = _history(api, missing, owner_headers)
    assert finance_denied.status_code == 403
    assert not _queries_include(list(finance_queries), "members_member")
    set_entitlement("finance", enabled=True)
    set_entitlement("members", enabled=False)
    with CaptureQueriesContext(connection) as member_queries:
        members_denied = _history(api, member.id, owner_headers)
    assert members_denied.status_code == 403
    assert not _queries_include(list(member_queries), "members_member")
    set_entitlement("members", enabled=True)
    set_entitlement("finance", effective_until=timezone.now() - timedelta(seconds=1))
    assert _history(api, member.id, owner_headers).status_code == 403
    set_entitlement(
        "finance",
        effective_until=None,
        effective_from=timezone.now() + timedelta(days=1),
    )
    assert _history(api, member.id, owner_headers).status_code == 403
    set_entitlement("finance", effective_from=None, effective_until=None, enabled=True)
    restored = _history(api, member.id, owner_headers)
    assert restored.status_code == 200, restored.json()
    assert restored.json()["success"] is True
    assert set(restored.json()["meta"]) == {"has_more", "next_after"}

    check_ins = api.get(f"/api/v1/members/{member.id}/check-ins/", **owner_headers)
    assert check_ins.status_code == 404
    assert other_gym.id != gym.id


@override_settings(**jwt_settings())
def test_member_payment_history_pages_statuses_receipts_and_query_budget() -> None:
    _restore_trial_features()
    client, platform_access = platform_token("payhist-page-platform@example.test")
    payload = provision(
        client, platform_access, code="PAY-C", owner_email="payhist-owner-c@example.test"
    )
    other_payload = provision(
        client, platform_access, code="PAY-D", owner_email="payhist-owner-d@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    other_gym = Gym.objects.get(id=other_payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    api = _client()
    member = _member(gym, owner.id, number="PAY-PAGE", first_name="Ledger")
    other_member = _member(gym, owner.id, number="PAY-OTHER", first_name="Other")
    newest = _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.POSTED,
        amount="40.10",
        received_at=NEWER,
        payment_id=OLDER_ID,
    )
    high = _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.DRAFT,
        received_at=TIED,
        payment_id=HIGH_ID,
    )
    mid = _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.PENDING_APPROVAL,
        received_at=TIED,
        payment_id=MID_ID,
        entry_type=Payment.EntryType.REFUND,
        original=high,
        amount="1.00",
    )
    low = _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.REJECTED,
        received_at=TIED,
        payment_id=LOW_ID,
    )
    _payment(
        gym,
        owner.id,
        member=other_member,
        status=Payment.Status.POSTED,
        amount="999.00",
        reference="OTHER-SECRET",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Receipt.objects.create(
            gym=gym,
            payment=newest,
            receipt_number="RCPT-100",
            issued_at=timezone.now(),
            member_number=member.member_number,
            member_name="Ledger Secret",
            description="receipt-secret-description",
            amount=newest.amount,
            currency=gym.currency,
            method=newest.method,
            business_date=newest.business_date,
            issuer_name="ISSUER-SECRET",
            created_by_membership_id=owner.id,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=other_gym.id)
        Receipt.objects.create(
            gym=other_gym,
            payment=newest,
            receipt_number="RCPT-FOREIGN",
            issued_at=timezone.now(),
            member_number="FOREIGN",
            member_name="Foreign",
            description="foreign",
            amount=newest.amount,
            currency=other_gym.currency,
            method=newest.method,
            business_date=newest.business_date,
            issuer_name="FOREIGN-SECRET",
            created_by_membership_id=owner.id,
        )

    first = _history(api, member.id, headers, "?limit=2")
    assert first.status_code == 200, first.json()
    body = first.json()
    assert body["success"] is True
    assert body["meta"] == {"has_more": True, "next_after": body["meta"]["next_after"]}
    assert body["meta"]["next_after"]
    assert [row["id"] for row in body["data"]] == [str(newest.id), str(high.id)]
    posted = body["data"][0]
    assert set(posted) == SAFE_FIELDS
    assert posted["amount"] == "40.10"
    assert posted["receipt_number"] == "RCPT-100"
    assert posted["membership_id"] is None
    assert posted["status"] == "POSTED"
    assert body["data"][1]["receipt_number"] is None
    rendered = first.content.decode()
    for secret in (
        "REF-SECRET",
        "REASON-SECRET",
        "ISSUER-SECRET",
        "Ledger Secret",
        "receipt-secret-description",
        "OTHER-SECRET",
        "FOREIGN-SECRET",
        str(owner.id),
    ):
        assert secret not in rendered

    cursor = body["meta"]["next_after"]
    second = _history(api, member.id, headers, _cursor_query(2, cursor))
    assert second.status_code == 200, second.json()
    assert [row["id"] for row in second.json()["data"]] == [str(mid.id), str(low.id)]
    assert second.json()["data"][0]["original_payment_id"] == str(high.id)
    assert second.json()["data"][0]["entry_type"] == "REFUND"
    assert second.json()["meta"]["has_more"] is False
    assert second.json()["meta"]["next_after"] is None

    changed_limit = _history(api, member.id, headers, _cursor_query(1, cursor))
    assert changed_limit.status_code == 400
    assert changed_limit.json()["error"]["fields"]["after"] == ["Invalid cursor."]
    assert high.received_at.isoformat() not in changed_limit.content.decode()
    assert str(member.id) not in changed_limit.content.decode()

    wrong_member = _history(api, other_member.id, headers, _cursor_query(2, cursor))
    assert wrong_member.status_code == 400
    assert str(member.id) not in wrong_member.content.decode()
    assert "RCPT-100" not in wrong_member.content.decode()

    tampered = f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}"
    damaged = _history(api, member.id, headers, _cursor_query(2, tampered))
    assert damaged.status_code == 400
    assert damaged.json()["error"]["fields"]["after"] == ["Invalid cursor."]
    assert cursor not in damaged.content.decode()

    scope = member_payment_history_scope(gym_id=uuid4(), member_id=member.id, limit=2)
    foreign_cursor = encode_member_payment_cursor(
        received_at=NEWER, payment_id=newest.id, scope=scope
    )
    foreign = _history(api, member.id, headers, _cursor_query(2, foreign_cursor))
    assert foreign.status_code == 400
    assert str(scope["gym_id"]) not in foreign.content.decode()

    issued_at = 1_700_000_000
    bound = member_payment_history_scope(gym_id=gym.id, member_id=member.id, limit=2)
    with patch("django.core.signing.time.time", return_value=issued_at):
        expired_cursor = encode_member_payment_cursor(
            received_at=datetime(1991, 3, 4, 5, 6, 7, tzinfo=UTC),
            payment_id=newest.id,
            scope=bound,
        )
    with patch("django.core.signing.time.time", return_value=issued_at + 24 * 60 * 60 + 5):
        expired = _history(api, member.id, headers, _cursor_query(2, expired_cursor))
    assert expired.status_code == 400
    assert "1991-03-04" not in expired.content.decode()

    _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.CANCELLED,
        entry_type=Payment.EntryType.REVERSAL,
        original=newest,
        amount="2.00",
        received_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.VOID,
        amount="3.00",
        received_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    full = _history(api, member.id, headers, "?limit=100")
    assert full.status_code == 200, full.json()
    assert {row["status"] for row in full.json()["data"]} == {
        "DRAFT",
        "PENDING_APPROVAL",
        "POSTED",
        "REJECTED",
        "CANCELLED",
        "VOID",
    }
    assert len(full.json()["data"]) == 6

    small = _member(gym, owner.id, number="PAY-SMALL", first_name="Small")
    _payment(gym, owner.id, member=small, status=Payment.Status.POSTED, amount="1.00")

    def measured(member_id: object) -> list[dict[str, object]]:
        with CaptureQueriesContext(connection) as captured:
            response = _history(api, member_id, headers, "?limit=2")
        assert response.status_code == 200, response.json()
        return list(captured)

    measured(small.id)
    small_queries = measured(small.id)
    for index in range(30):
        _payment(
            gym,
            owner.id,
            member=member,
            status=Payment.Status.POSTED,
            amount="1.00",
            received_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            payment_id=UUID(f"33333333-3333-4333-8333-{index:012d}"),
        )
    large_queries = measured(member.id)
    assert len(small_queries) == len(large_queries)
    payment_sql = [
        query for query in large_queries if "members_payment" in str(query["sql"]).lower()
    ]
    assert len(payment_sql) == 1
    sql = str(payment_sql[0]["sql"]).lower()
    params = payment_sql[0].get("params")
    flat = params if isinstance(params, list | tuple) else ()
    assert "members_receipt" in sql
    assert "limit 3" in sql or ("limit" in sql and 3 in flat)


HISTORY_INDEX = "payment_gym_member_hist_idx"


@override_settings(**jwt_settings())
def test_member_payment_history_index_orders_the_limited_page() -> None:
    _restore_trial_features()
    client, platform_access = platform_token("payhist-index-platform@example.test")
    payload = provision(
        client, platform_access, code="PAY-E", owner_email="payhist-owner-e@example.test"
    )
    gym = Gym.objects.get(id=payload["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=payload["owner"]["gym_membership"]["id"]
    )
    headers = auth_headers(staff_token(owner))
    api = _client()
    member = _member(gym, owner.id, number="PAY-INDEX", first_name="Indexed")
    created_at = timezone.now()
    payments = [
        Payment(
            id=UUID(f"44444444-4444-4444-8444-{index:012d}"),
            gym=gym,
            member=member,
            entry_type=Payment.EntryType.CHARGE,
            purpose=Payment.Purpose.MEMBERSHIP,
            description="Indexed dues",
            amount=Decimal("1.00"),
            currency=gym.currency,
            method=Payment.Method.CASH,
            business_date=date(2024, 6, 2),
            received_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            status=Payment.Status.POSTED,
            created_by_membership_id=owner.id,
            updated_by_membership_id=owner.id,
            created_at=created_at,
            updated_at=created_at,
        )
        for index in range(300)
    ]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Payment.objects.bulk_create(payments, batch_size=100)

    with connection.cursor() as cursor:
        cursor.execute("ANALYZE members_payment")
        cursor.execute(
            """
            SELECT indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = %s
            """,
            [HISTORY_INDEX],
        )
        indexdef = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT count(*) FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = 'payment_gym_member_idx'
            """
        )
        replaced = cursor.fetchone()[0]
    assert indexdef == (
        "CREATE INDEX payment_gym_member_hist_idx ON public.members_payment "
        "USING btree (gym_id, member_id, received_at DESC, id DESC)"
    )
    assert replaced == 0

    with CaptureQueriesContext(connection) as captured:
        response = _history(api, member.id, headers, "?limit=25")
    assert response.status_code == 200, response.json()
    assert len(response.json()["data"]) == 25
    payment_sql = [
        query
        for query in captured
        if "members_payment" in str(query["sql"]).lower()
        and "order by" in str(query["sql"]).lower()
    ]
    assert len(payment_sql) == 1
    sql = str(payment_sql[0]["sql"])
    params = payment_sql[0].get("params")
    lowered = sql.lower()
    assert "members_receipt" in lowered
    assert "received_at" in lowered and "desc" in lowered
    assert " limit " in lowered

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym.id)])
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute("SET LOCAL enable_bitmapscan = off")
        cursor.execute("SET LOCAL enable_sort = off")
        if isinstance(params, list | tuple):
            cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}", params)
        else:
            cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        plan = json.dumps(cursor.fetchone()[0])
    assert HISTORY_INDEX in plan, plan
