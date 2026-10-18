from __future__ import annotations

from typing import Any

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models

from apps.common.db.models import TenantModel
from apps.members.normalization import apply_member_normalization


class Member(TenantModel):
    """Tenant member profile.

    ``linked_user`` and ``authz_version`` identify a member session. Linking is
    not exposed on the staff or member HTTP APIs. ``authz_version`` increases
    when the member is archived or the link changes, and those changes revoke
    MEMBER refresh sessions. Migration 0007 adds the same-gym profile-photo
    foreign key, the address shape check, and ``member_linked_user_fk`` with
    ``ON DELETE RESTRICT``. Django cannot express the composite photo
    reference, so that foreign key is declared in SQL. ``linked_user`` uses
    ``db_constraint=False`` so Django does not add a second foreign key beside
    that SQL ``ON DELETE RESTRICT`` constraint.
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
        CLASS = "CLASS"
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

    member = models.ForeignKey(
        Member, on_delete=models.RESTRICT, null=True, blank=True, related_name="payments"
    )
    membership = models.ForeignKey(
        Membership, on_delete=models.RESTRICT, null=True, blank=True, related_name="payments"
    )
    original_payment = models.ForeignKey(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="corrections"
    )
    reversal_payment = models.ForeignKey(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="reversed_by"
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
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by_membership_id = models.UUIDField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_membership_id = models.UUIDField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    cash_session = models.ForeignKey(
        "cashbook.CashSession",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="payments",
    )
    cash_transaction = models.OneToOneField(
        "cashbook.CashTransaction",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="source_payment",
    )
    retail_sale = models.ForeignKey(
        "sales.RetailSale",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="source_payments",
    )
    legacy_unlinked_cash = models.BooleanField(default=False)
    created_with_reception_scope = models.BooleanField(default=False)
    submitted_with_reception_scope = models.BooleanField(default=False)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_payment"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="payment_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payment_amount_positive_ck"
            ),
            models.UniqueConstraint(
                fields=["gym", "retail_sale"],
                condition=models.Q(entry_type="CHARGE"),
                name="payment_one_retail_sale_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "business_date"], name="payment_gym_status_date_idx"
            ),
            models.Index(
                fields=["gym", "member", "-received_at", "-id"],
                name="payment_gym_member_hist_idx",
            ),
            models.Index(fields=["gym", "membership", "status"], name="payment_gym_membership_idx"),
            models.Index(
                fields=["gym", "purpose", "business_date"], name="payment_gym_purpose_date_idx"
            ),
            models.Index(
                fields=["gym", "method", "business_date"], name="payment_gym_method_date_idx"
            ),
            models.Index(fields=["gym", "original_payment"], name="payment_gym_original_idx"),
            models.Index(fields=["gym", "retail_sale"], name="payment_gym_retail_sale_idx"),
        ]


class Receipt(TenantModel):
    class Status(models.TextChoices):
        ISSUED = "ISSUED"
        VOID = "VOID"
        REPLACED = "REPLACED"

    payment = models.ForeignKey(Payment, on_delete=models.RESTRICT, related_name="receipts")
    receipt_number = models.CharField(max_length=64)
    sequence_key = models.CharField(max_length=48)
    sequence_value = models.PositiveBigIntegerField()
    sequence_year = models.PositiveIntegerField()
    issued_at = models.DateTimeField()
    gym_code = models.CharField(max_length=32)
    gym_name = models.CharField(max_length=150)
    gym_address = models.CharField(max_length=800, blank=True, default="")
    member_number = models.CharField(max_length=32)
    member_name = models.CharField(max_length=201)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20)
    business_date = models.DateField()
    issuer_name = models.CharField(max_length=201)
    status = models.CharField(max_length=12, choices=Status, default=Status.ISSUED)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_membership_id = models.UUIDField(null=True, blank=True)
    void_reason = models.TextField(blank=True)
    replacement_receipt = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="replaced_receipts",
    )
    rendered_file_asset = models.ForeignKey(
        "files.FileAsset",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="receipts",
    )
    created_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "members_receipt"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="receipt_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "receipt_number"], name="receipt_gym_number_uniq"
            ),
            models.UniqueConstraint(
                fields=["gym", "sequence_key", "sequence_value"],
                name="receipt_gym_sequence_uniq",
            ),
            models.UniqueConstraint(
                fields=["gym", "payment"],
                condition=models.Q(status="ISSUED"),
                name="receipt_one_issued_per_payment",
            ),
        ]


class CheckIn(TenantModel):
    """Attendance record.

    Manual rows name the staff membership and leave ``device_id`` null. A
    member-self QR row has no membership actor and no device: the challenge
    hash is not copied onto the visit, and the challenge row already records
    which display issued it. NFC and biometric ingestion rows have no
    membership actor and a same-gym device of that capability. Source
    metadata stays null. Migration 0014 adds the device foreign key and the
    capability guard. Duplicate windows are service locks, not a
    timestamp-rounding constraint.
    """

    class Method(models.TextChoices):
        MANUAL = "MANUAL"
        QR = "QR"
        NFC = "NFC"
        BIOMETRIC = "BIOMETRIC"

    member = models.ForeignKey(Member, on_delete=models.RESTRICT, related_name="check_ins")
    membership = models.ForeignKey(
        Membership,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="check_ins",
    )
    check_in_at = models.DateTimeField()
    check_out_at = models.DateTimeField(null=True, blank=True)
    method = models.CharField(max_length=12, choices=Method.choices)
    recorded_by_membership_id = models.UUIDField(null=True, blank=True)
    device_id = models.UUIDField(null=True, blank=True)
    source_metadata = models.JSONField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default="")
    eligibility_snapshot = models.JSONField()
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "members_check_in"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="checkin_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="checkin_gym_idempotency_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=["MANUAL", "QR", "NFC", "BIOMETRIC"]),
                name="checkin_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(check_out_at__isnull=True)
                | models.Q(check_out_at__gt=models.F("check_in_at")),
                name="checkin_checkout_after_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(method="MANUAL")
                | (models.Q(device_id__isnull=True) & models.Q(source_metadata__isnull=True)),
                name="checkin_manual_source_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    method="MANUAL",
                    created_by_membership_id__isnull=False,
                    recorded_by_membership_id__isnull=False,
                )
                | models.Q(
                    method="QR",
                    created_by_membership_id__isnull=True,
                    recorded_by_membership_id__isnull=True,
                    device_id__isnull=True,
                    source_metadata__isnull=True,
                )
                | models.Q(
                    method__in=["NFC", "BIOMETRIC"],
                    created_by_membership_id__isnull=True,
                    recorded_by_membership_id__isnull=True,
                    device_id__isnull=False,
                    source_metadata__isnull=True,
                ),
                name="checkin_actor_method_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(idempotency_key__isnull=True)
                | models.Q(idempotency_key__regex=r"^[A-Za-z0-9._:-]{16,128}$"),
                name="checkin_idempotency_key_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "member", "-check_in_at", "-id"],
                name="checkin_gym_member_hist_idx",
            ),
            models.Index(
                fields=["gym", "-check_in_at", "-id"],
                name="checkin_gym_time_idx",
            ),
            models.Index(
                fields=["gym", "member"],
                condition=models.Q(check_out_at__isnull=True),
                name="checkin_open_member_idx",
            ),
        ]


class CheckInChallenge(TenantModel):
    """Short-lived onsite QR challenge.

    The plaintext secret is never a column. Staff issuance sets the membership
    actor and leaves ``display_device_id`` null. A display device sets
    ``display_device_id`` and leaves the membership actors null. The composite
    device parent is added in migration 0013.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        CONSUMED = "CONSUMED"
        EXPIRED = "EXPIRED"
        REVOKED = "REVOKED"

    challenge_hash = models.CharField(max_length=64)
    display_location = models.CharField(max_length=80)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    max_uses = models.PositiveIntegerField()
    issued_by_membership_id = models.UUIDField(null=True, blank=True)
    display_device_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices)
    created_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "members_check_in_challenge"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="checkin_chal_gym_id_uniq"),
            models.UniqueConstraint(fields=["challenge_hash"], name="checkin_chal_hash_uniq"),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "CONSUMED", "EXPIRED", "REVOKED"]),
                name="checkin_chal_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(challenge_hash__regex=r"^[0-9a-f]{64}$"),
                name="checkin_chal_hash_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(display_location__regex=r"^[^[:cntrl:]]{1,80}$"),
                name="checkin_chal_location_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(max_uses__gte=1) & models.Q(max_uses__lte=100),
                name="checkin_chal_max_uses_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(use_count__lte=models.F("max_uses")),
                name="checkin_chal_use_count_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="checkin_chal_expiry_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    issued_by_membership_id__isnull=False,
                    created_by_membership_id__isnull=False,
                    display_device_id__isnull=True,
                )
                | models.Q(
                    issued_by_membership_id__isnull=True,
                    created_by_membership_id__isnull=True,
                    display_device_id__isnull=False,
                ),
                name="checkin_chal_actor_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "status", "expires_at"],
                name="checkin_chal_status_idx",
            ),
            models.Index(
                fields=["gym"],
                condition=models.Q(status="ACTIVE"),
                name="checkin_chal_active_idx",
            ),
        ]


class CheckInDevice(TenantModel):
    """Gym-owned credential for one check-in capability.

    ``DISPLAY`` may issue challenges. ``NFC`` may ingest NFC events.
    ``BIOMETRIC`` may ingest biometric-result events. The selector is public.
    The secret is stored only as a Django password hash. Plaintext, biometric
    templates, card secrets, and vendor secrets are not columns. ``kind`` is
    immutable.
    """

    class Kind(models.TextChoices):
        DISPLAY = "DISPLAY"
        NFC = "NFC"
        BIOMETRIC = "BIOMETRIC"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        REVOKED = "REVOKED"

    label = models.CharField(max_length=80)
    location = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices)
    credential_selector = models.CharField(max_length=64)
    credential_hash = models.CharField(max_length=128)
    credential_version = models.PositiveIntegerField()
    rotated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by_membership_id = models.UUIDField()
    rotated_by_membership_id = models.UUIDField(null=True, blank=True)
    revoked_by_membership_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "members_check_in_device"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="checkin_dev_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["credential_selector"], name="checkin_dev_selector_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=["DISPLAY", "NFC", "BIOMETRIC"]),
                name="checkin_dev_kind_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "REVOKED"]),
                name="checkin_dev_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(label__regex=r"^[^[:cntrl:]]{1,80}$"),
                name="checkin_dev_label_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(location__regex=r"^[^[:cntrl:]]{1,80}$"),
                name="checkin_dev_location_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(credential_selector__regex=r"^[A-Za-z0-9_-]{16,64}$"),
                name="checkin_dev_selector_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credential_hash__regex=(
                        r"^pbkdf2_sha256\$[1-9][0-9]{6,}\$"
                        r"[A-Za-z0-9]{22}\$[A-Za-z0-9+/]{43}=$"
                    )
                ),
                name="checkin_dev_hash_ck",
            ),
            models.CheckConstraint(
                condition=~models.Q(credential_hash=models.F("credential_selector")),
                name="checkin_dev_hash_ne_sel_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credential_version=1,
                    rotated_at__isnull=True,
                    rotated_by_membership_id__isnull=True,
                )
                | models.Q(
                    credential_version__gte=2,
                    rotated_at__isnull=False,
                    rotated_by_membership_id__isnull=False,
                ),
                name="checkin_dev_rotation_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status="ACTIVE",
                    revoked_at__isnull=True,
                    revoked_by_membership_id__isnull=True,
                )
                | models.Q(
                    status="REVOKED",
                    revoked_at__isnull=False,
                    revoked_by_membership_id__isnull=False,
                ),
                name="checkin_dev_revoke_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "status"], name="checkin_dev_status_idx"),
        ]
