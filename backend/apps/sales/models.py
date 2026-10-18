"""Gym-owned retail sale drafts and their frozen product lines.

Clients send a member, a business date, a payment method, a reference, and
product lines. The server copies the gym currency and the current product
SKU, name, sale price, and unit cost, then stores the exact line and sale
totals. Only a ``DRAFT`` can change. Submission freezes those inputs.
Approval posts stock, payment, receipt, and cash effects, or rolls them all
back. A later full void restores stock and reverses the product charge
without rewriting those posted facts. A partial return leaves the sale
``POSTED`` and appends immutable return evidence instead.
"""

from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel
from apps.members.models import Payment


class RetailSale(TenantModel):
    """One gym sale. Walk-in sales leave ``member`` null.

    ``created_with_reception_scope`` and ``submitted_with_reception_scope``
    snapshot the protected Reception assignment at create and submit. Cash
    approval must use that membership's own open till. A posted sale can be
    voided once. The correction names its own business date and, for cash,
    an open destination session.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        POSTED = "POSTED"
        REJECTED = "REJECTED"
        CANCELLED = "CANCELLED"
        VOID = "VOID"

    member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="retail_sales",
    )
    business_date = models.DateField()
    currency = models.CharField(max_length=3)
    payment_method = models.CharField(max_length=20, choices=Payment.Method)
    reference_number = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    discount_total = models.DecimalField(max_digits=18, decimal_places=2)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    cogs_total = models.DecimalField(max_digits=18, decimal_places=2)
    gross_profit_total = models.DecimalField(max_digits=18, decimal_places=2)
    payment = models.ForeignKey(
        "members.Payment",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="posted_retail_sale",
    )
    receipt = models.ForeignKey(
        "members.Receipt",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="posted_retail_sale",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_membership_id = models.UUIDField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by_membership_id = models.UUIDField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by_membership_id = models.UUIDField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by_membership_id = models.UUIDField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_membership_id = models.UUIDField(null=True, blank=True)
    void_business_date = models.DateField(null=True, blank=True)
    void_reason = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")
    created_with_reception_scope = models.BooleanField(default=False)
    submitted_with_reception_scope = models.BooleanField(default=False)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "sales_retail_sale"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="rsale_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "payment"], name="rsale_one_payment_uniq"),
            models.UniqueConstraint(fields=["gym", "receipt"], name="rsale_one_receipt_uniq"),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="rsale_currency_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    payment_method__in=[
                        "CASH",
                        "BANK_TRANSFER",
                        "CARD",
                        "MOBILE_PAYMENT",
                        "OTHER",
                    ]
                ),
                name="rsale_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "DRAFT",
                        "PENDING_APPROVAL",
                        "POSTED",
                        "REJECTED",
                        "CANCELLED",
                        "VOID",
                    ]
                ),
                name="rsale_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(subtotal__gte=0)
                    & models.Q(discount_total__gte=0)
                    & models.Q(cogs_total__gte=0)
                    & models.Q(total_amount__gt=0)
                    & models.Q(total_amount=models.F("subtotal") - models.F("discount_total"))
                    & models.Q(gross_profit_total=models.F("total_amount") - models.F("cogs_total"))
                ),
                name="rsale_totals_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "business_date"],
                name="rsale_gym_status_date_idx",
            ),
            models.Index(fields=["gym", "-created_at", "-id"], name="rsale_gym_created_idx"),
            models.Index(fields=["gym", "member"], name="rsale_gym_member_idx"),
        ]


class RetailSaleLine(TenantModel):
    """One product on a sale. Product identity and money are snapshotted.

    Line revenue, cost of goods sold, and gross profit are exact server
    formulas. A sale can contain each product once. Lines change only while
    the parent sale is an unposted draft.
    """

    sale = models.ForeignKey(
        RetailSale,
        on_delete=models.RESTRICT,
        related_name="lines",
    )
    product = models.ForeignKey(
        "inventory.Product",
        on_delete=models.RESTRICT,
        related_name="retail_lines",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    discount = models.DecimalField(max_digits=18, decimal_places=2)
    sku_snapshot = models.CharField(max_length=32)
    name_snapshot = models.CharField(max_length=255)
    unit_price_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    unit_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    line_revenue = models.DecimalField(max_digits=18, decimal_places=2)
    cost_of_goods_sold = models.DecimalField(max_digits=18, decimal_places=2)
    gross_profit = models.DecimalField(max_digits=18, decimal_places=2)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "sales_retail_sale_line"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="rsline_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "sale", "product"],
                name="rsline_sale_product_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="rsline_quantity_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(discount__gte=0)
                & models.Q(unit_price_snapshot__gte=0)
                & models.Q(unit_cost_snapshot__gte=0),
                name="rsline_money_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(sku_snapshot="") & ~models.Q(name_snapshot=""),
                name="rsline_snapshot_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "sale", "product"], name="rsline_gym_sale_product_idx"),
            models.Index(fields=["gym", "product"], name="rsline_gym_product_idx"),
        ]


class RetailReturn(TenantModel):
    """Immutable partial return of a posted sale.

    The sale stays ``POSTED``. The server owns every amount. A positive
    refund links one ``PRODUCT`` ``REFUND`` payment and one issued correction
    receipt. A zero refund restores stock and has neither. Cash names the
    open destination session only when money is refunded. Rows are not
    updated or deleted.
    """

    sale = models.ForeignKey(
        RetailSale,
        on_delete=models.RESTRICT,
        related_name="returns",
    )
    business_date = models.DateField()
    reason = models.TextField()
    currency = models.CharField(max_length=3)
    payment_method = models.CharField(max_length=20, choices=Payment.Method)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2)
    discount_total = models.DecimalField(max_digits=18, decimal_places=2)
    cogs_total = models.DecimalField(max_digits=18, decimal_places=2)
    gross_profit_total = models.DecimalField(max_digits=18, decimal_places=2)
    payment = models.ForeignKey(
        "members.Payment",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="retail_returns",
    )
    receipt = models.ForeignKey(
        "members.Receipt",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="retail_returns",
    )
    cash_session = models.ForeignKey(
        "cashbook.CashSession",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="retail_returns",
    )
    returned_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "sales_retail_return"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="rret_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "payment"],
                condition=models.Q(payment__isnull=False),
                name="rret_one_payment_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "receipt"],
                condition=models.Q(receipt__isnull=False),
                name="rret_one_receipt_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="rret_currency_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    payment_method__in=[
                        "CASH",
                        "BANK_TRANSFER",
                        "CARD",
                        "MOBILE_PAYMENT",
                        "OTHER",
                    ]
                ),
                name="rret_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(reason__regex=r"[^[:space:]]"),
                name="rret_reason_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(refund_amount__gte=0)
                    & models.Q(discount_total__gte=0)
                    & models.Q(cogs_total__gte=0)
                    & models.Q(
                        gross_profit_total=models.F("refund_amount") - models.F("cogs_total")
                    )
                ),
                name="rret_money_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        refund_amount__gt=0,
                        payment__isnull=False,
                        receipt__isnull=False,
                    )
                    | models.Q(
                        refund_amount=0,
                        payment__isnull=True,
                        receipt__isnull=True,
                    )
                ),
                name="rret_settlement_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payment_method="CASH",
                        refund_amount__gt=0,
                        cash_session__isnull=False,
                    )
                    | models.Q(
                        payment_method="CASH",
                        refund_amount=0,
                        cash_session__isnull=True,
                    )
                    | (~models.Q(payment_method="CASH") & models.Q(cash_session__isnull=True))
                ),
                name="rret_cash_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "sale", "created_at", "id"], name="rret_gym_sale_idx"),
            models.Index(fields=["gym", "business_date", "id"], name="rret_gym_date_idx"),
        ]


class RetailReturnLine(TenantModel):
    """One returned sale line. Quantity and money are server-owned.

    Discount, refund revenue, and cost are cumulative ``ROUND_HALF_UP``
    slices of the immutable sale line. The final returned quantity receives
    the exact remainder. Gross profit is refund revenue minus cost. The row
    points at the positive ``SALE_RETURN`` movement for this quantity.
    """

    retail_return = models.ForeignKey(
        RetailReturn,
        on_delete=models.RESTRICT,
        related_name="lines",
    )
    sale_line = models.ForeignKey(
        RetailSaleLine,
        on_delete=models.RESTRICT,
        related_name="return_lines",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    discount = models.DecimalField(max_digits=18, decimal_places=2)
    refund_revenue = models.DecimalField(max_digits=18, decimal_places=2)
    cost_of_goods_sold = models.DecimalField(max_digits=18, decimal_places=2)
    gross_profit = models.DecimalField(max_digits=18, decimal_places=2)
    stock_movement = models.ForeignKey(
        "inventory.StockMovement",
        on_delete=models.RESTRICT,
        related_name="retail_return_lines",
    )
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "sales_retail_return_line"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="rretline_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "retail_return", "sale_line"],
                name="rretline_return_line_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "stock_movement"],
                name="rretline_one_movement_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="rretline_quantity_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(discount__gte=0)
                    & models.Q(refund_revenue__gte=0)
                    & models.Q(cost_of_goods_sold__gte=0)
                    & models.Q(
                        gross_profit=models.F("refund_revenue") - models.F("cost_of_goods_sold")
                    )
                ),
                name="rretline_money_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "sale_line"], name="rretline_gym_sale_line_idx"),
            models.Index(fields=["gym", "retail_return"], name="rretline_gym_return_idx"),
        ]
