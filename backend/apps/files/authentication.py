from __future__ import annotations

from typing import Any

import jwt
from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request

from apps.common.api.exceptions import AccessTokenExpired, AuthenticationFailed
from apps.users.authentication import StaffJWTAuthentication, _bearer_token
from apps.users.tokens import _b64json


class StaffFileAuthentication(StaffJWTAuthentication):
    """Accept a selected-gym staff token. Platform and member tokens are denied."""

    def authenticate(self, request: Request) -> tuple[Any, dict[str, Any]] | None:
        token = _bearer_token(request)
        if token is None:
            return None
        audience = _verified_audience(token)
        if audience in {settings.JWT_PLATFORM_AUDIENCE, settings.JWT_MEMBER_AUDIENCE}:
            raise PermissionDenied()
        return super().authenticate(request)


def _verified_audience(token: str) -> str:
    if token.count(".") != 2:
        raise AuthenticationFailed()
    header = _b64json(token.split(".", 1)[0])
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str):
        raise AuthenticationFailed()
    if kid not in settings.JWT_PUBLIC_KEYS:
        raise AuthenticationFailed()
    try:
        claims = jwt.decode(
            token,
            settings.JWT_PUBLIC_KEYS[kid],
            algorithms=["RS256"],
            issuer=settings.JWT_ISSUER,
            leeway=settings.JWT_CLOCK_SKEW_SECONDS,
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenExpired() from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed() from exc
    audience = claims.get("aud")
    if not isinstance(audience, str):
        raise AuthenticationFailed()
    return audience
