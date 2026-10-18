"""Tenant-scoped retail sale reads.

A protected Reception membership sees only sales it created or submitted,
and never a posted or void sale. Another creator's draft is absent.
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
from apps.sales.models import RetailSale

_SALT = "retail-sale-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_HIDDEN_FROM_RECEPTION = (RetailSale.Status.POSTED, RetailSale.Status.VOID)


def get_sale(*, gym_id: UUID, sale_id: UUID, actor_membership_id: UUID) -> RetailSale:
    sale = (
        visible_sales(gym_id=gym_id, actor_membership_id=actor_membership_id)
        .filter(id=sale_id)
        .first()
    )
    if sale is None:
        raise Http404
    return sale


def visible_sales(*, gym_id: UUID, actor_membership_id: UUID) -> QuerySet[RetailSale]:
    queryset = RetailSale.objects.filter(gym_id=gym_id)
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


def page_sales(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    status: str,
    business_date: date | None,
    member_id: UUID | None,
    limit: int,
    after: str,
) -> tuple[list[RetailSale], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "status": status,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "member": "" if member_id is None else str(member_id),
        "limit": str(limit),
        "ordering": "created_at DESC, id DESC",
    }
    queryset = visible_sales(gym_id=gym_id, actor_membership_id=actor_membership_id)
    if status:
        queryset = queryset.filter(status=status)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    queryset = queryset.order_by("-created_at", "-id")
    return _page(queryset, after=after, limit=limit, scope=scope)


def _page(
    queryset: QuerySet[RetailSale],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
) -> tuple[list[RetailSale], dict[str, Any]]:
    if after:
        moment, row_id = _decode_cursor(after, scope=scope)
        queryset = queryset.filter(Q(created_at__lt=moment) | Q(created_at=moment, id__lt=row_id))
    rows = list(queryset.prefetch_related("lines")[: limit + 1])
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
