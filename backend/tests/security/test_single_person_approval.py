"""Owner and desk-manager self-approval, with the other role shapes left unchanged."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.test import override_settings

from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import GymMembership, GymMembershipRole, Role
from tests.security.test_cashbook import _adjust, _open
from tests.security.test_expenses import _category
from tests.security.test_expenses import _draft as _expense_draft
from tests.security.test_payment_controls import _approve, _pending_bank
from tests.security.test_payments import _gym, _member, _membership
from tests.security.test_tenant_gym_settings import (
    add_staff,
    auth_headers,
    jwt_settings,
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


def _grant(gym, membership: GymMembership, *role_keys: str, owner: bool = False) -> None:
    if owner:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE tenants_gym_membership_role "
                "DISABLE TRIGGER membership_role_owner_guard"
            )
    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            for key in role_keys:
                GymMembershipRole.objects.create(
                    gym=gym,
                    gym_membership=membership,
                    role=Role.objects.get(gym=gym, key=key),
                )
    finally:
        if owner:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE tenants_gym_membership_role "
                    "ENABLE TRIGGER membership_role_owner_guard"
                )


def _expense_self(api, headers, gym, category_id: str) -> int:
    created = _expense_draft(
        api,
        headers,
        gym=gym,
        category_id=category_id,
        method="BANK_TRANSFER",
        amount="12.00",
    )
    if created.status_code != 201:
        return created.status_code
    expense_id = created.json()["data"]["id"]
    submitted = api.post(
        f"/api/v1/expenses/{expense_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sod-exp-sub-{uuid4().hex}",
        **headers,
    )
    if submitted.status_code != 200:
        return submitted.status_code
    approved = api.post(
        f"/api/v1/expenses/{expense_id}/approve/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"sod-exp-ap-{uuid4().hex}",
        **headers,
    )
    return approved.status_code


@override_settings(**jwt_settings())
def test_owner_and_desk_complete_their_own_finance_without_a_second_person() -> None:
    api, gym, owner, owner_headers = _gym("SOD1")
    _other_api, other_gym, other_owner, other_headers = _gym("SOD2")
    member_id = _member(api, owner_headers, gym)
    membership_id = _membership(api, owner_headers, gym, member_id)
    category_id = str(_category(gym, "INTERNET").id)

    owner_payment = _pending_bank(api, owner_headers, gym, member_id, membership_id, "11.00")
    assert (
        _approve(api, owner_headers, owner_payment, key=f"sod-owner-pay-{uuid4().hex}").status_code
        == 200
    )
    assert _expense_self(api, owner_headers, gym, category_id) == 200

    _grant(gym, owner, "RECEPTIONIST", owner=True)
    owner_reception_payment = _pending_bank(
        api, owner_headers, gym, member_id, membership_id, "12.00"
    )
    assert (
        _approve(
            api,
            owner_headers,
            owner_reception_payment,
            key=f"sod-owner-rec-pay-{uuid4().hex}",
        ).status_code
        == 200
    )
    assert _expense_self(api, owner_headers, gym, category_id) == 200

    _grant(gym, owner, "TRAINER", owner=True)
    owner_trainer_payment = _pending_bank(
        api, owner_headers, gym, member_id, membership_id, "13.00"
    )
    assert (
        _approve(
            api,
            owner_headers,
            owner_trainer_payment,
            key=f"sod-owner-trn-pay-{uuid4().hex}",
        ).status_code
        == 200
    )
    assert _expense_self(api, owner_headers, gym, category_id) == 200

    desk = add_staff(gym, email="sod-desk@example.test", role_key="MANAGER")
    _grant(gym, desk, "RECEPTIONIST")
    desk_headers = auth_headers(staff_token(desk))
    desk_payment = _pending_bank(api, desk_headers, gym, member_id, membership_id, "14.00")
    assert (
        _approve(api, desk_headers, desk_payment, key=f"sod-desk-pay-{uuid4().hex}").status_code
        == 200
    )
    assert _expense_self(api, desk_headers, gym, category_id) == 200

    desk_trainer = add_staff(gym, email="sod-desk-trainer@example.test", role_key="MANAGER")
    _grant(gym, desk_trainer, "RECEPTIONIST", "TRAINER")
    desk_trainer_headers = auth_headers(staff_token(desk_trainer))
    desk_trainer_payment = _pending_bank(
        api, desk_trainer_headers, gym, member_id, membership_id, "15.00"
    )
    assert (
        _approve(
            api,
            desk_trainer_headers,
            desk_trainer_payment,
            key=f"sod-desk-trn-pay-{uuid4().hex}",
        ).status_code
        == 200
    )
    assert _expense_self(api, desk_trainer_headers, gym, category_id) == 200

    manager = add_staff(gym, email="sod-manager@example.test", role_key="MANAGER")
    manager_headers = auth_headers(staff_token(manager))
    manager_payment = _pending_bank(api, manager_headers, gym, member_id, membership_id, "16.00")
    assert (
        _approve(
            api, manager_headers, manager_payment, key=f"sod-mgr-pay-{uuid4().hex}"
        ).status_code
        == 403
    )
    assert _expense_self(api, manager_headers, gym, category_id) == 403
    owner_closed = _approve(api, owner_headers, manager_payment, key=f"sod-mgr-owner-{uuid4().hex}")
    assert owner_closed.status_code == 200

    receptionist = add_staff(gym, email="sod-reception@example.test", role_key="RECEPTIONIST")
    reception_headers = auth_headers(staff_token(receptionist))
    reception_payment = _pending_bank(
        api, reception_headers, gym, member_id, membership_id, "17.00"
    )
    assert (
        _approve(
            api, reception_headers, reception_payment, key=f"sod-rec-pay-{uuid4().hex}"
        ).status_code
        == 403
    )
    assert _expense_self(api, reception_headers, gym, category_id) == 403

    trainer = add_staff(gym, email="sod-trainer@example.test", role_key="TRAINER")
    trainer_headers = auth_headers(staff_token(trainer))
    assert (
        _approve(
            api, trainer_headers, reception_payment, key=f"sod-trn-pay-{uuid4().hex}"
        ).status_code
        == 403
    )
    assert _expense_self(api, trainer_headers, gym, category_id) == 403

    assert (
        _approve(api, other_headers, owner_payment, key=f"sod-cross-pay-{uuid4().hex}").status_code
        == 404
    )
    assert other_owner.gym_id == other_gym.id

    owner_session = _open(api, owner_headers, gym=gym, register="OWN1")
    assert owner_session.status_code == 201, owner_session.content
    owner_session_id = owner_session.json()["data"]["session"]["id"]
    assert (
        _adjust(
            api,
            owner_headers,
            gym=gym,
            session_id=owner_session_id,
            direction="IN",
            amount="1.00",
        ).status_code
        == 201
    )
    desk_session = _open(api, desk_headers, gym=gym, register="DSK1")
    assert desk_session.status_code == 201, desk_session.content
    assert (
        _adjust(
            api,
            desk_headers,
            gym=gym,
            session_id=desk_session.json()["data"]["session"]["id"],
            direction="IN",
            amount="1.00",
        ).status_code
        == 403
    )
    manager_session = _open(api, manager_headers, gym=gym, register="MGR1")
    assert manager_session.status_code == 201, manager_session.content
    assert (
        _adjust(
            api,
            manager_headers,
            gym=gym,
            session_id=manager_session.json()["data"]["session"]["id"],
            direction="IN",
            amount="1.00",
        ).status_code
        == 403
    )
    reception_session = _open(api, reception_headers, gym=gym, register="REC1")
    assert reception_session.status_code == 201, reception_session.content
    assert (
        _adjust(
            api,
            reception_headers,
            gym=gym,
            session_id=reception_session.json()["data"]["session"]["id"],
            direction="IN",
            amount="1.00",
        ).status_code
        == 403
    )
