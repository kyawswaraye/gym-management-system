"""FCM HTTP v1 transport and the in-memory test transport.

``locmem`` records a fingerprint of the token, never the token itself. The FCM
client is used only when ``PUSH_BACKEND=fcm`` and does not log the request.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from django.conf import settings

from apps.notifications.tokens import fingerprint_push_token

PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
DELIVERY_REJECTED = "DELIVERY_REJECTED"
INVALID_TOKEN = "INVALID_TOKEN"
SENT = "SENT"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_MESSAGE_ID_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:/-")


@dataclass(frozen=True)
class PushRequest:
    notification_id: str
    title: str
    body: str
    token: str


@dataclass(frozen=True)
class PushResult:
    code: str
    provider_message_id: str = ""


@dataclass(frozen=True)
class RecordedPush:
    notification_id: str
    title: str
    body: str
    token_fingerprint: str


UrlOpen = Callable[..., Any]


def classify_fcm_http(*, status: int, body: str) -> str:
    """Map an FCM HTTP result to a stable code. The body is not retained."""

    folded = body.upper()
    token_rejected = "UNREGISTERED" in folded or (
        "INVALID_ARGUMENT" in folded and "TOKEN" in folded
    )
    if status == 404 or token_rejected:
        return INVALID_TOKEN
    if status in {401, 403, 408, 429} or status >= 500:
        return PROVIDER_UNAVAILABLE
    return DELIVERY_REJECTED


def safe_provider_message_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 255:
        return ""
    if any(character not in _MESSAGE_ID_SAFE for character in value):
        return ""
    return value


class PushTransport(Protocol):
    def send(self, request: PushRequest) -> PushResult: ...


@dataclass
class LocMemPushTransport:
    deliveries: list[RecordedPush] = field(default_factory=list)
    handler: Callable[[RecordedPush], PushResult] | None = None

    def send(self, request: PushRequest) -> PushResult:
        recorded = RecordedPush(
            notification_id=request.notification_id,
            title=request.title,
            body=request.body,
            token_fingerprint=fingerprint_push_token(request.token),
        )
        self.deliveries.append(recorded)
        if self.handler is not None:
            return self.handler(recorded)
        return PushResult(code=SENT, provider_message_id="locmem")

    def reset(self) -> None:
        self.deliveries.clear()
        self.handler = None


_LOCMEM: LocMemPushTransport | None = None


def locmem_transport() -> LocMemPushTransport:
    global _LOCMEM
    if _LOCMEM is None:
        _LOCMEM = LocMemPushTransport()
    return _LOCMEM


class FcmPushTransport:
    """Firebase Cloud Messaging HTTP v1. Tests inject ``urlopen``."""

    def __init__(self, *, urlopen: UrlOpen | None = None) -> None:
        self._urlopen = urlopen or urllib.request.urlopen
        self._access_token = ""
        self._access_token_until = 0.0

    def send(self, request: PushRequest) -> PushResult:
        project_id = str(settings.FCM_PROJECT_ID)
        try:
            token = self._bearer()
            payload = {
                "message": {
                    "token": request.token,
                    "notification": {"title": request.title, "body": request.body},
                    "data": {"notification_id": request.notification_id},
                    "android": {"collapse_key": request.notification_id},
                    "apns": {"headers": {"apns-collapse-id": request.notification_id}},
                }
            }
            http_request = urllib.request.Request(
                f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
                data=json.dumps(payload).encode(),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            response = self._urlopen(http_request, timeout=10)
            try:
                status = int(getattr(response, "status", 200))
                raw = response.read(4096)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            raw = exc.read(4096)
            return PushResult(code=classify_fcm_http(status=exc.code, body=_safe_text(raw)))
        except (OSError, TimeoutError, urllib.error.URLError):
            return PushResult(code=PROVIDER_UNAVAILABLE)
        text = _safe_text(raw)
        if status != 200:
            return PushResult(code=classify_fcm_http(status=status, body=text))
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return PushResult(code=DELIVERY_REJECTED)
        name = parsed.get("name") if isinstance(parsed, dict) else None
        return PushResult(code=SENT, provider_message_id=safe_provider_message_id(name))

    def _bearer(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_until:
            return self._access_token
        assertion = _service_account_assertion()
        body = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        ).encode()
        http_request = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        response = self._urlopen(http_request, timeout=10)
        try:
            raw = response.read(8192)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        parsed = json.loads(_safe_text(raw))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("access_token"), str):
            raise RuntimeError("FCM authentication failed")
        self._access_token = parsed["access_token"]
        self._access_token_until = now + 3000
        return self._access_token


def get_transport() -> PushTransport:
    backend = str(getattr(settings, "PUSH_BACKEND", "locmem"))
    if backend == "locmem":
        return locmem_transport()
    if backend == "fcm":
        return FcmPushTransport()
    raise RuntimeError("PUSH_BACKEND is not configured")


def _safe_text(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")[:4096]
    return ""


def _service_account_assertion() -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

    raw = str(settings.FCM_SERVICE_ACCOUNT_JSON)
    account = json.loads(raw)
    if not isinstance(account, dict):
        raise RuntimeError("FCM service account is invalid")
    client_email = account.get("client_email")
    private_key = account.get("private_key")
    if not isinstance(client_email, str) or not isinstance(private_key, str):
        raise RuntimeError("FCM service account is invalid")
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(
        json.dumps(
            {
                "iss": client_email,
                "scope": _FCM_SCOPE,
                "aud": _TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    if not isinstance(key, RSAPrivateKey):
        raise RuntimeError("FCM service account is invalid")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


def _b64(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
