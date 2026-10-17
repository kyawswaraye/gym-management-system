from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from apps.common.api.exceptions import AccessTokenExpired, AuthenticationFailed
from apps.users.authentication import PlatformJWTAuthentication, StaffOrPlatformJWTAuthentication
from apps.users.models import RefreshSession
from apps.users.permissions import RequiresPlatformPermission
from apps.users.services import TokenPair
from apps.users.views import LoginView


def test_platform_permission_requires_platform_principal_and_namespace() -> None:
    permission = RequiresPlatformPermission()
    platform_request = SimpleNamespace(
        principal_type=RefreshSession.PrincipalType.PLATFORM,
        permission_codes=frozenset({"platform.gyms.view"}),
    )
    tenant_request = SimpleNamespace(
        principal_type=RefreshSession.PrincipalType.STAFF,
        permission_codes=frozenset({"platform.gyms.view"}),
    )

    assert permission.has_permission(
        platform_request,
        SimpleNamespace(required_permission="platform.gyms.view"),
    )
    assert not permission.has_permission(
        tenant_request,
        SimpleNamespace(required_permission="platform.gyms.view"),
    )
    assert not permission.has_permission(
        platform_request,
        SimpleNamespace(required_permission="gym.view"),
    )


@override_settings(REFRESH_TOKEN_PEPPER="test-pepper")
def test_platform_admin_login_issues_platform_session_without_membership_lookup() -> None:
    now = datetime.now(UTC)
    user = SimpleNamespace(
        id="9f40e50f-3d5e-453e-a824-49438f620f6a",
        is_active=True,
        is_platform_admin=True,
        mfa_enabled=False,
        check_password=lambda password: password == "correct horse battery staple",
    )
    pair = TokenPair(
        access_token="platform-access",
        access_expires_at=now + timedelta(minutes=10),
        refresh_token="platform-refresh",
        refresh_expires_at=now + timedelta(days=30),
    )
    request = APIRequestFactory().post(
        "/api/v1/auth/login/",
        {
            "email": "admin@example.test",
            "password": "correct horse battery staple",
            "device_name": "Web browser",
        },
        format="json",
    )
    request.request_id = "req_platform_login"

    with (
        patch("apps.users.views.transaction.atomic", return_value=nullcontext()),
        patch("apps.users.views.User.objects.get", return_value=user),
        patch("apps.users.views.set_identity_context"),
        patch("apps.users.views.create_platform_session", return_value=pair) as create_session,
        patch("apps.users.views._membership_rows") as membership_rows,
    ):
        response = LoginView.as_view()(request)

    assert response.status_code == 200
    assert response.data["data"]["requires_gym_selection"] is False
    assert response.data["data"]["available_gym_memberships"] == []
    create_session.assert_called_once()
    membership_rows.assert_not_called()


def test_platform_authentication_never_installs_tenant_context() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, is_active=True, is_platform_admin=True)
    session = SimpleNamespace(user=user)
    session_query = MagicMock()
    session_query.get.return_value = session
    permission_query = MagicMock()
    permission_query.values_list.return_value = ["platform.gyms.view"]
    request = APIRequestFactory().get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION="Bearer opaque-platform-token",
    )
    request.request_id = "req_platform_me"

    with (
        patch(
            "apps.users.authentication.decode_platform_access_token",
            return_value={"sub": str(user_id), "session_id": str(session_id)},
        ),
        patch(
            "apps.users.authentication.RefreshSession.objects.select_related",
            return_value=session_query,
        ),
        patch(
            "apps.users.authentication.Permission.objects.filter",
            return_value=permission_query,
        ) as permission_filter,
        patch("apps.users.authentication.set_identity_context"),
    ):
        authenticated = PlatformJWTAuthentication().authenticate(request)

    assert authenticated is not None
    assert authenticated[0] is user
    assert request.principal_type == RefreshSession.PrincipalType.PLATFORM
    assert request.permission_codes == frozenset({"platform.gyms.view"})
    assert "platform.settings.manage" not in request.permission_codes
    permission_filter.assert_called_once_with(code__startswith="platform.", is_active=True)
    permission_query.values_list.assert_called_once_with("code", flat=True)
    assert not hasattr(request, "gym")
    assert not hasattr(request, "gym_membership")
    filters = session_query.get.call_args.kwargs
    assert filters["id"] == session_id
    assert filters["user_id"] == user_id
    assert filters["principal_type"] == RefreshSession.PrincipalType.PLATFORM
    assert filters["selected_gym__isnull"] is True
    assert filters["gym_membership__isnull"] is True
    assert filters["member_id__isnull"] is True
    assert filters["user__is_active"] is True
    assert filters["user__is_platform_admin"] is True


def test_platform_authentication_denies_missing_or_ineligible_session() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session_query = MagicMock()
    session_query.get.side_effect = RefreshSession.DoesNotExist
    request = APIRequestFactory().get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION="Bearer opaque-platform-token",
    )
    request.request_id = "req_platform_denied"

    with (
        patch(
            "apps.users.authentication.decode_platform_access_token",
            return_value={"sub": str(user_id), "session_id": str(session_id)},
        ),
        patch(
            "apps.users.authentication.RefreshSession.objects.select_related",
            return_value=session_query,
        ),
        patch("apps.users.authentication.set_identity_context"),
        pytest.raises(AuthenticationFailed),
    ):
        PlatformJWTAuthentication().authenticate(request)


def test_combined_authentication_preserves_platform_expiry_for_bff_refresh() -> None:
    request = APIRequestFactory().get(
        "/api/v1/auth/me/",
        HTTP_AUTHORIZATION="Bearer expired-platform-token",
    )

    with (
        patch(
            "apps.users.authentication.decode_platform_access_token",
            side_effect=AccessTokenExpired(),
        ),
        patch.object(PlatformJWTAuthentication, "authenticate") as platform_authenticate,
        pytest.raises(AccessTokenExpired),
    ):
        StaffOrPlatformJWTAuthentication().authenticate(request)

    platform_authenticate.assert_not_called()
