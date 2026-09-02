from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from django.conf import settings

from apps.common.api.exceptions import AccessTokenExpired, AuthenticationFailed

REQUIRED_CLAIMS = {
    "iss",
    "aud",
    "sub",
    "gym_id",
    "gym_membership_id",
    "session_id",
    "authz_version",
    "token_type",
    "iat",
    "nbf",
    "exp",
    "jti",
}


def _b64json(segment: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

        def reject_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate claim")
                value[key] = item
            return value

        parsed = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AuthenticationFailed() from exc
    if not isinstance(parsed, dict):
        raise AuthenticationFailed()
    return parsed


def issue_access_token(
    *, user_id: uuid.UUID, membership: Any, session_id: uuid.UUID
) -> tuple[str, datetime]:
    now = datetime.now(UTC).replace(microsecond=0)
    expires = now + timedelta(seconds=600)
    claims = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_STAFF_AUDIENCE,
        "sub": str(user_id),
        "gym_id": str(membership.gym_id),
        "gym_membership_id": str(membership.id),
        "session_id": str(session_id),
        "authz_version": membership.authz_version,
        "token_type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(
        claims,
        settings.JWT_PRIVATE_KEY,
        algorithm="RS256",
        headers={"typ": "JWT", "kid": settings.JWT_KEY_ID},
    )
    return token, expires


def decode_access_token(token: str) -> dict[str, Any]:
    if token.count(".") != 2:
        raise AuthenticationFailed()
    encoded_header, encoded_payload, _ = token.split(".")
    header = _b64json(encoded_header)
    unverified = _b64json(encoded_payload)
    if set(unverified) != REQUIRED_CLAIMS:
        raise AuthenticationFailed()
    if header.get("typ") != "JWT" or header.get("alg") != "RS256":
        raise AuthenticationFailed()
    if "crit" in header or set(header) - {"typ", "alg", "kid"}:
        raise AuthenticationFailed()
    kid = header.get("kid")
    if not isinstance(kid, str) or kid not in settings.JWT_PUBLIC_KEYS:
        raise AuthenticationFailed()
    try:
        claims = jwt.decode(
            token,
            settings.JWT_PUBLIC_KEYS[kid],
            algorithms=["RS256"],
            audience=settings.JWT_STAFF_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            leeway=settings.JWT_CLOCK_SKEW_SECONDS,
            options={"require": sorted(REQUIRED_CLAIMS)},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AccessTokenExpired() from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed() from exc
    if claims.get("aud") != settings.JWT_STAFF_AUDIENCE or claims.get("token_type") != "access":
        raise AuthenticationFailed()
    if not isinstance(claims.get("authz_version"), int) or claims["authz_version"] < 1:
        raise AuthenticationFailed()
    now = int(datetime.now(UTC).timestamp())
    if (
        not isinstance(claims.get("iat"), int)
        or claims["iat"] > now + settings.JWT_CLOCK_SKEW_SECONDS
    ):
        raise AuthenticationFailed()
    for field in ("sub", "gym_id", "gym_membership_id", "session_id", "jti"):
        try:
            uuid.UUID(claims[field])
        except (ValueError, TypeError, AttributeError) as exc:
            raise AuthenticationFailed() from exc
    return claims
