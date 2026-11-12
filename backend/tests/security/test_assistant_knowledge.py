"""The assistant brief is this gym's rows, and only the rows the staff may see."""

from __future__ import annotations

import os
from datetime import date
from uuid import uuid4

import pytest
from django.db import transaction

from apps.assistant.knowledge import gym_facts
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member
from apps.tenants.models import Gym

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def test_brief_names_this_gym_and_hides_unpermitted_members() -> None:
    gym = Gym.objects.create(
        code=f"BR{uuid4().hex[:6]}".upper(),
        name="Brief Gym",
        status=Gym.Status.ACTIVE,
        city="Yangon",
        timezone="Asia/Yangon",
        currency="MMK",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.create(
            gym=gym,
            member_number="M-1",
            first_name="Hla",
            last_name="Hla",
            normalized_full_name="hla hla",
            join_date=date(2026, 11, 1),
            status=Member.Status.ACTIVE,
            created_by_membership_id=uuid4(),
            updated_by_membership_id=uuid4(),
        )
    hidden = gym_facts(gym, frozenset(), False)
    shown = gym_facts(gym, frozenset({"members.view"}), False)
    owner = gym_facts(gym, frozenset(), True)

    assert "Brief Gym" in hidden
    assert "Yangon" in hidden
    assert "MMK" in hidden
    assert "Members by status" not in hidden
    assert "Active staff" not in hidden
    assert "Hla" not in hidden
    assert "Members by status: ACTIVE 1" in shown
    assert "Hla Hla ACTIVE" in shown
    assert "Payments by status" in owner
    assert "Products:" in owner
    assert "Cash sessions by status" in owner
    assert "Active staff:" in owner
    assert "Today's classes:" in owner
    assert "Today's PT sessions:" in owner
