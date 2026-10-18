"""Retail sale drafts and the single approval posting path.

Lock order inside approval: the sale, its lines, the member when present,
then products ordered by id. Stock movements are appended only after those
locks. The product payment, receipt, and cash movement are created next, and
the sale is marked posted last. A failure rolls all of it back.

Price and cost are copied when the draft is saved. Submission and approval
compare those snapshots with the current active product and fail when they
differ. They do not rewrite the submitted totals.

``mutate()`` reloads the actor, the finance entitlement, and the live
permission before it locks or writes. Reception-origin cash uses the
originating Reception membership's own open till.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.models import CashSession, CashTransaction
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.cashbook.services import revalidate_cash_actor
from apps.inventory.models import Product
from apps.inventory.quantities import parse_quantity
from apps.inventory.services import post_sale_movement
from apps.members.models import Member, Payment, Receipt
from apps.members.payment_serializers import money_text
from apps.members.payment_services import post_retail_product_payment
from apps.members.services import gym_today
from apps.sales.exceptions import RetailPostingConflict
from apps.sales.models import RetailSale, RetailSaleLine
from apps.sales.pricing import extend_money
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
