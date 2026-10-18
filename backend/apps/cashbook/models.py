from __future__ import annotations

from decimal import Decimal

from django.db import models

from apps.common.db.models import TenantModel

_OPEN_EVIDENCE = models.Q(
    status="OPEN",
    closed_by_membership__isnull=True,
    closed_at__isnull=True,
    actual_closing__isnull=True,
    difference__isnull=True,
    difference_reason="",
)
_TERMINAL_EVIDENCE = models.Q(
    status__in=["CLOSED", "VOID"],
    closed_by_membership__isnull=False,
    closed_at__isnull=False,
    actual_closing__isnull=False,
    difference__isnull=False,
) & (models.Q(difference=0) | models.Q(difference_reason__regex=r"\S"))
_EXPECTED = models.F("opening_cash") + models.F("cash_sales") - models.F("cash_expenses")
_EXPECTED = _EXPECTED + models.F("manual_net")
_MANUAL_TXN = models.Q(
    source_type="MANUAL",
    source_id__isnull=True,
    reversal_of__isnull=True,
    reason__regex=r"\S",
)
_REVERSAL_TXN = models.Q(
    source_type="REVERSAL",
    source_id__isnull=True,
    reversal_of__isnull=False,
    reason__regex=r"\S",
)
_AUTOMATIC_TXN = models.Q(
    source_type__in=["PAYMENT", "EXPENSE", "COMMISSION"],
    reversal_of__isnull=True,
    source_id__isnull=False,
)
_LINKED_CORRECTION_TXN = models.Q(
    source_type__in=["PAYMENT", "EXPENSE"],
    reversal_of__isnull=False,
    source_id__isnull=False,
    reason__regex=r"\S",
)


class CashSession(TenantModel):
    """One open till for a gym register and business date.

    ``VOID`` is reserved. Increment 2 never creates it and the database
    trigger rejects every transition to it. Reconcile is the close command.
    """

    class Status(models.TextChoices):
        OPEN = "OPEN"
        CLOSED = "CLOSED"
        VOID = "VOID"

    business_date = models.DateField()
    register_code = models.CharField(max_length=32)
    currency = models.CharField(max_length=3)
    opening_cash = models.DecimalField(max_digits=18, decimal_places=2)
    cash_sales = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    cash_expenses = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    manual_net = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0.00"))
    expected_closing = models.DecimalField(max_digits=18, decimal_places=2)
    actual_closing = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    difference_reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=8, choices=Status, default=Status.OPEN)
    opened_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="opened_cash_sessions",
    )
    closed_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="closed_cash_sessions",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "cashbook_cash_session"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="cashsession_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "register_code"],
                condition=models.Q(status="OPEN"),
                name="cashsession_open_register_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(register_code__regex=r"^[A-Z0-9_-]{1,32}$"),
                name="cashsession_register_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="cashsession_currency_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["OPEN", "CLOSED", "VOID"]),
                name="cashsession_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(opening_cash__gte=0),
                name="cashsession_opening_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(actual_closing__isnull=True) | models.Q(actual_closing__gte=0),
                name="cashsession_actual_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_closing=_EXPECTED),
                name="cashsession_expected_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(difference__isnull=True)
                | models.Q(difference=models.F("actual_closing") - models.F("expected_closing")),
                name="cashsession_difference_ck",
            ),
            models.CheckConstraint(
                condition=_OPEN_EVIDENCE | _TERMINAL_EVIDENCE,
                name="cashsession_evidence_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status"], name="cashsession_gym_status_idx"),
            models.Index(fields=["gym", "business_date"], name="cashsession_gym_date_idx"),
            models.Index(fields=["gym", "register_code"], name="cashsession_gym_register_idx"),
            models.Index(fields=["gym", "created_at"], name="cashsession_gym_created_idx"),
        ]


class CashTransaction(TenantModel):
    """Append-only cash movement. Increment 2 publishes manual adjustments only."""

    class Direction(models.TextChoices):
        IN = "IN"
        OUT = "OUT"

    class SourceType(models.TextChoices):
        PAYMENT = "PAYMENT"
        EXPENSE = "EXPENSE"
        COMMISSION = "COMMISSION"
        MANUAL = "MANUAL"
        REVERSAL = "REVERSAL"

    cash_session = models.ForeignKey(
        CashSession,
        on_delete=models.RESTRICT,
        related_name="transactions",
    )
    direction = models.CharField(max_length=3, choices=Direction)
    source_type = models.CharField(max_length=12, choices=SourceType)
    source_id = models.UUIDField(null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    business_date = models.DateField()
    occurred_at = models.DateTimeField()
    description = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    reversal_of = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="reversals",
    )
    created_by_membership = models.ForeignKey(
        "tenants.GymMembership",
        on_delete=models.RESTRICT,
        related_name="cash_transactions",
    )

    class Meta:
        db_table = "cashbook_cash_transaction"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="cashtxn_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "source_type", "source_id"],
                condition=models.Q(source_id__isnull=False),
                name="cashtxn_source_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "reversal_of"],
                condition=models.Q(reversal_of__isnull=False),
                name="cashtxn_reversal_uniq",
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="cashtxn_amount_ck"),
            models.CheckConstraint(
                condition=models.Q(direction__in=["IN", "OUT"]),
                name="cashtxn_direction_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="cashtxn_currency_ck",
            ),
            models.CheckConstraint(
                condition=_MANUAL_TXN | _REVERSAL_TXN | _AUTOMATIC_TXN | _LINKED_CORRECTION_TXN,
                name="cashtxn_shape_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "cash_session", "occurred_at"],
                name="cashtxn_gym_session_time_idx",
            ),
            models.Index(fields=["gym", "business_date"], name="cashtxn_gym_date_idx"),
            models.Index(
                fields=["gym", "source_type", "source_id"],
                name="cashtxn_gym_source_idx",
            ),
            models.Index(fields=["gym", "created_at"], name="cashtxn_gym_created_idx"),
        ]
