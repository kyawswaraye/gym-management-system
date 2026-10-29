import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from smtplib import SMTPException
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core import mail
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog, PlatformAuditLog
from apps.common.db.tenant_context import set_gym_context
from apps.members.models import Member, Membership, MembershipPlan, Payment, Receipt
from apps.notifications.models import Notification
from apps.tenants.models import (
    ExpenseCategory,
    Feature,
    Gym,
    GymFeatureEntitlement,
    GymMembership,
    GymProvisioningRequest,
    GymSettings,
    Permission,
    Role,
    StaffInvitation,
    StaffInvitationRole,
    SubscriptionPlan,
    SubscriptionPlanFeature,
    SystemRoleTemplate,
    TenantSequence,
)
from apps.tenants.services import (
    EXPENSE_CATEGORY_CATALOG,
    OWNER_INVITATION_PENDING_MESSAGE,
    _hash_invitation_token,
    invite_owner,
    revoke_owner,
)
from apps.users.mfa import _totp
from apps.users.models import RefreshSession, User
from apps.users.tokens import decode_access_token

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

BFF_SECRET = "test-bff-shared-secret-32-chars-min"
PASSWORD = "correct horse battery staple"
PLATFORM_PERMISSION_CODES = (
    "platform.audit.view",
    "platform.gyms.view",
    "platform.gyms.create",
    "platform.gyms.update",
    "platform.gyms.activate",
    "platform.gyms.suspend",
    "platform.owners.create",
    "platform.subscriptions.manage",
)
ROLE_KEYS = ("OWNER", "MANAGER", "RECEPTIONIST", "ACCOUNTANT", "TRAINER")


def jwt_settings() -> dict[str, object]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return {
        "REFRESH_TOKEN_PEPPER": "platform-provisioning-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"provisioning-key": public_pem},
        "JWT_KEY_ID": "provisioning-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


def ensure_provisioning_catalog() -> SubscriptionPlan:
    for code in PLATFORM_PERMISSION_CODES:
        Permission.objects.get_or_create(
            code=code,
            defaults={
                "module": "platform",
                "description": code,
                "risk_level": Permission.Risk.CRITICAL,
            },
        )
    for key in ROLE_KEYS:
        SystemRoleTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name": key.title(),
                "description": f"{key.title()} role",
                "version": 1,
                "is_active": True,
            },
        )
    feature, _ = Feature.objects.get_or_create(
        code="members",
        defaults={"name": "Members", "description": "Members module"},
    )
    plan, _ = SubscriptionPlan.objects.get_or_create(
        code="TRIAL",
        defaults={
            "name": "Trial",
            "status": SubscriptionPlan.Status.ACTIVE,
            "price": "0.00",
            "currency": "THB",
            "billing_interval": "TRIAL",
        },
    )
    SubscriptionPlanFeature.objects.get_or_create(
        subscription_plan=plan,
        feature=feature,
        defaults={"enabled": True},
    )
    return plan


def platform_client(email: str) -> tuple[APIClient, str, SubscriptionPlan]:
    User.objects.create_superuser(email=email, password=PASSWORD)
    plan = ensure_provisioning_catalog()
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200
    return client, login.json()["data"]["access_token"], plan


def provision_gym(
    client: APIClient,
    token: str,
    plan: SubscriptionPlan,
    *,
    code: str,
    owner_email: str,
    idempotency_key: str,
    send_invitation: bool | None = None,
    owner_first_name: str = "Provisioned",
    owner_last_name: str = "Owner",
):
    owner: dict[str, object] = {
        "email": owner_email,
        "first_name": owner_first_name,
        "last_name": owner_last_name,
    }
    if send_invitation is not None:
        owner["send_invitation"] = send_invitation
    return client.post(
        "/api/v1/admin/gyms/",
        {
            "gym": {
                "code": code,
                "name": f"{code} Gym",
                "timezone": "Asia/Yangon",
                "currency": "THB",
                "subscription_plan_id": str(plan.id),
                "subscription_status": "ACTIVE",
            },
            "owner": owner,
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_IDEMPOTENCY_KEY=idempotency_key,
    )


def invitation_token(message: str) -> str:
    match = re.search(r"token=([A-Za-z0-9_-]+)", message)
    assert match is not None
    return match.group(1)


def accept_invitation(client: APIClient, token: str, password: str = PASSWORD):
    return client.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": token,
            "password": password,
            "password_confirmation": password,
        },
        format="json",
    )


def message_for(recipient: str) -> str:
    return next(message.body for message in mail.outbox if recipient in message.to)


def enable_recent_mfa(client: APIClient, access_token: str) -> None:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}
    enrollment = client.post(
        "/api/v1/auth/mfa/enroll/",
        {"current_password": PASSWORD},
        format="json",
        **headers,
    )
    assert enrollment.status_code == 200, enrollment.content
    enrollment_data = enrollment.json()["data"]
    code = _totp(enrollment_data["secret"], int(time.time()) // 30)
    confirmed = client.post(
        "/api/v1/auth/mfa/enroll/confirm/",
        {"enrollment_token": enrollment_data["enrollment_token"], "code": code},
        format="json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.content


@override_settings(**jwt_settings())
def test_platform_provisioning_is_atomic_and_idempotent() -> None:
    admin = User.objects.create_superuser(
        email="platform@example.test",
        password="correct horse battery staple",
    )
    plan = ensure_provisioning_catalog()
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {
            "email": admin.email,
            "password": "correct horse battery staple",
        },
        format="json",
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]
    body = {
        "gym": {
            "code": "PROV-01",
            "name": "Provisioned Gym",
            "timezone": "Asia/Yangon",
            "currency": "THB",
            "subscription_plan_id": str(plan.id),
            "subscription_status": "TRIAL",
        },
        "owner": {
            "email": "provisioned-owner@example.test",
            "first_name": "Provisioned",
            "last_name": "Owner",
        },
    }
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_IDEMPOTENCY_KEY": "f52030c1-18af-4e18-a1ec-50997d892ef4",
    }
    mail.outbox.clear()

    first = client.post("/api/v1/admin/gyms/", body, format="json", **headers)
    replay = client.post("/api/v1/admin/gyms/", body, format="json", **headers)

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay["Idempotency-Replayed"] == "true"
    assert replay.json()["data"] == first.json()["data"]
    assert len(mail.outbox) == 1
    gym = Gym.objects.get(code="PROV-01")
    assert StaffInvitation.objects.filter(gym=gym).count() == 1
    assert ExpenseCategory.objects.filter(gym=gym).count() == 10
    assert TenantSequence.objects.filter(gym=gym, sequence_key__startswith="RECEIPT:").count() == 1
    assert GymProvisioningRequest.objects.filter(gym=gym).count() == 1
    assert (
        PlatformAuditLog.objects.filter(
            target_gym=gym,
            action="GYM_PROVISIONED",
        ).count()
        == 1
    )


@override_settings(**jwt_settings())
def test_reused_key_with_different_body_is_rejected() -> None:
    admin = User.objects.create_superuser(
        email="platform-conflict@example.test",
        password="correct horse battery staple",
    )
    plan = ensure_provisioning_catalog()
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {
            "email": admin.email,
            "password": "correct horse battery staple",
        },
        format="json",
    )
    token = login.json()["data"]["access_token"]
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        "HTTP_IDEMPOTENCY_KEY": "8fe41bad-d545-4303-9b21-2c5e99348941",
    }
    mail.outbox.clear()
    body = {
        "gym": {
            "code": "PROV-02",
            "name": "Original Name",
            "timezone": "Asia/Yangon",
            "currency": "THB",
            "subscription_plan_id": str(plan.id),
        },
        "owner": {
            "email": "owner-two@example.test",
            "first_name": "Second",
            "last_name": "Owner",
        },
    }
    assert client.post("/api/v1/admin/gyms/", body, format="json", **headers).status_code == 201
    assert len(mail.outbox) == 1
    body["gym"]["name"] = "Changed Name"
    conflict = client.post("/api/v1/admin/gyms/", body, format="json", **headers)

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    body["gym"]["name"] = "Original Name"
    body["owner"]["send_invitation"] = False
    invitation_conflict = client.post("/api/v1/admin/gyms/", body, format="json", **headers)
    assert invitation_conflict.status_code == 409
    assert invitation_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert Gym.objects.filter(code="PROV-02").count() == 1
    assert len(mail.outbox) == 1


@override_settings(**jwt_settings())
def test_gym_lifecycle_revokes_only_affected_sessions_and_records_audit() -> None:
    password = "correct horse battery staple"
    admin = User.objects.create_superuser(
        email="platform-lifecycle@example.test",
        password=password,
    )
    plan = ensure_provisioning_catalog()
    platform_client = APIClient()
    platform_client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    platform_login = platform_client.post(
        "/api/v1/auth/login/",
        {"email": admin.email, "password": password},
        format="json",
    ).json()["data"]
    platform_headers = {
        "HTTP_AUTHORIZATION": f"Bearer {platform_login['access_token']}",
    }

    def provision(code: str, owner_email: str, key: str) -> tuple[Gym, User]:
        response = platform_client.post(
            "/api/v1/admin/gyms/",
            {
                "gym": {
                    "code": code,
                    "name": f"{code} Gym",
                    "timezone": "Asia/Yangon",
                    "currency": "THB",
                    "subscription_plan_id": str(plan.id),
                    "subscription_status": "ACTIVE",
                },
                "owner": {
                    "email": owner_email,
                    "first_name": code,
                    "last_name": "Owner",
                },
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY=key,
            **platform_headers,
        )
        assert response.status_code == 201
        gym = Gym.objects.get(code=code)
        owner = User.objects.get(normalized_email=owner_email)
        accepted = accept_invitation(
            platform_client,
            invitation_token(message_for(owner_email)),
            password,
        )
        assert accepted.status_code == 200
        activated = platform_client.post(
            f"/api/v1/admin/gyms/{gym.id}/activate/",
            {"reason": "Initial launch"},
            format="json",
            **platform_headers,
        )
        assert activated.status_code == 200
        return gym, owner

    affected_gym, affected_owner = provision(
        "LIFE-A",
        "life-a@example.test",
        "lifecycle-gym-a-key-00000001",
    )
    other_gym, other_owner = provision(
        "LIFE-B",
        "life-b@example.test",
        "lifecycle-gym-b-key-00000001",
    )

    affected_login = platform_client.post(
        "/api/v1/auth/login/",
        {"email": affected_owner.email, "password": password},
        format="json",
    ).json()["data"]
    other_login = platform_client.post(
        "/api/v1/auth/login/",
        {"email": other_owner.email, "password": password},
        format="json",
    ).json()["data"]

    suspended = platform_client.post(
        f"/api/v1/admin/gyms/{affected_gym.id}/suspend/",
        {"reason": "Payment review"},
        format="json",
        **platform_headers,
    )
    assert suspended.status_code == 200
    assert suspended.json()["data"]["status"] == Gym.Status.SUSPENDED

    affected_me = platform_client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {affected_login['access_token']}",
    )
    affected_refresh = platform_client.post(
        "/api/v1/auth/refresh/",
        {"refresh_token": affected_login["refresh_token"]},
        format="json",
    )
    other_me = platform_client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {other_login['access_token']}",
    )
    platform_me = platform_client.get("/api/v1/auth/me/", **platform_headers)

    assert affected_me.status_code == 401
    assert affected_refresh.status_code == 401
    assert other_me.status_code == 200
    assert platform_me.status_code == 200
    assert RefreshSession.objects.filter(
        selected_gym=affected_gym,
        revoked_at__isnull=False,
    ).exists()
    assert RefreshSession.objects.filter(
        selected_gym=other_gym,
        revoked_at__isnull=True,
    ).exists()

    repeated = platform_client.post(
        f"/api/v1/admin/gyms/{affected_gym.id}/suspend/",
        {"reason": "Repeated request"},
        format="json",
        **platform_headers,
    )
    assert repeated.status_code == 200
    assert (
        PlatformAuditLog.objects.filter(
            target_gym=affected_gym,
            action="GYM_SUSPENDED",
        ).count()
        == 1
    )
    reactivated = platform_client.post(
        f"/api/v1/admin/gyms/{affected_gym.id}/activate/",
        {"reason": "Payment restored"},
        format="json",
        **platform_headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["data"]["status"] == Gym.Status.ACTIVE
    relogin = platform_client.post(
        "/api/v1/auth/login/",
        {"email": affected_owner.email, "password": password},
        format="json",
    ).json()["data"]
    past_due = platform_client.patch(
        f"/api/v1/admin/gyms/{affected_gym.id}/",
        {"subscription_status": Gym.SubscriptionStatus.PAST_DUE},
        format="json",
        **platform_headers,
    )
    assert past_due.status_code == 200
    assert (
        platform_client.post(
            "/api/v1/auth/refresh/",
            {"refresh_token": relogin["refresh_token"]},
            format="json",
        ).status_code
        == 401
    )

    audit = platform_client.get(
        f"/api/v1/admin/audit-logs/?target_gym_id={affected_gym.id}",
        **platform_headers,
    )
    assert audit.status_code == 200
    assert {row["action"] for row in audit.json()["data"]} >= {
        "GYM_PROVISIONED",
        "GYM_ACTIVATED",
        "GYM_SUSPENDED",
        "GYM_UPDATED",
    }

    no_owner = Gym.objects.create(
        code="LIFE-NO-OWNER",
        name="No Owner",
        status=Gym.Status.PENDING,
        subscription_plan=plan,
        subscription_status=Gym.SubscriptionStatus.ACTIVE,
        currency=plan.currency,
    )
    denied = platform_client.post(
        f"/api/v1/admin/gyms/{no_owner.id}/activate/",
        {"reason": "Attempt activation"},
        format="json",
        **platform_headers,
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "INVALID_GYM_TRANSITION"


@override_settings(**jwt_settings())
def test_owner_invitation_acceptance_and_revoke_are_mfa_protected() -> None:
    password = "correct horse battery staple"
    admin = User.objects.create_superuser(
        email="owner-flow-platform@example.test",
        password=password,
    )
    plan = ensure_provisioning_catalog()
    api = APIClient()
    api.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    platform_login = api.post(
        "/api/v1/auth/login/",
        {"email": admin.email, "password": password},
        format="json",
    ).json()["data"]
    provisioned = api.post(
        "/api/v1/admin/gyms/",
        {
            "gym": {
                "code": "OWN-FLOW",
                "name": "Owner Flow Gym",
                "timezone": "Asia/Yangon",
                "currency": "THB",
                "subscription_plan_id": str(plan.id),
                "subscription_status": "ACTIVE",
            },
            "owner": {
                "email": "first-owner@example.test",
                "first_name": "First",
                "last_name": "Owner",
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="owner-flow-provision-key-0001",
        HTTP_AUTHORIZATION=f"Bearer {platform_login['access_token']}",
    )
    assert provisioned.status_code == 201
    first_owner = User.objects.get(normalized_email="first-owner@example.test")
    accepted_initial = accept_invitation(
        api,
        invitation_token(message_for("first-owner@example.test")),
        password,
    )
    assert accepted_initial.status_code == 200
    mail.outbox.clear()
    first_login = api.post(
        "/api/v1/auth/login/",
        {"email": first_owner.email, "password": password},
        format="json",
    ).json()["data"]
    owner_headers = {"HTTP_AUTHORIZATION": f"Bearer {first_login['access_token']}"}

    blocked = api.post(
        "/api/v1/owners/invitations/",
        {
            "email": "second-owner@example.test",
            "first_name": "Second",
            "last_name": "Owner",
        },
        format="json",
        **owner_headers,
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MFA_STEP_UP_REQUIRED"

    enrollment = api.post(
        "/api/v1/auth/mfa/enroll/",
        {"current_password": password},
        format="json",
        **owner_headers,
    )
    assert enrollment.status_code == 200
    enrollment_data = enrollment.json()["data"]
    code = _totp(enrollment_data["secret"], int(time.time()) // 30)
    confirmed = api.post(
        "/api/v1/auth/mfa/enroll/confirm/",
        {"enrollment_token": enrollment_data["enrollment_token"], "code": code},
        format="json",
        **owner_headers,
    )
    assert confirmed.status_code == 200
    challenged_login = api.post(
        "/api/v1/auth/login/",
        {"email": first_owner.email, "password": password},
        format="json",
    )
    assert challenged_login.status_code == 200
    replayed_step = api.post(
        "/api/v1/auth/mfa/challenge/",
        {
            "challenge_token": challenged_login.json()["data"]["mfa_challenge_token"],
            "code": code,
        },
        format="json",
    )
    assert replayed_step.status_code == 401

    invited = api.post(
        "/api/v1/owners/invitations/",
        {
            "email": "second-owner@example.test",
            "first_name": "Second",
            "last_name": "Owner",
        },
        format="json",
        **owner_headers,
    )
    assert invited.status_code == 201
    assert "token" not in invited.json()["data"]
    assert len(mail.outbox) == 1
    duplicate = api.post(
        "/api/v1/owners/invitations/",
        {
            "email": "second-owner@example.test",
            "first_name": "Second",
            "last_name": "Owner",
        },
        format="json",
        **owner_headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["message"] == OWNER_INVITATION_PENDING_MESSAGE
    assert duplicate.json()["data"]["id"] == invited.json()["data"]["id"]
    assert "token" not in duplicate.json()["data"]
    assert len(mail.outbox) == 1
    token_match = re.search(r"token=([A-Za-z0-9_-]+)", mail.outbox[0].body)
    assert token_match is not None

    accepted = api.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": token_match.group(1),
            "password": password,
            "password_confirmation": password,
        },
        format="json",
    )
    assert accepted.status_code == 200
    second_refresh = accepted.json()["data"]["refresh_token"]
    second_membership_id = invited.json()["data"]["gym_membership_id"]
    owners = api.get("/api/v1/owners/", **owner_headers)
    assert owners.status_code == 200
    assert {row["email"] for row in owners.json()["data"]} == {
        "first-owner@example.test",
        "second-owner@example.test",
    }

    revoked = api.post(
        f"/api/v1/owners/{second_membership_id}/revoke/",
        {"reason": "Owner access removed"},
        format="json",
        **owner_headers,
    )
    assert revoked.status_code == 200
    assert (
        api.post(
            "/api/v1/auth/refresh/",
            {"refresh_token": second_refresh},
            format="json",
        ).status_code
        == 401
    )
    own_membership_id = provisioned.json()["data"]["owner"]["gym_membership"]["id"]
    self_revoke = api.post(
        f"/api/v1/owners/{own_membership_id}/revoke/",
        {"reason": "Unsafe self revoke"},
        format="json",
        **owner_headers,
    )
    assert self_revoke.status_code == 409

    gym_id = provisioned.json()["data"]["gym"]["id"]
    actor_membership_id = provisioned.json()["data"]["owner"]["gym_membership"]["id"]

    def concurrent_invite() -> tuple[str, bool]:
        connections.close_all()
        try:
            actor = User.objects.get(id=first_owner.id)
            invitation, created = invite_owner(
                gym_id=gym_id,
                actor=actor,
                actor_membership_id=actor_membership_id,
                owner_data={
                    "email": "concurrent-owner@example.test",
                    "first_name": "Concurrent",
                    "last_name": "Owner",
                },
                request_id="req_concurrent_owner",
            )
            return str(invitation.id), created
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        concurrent_results = list(executor.map(lambda _: concurrent_invite(), range(2)))

    assert len({result[0] for result in concurrent_results}) == 1
    assert sum(1 for _, created in concurrent_results if created) == 1
    assert len(mail.outbox) == 2

    concurrent_token = re.search(r"token=([A-Za-z0-9_-]+)", mail.outbox[-1].body)
    assert concurrent_token is not None
    concurrent_accept = api.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": concurrent_token.group(1),
            "password": password,
            "password_confirmation": password,
        },
        format="json",
    )
    assert concurrent_accept.status_code == 200
    concurrent_user = User.objects.get(normalized_email="concurrent-owner@example.test")
    concurrent_membership = GymMembership.objects.get(
        gym_id=gym_id,
        user=concurrent_user,
    )

    def concurrent_revoke(actor_user_id: str, actor_id: str, target_id: str) -> bool:
        connections.close_all()
        try:
            actor = User.objects.get(id=actor_user_id)
            revoke_owner(
                gym_id=gym_id,
                target_membership_id=target_id,
                actor=actor,
                actor_membership_id=actor_id,
                reason="Concurrent owner change",
                request_id="req_concurrent_revoke",
            )
            return True
        except Exception:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                concurrent_revoke,
                str(first_owner.id),
                str(actor_membership_id),
                str(concurrent_membership.id),
            ),
            executor.submit(
                concurrent_revoke,
                str(concurrent_user.id),
                str(concurrent_membership.id),
                str(actor_membership_id),
            ),
        ]
        revoke_results = [future.result() for future in futures]

    assert sum(revoke_results) == 1
    assert (
        GymMembership.objects.filter(
            gym_id=gym_id,
            is_owner=True,
            status=GymMembership.Status.ACTIVE,
        ).count()
        == 1
    )


@override_settings(**jwt_settings())
def test_concurrent_provisioning_with_one_key_creates_one_gym() -> None:
    _client, token, plan = platform_client("concurrent-platform@example.test")
    code = "CONC-01"
    owner_email = "concurrent-provision-owner@example.test"
    idempotency_key = f"concurrent-provision-{uuid4()}"
    barrier = Barrier(2, timeout=15)

    def attempt() -> int:
        connections.close_all()
        try:
            local = APIClient()
            local.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
            barrier.wait()
            response = provision_gym(
                local,
                token,
                plan,
                code=code,
                owner_email=owner_email,
                idempotency_key=idempotency_key,
            )
            return response.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: attempt(), range(2)))

    assert 201 in statuses
    assert set(statuses) <= {201, 409}
    gym = Gym.objects.get(code=code)
    assert (
        GymProvisioningRequest.objects.filter(
            idempotency_key=idempotency_key,
            state=GymProvisioningRequest.State.COMPLETED,
            gym=gym,
        ).count()
        == 1
    )
    assert GymMembership.objects.filter(gym=gym, is_owner=True).count() == 1
    assert User.objects.filter(normalized_email=owner_email).count() == 1


@override_settings(**jwt_settings())
def test_injected_provisioning_failure_rolls_back_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token, plan = platform_client("rollback-platform@example.test")

    def explode(**_kwargs: object) -> None:
        raise RuntimeError("injected provisioning failure")

    monkeypatch.setattr("apps.tenants.services.write_platform_audit", explode)
    code = "ROLL-01"
    owner_email = "rollback-owner@example.test"
    idempotency_key = f"rollback-provision-{uuid4()}"

    response = provision_gym(
        client,
        token,
        plan,
        code=code,
        owner_email=owner_email,
        idempotency_key=idempotency_key,
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert not Gym.objects.filter(code=code).exists()
    assert not User.objects.filter(normalized_email=owner_email).exists()
    assert not GymProvisioningRequest.objects.filter(idempotency_key=idempotency_key).exists()
    assert not ExpenseCategory.objects.filter(gym__code=code).exists()
    assert not TenantSequence.objects.filter(gym__code=code).exists()


@override_settings(**jwt_settings())
def test_provisioned_seed_is_complete_and_has_no_economic_rows() -> None:
    client, token, plan = platform_client("seed-platform@example.test")
    response = provision_gym(
        client,
        token,
        plan,
        code="SEED-01",
        owner_email="seed-owner@example.test",
        idempotency_key=f"seed-provision-{uuid4()}",
    )
    assert response.status_code == 201
    gym = Gym.objects.get(code="SEED-01")
    settings = GymSettings.objects.get(gym=gym)
    assert settings.receipt_number_prefix == "RCPT"
    categories = ExpenseCategory.objects.filter(gym=gym)
    assert set(categories.values_list("system_key", "code", "is_active")) == {
        (code, code, True) for code, _name in EXPENSE_CATEGORY_CATALOG
    }
    sequence = TenantSequence.objects.get(gym=gym, sequence_key__startswith="RECEIPT:")
    assert (sequence.prefix, sequence.next_value, sequence.padding) == ("RCPT", 1, 6)
    assert set(Role.objects.filter(gym=gym).values_list("key", flat=True)) == set(ROLE_KEYS)
    assert GymFeatureEntitlement.objects.filter(gym=gym).count() == (
        SubscriptionPlanFeature.objects.filter(subscription_plan=plan).count()
    )
    assert not Member.objects.filter(gym=gym).exists()
    assert not MembershipPlan.objects.filter(gym=gym).exists()
    assert not Membership.objects.filter(gym=gym).exists()
    assert not Payment.objects.filter(gym=gym).exists()
    assert not Receipt.objects.filter(gym=gym).exists()
    assert not Notification.objects.filter(gym=gym).exists()
    payload = json.dumps(response.json()["data"])
    assert "password" not in payload
    assert "invitation_token" not in payload
    assert "token_hash" not in payload


@override_settings(**jwt_settings())
def test_platform_token_cannot_call_tenant_apis() -> None:
    client, token, _plan = platform_client("tenant-denial-platform@example.test")
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    denied = [
        client.get("/api/v1/owners/", **headers),
        client.get("/api/v1/members/", **headers),
        client.get("/api/v1/memberships/", **headers),
        client.get("/api/v1/payments/", **headers),
        client.get("/api/v1/dashboard/?range=today", **headers),
        client.post(
            "/api/v1/owners/invitations/",
            {"email": "other@example.test", "first_name": "Other", "last_name": "Owner"},
            format="json",
            **headers,
        ),
    ]
    assert [response.status_code for response in denied] == [401, 401, 401, 401, 401, 401]
    assert {response.json()["error"]["code"] for response in denied} == {"AUTHENTICATION_FAILED"}


@override_settings(**jwt_settings())
def test_invitation_replay_expiry_and_cross_gym_acceptance_are_safe() -> None:
    client, token, plan = platform_client("invite-safety-platform@example.test")
    first = provision_gym(
        client,
        token,
        plan,
        code="SAFE-A",
        owner_email="safe-a-owner@example.test",
        idempotency_key=f"safe-a-{uuid4()}",
    )
    second = provision_gym(
        client,
        token,
        plan,
        code="SAFE-B",
        owner_email="safe-b-owner@example.test",
        idempotency_key=f"safe-b-{uuid4()}",
    )
    assert first.status_code == 201
    assert second.status_code == 201
    gym_a = Gym.objects.get(code="SAFE-A")
    gym_b = Gym.objects.get(code="SAFE-B")
    actor_a = User.objects.get(normalized_email="safe-a-owner@example.test")
    actor_b = User.objects.get(normalized_email="safe-b-owner@example.test")
    membership_a = GymMembership.objects.get(gym=gym_a, user=actor_a)
    membership_b = GymMembership.objects.get(gym=gym_b, user=actor_b)
    mail.outbox.clear()

    invited_a, created_a = invite_owner(
        gym_id=gym_a.id,
        actor=actor_a,
        actor_membership_id=membership_a.id,
        owner_data={
            "email": "safe-a-second@example.test",
            "first_name": "Second",
            "last_name": "Alpha",
        },
        request_id="req_safe_a",
        platform_assisted=True,
    )
    invited_b, created_b = invite_owner(
        gym_id=gym_b.id,
        actor=actor_b,
        actor_membership_id=membership_b.id,
        owner_data={
            "email": "safe-b-second@example.test",
            "first_name": "Second",
            "last_name": "Beta",
        },
        request_id="req_safe_b",
    )
    assert created_a is True
    assert created_b is True
    token_a = invitation_token(mail.outbox[0].body)
    token_b = invitation_token(mail.outbox[1].body)
    stored_audit = json.dumps(
        list(AuditLog.objects.filter(action="OWNER_INVITED").values_list("new_values", flat=True))
    )
    stored_platform_audit = json.dumps(
        list(
            PlatformAuditLog.objects.filter(action="OWNER_INVITED").values_list(
                "new_values", flat=True
            )
        )
    )
    assert token_a not in stored_audit
    assert token_b not in stored_audit
    assert token_a not in stored_platform_audit
    assert invited_a.token_hash != token_a

    accepted = client.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": token_a,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert accepted.status_code == 200
    claims = decode_access_token(accepted.json()["data"]["access_token"])
    assert claims["gym_id"] == str(gym_a.id)
    replay = client.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": token_a,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "INVALID_INVITATION"
    assert token_a not in replay.content.decode()

    invited_b.refresh_from_db()
    beta = GymMembership.objects.get(gym=gym_b, user__normalized_email="safe-b-second@example.test")
    assert invited_b.status == StaffInvitation.Status.PENDING
    assert beta.status == GymMembership.Status.INVITED
    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        invited_b.expires_at = timezone.now() - timedelta(seconds=5)
        invited_b.save(update_fields=["expires_at", "updated_at"])
    expired = client.post(
        "/api/v1/owners/invitations/accept/",
        {
            "invitation_token": token_b,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        format="json",
    )
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "INVALID_INVITATION"
    beta.refresh_from_db()
    invited_b.refresh_from_db()
    assert beta.status == GymMembership.Status.INVITED
    assert invited_b.status == StaffInvitation.Status.PENDING
    assert GymMembership.objects.filter(gym=gym_a, is_owner=True, status="ACTIVE").count() == 2
    assert GymMembership.objects.filter(gym=gym_b, is_owner=True, status="ACTIVE").count() == 1


@override_settings(**jwt_settings())
def test_platform_audit_read_redacts_secrets() -> None:
    client, token, _plan = platform_client("audit-redaction-platform@example.test")
    actor = User.objects.get(normalized_email="audit-redaction-platform@example.test")
    PlatformAuditLog.objects.create(
        actor_user=actor,
        action="SECRET_REDACTION_PROBE",
        module="platform",
        target_type="StaffInvitation",
        target_id=uuid4(),
        old_values={"credential": "raw-credential-value"},
        new_values={
            "email": "invitee@example.test",
            "invitation_token": "raw-invitation-token",
            "password": "raw-password-value",
            "nested": {"client_secret": "raw-secret-value", "status": "PENDING"},
        },
        request_id="req_redaction",
        outcome="SUCCESS",
        occurred_at=timezone.now(),
    )

    response = client.get(
        "/api/v1/admin/audit-logs/?action=SECRET_REDACTION_PROBE",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "raw-credential-value" not in body
    assert "raw-invitation-token" not in body
    assert "raw-password-value" not in body
    assert "raw-secret-value" not in body
    row = response.json()["data"][0]
    assert row["old_values"] == {"credential": "[REDACTED]"}
    assert row["new_values"]["email"] == "invitee@example.test"
    assert row["new_values"]["invitation_token"] == "[REDACTED]"
    assert row["new_values"]["password"] == "[REDACTED]"
    assert row["new_values"]["nested"] == {"client_secret": "[REDACTED]", "status": "PENDING"}


def assert_provisioning_absent(*, code: str, idempotency_key: str) -> None:
    assert not Gym.objects.filter(code=code).exists()
    assert not GymProvisioningRequest.objects.filter(idempotency_key=idempotency_key).exists()
    assert not GymSettings.objects.filter(gym__code=code).exists()
    assert not GymMembership.objects.filter(gym__code=code).exists()
    assert not Role.objects.filter(gym__code=code).exists()
    assert not ExpenseCategory.objects.filter(gym__code=code).exists()
    assert not TenantSequence.objects.filter(gym__code=code).exists()
    assert not StaffInvitation.objects.filter(gym__code=code).exists()
    assert not AuditLog.objects.filter(gym__code=code).exists()
    assert not PlatformAuditLog.objects.filter(new_values__gym_code=code).exists()


@pytest.mark.parametrize("send_invitation", [True, None], ids=["explicit", "omitted"])
@override_settings(**jwt_settings(), APP_PUBLIC_URL="https://owners.example.test")
def test_new_owner_invitation_onboards_and_replay_does_not_resend(
    send_invitation: bool | None,
) -> None:
    suffix = "true" if send_invitation is True else "omit"
    code = f"OWN-{suffix}".upper()
    owner_email = f"initial-{suffix}@example.test"
    idempotency_key = f"initial-owner-{suffix}-{uuid4()}"
    client, token, plan = platform_client(f"initial-{suffix}-platform@example.test")
    mail.outbox.clear()

    created = provision_gym(
        client,
        token,
        plan,
        code=code,
        owner_email=owner_email,
        idempotency_key=idempotency_key,
        send_invitation=send_invitation,
    )
    explicit_replay = provision_gym(
        client,
        token,
        plan,
        code=code,
        owner_email=owner_email,
        idempotency_key=idempotency_key,
        send_invitation=True,
    )

    assert created.status_code == 201, created.content
    assert explicit_replay.status_code == 201
    assert explicit_replay["Idempotency-Replayed"] == "true"
    assert explicit_replay.json()["data"] == created.json()["data"]
    assert set(created.json()["data"]) == {
        "gym",
        "owner",
        "roles",
        "feature_entitlements",
        "expense_categories",
    }
    assert created.json()["data"]["owner"]["gym_membership"]["status"] == "ACTIVE"
    assert created.json()["data"]["owner"]["gym_membership"]["is_owner"] is True
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [owner_email]
    assert "You were invited to join" in message.body
    assert f"{code} Gym" in message.body
    assert "as an Owner" in message.body
    assert "https://owners.example.test/accept-owner-invitation?token=" in message.body
    assert message.alternatives
    html_body, html_subtype = message.alternatives[0]
    assert html_subtype == "text/html"
    assert "Accept invitation" in html_body
    assert f"{code} Gym" in html_body
    plaintext = invitation_token(message.body)
    gym = Gym.objects.get(code=code)
    invitation = StaffInvitation.objects.get(gym=gym)
    membership = GymMembership.objects.select_related("user").get(gym=gym)
    assert invitation.gym_membership_id == membership.id
    assert invitation.status == StaffInvitation.Status.PENDING
    assert invitation.token_hash == _hash_invitation_token(plaintext)
    assert plaintext not in invitation.token_hash
    assert StaffInvitationRole.objects.get(staff_invitation=invitation).role.key == "OWNER"
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert membership.authz_version == 1
    assert membership.user.has_usable_password() is False
    tenant_audit = AuditLog.objects.get(gym=gym, action="OWNER_INVITED")
    platform_audit = PlatformAuditLog.objects.get(target_gym=gym, action="GYM_PROVISIONED")
    stored = json.dumps(
        {
            "response": created.json(),
            "record": GymProvisioningRequest.objects.get(gym=gym).response_body,
            "tenant": tenant_audit.new_values,
            "platform": platform_audit.new_values,
        }
    )
    assert plaintext not in stored
    assert "email" not in tenant_audit.new_values
    assert tenant_audit.new_values["status"] == "PENDING"
    assert tenant_audit.new_values["gym_membership_id"] == str(membership.id)
    assert platform_audit.new_values["send_invitation"] is True
    assert platform_audit.new_values["owner_invitation_id"] == str(invitation.id)
    assert "email" not in platform_audit.new_values
    assert StaffInvitation.objects.filter(gym=gym).count() == 1

    denied = client.post(
        "/api/v1/auth/login/",
        {"email": owner_email, "password": PASSWORD},
        format="json",
    )
    assert denied.status_code == 401

    accepted = accept_invitation(client, plaintext)
    assert accepted.status_code == 200
    membership.refresh_from_db()
    membership.user.refresh_from_db()
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert membership.authz_version == 2
    assert membership.user.has_usable_password() is True
    login = client.post(
        "/api/v1/auth/login/",
        {"email": owner_email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200
    login_data = login.json()["data"]
    assert login_data["requires_gym_selection"] is False
    claims = decode_access_token(login_data["access_token"])
    assert claims["gym_id"] == str(gym.id)
    assert claims["gym_membership_id"] == str(membership.id)
    assert claims["authz_version"] == 2
    me = client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {login_data['access_token']}",
    )
    assert me.status_code == 200
    assert me.json()["data"]["gym"]["code"] == code
    assert me.json()["data"]["gym_membership"]["is_owner"] is True
    assert "OWNER" in me.json()["data"]["gym_membership"]["roles"]
    assert len(mail.outbox) == 1


@override_settings(**jwt_settings())
def test_send_invitation_false_rejects_new_and_unusable_without_residue() -> None:
    client, token, plan = platform_client("false-invite-platform@example.test")
    mail.outbox.clear()
    new_email = "false-new-owner@example.test"
    new_code = "FALSE-NEW"
    new_key = f"false-new-{uuid4()}"
    created = provision_gym(
        client,
        token,
        plan,
        code=new_code,
        owner_email=new_email,
        idempotency_key=new_key,
        send_invitation=False,
    )
    unusable_email = "false-unusable-owner@example.test"
    unusable = User(
        email=unusable_email,
        normalized_email=unusable_email,
        first_name="Existing",
        last_name="Owner",
        is_active=True,
    )
    unusable.set_unusable_password()
    unusable.save()
    unusable_code = "FALSE-OLD"
    unusable_key = f"false-unusable-{uuid4()}"
    linked = provision_gym(
        client,
        token,
        plan,
        code=unusable_code,
        owner_email=unusable_email,
        idempotency_key=unusable_key,
        send_invitation=False,
    )

    assert created.status_code == linked.status_code == 409
    assert created.json()["error"]["code"] == "OWNER_CREDENTIAL_REQUIRED"
    assert linked.json()["error"] == created.json()["error"]
    message = created.json()["error"]["message"].lower()
    assert "exist" not in message
    assert "unusable" not in message
    assert "not found" not in message
    assert_provisioning_absent(code=new_code, idempotency_key=new_key)
    assert_provisioning_absent(code=unusable_code, idempotency_key=unusable_key)
    assert not User.objects.filter(normalized_email=new_email).exists()
    assert User.objects.filter(pk=unusable.pk, is_active=True).exists()
    assert mail.outbox == []

    retried = provision_gym(
        client,
        token,
        plan,
        code=new_code,
        owner_email=new_email,
        idempotency_key=new_key,
        send_invitation=True,
    )
    assert retried.status_code == 201
    assert Gym.objects.filter(code=new_code).count() == 1

    inactive = User.objects.create_user(
        email="inactive-owner@example.test",
        password=PASSWORD,
        first_name="Inactive",
        last_name="Owner",
    )
    inactive.is_active = False
    inactive.save(update_fields=["is_active", "updated_at"])
    inactive_code = "FALSE-OFF"
    inactive_key = f"false-inactive-{uuid4()}"
    inactive_response = provision_gym(
        client,
        token,
        plan,
        code=inactive_code,
        owner_email=inactive.email,
        idempotency_key=inactive_key,
        send_invitation=False,
    )
    assert inactive_response.status_code == 409
    assert inactive_response.json()["error"]["code"] == "PROVISIONING_CONFLICT"
    assert inactive_response.json()["error"]["message"] != created.json()["error"]["message"]
    assert_provisioning_absent(code=inactive_code, idempotency_key=inactive_key)
    assert User.objects.filter(pk=inactive.pk, is_active=False).exists()

    platform_owner = User.objects.create_superuser(
        email="platform-owner-link@example.test",
        password=PASSWORD,
    )
    platform_code = "FALSE-PLAT"
    platform_key = f"false-platform-{uuid4()}"
    platform_response = provision_gym(
        client,
        token,
        plan,
        code=platform_code,
        owner_email=platform_owner.email,
        idempotency_key=platform_key,
    )
    assert platform_response.status_code == 422
    assert platform_response.json()["error"]["code"] == "PROVISIONING_VALIDATION_FAILED"
    assert_provisioning_absent(code=platform_code, idempotency_key=platform_key)
    assert User.objects.filter(pk=platform_owner.pk, is_platform_admin=True).exists()


@override_settings(**jwt_settings())
def test_existing_owner_keeps_password_and_selects_a_gym() -> None:
    other_password = "different horse battery staple"
    owner_email = "existing-usable-owner@example.test"
    User.objects.create_user(
        email=owner_email,
        password=PASSWORD,
        first_name="Existing",
        last_name="Owner",
    )
    client, token, plan = platform_client("existing-usable-platform@example.test")
    mail.outbox.clear()
    first = provision_gym(
        client,
        token,
        plan,
        code="USE-A",
        owner_email=owner_email,
        idempotency_key=f"usable-a-{uuid4()}",
        send_invitation=True,
    )
    assert first.status_code == 201
    assert len(mail.outbox) == 1
    gym_a = Gym.objects.get(code="USE-A")
    membership_a = GymMembership.objects.get(gym=gym_a)
    plaintext = invitation_token(message_for(owner_email))
    rejected = accept_invitation(client, plaintext, other_password)
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "INVALID_INVITATION"
    owner = User.objects.get(normalized_email=owner_email)
    membership_a.refresh_from_db()
    assert owner.check_password(PASSWORD)
    assert not owner.check_password(other_password)
    assert membership_a.status == GymMembership.Status.ACTIVE
    assert membership_a.authz_version == 1
    assert StaffInvitation.objects.get(gym=gym_a).status == StaffInvitation.Status.PENDING

    accepted = accept_invitation(client, plaintext, PASSWORD)
    assert accepted.status_code == 200
    owner.refresh_from_db()
    membership_a.refresh_from_db()
    assert owner.check_password(PASSWORD)
    assert membership_a.authz_version == 2
    assert membership_a.status == GymMembership.Status.ACTIVE

    mail.outbox.clear()
    second = provision_gym(
        client,
        token,
        plan,
        code="USE-B",
        owner_email=owner_email,
        idempotency_key=f"usable-b-{uuid4()}",
        send_invitation=False,
    )
    assert second.status_code == 201
    gym_b = Gym.objects.get(code="USE-B")
    membership_b = GymMembership.objects.get(gym=gym_b, user=owner)
    assert membership_b.status == GymMembership.Status.ACTIVE
    assert membership_b.is_owner is True
    assert not StaffInvitation.objects.filter(gym=gym_b).exists()
    assert mail.outbox == []

    login = client.post(
        "/api/v1/auth/login/",
        {"email": owner_email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200
    login_data = login.json()["data"]
    assert login_data["requires_gym_selection"] is True
    assert {item["gym_id"] for item in login_data["available_gym_memberships"]} == {
        str(gym_a.id),
        str(gym_b.id),
    }
    selected = client.post(
        "/api/v1/auth/select-gym/",
        {
            "gym_membership_id": str(membership_b.id),
            "gym_selection_ticket": login_data["gym_selection_ticket"],
        },
        format="json",
    )
    assert selected.status_code == 200
    claims = decode_access_token(selected.json()["data"]["access_token"])
    assert claims["gym_id"] == str(gym_b.id)
    assert claims["gym_membership_id"] == str(membership_b.id)
    me = client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {selected.json()['data']['access_token']}",
    )
    assert me.status_code == 200
    assert me.json()["data"]["gym"]["code"] == "USE-B"
    assert me.json()["data"]["gym_membership"]["is_owner"] is True


@override_settings(**jwt_settings())
def test_invitation_email_failure_rolls_back_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token, plan = platform_client("mail-fail-platform@example.test")
    code = "MAIL-FAIL"
    owner_email = "mail-fail-owner@example.test"
    idempotency_key = f"mail-fail-{uuid4()}"

    def fail_delivery(*_args: object, **_kwargs: object) -> None:
        raise SMTPException("mailbox unavailable")

    monkeypatch.setattr("apps.tenants.services.send_mail", fail_delivery)
    mail.outbox.clear()
    failed = provision_gym(
        client,
        token,
        plan,
        code=code,
        owner_email=owner_email,
        idempotency_key=idempotency_key,
    )

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "mailbox unavailable" not in failed.content.decode()
    assert_provisioning_absent(code=code, idempotency_key=idempotency_key)
    assert not User.objects.filter(normalized_email=owner_email).exists()
    assert mail.outbox == []

    monkeypatch.setattr("apps.tenants.services.send_mail", send_mail)
    retried = provision_gym(
        client,
        token,
        plan,
        code=code,
        owner_email=owner_email,
        idempotency_key=idempotency_key,
    )
    assert retried.status_code == 201
    assert len(mail.outbox) == 1
    assert (
        GymProvisioningRequest.objects.filter(
            idempotency_key=idempotency_key,
            state=GymProvisioningRequest.State.COMPLETED,
        ).count()
        == 1
    )


@override_settings(**jwt_settings())
def test_initial_owner_invitation_expiry_replay_and_cross_gym() -> None:
    client, token, plan = platform_client("initial-safety-platform@example.test")
    mail.outbox.clear()
    first = provision_gym(
        client,
        token,
        plan,
        code="INIT-A",
        owner_email="init-a@example.test",
        idempotency_key=f"init-a-{uuid4()}",
    )
    second = provision_gym(
        client,
        token,
        plan,
        code="INIT-B",
        owner_email="init-b@example.test",
        idempotency_key=f"init-b-{uuid4()}",
    )
    assert first.status_code == 201
    assert second.status_code == 201
    token_a = invitation_token(message_for("init-a@example.test"))
    token_b = invitation_token(message_for("init-b@example.test"))
    gym_a = Gym.objects.get(code="INIT-A")
    gym_b = Gym.objects.get(code="INIT-B")
    membership_a = GymMembership.objects.get(gym=gym_a)
    membership_b = GymMembership.objects.get(gym=gym_b)
    invitation_b = StaffInvitation.objects.get(gym=gym_b)
    assert membership_a.status == membership_b.status == GymMembership.Status.ACTIVE
    assert membership_b.authz_version == 1
    assert not User.objects.get(normalized_email="init-b@example.test").has_usable_password()

    accepted = accept_invitation(client, token_a)
    assert accepted.status_code == 200
    claims = decode_access_token(accepted.json()["data"]["access_token"])
    assert claims["gym_id"] == str(gym_a.id)
    replay = accept_invitation(client, token_a)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "INVALID_INVITATION"
    assert token_a not in replay.content.decode()
    membership_a.refresh_from_db()
    membership_b.refresh_from_db()
    invitation_b.refresh_from_db()
    assert membership_a.authz_version == 2
    assert membership_b.status == GymMembership.Status.ACTIVE
    assert membership_b.is_owner is True
    assert membership_b.authz_version == 1
    assert invitation_b.status == StaffInvitation.Status.PENDING

    with transaction.atomic():
        set_gym_context(gym_id=gym_b.id)
        invitation_b.expires_at = timezone.now() - timedelta(seconds=5)
        invitation_b.save(update_fields=["expires_at", "updated_at"])
    expired = accept_invitation(client, token_b)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "INVALID_INVITATION"
    membership_a.refresh_from_db()
    membership_b.refresh_from_db()
    invitation_b.refresh_from_db()
    assert membership_a.authz_version == 2
    assert membership_a.status == GymMembership.Status.ACTIVE
    assert membership_b.status == GymMembership.Status.ACTIVE
    assert membership_b.is_owner is True
    assert membership_b.authz_version == 1
    assert invitation_b.status == StaffInvitation.Status.PENDING
    assert not User.objects.get(normalized_email="init-b@example.test").has_usable_password()
    assert GymMembership.objects.filter(gym=gym_a, is_owner=True, status="ACTIVE").count() == 1
    assert GymMembership.objects.filter(gym=gym_b, is_owner=True, status="ACTIVE").count() == 1
    stored = json.dumps(
        list(AuditLog.objects.filter(gym__in=[gym_a, gym_b]).values_list("new_values", flat=True))
        + list(
            PlatformAuditLog.objects.filter(target_gym__in=[gym_a, gym_b]).values_list(
                "new_values", flat=True
            )
        )
    )
    assert token_a not in stored
    assert token_b not in stored


@override_settings(**jwt_settings())
def test_platform_assisted_owner_creation_requires_mfa_and_permission() -> None:
    client, token, plan = platform_client("assisted-owner-platform@example.test")
    mail.outbox.clear()
    provisioned = provision_gym(
        client,
        token,
        plan,
        code="PLAT-OWN",
        owner_email="assisted-initial-owner@example.test",
        idempotency_key=f"assisted-initial-{uuid4()}",
    )
    assert provisioned.status_code == 201
    gym_id = provisioned.json()["data"]["gym"]["id"]
    accepted = accept_invitation(
        client,
        invitation_token(message_for("assisted-initial-owner@example.test")),
    )
    assert accepted.status_code == 200
    staff_denied = client.post(
        f"/api/v1/admin/gyms/{gym_id}/owners/",
        {"email": "assisted-added@example.test", "first_name": "Added", "last_name": "Owner"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {accepted.json()['data']['access_token']}",
    )
    assert staff_denied.status_code == 401
    assert staff_denied.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    platform_headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    listed = client.get(f"/api/v1/admin/gyms/{gym_id}/owners/", **platform_headers)
    assert listed.status_code == 200, listed.content
    assert {
        row["email"] for row in listed.json()["data"]
    } == {"assisted-initial-owner@example.test"}
    blocked = client.post(
        f"/api/v1/admin/gyms/{gym_id}/owners/",
        {"email": "assisted-added@example.test", "first_name": "Added", "last_name": "Owner"},
        format="json",
        **platform_headers,
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MFA_STEP_UP_REQUIRED"

    enrollment = client.post(
        "/api/v1/auth/mfa/enroll/",
        {"current_password": PASSWORD},
        format="json",
        **platform_headers,
    )
    assert enrollment.status_code == 200
    enrollment_data = enrollment.json()["data"]
    code = _totp(enrollment_data["secret"], int(time.time()) // 30)
    confirmed = client.post(
        "/api/v1/auth/mfa/enroll/confirm/",
        {"enrollment_token": enrollment_data["enrollment_token"], "code": code},
        format="json",
        **platform_headers,
    )
    assert confirmed.status_code == 200
    Permission.objects.filter(code="platform.owners.create").update(is_active=False)
    missing_permission = client.post(
        f"/api/v1/admin/gyms/{gym_id}/owners/",
        {"email": "assisted-added@example.test", "first_name": "Added", "last_name": "Owner"},
        format="json",
        **platform_headers,
    )
    assert missing_permission.status_code == 403
    assert missing_permission.json()["error"]["code"] == "PERMISSION_DENIED"
    Permission.objects.filter(code="platform.owners.create").update(is_active=True)

    mail.outbox.clear()
    invited = client.post(
        f"/api/v1/admin/gyms/{gym_id}/owners/",
        {"email": "assisted-added@example.test", "first_name": "Added", "last_name": "Owner"},
        format="json",
        **platform_headers,
    )
    assert invited.status_code == 201
    assert "token" not in invited.json()["data"]
    assert len(mail.outbox) == 1
    added_token = invitation_token(mail.outbox[0].body)
    invitation = StaffInvitation.objects.get(id=invited.json()["data"]["id"])
    assert invitation.token_hash == _hash_invitation_token(added_token)
    assert added_token not in invitation.token_hash
    assert StaffInvitationRole.objects.get(staff_invitation=invitation).role.key == "OWNER"
    added_membership = GymMembership.objects.get(id=invitation.gym_membership_id)
    assert added_membership.status == GymMembership.Status.INVITED
    assert added_membership.is_owner is True
    assert (
        GymMembership.objects.filter(
            gym_id=gym_id,
            is_owner=True,
            status=GymMembership.Status.ACTIVE,
        ).count()
        == 1
    )
    stored = json.dumps(
        list(AuditLog.objects.filter(target_id=invitation.id).values_list("new_values", flat=True))
        + list(
            PlatformAuditLog.objects.filter(target_id=invitation.id).values_list(
                "new_values", flat=True
            )
        )
    )
    assert added_token not in stored
    duplicate = client.post(
        f"/api/v1/admin/gyms/{gym_id}/owners/",
        {"email": "assisted-added@example.test", "first_name": "Added", "last_name": "Owner"},
        format="json",
        **platform_headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["message"] == OWNER_INVITATION_PENDING_MESSAGE
    assert duplicate.json()["data"]["id"] == invited.json()["data"]["id"]
    assert "token" not in duplicate.json()["data"]
    assert len(mail.outbox) == 1


def _owner_invite_body(email: str) -> dict[str, str]:
    return {"email": email, "first_name": "Recovered", "last_name": "Owner"}


@override_settings(**jwt_settings())
def test_platform_reissues_expired_owner_credential_without_changing_authority() -> None:
    cache.clear()
    locked_email = "locked-owner@example.test"
    helper_email = "helper-owner@example.test"
    client, platform_token, plan = platform_client("recovery-platform@example.test")
    enable_recent_mfa(client, platform_token)
    platform_headers = {"HTTP_AUTHORIZATION": f"Bearer {platform_token}"}
    mail.outbox.clear()
    provisioned = provision_gym(
        client,
        platform_token,
        plan,
        code="RECOV-1",
        owner_email=locked_email,
        idempotency_key=f"recover-initial-{uuid4()}",
    )
    assert provisioned.status_code == 201
    original_token = invitation_token(message_for(locked_email))
    gym = Gym.objects.get(code="RECOV-1")
    membership = GymMembership.objects.get(gym=gym, user__normalized_email=locked_email)
    original = StaffInvitation.objects.get(gym=gym, normalized_email=locked_email)
    original_hash = original.token_hash
    original_expiry = original.expires_at
    authz_version = membership.authz_version
    role_ids = list(
        membership.gymmembershiprole_set.order_by("id").values_list("role_id", flat=True)
    )
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert role_ids

    mail.outbox.clear()
    pending = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(locked_email),
        format="json",
        **platform_headers,
    )
    assert pending.status_code == 200
    assert pending.json()["message"] == OWNER_INVITATION_PENDING_MESSAGE
    assert pending.json()["data"]["id"] == str(original.id)
    assert "token" not in pending.json()["data"]
    assert mail.outbox == []
    original.refresh_from_db()
    membership.refresh_from_db()
    assert original.token_hash == original_hash
    assert original.expires_at == original_expiry
    assert original.status == StaffInvitation.Status.PENDING
    assert membership.authz_version == authz_version

    invited_helper = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(helper_email),
        format="json",
        **platform_headers,
    )
    assert invited_helper.status_code == 201
    assert invited_helper.json()["message"] == "Owner invitation created"
    helper_accepted = accept_invitation(client, invitation_token(message_for(helper_email)))
    assert helper_accepted.status_code == 200
    helper_login = client.post(
        "/api/v1/auth/login/",
        {"email": helper_email, "password": PASSWORD},
        format="json",
    )
    assert helper_login.status_code == 200
    assert helper_login.json()["data"]["requires_gym_selection"] is False
    helper_token = helper_login.json()["data"]["access_token"]
    enable_recent_mfa(client, helper_token)
    helper_headers = {"HTTP_AUTHORIZATION": f"Bearer {helper_token}"}

    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original.expires_at = timezone.now() - timedelta(seconds=30)
        original.save(update_fields=["expires_at", "updated_at"])
    mail.outbox.clear()
    tenant_locked = client.post(
        "/api/v1/owners/invitations/",
        _owner_invite_body(locked_email),
        format="json",
        **helper_headers,
    )
    tenant_helper = client.post(
        "/api/v1/owners/invitations/",
        _owner_invite_body(helper_email),
        format="json",
        **helper_headers,
    )
    assert tenant_locked.status_code == tenant_helper.status_code == 409
    assert tenant_locked.json()["error"]["code"] == "OWNER_WORKFLOW_CONFLICT"
    assert tenant_locked.json()["error"]["message"] == tenant_helper.json()["error"]["message"]
    assert "active Owner" in tenant_locked.json()["error"]["message"]
    assert "password" not in tenant_locked.json()["error"]["message"].lower()
    assert mail.outbox == []
    assert StaffInvitation.objects.filter(gym=gym, normalized_email=locked_email).count() == 1
    original.refresh_from_db()
    assert original.status == StaffInvitation.Status.PENDING
    assert original.token_hash == original_hash

    usable = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(helper_email),
        format="json",
        **platform_headers,
    )
    assert usable.status_code == 409
    assert usable.json()["error"]["code"] == "OWNER_WORKFLOW_CONFLICT"
    assert usable.json()["error"]["message"] == tenant_locked.json()["error"]["message"]
    assert not StaffInvitation.objects.filter(
        gym=gym,
        normalized_email=helper_email,
        status=StaffInvitation.Status.PENDING,
    ).exists()

    recovered = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(locked_email),
        format="json",
        **platform_headers,
    )
    assert recovered.status_code == 201
    assert recovered.json()["message"] == "Owner invitation created"
    assert recovered.json()["data"]["id"] != str(original.id)
    assert "token" not in recovered.json()["data"]
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [locked_email]
    replacement = StaffInvitation.objects.get(id=recovered.json()["data"]["id"])
    plaintext = invitation_token(mail.outbox[0].body)
    assert replacement.token_hash == _hash_invitation_token(plaintext)
    assert replacement.token_hash != original_hash
    assert replacement.gym_membership_id == membership.id
    assert replacement.status == StaffInvitation.Status.PENDING
    assert StaffInvitationRole.objects.get(staff_invitation=replacement).role.key == "OWNER"
    original.refresh_from_db()
    membership.refresh_from_db()
    assert original.status == StaffInvitation.Status.EXPIRED
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert membership.authz_version == authz_version
    assert (
        list(membership.gymmembershiprole_set.order_by("id").values_list("role_id", flat=True))
        == role_ids
    )
    assert not membership.user.has_usable_password()
    repeated = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(locked_email),
        format="json",
        **platform_headers,
    )
    assert repeated.status_code == 200
    assert repeated.json()["message"] == OWNER_INVITATION_PENDING_MESSAGE
    assert repeated.json()["data"]["id"] == str(replacement.id)
    replacement.refresh_from_db()
    assert replacement.token_hash == _hash_invitation_token(plaintext)
    assert len(mail.outbox) == 1
    stored = json.dumps(
        list(AuditLog.objects.filter(target_id=replacement.id).values_list("new_values", flat=True))
        + list(
            PlatformAuditLog.objects.filter(target_id=replacement.id).values_list(
                "new_values",
                flat=True,
            )
        )
    )
    assert plaintext not in stored
    assert original_token not in stored
    assert '"invitation_reissue": true' in stored

    stale = accept_invitation(client, original_token)
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "INVALID_INVITATION"
    accepted = accept_invitation(client, plaintext)
    assert accepted.status_code == 200
    membership.refresh_from_db()
    assert membership.authz_version == authz_version + 1
    assert membership.user.has_usable_password()
    login = client.post(
        "/api/v1/auth/login/",
        {"email": locked_email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200
    assert login.json()["data"]["requires_gym_selection"] is False
    claims = decode_access_token(login.json()["data"]["access_token"])
    assert claims["gym_id"] == str(gym.id)
    assert claims["gym_membership_id"] == str(membership.id)
    me = client.get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {login.json()['data']['access_token']}",
    )
    assert me.status_code == 200
    assert me.json()["data"]["gym"]["code"] == "RECOV-1"
    assert me.json()["data"]["gym_membership"]["is_owner"] is True

    outside = provision_gym(
        client,
        platform_token,
        plan,
        code="RECOV-B",
        owner_email="outside-owner@example.test",
        idempotency_key=f"recover-outside-{uuid4()}",
    )
    assert outside.status_code == 201
    outside_membership = GymMembership.objects.get(
        gym__code="RECOV-B",
        user__normalized_email="outside-owner@example.test",
    )
    outside_authz = outside_membership.authz_version
    mail.outbox.clear()
    fresh = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body("brand-new-owner@example.test"),
        format="json",
        **platform_headers,
    )
    linked = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body("outside-owner@example.test"),
        format="json",
        **platform_headers,
    )
    assert fresh.status_code == linked.status_code == 201
    assert fresh.json()["message"] == linked.json()["message"] == "Owner invitation created"
    assert "RECOV-B" not in linked.content.decode()
    outside_membership.refresh_from_db()
    assert outside_membership.authz_version == outside_authz
    assert outside_membership.status == GymMembership.Status.ACTIVE

    inactive = User.objects.create_user(
        email="inactive-recovery@example.test",
        password=PASSWORD,
    )
    inactive.is_active = False
    inactive.save(update_fields=["is_active", "updated_at"])
    platform_identity = User.objects.create_superuser(
        email="platform-identity-recovery@example.test",
        password=PASSWORD,
    )
    inactive_response = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(inactive.email),
        format="json",
        **platform_headers,
    )
    platform_denied = client.post(
        f"/api/v1/admin/gyms/{gym.id}/owners/",
        _owner_invite_body(platform_identity.email),
        format="json",
        **platform_headers,
    )
    assert inactive_response.status_code == platform_denied.status_code == 409
    assert inactive_response.json()["error"]["code"] == "OWNER_WORKFLOW_CONFLICT"
    assert (
        inactive_response.json()["error"]["message"] == platform_denied.json()["error"]["message"]
    )
    assert "cannot be linked" in inactive_response.json()["error"]["message"]


@override_settings(**jwt_settings())
def test_concurrent_owner_credential_recovery_sends_one_invitation() -> None:
    cache.clear()
    _client, token, plan = platform_client("concurrent-recovery-platform@example.test")
    local = APIClient()
    local.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    mail.outbox.clear()
    provisioned = provision_gym(
        local,
        token,
        plan,
        code="RECOV-C",
        owner_email="concurrent-locked-owner@example.test",
        idempotency_key=f"recover-concurrent-{uuid4()}",
    )
    assert provisioned.status_code == 201
    gym = Gym.objects.get(code="RECOV-C")
    membership = GymMembership.objects.get(gym=gym)
    authz_version = membership.authz_version
    original = StaffInvitation.objects.get(gym=gym)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        original.expires_at = timezone.now() - timedelta(seconds=30)
        original.save(update_fields=["expires_at", "updated_at"])
    actor_id = User.objects.get(normalized_email="concurrent-recovery-platform@example.test").id
    mail.outbox.clear()

    def recover() -> tuple[str, bool]:
        connections.close_all()
        try:
            actor = User.objects.get(id=actor_id)
            invitation, created = invite_owner(
                gym_id=gym.id,
                actor=actor,
                actor_membership_id=None,
                owner_data=_owner_invite_body("concurrent-locked-owner@example.test"),
                request_id="req_owner_recovery",
                platform_assisted=True,
            )
            return str(invitation.id), created
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: recover(), range(2)))

    assert len({invitation_id for invitation_id, _created in results}) == 1
    assert sum(1 for _invitation_id, created in results if created) == 1
    assert len(mail.outbox) == 1
    pending = StaffInvitation.objects.get(gym=gym, status=StaffInvitation.Status.PENDING)
    assert str(pending.id) == results[0][0]
    assert pending.gym_membership_id == membership.id
    plaintext = invitation_token(mail.outbox[0].body)
    assert pending.token_hash == _hash_invitation_token(plaintext)
    original.refresh_from_db()
    membership.refresh_from_db()
    assert original.status == StaffInvitation.Status.EXPIRED
    assert membership.status == GymMembership.Status.ACTIVE
    assert membership.is_owner is True
    assert membership.authz_version == authz_version
    stored = json.dumps(
        list(AuditLog.objects.filter(target_id=pending.id).values_list("new_values", flat=True))
        + list(
            PlatformAuditLog.objects.filter(target_id=pending.id).values_list(
                "new_values",
                flat=True,
            )
        )
    )
    assert plaintext not in stored


@override_settings(**jwt_settings())
def test_owner_can_remove_a_pending_invitation_from_the_list() -> None:
    client, token, plan = platform_client("cancel-invite-platform@example.test")
    mail.outbox.clear()
    created = provision_gym(
        client,
        token,
        plan,
        code="OWN-CANCEL",
        owner_email="cancel-owner@example.test",
        idempotency_key="cancel-invite-provision-0001",
    )
    assert created.status_code == 201
    accepted = accept_invitation(
        client,
        invitation_token(message_for("cancel-owner@example.test")),
    )
    assert accepted.status_code == 200
    owner_token = accepted.json()["data"]["access_token"]
    enable_recent_mfa(client, owner_token)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {owner_token}"}
    mail.outbox.clear()

    invited = client.post(
        "/api/v1/owners/invitations/",
        {
            "email": "wrong-owner@example.test",
            "first_name": "Wrong",
            "last_name": "Address",
        },
        format="json",
        **headers,
    )
    assert invited.status_code == 201
    plaintext = invitation_token(mail.outbox[0].body)
    listed = client.get("/api/v1/owners/", **headers)
    assert listed.status_code == 200
    emails = {item["email"] for item in listed.json()["data"]}
    assert "wrong-owner@example.test" in emails
    wrong = next(
        item for item in listed.json()["data"] if item["email"] == "wrong-owner@example.test"
    )

    removed = client.post(
        f"/api/v1/owners/{wrong['gym_membership_id']}/cancel-invitation/",
        format="json",
        **headers,
    )
    assert removed.status_code == 200
    assert removed.json()["message"] == "Owner invitation removed"

    after = client.get("/api/v1/owners/", **headers)
    assert "wrong-owner@example.test" not in {item["email"] for item in after.json()["data"]}
    invitation = StaffInvitation.objects.get(id=invited.json()["data"]["id"])
    assert invitation.status == StaffInvitation.Status.REVOKED
    rejected = accept_invitation(client, plaintext)
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "INVALID_INVITATION"

    again = client.post(
        "/api/v1/owners/invitations/",
        {
            "email": "wrong-owner@example.test",
            "first_name": "Wrong",
            "last_name": "Address",
        },
        format="json",
        **headers,
    )
    assert again.status_code == 201
    assert again.json()["data"]["id"] != invited.json()["data"]["id"]
