"""Retail sale drafts, approval posting, full void, and partial return.

Lock order inside approval: the sale, its lines, the member when present,
then products ordered by id. Stock movements are appended only after those
locks. The product payment, receipt, and cash movement are created next, and
the sale is marked posted last. A failure rolls all of it back.

Full void locks the posted sale, its lines, products in id order, the product
charge, receipts, the named destination session, and any existing correction.
The original sale movements and cash movement are read under that sale lock.
Those tables, and the return tables, are insert-only for the runtime role, so
they are not locked with ``SELECT FOR UPDATE``. The void appends one
``SALE_RETURN`` per line, reverses the product charge, and marks the sale void
last. A return row makes that void invalid. A cash void names an open
destination session and does not reopen the original till.

A partial return locks the posted sale first, then the requested lines,
products, the product charge, receipts, the destination session, and existing
corrections. Prior returns and the original sale movements are read under the
sale lock. It appends one ``SALE_RETURN`` per requested line, posts a product
refund and correction receipt only when the allocated refund is positive, then
writes the immutable return header and lines. The sale stays posted.

Price and cost are copied when the draft is saved. Submission and approval
compare those snapshots with the current active product and fail when they
differ. They do not rewrite the submitted totals.

``mutate()`` reloads the actor, the finance entitlement, and the live
permission before it locks or writes. Reception-origin cash uses the
originating Reception membership's own open till.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import DatabaseError, IntegrityError, connection
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import revalidate_cash_actor
from apps.inventory.models import Product, StockMovement
from apps.inventory.quantities import parse_quantity
from apps.inventory.services import post_sale_movement, post_sale_return
from apps.members.models import Member, Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.members.payment_services import (
    post_retail_product_payment,
    post_retail_product_refund,
    reverse_retail_product_payment,
)
from apps.members.services import gym_today
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailReturn, RetailReturnLine, RetailSale, RetailSaleLine
from apps.sales.pricing import allocate_return_slice, extend_money
from apps.sales.selectors import visible_sales
from apps.tenants.models import Gym, GymMembership

_ZERO = Decimal("0.00")


def create_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> RetailSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.create")
    member = _member(gym=gym, member_id=fields.get("member_id"))
    business_date = _business_date(gym=gym, value=fields["business_date"])
    priced = _price_lines(gym=gym, lines=list(fields["lines"]))
    totals = _totals(priced)
    sale = RetailSale.objects.create(
        gym=gym,
        member=member,
        business_date=business_date,
        currency=gym.currency,
        payment_method=fields["payment_method"],
        reference_number=str(fields.get("reference_number") or ""),
        status=RetailSale.Status.DRAFT,
        created_with_reception_scope=_holds_reception(gym_id=gym.id, membership_id=actor.id),
        created_by_membership_id=actor.id,
        updated_by_membership_id=actor.id,
        **totals,
    )
    _insert_lines(sale=sale, actor=actor, priced=priced)
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_DRAFT_CREATED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    sale.refresh_from_db()
    return sale


def update_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> RetailSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.update")
    sale = _lock_visible(gym_id=gym.id, sale_id=sale_id, actor=actor)
    if sale.status != RetailSale.Status.DRAFT or sale.submitted_at is not None:
        raise ValidationError({"status": ["Only a draft retail sale can be edited."]})
    if "member_id" in fields:
        sale.member = _member(gym=gym, member_id=fields.get("member_id"))
    if "business_date" in fields:
        sale.business_date = _business_date(gym=gym, value=fields["business_date"])
    if "payment_method" in fields:
        sale.payment_method = fields["payment_method"]
    if "reference_number" in fields:
        sale.reference_number = str(fields.get("reference_number") or "")
    if "lines" in fields:
        priced = _price_lines(gym=gym, lines=list(fields["lines"]))
    else:
        priced = _reprice_stored(gym=gym, sale=sale)
    totals = _totals(priced)
    sale.lines.all().delete()
    _insert_lines(sale=sale, actor=actor, priced=priced)
    for name, value in totals.items():
        setattr(sale, name, value)
    sale.currency = gym.currency
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_DRAFT_UPDATED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    sale.refresh_from_db()
    return sale


def submit_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> RetailSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.submit")
    sale = _lock_visible(gym_id=gym.id, sale_id=sale_id, actor=actor)
    if sale.status != RetailSale.Status.DRAFT or sale.submitted_at is not None:
        raise ValidationError({"status": ["Only a draft retail sale can be submitted."]})
    _business_date(gym=gym, value=sale.business_date)
    if sale.currency != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    _member(gym=gym, member_id=sale.member_id)
    lines = _lock_lines(sale)
    _assert_current_prices(gym=gym, lines=lines)
    now = timezone.now()
    sale.status = RetailSale.Status.PENDING_APPROVAL
    sale.submitted_at = now
    sale.submitted_by_membership_id = actor.id
    sale.submitted_with_reception_scope = _holds_reception(gym_id=gym.id, membership_id=actor.id)
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_SUBMITTED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return sale


def approve_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[RetailSale, Payment, Receipt, CashTransaction | None]:
    if not connection.in_atomic_block:
        raise RuntimeError("Retail approval requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    sale = _lock_sale(gym_id=gym.id, sale_id=sale_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if sale.status != RetailSale.Status.PENDING_APPROVAL:
        raise RetailPostingConflict()
    if actor.id in {sale.created_by_membership_id, sale.submitted_by_membership_id}:
        raise PermissionDenied()
    if sale.currency != gym.currency:
        raise ValidationError({"currency": ["Currency must match the selected gym."]})
    _business_date(gym=gym, value=sale.business_date)
    if sale.payment_method == Payment.Method.CASH:
        if cash_session_id is None:
            raise ValidationError({"cash_session_id": ["A cash sale requires an open session."]})
    elif cash_session_id is not None:
        raise ValidationError({"cash_session_id": ["Only a cash sale can use a cash session."]})
    member = _lock_member(gym=gym, member_id=sale.member_id)
    lines = _lock_lines(sale)
    products = _lock_products(gym_id=gym.id, lines=lines)
    _assert_current_prices(gym=gym, lines=lines, products=products)
    for line in lines:
        post_sale_movement(
            gym=gym,
            actor=actor,
            product=products[line.product_id],
            line=line,
            business_date=sale.business_date,
        )
    payment, receipt, movement = post_retail_product_payment(
        gym=gym,
        approver=actor,
        retail_sale_id=sale.id,
        member=member,
        created_by_membership_id=sale.created_by_membership_id,
        submitted_by_membership_id=sale.submitted_by_membership_id,
        amount=sale.total_amount,
        currency=sale.currency,
        method=sale.payment_method,
        business_date=sale.business_date,
        reference_number=sale.reference_number,
        cash_session_id=cash_session_id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if movement is not None:
        _assert_sale_till(sale=sale, session=movement.cash_session)
    now = timezone.now()
    sale.status = RetailSale.Status.POSTED
    sale.approved_at = now
    sale.approved_by_membership_id = actor.id
    sale.posted_at = now
    sale.posted_by_membership_id = actor.id
    sale.payment = payment
    sale.receipt = receipt
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_APPROVED_POSTED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "payment_id": str(payment.id),
            "receipt_number": receipt.receipt_number,
            "total_amount": money_text(sale.total_amount),
        },
    )
    return sale, payment, receipt, movement


def void_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    business_date: date | None,
    cash_session_id: UUID | None,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Void one posted sale, restore its stock, and reverse its product charge."""

    if not connection.in_atomic_block:
        raise RuntimeError("Retail void requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.void")
    sale = _lock_sale(gym_id=gym.id, sale_id=sale_id)
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.void")
    if sale.status != RetailSale.Status.POSTED or sale.payment_id is None:
        raise RetailPostingConflict()
    if RetailReturn.objects.filter(gym_id=gym.id, sale_id=sale.id).exists():
        raise RetailPostingConflict()
    if sale.payment_method == Payment.Method.CASH:
        if cash_session_id is None:
            raise ValidationError(
                {"cash_session_id": ["A cash sale void requires an open session."]}
            )
    elif cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["Only a cash sale void can use a cash session."]}
        )
    chosen_date = _correction_date(
        gym=gym, original_date=sale.business_date, requested=business_date
    )
    lines = list(sale.lines.select_for_update().order_by("id"))
    if not lines:
        raise RetailPostingConflict()
    line_ids = [line.id for line in lines]
    originals = list(
        StockMovement.objects.filter(
            gym_id=gym.id,
            retail_sale_line_id__in=line_ids,
            movement_type=StockMovement.Type.SALE,
        ).order_by("id")
    )
    by_line = {movement.retail_sale_line_id: movement for movement in originals}
    if set(by_line) != set(line_ids) or len(originals) != len(lines):
        raise RetailPostingConflict()
    products = _lock_products(gym_id=gym.id, lines=lines)
    payment = Payment.objects.select_for_update().filter(gym_id=gym.id, id=sale.payment_id).first()
    if payment is None or payment.retail_sale_id != sale.id:
        raise RetailPostingConflict()
    receipts = list(
        Receipt.objects.select_for_update()
        .filter(gym_id=gym.id, payment_id=payment.id)
        .order_by("id")
    )
    if payment.cash_transaction_id is not None:
        locked_cash = CashTransaction.objects.filter(
            gym_id=gym.id, id=payment.cash_transaction_id
        ).first()
        if locked_cash is None:
            raise RetailPostingConflict()
    if cash_session_id is not None:
        session = (
            CashSession.objects.select_for_update()
            .filter(gym_id=gym.id, id=cash_session_id)
            .first()
        )
        if (
            session is None
            or session.status != CashSession.Status.OPEN
            or session.currency != sale.currency
            or session.business_date != chosen_date
        ):
            raise ValidationError(
                {
                    "cash_session_id": [
                        "Select an open cash session for the correction date and currency."
                    ]
                }
            )
    related = list(
        Payment.objects.select_for_update()
        .filter(gym_id=gym.id, original_payment_id=payment.id)
        .order_by("id")
    )
    if related or payment.reversal_payment_id is not None:
        raise RetailPostingConflict()
    returns: list[StockMovement] = []
    for line in lines:
        returns.append(
            post_sale_return(
                gym=gym,
                actor=actor,
                product=products[line.product_id],
                line=line,
                original=by_line[line.id],
                business_date=chosen_date,
                reason=reason,
            )
        )
    reversed_payment = reverse_retail_product_payment(
        gym=gym,
        actor=actor,
        original=payment,
        receipts=receipts,
        reason=reason,
        business_date=chosen_date,
        cash_session_id=cash_session_id,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    now = timezone.now()
    sale.status = RetailSale.Status.VOID
    sale.voided_at = now
    sale.voided_by_membership_id = actor.id
    sale.void_business_date = chosen_date
    sale.void_reason = reason
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_VOIDED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "void_business_date": chosen_date.isoformat(),
            "reversal_payment_id": str(reversed_payment["correction"].id),
            "stock_return_count": len(returns),
        },
    )
    return {
        "sale": sale,
        "stock_movements": returns,
        **reversed_payment,
    }


def return_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    business_date: date | None,
    cash_session_id: UUID | None,
    lines: list[dict[str, Any]],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> dict[str, Any]:
    """Return part of a posted sale and leave that sale posted.

    Amounts come from the cumulative half-up slices of the immutable lines.
    Stock is restored first. A positive refund then posts one product refund,
    one correction receipt, and, for cash, one outbound movement that does not
    set ``reversal_of``. The return header and lines are last. Deferred
    database checks run before this function returns.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("Retail return requires an active transaction.")
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.void")
    sale = _lock_sale(gym_id=gym.id, sale_id=sale_id)
    if not lines or len(lines) > 100:
        raise ValidationError({"lines": ["Enter from 1 to 100 return lines."]})
    requested = {UUID(str(row["line_id"])): parse_quantity(row["quantity"]) for row in lines}
    if len(requested) != len(lines):
        raise ValidationError({"lines": ["Each sale line can appear once."]})
    locked_lines = list(
        RetailSaleLine.objects.select_for_update()
        .filter(gym_id=gym.id, sale_id=sale.id, id__in=requested)
        .order_by("id")
    )
    if len(locked_lines) != len(requested):
        raise ValidationError({"lines": ["Select a line of this sale."]})
    prior_headers = list(
        RetailReturn.objects.filter(gym_id=gym.id, sale_id=sale.id).order_by("created_at", "id")
    )
    prior_lines = list(
        RetailReturnLine.objects.filter(
            gym_id=gym.id, retail_return_id__in=[row.id for row in prior_headers]
        ).order_by("id")
    )
    originals = list(
        StockMovement.objects.filter(
            gym_id=gym.id,
            retail_sale_line_id__in=requested,
            movement_type=StockMovement.Type.SALE,
        ).order_by("id")
    )
    by_line = {movement.retail_sale_line_id: movement for movement in originals}
    if set(by_line) != set(requested) or len(originals) != len(locked_lines):
        raise RetailPostingConflict()
    products = _lock_products(gym_id=gym.id, lines=locked_lines)
    if sale.payment_id is None or sale.receipt_id is None:
        raise RetailPostingConflict()
    payment = Payment.objects.select_for_update().filter(gym_id=gym.id, id=sale.payment_id).first()
    if (
        payment is None
        or payment.retail_sale_id != sale.id
        or payment.status != Payment.Status.POSTED
        or payment.entry_type != Payment.EntryType.CHARGE
        or payment.reversal_payment_id is not None
    ):
        raise RetailPostingConflict()
    receipts = list(
        Receipt.objects.select_for_update()
        .filter(gym_id=gym.id, payment_id=payment.id)
        .order_by("id")
    )
    issued_original = any(
        row.status == Receipt.Status.ISSUED and row.id == sale.receipt_id for row in receipts
    )
    if not issued_original:
        raise RetailPostingConflict()
    if payment.cash_transaction_id is not None:
        locked_cash = CashTransaction.objects.filter(
            gym_id=gym.id, id=payment.cash_transaction_id
        ).first()
        if locked_cash is None:
            raise RetailPostingConflict()
    related = list(
        Payment.objects.select_for_update()
        .filter(gym_id=gym.id, original_payment_id=payment.id)
        .order_by("id")
    )
    if any(row.entry_type == Payment.EntryType.REVERSAL for row in related):
        raise RetailPostingConflict()
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.void")
    if sale.status != RetailSale.Status.POSTED:
        raise RetailPostingConflict()
    chosen_date = _correction_date(
        gym=gym, original_date=sale.business_date, requested=business_date
    )
    prior_taken: dict[UUID, Decimal] = {}
    for prior in prior_lines:
        prior_taken[prior.sale_line_id] = prior_taken.get(
            prior.sale_line_id, Decimal("0.000")
        ) + parse_quantity(prior.quantity)
    priced: list[dict[str, Any]] = []
    for line in locked_lines:
        quantity = requested[line.id]
        sold = parse_quantity(line.quantity)
        already = prior_taken.get(line.id, Decimal("0.000"))
        if quantity > sold - already:
            raise RetailPostingConflict()
        discount = allocate_return_slice(Decimal(line.discount), already, quantity, sold)
        revenue = allocate_return_slice(Decimal(line.line_revenue), already, quantity, sold)
        cost = allocate_return_slice(Decimal(line.cost_of_goods_sold), already, quantity, sold)
        priced.append(
            {
                "line": line,
                "quantity": quantity,
                "discount": discount,
                "refund_revenue": revenue,
                "cost_of_goods_sold": cost,
                "gross_profit": revenue - cost,
            }
        )
    refund_amount = sum((row["refund_revenue"] for row in priced), Decimal("0.00"))
    discount_total = sum((row["discount"] for row in priced), Decimal("0.00"))
    cogs_total = sum((row["cost_of_goods_sold"] for row in priced), Decimal("0.00"))
    gross_profit_total = refund_amount - cogs_total
    refunded_already = sum(
        (
            Decimal(row.amount)
            for row in related
            if row.status == Payment.Status.POSTED and row.entry_type == Payment.EntryType.REFUND
        ),
        Decimal("0.00"),
    )
    if refund_amount > Decimal(payment.amount) - refunded_already:
        raise RetailPostingConflict()
    if sale.payment_method == Payment.Method.CASH and refund_amount > 0:
        if cash_session_id is None:
            raise ValidationError({"cash_session_id": ["A cash refund requires an open session."]})
    elif cash_session_id is not None:
        raise ValidationError(
            {"cash_session_id": ["Only a positive cash refund can use a cash session."]}
        )
    session = None
    if cash_session_id is not None:
        session = (
            CashSession.objects.select_for_update()
            .filter(gym_id=gym.id, id=cash_session_id)
            .first()
        )
        if (
            session is None
            or session.status != CashSession.Status.OPEN
            or session.currency != sale.currency
            or session.business_date != chosen_date
        ):
            raise ValidationError(
                {
                    "cash_session_id": [
                        "Select an open cash session for the correction date and currency."
                    ]
                }
            )
    try:
        movements: list[StockMovement] = []
        for row in priced:
            movements.append(
                post_sale_return(
                    gym=gym,
                    actor=actor,
                    product=products[row["line"].product_id],
                    line=row["line"],
                    original=by_line[row["line"].id],
                    business_date=chosen_date,
                    reason=reason,
                    quantity=row["quantity"],
                )
            )
        refund_payment = None
        correction_receipt = None
        movement = None
        if refund_amount > 0:
            refund_payment, correction_receipt, movement = post_retail_product_refund(
                gym=gym,
                actor=actor,
                original=payment,
                amount=refund_amount,
                business_date=chosen_date,
                reason=reason,
                cash_session_id=None if session is None else session.id,
                request_id=request_id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        retail_return = RetailReturn.objects.create(
            gym=gym,
            sale=sale,
            business_date=chosen_date,
            reason=reason,
            currency=sale.currency,
            payment_method=sale.payment_method,
            refund_amount=refund_amount,
            discount_total=discount_total,
            cogs_total=cogs_total,
            gross_profit_total=gross_profit_total,
            payment=refund_payment,
            receipt=correction_receipt,
            cash_session=session,
            returned_by_membership_id=actor.id,
        )
        RetailReturnLine.objects.bulk_create(
            [
                RetailReturnLine(
                    gym=gym,
                    retail_return=retail_return,
                    sale_line=row["line"],
                    quantity=row["quantity"],
                    discount=row["discount"],
                    refund_revenue=row["refund_revenue"],
                    cost_of_goods_sold=row["cost_of_goods_sold"],
                    gross_profit=row["gross_profit"],
                    stock_movement=stock,
                    created_by_membership_id=actor.id,
                )
                for row, stock in zip(priced, movements, strict=True)
            ]
        )
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    except DatabaseError as exc:
        if _return_guard_conflict(exc):
            raise RetailPostingConflict() from exc
        raise
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_RETURNED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        extra={
            "retail_return_id": str(retail_return.id),
            "business_date": chosen_date.isoformat(),
            "refund_amount": money_text(refund_amount),
            "stock_return_count": len(movements),
        },
    )
    return {
        "sale": sale,
        "retail_return": retail_return,
        "return_lines": list(retail_return.lines.order_by("sale_line_id", "id")),
        "refund_payment": refund_payment,
        "correction_receipt": correction_receipt,
        "cash_transaction": movement,
        "stock_movements": movements,
    }


def reject_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> RetailSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.approve")
    _assert_approver(gym_id=gym.id, actor=actor)
    sale = _lock_sale(gym_id=gym.id, sale_id=sale_id)
    _assert_approver(gym_id=gym.id, actor=actor)
    if sale.status != RetailSale.Status.PENDING_APPROVAL:
        raise RetailPostingConflict()
    if actor.id in {sale.created_by_membership_id, sale.submitted_by_membership_id}:
        raise PermissionDenied()
    now = timezone.now()
    sale.status = RetailSale.Status.REJECTED
    sale.rejected_at = now
    sale.rejected_by_membership_id = actor.id
    sale.rejection_reason = reason
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_REJECTED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return sale


def cancel_sale(
    *,
    gym: Gym,
    actor: GymMembership,
    sale_id: UUID,
    reason: str,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> RetailSale:
    gym, actor = _reload(gym_id=gym.id, membership_id=actor.id, permission_code="retail.update")
    sale = _lock_visible(gym_id=gym.id, sale_id=sale_id, actor=actor)
    if sale.status != RetailSale.Status.DRAFT or sale.submitted_at is not None:
        raise ValidationError({"status": ["Only an unsubmitted draft can be cancelled."]})
    now = timezone.now()
    sale.status = RetailSale.Status.CANCELLED
    sale.cancelled_at = now
    sale.cancelled_by_membership_id = actor.id
    sale.reason = reason
    sale.updated_by_membership_id = actor.id
    sale.save()
    _audit(
        gym=gym,
        actor=actor,
        action="RETAIL_SALE_CANCELLED",
        sale=sale,
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return sale


def command_route(*, sale_id: UUID | None, action: str) -> str:
    if sale_id is None:
        return "/api/v1/retail-sales/"
    if not action:
        return f"/api/v1/retail-sales/{sale_id}/"
    return f"/api/v1/retail-sales/{sale_id}/{action}/"


def _reload(
    *, gym_id: UUID, membership_id: UUID, permission_code: str
) -> tuple[Gym, GymMembership]:
    return revalidate_cash_actor(
        gym_id=gym_id,
        membership_id=membership_id,
        permission_code=permission_code,
    )


def _assert_approver(*, gym_id: UUID, actor: GymMembership) -> None:
    if holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="RECEPTIONIST"):
        raise PermissionDenied()
    manager = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="MANAGER")
    owner = holds_protected_role(gym_id=gym_id, membership_id=actor.id, role_key="OWNER")
    if not manager and not owner:
        raise PermissionDenied()
    if not membership_has_permission(membership=actor, code="retail.approve"):
        raise PermissionDenied()


def _lock_sale(*, gym_id: UUID, sale_id: UUID) -> RetailSale:
    sale = RetailSale.objects.select_for_update().filter(gym_id=gym_id, id=sale_id).first()
    if sale is None:
        raise Http404
    return sale


def _lock_visible(*, gym_id: UUID, sale_id: UUID, actor: GymMembership) -> RetailSale:
    sale = (
        visible_sales(gym_id=gym_id, actor_membership_id=actor.id)
        .select_for_update()
        .filter(id=sale_id)
        .first()
    )
    if sale is None:
        raise Http404
    return sale


def _lock_lines(sale: RetailSale) -> list[RetailSaleLine]:
    return list(sale.lines.select_for_update().order_by("product_id", "id"))


def _lock_products(*, gym_id: UUID, lines: list[RetailSaleLine]) -> dict[UUID, Product]:
    product_ids = [line.product_id for line in lines]
    rows = (
        Product.objects.select_for_update().filter(gym_id=gym_id, id__in=product_ids).order_by("id")
    )
    found = {row.id: row for row in rows}
    if set(found) != set(product_ids):
        raise ValidationError({"lines": ["Select a product of this gym."]})
    return found


def _lock_member(*, gym: Gym, member_id: UUID | None) -> Member | None:
    if member_id is None:
        return None
    member = Member.objects.select_for_update().filter(gym_id=gym.id, id=member_id).first()
    if member is None or member.status != Member.Status.ACTIVE:
        raise ValidationError({"member_id": ["Select an active member of this gym."]})
    return member


def _member(*, gym: Gym, member_id: UUID | None) -> Member | None:
    if member_id is None:
        return None
    member = Member.objects.filter(gym_id=gym.id, id=member_id).first()
    if member is None or member.status != Member.Status.ACTIVE:
        raise ValidationError({"member_id": ["Select an active member of this gym."]})
    return member


def _correction_date(*, gym: Gym, original_date: date, requested: date | None) -> date:
    chosen = gym_today(gym) if requested is None else requested
    if chosen > gym_today(gym):
        raise ValidationError({"business_date": ["The business date cannot be after today."]})
    if chosen < original_date:
        raise ValidationError(
            {"business_date": ["The business date cannot be before the original business date."]}
        )
    return chosen


def _business_date(*, gym: Gym, value: Any) -> Any:
    if value > gym_today(gym):
        raise ValidationError({"business_date": ["Business date cannot be in the future."]})
    return value


def _price_lines(*, gym: Gym, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_ids = [row["product_id"] for row in lines]
    products = {
        row.id: row
        for row in Product.objects.filter(gym_id=gym.id, id__in=product_ids).order_by("id")
    }
    if set(products) != set(product_ids):
        raise ValidationError({"lines": ["Select a product of this gym."]})
    priced: list[dict[str, Any]] = []
    for row in sorted(lines, key=lambda item: str(item["product_id"])):
        product = products[row["product_id"]]
        if not product.is_active:
            raise ValidationError({"lines": ["This product is not active."]})
        quantity = parse_quantity(row["quantity"])
        if quantity <= 0:
            raise ValidationError({"lines": ["Enter a positive quantity."]})
        amounts = _line_amounts(
            quantity=quantity,
            unit_price=Decimal(product.sale_price),
            unit_cost=Decimal(product.current_unit_cost),
            discount=Decimal(row["discount"]),
        )
        priced.append(
            {
                "product": product,
                "quantity": quantity,
                "discount": amounts["discount"],
                "sku_snapshot": product.sku,
                "name_snapshot": product.name,
                "unit_price_snapshot": amounts["unit_price"],
                "unit_cost_snapshot": amounts["unit_cost"],
                "line_revenue": amounts["line_revenue"],
                "cost_of_goods_sold": amounts["cost_of_goods_sold"],
                "gross_profit": amounts["gross_profit"],
                "pre_discount": amounts["pre_discount"],
            }
        )
    return priced


def _reprice_stored(*, gym: Gym, sale: RetailSale) -> list[dict[str, Any]]:
    stored = [
        {
            "product_id": line.product_id,
            "quantity": line.quantity,
            "discount": line.discount,
        }
        for line in sale.lines.order_by("product_id", "id")
    ]
    if not stored:
        raise ValidationError({"lines": ["Enter at least one product line."]})
    return _price_lines(gym=gym, lines=stored)


def _line_amounts(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    unit_cost: Decimal,
    discount: Decimal,
) -> dict[str, Decimal]:
    pre_discount = extend_money(quantity, unit_price)
    if discount < 0 or discount > pre_discount:
        raise ValidationError({"discount": ["Discount must be from zero through the line amount."]})
    revenue = pre_discount - discount
    cogs = extend_money(quantity, unit_cost)
    return {
        "unit_price": unit_price,
        "unit_cost": unit_cost,
        "discount": discount,
        "pre_discount": pre_discount,
        "line_revenue": revenue,
        "cost_of_goods_sold": cogs,
        "gross_profit": revenue - cogs,
    }


def _totals(priced: list[dict[str, Any]]) -> dict[str, Decimal]:
    subtotal = sum((row["pre_discount"] for row in priced), _ZERO)
    discount_total = sum((row["discount"] for row in priced), _ZERO)
    total_amount = sum((row["line_revenue"] for row in priced), _ZERO)
    cogs_total = sum((row["cost_of_goods_sold"] for row in priced), _ZERO)
    if total_amount <= 0:
        raise ValidationError({"lines": ["The sale total must be greater than zero."]})
    return {
        "subtotal": subtotal,
        "discount_total": discount_total,
        "total_amount": total_amount,
        "cogs_total": cogs_total,
        "gross_profit_total": total_amount - cogs_total,
    }


def _insert_lines(*, sale: RetailSale, actor: GymMembership, priced: list[dict[str, Any]]) -> None:
    try:
        RetailSaleLine.objects.bulk_create(
            [
                RetailSaleLine(
                    gym_id=sale.gym_id,
                    sale=sale,
                    product=row["product"],
                    quantity=row["quantity"],
                    discount=row["discount"],
                    sku_snapshot=row["sku_snapshot"],
                    name_snapshot=row["name_snapshot"],
                    unit_price_snapshot=row["unit_price_snapshot"],
                    unit_cost_snapshot=row["unit_cost_snapshot"],
                    line_revenue=row["line_revenue"],
                    cost_of_goods_sold=row["cost_of_goods_sold"],
                    gross_profit=row["gross_profit"],
                    created_by_membership_id=actor.id,
                    updated_by_membership_id=actor.id,
                )
                for row in priced
            ]
        )
    except IntegrityError as exc:
        raise ValidationError({"lines": ["Each product can appear once in the sale."]}) from exc


def _assert_current_prices(
    *,
    gym: Gym,
    lines: list[RetailSaleLine],
    products: dict[UUID, Product] | None = None,
) -> None:
    if not lines:
        raise ValidationError({"lines": ["Enter at least one product line."]})
    if products is None:
        products = {
            row.id: row
            for row in Product.objects.select_for_update()
            .filter(gym_id=gym.id, id__in=[line.product_id for line in lines])
            .order_by("id")
        }
    for line in lines:
        product = products.get(line.product_id)
        if product is None or product.gym_id != gym.id:
            raise ValidationError({"lines": ["Select a product of this gym."]})
        if not product.is_active:
            raise ValidationError({"lines": ["This product is not active."]})
        if Decimal(product.sale_price) != Decimal(line.unit_price_snapshot) or Decimal(
            product.current_unit_cost
        ) != Decimal(line.unit_cost_snapshot):
            raise ValidationError(
                {"lines": ["The product price or cost changed since submission."]}
            )


def _holds_reception(*, gym_id: UUID, membership_id: UUID) -> bool:
    return holds_protected_role(
        gym_id=gym_id,
        membership_id=membership_id,
        role_key="RECEPTIONIST",
    )


def _assert_sale_till(*, sale: RetailSale, session: CashSession) -> None:
    scoped = (
        (sale.created_with_reception_scope, sale.created_by_membership_id),
        (sale.submitted_with_reception_scope, sale.submitted_by_membership_id),
    )
    for required, membership_id in scoped:
        if not required or membership_id is None:
            continue
        if session.opened_by_membership_id != membership_id:
            raise ValidationError(
                {
                    "cash_session_id": [
                        "The cash session must be opened by the responsible receptionist."
                    ]
                }
            )


def _return_guard_conflict(exc: DatabaseError) -> bool:
    message = str(exc)
    return any(
        token in message
        for token in (
            "retail_return:",
            "retail_guard:",
            "retail_posting:",
            "stock_guard:",
            "payment_guard:",
            "payment_refund:",
            "payment_posting:",
        )
    )


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    sale: RetailSale,
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    extra: dict[str, object] | None = None,
) -> None:
    values: dict[str, object] = {
        "status": sale.status,
        "payment_method": sale.payment_method,
        "total_amount": money_text(Decimal(sale.total_amount)),
        "currency": sale.currency,
        "business_date": sale.business_date.isoformat(),
    }
    if extra:
        values.update(extra)
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="sales",
        target_type="RetailSale",
        target_id=sale.id,
        request_id=request_id,
        new_values=values,
        ip_address=ip_address,
        user_agent=user_agent,
    )
