"""Member-self API isolation, paging, booking, and profile-photo ownership."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import connections, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from PIL import Image
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.classes.booking_services import create_member_class_booking
from apps.classes.exceptions import ClassFull, ScheduleConflict
from apps.classes.models import ClassOccurrence
from apps.common.db.tenant_context import set_gym_context
from apps.files.models import FileAsset
from apps.files.storage import StoredObject
from apps.members.models import Member, Payment
from apps.tenants.models import Gym, GymFeatureEntitlement, GymMembership
from apps.users.models import User
from tests.file_fakes import CURRENT, MemoryStore, use_fakes
from tests.security.test_bookings import _grant, _key, _package
from tests.security.test_class_schedules import _profile, _schedule_body, _type_body
from tests.security.test_member_payment_history import _payment
from tests.security.test_member_scan import _link
from tests.security.test_memberships import _enable_memberships, _member, _plan
from tests.security.test_payments import _enable
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
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

_PROFILE_FIELDS = {
    "id",
    "member_number",
    "first_name",
    "last_name",
    "phone",
    "email",
    "gender",
    "date_of_birth",
    "address",
    "join_date",
    "emergency_contact_name",
    "emergency_contact_phone",
    "profile_photo_asset_id",
    "is_pt_client",
    "status",
    "archived_at",
    "created_at",
    "updated_at",
}


def _staff() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _tenant(prefix: str) -> tuple[APIClient, dict[str, str], Gym, GymMembership]:
    client, token = platform_token(f"{prefix}-{uuid4().hex[:8]}@example.test")
    created = provision(
        client,
        token,
        code=f"{prefix[:4].upper()}{uuid4().hex[:6].upper()}",
        owner_email=f"{prefix}-owner-{uuid4().hex[:8]}@example.test",
    )
    gym_payload = created["gym"]
    owner_payload = created["owner"]["gym_membership"]
    gym = Gym.objects.get(id=gym_payload["id"])
    owner = GymMembership.objects.select_related("user").get(id=owner_payload["id"])
    api = _staff()
    headers = auth_headers(staff_token(owner))
    _enable_memberships(gym)
    _enable(gym, "finance")
    _enable(gym, "pt")
    return api, headers, gym, owner


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (8, 16, 24)).save(buffer, format="PNG")
    return buffer.getvalue()


def _load_member(gym: Gym, member_id: str) -> Member:
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        return Member.objects.get(gym=gym, id=member_id)


def _sell(api: APIClient, headers: dict[str, str], *, member_id: str, plan_id: str) -> str:
    response = api.post(
        "/api/v1/memberships/",
        {
            "member_id": member_id,
            "plan_id": plan_id,
            "start_date": "2026-10-01",
            "discount_amount": "0.00",
        },
        format="json",
        **headers,
    )
    assert response.status_code == 201, response.json()
    return str(response.json()["data"]["id"])


@override_settings(**jwt_settings())
def test_member_self_profile_is_token_scoped_and_allowlisted() -> None:
    api, headers, gym, owner = _tenant("self")
    other_api, other_headers, other_gym, _other_owner = _tenant("othr")
    member_id = _member(api, headers, first_name="Ada")
    other_id = _member(other_api, other_headers, first_name="Grace")
    _user, token = _link(gym, member_id, f"ada-{uuid4().hex[:8]}@example.test")
    _other_user, other_token = _link(other_gym, other_id, f"grace-{uuid4().hex[:8]}@example.test")
    client = APIClient()

    staff_denied = client.get("/api/v1/member/me/", **auth_headers(staff_token(owner)))
    assert staff_denied.status_code == 401
    assert staff_denied.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    _platform_client, platform = platform_token(f"plat-{uuid4().hex[:8]}@example.test")
    platform_denied = client.get("/api/v1/member/me/", HTTP_AUTHORIZATION=f"Bearer {platform}")
    assert platform_denied.status_code == 401

    profile = client.get("/api/v1/member/me/", **_auth(token))
    assert profile.status_code == 200, profile.json()
    assert set(profile.json()["data"]) == _PROFILE_FIELDS
    assert profile.json()["data"]["id"] == member_id
    other_profile = client.get("/api/v1/member/me/", **_auth(other_token))
    assert other_profile.json()["data"]["id"] == other_id

    selector = client.get(f"/api/v1/member/me/?member_id={other_id}", **_auth(token))
    assert selector.status_code == 400
    rejected = client.patch(
        "/api/v1/member/me/",
        {
            "phone": "+66812345678",
            "first_name": "Changed",
            "status": "ARCHIVED",
            "gym_id": str(other_gym.id),
            "linked_user_id": str(uuid4()),
        },
        format="json",
        **_auth(token),
    )
    assert rejected.status_code == 400
    stored = _load_member(gym, member_id)
    assert stored.first_name == "Ada"
    assert stored.status == Member.Status.ACTIVE

    before_version = stored.authz_version
    updated = client.patch(
        "/api/v1/member/me/",
        {"phone": "+66812345678", "emergency_contact_name": "Byron"},
        format="json",
        **_auth(token),
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["data"]["phone"] == "+66812345678"
    assert updated.json()["data"]["first_name"] == "Ada"
    stored = _load_member(gym, member_id)
    assert stored.authz_version == before_version
    assert stored.linked_user_id == _user.id
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        audit = AuditLog.objects.get(gym=gym, action="MEMBER_PROFILE_UPDATED", target_id=stored.id)
    assert set(audit.new_values) <= {"phone", "emergency_contact_name"}
    assert audit.actor_membership_id is None
    empty = client.patch("/api/v1/member/me/", {}, format="json", **_auth(token))
    assert empty.status_code == 400


@override_settings(**jwt_settings())
def test_member_histories_are_self_scoped_paged_and_finance_gated() -> None:
    api, headers, gym, owner = _tenant("hist")
    other_api, other_headers, other_gym, other_owner = _tenant("histb")
    member_id = _member(api, headers, first_name="Ada")
    other_member_id = _member(other_api, other_headers, first_name="Grace")
    same_gym_other = _member(api, headers, first_name="Other")
    plan_id = _plan(api, headers, code="M30", duration_value=30, duration_unit="DAY")
    membership_id = _sell(api, headers, member_id=member_id, plan_id=plan_id)
    _user, token = _link(gym, member_id, f"hist-{uuid4().hex[:8]}@example.test")
    _other_user, other_token = _link(
        other_gym, other_member_id, f"histb-{uuid4().hex[:8]}@example.test"
    )
    member = _load_member(gym, member_id)
    other_member = _load_member(other_gym, other_member_id)
    first = _payment(gym, owner.id, member=member, status=Payment.Status.POSTED, amount="10.00")
    second = _payment(
        gym,
        owner.id,
        member=member,
        status=Payment.Status.POSTED,
        amount="20.00",
        received_at=first.received_at + timedelta(minutes=1),
    )
    foreign = _payment(
        other_gym,
        other_owner.id,
        member=other_member,
        status=Payment.Status.POSTED,
        amount="99.00",
    )
    checked = api.post(
        "/api/v1/check-ins/",
        {"member_id": member_id, "note": "door"},
        format="json",
        **headers,
        **_key("visit"),
    )
    assert checked.status_code == 201, checked.json()

    client = APIClient()
    dashboard = client.get("/api/v1/member/dashboard/", **_auth(token))
    assert dashboard.status_code == 200, dashboard.json()
    body = dashboard.json()["data"]
    assert body["member"]["id"] == member_id
    assert "notes" not in body["member"]
    assert "cancelled_by_membership_id" not in body["current_membership"]
    assert body["current_membership"]["id"] == membership_id
    assert body["payment_summary"]["available"] is True
    assert body["payment_summary"]["total_charged"] == "30.00"

    payments = client.get("/api/v1/member/payments/?limit=1", **_auth(token))
    assert payments.status_code == 200, payments.json()
    page = payments.json()
    assert [row["id"] for row in page["data"]] == [str(second.id)]
    assert page["meta"]["has_more"] is True
    nxt = client.get(
        f"/api/v1/member/payments/?limit=1&after={page['meta']['next_after']}",
        **_auth(token),
    )
    assert [row["id"] for row in nxt.json()["data"]] == [str(first.id)]
    assert str(foreign.id) not in nxt.content.decode()
    stolen = client.get(
        f"/api/v1/member/payments/?limit=1&after={page['meta']['next_after']}",
        **_auth(other_token),
    )
    assert stolen.status_code == 400
    assert (
        client.get(
            f"/api/v1/member/payments/?member_id={same_gym_other}", **_auth(token)
        ).status_code
        == 400
    )

    check_ins = client.get("/api/v1/member/check-ins/", **_auth(token))
    assert check_ins.status_code == 200, check_ins.json()
    assert check_ins.json()["data"][0]["member"]["id"] == member_id
    assert "device_id" not in check_ins.json()["data"][0]

    suspended = api.post(
        f"/api/v1/memberships/{membership_id}/suspend/",
        {},
        format="json",
        **headers,
    )
    assert suspended.status_code == 200, suspended.json()
    still = client.get("/api/v1/member/membership/", **_auth(token))
    assert still.json()["data"]["status"] == "SUSPENDED"

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        GymFeatureEntitlement.objects.filter(gym=gym, feature__code="finance").update(enabled=False)
    with CaptureQueriesContext(connections["default"]) as captured:
        gated = client.get("/api/v1/member/dashboard/", **_auth(token))
    assert gated.status_code == 200, gated.json()
    summary = gated.json()["data"]["payment_summary"]
    assert summary["available"] is False
    assert summary["total_charged"] is None
    rendered_sql = " ".join(item["sql"] for item in captured.captured_queries)
    assert "members_payment" not in rendered_sql

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = Member.objects.select_for_update().get(id=member_id)
        row.status = Member.Status.SUSPENDED
        row.save(update_fields=["status", "updated_at"])
    assert client.get("/api/v1/member/me/", **_auth(token)).status_code == 401


@override_settings(**jwt_settings())
def test_member_packages_schedule_and_class_booking_ignore_body_selectors() -> None:
    api, headers, gym, owner = _tenant("book")
    member_id = _member(api, headers, first_name="Ada")
    other_id = _member(api, headers, first_name="Grace")
    _user, token = _link(gym, member_id, f"book-{uuid4().hex[:8]}@example.test")
    trainer_membership = add_staff(
        gym, email=f"trainer-{uuid4().hex[:8]}@example.test", role_key="TRAINER"
    )
    trainer = _profile(gym, trainer_membership, code="TRN", name="Trainer Ada")
    class_type = api.post(
        "/api/v1/class-types/", _type_body(code="SPIN", name="Spin"), format="json", **headers
    )
    assert class_type.status_code == 201, class_type.json()
    type_id = class_type.json()["data"]["id"]
    class_package = _package(
        api, headers, package_type="CLASS", code="SPIN_PACK", class_type_id=type_id
    )
    grant = _grant(api, headers, member_id=member_id, package_id=class_package)
    other_grant = _grant(api, headers, member_id=other_id, package_id=class_package)
    schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=1,
            location="Studio A",
            location_is_exclusive=False,
            effective_from="2026-11-02",
            effective_until="2026-11-02",
            weekdays=[0],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("member-schedule"),
    )
    assert schedule.status_code == 201, schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        occurrence = ClassOccurrence.objects.get(class_schedule_id=schedule.json()["data"]["id"])

    client = APIClient()
    packages = client.get("/api/v1/member/service-packages/", **_auth(token))
    assert packages.status_code == 200, packages.json()
    assert [row["id"] for row in packages.json()["data"]] == [grant]
    assert "amount_snapshot" not in packages.json()["data"][0]
    assert (
        client.get(
            f"/api/v1/member/service-packages/?member_id={other_id}", **_auth(token)
        ).status_code
        == 400
    )

    listed = client.get(
        "/api/v1/member/schedule/?date_from=2026-11-02&date_to=2026-11-02",
        **_auth(token),
    )
    assert listed.status_code == 200, listed.json()
    occurrence_row = listed.json()["data"]["occurrences"][0]
    assert occurrence_row["id"] == str(occurrence.id)
    assert occurrence_row["class_name"] == "Spin"
    assert "Grace" not in listed.content.decode()
    assert occurrence_row["member_booking_id"] is None

    tampered = client.post(
        "/api/v1/member/class-bookings/",
        {
            "occurrence_id": str(occurrence.id),
            "member_service_package_id": grant,
            "member_id": other_id,
            "unmetered": True,
        },
        format="json",
        **_auth(token),
        **_key("tamper"),
    )
    assert tampered.status_code == 400
    book_key = {"HTTP_IDEMPOTENCY_KEY": "member-self-book-key"}
    booked = client.post(
        "/api/v1/member/class-bookings/",
        {"occurrence_id": str(occurrence.id), "member_service_package_id": grant},
        format="json",
        **_auth(token),
        **book_key,
    )
    assert booked.status_code == 201, booked.json()
    assert booked.json()["data"]["kind"] == "CLASS"
    assert booked.json()["data"]["unmetered"] is False
    replay = client.post(
        "/api/v1/member/class-bookings/",
        {"occurrence_id": str(occurrence.id), "member_service_package_id": grant},
        format="json",
        **_auth(token),
        **book_key,
    )
    assert replay.status_code == 201, replay.json()
    assert replay.json()["data"]["id"] == booked.json()["data"]["id"]
    reused = client.post(
        "/api/v1/member/class-bookings/",
        {"occurrence_id": str(occurrence.id), "member_service_package_id": other_grant},
        format="json",
        **_auth(token),
        **book_key,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    occupied = client.get(
        "/api/v1/member/schedule/?date_from=2026-11-02&date_to=2026-11-02",
        **_auth(token),
    )
    booking_id = booked.json()["data"]["id"]
    assert occupied.json()["data"]["occurrences"][0]["member_booking_id"] == booking_id
    foreign_package = client.post(
        "/api/v1/member/class-bookings/",
        {"occurrence_id": str(occurrence.id), "member_service_package_id": other_grant},
        format="json",
        **_auth(token),
        **_key("foreign-package"),
    )
    assert foreign_package.status_code == 422
    assert foreign_package.json()["error"]["code"] == "PACKAGE_NOT_ELIGIBLE"

    second_schedule = api.post(
        "/api/v1/class-schedules/",
        _schedule_body(
            class_type_id=type_id,
            trainer_staff_profile_id=str(trainer.id),
            capacity=1,
            location="Race room",
            location_is_exclusive=False,
            effective_from="2026-11-09",
            effective_until="2026-11-09",
            weekdays=[0],
            local_start_time="18:00:00",
            local_end_time="19:00:00",
        ),
        format="json",
        **headers,
        **_key("race-schedule"),
    )
    assert second_schedule.status_code == 201, second_schedule.json()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        race = ClassOccurrence.objects.get(class_schedule_id=second_schedule.json()["data"]["id"])
    third_id = _member(api, headers, first_name="Third")
    third_grant = _grant(api, headers, member_id=third_id, package_id=class_package)
    barrier = Barrier(2, timeout=20)
    actor_id = owner.user_id

    def book(person: str, package_id: str) -> str:
        connections.close_all()
        try:
            barrier.wait()
            try:
                create_member_class_booking(
                    gym=Gym.objects.get(id=gym.id),
                    member_id=UUID(person),
                    actor_user=User.objects.get(id=actor_id),
                    class_occurrence_id=race.id,
                    member_service_package_id=UUID(package_id),
                    request_id=f"req_{uuid4().hex}",
                    ip_address=None,
                    user_agent="",
                )
            except (ClassFull, ScheduleConflict):
                return "blocked"
            return "booked"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(
            pool.map(
                lambda item: book(item[0], item[1]),
                ((other_id, other_grant), (third_id, third_grant)),
            )
        )
    assert results == ["blocked", "booked"]


@_FAKES
def test_member_profile_photo_stays_on_the_token_member() -> None:
    from apps.files.images import canonicalize

    use_fakes(processor=canonicalize)
    api, headers, gym, owner = _tenant("photo")
    member_id = _member(api, headers, first_name="Ada")
    other_id = _member(api, headers, first_name="Grace")
    _user, token = _link(gym, member_id, f"photo-{uuid4().hex[:8]}@example.test")
    image = _png()
    digest = hashlib.sha256(image).hexdigest()
    client = APIClient()
    staff_file = client.post(
        "/api/v1/files/upload-intents/",
        {
            "original_name": "portrait.png",
            "content_type": "image/png",
            "byte_size": len(image),
            "sha256": digest,
            "purpose": "PROFILE_PHOTO",
            "owner_type": "MEMBER",
            "owner_id": member_id,
        },
        format="json",
        **_auth(token),
    )
    assert staff_file.status_code == 403
    assert staff_file.json()["error"]["code"] == "PERMISSION_DENIED"
    rejected_type = client.post(
        "/api/v1/member/files/upload-intents/",
        {
            "original_name": "portrait.gif",
            "content_type": "image/gif",
            "byte_size": len(image),
            "sha256": digest,
        },
        format="json",
        **_auth(token),
    )
    assert rejected_type.status_code == 422
    assert rejected_type.json()["error"]["code"] == "FILE_TYPE_NOT_ALLOWED"
    owned_fields = client.post(
        "/api/v1/member/files/upload-intents/",
        {
            "original_name": "portrait.png",
            "content_type": "image/png",
            "byte_size": len(image),
            "sha256": digest,
            "purpose": "PROFILE_PHOTO",
            "owner_id": other_id,
        },
        format="json",
        **_auth(token),
    )
    assert owned_fields.status_code == 400
    created = client.post(
        "/api/v1/member/files/upload-intents/",
        {
            "original_name": "portrait.png",
            "content_type": "image/png",
            "byte_size": len(image),
            "sha256": digest,
        },
        format="json",
        **_auth(token),
    )
    assert created.status_code == 201, created.json()
    assert "object_key" not in created.content.decode()
    file_id = created.json()["data"]["file_id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        asset = FileAsset.objects.get(gym=gym, id=file_id)
        assert asset.owner_id == UUID(member_id)
        assert asset.uploaded_by_membership_id is None
        assert asset.uploaded_by_member_id == UUID(member_id)
        store_key = asset.object_key
    store = CURRENT.get("store")
    assert isinstance(store, MemoryStore)
    store.objects[store_key] = StoredObject(body=image, metadata={})
    completed = client.post(
        f"/api/v1/member/files/{file_id}/complete/",
        {"sha256": digest, "byte_size": len(image)},
        format="json",
        **_auth(token),
    )
    assert completed.status_code == 200, completed.json()
    assert completed.json()["data"]["scan_status"] == "CLEAN"
    assert _load_member(gym, member_id).profile_photo_asset_id == UUID(file_id)

    other_asset_id = uuid4()
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        FileAsset.objects.create(
            id=other_asset_id,
            gym=gym,
            object_key=f"{gym.id}/{uuid4()}",
            original_name="other.png",
            content_type="image/png",
            byte_size=len(image),
            sha256=digest,
            upload_content_type="image/png",
            upload_byte_size=len(image),
            upload_sha256=digest,
            purpose="PROFILE_PHOTO",
            owner_type="MEMBER",
            owner_id=UUID(other_id),
            scan_status=FileAsset.ScanStatus.CLEAN,
            retention_status=FileAsset.RetentionStatus.ACTIVE,
            uploaded_by_membership_id=owner.id,
        )
    missing = client.get(f"/api/v1/member/files/{other_asset_id}/download-url/", **_auth(token))
    assert missing.status_code == 404
    removed = client.delete(f"/api/v1/member/files/{file_id}/", **_auth(token))
    assert removed.status_code == 200, removed.json()
    assert _load_member(gym, member_id).profile_photo_asset_id is None
