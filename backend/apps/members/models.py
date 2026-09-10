from __future__ import annotations

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models

from apps.common.db.models import TenantModel


class Member(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        EXPIRED = "EXPIRED"
        SUSPENDED = "SUSPENDED"
        CANCELLED = "CANCELLED"
        ARCHIVED = "ARCHIVED"

    member_number = models.CharField(max_length=32)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    gender = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    join_date = models.DateField()
    address = models.JSONField(default=dict, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_member"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="member_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "member_number"], name="member_gym_number_uniq"),
            models.CheckConstraint(
                condition=~models.Q(first_name=""), name="member_name_nonblank_ck"
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "member_number"], name="member_gym_status_no_idx"
            ),
            models.Index(fields=["gym", "join_date"], name="member_gym_joined_idx"),
        ]

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class MembershipPlan(TenantModel):
    class DurationUnit(models.TextChoices):
        DAY = "DAY"
        WEEK = "WEEK"
        MONTH = "MONTH"
        YEAR = "YEAR"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    duration_value = models.PositiveIntegerField()
    duration_unit = models.CharField(max_length=8, choices=DurationUnit)
    price = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_membership_plan"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="mplan_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "code"], name="mplan_gym_code_uniq"),
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="mplan_price_nonnegative_ck"
            ),
        ]


class Membership(TenantModel):
    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID"
        PARTIAL = "PARTIAL"
        PAID = "PAID"
        REFUNDED = "REFUNDED"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        ACTIVE = "ACTIVE"
        EXPIRED = "EXPIRED"
        SUSPENDED = "SUSPENDED"
        CANCELLED = "CANCELLED"

    class RevenueSource(models.TextChoices):
        NEW_MEMBER = "NEW_MEMBER"
        RENEWAL = "RENEWAL"

    member = models.ForeignKey(Member, on_delete=models.RESTRICT, related_name="memberships")
    plan = models.ForeignKey(MembershipPlan, on_delete=models.RESTRICT)
    plan_code = models.CharField(max_length=32)
    plan_name = models.CharField(max_length=100)
    duration_value = models.PositiveIntegerField()
    duration_unit = models.CharField(max_length=8, choices=MembershipPlan.DurationUnit)
    start_date = models.DateField()
    expiry_date = models.DateField()
    list_price = models.DecimalField(max_digits=18, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    payment_status = models.CharField(
        max_length=12, choices=PaymentStatus, default=PaymentStatus.UNPAID
    )
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    revenue_source = models.CharField(max_length=12, choices=RevenueSource)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_membership"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="memberplan_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(expiry_date__gte=models.F("start_date")),
                name="membership_dates_valid_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    final_amount=models.F("list_price") - models.F("discount_amount")
                ),
                name="membership_total_exact_ck",
            ),
            ExclusionConstraint(
                name="membership_no_active_overlap_excl",
                expressions=[
                    (models.F("gym"), RangeOperators.EQUAL),
                    (models.F("member"), RangeOperators.EQUAL),
                    (
                        models.Func(
                            models.F("start_date"),
                            models.F("expiry_date"),
                            models.Value("[]"),
                            function="DATERANGE",
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=models.Q(status__in=["PENDING", "ACTIVE"]),
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status", "expiry_date"], name="membership_gym_expiry_idx"),
            models.Index(fields=["gym", "member", "start_date"], name="membership_member_date_idx"),
        ]


class Payment(TenantModel):
    class EntryType(models.TextChoices):
        CHARGE = "CHARGE"
        REFUND = "REFUND"
        REVERSAL = "REVERSAL"

    class Purpose(models.TextChoices):
        MEMBERSHIP = "MEMBERSHIP"
        PT = "PT"
        PRODUCT = "PRODUCT"
        OTHER = "OTHER"

    class Method(models.TextChoices):
        CASH = "CASH"
        BANK_TRANSFER = "BANK_TRANSFER"
        CARD = "CARD"
        MOBILE_PAYMENT = "MOBILE_PAYMENT"
        OTHER = "OTHER"

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        POSTED = "POSTED"
        REJECTED = "REJECTED"
        CANCELLED = "CANCELLED"
        VOID = "VOID"

    member = models.ForeignKey(Member, on_delete=models.RESTRICT, related_name="payments")
    membership = models.ForeignKey(
        Membership, on_delete=models.RESTRICT, null=True, blank=True, related_name="payments"
    )
    original_payment = models.ForeignKey(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="corrections"
    )
    entry_type = models.CharField(max_length=10, choices=EntryType, default=EntryType.CHARGE)
    purpose = models.CharField(max_length=12, choices=Purpose)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20, choices=Method)
    business_date = models.DateField()
    received_at = models.DateTimeField()
    reference_number = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_membership_id = models.UUIDField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by_membership_id = models.UUIDField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by_membership_id = models.UUIDField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_payment"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="payment_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payment_amount_positive_ck"
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "business_date"], name="payment_gym_status_date_idx"
            ),
            models.Index(fields=["gym", "member"], name="payment_gym_member_idx"),
        ]


class Receipt(TenantModel):
    payment = models.OneToOneField(Payment, on_delete=models.RESTRICT, related_name="receipt")
    receipt_number = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    member_number = models.CharField(max_length=32)
    member_name = models.CharField(max_length=201)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20)
    business_date = models.DateField()
    issuer_name = models.CharField(max_length=201)
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_receipt"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="receipt_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "receipt_number"], name="receipt_gym_number_uniq"
            ),
        ]
