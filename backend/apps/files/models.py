from __future__ import annotations

from django.db import models

from apps.common.db.models import TenantModel


class FileAsset(TenantModel):
    """Tenant-owned metadata for one private object.

    ``object_key`` is ``{gym_id}/{random_uuid}``. The key is organization, not
    authorization. ``owner_type`` and ``owner_id`` are an opaque pair with no
    foreign key; the file service proves that pair belongs to the same gym.
    ``upload_*`` keeps the original declaration. ``sha256``, ``byte_size``, and
    ``content_type`` describe the object currently stored at ``object_key``.
    ``deleted_at`` is present only when retention is ``DELETED``.
    """

    class ScanStatus(models.TextChoices):
        PENDING = "PENDING"
        SCANNING = "SCANNING"
        CLEAN = "CLEAN"
        REJECTED = "REJECTED"
        ERROR = "ERROR"

    class RetentionStatus(models.TextChoices):
        ACTIVE = "ACTIVE"
        QUARANTINED = "QUARANTINED"
        DELETE_PENDING = "DELETE_PENDING"
        DELETED = "DELETED"

    object_key = models.CharField(max_length=73)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255)
    byte_size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    purpose = models.CharField(max_length=64)
    scan_status = models.CharField(
        max_length=8,
        choices=ScanStatus,
        default=ScanStatus.PENDING,
    )
    owner_type = models.CharField(max_length=64, null=True, blank=True)
    owner_id = models.UUIDField(null=True, blank=True)
    uploaded_by_membership_id = models.UUIDField()
    upload_sha256 = models.CharField(max_length=64, null=True, blank=True)
    upload_byte_size = models.BigIntegerField(null=True, blank=True)
    upload_content_type = models.CharField(max_length=255, null=True, blank=True)
    rejection_code = models.CharField(max_length=64, null=True, blank=True)
    scan_attempt_id = models.UUIDField(null=True, blank=True)
    scan_started_at = models.DateTimeField(null=True, blank=True)
    retention_status = models.CharField(
        max_length=14,
        choices=RetentionStatus,
        default=RetentionStatus.ACTIVE,
    )
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "files_file_asset"
        constraints = [
            models.UniqueConstraint(fields=["gym", "id"], name="fileasset_gym_id_uniq"),
            models.UniqueConstraint(
                fields=["gym", "object_key"],
                name="fileasset_gym_object_key_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(original_name__regex=r"^[[:space:]]*$")
                    & ~models.Q(original_name__regex=r"[/\\]")
                    & ~models.Q(content_type__regex=r"^[[:space:]]*$")
                    & ~models.Q(purpose__regex=r"^[[:space:]]*$")
                ),
                name="fileasset_metadata_nonblank_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    content_type__regex=(r"^[a-z0-9][a-z0-9.+-]{0,126}/[a-z0-9][a-z0-9.+-]{0,126}$")
                ),
                name="fileasset_content_type_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    object_key__regex=(
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}$"
                    )
                ),
                name="fileasset_object_key_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(byte_size__gte=0),
                name="fileasset_byte_size_nonnegative_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="fileasset_sha256_format_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    scan_status__in=["PENDING", "SCANNING", "CLEAN", "REJECTED", "ERROR"]
                ),
                name="fileasset_scan_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(upload_sha256__isnull=True)
                    | models.Q(upload_sha256__regex=r"^[0-9a-f]{64}$")
                ),
                name="fileasset_upload_sha256_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(rejection_code__isnull=True)
                    | models.Q(rejection_code__regex=r"^[A-Z0-9_]{1,64}$")
                ),
                name="fileasset_rejection_code_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        scan_status="SCANNING",
                        scan_attempt_id__isnull=False,
                        scan_started_at__isnull=False,
                        retention_status="QUARANTINED",
                    )
                    | (
                        ~models.Q(scan_status="SCANNING")
                        & models.Q(scan_attempt_id__isnull=True, scan_started_at__isnull=True)
                    )
                ),
                name="fileasset_scan_claim_ck",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(purpose="PROFILE_PHOTO")
                    | (
                        models.Q(owner_type="MEMBER", owner_id__isnull=False)
                        & models.Q(
                            upload_sha256__isnull=False,
                            upload_byte_size__isnull=False,
                            upload_content_type__isnull=False,
                        )
                        & models.Q(
                            upload_content_type__in=["image/jpeg", "image/png", "image/webp"],
                            content_type__in=["image/jpeg", "image/png", "image/webp"],
                        )
                        & models.Q(upload_byte_size__gte=1, upload_byte_size__lte=5242880)
                        & models.Q(byte_size__gte=1, byte_size__lte=5242880)
                        & (~models.Q(retention_status="ACTIVE") | models.Q(scan_status="CLEAN"))
                        & (
                            models.Q(scan_status="REJECTED", rejection_code__isnull=False)
                            | (
                                ~models.Q(scan_status="REJECTED")
                                & models.Q(rejection_code__isnull=True)
                            )
                        )
                        & (
                            ~models.Q(scan_status="CLEAN")
                            | models.Q(content_type=models.F("upload_content_type"))
                        )
                    )
                ),
                name="fileasset_profile_photo_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(owner_type__isnull=True, owner_id__isnull=True)
                    | (
                        models.Q(owner_type__isnull=False, owner_id__isnull=False)
                        & ~models.Q(owner_type__regex=r"^[[:space:]]*$")
                    )
                ),
                name="fileasset_owner_pair_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    retention_status__in=[
                        "ACTIVE",
                        "QUARANTINED",
                        "DELETE_PENDING",
                        "DELETED",
                    ]
                ),
                name="fileasset_retention_status_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(retention_status="DELETED", deleted_at__isnull=False)
                    | (~models.Q(retention_status="DELETED") & models.Q(deleted_at__isnull=True))
                ),
                name="fileasset_retention_deleted_ck",
            ),
        ]
        indexes = [
            models.Index(fields=["gym", "purpose"], name="fileasset_gym_purpose_idx"),
            models.Index(
                fields=["gym", "owner_type", "owner_id"],
                name="fileasset_gym_owner_idx",
            ),
            models.Index(fields=["gym", "scan_status"], name="fileasset_gym_scan_idx"),
            models.Index(
                fields=["gym", "retention_status"],
                name="fileasset_gym_retention_idx",
            ),
            models.Index(
                fields=["gym", "uploaded_by_membership_id"],
                name="fileasset_gym_uploader_idx",
            ),
        ]
