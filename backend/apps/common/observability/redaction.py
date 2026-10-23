"""Recursive redaction for diagnostic logs.

The result is safe to print on one line. It is not an audit record.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|x-amz-(?:signature|credential|security-token)=[^&\s]+"
    r"|(?:access_token|refresh_token|id_token|token|ticket|password|secret|"
    r"signature|reset_token|invitation(?:_token)?|mfa(?:_challenge_token)?|"
    r"challenge_token|bff_shared_secret|x-gym-bff-secret)=[^&\s]+)"
)
_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "cookies",
        "setcookie",
        "password",
        "passwd",
        "secret",
        "token",
        "ticket",
        "tickets",
        "mfa",
        "otp",
        "totp",
        "challenge",
        "reset",
        "invitation",
        "invite",
        "pepper",
        "credential",
        "credentials",
        "bff",
        "bearer",
        "jwt",
        "signature",
        "presigned",
        "session",
        "apikey",
        "privatekey",
        "accesskey",
        "secretkey",
        "email",
        "mail",
        "query",
        "headers",
        "header",
        "cookieheader",
    }
)
_BODY = frozenset({"body", "rawbody", "requestbody", "responsebody", "payload"})
_SUFFIXES = (
    "token",
    "secret",
    "password",
    "cookie",
    "ticket",
    "signature",
    "pepper",
    "apikey",
    "privatekey",
    "credential",
)
_MAX_DEPTH = 8
_MAX_ITEMS = 40
_MAX_STRING = 512
REDACTED = "[redacted]"


def redact_text(value: str) -> str:
    cleaned = _CONTROL.sub("", value)
    cleaned = _SECRET_TEXT.sub(REDACTED, cleaned)
    if len(cleaned) > _MAX_STRING:
        return cleaned[:_MAX_STRING]
    return cleaned


def redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTED
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value, depth)
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth=depth + 1) for item in value[:_MAX_ITEMS]]
    return REDACTED


def _redact_mapping(value: Mapping[Any, Any], depth: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= _MAX_ITEMS:
            break
        name = str(key)
        if _sensitive_key(name) or _body_key(name):
            result[redact_text(name)[:80]] = REDACTED
            continue
        result[redact_text(name)[:80]] = redact_value(item, depth=depth + 1)
    return result


def _parts(key: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", key.lower()) if part]


def _sensitive_key(key: str) -> bool:
    parts = _parts(key)
    if any(part in _PARTS for part in parts):
        return True
    collapsed = "".join(parts)
    if collapsed in _PARTS or collapsed in {"setcookie", "xamzsignature", "bffsharedsecret"}:
        return True
    return any(collapsed.endswith(suffix) for suffix in _SUFFIXES)


def _body_key(key: str) -> bool:
    parts = _parts(key)
    collapsed = "".join(parts)
    return collapsed in _BODY or any(part in _BODY for part in parts)
