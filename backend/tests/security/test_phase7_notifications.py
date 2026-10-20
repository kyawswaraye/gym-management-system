"""Announcements, member devices, inbox, and push delivery."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.announcements.exceptions import AnnouncementStateConflict
from apps.announcements.models import Announcement
from apps.announcements.services import (
    expire_due_announcement,
    publish_announcement,
    publish_due_announcement,
    resolve_recipient_ids,
)
from apps.audit.models import AuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.member_device_services import revoke_devices_for_user
from apps.members.models import Member, MemberDevice, MembershipPlan
from apps.members.services import archive_member, gym_today, link_member_identity
from apps.notifications.models import Notification, PushAttempt
from apps.notifications.push_services import (
    deliver_next_push_notification,
    schedule_membership_expiry_pushes,
)
from apps.notifications.services import DeliveryOutcome
from apps.notifications.tokens import decrypt_push_token
from apps.notifications.transport import (
    INVALID_TOKEN,
    PROVIDER_UNAVAILABLE,
    PushResult,
    locmem_transport,
)
from apps.tenants.models import Gym, GymMembership, GymSettings
from apps.users.models import PasswordResetToken, User
from apps.users.services import hash_password_reset_token, reset_password
from tests.security.test_member_scan import PASSWORD, _link
from tests.security.test_membership_expiry_email import _member as _orm_member
from tests.security.test_membership_expiry_email import _plan, _prepare, _provision, _sell
from tests.security.test_memberships import _enable_memberships, _member
from tests.security.test_tenant_gym_settings import (
    BFF_SECRET,
    add_staff,
    auth_headers,
    jwt_settings,
    platform_token,
    provision,
    staff_token,
)
from tests.security.test_trainer_assignments import _assign, _profile

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]


def _staff() -> APIClient:
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    return client


def _gym(prefix: str) -> tuple[APIClient, dict[str, str], Gym, GymMembership]:
    client, token = platform_token(f"{prefix}-{uuid4().hex[:8]}@example.test")
    created = provision(
        client,
        token,
        code=f"{prefix[:4].upper()}{uuid4().hex[:6].upper()}",
        owner_email=f"{prefix}-owner-{uuid4().hex[:8]}@example.test",
    )
    gym = Gym.objects.get(id=created["gym"]["id"])
    owner = GymMembership.objects.select_related("user").get(
        id=created["owner"]["gym_membership"]["id"]
    )
    return _staff(), auth_headers(staff_token(owner)), gym, owner


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "GENERAL",
        "title": "Holiday hours",
        "body": "The gym closes at six.",
        "audience": "ALL_ACTIVE_MEMBERS",
    }
    payload.update(overrides)
    return payload


def _device_body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "platform": "ANDROID",
        "installation_id": str(uuid4()),
        "push_consent": True,
        "push_token": f"fcm-{uuid4().hex}",
        "app_version": "1.2.3",
        "os_version": "14",
        "device_name": "Pixel",
    }
    payload.update(overrides)
    return payload


@override_settings(**jwt_settings())
def test_announcement_permissions_audience_and_publish_state() -> None:
    api, headers, gym, owner = _gym("ann")
    _other_api, other_headers, other_gym, _other_owner = _gym("ano")
    receptionist = add_staff(
        gym, email=f"desk-{uuid4().hex[:8]}@example.test", role_key="RECEPTIONIST"
    )
    trainer = add_staff(gym, email=f"coach-{uuid4().hex[:8]}@example.test", role_key="TRAINER")
    desk_headers = auth_headers(staff_token(receptionist))
    trainer_headers = auth_headers(staff_token(trainer))
    active = _member(api, headers, first_name="Active")
    suspended = _member(api, headers, first_name="Paused")
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        Member.objects.filter(id=suspended).update(status=Member.Status.SUSPENDED)
    created = api.post("/api/v1/announcements/", _body(), format="json", **headers)
    assert created.status_code == 201, created.content
    assert created.json()["data"]["status"] == "DRAFT"
    assert "recipient_count" not in created.json()["data"]
    announcement_id = created.json()["data"]["id"]
    assert api.get(f"/api/v1/announcements/{announcement_id}/", **desk_headers).status_code == 200
    denied = api.post("/api/v1/announcements/", _body(), format="json", **desk_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert api.get("/api/v1/announcements/", **trainer_headers).status_code == 200
    leaked = api.get(f"/api/v1/announcements/{announcement_id}/", **other_headers)
    assert leaked.status_code == 404
    assert leaked.json()["error"]["code"] == "NOT_FOUND"
    mass = api.post(
        "/api/v1/announcements/",
        _body(gym_id=str(gym.id), member_ids=[active], status="PUBLISHED"),
        format="json",
        **headers,
    )
    assert mass.status_code == 400
    assert mass.json()["error"]["code"] == "INVALID_REQUEST"
    idempotent = api.post(
        "/api/v1/announcements/",
        _body(),
        format="json",
        HTTP_IDEMPOTENCY_KEY="announcement-key-01",
        **headers,
    )
    assert idempotent.status_code == 400
    assert idempotent.json()["error"]["code"] == "INVALID_REQUEST"
    platform_client, platform_access = platform_token(f"plat-{uuid4().hex[:8]}@example.test")
    platform_client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    assert (
        platform_client.get(
            "/api/v1/announcements/",
            **auth_headers(platform_access),
        ).status_code
        == 401
    )
    _user, member_token = _link(gym, active, f"member-{uuid4().hex[:8]}@example.test")
    member_client = APIClient()
    assert (
        member_client.get(
            "/api/v1/announcements/",
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        ).status_code
        == 401
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        recipients = resolve_recipient_ids(
            gym=gym,
            audience=Announcement.Audience.ALL_ACTIVE_MEMBERS,
            today=gym_today(gym),
        )
    assert active in {str(item) for item in recipients}
    assert suspended not in {str(item) for item in recipients}
    published = api.post(
        f"/api/v1/announcements/{announcement_id}/publish/",
        {},
        format="json",
        **headers,
    )
    assert published.status_code == 200, published.content
    assert published.json()["data"]["status"] == "PUBLISHED"
    again = api.post(
        f"/api/v1/announcements/{announcement_id}/publish/",
        {},
        format="json",
        **headers,
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ANNOUNCEMENT_STATE_CONFLICT"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        notes = Notification.objects.filter(gym=gym, announcement_id=announcement_id)
        assert notes.filter(channel=Notification.Channel.IN_APP).count() == 1
        assert notes.filter(channel=Notification.Channel.PUSH).count() == 1
        assert not notes.filter(recipient_member_id=suspended).exists()
    cancelled = api.post(
        f"/api/v1/announcements/{announcement_id}/cancel/",
        {},
        format="json",
        **headers,
    )
    assert cancelled.status_code == 200
    first_cancelled_at = cancelled.json()["data"]["cancelled_at"]
    repeat = api.post(
        f"/api/v1/announcements/{announcement_id}/cancel/",
        {},
        format="json",
        **headers,
    )
    assert repeat.status_code == 200
    assert repeat.json()["data"]["cancelled_at"] == first_cancelled_at
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        push = Notification.objects.get(
            gym=gym,
            announcement_id=announcement_id,
            channel=Notification.Channel.PUSH,
        )
        inbox = Notification.objects.get(
            gym=gym,
            announcement_id=announcement_id,
            channel=Notification.Channel.IN_APP,
        )
    assert push.status == Notification.Status.CANCELLED
    assert inbox.status == Notification.Status.SENT
    assert AuditLog.objects.filter(gym=gym, action="ANNOUNCEMENT_PUBLISHED").exists()
    assert other_gym.id != gym.id
    assert owner.id


@override_settings(**jwt_settings())
def test_audience_pt_and_expiry_windows_are_snapshotted() -> None:
    api, headers, gym, owner = _gym("aud")
    _enable_memberships(gym)
    trainer = add_staff(gym, email=f"pt-{uuid4().hex[:8]}@example.test", role_key="TRAINER")
    profile = _profile(gym, trainer, code="PT01", name="Coach")
    today = gym_today(gym)
    soon = _member(api, headers, first_name="Soon")
    later = _member(api, headers, first_name="Later")
    client = _member(api, headers, first_name="Client")
    plan_soon = api.post(
        "/api/v1/membership-plans/",
        {
            "code": "SOON6",
            "name": "Soon",
            "duration_value": 6,
            "duration_unit": "DAY",
            "price": "10.00",
        },
        format="json",
        **headers,
    )
    assert plan_soon.status_code == 201, plan_soon.content
    plan_later = api.post(
        "/api/v1/membership-plans/",
        {
            "code": "LATE21",
            "name": "Later",
            "duration_value": 21,
            "duration_unit": "DAY",
            "price": "10.00",
        },
        format="json",
        **headers,
    )
    assert plan_later.status_code == 201, plan_later.content
    for member_id, plan_id in (
        (soon, plan_soon.json()["data"]["id"]),
        (later, plan_later.json()["data"]["id"]),
    ):
        sold = api.post(
            "/api/v1/memberships/",
            {"member_id": member_id, "plan_id": plan_id, "start_date": today.isoformat()},
            format="json",
            **headers,
        )
        assert sold.status_code == 201, sold.content
    assigned = _assign(
        api,
        headers,
        trainer_id=str(profile.id),
        member_id=client,
        start=today.isoformat(),
    )
    assert assigned.status_code == 201, assigned.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        week = {
            str(item)
            for item in resolve_recipient_ids(
                gym=gym, audience="EXPIRING_WITHIN_7_DAYS", today=today
            )
        }
        month = {
            str(item)
            for item in resolve_recipient_ids(
                gym=gym, audience="EXPIRING_WITHIN_30_DAYS", today=today
            )
        }
        clients = {
            str(item) for item in resolve_recipient_ids(gym=gym, audience="PT_CLIENTS", today=today)
        }
    assert soon in week
    assert later not in week
    assert soon in month and later in month
    assert week <= month
    assert clients == {client}
    created = api.post(
        "/api/v1/announcements/",
        _body(audience="PT_CLIENTS", starts_at=(timezone.now() + timedelta(days=2)).isoformat()),
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.content
    scheduled = api.post(
        f"/api/v1/announcements/{created.json()['data']['id']}/publish/",
        {},
        format="json",
        **headers,
    )
    assert scheduled.status_code == 200, scheduled.content
    assert scheduled.json()["data"]["status"] == "SCHEDULED"
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not Notification.objects.filter(
            gym=gym, announcement_id=created.json()["data"]["id"]
        ).exists()
    call_command("publish_scheduled_announcements", gym_code=gym.code)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = Announcement.objects.get(id=created.json()["data"]["id"])
        assert row.status == Announcement.Status.SCHEDULED
    assert owner.id


@override_settings(**jwt_settings())
def test_due_publish_inserts_recipients_and_expiry_keeps_the_inbox() -> None:
    api, headers, gym, _owner = _gym("due")
    member_id = _member(api, headers, first_name="Due")
    starts = timezone.now() + timedelta(hours=2)
    ends = starts + timedelta(hours=1)
    created = api.post(
        "/api/v1/announcements/",
        _body(starts_at=starts.isoformat(), ends_at=ends.isoformat()),
        format="json",
        **headers,
    )
    assert created.status_code == 201, created.content
    announcement_id = created.json()["data"]["id"]
    scheduled = api.post(
        f"/api/v1/announcements/{announcement_id}/publish/",
        {},
        format="json",
        **headers,
    )
    assert scheduled.status_code == 200, scheduled.content
    assert scheduled.json()["data"]["status"] == "SCHEDULED"
    call_command("publish_scheduled_announcements", gym_code=gym.code)
    call_command("expire_announcements", gym_code=gym.code)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        row = Announcement.objects.get(id=announcement_id)
        assert row.status == Announcement.Status.SCHEDULED
        assert publish_due_announcement(gym=gym, now=starts) is True
        row.refresh_from_db()
        assert row.status == Announcement.Status.PUBLISHED
        notes = Notification.objects.filter(gym=gym, announcement_id=announcement_id)
        assert notes.filter(channel=Notification.Channel.IN_APP).count() == 1
        assert notes.filter(channel=Notification.Channel.PUSH).count() == 1
        assert notes.filter(recipient_member_id=member_id).count() == 2
        assert publish_due_announcement(gym=gym, now=starts) is False
        assert expire_due_announcement(gym=gym, now=ends) is False
        assert expire_due_announcement(gym=gym, now=ends + timedelta(seconds=1)) is True
        row.refresh_from_db()
        expired_at = row.expired_at
        assert row.status == Announcement.Status.EXPIRED
        assert expired_at is not None
        assert expire_due_announcement(gym=gym, now=ends + timedelta(hours=1)) is False
        row.refresh_from_db()
        assert row.expired_at == expired_at
        inbox = notes.get(channel=Notification.Channel.IN_APP)
        push = notes.get(channel=Notification.Channel.PUSH)
        assert inbox.status == Notification.Status.SENT
        assert push.status == Notification.Status.PENDING


@override_settings(**jwt_settings())
def test_publish_locks_out_a_second_writer() -> None:
    api, headers, gym, owner = _gym("race")
    member_id = _member(api, headers, first_name="Racer")
    created = api.post("/api/v1/announcements/", _body(), format="json", **headers)
    assert created.status_code == 201, created.content
    announcement_id = created.json()["data"]["id"]
    barrier = Barrier(2)

    def worker() -> str:
        connections.close_all()
        try:
            barrier.wait(timeout=5)
            with transaction.atomic():
                set_gym_context(gym_id=gym.id)
                publish_announcement(
                    gym=Gym.objects.get(pk=gym.id),
                    actor=GymMembership.objects.get(pk=owner.id),
                    actor_user=owner.user,
                    announcement_id=announcement_id,
                    request_id="race",
                    ip_address=None,
                    user_agent="",
                )
            return "published"
        except AnnouncementStateConflict:
            return "conflict"
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _: worker(), range(2)))
    assert results == ["conflict", "published"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert Notification.objects.filter(gym=gym, announcement_id=announcement_id).count() == 2
        assert str(member_id)


@override_settings(**jwt_settings())
def test_member_devices_encrypt_rotate_and_revoke(caplog: pytest.LogCaptureFixture) -> None:
    api, headers, gym, _owner = _gym("dev")
    member_id = _member(api, headers, first_name="Device")
    _user, token = _link(gym, member_id, f"device-{uuid4().hex[:8]}@example.test")
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    secret = "fcm-secret-token-value"
    caplog.set_level("INFO")
    created = client.post(
        "/api/v1/member/devices/",
        _device_body(push_token=secret),
        format="json",
        **auth,
    )
    assert created.status_code == 201, created.content
    body = created.json()["data"]
    assert body["push_token_present"] is True
    assert secret not in created.content.decode()
    assert secret not in caplog.text
    device_id = body["id"]
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = MemberDevice.objects.get(id=device_id)
        assert stored.token_ciphertext is not None
        assert secret not in stored.token_ciphertext
        assert decrypt_push_token(stored.token_ciphertext) == secret
        assert stored.token_fingerprint is not None
        assert len(stored.token_fingerprint) == 64
    rotated = client.post(
        "/api/v1/member/devices/",
        _device_body(
            installation_id=body["installation_id"],
            push_token="fcm-rotated-token-value",
        ),
        format="json",
        **auth,
    )
    assert rotated.status_code == 200, rotated.content
    conflict = client.post(
        "/api/v1/member/devices/",
        _device_body(installation_id=body["installation_id"], platform="IOS"),
        format="json",
        **auth,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "DEVICE_CONFLICT"
    cleared = client.patch(
        f"/api/v1/member/devices/{device_id}/",
        {"push_consent": False},
        format="json",
        **auth,
    )
    assert cleared.status_code == 200, cleared.content
    assert cleared.json()["data"]["revoked_at"] is None
    assert cleared.json()["data"]["push_token_present"] is False
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored.refresh_from_db()
        assert stored.token_ciphertext is None
        assert stored.token_fingerprint is None
        assert stored.revoked_at is None
    restored = client.patch(
        f"/api/v1/member/devices/{device_id}/",
        {"push_consent": True, "push_token": secret},
        format="json",
        **auth,
    )
    assert restored.status_code == 200, restored.content
    deleted = client.delete(f"/api/v1/member/devices/{device_id}/", **auth)
    assert deleted.status_code == 204
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored.refresh_from_db()
        revoked_at = stored.revoked_at
        assert stored.revocation_reason == MemberDevice.RevocationReason.CLIENT_DELETED
        assert stored.token_ciphertext is None
    assert client.delete(f"/api/v1/member/devices/{device_id}/", **auth).status_code == 204
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored.refresh_from_db()
        assert stored.revoked_at == revoked_at
    other = _member(api, headers, first_name="Other")
    _other_user, other_token = _link(gym, other, f"other-{uuid4().hex[:8]}@example.test")
    other_client = APIClient()
    hidden = other_client.patch(
        f"/api/v1/member/devices/{device_id}/",
        {"device_name": "Stolen"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {other_token}",
    )
    assert hidden.status_code == 404
    staff_denied = api.post("/api/v1/member/devices/", _device_body(), format="json", **headers)
    assert staff_denied.status_code == 401


@override_settings(**jwt_settings())
def test_sixth_device_supersedes_the_least_recently_seen() -> None:
    _api, _headers, gym, _owner = _gym("six")
    member_id = _member(_api, _headers, first_name="Six")
    _user, token = _link(gym, member_id, f"six-{uuid4().hex[:8]}@example.test")
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    ids = []
    for index in range(5):
        created = client.post(
            "/api/v1/member/devices/",
            _device_body(device_name=f"Phone {index}"),
            format="json",
            **auth,
        )
        assert created.status_code == 201, created.content
        ids.append(created.json()["data"]["id"])
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        oldest = MemberDevice.objects.get(id=ids[0])
        oldest.last_seen_at = timezone.now() - timedelta(days=3)
        oldest.save(update_fields=["last_seen_at", "updated_at"])
    extra = client.post("/api/v1/member/devices/", _device_body(), format="json", **auth)
    assert extra.status_code == 201, extra.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        oldest.refresh_from_db()
        assert oldest.revocation_reason == MemberDevice.RevocationReason.SUPERSEDED
        assert (
            MemberDevice.objects.filter(member_id=member_id, revoked_at__isnull=True).count() == 5
        )


@override_settings(**jwt_settings())
def test_logout_keeps_devices_and_lifecycle_revokes_them() -> None:
    api, headers, gym, _owner = _gym("life")
    member_id = _member(api, headers, first_name="Life")
    user, _token = _link(gym, member_id, f"life-{uuid4().hex[:8]}@example.test")
    login = APIClient().post(
        "/api/v1/member-auth/login/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    access = login.json()["data"]["access_token"]
    refresh = login.json()["data"]["refresh_token"]
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {access}"}
    first = client.post("/api/v1/member/devices/", _device_body(), format="json", **auth)
    second = client.post("/api/v1/member/devices/", _device_body(), format="json", **auth)
    assert first.status_code == 201 and second.status_code == 201
    logged_out = APIClient().post(
        "/api/v1/member-auth/logout/",
        {"refresh_token": refresh},
        format="json",
    )
    assert logged_out.status_code == 204, logged_out.content
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert (
            MemberDevice.objects.filter(member_id=member_id, revoked_at__isnull=True).count() == 2
        )
        member = Member.objects.get(id=member_id)
        member.status = Member.Status.SUSPENDED
        member.save(update_fields=["status", "updated_at"])
        assert not MemberDevice.objects.filter(
            member_id=member_id, revoked_at__isnull=True
        ).exists()
        reason = set(
            MemberDevice.objects.filter(member_id=member_id).values_list(
                "revocation_reason", flat=True
            )
        )
    assert reason == {MemberDevice.RevocationReason.MEMBER_SUSPENDED}
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        member.status = Member.Status.ACTIVE
        member.save(update_fields=["status", "updated_at"])
    replacement = user
    fresh = User.objects.create_user(f"relink-{uuid4().hex[:8]}@example.test", PASSWORD)
    del replacement
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        MemberDevice.objects.filter(member_id=member_id).update(
            revoked_at=None,
            revocation_reason=None,
            push_consent=False,
            token_ciphertext=None,
            token_fingerprint=None,
        )
    link_member_identity(gym=gym, member_id=member.id, user=fresh)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert not MemberDevice.objects.filter(
            member_id=member_id, revoked_at__isnull=True
        ).exists()
        stored = MemberDevice.objects.filter(member_id=member_id).first()
        assert stored is not None
        assert stored.revocation_reason == MemberDevice.RevocationReason.MEMBER_UNLINKED


@override_settings(**jwt_settings())
def test_archive_and_password_reset_clear_device_secrets() -> None:
    api, headers, gym, owner = _gym("arch")
    member_id = _member(api, headers, first_name="Arch")
    user, token = _link(gym, member_id, f"arch-{uuid4().hex[:8]}@example.test")
    client = APIClient()
    created = client.post(
        "/api/v1/member/devices/",
        _device_body(push_token="fcm-archive-token"),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert created.status_code == 201, created.content
    with transaction.atomic():
        archive_member(gym=gym, actor=owner, member_id=member_id)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        stored = MemberDevice.objects.get(id=created.json()["data"]["id"])
        assert stored.revocation_reason == MemberDevice.RevocationReason.MEMBER_ARCHIVED
        assert stored.token_ciphertext is None
        audit_values = list(AuditLog.objects.filter(gym=gym).values_list("new_values", flat=True))
        assert "fcm-archive-token" not in json.dumps(audit_values, default=str)
    other_id = _member(api, headers, first_name="Reset")
    other_user, other_token = _link(gym, other_id, f"reset-{uuid4().hex[:8]}@example.test")
    registered = client.post(
        "/api/v1/member/devices/",
        _device_body(push_token="fcm-reset-token"),
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {other_token}",
    )
    assert registered.status_code == 201, registered.content
    now = timezone.now()
    PasswordResetToken.objects.create(
        user=other_user,
        token_hash=hash_password_reset_token("phase7-reset-token"),
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    reset_password(
        reset_token="phase7-reset-token",
        password="another horse battery",
        request_id="reset-test",
    )
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        reset_device = MemberDevice.objects.get(id=registered.json()["data"]["id"])
        assert reset_device.revocation_reason == MemberDevice.RevocationReason.SESSION_REVOKED
        assert reset_device.token_ciphertext is None
    with transaction.atomic():
        revoke_devices_for_user(
            user_id=user.id,
            reason=MemberDevice.RevocationReason.SESSION_REVOKED,
        )
    assert other_user.id


@override_settings(**jwt_settings())
def test_member_inbox_lists_in_app_rows_and_reads_once() -> None:
    api, headers, gym, _owner = _gym("inbox")
    member_id = _member(api, headers, first_name="Inbox")
    other_id = _member(api, headers, first_name="Stranger")
    _user, token = _link(gym, member_id, f"inbox-{uuid4().hex[:8]}@example.test")
    _other, other_token = _link(gym, other_id, f"stranger-{uuid4().hex[:8]}@example.test")
    created = api.post(
        "/api/v1/announcements/",
        _body(audience="ALL_ACTIVE_MEMBERS"),
        format="json",
        **headers,
    )
    announcement_id = created.json()["data"]["id"]
    assert (
        api.post(
            f"/api/v1/announcements/{announcement_id}/publish/",
            {},
            format="json",
            **headers,
        ).status_code
        == 200
    )
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    listed = client.get("/api/v1/member/notifications/", **auth)
    assert listed.status_code == 200, listed.content
    items = listed.json()["data"]
    assert len(items) == 1
    assert items[0]["type"] == "ANNOUNCEMENT"
    assert items[0]["announcement_type"] == "GENERAL"
    assert items[0]["read_at"] is None
    assert "payload" not in items[0]
    rendered = listed.content.decode()
    assert "deduplication_key" not in rendered
    read = client.post(
        f"/api/v1/member/notifications/{items[0]['id']}/read/",
        {},
        format="json",
        **auth,
    )
    assert read.status_code == 200, read.content
    first_read = read.json()["data"]["read_at"]
    assert first_read
    repeat = client.post(
        f"/api/v1/member/notifications/{items[0]['id']}/read/",
        {},
        format="json",
        **auth,
    )
    assert repeat.json()["data"]["read_at"] == first_read
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        assert AuditLog.objects.filter(gym=gym, action="MEMBER_NOTIFICATION_READ").count() == 1
        push = Notification.objects.get(
            gym=gym,
            recipient_member_id=member_id,
            channel=Notification.Channel.PUSH,
        )
    missing = client.post(
        f"/api/v1/member/notifications/{push.id}/read/",
        {},
        format="json",
        **auth,
    )
    assert missing.status_code == 404
    stranger = APIClient()
    hidden = stranger.post(
        f"/api/v1/member/notifications/{items[0]['id']}/read/",
        {},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {other_token}",
    )
    assert hidden.status_code == 404
    audits = AuditLog.objects.filter(gym=gym, action="MEMBER_NOTIFICATION_READ")
    assert "The gym closes at six." not in json.dumps(
        list(audits.values_list("new_values", flat=True))
    )


def test_expiry_pushes_dedup_and_delivery_outcomes() -> None:
    tenant = _provision(f"PUSH{uuid4().hex[:6].upper()}")
    today = gym_today(tenant.gym)
    _prepare(tenant, warning_days=[7], email_enabled=False)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        settings_row = GymSettings.objects.get(gym=tenant.gym)
        settings_row.membership_expiry_push_enabled = False
        settings_row.save(update_fields=["membership_expiry_push_enabled", "updated_at"])
        plan = _plan(tenant)
        quiet = _orm_member(
            tenant,
            number="QUIET",
            first_name="Quiet",
            email=None,
            today=today,
        )
        _sell(tenant, member=quiet, plan=plan, warning_day=7, today=today)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        created = schedule_membership_expiry_pushes(gym=tenant.gym, today=today)
    assert created == 1
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        assert schedule_membership_expiry_pushes(gym=tenant.gym, today=today) == 0
        assert (
            Notification.objects.filter(gym=tenant.gym, channel=Notification.Channel.EMAIL).count()
            == 0
        )
        assert (
            Notification.objects.filter(gym=tenant.gym, channel=Notification.Channel.IN_APP).count()
            == 1
        )
        assert (
            Notification.objects.filter(gym=tenant.gym, channel=Notification.Channel.PUSH).count()
            == 0
        )
        settings_row = GymSettings.objects.get(gym=tenant.gym)
        settings_row.membership_expiry_push_enabled = True
        settings_row.save(update_fields=["membership_expiry_push_enabled", "updated_at"])
        assert schedule_membership_expiry_pushes(gym=tenant.gym, today=today) == 1
        push = Notification.objects.get(gym=tenant.gym, channel=Notification.Channel.PUSH)
        assert push.status == Notification.Status.PENDING
    user = User.objects.create_user(f"push-{uuid4().hex[:8]}@example.test", PASSWORD)
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        link_member_identity(gym=tenant.gym, member_id=quiet.id, user=user)
        device = MemberDevice.objects.create(
            gym=tenant.gym,
            member=quiet,
            installation_id=uuid4(),
            platform=MemberDevice.Platform.ANDROID,
            push_consent=True,
            token_ciphertext="placeholder",
            token_fingerprint="ab" * 32,
            app_version="1",
            os_version="1",
            last_seen_at=timezone.now(),
        )
    from apps.notifications.tokens import encrypt_push_token, fingerprint_push_token

    token = "fcm-delivery-token"
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        device.token_ciphertext = encrypt_push_token(token)
        device.token_fingerprint = fingerprint_push_token(token)
        device.save(update_fields=["token_ciphertext", "token_fingerprint", "updated_at"])
    transport = locmem_transport()
    transport.reset()

    def unavailable(_recorded: object) -> PushResult:
        return PushResult(code=PROVIDER_UNAVAILABLE)

    transport.handler = unavailable
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        outcome = deliver_next_push_notification(gym=tenant.gym)
    assert outcome is DeliveryOutcome.RETRIED
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        push.refresh_from_db()
        assert push.status == Notification.Status.PENDING
        assert push.last_error_code == PROVIDER_UNAVAILABLE
        assert push.provider_id == ""
        attempt = PushAttempt.objects.get(notification=push)
        assert attempt.last_error_code == PROVIDER_UNAVAILABLE
        assert token not in attempt.provider_message_id
    transport.reset()

    def invalid(_recorded: object) -> PushResult:
        return PushResult(code=INVALID_TOKEN)

    transport.handler = invalid
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        push.next_attempt_at = timezone.now()
        push.save(update_fields=["next_attempt_at", "updated_at"])
        outcome = deliver_next_push_notification(gym=tenant.gym)
        device.refresh_from_db()
    assert outcome is DeliveryOutcome.CANCELLED
    assert device.revocation_reason == MemberDevice.RevocationReason.INVALID_TOKEN
    assert device.token_ciphertext is None
    assert token not in repr(transport.deliveries)


def test_phase7_constraints_grants_and_email_shape_remain() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = ANY(%s)
            """,
            [
                [
                    "announcements_announcement",
                    "members_member_device",
                    "notifications_push_attempt",
                ]
            ],
        )
        rows = {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}
        assert rows == {
            "announcements_announcement": (True, True),
            "members_member_device": (True, True),
            "notifications_push_attempt": (True, True),
        }
        for table in rows:
            cursor.execute(
                """
                SELECT
                  has_table_privilege('gym_runtime', %s, 'SELECT'),
                  has_table_privilege('gym_runtime', %s, 'INSERT'),
                  has_table_privilege('gym_runtime', %s, 'UPDATE'),
                  has_table_privilege('gym_runtime', %s, 'DELETE'),
                  has_table_privilege('gym_auth_bootstrap', %s, 'SELECT')
                """,
                [table, table, table, table, table],
            )
            assert cursor.fetchone() == (True, True, True, False, False)
        cursor.execute(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conname = ANY(%s)
            """,
            [
                [
                    "notification_channel_contract_ck",
                    "notification_announcement_same_gym_fk",
                    "member_device_member_same_gym_fk",
                    "announcement_created_by_same_gym_fk",
                    "push_attempt_notification_same_gym_fk",
                    "member_device_fingerprint_uniq",
                ]
            ],
        )
        found = {row[0] for row in cursor.fetchall()}
    assert "notification_channel_contract_ck" in found
    assert "notification_expiry_contract_ck" not in found
    tenant = _provision(f"SQL{uuid4().hex[:6].upper()}")
    with pytest.raises(IntegrityError), transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        Announcement.objects.create(
            gym=tenant.gym,
            type="GENERAL",
            title="x",
            body="y",
            audience="NOT_A_REAL_AUDIENCE",
            status=Announcement.Status.DRAFT,
            created_by_membership=tenant.owner_membership,
        )
    with transaction.atomic():
        set_gym_context(gym_id=tenant.gym.id)
        plan = MembershipPlan.objects.filter(gym=tenant.gym).first()
        assert plan is None or plan.gym_id == tenant.gym.id
