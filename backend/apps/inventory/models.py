"""Product catalog and the append-only stock ledger.

Quantity on hand is the sum of ``StockMovement.quantity``. It is not a column
and clients cannot write it. Money stays ``NUMERIC(18, 2)``. Quantities and
the low-stock threshold are ``NUMERIC(18, 3)`` and reject extra precision
instead of rounding it. ``current_unit_cost`` is product master data. A
movement copies that value into ``unit_cost_snapshot`` and does not
recalculate a weighted average.

``SALE`` is appended only by retail approval, with a negative quantity and
``retail_sale_line``. A full retail void appends one positive ``SALE_RETURN``
that reverses that sale movement. Manual adjustment cannot write either type.
"""

from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel

_CATEGORY_CODE = r"^[A-Z][A-Z0-9_]{0,31}$"
_SKU = r"^[A-Z0-9][A-Z0-9_-]{0,31}$"
_UNIT = r"^[A-Z][A-Z0-9_]{0,15}$"
_BARCODE = r"^[0-9A-Z]{1,64}$"


class ProductCategory(TenantModel):
    """Gym-local catalog group. Code and normalized name are unique per gym.

    Increment 6 publishes collection create only. Runtime may select and
    insert, not update or delete. Archive columns exist so a later command
    can use them without a new table.
    """

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by_membership_id = models.UUIDField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "inventory_product_category"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="prodcat_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "code"], name="prodcat_gym_code_uniq"),
            models.UniqueConstraint(
                fields=["gym", "normalized_name"],
                name="prodcat_gym_name_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(code__regex=_CATEGORY_CODE),
                name="prodcat_code_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(name="") & ~models.Q(normalized_name=""),
                name="prodcat_name_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        archived_at__isnull=True,
                        archived_by_membership_id__isnull=True,
                    )
                    | models.Q(
                        is_active=False,
                        archived_at__isnull=False,
                        archived_by_membership_id__isnull=False,
                    )
                ),
                name="prodcat_archive_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "code", "id"], name="prodcat_gym_code_id_idx"),
            models.Index(fields=["gym", "is_active"], name="prodcat_gym_active_idx"),
        ]


class Product(TenantModel):
    """Sellable catalog row. Stock is derived from movements, not this row."""

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.RESTRICT,
        related_name="products",
    )
    sku = models.CharField(max_length=32)
    barcode = models.CharField(max_length=64, null=True, blank=True)
    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=16)
    sale_price = models.DecimalField(max_digits=18, decimal_places=2)
    current_unit_cost = models.DecimalField(max_digits=18, decimal_places=2)
    default_unit_cost = models.DecimalField(max_digits=18, decimal_places=2)
    low_stock_threshold = models.DecimalField(max_digits=18, decimal_places=3)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by_membership_id = models.UUIDField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "inventory_product"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="product_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "sku"], name="product_gym_sku_uniq"),
            models.UniqueConstraint(
                fields=["gym", "barcode"],
                condition=models.Q(barcode__isnull=False),
                name="product_gym_barcode_uniq",
            ),
            models.CheckConstraint(condition=models.Q(sku__regex=_SKU), name="product_sku_ck"),
            models.CheckConstraint(
                condition=models.Q(barcode__isnull=True) | models.Q(barcode__regex=_BARCODE),
                name="product_barcode_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="product_name_nonblank_ck",
            ),
            models.CheckConstraint(condition=models.Q(unit__regex=_UNIT), name="product_unit_ck"),
            models.CheckConstraint(
                condition=models.Q(sale_price__gte=0),
                name="product_sale_price_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(current_unit_cost__gte=0),
                name="product_current_cost_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(default_unit_cost__gte=0),
                name="product_default_cost_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(low_stock_threshold__gte=0),
                name="product_threshold_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        is_active=True,
                        archived_at__isnull=True,
                        archived_by_membership_id__isnull=True,
                    )
                    | models.Q(
                        is_active=False,
                        archived_at__isnull=False,
                        archived_by_membership_id__isnull=False,
                    )
                ),
                name="product_archive_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "sku", "id"], name="product_gym_sku_id_idx"),
            models.Index(fields=["gym", "category", "sku"], name="product_gym_category_idx"),
        ]


class StockMovement(TenantModel):
    """Immutable stock delta. Deletes and updates are rejected.

    Manual rows use ``OPENING``, ``PURCHASE``, ``DAMAGE``, ``LOSS``,
    ``ADJUSTMENT``, and ``CORRECTION``. ``OPENING`` and ``PURCHASE`` are
    positive. ``DAMAGE`` and ``LOSS`` are negative. Adjustments and
    corrections are signed and nonzero. ``LOSS``, ``ADJUSTMENT``, and
    ``CORRECTION`` require a nonblank reason. ``SALE`` is negative and
    ``SALE_RETURN`` is an equal positive reversal of that sale. The resulting
    sum cannot be negative.
    """

    class Type(models.TextChoices):
        OPENING = "OPENING"
        PURCHASE = "PURCHASE"
        SALE = "SALE"
        SALE_RETURN = "SALE_RETURN"
        DAMAGE = "DAMAGE"
        LOSS = "LOSS"
        ADJUSTMENT = "ADJUSTMENT"
        CORRECTION = "CORRECTION"

    product = models.ForeignKey(
        Product,
        on_delete=models.RESTRICT,
        related_name="stock_movements",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    movement_type = models.CharField(max_length=16, choices=Type)
    unit_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.TextField(blank=True, default="")
    business_date = models.DateField()
    reverses_movement = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="reversed_by",
    )
    retail_sale_line = models.ForeignKey(
        "sales.RetailSaleLine",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "inventory_stock_movement"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="stock_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(
                    movement_type__in=[
                        "OPENING",
                        "PURCHASE",
                        "SALE",
                        "SALE_RETURN",
                        "DAMAGE",
                        "LOSS",
                        "ADJUSTMENT",
                        "CORRECTION",
                    ]
                ),
                name="stock_type_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(movement_type__in=["OPENING", "PURCHASE"], quantity__gt=0)
                    | models.Q(movement_type__in=["DAMAGE", "LOSS"], quantity__lt=0)
                    | (
                        models.Q(movement_type__in=["ADJUSTMENT", "CORRECTION"])
                        & ~models.Q(quantity=0)
                    )
                    | models.Q(movement_type="SALE", quantity__lt=0)
                    | models.Q(movement_type="SALE_RETURN", quantity__gt=0)
                ),
                name="stock_quantity_sign_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_cost_snapshot__gte=0),
                name="stock_unit_cost_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(movement_type__in=["LOSS", "ADJUSTMENT", "CORRECTION"])
                    & ~models.Q(reason="")
                )
                | models.Q(
                    movement_type__in=["OPENING", "PURCHASE", "DAMAGE", "SALE", "SALE_RETURN"]
                ),
                name="stock_reason_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        movement_type__in=[
                            "OPENING",
                            "PURCHASE",
                            "DAMAGE",
                            "LOSS",
                            "ADJUSTMENT",
                            "CORRECTION",
                        ],
                        reverses_movement__isnull=True,
                        retail_sale_line__isnull=True,
                    )
                    | models.Q(
                        movement_type="SALE",
                        quantity__lt=0,
                        reverses_movement__isnull=True,
                        retail_sale_line__isnull=False,
                    )
                    | models.Q(
                        movement_type="SALE_RETURN",
                        quantity__gt=0,
                        reverses_movement__isnull=False,
                        retail_sale_line__isnull=False,
                    )
                ),
                name="stock_evidence_ck",
            ),
            models.UniqueConstraint(
                fields=["gym", "retail_sale_line"],
                condition=models.Q(movement_type="SALE"),
                name="stock_one_sale_line_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "retail_sale_line"],
                condition=models.Q(movement_type="SALE_RETURN"),
                name="stock_one_return_line_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "reverses_movement"],
                condition=models.Q(reverses_movement__isnull=False),
                name="stock_one_reversal_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "product"], name="stock_gym_product_idx"),
            models.Index(fields=["gym", "-created_at", "-id"], name="stock_gym_created_idx"),
            models.Index(
                fields=["gym", "movement_type", "-created_at", "-id"],
                name="stock_gym_type_idx",
            ),
            models.Index(
                fields=["gym", "business_date", "-created_at", "-id"],
                name="stock_gym_date_idx",
            ),
            models.Index(
                fields=["gym", "retail_sale_line"],
                name="stock_gym_sale_line_idx",
            ),
        ]
