"""Platform AI prices, charge snapshots, and per-gym assistant entitlement."""

from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from apps.assistant.dispatch import execute_offered_read
from apps.assistant.entitlement import AssistantNotEntitled
from apps.assistant.execution import ExecutionRejected
from apps.common.db.tenant_context import set_gym_context
from apps.tenants.models import (
    Gym,
    GymMembership,
    GymSubscriptionCharge,
    Permission,
    SubscriptionPlan,
)
from apps.users.models import User
from apps.users.services import create_staff_session

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRESQL_TESTS") != "1",
        reason="Set RUN_POSTGRESQL_TESTS=1 with PostgreSQL migration credentials",
    ),
]

PASSWORD = "correct horse battery staple"
BFF_SECRET = "test-bff-shared-secret-32-chars-min"
PLATFORM_PERMISSIONS = (
    "platform.gyms.view",
    "platform.gyms.create",
    "platform.gyms.update",
    "platform.owners.create",
    "platform.subscriptions.manage",
)


def jwt_settings() -> dict[str, object]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return {
        "REFRESH_TOKEN_PEPPER": "platform-ai-subscription-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"platform-ai-key": public_pem},
        "JWT_KEY_ID": "platform-ai-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


def grant_platform_permissions() -> None:
    for code in PLATFORM_PERMISSIONS:
        Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "platform",
                "description": code,
                "risk_level": Permission.Risk.CRITICAL,
                "is_active": True,
            },
        )


def platform_client(email: str) -> tuple[APIClient, str]:
    admin = User.objects.create_superuser(email=email, password=PASSWORD)
    client = APIClient()
    client.defaults["HTTP_X_GYM_BFF_SECRET"] = BFF_SECRET
    login = client.post(
        "/api/v1/auth/login/",
        {"email": admin.email, "password": PASSWORD},
        format="json",
    )
    assert login.status_code == 200, login.content
    return client, login.json()["data"]["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def sell(
    client: APIClient,
    token: str,
    *,
    plan: SubscriptionPlan,
    code: str,
    email: str,
    ai_enabled: bool,
    key: str,
) -> dict[str, object]:
    User.objects.create_user(email, PASSWORD)
    response = client.post(
        "/api/v1/admin/gyms/",
        {
            "gym": {
                "code": code,
                "name": f"{code} Gym",
                "timezone": "Asia/Yangon",
                "currency": "THB",
                "subscription_plan_id": str(plan.id),
                "subscription_status": "ACTIVE",
                "ai_enabled": ai_enabled,
            },
            "owner": {
                "email": email,
                "first_name": code,
                "last_name": "Owner",
                "send_invitation": False,
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
        **auth(token),
    )
    assert response.status_code == 201, response.content
    body = response.json()["data"]
    assert isinstance(body, dict)
    return body


def staff_token(code: str) -> str:
    gym = Gym.objects.get(code=code)
    with transaction.atomic():
        set_gym_context(gym_id=gym.id)
        membership = GymMembership.objects.select_related("user").get(gym=gym, is_owner=True)
    return create_staff_session(user=membership.user, membership=membership).access_token


def charges(gym: Gym) -> list[GymSubscriptionCharge]:
    return list(GymSubscriptionCharge.objects.filter(gym=gym).order_by("created_at", "id"))


@override_settings(**jwt_settings())
def test_dual_prices_are_snapshotted_and_ai_access_is_per_gym() -> None:
    grant_platform_permissions()
    client, token = platform_client("ai-prices@example.test")
    plan = SubscriptionPlan.objects.create(
        code="AI-MONTH",
        name="AI Monthly",
        status=SubscriptionPlan.Status.ACTIVE,
        price=Decimal("0.00"),
        currency="THB",
        billing_interval="MONTHLY",
    )
    priced = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"price": "30.00", "price_with_ai": "45.00"},
        format="json",
        **auth(token),
    )
    assert priced.status_code == 200, priced.content
    assert priced.json()["data"]["price"] == "30.00"
    assert priced.json()["data"]["price_with_ai"] == "45.00"

    bare = SubscriptionPlan.objects.create(
        code="AI-BARE",
        name="AI Bare",
        status=SubscriptionPlan.Status.ACTIVE,
        price=Decimal("10.00"),
        currency="THB",
        billing_interval="MONTHLY",
    )
    User.objects.create_user("ai-bare-owner@example.test", PASSWORD)
    rejected = client.post(
        "/api/v1/admin/gyms/",
        {
            "gym": {
                "code": "AIBARE",
                "name": "Bare Gym",
                "timezone": "Asia/Yangon",
                "currency": "THB",
                "subscription_plan_id": str(bare.id),
                "subscription_status": "ACTIVE",
                "ai_enabled": True,
            },
            "owner": {
                "email": "ai-bare-owner@example.test",
                "first_name": "Bare",
                "last_name": "Owner",
                "send_invitation": False,
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="ai-bare-sale-0000001",
        **auth(token),
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "PROVISIONING_VALIDATION_FAILED"
    assert Gym.objects.filter(code="AIBARE").count() == 0

    without_ai = sell(
        client,
        token,
        plan=plan,
        code="AIOFF",
        email="aioff-owner@example.test",
        ai_enabled=False,
        key="ai-off-sale-00000001",
    )
    with_ai = sell(
        client,
        token,
        plan=plan,
        code="AION",
        email="aion-owner@example.test",
        ai_enabled=True,
        key="ai-on-sale-000000001",
    )
    assert without_ai["gym"]["ai_enabled"] is False
    assert with_ai["gym"]["ai_enabled"] is True
    off = Gym.objects.get(code="AIOFF")
    on = Gym.objects.get(code="AION")
    off_sale = charges(off)[0]
    on_sale = charges(on)[0]
    assert off_sale.kind == GymSubscriptionCharge.Kind.SALE
    assert off_sale.ai_enabled is False
    assert off_sale.charged_amount == Decimal("30.00")
    assert off_sale.selected_price == Decimal("30.00")
    assert off_sale.discount_amount == Decimal("0.00")
    assert off_sale.payment_reference == ""
    assert on_sale.charged_amount == Decimal("45.00")
    assert on_sale.ai_enabled is True
    assert on_sale.price_without_ai == Decimal("30.00")
    assert on_sale.price_with_ai == Decimal("45.00")

    revised = client.patch(
        f"/api/v1/admin/subscriptions/{plan.id}/",
        {"price": "80.00", "price_with_ai": "120.00"},
        format="json",
        **auth(token),
    )
    assert revised.status_code == 200
    off_sale.refresh_from_db()
    on_sale.refresh_from_db()
    assert off_sale.charged_amount == Decimal("30.00")
    assert on_sale.charged_amount == Decimal("45.00")
    assert on_sale.price_with_ai == Decimal("45.00")

    ends_at = timezone.now() + timedelta(days=30)
    renewed = client.patch(
        f"/api/v1/admin/gyms/{on.id}/",
        {"subscription_ends_at": ends_at.isoformat()},
        format="json",
        **auth(token),
    )
    assert renewed.status_code == 200, renewed.content
    on_rows = charges(on)
    assert [row.kind for row in on_rows] == [
        GymSubscriptionCharge.Kind.SALE,
        GymSubscriptionCharge.Kind.RENEWAL,
    ]
    assert on_rows[0].charged_amount == Decimal("45.00")
    assert on_rows[1].charged_amount == Decimal("120.00")
    assert on_rows[1].ai_enabled is True
    assert renewed.json()["data"]["last_charged_amount"] == "120.00"

    enabled = client.patch(
        f"/api/v1/admin/gyms/{off.id}/",
        {"ai_enabled": True},
        format="json",
        **auth(token),
    )
    assert enabled.status_code == 200, enabled.content
    assert enabled.json()["data"]["ai_enabled"] is True
    assert enabled.json()["data"]["effective_price"] == "120.00"
    assert enabled.json()["data"]["last_charged_amount"] == "30.00"
    off_rows = charges(off)
    assert off_rows[0].charged_amount == Decimal("30.00")
    assert off_rows[1].kind == GymSubscriptionCharge.Kind.ENTITLEMENT_CHANGE
    assert off_rows[1].charged_amount == Decimal("0.00")
    assert off_rows[1].ai_enabled is True
    on.refresh_from_db()
    assert on.ai_enabled is True

    disabled = client.patch(
        f"/api/v1/admin/gyms/{on.id}/",
        {"ai_enabled": False},
        format="json",
        **auth(token),
    )
    assert disabled.status_code == 200, disabled.content
    assert disabled.json()["data"]["ai_enabled"] is False
    assert charges(on)[1].charged_amount == Decimal("120.00")
    assert charges(on)[2].kind == GymSubscriptionCharge.Kind.ENTITLEMENT_CHANGE
    assert charges(on)[2].charged_amount == Decimal("0.00")
    off.refresh_from_db()
    assert off.ai_enabled is True

    enabled_list = client.get(
        "/api/v1/admin/gyms/?search=AIOFF&ai=enabled",
        **auth(token),
    )
    disabled_list = client.get(
        "/api/v1/admin/gyms/?search=AION&ai=disabled",
        **auth(token),
    )
    assert enabled_list.status_code == 200
    assert {row["code"] for row in enabled_list.json()["data"]} == {"AIOFF"}
    assert {row["code"] for row in disabled_list.json()["data"]} == {"AION"}
    assert enabled_list.json()["data"][0]["ai_enabled"] is True

    off_headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token('AIOFF')}"}
    on_headers = {"HTTP_AUTHORIZATION": f"Bearer {staff_token('AION')}"}
    off_status = client.get("/api/v1/assistant/status/", **off_headers)
    on_status = client.get("/api/v1/assistant/status/", **on_headers)
    assert off_status.status_code == 200
    assert "not_entitled" not in off_status.json()["data"]["reasons"]
    assert on_status.status_code == 200
    assert on_status.json()["data"]["available"] is False
    assert "not_entitled" in on_status.json()["data"]["reasons"]
    blocked = client.post(
        "/api/v1/assistant/turns/",
        {"message": "hello", "gym_id": str(off.id), "ai_enabled": True},
        format="json",
        **on_headers,
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "AI_NOT_ENTITLED"
    conversations = client.get("/api/v1/assistant/conversations/", **on_headers)
    assert conversations.status_code == 403
    assert conversations.json()["error"]["code"] == "AI_NOT_ENTITLED"
    profile = client.get("/api/v1/gym/", **on_headers)
    if profile.status_code == 403:
        assert profile.json()["error"]["code"] == "PERMISSION_DENIED"
    else:
        assert profile.status_code == 200, profile.content
        assert profile.json()["data"]["code"] == "AION"

    on.refresh_from_db()
    request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    request.gym = on
    request.gym_membership = GymMembership.objects.get(gym=on, is_owner=True)
    request.user = request.gym_membership.user
    request.permission_codes = frozenset({"pt.sales.view"})
    with pytest.raises(AssistantNotEntitled):
        execute_offered_read(
            request=request,
            view=APIView(),
            tool_name="search_pt_trainer_sales",
            payload={"gym_id": str(off.id)},
        )

    off.refresh_from_db()
    allowed = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    allowed.gym = off
    allowed.gym_membership = GymMembership.objects.get(gym=off, is_owner=True)
    allowed.user = allowed.gym_membership.user
    allowed.permission_codes = frozenset({"pt.sales.view"})
    with pytest.raises(ExecutionRejected) as mismatch:
        execute_offered_read(
            request=allowed,
            view=APIView(),
            tool_name="search_pt_trainer_sales",
            payload={"gym_id": str(on.id)},
        )
    assert mismatch.value.code == "gym_mismatch"

    with transaction.atomic():
        set_gym_context(gym_id=off.id)
        limited_user = User.objects.create_user("aioff-trainer@example.test", PASSWORD)
        limited = GymMembership.objects.create(
            gym=off,
            user=limited_user,
            status=GymMembership.Status.ACTIVE,
            is_owner=False,
            joined_date=timezone.localdate(),
            activated_at=timezone.now(),
        )
    limited_request = Request(APIRequestFactory().post("/api/v1/assistant/internal/"))
    limited_request.gym = off
    limited_request.gym_membership = limited
    limited_request.user = limited_user
    limited_request.permission_codes = frozenset()
    with pytest.raises(PermissionDenied):
        execute_offered_read(
            request=limited_request,
            view=APIView(),
            tool_name="search_pt_trainer_sales",
            payload={},
        )

    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            off_sale.charged_amount = Decimal("1.00")
            off_sale.save(update_fields=["charged_amount", "updated_at"])

    with pytest.raises(DatabaseError, match="subscription"):
        with transaction.atomic(), connection.cursor() as cursor:
            set_gym_context(gym_id=on.id)
            cursor.execute("SET LOCAL ROLE gym_runtime")
            cursor.execute(
                "UPDATE tenants_gym SET ai_enabled = true WHERE id = %s",
                [on.id],
            )
