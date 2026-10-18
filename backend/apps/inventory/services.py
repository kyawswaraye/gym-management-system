"""Catalog writes and manual stock adjustments.

Category create, product create, and product update require ``Idempotency-Key``.
That matches the salary and expense command policy and is stricter than a
reads-only interpretation of the inventory section. Adjustment also requires
the key.

``mutate()`` reloads the actor, the active ``finance`` entitlement, and the
live permission. The feature catalog has no separate inventory code. The
public adjustment body has no unit cost: ``current_unit_cost`` stays product
master data, and the movement stores that locked value as
``unit_cost_snapshot``. There is no weighted-average update.

The product row is locked before the on-hand sum is checked and the movement
is inserted. A negative result is rejected. ``SALE`` and ``SALE_RETURN`` are
not manual types, and reversal links are not accepted.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.db import IntegrityError, connection
from django.db.models import Sum
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.services import write_tenant_audit
from apps.cashbook.services import revalidate_cash_actor
from apps.common.finance.money import parse_money
from apps.inventory.models import Product, ProductCategory, StockMovement
from apps.inventory.normalization import canonical_category_name, canonical_display_name
from apps.inventory.quantities import parse_quantity
from apps.inventory.selectors import get_product
from apps.sales.models import RetailSaleLine
from apps.tenants.models import Gym, GymMembership

_POSITIVE = frozenset({StockMovement.Type.OPENING, StockMovement.Type.PURCHASE})
_NEGATIVE = frozenset({StockMovement.Type.DAMAGE, StockMovement.Type.LOSS})
_MANUAL = (
    _POSITIVE
    | _NEGATIVE
    | frozenset({StockMovement.Type.ADJUSTMENT, StockMovement.Type.CORRECTION})
)


def category_route() -> str:
    return "/api/v1/product-categories/"


def product_route(*, product_id: UUID | None) -> str:
    if product_id is None:
        return "/api/v1/products/"
    return f"/api/v1/products/{product_id}/"


def adjust_route() -> str:
    return "/api/v1/stock-movements/adjust/"


def create_category(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> ProductCategory:
    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code="inventory.manage",
    )
    display, normalized = canonical_category_name(str(fields["name"]))
    active = bool(fields.get("is_active", True))
    archived_at = None if active else timezone.now()
    archived_by = None if active else actor.id
    try:
        category = ProductCategory.objects.create(
            gym=gym,
            code=str(fields["code"]),
            name=display,
            normalized_name=normalized,
            is_active=active,
            archived_at=archived_at,
            archived_by_membership_id=archived_by,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        raise _category_conflict(exc) from exc
    _audit(
        gym=gym,
        actor=actor,
        action="PRODUCT_CATEGORY_CREATED",
        target_type="ProductCategory",
        target_id=category.id,
        new_values={
            "code": category.code,
            "name": category.name,
            "is_active": category.is_active,
        },
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return category


def create_product(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Product:
    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code="inventory.manage",
    )
    category = _category(gym_id=gym.id, category_id=fields["category_id"])
    active = bool(fields.get("is_active", True))
    try:
        product = Product.objects.create(
            gym=gym,
            category=category,
            sku=str(fields["sku"]),
            barcode=fields.get("barcode"),
            name=canonical_display_name(str(fields["name"])),
            unit=str(fields["unit"]),
            sale_price=parse_money(fields["sale_price"]),
            current_unit_cost=parse_money(fields["current_unit_cost"]),
            default_unit_cost=parse_money(fields["default_unit_cost"]),
            low_stock_threshold=parse_quantity(fields["low_stock_threshold"]),
            is_active=active,
            archived_at=None if active else timezone.now(),
            archived_by_membership_id=None if active else actor.id,
            created_by_membership_id=actor.id,
            updated_by_membership_id=actor.id,
        )
    except IntegrityError as exc:
        raise _product_conflict(exc) from exc
    _audit(
        gym=gym,
        actor=actor,
        action="PRODUCT_CREATED",
        target_type="Product",
        target_id=product.id,
        new_values=_product_audit(product),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return get_product(gym_id=gym.id, product_id=product.id)


def update_product(
    *,
    gym: Gym,
    actor: GymMembership,
    product_id: UUID,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> Product:
    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code="inventory.manage",
    )
    product = Product.objects.select_for_update().filter(gym_id=gym.id, id=product_id).first()
    if product is None:
        raise Http404
    before = _product_audit(product)
    if "category_id" in fields:
        product.category = _category(gym_id=gym.id, category_id=fields["category_id"])
    if "sku" in fields:
        product.sku = str(fields["sku"])
    if "barcode" in fields:
        product.barcode = fields["barcode"]
    if "name" in fields:
        product.name = canonical_display_name(str(fields["name"]))
    if "unit" in fields:
        product.unit = str(fields["unit"])
    if "sale_price" in fields:
        product.sale_price = parse_money(fields["sale_price"])
    if "current_unit_cost" in fields:
        product.current_unit_cost = parse_money(fields["current_unit_cost"])
    if "default_unit_cost" in fields:
        product.default_unit_cost = parse_money(fields["default_unit_cost"])
    if "low_stock_threshold" in fields:
        product.low_stock_threshold = parse_quantity(fields["low_stock_threshold"])
    if "is_active" in fields:
        _apply_active(product, active=bool(fields["is_active"]), actor_id=actor.id)
    product.updated_by_membership_id = actor.id
    try:
        product.save()
    except IntegrityError as exc:
        raise _product_conflict(exc) from exc
    _audit(
        gym=gym,
        actor=actor,
        action="PRODUCT_UPDATED",
        target_type="Product",
        target_id=product.id,
        old_values=before,
        new_values=_product_audit(product),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return get_product(gym_id=gym.id, product_id=product.id)


def adjust_stock(
    *,
    gym: Gym,
    actor: GymMembership,
    fields: dict[str, Any],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
) -> tuple[StockMovement, Product]:
    gym, actor = revalidate_cash_actor(
        gym_id=gym.id,
        membership_id=actor.id,
        permission_code="inventory.adjust",
    )
    movement_type = str(fields["movement_type"])
    if movement_type not in _MANUAL:
        raise ValidationError({"movement_type": ["This movement type cannot be posted manually."]})
    quantity = parse_quantity(fields["quantity"], nonzero=True)
    _require_sign(movement_type=movement_type, quantity=quantity)
    reason = str(fields["reason"]).strip()
    if reason == "":
        raise ValidationError({"reason": ["Enter an explanation."]})
    product = (
        Product.objects.select_for_update().filter(gym_id=gym.id, id=fields["product_id"]).first()
    )
    if product is None:
        raise Http404
    on_hand = _on_hand(gym_id=gym.id, product_id=product.id)
    if on_hand + quantity < 0:
        raise ValidationError({"quantity": ["Resulting stock cannot be negative."]})
    snapshot = parse_money(product.current_unit_cost)
    movement = StockMovement.objects.create(
        gym=gym,
        product=product,
        quantity=quantity,
        movement_type=movement_type,
        unit_cost_snapshot=snapshot,
        reason=reason,
        business_date=fields["business_date"],
        reverses_movement=None,
        created_by_membership_id=actor.id,
    )
    _audit(
        gym=gym,
        actor=actor,
        action="STOCK_ADJUSTED",
        target_type="StockMovement",
        target_id=movement.id,
        new_values={
            "product_id": str(product.id),
            "movement_type": movement.movement_type,
            "quantity": format(quantity, "f"),
            "unit_cost_snapshot": format(snapshot, "f"),
            "business_date": movement.business_date.isoformat(),
            "current_unit_cost": format(snapshot, "f"),
        },
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return movement, get_product(gym_id=gym.id, product_id=product.id)


def post_sale_movement(
    *,
    gym: Gym,
    actor: GymMembership,
    product: Product,
    line: RetailSaleLine,
    business_date: Any,
) -> StockMovement:
    """Append one negative ``SALE`` movement for a locked retail line.

    The public adjustment command still rejects ``SALE``, ``SALE_RETURN``, and
    reversal links. The caller must already be inside a transaction and should
    lock products in id order before calling this helper.
    """

    if not connection.in_atomic_block:
        raise RuntimeError("A sale movement requires an active transaction.")
    if line.gym_id != gym.id or product.gym_id != gym.id or line.product_id != product.id:
        raise ValidationError({"lines": ["Select a product of this gym."]})
    quantity = -parse_quantity(line.quantity)
    if quantity >= 0:
        raise ValidationError({"quantity": ["A sale movement must reduce stock."]})
    locked = Product.objects.select_for_update().filter(gym_id=gym.id, id=product.id).first()
    if locked is None:
        raise Http404
    on_hand = _on_hand(gym_id=gym.id, product_id=locked.id)
    if on_hand + quantity < 0:
        raise ValidationError({"quantity": ["Available stock is not enough for this sale."]})
    snapshot = parse_money(locked.current_unit_cost)
    if snapshot != parse_money(line.unit_cost_snapshot):
        raise ValidationError({"lines": ["The product price or cost changed since submission."]})
    return StockMovement.objects.create(
        gym=gym,
        product=locked,
        quantity=quantity,
        movement_type=StockMovement.Type.SALE,
        unit_cost_snapshot=snapshot,
        reason="",
        business_date=business_date,
        reverses_movement=None,
        retail_sale_line=line,
        created_by_membership_id=actor.id,
    )


def _category(*, gym_id: UUID, category_id: UUID) -> ProductCategory:
    category = ProductCategory.objects.filter(gym_id=gym_id, id=category_id).first()
    if category is None:
        raise Http404
    return category


def _on_hand(*, gym_id: UUID, product_id: UUID) -> Decimal:
    total = StockMovement.objects.filter(gym_id=gym_id, product_id=product_id).aggregate(
        total=Sum("quantity")
    )["total"]
    if total is None:
        return Decimal("0.000")
    return parse_quantity(total)


def _require_sign(*, movement_type: str, quantity: Decimal) -> None:
    if movement_type in _POSITIVE and quantity <= 0:
        raise ValidationError({"quantity": ["Enter a positive quantity."]})
    if movement_type in _NEGATIVE and quantity >= 0:
        raise ValidationError({"quantity": ["Enter a negative quantity."]})


def _apply_active(product: Product, *, active: bool, actor_id: UUID) -> None:
    product.is_active = active
    if active:
        product.archived_at = None
        product.archived_by_membership_id = None
        return
    if product.archived_at is None:
        product.archived_at = timezone.now()
    product.archived_by_membership_id = actor_id


def _product_audit(product: Product) -> dict[str, object]:
    return {
        "sku": product.sku,
        "barcode": product.barcode,
        "name": product.name,
        "category_id": str(product.category_id),
        "unit": product.unit,
        "sale_price": format(parse_money(product.sale_price), "f"),
        "current_unit_cost": format(parse_money(product.current_unit_cost), "f"),
        "default_unit_cost": format(parse_money(product.default_unit_cost), "f"),
        "low_stock_threshold": format(parse_quantity(product.low_stock_threshold), "f"),
        "is_active": product.is_active,
    }


def _constraint_name(exc: IntegrityError) -> str:
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    if diag is None:
        return ""
    return str(getattr(diag, "constraint_name", "") or "")


def _category_conflict(exc: IntegrityError) -> ValidationError:
    field = "name" if "name" in _constraint_name(exc) else "code"
    return ValidationError({field: ["A category with this code or name already exists."]})


def _product_conflict(exc: IntegrityError) -> ValidationError:
    field = "barcode" if "barcode" in _constraint_name(exc) else "sku"
    return ValidationError({field: ["A product with this SKU or barcode already exists."]})


def _audit(
    *,
    gym: Gym,
    actor: GymMembership,
    action: str,
    target_type: str,
    target_id: UUID,
    new_values: dict[str, object],
    request_id: str,
    ip_address: str | None,
    user_agent: str,
    old_values: dict[str, object] | None = None,
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=actor.user,
        actor_membership_id=actor.id,
        action=action,
        module="inventory",
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        old_values=old_values,
        new_values=new_values,
        ip_address=ip_address,
        user_agent=user_agent,
    )
