"""Tenant-scoped expense and category reads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import DecimalField, Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.cashbook.selectors import holds_protected_role
from apps.expenses.models import Expense
from apps.tenants.models import ExpenseCategory

_CATEGORY_SALT = "expense-category-list"
_EXPENSE_SALT = "expense-list"
_CURSOR_MAX_AGE = 24 * 60 * 60


def expense_net_totals(
    *,
    gym_id: UUID,
    business_date: date | None = None,
    category_id: UUID | None = None,
) -> dict[str, Any]:
    """Posted and void originals minus linked posted reversals, once each.

    Originals count on their own business date. Reversals subtract on the
    correction business date. Draft, rejected, and cancelled rows are absent.
    """

    originals = Expense.objects.filter(
        gym_id=gym_id,
        status__in=[Expense.Status.POSTED, Expense.Status.VOID],
        original_expense_id__isnull=True,
    )
    reversals = Expense.objects.filter(
        gym_id=gym_id,
        status=Expense.Status.POSTED,
        original_expense_id__isnull=False,
    )
    if business_date is not None:
        originals = originals.filter(business_date=business_date)
        reversals = reversals.filter(business_date=business_date)
    if category_id is not None:
        originals = originals.filter(category_id=category_id)
        reversals = reversals.filter(category_id=category_id)
    zero = Decimal("0.00")
    gross = originals.aggregate(
        total=Coalesce(
            Sum("amount"),
            zero,
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )["total"]
    reversed_amount = reversals.aggregate(
        total=Coalesce(
            Sum("amount"),
            zero,
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )["total"]
    gross_amount = gross if isinstance(gross, Decimal) else zero
    reversal_amount = reversed_amount if isinstance(reversed_amount, Decimal) else zero
    return {
        "gross_amount": gross_amount.quantize(Decimal("0.01")),
        "reversal_amount": reversal_amount.quantize(Decimal("0.01")),
        "net_amount": (gross_amount - reversal_amount).quantize(Decimal("0.01")),
    }


def get_category(*, gym_id: UUID, category_id: UUID) -> ExpenseCategory:
    category = ExpenseCategory.objects.filter(gym_id=gym_id, id=category_id).first()
    if category is None:
        raise Http404
    return category


def get_expense(*, gym_id: UUID, expense_id: UUID, actor_membership_id: UUID) -> Expense:
    queryset = visible_expenses(gym_id=gym_id, actor_membership_id=actor_membership_id)
    expense = queryset.filter(id=expense_id).first()
    if expense is None:
        raise Http404
    return expense


_SERVICE_ONLY_CATEGORIES = ("STAFF_SALARY", "PT_COMMISSION")


def visible_expenses(*, gym_id: UUID, actor_membership_id: UUID) -> QuerySet[Expense]:
    queryset = Expense.objects.filter(gym_id=gym_id)
    if holds_protected_role(
        gym_id=gym_id, membership_id=actor_membership_id, role_key="RECEPTIONIST"
    ):
        queryset = queryset.filter(
            Q(created_by_membership_id=actor_membership_id)
            | Q(submitted_by_membership_id=actor_membership_id)
        ).exclude(category__system_key__in=_SERVICE_ONLY_CATEGORIES)
    return queryset


def page_categories(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    is_active: bool | None,
    limit: int,
    after: str,
) -> tuple[list[ExpenseCategory], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "is_active": "" if is_active is None else str(is_active),
        "limit": str(limit),
        "ordering": "display_order ASC, code ASC, id ASC",
    }
    queryset = ExpenseCategory.objects.filter(gym_id=gym_id)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    queryset = queryset.order_by("display_order", "code", "id")
    return _page_ordered(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_CATEGORY_SALT,
        position_fields=("display_order", "code", "id"),
    )


def page_expenses(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    status: str,
    category_id: UUID | None,
    payment_method: str,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[Expense], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "status": status,
        "category": "" if category_id is None else str(category_id),
        "payment_method": payment_method,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "created_at DESC, id DESC",
    }
    queryset = visible_expenses(gym_id=gym_id, actor_membership_id=actor_membership_id)
    if status:
        queryset = queryset.filter(status=status)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)
    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-created_at", "-id")
    return _page(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_EXPENSE_SALT,
        time_attr="created_at",
    )


def _page(
    queryset: QuerySet[Any],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
    time_attr: str,
) -> tuple[list[Any], dict[str, Any]]:
    if after:
        moment, row_id = _decode_time_cursor(after, scope=scope, salt=salt)
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


def _page_ordered(
    queryset: QuerySet[Any],
    *,
    after: str,
    limit: int,
    scope: dict[str, str],
    salt: str,
    position_fields: tuple[str, ...],
) -> tuple[list[Any], dict[str, Any]]:
    if after:
        loaded = _decode_scope_cursor(after, scope=scope, salt=salt)
        display_order = int(loaded["display_order"])
        code = str(loaded["code"])
        row_id = uuid.UUID(str(loaded["id"]))
        queryset = queryset.filter(
            Q(display_order__gt=display_order)
            | Q(display_order=display_order, code__gt=code)
            | Q(display_order=display_order, code=code, id__gt=row_id)
        )
    rows = list(queryset[: limit + 1])
    has_more = len(rows) > limit
    page = rows[:limit]
    next_after = None
    if has_more and page:
        last = page[-1]
        next_after = signing.dumps(
            {field: str(getattr(last, field)) for field in position_fields} | scope,
            salt=salt,
            compress=True,
        )
    return page, {"has_more": has_more, "next_after": next_after}


def _decode_time_cursor(token: str, *, scope: dict[str, str], salt: str) -> tuple[datetime, UUID]:
    loaded = _decode_scope_cursor(token, scope=scope, salt=salt)
    position = {key for key in loaded if key not in {"id"} and key not in scope}
    if len(position) != 1:
        raise ValidationError({"after": ["Invalid cursor."]})
    time_key = next(iter(position))
    raw_time = loaded[time_key]
    if not isinstance(raw_time, str):
        raise ValidationError({"after": ["Invalid cursor."]})
    try:
        moment = datetime.fromisoformat(raw_time)
        row_id = uuid.UUID(str(loaded["id"]))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    if moment.tzinfo is None:
        raise ValidationError({"after": ["Invalid cursor."]})
    return moment, row_id


def _decode_scope_cursor(token: str, *, scope: dict[str, str], salt: str) -> dict[str, Any]:
    try:
        loaded = signing.loads(token, salt=salt, max_age=_CURSOR_MAX_AGE)
        if not isinstance(loaded, dict):
            raise signing.BadSignature
        if any(loaded.get(key) != value for key, value in scope.items()):
            raise signing.BadSignature
    except (signing.BadSignature, signing.SignatureExpired) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return loaded
