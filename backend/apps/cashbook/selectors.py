from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Q, QuerySet
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.cashbook.models import CashSession, CashTransaction
from apps.tenants.models import GymMembership, Permission, Role

_SESSION_SALT = "cash-session-list"
_TRANSACTION_SALT = "cash-transaction-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_RECEPTION_KEY = "RECEPTIONIST"


def holds_protected_role(*, gym_id: UUID, membership_id: UUID, role_key: str) -> bool:
    """True when an active assignment is the protected template for ``role_key``."""

    return Role.objects.filter(
        gym_id=gym_id,
        is_active=True,
        key=role_key,
        source_template__key=role_key,
        gymmembershiprole__gym_id=gym_id,
        gymmembershiprole__gym_membership_id=membership_id,
    ).exists()


def restricts_to_own_sessions(
    *,
    gym_id: UUID,
    membership_id: UUID,
    permission_code: str,
) -> bool:
    """True when this membership holds the protected Reception role.

    Another role, including a custom role that grants ``permission_code``,
    does not remove the own-session limit.
    """

    if permission_code == "":
        return False
    return holds_protected_role(
        gym_id=gym_id,
        membership_id=membership_id,
        role_key=_RECEPTION_KEY,
    )


def membership_has_permission(*, membership: GymMembership, code: str) -> bool:
    return Permission.objects.filter(
        code=code,
        is_active=True,
        rolepermission__gym_id=membership.gym_id,
        rolepermission__role__is_active=True,
        rolepermission__role__gymmembershiprole__gym_membership=membership,
    ).exists()


def visible_sessions(
    *,
    gym_id: UUID,
    actor: GymMembership,
    permission_code: str,
) -> QuerySet[CashSession]:
    queryset = CashSession.objects.filter(gym_id=gym_id)
    if restricts_to_own_sessions(
        gym_id=gym_id,
        membership_id=actor.id,
        permission_code=permission_code,
    ):
        queryset = queryset.filter(opened_by_membership_id=actor.id)
    return queryset


def get_visible_session(
    *,
    gym_id: UUID,
    actor: GymMembership,
    session_id: UUID,
    permission_code: str,
) -> CashSession:
    session = (
        visible_sessions(
            gym_id=gym_id,
            actor=actor,
            permission_code=permission_code,
        )
        .filter(id=session_id)
        .first()
    )
    if session is None:
        raise Http404
    return session


def page_cash_sessions(
    *,
    gym_id: UUID,
    actor: GymMembership,
    status: str,
    register_code: str,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[CashSession], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor.id),
        "status": status,
        "register_code": register_code,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "created_at DESC, id DESC",
    }
    queryset = visible_sessions(
        gym_id=gym_id,
        actor=actor,
        permission_code="cashbook.view",
    )
    if status:
        queryset = queryset.filter(status=status)
    if register_code:
        queryset = queryset.filter(register_code=register_code)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-created_at", "-id")
    return _page_time(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_SESSION_SALT,
        time_attr="created_at",
    )


def page_cash_transactions(
    *,
    gym_id: UUID,
    actor: GymMembership,
    cash_session_id: UUID | None,
    business_date: date | None,
    source_type: str,
    direction: str,
    limit: int,
    after: str,
) -> tuple[list[CashTransaction], dict[str, Any]]:
    if cash_session_id is not None:
        get_visible_session(
            gym_id=gym_id,
            actor=actor,
            session_id=cash_session_id,
            permission_code="cashbook.view",
        )
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor.id),
        "cash_session_id": "" if cash_session_id is None else str(cash_session_id),
        "business_date": "" if business_date is None else business_date.isoformat(),
        "source_type": source_type,
        "direction": direction,
        "limit": str(limit),
        "ordering": "occurred_at DESC, id DESC",
    }
    visible = visible_sessions(gym_id=gym_id, actor=actor, permission_code="cashbook.view")
    queryset = CashTransaction.objects.filter(gym_id=gym_id, cash_session__in=visible)
    if cash_session_id is not None:
        queryset = queryset.filter(cash_session_id=cash_session_id)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if direction:
        queryset = queryset.filter(direction=direction)
    queryset = queryset.order_by("-occurred_at", "-id")
    return _page_time(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_TRANSACTION_SALT,
        time_attr="occurred_at",
    )


def _page_time(
    queryset: QuerySet[Any],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
    time_attr: str,
) -> tuple[list[Any], dict[str, Any]]:
    if after:
        moment, row_id = _decode_cursor(after, scope=scope, salt=salt)
        queryset = queryset.filter(
            Q(**{f"{time_attr}__lt": moment}) | Q(**{time_attr: moment, "id__lt": row_id})
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        moment = getattr(last, time_attr)
        next_after = signing.dumps(
            {time_attr: moment.isoformat(), "id": str(last.id), **scope},
            salt=salt,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


def _decode_cursor(token: str, *, scope: dict[str, str], salt: str) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        position = {key for key in loaded if key not in {"id"} and key not in scope}
        if set(loaded) != set(scope) | position | {"id"} or len(position) != 1:
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        time_key = next(iter(position))
        raw_time = loaded[time_key]
        if not isinstance(raw_time, str):
            raise TypeError
        moment = datetime.fromisoformat(raw_time)
        if moment.tzinfo is None:
            raise ValueError
        row_id = uuid.UUID(str(loaded["id"]))
    except (signing.BadSignature, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return moment, row_id
