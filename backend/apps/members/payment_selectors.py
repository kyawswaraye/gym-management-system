"""Payment, receipt, and daily-sales reads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core import signing
from django.db.models import Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from rest_framework.exceptions import ValidationError

from apps.inventory.quantities import format_quantity, parse_quantity
from apps.members.models import Membership, Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.sales.models import RetailReturnLine, RetailSale, RetailSaleLine

_PAYMENT_SALT = "payment-list"
_RECEIPT_SALT = "receipt-list"
_CURSOR_MAX_AGE = 24 * 60 * 60
_ZERO = Decimal("0.00")
_METHODS = (
    Payment.Method.CASH,
    Payment.Method.BANK_TRANSFER,
    Payment.Method.CARD,
    Payment.Method.MOBILE_PAYMENT,
    Payment.Method.OTHER,
)


def get_payment(*, gym_id: UUID, payment_id: UUID) -> Payment:
    payment = Payment.objects.filter(gym_id=gym_id, id=payment_id).first()
    if payment is None:
        raise Http404
    return payment


def get_receipt(*, gym_id: UUID, receipt_id: UUID) -> Receipt:
    receipt = Receipt.objects.select_related("payment").filter(gym_id=gym_id, id=receipt_id).first()
    if receipt is None:
        raise Http404
    return receipt


def page_payments(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    status: str,
    member_id: UUID | None,
    membership_id: UUID | None,
    purpose: str,
    method: str,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[Payment], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "status": status,
        "member": "" if member_id is None else str(member_id),
        "membership": "" if membership_id is None else str(membership_id),
        "purpose": purpose,
        "method": method,
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "received_at DESC, id DESC",
    }
    queryset = Payment.objects.filter(gym_id=gym_id)
    if status:
        queryset = queryset.filter(status=status)
    if member_id is not None:
        queryset = queryset.filter(member_id=member_id)
    if membership_id is not None:
        queryset = queryset.filter(membership_id=membership_id)
    if purpose:
        queryset = queryset.filter(purpose=purpose)
    if method:
        queryset = queryset.filter(method=method)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-received_at", "-id")
    return _page(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_PAYMENT_SALT,
        time_attr="received_at",
    )


def page_receipts(
    *,
    gym_id: UUID,
    actor_membership_id: UUID,
    status: str,
    payment_id: UUID | None,
    member_id: UUID | None,
    business_date: date | None,
    limit: int,
    after: str,
) -> tuple[list[Receipt], dict[str, Any]]:
    scope = {
        "gym_id": str(gym_id),
        "actor_membership_id": str(actor_membership_id),
        "status": status,
        "payment": "" if payment_id is None else str(payment_id),
        "member": "" if member_id is None else str(member_id),
        "business_date": "" if business_date is None else business_date.isoformat(),
        "limit": str(limit),
        "ordering": "issued_at DESC, id DESC",
    }
    queryset = Receipt.objects.filter(gym_id=gym_id)
    if status:
        queryset = queryset.filter(status=status)
    if payment_id is not None:
        queryset = queryset.filter(payment_id=payment_id)
    if member_id is not None:
        queryset = queryset.filter(payment__member_id=member_id)
    if business_date is not None:
        queryset = queryset.filter(business_date=business_date)
    queryset = queryset.order_by("-issued_at", "-id")
    return _page(
        queryset,
        after=after,
        limit=limit,
        scope=scope,
        salt=_RECEIPT_SALT,
        time_attr="issued_at",
    )


def daily_sales(*, gym_id: UUID, business_date: date, currency: str) -> dict[str, object]:
    """Aggregate the finalized ledger for one date.

    Posted and void charges count as gross on their own business date. Posted
    refunds and reversals subtract on their own business date, in the original
    charge's purpose bucket.     Product quantity, revenue, cost of goods sold, and
    gross profit count posted and voided retail lines on the original business
    date, then subtract a full void and each partial return on its correction
    date. ``product_total`` is the net of ``PRODUCT`` payments and reconciles
    to that line revenue.
    Draft, pending, rejected, and cancelled sales contribute nothing.
    ``pt_total`` is the net of purpose ``PT`` charges and their refunds and
    reversals. ``class_total`` stays zero.
    """

    charges = Q(
        entry_type=Payment.EntryType.CHARGE,
        status__in=[Payment.Status.POSTED, Payment.Status.VOID],
    )
    refunds = Q(entry_type=Payment.EntryType.REFUND, status=Payment.Status.POSTED)
    reversals = Q(entry_type=Payment.EntryType.REVERSAL, status=Payment.Status.POSTED)
    returns = refunds | reversals
    finalized = Payment.objects.filter(gym_id=gym_id, business_date=business_date).filter(
        charges | returns
    )
    membership = Q(purpose=Payment.Purpose.MEMBERSHIP)
    new_member = Q(membership__revenue_source=Membership.RevenueSource.NEW_MEMBER)
    renewal = Q(membership__revenue_source=Membership.RevenueSource.RENEWAL)
    other = Q(purpose=Payment.Purpose.OTHER)
    product = Q(purpose=Payment.Purpose.PRODUCT)
    pt = Q(purpose=Payment.Purpose.PT)
    original_membership = Q(original_payment__purpose=Payment.Purpose.MEMBERSHIP)
    original_new = Q(
        original_payment__membership__revenue_source=Membership.RevenueSource.NEW_MEMBER
    )
    original_renewal = Q(
        original_payment__membership__revenue_source=Membership.RevenueSource.RENEWAL
    )
    original_other = Q(original_payment__purpose=Payment.Purpose.OTHER)
    original_product = Q(original_payment__purpose=Payment.Purpose.PRODUCT)
    original_pt = Q(original_payment__purpose=Payment.Purpose.PT)
    totals = finalized.aggregate(
        gross=_sum(charges),
        refunds=_sum(refunds),
        reversals=_sum(reversals),
        new_charges=_sum(charges & membership & new_member),
        new_returns=_sum(returns & original_membership & original_new),
        renewal_charges=_sum(charges & membership & renewal),
        renewal_returns=_sum(returns & original_membership & original_renewal),
        other_charges=_sum(charges & other),
        other_returns=_sum(returns & original_other),
        product_charges=_sum(charges & product),
        product_returns=_sum(returns & original_product),
        pt_charges=_sum(charges & pt),
        pt_returns=_sum(returns & original_pt),
        **{f"method_{method}_charges": _sum(charges & Q(method=method)) for method in _METHODS},
        **{f"method_{method}_returns": _sum(returns & Q(method=method)) for method in _METHODS},
    )
    gross = _money(totals["gross"])
    refunded = _money(totals["refunds"])
    reversed_amount = _money(totals["reversals"])
    net = gross - refunded - reversed_amount
    product_payment_net = _money(totals["product_charges"]) - _money(totals["product_returns"])
    originals = _retail_lines(gym_id=gym_id, business_date=business_date, correction=False)
    corrections = _retail_lines(gym_id=gym_id, business_date=business_date, correction=True)
    returned = _returned_lines(gym_id=gym_id, business_date=business_date)
    lines = {
        "quantity": (originals["quantity"] or Decimal("0.000"))
        - (corrections["quantity"] or Decimal("0.000"))
        - (returned["quantity"] or Decimal("0.000")),
        "revenue": _money(originals["revenue"])
        - _money(corrections["revenue"])
        - _money(returned["revenue"]),
        "cogs": _money(originals["cogs"]) - _money(corrections["cogs"]) - _money(returned["cogs"]),
        "profit": _money(originals["profit"])
        - _money(corrections["profit"])
        - _money(returned["profit"]),
    }
    return {
        "date": business_date.isoformat(),
        "currency": currency,
        "gross_sales": money_text(gross),
        "refunds": money_text(refunded),
        "reversals": money_text(reversed_amount),
        "net_total": money_text(net),
        "by_payment_method": [
            {
                "method": method,
                "total": money_text(
                    _money(totals[f"method_{method}_charges"])
                    - _money(totals[f"method_{method}_returns"])
                ),
            }
            for method in _METHODS
        ],
        "new_member_total": money_text(
            _money(totals["new_charges"]) - _money(totals["new_returns"])
        ),
        "renewal_total": money_text(
            _money(totals["renewal_charges"]) - _money(totals["renewal_returns"])
        ),
        "pt_total": money_text(_money(totals["pt_charges"]) - _money(totals["pt_returns"])),
        "class_total": money_text(_ZERO),
        "product_total": money_text(product_payment_net),
        "other_total": money_text(
            _money(totals["other_charges"]) - _money(totals["other_returns"])
        ),
        "product_quantity": format_quantity(parse_quantity(lines["quantity"] or Decimal("0.000"))),
        "product_revenue": money_text(_money(lines["revenue"])),
        "product_cogs": money_text(_money(lines["cogs"])),
        "product_gross_profit": money_text(_money(lines["profit"])),
    }


def _retail_lines(*, gym_id: UUID, business_date: date, correction: bool) -> dict[str, Any]:
    queryset = RetailSaleLine.objects.filter(gym_id=gym_id, sale__gym_id=gym_id)
    if correction:
        queryset = queryset.filter(
            sale__status=RetailSale.Status.VOID,
            sale__void_business_date=business_date,
        )
    else:
        queryset = queryset.filter(
            sale__status__in=[RetailSale.Status.POSTED, RetailSale.Status.VOID],
            sale__business_date=business_date,
        )
    return queryset.aggregate(
        quantity=Coalesce(Sum("quantity"), Decimal("0.000")),
        revenue=Coalesce(Sum("line_revenue"), _ZERO),
        cogs=Coalesce(Sum("cost_of_goods_sold"), _ZERO),
        profit=Coalesce(Sum("gross_profit"), _ZERO),
    )


def _returned_lines(*, gym_id: UUID, business_date: date) -> dict[str, Any]:
    """Partial-return slices posted on the correction date."""

    return RetailReturnLine.objects.filter(
        gym_id=gym_id,
        retail_return__gym_id=gym_id,
        retail_return__business_date=business_date,
    ).aggregate(
        quantity=Coalesce(Sum("quantity"), Decimal("0.000")),
        revenue=Coalesce(Sum("refund_revenue"), _ZERO),
        cogs=Coalesce(Sum("cost_of_goods_sold"), _ZERO),
        profit=Coalesce(Sum("gross_profit"), _ZERO),
    )


def _sum(condition: Q) -> Coalesce:
    return Coalesce(Sum("amount", filter=condition), _ZERO)


def _money(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        return _ZERO
    return value.quantize(Decimal("0.01"))


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
    except (
        signing.BadSignature,
        signing.SignatureExpired,
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        raise ValidationError({"after": ["Invalid cursor."]}) from exc
    return moment, row_id
