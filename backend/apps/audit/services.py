from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.core import signing
from django.db.models import Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import AuditLog, Outcome, PlatformAuditLog

TENANT_AUDIT_SCOPE_GYM = "GYM"
TENANT_AUDIT_SCOPE_SELF = "SELF"
TENANT_AUDIT_CURSOR_SALT = "tenant-audit-list"
TENANT_AUDIT_CURSOR_MAX_AGE = 24 * 60 * 60
TENANT_AUDIT_ORDERING = "occurred_at DESC, id DESC"
TENANT_AUDIT_ONLY_FIELDS = (
    "id",
    "actor_membership_id",
    "impersonator_membership_id",
    "action",
    "module",
    "target_type",
    "target_id",
    "old_values",
    "new_values",
    "request_id",
    "outcome",
    "occurred_at",
)
_CURSOR_POSITION_KEYS = frozenset({"occurred_at", "id"})


def write_platform_audit(
    *,
    actor: Any,
    action: str,
    target_id: uuid.UUID,
    request_id: str,
    outcome: str,
    target_type: str = "GymSelectionTicket",
    module: str = "auth",
    target_gym: Any | None = None,
    effective_user: Any | None = None,
    old_values: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
) -> None:
    PlatformAuditLog.objects.create(
        actor_user=actor,
        action=action,
        module=module,
        target_gym=target_gym,
        target_type=target_type,
        target_id=target_id,
        effective_user=effective_user,
        old_values=old_values or {},
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


_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "credential")


def redact_audit_values(value: Any) -> Any:
    """Return a JSON-safe copy with credential-like keys removed."""

    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SENSITIVE_KEY_PARTS):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_audit_values(item)
        return redacted
    if isinstance(value, list):
        return [redact_audit_values(item) for item in value]
    return value


def write_tenant_audit(
    *,
    gym: Any,
    actor_user: Any,
    actor_membership_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    request_id: str,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str = "",
    module: str = "owners",
) -> None:
    AuditLog.objects.create(
        gym=gym,
        actor_user=actor_user,
        actor_membership_id=actor_membership_id,
        action=action,
        module=module,
        target_type=target_type,
        target_id=target_id,
        old_values=redact_audit_values(old_values or {}),
        new_values=redact_audit_values(new_values or {}),
        request_id=request_id[:128],
        ip_address=ip_address,
        user_agent=user_agent[:512],
        outcome=Outcome.SUCCESS,
        occurred_at=timezone.now(),
    )


def tenant_audit_scope(*, is_owner: bool, membership_status: str) -> str:
    """Return GYM for an active owner and SELF for every other caller.

    SELF is the conservative conditional grant. A manager or accountant with
    ``audit.view`` sees only rows that caller performed, not other staff,
    owners, or platform-written tenant rows.
    """

    if is_owner and membership_status == "ACTIVE":
        return TENANT_AUDIT_SCOPE_GYM
    return TENANT_AUDIT_SCOPE_SELF


def tenant_audit_cursor_scope(
    *,
    gym_id: uuid.UUID,
    scope: str,
    actor_membership_id: uuid.UUID,
    action: str,
    module: str,
    outcome: str,
    target_type: str,
    target_id: uuid.UUID | None,
) -> dict[str, str]:
    """Bind a cursor to gym, scope, SELF membership, and every list filter."""

    membership_binding = str(actor_membership_id) if scope == TENANT_AUDIT_SCOPE_SELF else ""
    return {
        "gym_id": str(gym_id),
        "scope": scope,
        "actor_membership_id": membership_binding,
        "action": action,
        "module": module,
        "outcome": outcome,
        "target_type": target_type,
        "target_id": "" if target_id is None else str(target_id),
        "ordering": TENANT_AUDIT_ORDERING,
    }


def encode_tenant_audit_cursor(
    *,
    occurred_at: datetime,
    audit_id: uuid.UUID,
    scope: dict[str, str],
) -> str:
    return signing.dumps(
        {"occurred_at": occurred_at.isoformat(), "id": str(audit_id), **scope},
        salt=TENANT_AUDIT_CURSOR_SALT,
        compress=True,
    )


def decode_tenant_audit_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, uuid.UUID]:
    try:
        loaded = signing.loads(
            token,
            salt=TENANT_AUDIT_CURSOR_SALT,
            max_age=TENANT_AUDIT_CURSOR_MAX_AGE,
        )
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        expected_keys = set(scope) | _CURSOR_POSITION_KEYS
        if set(loaded) != expected_keys or any(
            loaded.get(key) != value for key, value in scope.items()
        ):
            raise signing.BadSignature
        occurred_raw = loaded["occurred_at"]
        if not isinstance(occurred_raw, str):
            raise TypeError
        occurred_at = datetime.fromisoformat(occurred_raw)
        if occurred_at.tzinfo is None:
            raise ValueError
        audit_id = uuid.UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return occurred_at, audit_id


def list_tenant_audit_logs(
    *,
    gym_id: uuid.UUID,
    membership_id: uuid.UUID,
    is_owner: bool,
    membership_status: str,
    action: str,
    module: str,
    outcome: str,
    target_type: str,
    target_id: uuid.UUID | None,
    limit: int,
    after: str,
) -> tuple[list[AuditLog], dict[str, Any]]:
    """Return one gym-scoped page and metadata that states the effective scope."""

    scope = tenant_audit_scope(is_owner=is_owner, membership_status=membership_status)
    queryset: QuerySet[AuditLog] = AuditLog.objects.filter(gym_id=gym_id)
    if scope == TENANT_AUDIT_SCOPE_SELF:
        queryset = queryset.filter(actor_membership_id=membership_id)
    elif scope != TENANT_AUDIT_SCOPE_GYM:
        queryset = queryset.none()
    if action:
        queryset = queryset.filter(action=action)
    if module:
        queryset = queryset.filter(module=module)
    if outcome:
        queryset = queryset.filter(outcome=outcome)
    if target_type:
        queryset = queryset.filter(target_type=target_type)
    if target_id is not None:
        queryset = queryset.filter(target_id=target_id)
    queryset = queryset.only(*TENANT_AUDIT_ONLY_FIELDS).order_by("-occurred_at", "-id")
    cursor_scope = tenant_audit_cursor_scope(
        gym_id=gym_id,
        scope=scope,
        actor_membership_id=membership_id,
        action=action,
        module=module,
        outcome=outcome,
        target_type=target_type,
        target_id=target_id,
    )
    if after:
        occurred_at, audit_id = decode_tenant_audit_cursor(after, scope=cursor_scope)
        queryset = queryset.filter(
            Q(occurred_at__lt=occurred_at) | Q(occurred_at=occurred_at, id__lt=audit_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_after = None
    if has_more and rows:
        last = rows[-1]
        next_after = encode_tenant_audit_cursor(
            occurred_at=last.occurred_at,
            audit_id=last.id,
            scope=cursor_scope,
        )
    return rows, {"has_more": has_more, "next_after": next_after, "scope": scope}


def tenant_audit_data(row: AuditLog) -> dict[str, Any]:
    """Serialize the public audit fields and redact credential-like keys again."""

    return {
        "id": row.id,
        "actor_membership_id": row.actor_membership_id,
        "impersonator_membership_id": row.impersonator_membership_id,
        "action": row.action,
        "module": row.module,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "old_values": redact_audit_values(row.old_values),
        "new_values": redact_audit_values(row.new_values),
        "request_id": row.request_id,
        "outcome": row.outcome,
        "occurred_at": row.occurred_at,
    }
