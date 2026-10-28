"""Tenant-scoped salary period reads.

A protected Reception membership sees only periods it created or submitted,
and never a posted or void period. Another creator's draft is absent. The
payee object is limited to id, staff code, and display name.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Q, QuerySet
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.cashbook.selectors import holds_protected_role
from apps.salaries.models import SalaryPeriod

_SALT = "salary-period-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_HIDDEN_FROM_RECEPTION = (SalaryPeriod.Status.POSTED, SalaryPeriod.Status.VOID)


def get_period(*, gym_id: UUID, period_id: UUID, actor_membership_id: UUID) -> SalaryPeriod:
    period = (
        visible_periods(gym_id=gym_id, actor_membership_id=actor_membership_id)
        .filter(id=period_id)
        .prefetch_related("lines__staff_profile")
        .first()
    )
    if period is None:
        raise Http404
    return period


def visible_periods(*, gym_id: UUID, actor_membership_id: UUID) -> QuerySet[SalaryPeriod]:
    queryset = SalaryPeriod.objects.filter(gym_id=gym_id)
    if holds_protected_role(
        gym_id=gym_id,
        membership_id=actor_membership_id,
        role_key="RECEPTIONIST",
    ):
        queryset = queryset.filter(
            Q(created_by_membership_id=actor_membership_id)
            | Q(submitted_by_membership_id=actor_membership_id)
        ).exclude(status__in=_HIDDEN_FROM_RECEPTION)
    return queryset


def page_periods(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    status: str,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[SalaryPeriod], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "status": status,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "created_at DESC, id DESC",
    }
    queryset = visible_periods(gym_id=gym_id, actor_membership_id=actor_membership_id)
    if status:
        queryset = queryset.filter(status=status)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-created_at", "-id")
    return _page(queryset, after=after, limit=limit, scope=scope)


def _page(
    queryset: QuerySet[SalaryPeriod],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
) -> tuple[list[SalaryPeriod], dict[str, Any]]:
    if after:
        moment, row_id = _decode_cursor(after, scope=scope)
        queryset = queryset.filter(Q(created_at__lt=moment) | Q(created_at=moment, id__lt=row_id))
    rows = list(queryset.prefetch_related("lines__staff_profile")[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {"created_at": last.created_at.isoformat(), "id": str(last.id), **scope},
            salt=_SALT,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


def _decode_cursor(token: str, *, scope: dict[str, str]) -> tuple[datetime, UUID]:
    try:
        loaded = signing.loads(token, salt=_SALT, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
        raw_time = loaded["created_at"]
        if not isinstance(raw_time, str):
            raise signing.BadSignature
        moment = datetime.fromisoformat(raw_time)
        row_id = uuid.UUID(str(loaded["id"]))
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError, KeyError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    if moment.tzinfo is None:
        raise ValidationError({"after": ["Invalid cursor."]})
    return moment, row_id
