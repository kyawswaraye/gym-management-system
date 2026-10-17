from __future__ import annotations

from typing import Any

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models

from apps.common.db.models import TenantModel
from apps.members.normalization import apply_member_normalization


class Member(TenantModel):
    """Tenant member profile.

    ``linked_user`` and ``authz_version`` are read-only foundations for a later
    member session. This model does not authenticate members. Migration 0007
    adds the same-gym profile-photo foreign key, the address shape check, and
    ``member_linked_user_fk`` with ``ON DELETE RESTRICT``. Django cannot
    express the composite photo reference, so that foreign key is declared in
    SQL. ``linked_user`` uses ``db_constraint=False`` so Django does not add a
    second foreign key beside that SQL ``ON DELETE RESTRICT`` constraint.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        EXPIRED = "EXPIRED"
        SUSPENDED = "SUSPENDED"
        CANCELLED = "CANCELLED"
        ARCHIVED = "ARCHIVED"

    class Gender(models.TextChoices):
        FEMALE = "FEMALE"
        MALE = "MALE"
        OTHER = "OTHER"
        UNSPECIFIED = "UNSPECIFIED"

    member_number = models.CharField(max_length=32)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    normalized_full_name = models.CharField(max_length=201)
    phone = models.CharField(max_length=32, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    join_date = models.DateField()
    address = models.JSONField(default=dict, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)
    profile_photo_asset_id = models.UUIDField(null=True, blank=True)
    linked_user = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="linked_members",
        db_constraint=False,
    )
    authz_version = models.PositiveIntegerField(default=1, db_default=1)
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_member"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="member_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "member_number"], name="member_gym_number_uniq"),
            models.UniqueConstraint(
                fields=["gym", "linked_user"],
                condition=models.Q(linked_user__isnull=False),
                name="member_gym_linked_user_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(first_name=""), name="member_name_nonblank_ck"
            ),
            models.CheckConstraint(
                condition=~models.Q(first_name__regex=r"^[[:space:]]*$"),
                name="member_first_name_trimmed_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(member_number__regex=r"^[[:space:]]*$"),
                name="member_number_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(normalized_full_name__regex=r"^[[:space:]]*$"),
                name="member_normalized_name_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(authz_version__gt=0),
                name="member_authz_version_positive_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(date_of_birth__isnull=True)
                    | models.Q(date_of_birth__lte=models.F("join_date"))
                ),
                name="member_birth_not_after_join_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="ARCHIVED", archived_at__isnull=False)
                    | (~models.Q(status="ARCHIVED") & models.Q(archived_at__isnull=True))
                ),
                name="member_archive_state_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(phone__isnull=True) | models.Q(phone__regex=r"^\+?[0-9]{1,32}$")
                ),
                name="member_phone_normalized_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(email__isnull=True)
                    | models.Q(email__regex=r"^[^[:space:][:upper:]]+@[^[:space:][:upper:]]+$")
                ),
                name="member_email_normalized_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(gender__isnull=True)
                    | models.Q(gender__in=["FEMALE", "MALE", "OTHER", "UNSPECIFIED"])
                ),
                name="member_gender_normalized_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "member_number"], name="member_gym_status_no_idx"
            ),
            models.Index(fields=["gym", "-join_date"], name="member_gym_joined_idx"),
            models.Index(fields=["gym", "normalized_full_name"], name="member_gym_name_idx"),
            models.Index(fields=["gym", "phone"], name="member_gym_phone_idx"),
            models.Index(fields=["gym", "email"], name="member_gym_email_idx"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        apply_member_normalization(self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = list(update_fields)
            for name in (
                "first_name",
                "last_name",
                "normalized_full_name",
                "phone",
                "email",
                "gender",
                "emergency_contact_name",
                "emergency_contact_phone",
                "address",
            ):
                if name not in fields:
                    fields.append(name)
            kwargs["update_fields"] = fields
        super().save(*args, **kwargs)

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
    """Sold membership with an immutable plan and price snapshot.

    Migration 0008 adds the same-gym creator and canceller foreign keys and the
    snapshot guard. Django cannot express those composite references, so they
    stay in SQL. ``cancelled_by_membership_id`` is nullable and is not a second
    Django foreign key. Pending, active, and suspended date ranges cannot
    overlap for one member.
    """

    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID"
        PARTIAL = "PARTIAL"
        PAID = "PAID"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
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
        max_length=20, choices=PaymentStatus, default=PaymentStatus.UNPAID
    )
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    revenue_source = models.CharField(max_length=12, choices=RevenueSource)
    note = models.TextField(blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
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
                condition=models.Q(list_price__gte=0),
                name="membership_list_nonnegative_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="membership_discount_nonnegative_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(final_amount__gte=0),
                name="membership_final_nonnegative_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__lte=models.F("list_price")),
                name="membership_discount_within_list_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    final_amount=models.F("list_price") - models.F("discount_amount")
                ),
                name="membership_total_exact_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_value__gte=1),
                name="membership_duration_positive_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_unit__in=["DAY", "WEEK", "MONTH", "YEAR"]),
                name="membership_duration_unit_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(plan_code="") & ~models.Q(plan_name=""),
                name="membership_snapshot_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"),
                name="membership_currency_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    payment_status__in=[
                        "UNPAID",
                        "PARTIAL",
                        "PAID",
                        "PARTIALLY_REFUNDED",
                        "REFUNDED",
                    ]
                ),
                name="membership_payment_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["PENDING", "ACTIVE", "EXPIRED", "SUSPENDED", "CANCELLED"]
                ),
                name="membership_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="CANCELLED",
                        cancelled_at__isnull=False,
                        cancelled_by_membership_id__isnull=False,
                        cancellation_reason__regex=r"[^[:space:]]",
                    )
                    | (
                        ~models.Q(status="CANCELLED")
                        & models.Q(
                            cancelled_at__isnull=True,
                            cancelled_by_membership_id__isnull=True,
                        )
                        & ~models.Q(cancellation_reason__regex=r"[^[:space:]]")
                    )
                ),
                name="membership_cancellation_state_ck",
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
                condition=models.Q(status__in=["PENDING", "ACTIVE", "SUSPENDED"]),
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status", "expiry_date"], name="membership_gym_expiry_idx"),
            models.Index(fields=["gym", "member", "start_date"], name="membership_member_date_idx"),
            models.Index(fields=["gym", "member", "status"], name="membership_member_status_idx"),
            models.Index(
                fields=["gym", "status", "-created_at"], name="membership_status_created_idx"
            ),
            models.Index(fields=["gym", "plan"], name="membership_gym_plan_idx"),
            models.Index(
                fields=["gym", "created_by_membership_id"], name="membership_gym_creator_idx"
            ),
            models.Index(
                fields=["gym", "cancelled_by_membership_id"],
                name="membership_gym_canceller_idx",
                condition=models.Q(cancelled_by_membership_id__isnull=False),
            ),
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
