from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any, NoReturn
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.audit.services import write_tenant_audit
from apps.cashbook.selectors import holds_protected_role, membership_has_permission
from apps.common.db.tenant_context import set_gym_context
from apps.files.constants import (
    ATTACHMENT_NAMES,
    CLAIM_STALE_SECONDS,
    EXPENSE_RECEIPT_NAMES,
    EXPENSE_RECEIPT_PURPOSE,
    GYM_OWNER,
    MEMBER_OWNER,
    PROFILE_PHOTO_PURPOSE,
    QUARANTINE_HOURS,
)
from apps.files.exceptions import (
    FileRequestError,
    ObjectMissing,
    ObjectStatInvalid,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from apps.files.images import CanonicalImage
from apps.files.models import FileAsset
from apps.files.verification import Verdict, VerifyOutcome, verify_upload
from apps.members.models import Member
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership

_SAFE_AUDIT_KEYS = frozenset(
    {
        "purpose",
        "owner_id",
        "content_type",
        "byte_size",
        "sha256",
        "rejection_code",
        "scan_status",
        "retention_status",
    }
)


@dataclass(frozen=True)
class RequestAudit:
    actor_user: Any
    actor_membership_id: UUID | None
    request_id: str
    ip_address: str | None
    user_agent: str


@dataclass(frozen=True)
class FileCommand:
    status_code: int
    data: dict[str, object]
    message: str


@dataclass(frozen=True)
class _Claim:
    asset_id: UUID
    attempt_id: UUID
    object_key: str
    upload_sha256: str
    upload_byte_size: int
    upload_content_type: str


def utc_now() -> datetime:
    return timezone.now().astimezone(UTC).replace(microsecond=0)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_upload_intent(
    *,
    gym: Gym,
    actor: GymMembership,
    audit: RequestAudit,
    store: Any,
    owner_id: UUID,
    original_name: str,
    content_type: str,
    byte_size: int,
    sha256: str,
    purpose: str = PROFILE_PHOTO_PURPOSE,
    owner_type: str = MEMBER_OWNER,
    moment: datetime | None = None,
) -> FileCommand:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored_owner_id = owner_id
        stored_owner_type = owner_type
        stored_purpose = purpose
        if purpose == PROFILE_PHOTO_PURPOSE:
            locked_member = _locked_member(gym, owner_id)
            _require_scope(gym, actor, locked_member)
            _require_members_entitlement(gym)
            stored_owner_id = locked_member.id
            stored_owner_type = MEMBER_OWNER
            stored_purpose = PROFILE_PHOTO_PURPOSE
        elif purpose == EXPENSE_RECEIPT_PURPOSE:
            _require_finance_entitlement(gym)
            if owner_type != GYM_OWNER or owner_id != gym.id:
                raise FileRequestError(
                    code="UNSUPPORTED_FILE_OWNER",
                    message="This file owner is not supported.",
                    status_code=422,
                    fields={"owner_id": ["This file owner is not supported."]},
                )
            if not (
                membership_has_permission(membership=actor, code="expenses.create")
                or membership_has_permission(membership=actor, code="expenses.update")
            ):
                raise PermissionDenied()
            stored_owner_id = gym.id
            stored_owner_type = GYM_OWNER
            stored_purpose = EXPENSE_RECEIPT_PURPOSE
        else:
            raise FileRequestError(
                code="UNSUPPORTED_FILE_PURPOSE",
                message="This file purpose is not supported.",
                status_code=422,
                fields={"purpose": ["This file purpose is not supported."]},
            )
        asset = FileAsset.objects.create(
            gym=gym,
            object_key=f"{gym.id}/{uuid4()}",
            original_name=original_name,
            content_type=content_type,
            byte_size=byte_size,
            sha256=sha256,
            upload_content_type=content_type,
            upload_byte_size=byte_size,
            upload_sha256=sha256,
            purpose=stored_purpose,
            scan_status=FileAsset.ScanStatus.PENDING,
            owner_type=stored_owner_type,
            owner_id=stored_owner_id,
            uploaded_by_membership_id=actor.id,
            retention_status=FileAsset.RetentionStatus.QUARANTINED,
            rejection_code=None,
            scan_attempt_id=None,
            scan_started_at=None,
            deleted_at=None,
        )
        _audit(
            gym=gym,
            audit=audit,
            action="FILE_UPLOAD_INTENT",
            asset=asset,
            values={
                "purpose": asset.purpose,
                "owner_id": str(stored_owner_id),
                "content_type": content_type,
                "byte_size": byte_size,
                "sha256": sha256,
            },
        )
        signed_at = moment or utc_now()
        try:
            upload = store.presign_post(
                object_key=asset.object_key,
                content_type=content_type,
                moment=signed_at,
            )
        except ObjectStoreUnavailable:
            raise _store_failed() from None
    return FileCommand(
        status_code=201,
        message="Upload intent created",
        data={
            "file_id": asset.id,
            "purpose": asset.purpose,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
            "upload": {
                "method": "POST",
                "url": upload.url,
                "file_field": "file",
                "expires_at": format_utc(upload.expires_at),
                "fields": upload.fields,
            },
        },
    )


def create_member_profile_photo_intent(
    *,
    gym: Gym,
    member: Member,
    audit: RequestAudit,
    store: Any,
    original_name: str,
    content_type: str,
    byte_size: int,
    sha256: str,
    moment: datetime | None = None,
) -> FileCommand:
    """Create a profile-photo intent owned by the authenticated member.

    The     server chooses the purpose, owner, and member uploader. A staff
    membership is never invented for this row.
    """

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        locked_member = _locked_member(gym, member.id)
        if locked_member.status != Member.Status.ACTIVE:
            raise FileRequestError(
                code="AUTHENTICATION_FAILED",
                message="Authentication failed.",
                status_code=401,
            )
        _require_members_entitlement(gym)
        asset = FileAsset.objects.create(
            gym=gym,
            object_key=f"{gym.id}/{uuid4()}",
            original_name=original_name,
            content_type=content_type,
            byte_size=byte_size,
            sha256=sha256,
            upload_content_type=content_type,
            upload_byte_size=byte_size,
            upload_sha256=sha256,
            purpose=PROFILE_PHOTO_PURPOSE,
            scan_status=FileAsset.ScanStatus.PENDING,
            owner_type=MEMBER_OWNER,
            owner_id=locked_member.id,
            uploaded_by_membership_id=None,
            uploaded_by_member_id=locked_member.id,
            retention_status=FileAsset.RetentionStatus.QUARANTINED,
            rejection_code=None,
            scan_attempt_id=None,
            scan_started_at=None,
            deleted_at=None,
        )
        _audit(
            gym=gym,
            audit=audit,
            action="FILE_UPLOAD_INTENT",
            asset=asset,
            values={
                "purpose": asset.purpose,
                "owner_id": str(locked_member.id),
                "content_type": content_type,
                "byte_size": byte_size,
                "sha256": sha256,
            },
        )
        signed_at = moment or utc_now()
        try:
            upload = store.presign_post(
                object_key=asset.object_key,
                content_type=content_type,
                moment=signed_at,
            )
        except ObjectStoreUnavailable:
            raise _store_failed() from None
    return FileCommand(
        status_code=201,
        message="Upload intent created",
        data={
            "file_id": asset.id,
            "purpose": asset.purpose,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
            "upload": {
                "method": "POST",
                "url": upload.url,
                "file_field": "file",
                "expires_at": format_utc(upload.expires_at),
                "fields": upload.fields,
            },
        },
    )


def complete_upload(
    *,
    gym: Gym,
    actor: GymMembership | None = None,
    audit: RequestAudit,
    store: Any,
    scanner: Any,
    canonicalize: Callable[[bytes, str], CanonicalImage],
    asset_id: UUID,
    sha256: str,
    byte_size: int,
    member_self: Member | None = None,
) -> FileCommand:
    claim = _claim_upload(
        gym=gym,
        actor=actor,
        audit=audit,
        asset_id=asset_id,
        sha256=sha256,
        byte_size=byte_size,
        member_self=member_self,
    )
    if isinstance(claim, FileCommand):
        return claim
    verdict = verify_upload(
        store=store,
        scanner=scanner,
        canonicalize=canonicalize,
        object_key=claim.object_key,
        upload_sha256=claim.upload_sha256,
        upload_byte_size=claim.upload_byte_size,
        upload_content_type=claim.upload_content_type,
        allow_overwrite=lambda: _attempt_still_held(gym, actor, claim, member_self=member_self),
        release_unclaimed_object=lambda: _drop_orphaned_canonical(
            gym=gym,
            actor=actor,
            store=store,
            claim=claim,
            member_self=member_self,
        ),
    )
    return _finalize_upload(
        gym=gym,
        actor=actor,
        audit=audit,
        store=store,
        claim=claim,
        verdict=verdict,
        sha256=sha256,
        byte_size=byte_size,
        member_self=member_self,
    )


def issue_download_url(
    *,
    gym: Gym,
    actor: GymMembership | None = None,
    audit: RequestAudit,
    store: Any,
    asset_id: UUID,
    moment: datetime | None = None,
    member_self: Member | None = None,
) -> FileCommand:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=False)
        if not _downloadable(asset):
            raise _not_downloadable()
        snapshot = (asset.content_type, asset.byte_size, asset.sha256, asset.object_key)
    content_type, byte_size, digest, object_key = snapshot
    try:
        stat = store.head(object_key)
    except (ObjectMissing, ObjectStatInvalid):
        raise _integrity_failed() from None
    except (ObjectStoreUnavailable, ObjectTooLarge):
        raise _store_failed() from None
    metadata = {key.lower(): value for key, value in stat.metadata.items()}
    if (
        stat.content_length != byte_size
        or metadata.get("x-amz-meta-gms-byte-size") != str(byte_size)
        or metadata.get("x-amz-meta-gms-sha256") != digest
        or metadata.get("x-amz-meta-gms-content-type") != content_type
    ):
        raise _integrity_failed()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=False)
        if (
            not _downloadable(asset)
            or asset.content_type != content_type
            or asset.byte_size != byte_size
            or asset.sha256 != digest
        ):
            raise _not_downloadable()
        _audit(
            gym=gym,
            audit=audit,
            action="FILE_DOWNLOAD_URL_ISSUED",
            asset=asset,
            values={
                "purpose": asset.purpose,
                "owner_id": str(asset.owner_id),
                "content_type": content_type,
                "byte_size": byte_size,
                "sha256": digest,
            },
        )
    filename = (
        EXPENSE_RECEIPT_NAMES[content_type]
        if asset.purpose == EXPENSE_RECEIPT_PURPOSE
        else ATTACHMENT_NAMES[content_type]
    )
    signed_at = moment or utc_now()
    try:
        download = store.presign_get(
            object_key=object_key,
            content_type=content_type,
            filename=filename,
            moment=signed_at,
        )
    except ObjectStoreUnavailable:
        raise _store_failed() from None
    return FileCommand(
        status_code=200,
        message="Download URL issued",
        data={
            "url": download.url,
            "method": "GET",
            "expires_at": format_utc(download.expires_at),
            "filename": filename,
            "content_type": content_type,
            "byte_size": byte_size,
            "sha256": digest,
        },
    )


def delete_upload(
    *,
    gym: Gym,
    actor: GymMembership | None = None,
    audit: RequestAudit,
    store: Any,
    asset_id: UUID,
    member_self: Member | None = None,
) -> FileCommand:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=False)
        if asset.purpose == EXPENSE_RECEIPT_PURPOSE and _expense_receipt_referenced(gym, asset.id):
            raise _state_conflict()
        if asset.retention_status == FileAsset.RetentionStatus.DELETED:
            _detach_photo(member, asset.id, gym=gym, audit=audit, member_self=member_self)
            return _delete_result(asset)
        if asset.retention_status != FileAsset.RetentionStatus.DELETE_PENDING:
            _detach_photo(member, asset.id, gym=gym, audit=audit, member_self=member_self)
            if asset.scan_status == FileAsset.ScanStatus.SCANNING:
                asset.scan_status = FileAsset.ScanStatus.ERROR
                asset.scan_attempt_id = None
                asset.scan_started_at = None
            asset.retention_status = FileAsset.RetentionStatus.DELETE_PENDING
            asset.deleted_at = None
            asset.save(
                update_fields=[
                    "scan_status",
                    "scan_attempt_id",
                    "scan_started_at",
                    "retention_status",
                    "deleted_at",
                    "updated_at",
                ]
            )
            _audit(
                gym=gym,
                audit=audit,
                action="FILE_DELETE_REQUESTED",
                asset=asset,
                values={
                    "purpose": asset.purpose,
                    "owner_id": str(asset.owner_id),
                    "scan_status": asset.scan_status,
                    "retention_status": asset.retention_status,
                    "rejection_code": asset.rejection_code,
                },
            )
        object_key = asset.object_key
    removed = _delete_object(store, object_key)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        _member, asset = _lock_owned_asset(gym, asset_id)
        if removed and asset.retention_status == FileAsset.RetentionStatus.DELETE_PENDING:
            asset.retention_status = FileAsset.RetentionStatus.DELETED
            asset.deleted_at = timezone.now()
            asset.save(update_fields=["retention_status", "deleted_at", "updated_at"])
        if asset.retention_status != FileAsset.RetentionStatus.DELETED:
            raise _delete_pending()
        return _delete_result(asset)


def expire_quarantined_uploads(
    *,
    gym: Gym,
    store: Any,
    limit: int = 100,
    before_claim: Callable[[UUID], None] | None = None,
) -> int:
    """Retire abandoned uploads and retry object removal. One row at a time.

    ``before_claim`` runs after a row is selected and before its claim transaction.
    Tests use it to attach that photo before the locked recheck.
    """

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        cutoff = timezone.now() - timedelta(hours=QUARANTINE_HOURS)
        referenced = Member.objects.filter(gym=gym, profile_photo_asset_id=OuterRef("id"))
        private_purpose = Q(purpose__in=[PROFILE_PHOTO_PURPOSE, EXPENSE_RECEIPT_PURPOSE])
        cutoff_ids = list(
            FileAsset.objects.filter(gym=gym)
            .filter(
                (
                    private_purpose
                    & (
                        Q(retention_status=FileAsset.RetentionStatus.DELETE_PENDING)
                        | Q(
                            retention_status=FileAsset.RetentionStatus.QUARANTINED,
                            scan_status=FileAsset.ScanStatus.REJECTED,
                        )
                        | Q(
                            retention_status=FileAsset.RetentionStatus.QUARANTINED,
                            created_at__lte=cutoff,
                        )
                    )
                )
                | (
                    Q(
                        purpose=PROFILE_PHOTO_PURPOSE,
                        retention_status=FileAsset.RetentionStatus.ACTIVE,
                        scan_status=FileAsset.ScanStatus.CLEAN,
                        created_at__lte=cutoff,
                    )
                    & ~Exists(referenced)
                )
            )
            .order_by("created_at", "id")
            .values_list("id", flat=True)[:limit]
        )
    processed = 0
    for asset_id in cutoff_ids:
        if before_claim is not None:
            before_claim(asset_id)
        if _expire_one(gym=gym, store=store, asset_id=asset_id):
            processed += 1
    return processed


def _expire_one(*, gym: Gym, store: Any, asset_id: UUID) -> bool:
    removal_code = ""
    object_key = ""
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        try:
            member, asset = _lock_owned_asset(gym, asset_id)
        except Http404:
            return False
        if asset.retention_status == FileAsset.RetentionStatus.DELETE_PENDING:
            object_key = asset.object_key
        elif asset.retention_status == FileAsset.RetentionStatus.QUARANTINED:
            if asset.scan_status == FileAsset.ScanStatus.REJECTED:
                object_key = asset.object_key
                removal_code = asset.rejection_code or ""
            elif not _older_than_retention_window(asset.id):
                return False
            elif asset.scan_status in {FileAsset.ScanStatus.PENDING, FileAsset.ScanStatus.ERROR}:
                _reject_expired(gym, asset, attempt_id=None)
                object_key = asset.object_key
                removal_code = "FILE_UPLOAD_EXPIRED"
            elif asset.scan_status == FileAsset.ScanStatus.SCANNING:
                age = _claim_age_seconds(asset.id)
                if age is None or age < CLAIM_STALE_SECONDS:
                    return False
                if not _reject_expired(gym, asset, attempt_id=asset.scan_attempt_id):
                    return False
                object_key = asset.object_key
                removal_code = "FILE_UPLOAD_EXPIRED"
            else:
                return False
        elif _claim_unattached_profile_photo(gym, asset):
            object_key = asset.object_key
        else:
            return False
        _ = member
    if not object_key:
        return False
    _retire_object(
        gym=gym, store=store, asset_id=asset_id, object_key=object_key, code=removal_code
    )
    return True


def _claim_upload(
    *,
    gym: Gym,
    actor: GymMembership | None,
    audit: RequestAudit,
    asset_id: UUID,
    sha256: str,
    byte_size: int,
    member_self: Member | None = None,
) -> _Claim | FileCommand:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=False)
        if asset.scan_status == FileAsset.ScanStatus.REJECTED:
            raise _rejected(asset)
        if asset.retention_status in {
            FileAsset.RetentionStatus.DELETE_PENDING,
            FileAsset.RetentionStatus.DELETED,
        }:
            raise _state_conflict()
        if _is_clean(asset):
            if not _confirmation_matches(asset, sha256, byte_size):
                raise _confirmation_mismatch()
            if member_self is not None:
                _attach_member_photo(gym, audit, member, asset)
            _audit_completed(gym, audit, asset)
            return _clean_result(asset)
        if asset.scan_status == FileAsset.ScanStatus.SCANNING:
            age = _claim_age_seconds(asset.id)
            if age is None or age < CLAIM_STALE_SECONDS:
                raise _scan_in_progress(1 if age is None else _retry_after(age))
            if not _confirmation_matches(asset, sha256, byte_size):
                raise _confirmation_mismatch()
        elif asset.scan_status in {FileAsset.ScanStatus.PENDING, FileAsset.ScanStatus.ERROR}:
            if asset.retention_status != FileAsset.RetentionStatus.QUARANTINED:
                raise _state_conflict()
            if not _confirmation_matches(asset, sha256, byte_size):
                raise _confirmation_mismatch()
        else:
            raise _state_conflict()
        attempt_id = uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE files_file_asset
                SET scan_status = 'SCANNING',
                    scan_attempt_id = %s,
                    scan_started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND gym_id = %s
                  AND retention_status = 'QUARANTINED'
                """,
                [attempt_id, asset.id, gym.id],
            )
            if cursor.rowcount != 1:
                raise _state_conflict()
        return _Claim(
            asset_id=asset.id,
            attempt_id=attempt_id,
            object_key=asset.object_key,
            upload_sha256=asset.upload_sha256 or "",
            upload_byte_size=int(asset.upload_byte_size or 0),
            upload_content_type=asset.upload_content_type or "",
        )


def _finalize_upload(
    *,
    gym: Gym,
    actor: GymMembership | None,
    audit: RequestAudit,
    store: Any,
    claim: _Claim,
    verdict: Verdict,
    sha256: str,
    byte_size: int,
    member_self: Member | None = None,
) -> FileCommand:
    if verdict.wrote_canonical:
        _drop_orphaned_canonical(
            gym=gym, actor=actor, store=store, claim=claim, member_self=member_self
        )
    if verdict.outcome is VerifyOutcome.ABORTED:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            member, asset = _lock_owned_asset(gym, claim.asset_id)
            _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=True)
            return _command_from_locked(
                gym, audit, asset, sha256, byte_size, member=member, member_self=member_self
            )
    if verdict.outcome is VerifyOutcome.MISSING:
        return _release_missing_upload(
            gym=gym,
            actor=actor,
            audit=audit,
            claim=claim,
            sha256=sha256,
            byte_size=byte_size,
            member_self=member_self,
        )
    result: FileCommand | None = None
    error_code = ""
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, claim.asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=True)
        if asset.scan_attempt_id != claim.attempt_id or (
            asset.retention_status != FileAsset.RetentionStatus.QUARANTINED
        ):
            return _command_from_locked(
                gym, audit, asset, sha256, byte_size, member=member, member_self=member_self
            )
        if verdict.outcome is VerifyOutcome.CLEAN:
            asset.content_type = verdict.content_type
            asset.byte_size = verdict.byte_size
            asset.sha256 = verdict.sha256
            asset.scan_status = FileAsset.ScanStatus.CLEAN
            asset.retention_status = FileAsset.RetentionStatus.ACTIVE
            asset.rejection_code = None
            asset.scan_attempt_id = None
            asset.scan_started_at = None
            asset.save(
                update_fields=[
                    "content_type",
                    "byte_size",
                    "sha256",
                    "scan_status",
                    "retention_status",
                    "rejection_code",
                    "scan_attempt_id",
                    "scan_started_at",
                    "updated_at",
                ]
            )
            _audit_completed(gym, audit, asset)
            if member_self is not None:
                _attach_member_photo(gym, audit, member, asset)
            result = _clean_result(asset)
        elif verdict.outcome is VerifyOutcome.REJECTED:
            asset.scan_status = FileAsset.ScanStatus.REJECTED
            asset.rejection_code = verdict.code
            asset.retention_status = FileAsset.RetentionStatus.QUARANTINED
            asset.scan_attempt_id = None
            asset.scan_started_at = None
            asset.save(
                update_fields=[
                    "scan_status",
                    "rejection_code",
                    "retention_status",
                    "scan_attempt_id",
                    "scan_started_at",
                    "updated_at",
                ]
            )
            _audit(
                gym=gym,
                audit=audit,
                action="FILE_UPLOAD_REJECTED",
                asset=asset,
                values={
                    "purpose": asset.purpose,
                    "owner_id": str(asset.owner_id),
                    "content_type": asset.content_type,
                    "byte_size": asset.byte_size,
                    "sha256": asset.sha256,
                    "rejection_code": verdict.code,
                },
            )
            result = None
            error_code = ""
        else:
            asset.scan_status = FileAsset.ScanStatus.ERROR
            asset.scan_attempt_id = None
            asset.scan_started_at = None
            asset.save(
                update_fields=[
                    "scan_status",
                    "scan_attempt_id",
                    "scan_started_at",
                    "updated_at",
                ]
            )
            _audit(
                gym=gym,
                audit=audit,
                action="FILE_UPLOAD_ERROR",
                asset=asset,
                values={
                    "purpose": asset.purpose,
                    "owner_id": str(asset.owner_id),
                    "content_type": asset.content_type,
                    "byte_size": asset.byte_size,
                    "sha256": asset.sha256,
                },
            )
            error_code = verdict.code or "FILE_STORE_FAILED"
    if verdict.outcome is VerifyOutcome.CLEAN:
        assert result is not None
        return result
    if verdict.outcome is VerifyOutcome.REJECTED:
        _retire_object(
            gym=gym,
            store=store,
            asset_id=claim.asset_id,
            object_key=claim.object_key,
            code=verdict.code,
        )
        raise _rejected_code(verdict.code)
    raise _error_code(error_code)


def _attempt_still_held(
    gym: Gym,
    actor: GymMembership | None,
    claim: _Claim,
    *,
    member_self: Member | None = None,
) -> bool:
    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            member, asset = _lock_owned_asset(gym, claim.asset_id)
            _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=True)
            return (
                asset.scan_attempt_id == claim.attempt_id
                and asset.retention_status == FileAsset.RetentionStatus.QUARANTINED
            )
    except Http404:
        return False


def _release_missing_upload(
    *,
    gym: Gym,
    actor: GymMembership | None,
    audit: RequestAudit,
    claim: _Claim,
    sha256: str,
    byte_size: int,
    member_self: Member | None = None,
) -> FileCommand:
    released = False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member, asset = _lock_owned_asset(gym, claim.asset_id)
        _require_caller_asset(gym, actor, member, asset, member_self=member_self, held=True)
        if (
            asset.scan_attempt_id == claim.attempt_id
            and asset.retention_status == FileAsset.RetentionStatus.QUARANTINED
        ):
            asset.scan_status = FileAsset.ScanStatus.PENDING
            asset.scan_attempt_id = None
            asset.scan_started_at = None
            asset.save(
                update_fields=[
                    "scan_status",
                    "scan_attempt_id",
                    "scan_started_at",
                    "updated_at",
                ]
            )
            released = True
        else:
            return _command_from_locked(
                gym, audit, asset, sha256, byte_size, member=member, member_self=member_self
            )
    if released:
        raise _incomplete()
    raise _state_conflict()


def _command_from_locked(
    gym: Gym,
    audit: RequestAudit,
    asset: FileAsset,
    sha256: str,
    byte_size: int,
    *,
    member: Member | None = None,
    member_self: Member | None = None,
) -> FileCommand:
    if asset.scan_status == FileAsset.ScanStatus.REJECTED:
        raise _rejected(asset)
    if asset.retention_status in {
        FileAsset.RetentionStatus.DELETE_PENDING,
        FileAsset.RetentionStatus.DELETED,
    }:
        raise _state_conflict()
    if _is_clean(asset):
        if not _confirmation_matches(asset, sha256, byte_size):
            raise _confirmation_mismatch()
        if member_self is not None:
            _attach_member_photo(gym, audit, member, asset)
        _audit_completed(gym, audit, asset)
        return _clean_result(asset)
    if asset.scan_status == FileAsset.ScanStatus.SCANNING:
        age = _claim_age_seconds(asset.id)
        retry = 1 if age is None or age >= CLAIM_STALE_SECONDS else _retry_after(age)
        raise _scan_in_progress(retry)
    if asset.scan_status == FileAsset.ScanStatus.PENDING:
        raise _incomplete()
    if asset.scan_status == FileAsset.ScanStatus.ERROR:
        raise _error_code("FILE_SCAN_UNAVAILABLE")
    raise _state_conflict()


def _retire_object(
    *,
    gym: Gym,
    store: Any,
    asset_id: UUID,
    object_key: str,
    code: str,
) -> None:
    removed = _delete_object(store, object_key)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        try:
            _member, asset = _lock_owned_asset(gym, asset_id)
        except Http404:
            return
        if asset.retention_status == FileAsset.RetentionStatus.DELETE_PENDING and removed:
            asset.retention_status = FileAsset.RetentionStatus.DELETED
            asset.deleted_at = timezone.now()
            asset.save(update_fields=["retention_status", "deleted_at", "updated_at"])
            return
        if (
            asset.scan_status != FileAsset.ScanStatus.REJECTED
            or asset.retention_status != FileAsset.RetentionStatus.QUARANTINED
            or asset.rejection_code != code
        ):
            return
        if removed:
            asset.retention_status = FileAsset.RetentionStatus.DELETED
            asset.deleted_at = timezone.now()
        else:
            asset.retention_status = FileAsset.RetentionStatus.DELETE_PENDING
            asset.deleted_at = None
        asset.save(update_fields=["retention_status", "deleted_at", "updated_at"])


def _claim_unattached_profile_photo(gym: Gym, asset: FileAsset) -> bool:
    """Claim one old clean profile photo that no member currently displays.

    The caller holds the owning member lock and the asset lock. The reference
    check runs again under those locks so an attach that committed after the
    candidate query cannot be deleted. The member pointer is left unchanged.
    """

    if asset.purpose != PROFILE_PHOTO_PURPOSE:
        return False
    if asset.scan_status != FileAsset.ScanStatus.CLEAN:
        return False
    if asset.retention_status != FileAsset.RetentionStatus.ACTIVE:
        return False
    if not _older_than_retention_window(asset.id):
        return False
    if _profile_photo_referenced(gym, asset.id):
        return False
    updated = FileAsset.objects.filter(
        pk=asset.pk,
        gym=gym,
        purpose=PROFILE_PHOTO_PURPOSE,
        scan_status=FileAsset.ScanStatus.CLEAN,
        retention_status=FileAsset.RetentionStatus.ACTIVE,
    ).update(
        retention_status=FileAsset.RetentionStatus.DELETE_PENDING,
        deleted_at=None,
        updated_at=timezone.now(),
    )
    if updated != 1:
        return False
    asset.refresh_from_db()
    write_tenant_audit(
        gym=gym,
        actor_user=None,
        actor_membership_id=None,
        action="FILE_DELETE_REQUESTED",
        module="files",
        target_type="FileAsset",
        target_id=asset.id,
        request_id="file-retention",
        new_values=_safe_audit_values(
            purpose=asset.purpose,
            owner_id=str(asset.owner_id),
            scan_status=asset.scan_status,
            retention_status=asset.retention_status,
        ),
    )
    return True


def _profile_photo_referenced(gym: Gym, asset_id: UUID) -> bool:
    return Member.objects.filter(gym=gym, profile_photo_asset_id=asset_id).exists()


def _expense_receipt_referenced(gym: Gym, asset_id: UUID) -> bool:
    from apps.expenses.models import Expense

    return Expense.objects.filter(gym=gym, receipt_file_asset_id=asset_id).exists()


def _reject_expired(gym: Gym, asset: FileAsset, *, attempt_id: UUID | None) -> bool:
    updated = FileAsset.objects.filter(
        pk=asset.pk,
        gym=gym,
        retention_status=FileAsset.RetentionStatus.QUARANTINED,
        scan_attempt_id=attempt_id,
    ).update(
        scan_status=FileAsset.ScanStatus.REJECTED,
        rejection_code="FILE_UPLOAD_EXPIRED",
        scan_attempt_id=None,
        scan_started_at=None,
        updated_at=timezone.now(),
    )
    if updated != 1:
        return False
    asset.refresh_from_db()
    write_tenant_audit(
        gym=gym,
        actor_user=None,
        actor_membership_id=None,
        action="FILE_UPLOAD_REJECTED",
        module="files",
        target_type="FileAsset",
        target_id=asset.id,
        request_id="file-retention",
        new_values=_safe_audit_values(
            purpose=asset.purpose,
            owner_id=str(asset.owner_id),
            rejection_code="FILE_UPLOAD_EXPIRED",
            scan_status=asset.scan_status,
            retention_status=asset.retention_status,
        ),
    )
    return True


def _drop_orphaned_canonical(
    *,
    gym: Gym,
    actor: GymMembership | None,
    store: Any,
    claim: _Claim,
    member_self: Member | None = None,
) -> None:
    """Delete a canonical object after delete won, then record the storage result.

    A claim this worker still holds stays, including a stale claim whose token
    still matches and whose retention is still quarantined. A newer scanning
    claim also keeps the object so crash recovery can read it. Delete runs only
    when retention is already ``DELETE_PENDING`` or ``DELETED`` and no scan
    claim is present. A later short transaction finishes ``DELETED`` when the
    object is gone, or returns the row to ``DELETE_PENDING`` with
    ``deleted_at`` null when storage fails, so retention can retry. That write
    does not change scan state or detach a photo.
    """

    object_key = ""
    try:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            member, asset = _lock_owned_asset(gym, claim.asset_id)
            _require_caller_scope(gym, actor, member, asset, member_self=member_self)
            if (
                asset.scan_attempt_id == claim.attempt_id
                and asset.retention_status == FileAsset.RetentionStatus.QUARANTINED
            ):
                return
            if not _deleted_without_claim(asset):
                return
            object_key = asset.object_key
    except Http404:
        return
    removed = _delete_object(store, object_key)
    _settle_orphaned_canonical(
        gym=gym,
        actor=actor,
        asset_id=claim.asset_id,
        removed=removed,
        member_self=member_self,
    )


def _settle_orphaned_canonical(
    *,
    gym: Gym,
    actor: GymMembership | None,
    asset_id: UUID,
    removed: bool,
    member_self: Member | None = None,
) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        try:
            member, asset = _lock_owned_asset(gym, asset_id)
            _require_caller_scope(gym, actor, member, asset, member_self=member_self)
        except Http404:
            return
        if not _deleted_without_claim(asset):
            return
        if removed:
            if asset.retention_status != FileAsset.RetentionStatus.DELETE_PENDING:
                return
            asset.retention_status = FileAsset.RetentionStatus.DELETED
            asset.deleted_at = timezone.now()
        elif (
            asset.retention_status == FileAsset.RetentionStatus.DELETE_PENDING
            and asset.deleted_at is None
        ):
            return
        else:
            asset.retention_status = FileAsset.RetentionStatus.DELETE_PENDING
            asset.deleted_at = None
        asset.save(update_fields=["retention_status", "deleted_at", "updated_at"])


def _deleted_without_claim(asset: FileAsset) -> bool:
    if asset.retention_status not in {
        FileAsset.RetentionStatus.DELETE_PENDING,
        FileAsset.RetentionStatus.DELETED,
    }:
        return False
    return (
        asset.scan_status != FileAsset.ScanStatus.SCANNING
        and asset.scan_attempt_id is None
        and asset.scan_started_at is None
    )


def _delete_object(store: Any, object_key: str) -> bool:
    try:
        store.delete(object_key)
    except ObjectMissing:
        return True
    except ObjectStoreUnavailable:
        return False
    return True


def _lock_owned_asset(gym: Gym, asset_id: UUID) -> tuple[Member | None, FileAsset]:
    preview = (
        FileAsset.objects.filter(gym=gym, id=asset_id)
        .only("id", "owner_type", "owner_id", "purpose")
        .first()
    )
    if preview is None:
        raise Http404
    if preview.purpose == PROFILE_PHOTO_PURPOSE:
        if preview.owner_type != MEMBER_OWNER or preview.owner_id is None:
            raise Http404
        member = _locked_member(gym, preview.owner_id)
        asset = FileAsset.objects.select_for_update().filter(gym=gym, id=asset_id).first()
        if asset is None or asset.owner_type != MEMBER_OWNER or asset.owner_id != member.id:
            raise Http404
        return member, asset
    if preview.purpose == EXPENSE_RECEIPT_PURPOSE:
        asset = FileAsset.objects.select_for_update().filter(gym=gym, id=asset_id).first()
        if (
            asset is None
            or asset.owner_type != GYM_OWNER
            or asset.owner_id != gym.id
            or asset.purpose != EXPENSE_RECEIPT_PURPOSE
        ):
            raise Http404
        return None, asset
    raise Http404


def _locked_member(gym: Gym, member_id: UUID) -> Member:
    member = Member.objects.select_for_update().filter(gym=gym, id=member_id).first()
    if member is None:
        raise Http404
    return member


def _require_member_owned_photo(asset: FileAsset, member: Member) -> None:
    if (
        asset.purpose != PROFILE_PHOTO_PURPOSE
        or asset.owner_type != MEMBER_OWNER
        or asset.owner_id != member.id
    ):
        raise Http404


def _reject_mixed_file_principal() -> NoReturn:
    raise FileRequestError(
        code="INVALID_REQUEST",
        message="The request could not be processed.",
        status_code=400,
    )


def _require_caller_asset(
    gym: Gym,
    actor: GymMembership | None,
    member: Member | None,
    asset: FileAsset,
    *,
    member_self: Member | None,
    held: bool,
) -> None:
    if (actor is None) == (member_self is None):
        _reject_mixed_file_principal()
    if member_self is not None:
        _require_member_owned_photo(asset, member_self)
        _require_members_entitlement(gym)
        return
    if actor is None:
        _reject_mixed_file_principal()
    if held:
        _require_held_asset(gym, actor, member, asset)
        return
    _require_asset_access(gym, actor, member, asset)


def _require_caller_scope(
    gym: Gym,
    actor: GymMembership | None,
    member: Member | None,
    asset: FileAsset,
    *,
    member_self: Member | None,
) -> None:
    if (actor is None) == (member_self is None):
        _reject_mixed_file_principal()
    if member_self is not None:
        _require_member_owned_photo(asset, member_self)
        _require_members_entitlement(gym)
        return
    if actor is None:
        _reject_mixed_file_principal()
    _require_scope(gym, actor, member)


def _attach_member_photo(
    gym: Gym,
    audit: RequestAudit,
    member: Member | None,
    asset: FileAsset,
) -> None:
    if member is None or not _is_clean(asset):
        return
    if asset.owner_id != member.id or asset.purpose != PROFILE_PHOTO_PURPOSE:
        return
    if member.profile_photo_asset_id == asset.id:
        return
    previous = member.profile_photo_asset_id
    member.profile_photo_asset_id = asset.id
    member.save(update_fields=["profile_photo_asset_id", "updated_at"])
    write_tenant_audit(
        gym=gym,
        actor_user=audit.actor_user,
        actor_membership_id=None,
        action="MEMBER_PROFILE_UPDATED",
        module="members",
        target_type="Member",
        target_id=member.id,
        request_id=audit.request_id,
        old_values={
            "profile_photo_asset_id": None if previous is None else str(previous),
        },
        new_values={"profile_photo_asset_id": str(asset.id)},
        ip_address=audit.ip_address,
        user_agent=audit.user_agent,
    )


def _require_scope(gym: Gym, actor: GymMembership, member: Member | None) -> None:
    if member is None:
        return
    if member.gym_id != gym.id:
        raise Http404
    from apps.trainers.selectors import gym_local_date, require_assigned_member

    require_assigned_member(
        gym_id=gym.id,
        actor=actor,
        member_id=member.id,
        as_of=gym_local_date(gym),
    )


def _require_finance_entitlement(gym: Gym) -> None:
    now = timezone.now()
    entitled = GymFeatureEntitlement.objects.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(effective_until__isnull=True) | Q(effective_until__gt=now),
        gym=gym,
        feature__code="finance",
        enabled=True,
    ).exists()
    if not entitled:
        raise FileRequestError(
            code="FEATURE_NOT_ENTITLED",
            message="The finance feature is not enabled.",
            status_code=403,
        )


def _require_held_asset(
    gym: Gym, actor: GymMembership, member: Member | None, asset: FileAsset
) -> None:
    """Recheck the caller while a scan claim is finalized.

    Profile photos keep the original scope check. Expense receipts recheck the
    finance entitlement, an expense create or update permission, and receipt
    visibility.
    """

    if asset.purpose == PROFILE_PHOTO_PURPOSE:
        _require_scope(gym, actor, member)
        return
    if asset.purpose == EXPENSE_RECEIPT_PURPOSE:
        _require_expense_receipt_access(gym, actor, asset)
        return
    raise Http404


def _require_asset_access(
    gym: Gym, actor: GymMembership, member: Member | None, asset: FileAsset
) -> None:
    if asset.purpose == PROFILE_PHOTO_PURPOSE:
        if member is None:
            raise Http404
        _require_scope(gym, actor, member)
        _require_members_entitlement(gym)
        return
    if asset.purpose == EXPENSE_RECEIPT_PURPOSE:
        _require_expense_receipt_access(gym, actor, asset)
        return
    raise Http404


def _require_expense_capability(actor: GymMembership) -> None:
    if not (
        membership_has_permission(membership=actor, code="expenses.create")
        or membership_has_permission(membership=actor, code="expenses.update")
    ):
        raise PermissionDenied()


def _require_expense_receipt_access(gym: Gym, actor: GymMembership, asset: FileAsset) -> None:
    _require_finance_entitlement(gym)
    _require_expense_capability(actor)
    _require_expense_receipt_object_scope(gym, actor, asset)


def _require_expense_receipt_object_scope(gym: Gym, actor: GymMembership, asset: FileAsset) -> None:
    """Apply expense visibility to a receipt.

    An attached receipt follows the caller's expense object scope. A protected
    Reception membership may use it only when that membership created or
    submitted the expense. An unattached receipt is limited to its uploader,
    or to a same-gym actor who holds ``expenses.create`` or ``expenses.update``
    and does not hold the protected Reception role. That non-Reception grant is
    the finance scope already used for same-gym expense drafts.
    """

    from apps.expenses.models import Expense

    reception = holds_protected_role(gym_id=gym.id, membership_id=actor.id, role_key="RECEPTIONIST")
    linked = Expense.objects.filter(gym_id=gym.id, receipt_file_asset_id=asset.id)
    if reception:
        visible = linked.filter(
            Q(created_by_membership_id=actor.id) | Q(submitted_by_membership_id=actor.id)
        )
        if visible.exists():
            return
        if not linked.exists() and asset.uploaded_by_membership_id == actor.id:
            return
        raise Http404
    _require_expense_capability(actor)


def _require_members_entitlement(gym: Gym) -> None:
    now = timezone.now()
    entitled = GymFeatureEntitlement.objects.filter(
        Q(effective_from__isnull=True) | Q(effective_from__lte=now),
        Q(effective_until__isnull=True) | Q(effective_until__gt=now),
        gym=gym,
        feature__code="members",
        enabled=True,
    ).exists()
    if not entitled:
        raise FileRequestError(
            code="FEATURE_NOT_ENTITLED",
            message="The members feature is not enabled.",
            status_code=403,
        )


def _detach_photo(
    member: Member | None,
    asset_id: UUID,
    *,
    gym: Gym | None = None,
    audit: RequestAudit | None = None,
    member_self: Member | None = None,
) -> None:
    if member is None:
        return
    if member.profile_photo_asset_id != asset_id:
        return
    member.profile_photo_asset_id = None
    member.save(update_fields=["profile_photo_asset_id", "updated_at"])
    if member_self is None or gym is None or audit is None:
        return
    write_tenant_audit(
        gym=gym,
        actor_user=audit.actor_user,
        actor_membership_id=None,
        action="MEMBER_PROFILE_UPDATED",
        module="members",
        target_type="Member",
        target_id=member.id,
        request_id=audit.request_id,
        old_values={"profile_photo_asset_id": str(asset_id)},
        new_values={"profile_photo_asset_id": None},
        ip_address=audit.ip_address,
        user_agent=audit.user_agent,
    )


def _confirmation_matches(asset: FileAsset, sha256: str, byte_size: int) -> bool:
    return asset.upload_sha256 == sha256 and asset.upload_byte_size == byte_size


def _is_clean(asset: FileAsset) -> bool:
    return (
        asset.scan_status == FileAsset.ScanStatus.CLEAN
        and asset.retention_status == FileAsset.RetentionStatus.ACTIVE
    )


def _downloadable(asset: FileAsset) -> bool:
    return _is_clean(asset) and asset.content_type in ATTACHMENT_NAMES


def _claim_age_seconds(asset_id: UUID) -> float | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - scan_started_at))
            FROM files_file_asset
            WHERE id = %s
            """,
            [asset_id],
        )
        row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _older_than_retention_window(asset_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT created_at <= CURRENT_TIMESTAMP - interval '24 hours'
            FROM files_file_asset
            WHERE id = %s
            """,
            [asset_id],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def _retry_after(age_seconds: float) -> int:
    remaining = Decimal(CLAIM_STALE_SECONDS) - Decimal(str(age_seconds))
    seconds = int(remaining.to_integral_value(rounding=ROUND_CEILING))
    return min(CLAIM_STALE_SECONDS, max(1, seconds))


def _audit(
    *,
    gym: Gym,
    audit: RequestAudit,
    action: str,
    asset: FileAsset,
    values: dict[str, object],
) -> None:
    write_tenant_audit(
        gym=gym,
        actor_user=audit.actor_user,
        actor_membership_id=audit.actor_membership_id,
        action=action,
        module="files",
        target_type="FileAsset",
        target_id=asset.id,
        request_id=audit.request_id,
        new_values=_safe_audit_values(**values),
        ip_address=audit.ip_address,
        user_agent=audit.user_agent,
    )


def _audit_completed(gym: Gym, audit: RequestAudit, asset: FileAsset) -> None:
    _audit(
        gym=gym,
        audit=audit,
        action="FILE_UPLOAD_COMPLETED",
        asset=asset,
        values={
            "purpose": asset.purpose,
            "owner_id": str(asset.owner_id),
            "content_type": asset.content_type,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "scan_status": asset.scan_status,
            "retention_status": asset.retention_status,
        },
    )


def _safe_audit_values(**values: object) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in values.items():
        if key in _SAFE_AUDIT_KEYS and value is not None:
            safe[key] = str(value) if isinstance(value, UUID) else value
    return safe


def _clean_result(asset: FileAsset) -> FileCommand:
    return FileCommand(
        status_code=200,
        message="File upload completed",
        data={
            "file_id": asset.id,
            "purpose": asset.purpose,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
            "original_name": asset.original_name,
            "content_type": asset.content_type,
            "byte_size": asset.byte_size,
            "sha256": asset.sha256,
            "scan_status": asset.scan_status,
            "retention_status": asset.retention_status,
            "attachable": _is_clean(asset),
        },
    )


def _delete_result(asset: FileAsset) -> FileCommand:
    deleted_at = None if asset.deleted_at is None else format_utc(asset.deleted_at)
    return FileCommand(
        status_code=200,
        message="File deletion recorded",
        data={
            "file_id": asset.id,
            "purpose": asset.purpose,
            "owner_type": asset.owner_type,
            "owner_id": asset.owner_id,
            "scan_status": asset.scan_status,
            "retention_status": asset.retention_status,
            "rejection_code": asset.rejection_code,
            "deleted_at": deleted_at,
        },
    )


def _rejected(asset: FileAsset) -> FileRequestError:
    return _rejected_code(asset.rejection_code or "FILE_STATE_CONFLICT")


def _rejected_code(code: str) -> FileRequestError:
    return FileRequestError(
        code=code,
        message=_REJECTION_MESSAGES.get(code, "The file was rejected."),
        status_code=422,
    )


def _confirmation_mismatch() -> FileRequestError:
    return FileRequestError(
        code="FILE_CONFIRMATION_MISMATCH",
        message="The confirmation does not match the declared upload.",
        status_code=422,
        fields={
            "sha256": ["The confirmation does not match the declared upload."],
            "byte_size": ["The confirmation does not match the declared upload."],
        },
    )


def _scan_in_progress(retry_after: int) -> FileRequestError:
    return FileRequestError(
        code="FILE_SCAN_IN_PROGRESS",
        message="A file scan is already in progress.",
        status_code=409,
        retry_after=retry_after,
    )


def _state_conflict() -> FileRequestError:
    return FileRequestError(
        code="FILE_STATE_CONFLICT",
        message="The file cannot accept this command.",
        status_code=409,
    )


def _incomplete() -> FileRequestError:
    return FileRequestError(
        code="FILE_UPLOAD_INCOMPLETE",
        message="The upload is not in storage yet.",
        status_code=409,
    )


def _store_failed() -> FileRequestError:
    return _error_code("FILE_STORE_FAILED")


def _integrity_failed() -> FileRequestError:
    return FileRequestError(
        code="FILE_INTEGRITY_FAILED",
        message="The stored file failed an integrity check.",
        status_code=409,
    )


def _not_downloadable() -> FileRequestError:
    return FileRequestError(
        code="FILE_NOT_DOWNLOADABLE",
        message="The file is not available for download.",
        status_code=409,
    )


def _delete_pending() -> FileRequestError:
    return _error_code("OBJECT_DELETE_PENDING")


def _error_code(code: str) -> FileRequestError:
    messages = {
        "FILE_STORE_FAILED": "The file store is unavailable.",
        "FILE_SCAN_UNAVAILABLE": "The file scanner is unavailable.",
        "OBJECT_DELETE_PENDING": "Object deletion is still pending.",
    }
    return FileRequestError(
        code=code,
        message=messages.get(code, "The file could not be processed."),
        status_code=503,
    )


_REJECTION_MESSAGES = {
    "FILE_INFECTED": "The file was rejected by the scanner.",
    "FILE_TYPE_MISMATCH": "The file type does not match its contents.",
    "FILE_MALFORMED": "The file could not be read as an image.",
    "FILE_INVALID_DIMENSIONS": "The image dimensions are invalid.",
    "FILE_IMAGE_TOO_LARGE": "The image exceeds the pixel limit.",
    "FILE_CHECKSUM_MISMATCH": "The uploaded bytes do not match the declaration.",
    "FILE_EMPTY": "The file is empty.",
    "FILE_TOO_LARGE": "The file is too large.",
    "FILE_UPLOAD_EXPIRED": "The upload expired before it was completed.",
}
