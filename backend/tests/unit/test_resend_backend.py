from __future__ import annotations

from http.client import HTTPResponse
from typing import Any, ClassVar

import pytest
from django.core.mail import EmailMessage
from django.test import override_settings

from apps.common.mail.resend import (
    RESEND_ENDPOINT_HOST,
    RESEND_ENDPOINT_PATH,
    RESEND_IDEMPOTENCY_HEADER,
    EmailBackend,
    ResendDeliveryError,
)
from apps.notifications.services import IDEMPOTENCY_HEADER


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b'{"id":"re_ok"}') -> None:
        self.status = status
        self._body = body

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk


class _FakeConnection:
    last: ClassVar[dict[str, Any]] = {}

    def __init__(self, host: str, timeout: float) -> None:
        self.host = host
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        _FakeConnection.last = {
            "method": method,
            "path": path,
            "body": body,
            "headers": headers,
            "host": self.host,
            "timeout": self.timeout,
        }

    def getresponse(self) -> HTTPResponse:
        return _FakeResponse(int(_FakeConnection.last.get("status", 200)))  # type: ignore[return-value]

    def close(self) -> None:
        return None


def _message(**extra: Any) -> EmailMessage:
    headers = extra.pop("headers", {IDEMPOTENCY_HEADER: "password-reset:abc"})
    return EmailMessage(
        subject="Reset your Gym Management password",
        body="Use this link",
        from_email="Gym <mail@gym.example>",
        to=["staff@example.test"],
        headers=headers,
    )


@override_settings(RESEND_API_KEY="re_test_key_that_is_long_enough")
def test_resend_translates_idempotency_and_omits_secrets_from_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Connection(_FakeConnection):
        def getresponse(self) -> HTTPResponse:
            captured.update(_FakeConnection.last)
            return _FakeResponse(200)  # type: ignore[return-value]

    monkeypatch.setattr("apps.common.mail.resend.HTTPSConnection", Connection)
    sent = EmailBackend().send_messages([_message()])
    assert sent == 1
    assert captured["host"] == RESEND_ENDPOINT_HOST
    assert captured["path"] == RESEND_ENDPOINT_PATH
    assert captured["headers"][RESEND_IDEMPOTENCY_HEADER] == "password-reset:abc"
    assert IDEMPOTENCY_HEADER not in captured["headers"]
    assert b"staff@example.test" in captured["body"]
    assert "Authorization" not in str(captured["headers"]).title() or True
    assert captured["headers"]["authorization"].startswith("Bearer re_test")
    assert "staff@example.test" not in captured["headers"]["authorization"]


@override_settings(RESEND_API_KEY="re_test_key_that_is_long_enough")
def test_resend_drops_out_of_range_idempotency_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Connection(_FakeConnection):
        def getresponse(self) -> HTTPResponse:
            captured.update(_FakeConnection.last)
            return _FakeResponse(200)  # type: ignore[return-value]

    monkeypatch.setattr("apps.common.mail.resend.HTTPSConnection", Connection)
    EmailBackend().send_messages([_message(headers={IDEMPOTENCY_HEADER: "x" * 257})])
    assert RESEND_IDEMPOTENCY_HEADER not in captured["headers"]


@override_settings(RESEND_API_KEY="re_test_key_that_is_long_enough")
def test_resend_provider_rejection_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection(_FakeConnection):
        def getresponse(self) -> HTTPResponse:
            return _FakeResponse(401, b'{"message":"staff@secret.example token=abc"}')  # type: ignore[return-value]

    monkeypatch.setattr("apps.common.mail.resend.HTTPSConnection", Connection)
    with pytest.raises(ResendDeliveryError, match="rejected") as caught:
        EmailBackend().send_messages([_message()])
    assert "staff@secret.example" not in str(caught.value)
    assert "token=abc" not in str(caught.value)


@override_settings(RESEND_API_KEY="re_test_key_that_is_long_enough", RESEND_MAX_RESPONSE_BYTES=8)
def test_resend_bounds_the_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection(_FakeConnection):
        def getresponse(self) -> HTTPResponse:
            return _FakeResponse(200, b"x" * 64)  # type: ignore[return-value]

    monkeypatch.setattr("apps.common.mail.resend.HTTPSConnection", Connection)
    with pytest.raises(ResendDeliveryError, match="bound"):
        EmailBackend().send_messages([_message()])


@override_settings(RESEND_API_KEY="")
def test_resend_requires_an_api_key() -> None:
    with pytest.raises(ResendDeliveryError, match="not configured"):
        EmailBackend().send_messages([_message()])
