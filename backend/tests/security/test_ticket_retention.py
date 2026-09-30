from __future__ import annotations

import os
from datetime import datetime, timedelta
from io import StringIO
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.users.models import GymSelectionTicket, User
from apps.users.services import purge_terminal_gym_selection_tickets

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def create_ticket(
    *,
    user: User,
    terminal_at: datetime | None = None,
    consumed: bool = False,
) -> GymSelectionTicket:
    now = timezone.now()
    terminal = terminal_at if terminal_at is not None else None
    issued_at = (terminal if terminal is not None else now) - timedelta(hours=1)
    eligible_ids = [uuid4(), uuid4()]
    return GymSelectionTicket.objects.create(
        user=user,
        ticket_hash=uuid4().hex,
        eligible_gym_membership_ids=eligible_ids,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
        consumed_at=terminal if consumed else None,
        revoked_at=terminal if terminal is not None and not consumed else None,
        selected_gym_membership_id=eligible_ids[0] if consumed else None,
        revocation_reason="TEST" if terminal is not None and not consumed else "",
    )


def test_ticket_purge_keeps_nonterminal_and_recent_rows() -> None:
    user = User.objects.create_user("retention@example.test", "correct horse battery staple")
    now = timezone.now()
    old_consumed = create_ticket(
        user=user,
        terminal_at=now - timedelta(days=31),
        consumed=True,
    )
    old_revoked = create_ticket(
        user=user,
        terminal_at=now - timedelta(days=31),
    )
    recent_revoked = create_ticket(
        user=user,
        terminal_at=now - timedelta(days=29),
    )
    expired_nonterminal = create_ticket(user=user)
    GymSelectionTicket.objects.filter(id=expired_nonterminal.id).update(
        issued_at=now - timedelta(days=31),
        expires_at=now - timedelta(days=31) + timedelta(minutes=5),
    )

    dry_run = StringIO()
    call_command("purge_gym_selection_tickets", "--dry-run", stdout=dry_run)
    assert "2 terminal ticket(s) eligible" in dry_run.getvalue()
    assert GymSelectionTicket.objects.filter(id__in=[old_consumed.id, old_revoked.id]).count() == 2

    output = StringIO()
    call_command("purge_gym_selection_tickets", "--batch-size=1", stdout=output)
    assert "Purged 2 terminal ticket(s)." in output.getvalue()
    assert not GymSelectionTicket.objects.filter(id__in=[old_consumed.id, old_revoked.id]).exists()
    assert (
        GymSelectionTicket.objects.filter(
            id__in=[recent_revoked.id, expired_nonterminal.id]
        ).count()
        == 2
    )


def test_runtime_role_cannot_purge_retained_ticket() -> None:
    user = User.objects.create_user(
        "runtime-retention@example.test",
        "correct horse battery staple",
    )
    old_revoked = create_ticket(
        user=user,
        terminal_at=timezone.now() - timedelta(days=31),
    )

    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE gym_runtime")
        purge_terminal_gym_selection_tickets(batch_size=1)

    assert GymSelectionTicket.objects.filter(id=old_revoked.id).exists()
