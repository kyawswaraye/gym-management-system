from __future__ import annotations

import importlib
import os
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.apps import apps as django_apps
from django.db import migrations
from django.test import override_settings
from rest_framework.test import APIClient

from apps.tenants.models import Permission, SystemRoleTemplatePermission
from apps.users.models import User

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
RETIRED_CODE = "platform.settings.manage"
LIVE_PLATFORM_CODES = (
    "platform.dashboard.view",
    "platform.gyms.view",
    "platform.subscriptions.manage",
    "platform.audit.view",
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
        "REFRESH_TOKEN_PEPPER": "retired-platform-settings-pepper",
        "JWT_PRIVATE_KEY": private_pem,
        "JWT_PUBLIC_KEYS": {"retired-platform-settings-key": public_pem},
        "JWT_KEY_ID": "retired-platform-settings-key",
        "BFF_SHARED_SECRET": BFF_SECRET,
    }


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


def test_fresh_seed_then_retirement_is_inactive_and_idempotent() -> None:
    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    retirement = importlib.import_module(
        "apps.tenants.migrations.0020_retire_platform_settings_manage"
    )
    seed.seed(django_apps, None)
    permission = Permission.objects.get(code=RETIRED_CODE)
    assert permission.is_active is True
    original = (permission.id, permission.module, permission.risk_level, permission.code)

    retirement.retire_platform_settings_manage(django_apps, None)
    permission.refresh_from_db()
    assert permission.is_active is False
    assert permission.description == retirement.RETIRED_DESCRIPTION
    assert (permission.id, permission.module, permission.risk_level, permission.code) == original
    assert not SystemRoleTemplatePermission.objects.filter(permission=permission).exists()
    assert Permission.objects.get(code="platform.dashboard.view").is_active is True
    assert Permission.objects.get(code="settings.manage").is_active is True

    permission.description = RETIRED_CODE
    permission.is_active = True
    permission.save(update_fields=["description", "is_active", "updated_at"])
    retirement.retire_platform_settings_manage(django_apps, None)
    permission.refresh_from_db()
    assert permission.is_active is False
    assert permission.description == retirement.RETIRED_DESCRIPTION
    assert Permission.objects.get(code="platform.dashboard.view").is_active is True

    permission.delete()
    retirement.retire_platform_settings_manage(django_apps, None)
    assert not Permission.objects.filter(code=RETIRED_CODE).exists()
    retirement.Migration.operations[0].reverse_code(django_apps, None)
    assert migrations.RunPython.noop is retirement.Migration.operations[0].reverse_code
    assert not Permission.objects.filter(code=RETIRED_CODE).exists()


@override_settings(**jwt_settings())
def test_retired_code_is_absent_from_platform_sessions_and_cannot_substitute() -> None:
    seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")
    retirement = importlib.import_module(
        "apps.tenants.migrations.0020_retire_platform_settings_manage"
    )
    seed.seed(django_apps, None)
    retirement.retire_platform_settings_manage(django_apps, None)
    assert Permission.objects.filter(code=RETIRED_CODE, is_active=False).exists()

    client, token = platform_client("retired-settings@example.test")
    headers = auth(token)
    issued = client.get("/api/v1/auth/me/", **headers)
    assert issued.status_code == 200, issued.json()
    issued_codes = issued.json()["data"]["permissions"]
    assert RETIRED_CODE not in issued_codes
    assert set(LIVE_PLATFORM_CODES) <= set(issued_codes)

    Permission.objects.filter(code__startswith="platform.").update(is_active=False)
    Permission.objects.filter(code=RETIRED_CODE).update(is_active=True)
    stale = client.get("/api/v1/auth/me/", **headers)
    assert stale.status_code == 200, stale.json()
    assert stale.json()["data"]["permissions"] == [RETIRED_CODE]

    gym_id = uuid4()
    plan_id = uuid4()
    denied = {
        "dashboard": client.get("/api/v1/admin/dashboard/", **headers),
        "gyms": client.get("/api/v1/admin/gyms/", **headers),
        "gym_detail": client.get(f"/api/v1/admin/gyms/{gym_id}/", **headers),
        "gym_update": client.patch(
            f"/api/v1/admin/gyms/{gym_id}/",
            {"name": "Nope"},
            format="json",
            **headers,
        ),
        "activate": client.post(
            f"/api/v1/admin/gyms/{gym_id}/activate/",
            {"reason": "Retired permission must not activate a gym."},
            format="json",
            **headers,
        ),
        "suspend": client.post(
            f"/api/v1/admin/gyms/{gym_id}/suspend/",
            {"reason": "Retired permission must not suspend a gym."},
            format="json",
            **headers,
        ),
        "subscriptions": client.get("/api/v1/admin/subscriptions/", **headers),
        "subscription_update": client.patch(
            f"/api/v1/admin/subscriptions/{plan_id}/",
            {"name": "Nope"},
            format="json",
            **headers,
        ),
        "audit": client.get("/api/v1/admin/audit-logs/", **headers),
    }
    for name, response in denied.items():
        assert response.status_code == 403, (name, response.status_code, response.content)
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    settings_read = client.get("/api/v1/settings/", **headers)
    settings_write = client.patch(
        "/api/v1/settings/",
        {"week_starts_on": "MONDAY"},
        format="json",
        **headers,
    )
    assert settings_read.status_code == 401
    assert settings_read.json()["error"]["code"] == "AUTHENTICATION_FAILED"
    assert settings_write.status_code == 401
    assert settings_write.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    Permission.objects.filter(code=RETIRED_CODE).update(is_active=False)
    assert Permission.objects.filter(code=RETIRED_CODE).exists()
    omitted = client.get("/api/v1/auth/me/", **headers)
    assert omitted.status_code == 200, omitted.json()
    assert RETIRED_CODE not in omitted.json()["data"]["permissions"]
    assert omitted.json()["data"]["permissions"] == []
