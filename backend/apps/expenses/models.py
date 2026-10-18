from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel
from apps.members.models import Payment


class Expense(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        POSTED = "POSTED"
        REJECTED = "REJECTED"
        VOID = "VOID"
        CANCELLED = "CANCELLED"

    category = models.ForeignKey(
        "tenants.ExpenseCategory",
        on_delete=models.RESTRICT,
        related_name="expenses",
    )
    business_date = models.DateField()
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_method = models.CharField(max_length=20, choices=Payment.Method)
    receipt_file_asset = models.ForeignKey(
        "files.FileAsset",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="expense_receipts",
    )
    policy_note = models.CharField(max_length=2000, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    reversal_expense = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="reversed_by",
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
    voided_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    cash_session = models.ForeignKey(
        "cashbook.CashSession",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="expenses",
    )
    cash_transaction = models.OneToOneField(
        "cashbook.CashTransaction",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="source_expense",
    )
    created_with_reception_scope = models.BooleanField(default=False)
    submitted_with_reception_scope = models.BooleanField(default=False)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "expenses_expense"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="expense_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expense_amount_positive_ck"
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="expense_currency_ck",
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
                name="expense_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        "DRAFT",
                        "PENDING_APPROVAL",
                        "POSTED",
                        "REJECTED",
                        "VOID",
                        "CANCELLED",
                    ]
                ),
                name="expense_status_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "business_date"],
                name="expense_gym_status_date_idx",
            ),
            models.Index(
                fields=["gym", "category", "status"],
                name="expense_gym_category_idx",
            ),
            models.Index(
                fields=["gym", "payment_method", "business_date"],
                name="expense_gym_method_date_idx",
            ),
            models.Index(fields=["gym", "-created_at", "-id"], name="expense_gym_created_idx"),
        ]
