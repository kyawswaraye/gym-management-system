from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.common.db.models import TenantModel


class ClassType(TenantModel):
    """One gym's class catalog identity.

    ``code`` is immutable and matches the service-package token rule. Name is
    trimmed with collapsed whitespace. Description is optional.
    """

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    normalized_name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    default_duration_minutes = models.PositiveIntegerField()
    default_capacity = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "classes_class_type"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="class_type_gym_id_uniq"),
            models.UniqueConstraint(fields=["gym", "code"], name="class_type_gym_code_uniq"),
            models.CheckConstraint(
                condition=models.Q(default_duration_minutes__gte=1),
                name="class_type_duration_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(default_capacity__gte=1),
                name="class_type_capacity_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "code", "id"], name="class_type_gym_code_id_idx"),
            models.Index(fields=["gym", "is_active", "code", "id"], name="class_type_list_idx"),
            models.Index(fields=["gym", "created_by_membership_id"], name="class_type_actor_idx"),
        ]


class ClassSchedule(TenantModel):
    """A recurring class in the gym timezone snapshot taken at create."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        ENDED = "ENDED"
        CANCELLED = "CANCELLED"

    class_type = models.ForeignKey(
        ClassType,
        on_delete=models.RESTRICT,
        related_name="schedules",
    )
    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="class_schedules",
    )
    weekdays = ArrayField(base_field=models.SmallIntegerField(), size=7)
    local_start_time = models.TimeField()
    local_end_time = models.TimeField()
    effective_from = models.DateField()
    effective_until = models.DateField()
    timezone = models.CharField(max_length=64)
    location = models.CharField(max_length=120)
    normalized_location = models.CharField(max_length=120)
    location_is_exclusive = models.BooleanField()
    capacity = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    skipped_local_dates = ArrayField(base_field=models.DateField(), default=list, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by_membership_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "classes_class_schedule"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="class_sched_gym_id_uniq"),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "ENDED", "CANCELLED"]),
                name="class_sched_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(local_end_time__gt=models.F("local_start_time")),
                name="class_sched_times_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_until__gte=models.F("effective_from")),
                name="class_sched_dates_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="class_sched_capacity_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "trainer_staff_profile", "status", "effective_from"],
                name="class_sched_trainer_idx",
            ),
            models.Index(
                fields=["gym", "class_type", "status", "effective_from"],
                name="class_sched_type_idx",
            ),
            models.Index(
                fields=["gym", "effective_from", "local_start_time", "id"],
                name="class_sched_list_idx",
            ),
        ]


class ClassOccurrence(TenantModel):
    """One concrete class instant snapshotted from its schedule."""

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED"
        CANCELLED = "CANCELLED"
        COMPLETED = "COMPLETED"

    class_schedule = models.ForeignKey(
        ClassSchedule,
        on_delete=models.RESTRICT,
        related_name="occurrences",
    )
    class_type = models.ForeignKey(
        ClassType,
        on_delete=models.RESTRICT,
        related_name="occurrences",
    )
    trainer_staff_profile = models.ForeignKey(
        "tenants.StaffProfile",
        on_delete=models.RESTRICT,
        related_name="class_occurrences",
    )
    location = models.CharField(max_length=120)
    normalized_location = models.CharField(max_length=120)
    location_is_exclusive = models.BooleanField()
    capacity = models.PositiveIntegerField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    local_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_membership_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True, default="")
    created_by_membership_id = models.UUIDField()
    updated_by_membership_id = models.UUIDField()

    class Meta:
        db_table = "classes_class_occurrence"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="class_occ_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "class_schedule", "local_date"],
                name="class_occ_schedule_date_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["SCHEDULED", "CANCELLED", "COMPLETED"]),
                name="class_occ_status_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="class_occ_times_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity__gte=1),
                name="class_occ_capacity_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "starts_at", "id"], name="class_occ_start_idx"),
            models.Index(
                fields=["gym", "trainer_staff_profile", "starts_at", "id"],
                name="class_occ_trainer_idx",
            ),
            models.Index(
                fields=["gym", "status", "local_date", "id"],
                name="class_occ_status_idx",
            ),
        ]


class ClassBooking(TenantModel):
    """One member's booking of one class occurrence.

    Terminal rows stay. Cancellation, attendance, and no-show store actor and
    time. A metered booking names a class package; an unmetered booking stores
    a reason and no package.
    """

    class Status(models.TextChoices):
        BOOKED = "BOOKED"
        ATTENDED = "ATTENDED"
        NO_SHOW = "NO_SHOW"
        CANCELLED = "CANCELLED"

    class_occurrence = models.ForeignKey(
        ClassOccurrence,
        on_delete=models.RESTRICT,
        related_name="bookings",
    )
    member = models.ForeignKey(
        "members.Member",
        on_delete=models.RESTRICT,
        related_name="class_bookings",
    )
    member_service_package = models.ForeignKey(
        "trainers.MemberServicePackage",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="class_bookings",
    )
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
    created_by_membership_id = models.UUIDField(null=True, blank=True)
    updated_by_membership_id = models.UUIDField(null=True, blank=True)
    created_by_member_id = models.UUIDField(null=True, blank=True)
    updated_by_member_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "classes_class_booking"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="class_book_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "class_occurrence", "member"],
                condition=~models.Q(status="CANCELLED"),
                name="class_book_one_active_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["BOOKED", "ATTENDED", "NO_SHOW", "CANCELLED"]),
                name="class_book_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        created_by_membership_id__isnull=False,
                        created_by_member_id__isnull=True,
                    )
                    | models.Q(
                        created_by_membership_id__isnull=True,
                        created_by_member_id__isnull=False,
                    )
                ),
                name="class_book_creator_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["gym", "class_occurrence", "status"],
                name="class_book_occ_idx",
            ),
            models.Index(fields=["gym", "member", "status"], name="class_book_member_idx"),
            models.Index(
                fields=["gym", "created_by_membership_id"],
                name="class_book_actor_idx",
            ),
            models.Index(
                fields=["gym", "created_by_member_id"],
                name="class_book_member_actor_idx",
            ),
        ]
