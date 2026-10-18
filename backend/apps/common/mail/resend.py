from __future__ import annotations

import json
from collections.abc import Sequence
from http.client import HTTPException, HTTPSConnection
from typing import Any

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

RESEND_ENDPOINT_HOST = "api.resend.com"
RESEND_ENDPOINT_PATH = "/emails"
RESEND_IDEMPOTENCY_HEADER = "Idempotency-Key"
APPLICATION_IDEMPOTENCY_HEADER = "X-Idempotency-Key"
MIN_IDEMPOTENCY_LENGTH = 1
MAX_IDEMPOTENCY_LENGTH = 256
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RESPONSE_BYTES = 4096


class ResendDeliveryError(OSError):
    """The provider rejected or failed the send. Details stay out of logs."""


class EmailBackend(BaseEmailBackend):
    """POST https://api.resend.com/emails and map the application idempotency header."""

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        sent = 0
        for message in email_messages:
            try:
                self._deliver(message)
            except Exception:
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent

    def _deliver(self, message: EmailMessage) -> None:
        api_key = str(getattr(settings, "RESEND_API_KEY", "") or "")
        if not api_key:
            raise ResendDeliveryError("email provider is not configured")
        payload = _payload(message)
        headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        idempotency = _idempotency_key(message)
        if idempotency is not None:
            headers[RESEND_IDEMPOTENCY_HEADER] = idempotency
        timeout = float(getattr(settings, "RESEND_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        max_bytes = int(getattr(settings, "RESEND_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES))
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        connection = HTTPSConnection(RESEND_ENDPOINT_HOST, timeout=timeout)
        try:
            connection.request("POST", RESEND_ENDPOINT_PATH, body=body, headers=headers)
            response = connection.getresponse()
            _bounded_read(response, max_bytes)
            if response.status >= 400:
                raise ResendDeliveryError("email provider rejected the message")
        except ResendDeliveryError:
            raise
        except (TimeoutError, OSError, HTTPException, ValueError, UnicodeError) as exc:
            raise ResendDeliveryError("email provider is unavailable") from exc
        finally:
            connection.close()


def _payload(message: EmailMessage) -> dict[str, Any]:
    recipients = [str(address) for address in message.recipients() if address]
    if not recipients:
        raise ResendDeliveryError("email provider rejected the message")
    sender = message.from_email or str(getattr(settings, "DEFAULT_FROM_EMAIL", ""))
    if not sender:
        raise ResendDeliveryError("email provider rejected the message")
    payload: dict[str, Any] = {
        "from": sender,
        "to": recipients,
        "subject": str(message.subject or ""),
        "text": str(message.body or ""),
    }
    html = _html_body(message)
    if html:
        payload["html"] = html
    return payload


def _html_body(message: EmailMessage) -> str:
    if message.content_subtype == "html":
        return str(message.body or "")
    for alternative, mime in getattr(message, "alternatives", ()):
        if mime == "text/html":
            return str(alternative)
    return ""


def _idempotency_key(message: EmailMessage) -> str | None:
    headers = getattr(message, "extra_headers", {}) or {}
    value = headers.get(APPLICATION_IDEMPOTENCY_HEADER)
    if not isinstance(value, str):
        return None
    key = value.strip()
    if not MIN_IDEMPOTENCY_LENGTH <= len(key) <= MAX_IDEMPOTENCY_LENGTH:
        return None
    return key


def _bounded_read(response: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = response.read(min(4096, remaining))
        if not isinstance(chunk, bytes) or not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise ResendDeliveryError("email provider response exceeded the bound")
    return payload
