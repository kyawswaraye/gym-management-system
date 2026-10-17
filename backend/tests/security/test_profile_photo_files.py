from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO, StringIO
from threading import Barrier, Event
from uuid import UUID, uuid4

import jwt
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.files.exceptions import FileRequestError
from apps.files.images import canonicalize
from apps.files.models import FileAsset
from apps.files.services import (
    RequestAudit,
    complete_upload,
    delete_upload,
    expire_quarantined_uploads,
)
from apps.members.models import Member
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from apps.users.models import User
from apps.users.services import create_platform_session
from tests.file_fakes import MemoryScanner, MemoryStore, use_fakes
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    PASSWORD,
    add_staff,
    auth_headers,
    jwt_settings,
    provision,
    staff_token,
)

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

_FAKES = override_settings(
    **jwt_settings(),
    FILE_OBJECT_STORE="tests.file_fakes.build_store",
    FILE_SCANNER="tests.file_fakes.build_scanner",
    FILE_IMAGE_PROCESSOR="tests.file_fakes.build_processor",
)


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (8, 16, 24)).save(buffer, format="PNG")
    return buffer.getvalue()


def _client() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _member_body() -> dict[str, str]:
    return {"first_name": "Ada", "last_name": "Lovelace", "join_date": "2026-10-01"}


def _intent(owner_id: str, body: bytes) -> dict[str, object]:
    return {
        "original_name": "portrait.png",
        "content_type": "image/png",
        "byte_size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "purpose": "PROFILE_PHOTO",
        "owner_type": "MEMBER",
        "owner_id": owner_id,
    }


def _audit(actor: GymMembership) -> RequestAudit:
    return RequestAudit(
        actor_user=actor.user,
        actor_membership_id=actor.id,
        request_id="req_profile_photo",
        ip_address=None,
        user_agent="profile-photo-test",
    )


def _platform_access(email: str) -> tuple[APIClient, str]:
    admin = User.objects.create_superuser(email=email, password=PASSWORD)
    return _client(), create_platform_session(user=admin).access_token


def _tenant() -> tuple[APIClient, dict[str, str], Gym, GymMembership, str]:
    client, token = _platform_access(f"platform-{uuid4().hex[:8]}@example.com")
    created = provision(
        client,
        token,
        code=f"PHOTO{uuid4().hex[:8].upper()}",
        owner_email=f"owner-{uuid4().hex[:8]}@example.com",
    )
    gym_payload = created["gym"]
    owner_payload = created["owner"]
    assert isinstance(gym_payload, dict)
    assert isinstance(owner_payload, dict)
    membership_payload = owner_payload["gym_membership"]
    assert isinstance(membership_payload, dict)
    gym = Gym.objects.get(id=gym_payload["id"])
    owner = GymMembership.objects.select_related("user").get(id=membership_payload["id"])
    api = _client()
    headers = auth_headers(staff_token(owner))
    member = api.post("/api/v1/members/", _member_body(), format="json", **headers)
    assert member.status_code == 201, member.json()
    return api, headers, gym, owner, member.json()["data"]["id"]


def _asset(gym: Gym, file_id: str) -> FileAsset:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return FileAsset.objects.get(gym=gym, id=file_id)


def _stage_upload(
    api: APIClient,
    headers: dict[str, str],
    gym: Gym,
    member_id: str,
    store: MemoryStore,
    image: bytes,
) -> FileAsset:
    created = api.post(
        "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
    )
    assert created.status_code == 201, created.json()
    asset = _asset(gym, created.json()["data"]["file_id"])
    from apps.files.storage import StoredObject

    store.objects[asset.object_key] = StoredObject(body=image, metadata={})
    return asset


def _member_photo(gym: Gym, member_id: str) -> UUID | None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.get(gym=gym, id=member_id).profile_photo_asset_id


def _attach_other_photo(gym: Gym, member_id: str, source: FileAsset) -> UUID:
    replacement_id = uuid4()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        FileAsset.objects.create(
            id=replacement_id,
            gym=gym,
            object_key=f"{gym.id}/{uuid4()}",
            original_name=source.original_name,
            content_type=source.upload_content_type or source.content_type,
            byte_size=int(source.upload_byte_size or source.byte_size),
            sha256=source.upload_sha256 or source.sha256,
            upload_content_type=source.upload_content_type,
            upload_byte_size=source.upload_byte_size,
            upload_sha256=source.upload_sha256,
            purpose=source.purpose,
            owner_type=source.owner_type,
            owner_id=source.owner_id,
            scan_status=FileAsset.ScanStatus.CLEAN,
            retention_status=FileAsset.RetentionStatus.ACTIVE,
            uploaded_by_membership_id=source.uploaded_by_membership_id,
        )
        member = Member.objects.select_for_update().get(gym=gym, id=member_id)
        member.profile_photo_asset_id = replacement_id
        member.save(update_fields=["profile_photo_asset_id", "updated_at"])
    return replacement_id


def _complete(gym: Gym, owner: GymMembership, store: MemoryStore, asset: FileAsset) -> object:
    try:
        return complete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            scanner=MemoryScanner(),
            canonicalize=canonicalize,
            asset_id=asset.id,
            sha256=asset.upload_sha256 or "",
            byte_size=int(asset.upload_byte_size or 0),
        )
    except FileRequestError as exc:
        return exc


@_FAKES
def test_profile_photo_permissions_validation_and_scope() -> None:
    use_fakes(processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    missing = api.post("/api/v1/files/upload-intents/", _intent(member_id, image), format="json")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "NOT_AUTHENTICATED"

    _client_token, platform = _platform_access(f"again-{uuid4().hex[:8]}@example.com")
    denied_platform = api.post(
        "/api/v1/files/upload-intents/",
        _intent(member_id, image),
        format="json",
        **auth_headers(platform),
    )
    assert denied_platform.status_code == 403
    assert denied_platform.json()["error"]["code"] == "PERMISSION_DENIED"
    member_token = jwt.encode(
        {
            "aud": settings.JWT_MEMBER_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "sub": str(uuid4()),
            "exp": int(datetime(2026, 10, 18, tzinfo=UTC).timestamp()),
        },
        settings.JWT_PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": settings.JWT_KEY_ID},
    )
    denied_member = api.post(
        "/api/v1/files/upload-intents/",
        _intent(member_id, image),
        format="json",
        **auth_headers(member_token),
    )
    assert denied_member.status_code == 403

    accountant = add_staff(gym, email=f"acct-{uuid4().hex[:8]}@example.com", role_key="ACCOUNTANT")
    trainer = add_staff(gym, email=f"trainer-{uuid4().hex[:8]}@example.com", role_key="TRAINER")
    forbidden = api.post(
        "/api/v1/files/upload-intents/",
        _intent(member_id, image),
        format="json",
        **auth_headers(staff_token(accountant)),
    )
    assert forbidden.status_code == 403
    hidden = api.post(
        "/api/v1/files/upload-intents/",
        _intent(member_id, image),
        format="json",
        **auth_headers(staff_token(trainer)),
    )
    assert hidden.status_code == 404
    unknown = api.post(
        "/api/v1/files/upload-intents/",
        _intent(str(uuid4()), image),
        format="json",
        **headers,
    )
    assert unknown.status_code == 404
    queried = api.post(
        "/api/v1/files/upload-intents/?gym_id=1",
        _intent(member_id, image),
        format="json",
        **headers,
    )
    assert queried.status_code == 400
    assert queried.json()["error"]["code"] == "INVALID_REQUEST"
    unsupported = api.post(
        "/api/v1/files/upload-intents/",
        {**_intent(member_id, image), "purpose": "DOCUMENT"},
        format="json",
        **headers,
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "UNSUPPORTED_FILE_PURPOSE"
    assert FileAsset.objects.count() == 0

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="members").update(enabled=False)
    gated_missing = api.post(
        "/api/v1/files/upload-intents/",
        _intent(str(uuid4()), image),
        format="json",
        **headers,
    )
    gated_known = api.post(
        "/api/v1/files/upload-intents/",
        _intent(member_id, image),
        format="json",
        **headers,
    )
    assert gated_missing.status_code == 404
    assert gated_known.status_code == 403
    assert gated_known.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="members").update(enabled=True)
    _ = owner


@_FAKES
def test_profile_photo_complete_download_and_delete() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    created = api.post(
        "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
    )
    assert created.status_code == 201, created.json()
    assert store.atomic_depth_during_presign == [1]
    payload = created.json()["data"]
    rendered = created.content.decode()
    assert payload["upload"]["url"] == "https://files.example.com/gym-private"
    assert payload["upload"]["fields"]["success_action_status"] == "204"
    assert "object_key" not in rendered
    assert "local-secret" not in rendered
    asset = _asset(gym, payload["file_id"])
    assert asset.scan_status == FileAsset.ScanStatus.PENDING
    assert asset.retention_status == FileAsset.RetentionStatus.QUARANTINED
    assert asset.upload_sha256 == hashlib.sha256(image).hexdigest()
    assert asset.sha256 == asset.upload_sha256
    from apps.files.storage import StoredObject

    store.objects[asset.object_key] = StoredObject(body=image, metadata={})
    completed = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert completed.status_code == 200, completed.json()
    assert completed.json()["data"]["attachable"] is True
    assert completed.json()["data"]["scan_status"] == "CLEAN"
    assert "object_key" not in completed.content.decode()
    replay = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert replay.status_code == 200, replay.json()
    assert store.puts == [asset.object_key]
    trainer = add_staff(gym, email=f"trainer-{uuid4().hex[:8]}@example.com", role_key="TRAINER")
    trainer_headers = auth_headers(staff_token(trainer))
    trainer_complete = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **trainer_headers,
    )
    trainer_download = api.get(f"/api/v1/files/{asset.id}/download-url/", **trainer_headers)
    trainer_delete = api.delete(f"/api/v1/files/{asset.id}/", **trainer_headers)
    assert trainer_complete.status_code == 404
    assert trainer_download.status_code == 404
    assert trainer_delete.status_code == 403
    assert trainer_delete.json()["error"]["code"] == "PERMISSION_DENIED"
    attached = api.patch(
        f"/api/v1/members/{member_id}/",
        {"profile_photo_asset_id": str(asset.id)},
        format="json",
        **headers,
    )
    assert attached.status_code == 200, attached.json()
    download = api.get(f"/api/v1/files/{asset.id}/download-url/", **headers)
    assert download.status_code == 200, download.json()
    url = download.json()["data"]["url"]
    assert url.startswith("https://files.example.com/")
    assert "profile-photo.png" in url
    assert "response-content-disposition=inline%3B%20filename%3D%22profile-photo.png%22" in url
    assert "attachment" not in url
    assert "private%2C%20no-store" in url
    assert "X-Amz-Expires=60" in url
    assert "minio" not in url
    stored = store.objects[asset.object_key]
    store.objects[asset.object_key] = StoredObject(body=stored.body, metadata={})
    mismatched = api.get(f"/api/v1/files/{asset.id}/download-url/", **headers)
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "FILE_INTEGRITY_FAILED"
    store.fail = "head-stat"
    invalid_length = api.get(f"/api/v1/files/{asset.id}/download-url/", **headers)
    assert invalid_length.status_code == 409
    assert invalid_length.json()["error"]["code"] == "FILE_INTEGRITY_FAILED"
    store.fail = "head"
    unavailable = api.get(f"/api/v1/files/{asset.id}/download-url/", **headers)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "FILE_STORE_FAILED"
    store.fail = ""
    store.objects[asset.object_key] = stored
    removed = api.delete(f"/api/v1/files/{asset.id}/", **headers)
    assert removed.status_code == 200, removed.json()
    assert removed.json()["data"]["retention_status"] == "DELETED"
    deleted_at = removed.json()["data"]["deleted_at"]
    repeated = api.delete(f"/api/v1/files/{asset.id}/", **headers)
    assert repeated.status_code == 200, repeated.json()
    assert repeated.json()["data"]["deleted_at"] == deleted_at
    member = api.get(f"/api/v1/members/{member_id}/", **headers)
    assert member.json()["data"]["profile_photo_asset_id"] is None
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        rows = list(AuditLog.objects.filter(gym=gym))
    actions = {row.action for row in rows}
    rendered_audit = " ".join(str(row.new_values) for row in rows)
    assert {
        "FILE_UPLOAD_INTENT",
        "FILE_UPLOAD_COMPLETED",
        "FILE_DOWNLOAD_URL_ISSUED",
        "FILE_DELETE_REQUESTED",
    } <= actions
    assert asset.object_key not in rendered_audit
    _ = owner


@_FAKES
def test_profile_photo_failures_and_stale_claim_recovery() -> None:
    store = MemoryStore()
    scanner = MemoryScanner()
    use_fakes(store=store, scanner=scanner, processor=canonicalize)
    api, headers, gym, _owner, member_id = _tenant()
    image = _png()

    def intend() -> FileAsset:
        response = api.post(
            "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
        )
        assert response.status_code == 201, response.json()
        asset = _asset(gym, response.json()["data"]["file_id"])
        from apps.files.storage import StoredObject

        store.objects[asset.object_key] = StoredObject(body=image, metadata={})
        return asset

    asset = intend()
    mismatch = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": "cd" * 32, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert mismatch.status_code == 422, mismatch.json()
    assert mismatch.json()["error"]["code"] == "FILE_CONFIRMATION_MISMATCH"
    scanner.fail = True
    unavailable = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "FILE_SCAN_UNAVAILABLE"
    scanner.fail = False
    store.objects.pop(asset.object_key)
    incomplete = api.post(
        f"/api/v1/files/{asset.id}/complete/",
        {"sha256": asset.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert incomplete.status_code == 409
    assert incomplete.json()["error"]["code"] == "FILE_UPLOAD_INCOMPLETE"
    assert "Retry-After" not in incomplete
    refreshed = _asset(gym, str(asset.id))
    assert refreshed.scan_status == FileAsset.ScanStatus.PENDING

    junk = b"not-a-png"
    junk_intent = api.post(
        "/api/v1/files/upload-intents/", _intent(member_id, junk), format="json", **headers
    )
    assert junk_intent.status_code == 201, junk_intent.json()
    bad = _asset(gym, junk_intent.json()["data"]["file_id"])
    from apps.files.storage import StoredObject

    store.objects[bad.object_key] = StoredObject(body=junk, metadata={})
    malformed = api.post(
        f"/api/v1/files/{bad.id}/complete/",
        {"sha256": hashlib.sha256(junk).hexdigest(), "byte_size": len(junk)},
        format="json",
        **headers,
    )
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "FILE_MALFORMED"
    rejected = _asset(gym, str(bad.id))
    assert rejected.scan_status == FileAsset.ScanStatus.REJECTED
    assert bad.object_key not in store.objects

    claimed = intend()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        FileAsset.objects.filter(id=claimed.id).update(
            scan_status=FileAsset.ScanStatus.SCANNING,
            scan_attempt_id=uuid4(),
            scan_started_at=timezone.now(),
        )
    busy = api.post(
        f"/api/v1/files/{claimed.id}/complete/",
        {"sha256": claimed.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "FILE_SCAN_IN_PROGRESS"
    assert busy["Retry-After"].isdigit()
    assert 1 <= int(busy["Retry-After"]) <= 120
    assert "Retry-After" not in busy.json()["error"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        FileAsset.objects.filter(id=claimed.id).update(
            scan_started_at=timezone.now() - timezone.timedelta(seconds=180)
        )
    recovered = api.post(
        f"/api/v1/files/{claimed.id}/complete/",
        {"sha256": claimed.upload_sha256, "byte_size": len(image)},
        format="json",
        **headers,
    )
    assert recovered.status_code == 200, recovered.json()


@_FAKES
def test_delete_invalidates_an_in_flight_claim() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    created = api.post(
        "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
    )
    asset = _asset(gym, created.json()["data"]["file_id"])
    from apps.files.storage import StoredObject

    store.objects[asset.object_key] = StoredObject(body=image, metadata={})
    entered = Barrier(2)
    release = Event()

    def pause() -> None:
        if len(store.atomic_during_read) == 1:
            entered.wait(timeout=8)
            release.wait(timeout=8)

    store.on_read = pause

    def finish() -> object:
        connections.close_all()
        try:
            return complete_upload(
                gym=gym,
                actor=owner,
                audit=_audit(owner),
                store=store,
                scanner=MemoryScanner(),
                canonicalize=canonicalize,
                asset_id=asset.id,
                sha256=asset.upload_sha256 or "",
                byte_size=int(asset.upload_byte_size or 0),
            )
        except FileRequestError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(finish)
        entered.wait(timeout=8)
        removed = delete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            asset_id=asset.id,
        )
        release.set()
        outcome = worker.result(timeout=8)
    assert removed.status_code == 200
    assert isinstance(outcome, FileRequestError)
    assert outcome.status_code == 409
    final = _asset(gym, str(asset.id))
    assert final.retention_status == FileAsset.RetentionStatus.DELETED
    assert final.scan_status != FileAsset.ScanStatus.CLEAN
    assert store.puts == []


@_FAKES
def test_delete_during_canonical_put_does_not_resurrect_the_object() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()

    def upload() -> FileAsset:
        created = api.post(
            "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
        )
        assert created.status_code == 201, created.json()
        asset = _asset(gym, created.json()["data"]["file_id"])
        from apps.files.storage import StoredObject

        store.objects[asset.object_key] = StoredObject(body=image, metadata={})
        return asset

    def finish(asset: FileAsset) -> None:
        try:
            complete_upload(
                gym=gym,
                actor=owner,
                audit=_audit(owner),
                store=store,
                scanner=MemoryScanner(),
                canonicalize=canonicalize,
                asset_id=asset.id,
                sha256=asset.upload_sha256 or "",
                byte_size=int(asset.upload_byte_size or 0),
            )
        except FileRequestError as exc:
            assert exc.status_code == 409
            assert exc.detail.code == "FILE_STATE_CONFLICT"
            return
        raise AssertionError("finalize marked a deleted upload clean")

    deleted = upload()
    deleted_key = deleted.object_key
    seen: dict[str, object] = {}

    def delete_before_put() -> None:
        removed = delete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            asset_id=deleted.id,
        )
        assert removed.status_code == 200
        assert deleted_key not in store.objects
        seen["deletes_before_put"] = len(store.deletes)
        row = _asset(gym, str(deleted.id))
        assert row.retention_status == FileAsset.RetentionStatus.DELETED
        assert row.scan_status != FileAsset.ScanStatus.CLEAN

    store.puts.clear()
    store.deletes.clear()
    store.before_put = delete_before_put
    finish(deleted)
    assert store.puts == [deleted_key]
    assert len(store.deletes) > int(seen["deletes_before_put"])
    assert deleted_key not in store.objects
    deleted_row = _asset(gym, str(deleted.id))
    assert deleted_row.retention_status == FileAsset.RetentionStatus.DELETED
    assert deleted_row.deleted_at is not None
    assert deleted_row.scan_status != FileAsset.ScanStatus.CLEAN
    puts_after_delete = list(store.puts)
    with pytest.raises(FileRequestError) as late_deleted:
        complete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            scanner=MemoryScanner(),
            canonicalize=canonicalize,
            asset_id=deleted.id,
            sha256=deleted.upload_sha256 or "",
            byte_size=int(deleted.upload_byte_size or 0),
        )
    assert late_deleted.value.status_code == 409
    assert late_deleted.value.detail.code == "FILE_STATE_CONFLICT"
    assert store.puts == puts_after_delete
    assert deleted_key not in store.objects
    untouched = _asset(gym, str(deleted.id))
    assert untouched.retention_status == deleted_row.retention_status
    assert untouched.deleted_at == deleted_row.deleted_at
    assert untouched.scan_status == deleted_row.scan_status

    pending = upload()
    pending_key = pending.object_key

    def fail_delete_before_put() -> None:
        store.fail = "delete"
        try:
            delete_upload(
                gym=gym,
                actor=owner,
                audit=_audit(owner),
                store=store,
                asset_id=pending.id,
            )
        except FileRequestError as exc:
            assert exc.status_code == 503
            assert exc.detail.code == "OBJECT_DELETE_PENDING"
        else:
            raise AssertionError("storage failure still deleted the row")
        finally:
            store.fail = ""
        assert pending_key in store.objects
        row = _asset(gym, str(pending.id))
        assert row.retention_status == FileAsset.RetentionStatus.DELETE_PENDING
        assert row.deleted_at is None
        seen["deletes_before_pending_put"] = len(store.deletes)

    store.puts.clear()
    store.deletes.clear()
    store.before_put = fail_delete_before_put
    finish(pending)
    assert store.puts == [pending_key]
    assert len(store.deletes) > int(seen["deletes_before_pending_put"])
    assert pending_key not in store.objects
    pending_row = _asset(gym, str(pending.id))
    assert pending_row.retention_status == FileAsset.RetentionStatus.DELETED
    assert pending_row.deleted_at is not None
    assert pending_row.scan_status != FileAsset.ScanStatus.CLEAN
    puts_after_pending = list(store.puts)
    with pytest.raises(FileRequestError) as late_pending:
        complete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            scanner=MemoryScanner(),
            canonicalize=canonicalize,
            asset_id=pending.id,
            sha256=pending.upload_sha256 or "",
            byte_size=int(pending.upload_byte_size or 0),
        )
    assert late_pending.value.status_code == 409
    assert late_pending.value.detail.code == "FILE_STATE_CONFLICT"
    assert store.puts == puts_after_pending
    assert pending_key not in store.objects
    pending_again = _asset(gym, str(pending.id))
    assert pending_again.retention_status == FileAsset.RetentionStatus.DELETED
    assert pending_again.deleted_at == pending_row.deleted_at
    assert pending_again.scan_status == pending_row.scan_status


@_FAKES
def test_late_canonical_put_cleanup_preserves_deleted() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    asset = _stage_upload(api, headers, gym, member_id, store, image)
    key = asset.object_key
    seen: dict[str, object] = {}

    def delete_before_put() -> None:
        removed = delete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            asset_id=asset.id,
        )
        assert removed.status_code == 200
        assert key not in store.objects
        row = _asset(gym, str(asset.id))
        assert row.retention_status == FileAsset.RetentionStatus.DELETED
        assert row.deleted_at is not None
        assert row.scan_status == FileAsset.ScanStatus.ERROR
        assert row.scan_attempt_id is None
        seen["deleted_at"] = row.deleted_at
        seen["scan_status"] = row.scan_status
        seen["photo"] = _attach_other_photo(gym, member_id, row)

    store.before_put = delete_before_put
    outcome = _complete(gym, owner, store, asset)
    assert isinstance(outcome, FileRequestError)
    assert outcome.status_code == 409
    assert outcome.detail.code == "FILE_STATE_CONFLICT"
    assert key not in store.objects
    final = _asset(gym, str(asset.id))
    assert final.retention_status == FileAsset.RetentionStatus.DELETED
    assert final.deleted_at == seen["deleted_at"]
    assert final.scan_status == seen["scan_status"]
    assert final.scan_attempt_id is None
    assert final.scan_started_at is None
    assert _member_photo(gym, member_id) == seen["photo"]


@_FAKES
def test_late_canonical_put_cleanup_failure_is_retried_to_deleted() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    asset = _stage_upload(api, headers, gym, member_id, store, image)
    key = asset.object_key
    seen: dict[str, object] = {}

    def delete_before_put() -> None:
        removed = delete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            asset_id=asset.id,
        )
        assert removed.status_code == 200
        assert key not in store.objects
        row = _asset(gym, str(asset.id))
        assert row.retention_status == FileAsset.RetentionStatus.DELETED
        assert row.scan_status == FileAsset.ScanStatus.ERROR
        seen["scan_status"] = row.scan_status
        seen["photo"] = _attach_other_photo(gym, member_id, row)
        store.fail = "delete"

    store.before_put = delete_before_put
    outcome = _complete(gym, owner, store, asset)
    store.fail = ""
    assert isinstance(outcome, FileRequestError)
    assert outcome.status_code == 409
    assert outcome.detail.code == "FILE_STATE_CONFLICT"
    assert key in store.objects
    pending = _asset(gym, str(asset.id))
    assert pending.retention_status == FileAsset.RetentionStatus.DELETE_PENDING
    assert pending.deleted_at is None
    assert pending.scan_status == seen["scan_status"]
    assert pending.scan_attempt_id is None
    assert pending.scan_started_at is None
    assert _member_photo(gym, member_id) == seen["photo"]
    output = StringIO()
    call_command("expire_profile_photo_uploads", gym_code=gym.code, stdout=output)
    assert "Processed 1 profile photo row(s)." in output.getvalue()
    assert key not in store.objects
    finished = _asset(gym, str(asset.id))
    assert finished.retention_status == FileAsset.RetentionStatus.DELETED
    assert finished.deleted_at is not None
    assert finished.scan_status == seen["scan_status"]
    assert finished.scan_attempt_id is None
    assert finished.scan_started_at is None
    assert _member_photo(gym, member_id) == seen["photo"]


@_FAKES
def test_current_or_newer_claim_is_not_deleted_by_orphan_cleanup() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    current = _stage_upload(api, headers, gym, member_id, store, image)
    store.puts.clear()
    store.deletes.clear()
    completed = _complete(gym, owner, store, current)
    assert getattr(completed, "status_code", None) == 200
    assert store.puts == [current.object_key]
    assert store.deletes == []
    assert current.object_key in store.objects
    current_row = _asset(gym, str(current.id))
    assert current_row.scan_status == FileAsset.ScanStatus.CLEAN
    assert current_row.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert current_row.scan_attempt_id is None

    newer = _stage_upload(api, headers, gym, member_id, store, image)
    replacement = uuid4()

    def replace_claim() -> None:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            updated = FileAsset.objects.filter(gym=gym, id=newer.id).update(
                scan_status=FileAsset.ScanStatus.SCANNING,
                scan_attempt_id=replacement,
                scan_started_at=timezone.now(),
                updated_at=timezone.now(),
            )
        assert updated == 1

    store.puts.clear()
    store.deletes.clear()
    store.before_put = replace_claim
    replaced = _complete(gym, owner, store, newer)
    assert isinstance(replaced, FileRequestError)
    assert replaced.status_code == 409
    assert replaced.detail.code == "FILE_SCAN_IN_PROGRESS"
    assert store.deletes == []
    assert newer.object_key in store.objects
    newer_row = _asset(gym, str(newer.id))
    assert newer_row.scan_status == FileAsset.ScanStatus.SCANNING
    assert newer_row.scan_attempt_id == replacement
    assert newer_row.scan_started_at is not None
    assert newer_row.retention_status == FileAsset.RetentionStatus.QUARANTINED

    lost = _stage_upload(api, headers, gym, member_id, store, image)
    revived = uuid4()
    seen: dict[str, int] = {}

    def delete_then_revive_on_cleanup_failure() -> None:
        removed = delete_upload(
            gym=gym,
            actor=owner,
            audit=_audit(owner),
            store=store,
            asset_id=lost.id,
        )
        assert removed.status_code == 200

        def revive() -> None:
            store.fail = ""
            seen["deletes_before_claim"] = len(store.deletes)
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                updated = FileAsset.objects.filter(gym=gym, id=lost.id).update(
                    retention_status=FileAsset.RetentionStatus.QUARANTINED,
                    deleted_at=None,
                    scan_status=FileAsset.ScanStatus.SCANNING,
                    scan_attempt_id=revived,
                    scan_started_at=timezone.now(),
                    updated_at=timezone.now(),
                )
            assert updated == 1

        store.fail = "delete"
        store.on_failed_delete = revive

    store.puts.clear()
    store.deletes.clear()
    store.before_put = delete_then_revive_on_cleanup_failure
    conflict = _complete(gym, owner, store, lost)
    assert isinstance(conflict, FileRequestError)
    assert conflict.status_code == 409
    assert conflict.detail.code == "FILE_SCAN_IN_PROGRESS"
    assert len(store.deletes) == seen["deletes_before_claim"]
    assert lost.object_key in store.objects
    revived_row = _asset(gym, str(lost.id))
    assert revived_row.retention_status == FileAsset.RetentionStatus.QUARANTINED
    assert revived_row.deleted_at is None
    assert revived_row.scan_status == FileAsset.ScanStatus.SCANNING
    assert revived_row.scan_attempt_id == revived
    assert revived_row.scan_started_at is not None


@_FAKES
def test_stale_owned_claim_keeps_the_canonical_object() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    image = _png()
    created = api.post(
        "/api/v1/files/upload-intents/", _intent(member_id, image), format="json", **headers
    )
    assert created.status_code == 201, created.json()
    asset = _asset(gym, created.json()["data"]["file_id"])
    canonical = canonicalize(image, "image/png")
    store.put_canonical(asset.object_key, canonical.body, content_type="image/png")
    store.puts.clear()
    store.deletes.clear()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        FileAsset.objects.filter(id=asset.id).update(
            scan_status=FileAsset.ScanStatus.SCANNING,
            scan_attempt_id=uuid4(),
            scan_started_at=timezone.now() - timezone.timedelta(seconds=180),
        )
    recovered = complete_upload(
        gym=gym,
        actor=owner,
        audit=_audit(owner),
        store=store,
        scanner=MemoryScanner(),
        canonicalize=canonicalize,
        asset_id=asset.id,
        sha256=asset.upload_sha256 or "",
        byte_size=int(asset.upload_byte_size or 0),
    )
    assert recovered.status_code == 200
    final = _asset(gym, str(asset.id))
    assert final.scan_status == FileAsset.ScanStatus.CLEAN
    assert final.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert store.puts == []
    assert store.deletes == []
    assert store.objects[asset.object_key].body == canonical.body


@_FAKES
def test_profile_photo_constraints_and_immutability() -> None:
    _api, _headers, gym, owner, member_id = _tenant()
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO files_file_asset (
                        id, gym_id, object_key, original_name, content_type, byte_size, sha256,
                        purpose, scan_status, retention_status, uploaded_by_membership_id,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, 'portrait.png', 'image/png', 12, %s,
                              'PROFILE_PHOTO', 'PENDING', 'QUARANTINED', %s, NOW(), NOW())
                    """,
                    [uuid4(), gym.id, f"{gym.id}/{uuid4()}", "ab" * 32, owner.id],
                )
    legal = FileAsset(
        gym=gym,
        object_key=f"{gym.id}/{uuid4()}",
        original_name="portrait.png",
        content_type="image/png",
        byte_size=12,
        sha256="ab" * 32,
        upload_content_type="image/png",
        upload_byte_size=12,
        upload_sha256="ab" * 32,
        purpose="PROFILE_PHOTO",
        owner_type="MEMBER",
        owner_id=UUID(member_id),
        scan_status=FileAsset.ScanStatus.PENDING,
        retention_status=FileAsset.RetentionStatus.QUARANTINED,
        uploaded_by_membership_id=owner.id,
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        legal.save()
        with pytest.raises(DatabaseError), transaction.atomic():
            FileAsset.objects.filter(id=legal.id).update(original_name="changed.png")
    with connection.cursor() as cursor:
        cursor.execute("SELECT tgname FROM pg_trigger WHERE tgname = 'file_asset_immutable_guard'")
        assert cursor.fetchone() == ("file_asset_immutable_guard",)


def _backdate(gym: Gym, asset_id: UUID, *, hours: int = 25) -> None:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        updated = FileAsset.objects.filter(gym=gym, id=asset_id).update(
            created_at=timezone.now() - timezone.timedelta(hours=hours)
        )
    assert updated == 1


def _clean_photo(
    api: APIClient,
    headers: dict[str, str],
    gym: Gym,
    owner: GymMembership,
    member_id: str,
    store: MemoryStore,
) -> FileAsset:
    asset = _stage_upload(api, headers, gym, member_id, store, _png())
    outcome = _complete(gym, owner, store, asset)
    assert getattr(outcome, "status_code", None) == 200
    return _asset(gym, str(asset.id))


@_FAKES
def test_delete_of_replaced_photo_keeps_the_new_pointer() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    old = _clean_photo(api, headers, gym, owner, member_id, store)
    new = _clean_photo(api, headers, gym, owner, member_id, store)
    attached = api.patch(
        f"/api/v1/members/{member_id}/",
        {"profile_photo_asset_id": str(new.id)},
        format="json",
        **headers,
    )
    assert attached.status_code == 200, attached.json()
    removed = api.delete(f"/api/v1/files/{old.id}/", **headers)
    assert removed.status_code == 200, removed.json()
    assert removed.json()["data"]["retention_status"] == "DELETED"
    member = api.get(f"/api/v1/members/{member_id}/", **headers)
    assert member.json()["data"]["profile_photo_asset_id"] == str(new.id)
    current = _asset(gym, str(new.id))
    assert current.scan_status == FileAsset.ScanStatus.CLEAN
    assert current.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert new.object_key in store.objects
    assert old.object_key not in store.objects
    _ = owner


@_FAKES
def test_retention_removes_an_unattached_clean_profile_photo() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    abandoned = _clean_photo(api, headers, gym, owner, member_id, store)
    fresh = _clean_photo(api, headers, gym, owner, member_id, store)
    _backdate(gym, abandoned.id)
    output = StringIO()
    call_command("expire_profile_photo_uploads", gym_code=gym.code, stdout=output)
    assert "Processed 1 profile photo row(s)." in output.getvalue()
    removed = _asset(gym, str(abandoned.id))
    assert removed.scan_status == FileAsset.ScanStatus.CLEAN
    assert removed.retention_status == FileAsset.RetentionStatus.DELETED
    assert removed.deleted_at is not None
    assert abandoned.object_key not in store.objects
    kept = _asset(gym, str(fresh.id))
    assert kept.scan_status == FileAsset.ScanStatus.CLEAN
    assert kept.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert fresh.object_key in store.objects
    assert _member_photo(gym, member_id) is None
    _ = owner


@_FAKES
def test_retention_skips_an_attached_clean_profile_photo() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    current = _clean_photo(api, headers, gym, owner, member_id, store)
    attached = api.patch(
        f"/api/v1/members/{member_id}/",
        {"profile_photo_asset_id": str(current.id)},
        format="json",
        **headers,
    )
    assert attached.status_code == 200, attached.json()
    _backdate(gym, current.id)
    output = StringIO()
    call_command("expire_profile_photo_uploads", gym_code=gym.code, stdout=output)
    assert "Processed 0 profile photo row(s)." in output.getvalue()
    row = _asset(gym, str(current.id))
    assert row.scan_status == FileAsset.ScanStatus.CLEAN
    assert row.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert row.deleted_at is None
    assert current.object_key in store.objects
    assert _member_photo(gym, member_id) == current.id
    _ = owner


@_FAKES
def test_retention_rechecks_attachment_before_deleting_a_clean_photo() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    abandoned = _clean_photo(api, headers, gym, owner, member_id, store)
    _backdate(gym, abandoned.id)

    def attach(asset_id: UUID) -> None:
        with transaction.atomic():
            set_gym_context(gym_id=gym.id)
            member = Member.objects.select_for_update().get(gym=gym, id=member_id)
            member.profile_photo_asset_id = asset_id
            member.save(update_fields=["profile_photo_asset_id", "updated_at"])

    processed = expire_quarantined_uploads(gym=gym, store=store, before_claim=attach)
    assert processed == 0
    row = _asset(gym, str(abandoned.id))
    assert row.scan_status == FileAsset.ScanStatus.CLEAN
    assert row.retention_status == FileAsset.RetentionStatus.ACTIVE
    assert row.deleted_at is None
    assert abandoned.object_key in store.objects
    assert abandoned.object_key not in store.deletes
    assert _member_photo(gym, member_id) == abandoned.id
    _ = (api, headers, owner)


@_FAKES
def test_retention_retries_a_failed_delete_of_an_unattached_clean_photo() -> None:
    store = MemoryStore()
    use_fakes(store=store, processor=canonicalize)
    api, headers, gym, owner, member_id = _tenant()
    abandoned = _clean_photo(api, headers, gym, owner, member_id, store)
    _backdate(gym, abandoned.id)
    store.fail = "delete"
    output = StringIO()
    call_command("expire_profile_photo_uploads", gym_code=gym.code, stdout=output)
    assert "Processed 1 profile photo row(s)." in output.getvalue()
    pending = _asset(gym, str(abandoned.id))
    assert pending.scan_status == FileAsset.ScanStatus.CLEAN
    assert pending.retention_status == FileAsset.RetentionStatus.DELETE_PENDING
    assert pending.deleted_at is None
    assert abandoned.object_key in store.objects
    assert _member_photo(gym, member_id) is None
    store.fail = ""
    retried = StringIO()
    call_command("expire_profile_photo_uploads", gym_code=gym.code, stdout=retried)
    assert "Processed 1 profile photo row(s)." in retried.getvalue()
    finished = _asset(gym, str(abandoned.id))
    assert finished.scan_status == FileAsset.ScanStatus.CLEAN
    assert finished.retention_status == FileAsset.RetentionStatus.DELETED
    assert finished.deleted_at is not None
    assert abandoned.object_key not in store.objects
    assert _member_photo(gym, member_id) is None
    _ = (api, headers, owner)
