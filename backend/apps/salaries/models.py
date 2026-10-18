from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel
from apps.members.models import Payment


class SalaryPeriod(TenantModel):
    """One gym-local pay period. Business fields freeze at submission.

    ``created_with_reception_scope`` and ``submitted_with_reception_scope``
    snapshot the protected Reception assignment at create and submit. They stay
    immutable so a later role change cannot move a cash posting onto another
    till. A posted period can be voided once. The correction names its own
    business date and, for cash, an open destination session.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        POSTED = "POSTED"
        REJECTED = "REJECTED"
        CANCELLED = "CANCELLED"
        VOID = "VOID"

    period_start = models.DateField()
    period_end = models.DateField()
    business_date = models.DateField()
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
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
    reason = models.TextField(blank=True, default="")
    created_with_reception_scope = models.BooleanField(default=False)
    submitted_with_reception_scope = models.BooleanField(default=False)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "salaries_salary_period"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="salper_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="salper_period_order_ck",
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
                name="salper_status_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "period_start", "period_end"],
                name="salper_gym_status_period_idx",
            ),
            models.Index(
                fields=["gym", "business_date", "status"],
                name="salper_gym_date_status_idx",
            ),
            models.Index(fields=["gym", "-created_at", "-id"], name="salper_gym_created_idx"),
        ]


class StaffSalaryLine(TenantModel):
    """Manually entered pay for one staff profile in one period.

    Amounts are never copied from ``StaffProfile``. Lines are editable only
    while the period is ``DRAFT``. Approval links one posted salary expense
    and, for cash, the open session that funded it.
    """

    salary_period = models.ForeignKey(
        SalaryPeriod,
        on_delete=models.RESTRICT,
        related_name="lines",
    )
    staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="salary_lines",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_method = models.CharField(max_length=20, choices=Payment.Method)
    external_reference = models.CharField(max_length=128, blank=True, default="")
    cash_session = models.ForeignKey(
        "cashbook.CashSession",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="salary_lines",
    )
    expense = models.ForeignKey(
        "expenses.Expense",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="salary_lines",
    )
    description = models.CharField(max_length=255)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "salaries_staff_salary_line"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="salline_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "salary_period", "staff_profile"],
                name="salline_period_staff_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="salline_amount_positive_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="salline_currency_ck",
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
                name="salline_method_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "-created_at", "-id"], name="salline_gym_created_idx"),
            models.Index(
                fields=["gym", "cash_session"],
                name="salline_gym_session_idx",
            ),
            models.Index(fields=["gym", "expense"], name="salline_gym_expense_idx"),
        ]
