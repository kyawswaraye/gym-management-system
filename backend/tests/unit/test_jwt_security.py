from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings

from apps.common.api.exceptions import AuthenticationFailed
from apps.users.tokens import decode_access_token, issue_access_token


@pytest.fixture
def keys() -> tuple[str, str]:
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
    return private_pem, public_pem


def claims() -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0)
    return {
        "iss": "gym-platform-api",
        "aud": "gym-platform",
        "sub": str(uuid.uuid4()),
        "gym_id": str(uuid.uuid4()),
        "gym_membership_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "authz_version": 1,
        "token_type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "jti": str(uuid.uuid4()),
    }


def encode(private: str, payload: dict[str, object], *, kid: str = "test-key") -> str:
    return jwt.encode(payload, private, algorithm="RS256", headers={"typ": "JWT", "kid": kid})


def test_access_token_has_exact_claims_and_ten_minute_ttl(keys: tuple[str, str]) -> None:
    private, public = keys
    membership = SimpleNamespace(id=uuid.uuid4(), gym_id=uuid.uuid4(), authz_version=7)
    with override_settings(
        JWT_PRIVATE_KEY=private, JWT_PUBLIC_KEYS={"test-key": public}, JWT_KEY_ID="test-key"
    ):
        token, _ = issue_access_token(
            user_id=uuid.uuid4(), membership=membership, session_id=uuid.uuid4()
        )
        decoded = decode_access_token(token)
    assert decoded["exp"] - decoded["iat"] == 600
    assert set(decoded) == set(claims())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iss", "wrong"),
        ("aud", "wrong"),
        ("token_type", "refresh"),
        ("authz_version", 0),
        ("sub", "not-a-uuid"),
    ],
)
def test_rejects_invalid_required_claims(keys: tuple[str, str], field: str, value: object) -> None:
    private, public = keys
    payload = claims()
    payload[field] = value
    with override_settings(JWT_PUBLIC_KEYS={"test-key": public}):
        with pytest.raises(AuthenticationFailed):
            decode_access_token(encode(private, payload))


def test_rejects_unknown_kid_and_alg_none(keys: tuple[str, str]) -> None:
    private, public = keys
    with override_settings(JWT_PUBLIC_KEYS={"test-key": public}):
        with pytest.raises(AuthenticationFailed):
            decode_access_token(encode(private, claims(), kid="unknown"))
        unsigned = jwt.encode(claims(), key="", algorithm="none", headers={"kid": "test-key"})
        with pytest.raises(AuthenticationFailed):
            decode_access_token(unsigned)


def test_rejects_extra_and_duplicate_claims(keys: tuple[str, str]) -> None:
    private, public = keys
    payload = claims()
    payload["permissions"] = ["roles.manage"]
    with override_settings(JWT_PUBLIC_KEYS={"test-key": public}):
        with pytest.raises(AuthenticationFailed):
            decode_access_token(encode(private, payload))

    header = base64.urlsafe_b64encode(
        json.dumps({"typ": "JWT", "alg": "RS256", "kid": "test-key"}).encode()
    ).rstrip(b"=")
    body = base64.urlsafe_b64encode(b'{"iss":"a","iss":"b"}').rstrip(b"=")
    malformed = b".".join((header, body, b"signature")).decode()
    with override_settings(JWT_PUBLIC_KEYS={"test-key": public}):
        with pytest.raises(AuthenticationFailed):
            decode_access_token(malformed)
