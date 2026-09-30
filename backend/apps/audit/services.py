from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone

from .models import Outcome, PlatformAuditLog


def write_platform_audit(
    *,
    actor: Any,
    action: str,
    target_id: uuid.UUID,
    request_id: str,
    outcome: str,
    target_type: str = "GymSelectionTicket",
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
) -> None:
    PlatformAuditLog.objects.create(
        actor_user=actor,
        action=action,
        module="auth",
        target_type=target_type,
        target_id=target_id,
        old_values={},
        new_values=details or {},
        request_id=request_id[:128],
        ip_address=ip_address,
        user_agent=user_agent[:512],
        outcome=outcome,
        occurred_at=timezone.now(),
    )


def audit_ticket(
    *,
    user: Any,
    ticket_id: uuid.UUID,
    action: str,
    request_id: str,
    outcome: str = Outcome.SUCCESS,
    reason: str = "",
) -> None:
    details = {"reason": reason} if reason else {}
    write_platform_audit(
        actor=user,
        action=action,
        target_id=ticket_id,
        request_id=request_id,
        outcome=outcome,
        details=details,
    )
