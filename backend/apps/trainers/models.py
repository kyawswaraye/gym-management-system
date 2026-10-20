from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel


class TrainerAssignment(TenantModel):
    """One gym's assignment of a trainer to a member over an effective date range.

    Rows are ended or cancelled. The database rejects hard deletion and overlapping
    active ranges for the same gym, trainer, and member.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ENDED = "ENDED"
        CANCELLED = "CANCELLED"

    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="trainer_assignments",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        related_name="trainer_assignments",
    )
    effective_from = models.DateField()
    effective_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    ended_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_trainer_assignment"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="trainer_asg_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "ENDED", "CANCELLED"]),
                name="trainer_asg_status_value_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__isnull=True)
                | models.Q(effective_until__gte=models.F("effective_from")),
                name="trainer_asg_dates_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="ACTIVE", ended_at__isnull=True)
                    | models.Q(
                        status="ENDED",
                        ended_at__isnull=False,
                        effective_until__isnull=False,
                    )
                    | models.Q(status="CANCELLED", ended_at__isnull=False)
                ),
                name="trainer_asg_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status="ACTIVE") | ~models.Q(reason=""),
                name="trainer_asg_reason_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "member", "status", "effective_from", "effective_until"],
                name="trainer_asg_member_badge_idx",
            ),
            models.Index(
                fields=[
                    "gym",
                    "trainer_staff_profile",
                    "status",
                    "effective_from",
                    "effective_until",
                ],
                name="trainer_asg_trainer_range_idx",
            ),
            models.Index(
                fields=["gym", "-created_at", "-id"],
                name="trainer_asg_gym_created_idx",
            ),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="trainer_asg_actor_idx",
            ),
        ]


class ServicePackage(TenantModel):
    """One gym's sellable PT or class package definition.

    PT rows keep ``class_type`` null. CLASS rows require an active same-gym
    ``ClassType``. Definitions stay immutable after insert.
    """

    class PackageType(models.TextChoices):
        PT = "PT"
        CLASS = "CLASS"

    class ValidityUnit(models.TextChoices):
        DAY = "DAY"
        WEEK = "WEEK"
        MONTH = "MONTH"
        YEAR = "YEAR"

    type = models.CharField(max_length=8, choices=PackageType.choices)
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)
    session_count = models.PositiveIntegerField()
    validity_value = models.PositiveIntegerField()
    validity_unit = models.CharField(max_length=8, choices=ValidityUnit.choices)
    price = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    class_type = models.ForeignKey(
        "classes.ClassType",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="service_packages",
    )
    is_active = models.BooleanField(default=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_service_package"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="svc_pkg_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "code"], name="svc_pkg_gym_code_uniq"),
            models.UniqueConstraint(
                fields=["gym", "normalized_name"],
                name="svc_pkg_gym_name_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=["PT", "CLASS"]),
                name="svc_pkg_type_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(type="PT", class_type__isnull=True)
                    | models.Q(type="CLASS", class_type__isnull=False)
                ),
                name="svc_pkg_class_link_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(session_count__gte=1),
                name="svc_pkg_sessions_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(validity_value__gte=1),
                name="svc_pkg_validity_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(validity_unit__in=["DAY", "WEEK", "MONTH", "YEAR"]),
                name="svc_pkg_unit_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="svc_pkg_price_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(is_active=True),
                name="svc_pkg_active_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "code", "id"], name="svc_pkg_gym_code_id_idx"),
            models.Index(
                fields=["gym", "type", "is_active", "code", "id"],
                name="svc_pkg_list_idx",
            ),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="svc_pkg_actor_idx",
            ),
        ]


class PTSale(TenantModel):
    """One gym's personal-training sale.

    Increment 3 stores drafts and draft cancellation only. Payment, posting,
    approval, rejection, and reversal columns stay null until those commands
    are published.
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
        related_name="pt_sales",
    )
    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="pt_sales",
    )
    service_date = models.DateField()
    description = models.CharField(max_length=500)
    gross_fee = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    commission_percent_snapshot = models.DecimalField(max_digits=5, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    payment = models.ForeignKey(
        "members.Payment",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="authoritative_pt_sales",
    )
    service_package = models.ForeignKey(
        "trainers.ServicePackage",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_sales",
    )
    package_price_snapshot = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_membership_id = models.UUIDField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by_membership_id = models.UUIDField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by_membership_id = models.UUIDField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True, default="")
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_membership_id = models.UUIDField(null=True, blank=True)
    reversal_pt_sale_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_pt_sale"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="pt_sale_gym_id_uniq"),
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
                name="pt_sale_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(gross_fee__gt=0),
                name="pt_sale_fee_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(commission_percent_snapshot__gte=0)
                & models.Q(commission_percent_snapshot__lte=100),
                name="pt_sale_percent_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        service_package__isnull=True,
                        package_price_snapshot__isnull=True,
                    )
                    | models.Q(
                        service_package__isnull=False,
                        package_price_snapshot__isnull=False,
                    )
                ),
                name="pt_sale_package_pair_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="DRAFT",
                        submitted_at__isnull=True,
                        submitted_by_membership_id__isnull=True,
                        approved_at__isnull=True,
                        approved_by_membership_id__isnull=True,
                        rejected_at__isnull=True,
                        rejected_by_membership_id__isnull=True,
                        rejection_reason="",
                        posted_at__isnull=True,
                        cancelled_at__isnull=True,
                        cancelled_by_membership_id__isnull=True,
                        voided_at__isnull=True,
                        voided_by_membership_id__isnull=True,
                        reversal_pt_sale_id__isnull=True,
                        reason="",
                    )
                    | models.Q(
                        status="PENDING_APPROVAL",
                        submitted_at__isnull=False,
                        submitted_by_membership_id__isnull=False,
                        approved_at__isnull=True,
                        approved_by_membership_id__isnull=True,
                        rejected_at__isnull=True,
                        rejected_by_membership_id__isnull=True,
                        rejection_reason="",
                        posted_at__isnull=True,
                        cancelled_at__isnull=True,
                        cancelled_by_membership_id__isnull=True,
                        voided_at__isnull=True,
                        voided_by_membership_id__isnull=True,
                        reversal_pt_sale_id__isnull=True,
                        reason="",
                    )
                    | models.Q(
                        status="POSTED",
                        submitted_at__isnull=False,
                        submitted_by_membership_id__isnull=False,
                        approved_at__isnull=False,
                        approved_by_membership_id__isnull=False,
                        rejected_at__isnull=True,
                        rejected_by_membership_id__isnull=True,
                        rejection_reason="",
                        posted_at__isnull=False,
                        cancelled_at__isnull=True,
                        cancelled_by_membership_id__isnull=True,
                        voided_at__isnull=True,
                        voided_by_membership_id__isnull=True,
                        reversal_pt_sale_id__isnull=True,
                        reason="",
                    )
                    | (
                        models.Q(
                            status="REJECTED",
                            submitted_at__isnull=False,
                            submitted_by_membership_id__isnull=False,
                            approved_at__isnull=True,
                            approved_by_membership_id__isnull=True,
                            rejected_at__isnull=False,
                            rejected_by_membership_id__isnull=False,
                            posted_at__isnull=True,
                            cancelled_at__isnull=True,
                            cancelled_by_membership_id__isnull=True,
                            voided_at__isnull=True,
                            voided_by_membership_id__isnull=True,
                            reversal_pt_sale_id__isnull=True,
                            reason="",
                        )
                        & ~models.Q(rejection_reason="")
                    )
                    | (
                        models.Q(
                            status="VOID",
                            submitted_at__isnull=False,
                            submitted_by_membership_id__isnull=False,
                            approved_at__isnull=False,
                            approved_by_membership_id__isnull=False,
                            rejected_at__isnull=True,
                            rejected_by_membership_id__isnull=True,
                            rejection_reason="",
                            posted_at__isnull=False,
                            cancelled_at__isnull=True,
                            cancelled_by_membership_id__isnull=True,
                            voided_at__isnull=False,
                            voided_by_membership_id__isnull=False,
                            reversal_pt_sale_id__isnull=True,
                        )
                        & ~models.Q(reason="")
                    )
                    | (
                        models.Q(
                            status="CANCELLED",
                            payment__isnull=True,
                            submitted_at__isnull=True,
                            submitted_by_membership_id__isnull=True,
                            approved_at__isnull=True,
                            approved_by_membership_id__isnull=True,
                            rejected_at__isnull=True,
                            rejected_by_membership_id__isnull=True,
                            rejection_reason="",
                            posted_at__isnull=True,
                            cancelled_at__isnull=False,
                            cancelled_by_membership_id__isnull=False,
                            voided_at__isnull=True,
                            voided_by_membership_id__isnull=True,
                            reversal_pt_sale_id__isnull=True,
                        )
                        & ~models.Q(reason="")
                    )
                ),
                name="pt_sale_state_evidence_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "trainer_staff_profile", "service_date"],
                name="pt_sale_trainer_date_idx",
            ),
            models.Index(
                fields=["gym", "status", "service_date"],
                name="pt_sale_status_date_idx",
            ),
            models.Index(fields=["gym", "-created_at", "-id"], name="pt_sale_gym_created_idx"),
            models.Index(fields=["gym", "member"], name="pt_sale_member_idx"),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="pt_sale_actor_idx",
            ),
        ]


class PTCommission(TenantModel):
    """One commission for a posted personal-training sale.

    Approval inserts ``ACCRUED`` with a zero paid amount. Payout updates only
    the paid total, outstanding total, status, and ``paid_at``. Parent ``VOID``
    stays rejected until sale void is published.
    """

    class Status(models.TextChoices):
        ACCRUED = "ACCRUED"
        PARTIALLY_PAID = "PARTIALLY_PAID"
        PAID = "PAID"
        VOID = "VOID"

    pt_sale = models.ForeignKey(PTSale, on_delete=models.RESTRICT, related_name="commissions")
    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="pt_commissions",
    )
    gross_fee_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    commission_percent_snapshot = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=18, decimal_places=2)
    gym_share = models.DecimalField(max_digits=18, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2)
    outstanding_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACCRUED)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_pt_commission"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="pt_comm_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "pt_sale"], name="pt_comm_one_sale_uniq"),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACCRUED", "PARTIALLY_PAID", "PAID", "VOID"]),
                name="pt_comm_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(gross_fee_snapshot__gt=0)
                & models.Q(commission_percent_snapshot__gte=0)
                & models.Q(commission_percent_snapshot__lte=100)
                & models.Q(commission_amount__gte=0)
                & models.Q(gym_share__gte=0)
                & models.Q(paid_amount__gte=0)
                & models.Q(outstanding_amount__gte=0)
                & (
                    models.Q(status="ACCRUED", paid_amount=0, paid_at__isnull=True)
                    | models.Q(
                        status="PARTIALLY_PAID",
                        paid_amount__gt=0,
                        paid_amount__lt=models.F("commission_amount"),
                        paid_at__isnull=True,
                    )
                    | models.Q(
                        status="PAID",
                        paid_amount__gt=0,
                        paid_amount=models.F("commission_amount"),
                        paid_at__isnull=False,
                    )
                    | models.Q(
                        status="VOID",
                        paid_amount=0,
                        outstanding_amount=0,
                        paid_at__isnull=True,
                    )
                ),
                name="pt_comm_accrual_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "trainer_staff_profile", "status"],
                name="pt_comm_trainer_status_idx",
            ),
            models.Index(fields=["gym", "-created_at", "-id"], name="pt_comm_gym_created_idx"),
        ]


class PTCommissionPayment(TenantModel):
    """One immutable payout against a commission.

    Each command inserts one posted child. Sale void, through the internal
    reversal helper, is the only transition, and it moves that child to ``VOID``.
    """

    class Status(models.TextChoices):
        POSTED = "POSTED"
        VOID = "VOID"

    pt_commission = models.ForeignKey(
        PTCommission,
        on_delete=models.RESTRICT,
        related_name="payments",
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    business_date = models.DateField()
    payment_method = models.CharField(max_length=20)
    reference_number = models.CharField(max_length=100, blank=True, default="")
    policy_note = models.CharField(max_length=2000, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.POSTED)
    expense = models.ForeignKey(
        "expenses.Expense",
        on_delete=models.RESTRICT,
        related_name="pt_commission_payments",
    )
    cash_session = models.ForeignKey(
        "cashbook.CashSession",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_commission_payments",
    )
    cash_transaction = models.ForeignKey(
        "cashbook.CashTransaction",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_commission_payments",
    )
    reversal_expense = models.ForeignKey(
        "expenses.Expense",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_commission_reversals",
    )
    reversal_cash_transaction = models.ForeignKey(
        "cashbook.CashTransaction",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_commission_reversals",
    )
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_membership_id = models.UUIDField(null=True, blank=True)
    void_reason = models.CharField(max_length=2000, blank=True, default="")
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_pt_commission_payment"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="pt_comm_pay_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "expense"], name="pt_comm_pay_expense_uq"),
            models.UniqueConstraint(
                fields=["gym", "reversal_expense"],
                condition=models.Q(reversal_expense__isnull=False),
                name="pt_comm_pay_reversal_uq",
            ),
            models.UniqueConstraint(
                fields=["gym", "cash_transaction"],
                condition=models.Q(cash_transaction__isnull=False),
                name="pt_comm_pay_cash_uq",
            ),
            models.UniqueConstraint(
                fields=["gym", "reversal_cash_transaction"],
                condition=models.Q(reversal_cash_transaction__isnull=False),
                name="pt_comm_pay_rev_cash_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="pt_comm_pay_amount_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["POSTED", "VOID"]),
                name="pt_comm_pay_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payment_method="CASH",
                        cash_session__isnull=False,
                        cash_transaction__isnull=False,
                    )
                    & (
                        models.Q(status="POSTED", reversal_cash_transaction__isnull=True)
                        | models.Q(status="VOID", reversal_cash_transaction__isnull=False)
                    )
                )
                | (
                    ~models.Q(payment_method="CASH")
                    & models.Q(
                        cash_session__isnull=True,
                        cash_transaction__isnull=True,
                        reversal_cash_transaction__isnull=True,
                    )
                ),
                name="pt_comm_pay_cash_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "pt_commission", "created_at", "id"],
                name="pt_comm_pay_page_idx",
            ),
            models.Index(fields=["gym", "status"], name="pt_comm_pay_gym_status_idx"),
        ]


class MemberServicePackage(TenantModel):
    """One granted package for one member.

    Sale approval and the admin grant each insert one row and one ``GRANT``
    ledger entry. Status changes are controlled. Sale void is not published,
    so ``VOID`` stays unreachable.
    """

    class Source(models.TextChoices):
        PT_SALE = "PT_SALE"
        ADMIN_GRANT = "ADMIN_GRANT"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        EXHAUSTED = "EXHAUSTED"
        EXPIRED = "EXPIRED"
        VOID = "VOID"

    member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        related_name="service_packages",
    )
    service_package = models.ForeignKey(
        "trainers.ServicePackage",
        on_delete=models.RESTRICT,
        related_name="grants",
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    pt_sale = models.ForeignKey(
        "trainers.PTSale",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="granted_packages",
    )
    payment = models.ForeignKey(
        "members.Payment",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="granted_service_packages",
    )
    package_type = models.CharField(max_length=8)
    package_code = models.CharField(max_length=32)
    package_name = models.CharField(max_length=120)
    granted_sessions = models.PositiveIntegerField()
    amount_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    validity_value = models.PositiveIntegerField()
    validity_unit = models.CharField(max_length=8)
    valid_from = models.DateField()
    valid_until = models.DateField()
    complimentary = models.BooleanField()
    status = models.CharField(max_length=16, choices=Status.choices)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_member_service_package"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="mbr_svc_pkg_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "pt_sale"],
                condition=models.Q(pt_sale__isnull=False),
                name="mbr_svc_pkg_one_sale_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["PT_SALE", "ADMIN_GRANT"]),
                name="mbr_svc_pkg_source_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "EXHAUSTED", "EXPIRED", "VOID"]),
                name="mbr_svc_pkg_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(package_type__in=["PT", "CLASS"]),
                name="mbr_svc_pkg_type_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(granted_sessions__gte=1),
                name="mbr_svc_pkg_sessions_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(validity_value__gte=1),
                name="mbr_svc_pkg_validity_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(validity_unit__in=["DAY", "WEEK", "MONTH", "YEAR"]),
                name="mbr_svc_pkg_unit_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_snapshot__gte=0),
                name="mbr_svc_pkg_amount_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__gte=models.F("valid_from")),
                name="mbr_svc_pkg_dates_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source="PT_SALE", pt_sale__isnull=False, package_type="PT")
                    | models.Q(source="ADMIN_GRANT", pt_sale__isnull=True, payment__isnull=True)
                ),
                name="mbr_svc_pkg_origin_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(
                    source="ADMIN_GRANT",
                    package_type="PT",
                    complimentary=False,
                ),
                name="mbr_svc_pkg_pt_comp_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "-created_at", "-id"],
                name="mbr_svc_pkg_page_idx",
            ),
            models.Index(
                fields=["gym", "member", "status"],
                name="mbr_svc_pkg_member_idx",
            ),
            models.Index(
                fields=["gym", "package_type", "-created_at"],
                name="mbr_svc_pkg_type_idx",
            ),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="mbr_svc_pkg_actor_idx",
            ),
        ]


class PTBooking(TenantModel):
    """One trainer session for one member.

    Non-cancelled ranges cannot overlap for the same trainer or member.
    Terminal rows stay. Metered attendance and no-show each consume one session.
    """

    class Status(models.TextChoices):
        BOOKED = "BOOKED"
        ATTENDED = "ATTENDED"
        NO_SHOW = "NO_SHOW"
        CANCELLED = "CANCELLED"

    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="pt_bookings",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        related_name="pt_bookings",
    )
    member_service_package = models.ForeignKey(
        "trainers.MemberServicePackage",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="pt_bookings",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    unmetered = models.BooleanField(default=False)
    unmetered_reason = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.BOOKED)
    attended_at = models.DateTimeField(null=True, blank=True)
    attended_by_membership_id = models.UUIDField(null=True, blank=True)
    no_show_at = models.DateTimeField(null=True, blank=True)
    no_show_by_membership_id = models.UUIDField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_pt_booking"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="pt_book_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="pt_book_times_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["BOOKED", "ATTENDED", "NO_SHOW", "CANCELLED"]),
                name="pt_book_status_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "starts_at", "id"], name="pt_book_start_idx"),
            models.Index(
                fields=["gym", "trainer_staff_profile", "starts_at", "id"],
                name="pt_book_trainer_idx",
            ),
            models.Index(fields=["gym", "member", "starts_at", "id"], name="pt_book_member_idx"),
            models.Index(fields=["gym", "status", "starts_at"], name="pt_book_status_idx"),
            models.Index(fields=["gym", "created_by_membership_id"], name="pt_book_actor_idx"),
        ]


class ServiceSessionLedger(TenantModel):
    """One append-only session movement for a granted package."""

    class Reason(models.TextChoices):
        GRANT = "GRANT"
        ATTENDED = "ATTENDED"
        POLICY_NO_SHOW = "POLICY_NO_SHOW"
        CORRECTION = "CORRECTION"
        REFUND = "REFUND"
        EXPIRY = "EXPIRY"

    member_service_package = models.ForeignKey(
        "trainers.MemberServicePackage",
        on_delete=models.RESTRICT,
        related_name="ledger_entries",
    )
    session_delta = models.IntegerField()
    reason = models.CharField(max_length=16, choices=Reason.choices)
    pt_booking = models.ForeignKey(
        "trainers.PTBooking",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="ledger_entries",
        db_column="pt_booking_id",
    )
    class_booking = models.ForeignKey(
        "classes.ClassBooking",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="ledger_entries",
        db_column="class_booking_id",
    )
    reverses_entry = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="corrections",
    )
    occurred_at = models.DateTimeField()
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "trainers_service_session_ledger"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="sess_ledger_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "member_service_package"],
                condition=models.Q(reason="GRANT"),
                name="sess_ledger_one_grant_uq",
            ),
            models.UniqueConstraint(
                fields=["gym", "reverses_entry"],
                condition=models.Q(reverses_entry__isnull=False),
                name="sess_ledger_one_reversal_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    reason__in=[
                        "GRANT",
                        "ATTENDED",
                        "POLICY_NO_SHOW",
                        "CORRECTION",
                        "REFUND",
                        "EXPIRY",
                    ]
                ),
                name="sess_ledger_reason_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(session_delta=0),
                name="sess_ledger_delta_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        reason__in=["CORRECTION", "REFUND"],
                        reverses_entry__isnull=False,
                    )
                    | models.Q(
                        ~models.Q(reason__in=["CORRECTION", "REFUND"]),
                        reverses_entry__isnull=True,
                    )
                ),
                name="sess_ledger_reversal_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(reason__in=["ATTENDED", "POLICY_NO_SHOW"])
                    | models.Q(session_delta=-1)
                ),
                name="sess_ledger_consume_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "member_service_package", "-occurred_at", "-id"],
                name="sess_ledger_page_idx",
            ),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="sess_ledger_actor_idx",
            ),
        ]
