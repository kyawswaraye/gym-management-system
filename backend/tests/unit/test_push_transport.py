from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.common.production import require_push_backend
from apps.notifications.tokens import decrypt_push_token, encrypt_push_token, fingerprint_push_token
from apps.notifications.transport import (
    DELIVERY_REJECTED,
    INVALID_TOKEN,
    PROVIDER_UNAVAILABLE,
    SENT,
    FcmPushTransport,
    PushRequest,
    classify_fcm_http,
    locmem_transport,
    safe_provider_message_id,
)


def test_fcm_status_mapping_never_keeps_the_body() -> None:
    assert classify_fcm_http(status=404, body="UNREGISTERED secret-token") == INVALID_TOKEN
    assert (
        classify_fcm_http(status=400, body="INVALID_ARGUMENT registration token") == INVALID_TOKEN
    )
    assert classify_fcm_http(status=401, body="credential") == PROVIDER_UNAVAILABLE
    assert classify_fcm_http(status=403, body="") == PROVIDER_UNAVAILABLE
    assert classify_fcm_http(status=429, body="") == PROVIDER_UNAVAILABLE
    assert classify_fcm_http(status=500, body="boom") == PROVIDER_UNAVAILABLE
    assert classify_fcm_http(status=400, body="other") == DELIVERY_REJECTED
    assert safe_provider_message_id("projects/demo/messages/abc") == "projects/demo/messages/abc"
    assert safe_provider_message_id("bad id") == ""
    assert safe_provider_message_id("x" * 256) == ""


def test_locmem_records_a_fingerprint_and_not_the_token() -> None:
    transport = locmem_transport()
    transport.reset()
    token = "fcm-token-not-stored"
    result = transport.send(
        PushRequest(notification_id="note-1", title="Hello", body="Body", token=token)
    )
    assert result.code == SENT
    assert transport.deliveries[0].token_fingerprint == fingerprint_push_token(token)
    assert token not in transport.deliveries[0].token_fingerprint
    rendered = repr(transport.deliveries)
    assert token not in rendered


def test_push_token_encryption_round_trip_uses_a_dedicated_key() -> None:
    token = "rotation-token-value"
    ciphertext = encrypt_push_token(token)
    assert ciphertext != token
    assert not ciphertext.startswith(token)
    assert decrypt_push_token(ciphertext) == token
    assert len(fingerprint_push_token(token)) == 64
    with override_settings(PUSH_TOKEN_ENCRYPTION_KEY="test-mfa-encryption-key"):
        with pytest.raises(RuntimeError, match="must not match"):
            encrypt_push_token(token)


def test_production_push_backend_fails_closed_without_credentials() -> None:
    with pytest.raises(ImproperlyConfigured, match="FCM HTTP v1"):
        require_push_backend(
            backend="locmem",
            project_id="demo",
            service_account_json="{}",
            encryption_key="push-encryption-secret",
            fingerprint_pepper="push-fingerprint-secret",
            other_secrets={"MFA_ENCRYPTION_KEY": "mfa-secret-value"},
        )
    with pytest.raises(ImproperlyConfigured, match="dedicated"):
        require_push_backend(
            backend="fcm",
            project_id="demo",
            service_account_json="{}",
            encryption_key="shared-secret-value",
            fingerprint_pepper="push-fingerprint-secret",
            other_secrets={"MFA_ENCRYPTION_KEY": "shared-secret-value"},
        )
    with pytest.raises(ImproperlyConfigured, match="FCM_PROJECT_ID"):
        require_push_backend(
            backend="fcm",
            project_id="",
            service_account_json="{}",
            encryption_key="push-encryption-secret",
            fingerprint_pepper="push-fingerprint-secret",
            other_secrets={"MFA_ENCRYPTION_KEY": "mfa-secret-value"},
        )


class _HttpResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, _limit: int) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def test_fcm_http_v1_sends_only_the_collapse_id_and_text() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    account = {"client_email": "push@example.test", "private_key": pem.decode()}
    token = "fcm-destination-secret"
    notification_id = "11111111-1111-1111-1111-111111111111"
    calls: list[Any] = []

    def urlopen(request: Any, timeout: int = 10) -> _HttpResponse:
        calls.append(request)
        assert timeout == 10
        if str(request.full_url).endswith("/token"):
            return _HttpResponse(b'{"access_token":"ya29.test-access"}')
        return _HttpResponse(b'{"name":"projects/demo-project/messages/abc"}')

    with override_settings(
        PUSH_BACKEND="fcm",
        FCM_PROJECT_ID="demo-project",
        FCM_SERVICE_ACCOUNT_JSON=json.dumps(account),
    ):
        result = FcmPushTransport(urlopen=urlopen).send(
            PushRequest(
                notification_id=notification_id,
                title="Holiday hours",
                body="The gym closes at six.",
                token=token,
            )
        )
    assert result.code == SENT
    assert result.provider_message_id == "projects/demo-project/messages/abc"
    assert token not in result.provider_message_id
    assert len(calls) == 2
    send = calls[1]
    assert send.full_url == "https://fcm.googleapis.com/v1/projects/demo-project/messages:send"
    assert send.get_method() == "POST"
    payload = json.loads(send.data.decode())
    message = payload["message"]
    assert set(message) == {"token", "notification", "data", "android", "apns"}
    assert message["token"] == token
    assert message["notification"] == {"title": "Holiday hours", "body": "The gym closes at six."}
    assert message["data"] == {"notification_id": notification_id}
    assert message["android"]["collapse_key"] == notification_id
    assert message["apns"]["headers"]["apns-collapse-id"] == notification_id
    rendered = json.dumps(payload)
    assert "gym_id" not in rendered
    assert "member_id" not in rendered
    assert "@" not in rendered
    assert "amount" not in rendered
    assert token not in json.dumps(message["data"])
