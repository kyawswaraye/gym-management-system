"""Display-device credential parsing and one-way verification.

The selector is a public identifier. The secret is verified with Django's
PBKDF2 hasher and is never stored, logged, or returned except at enrollment
and rotation.
"""

from __future__ import annotations

import re
import secrets
from typing import NamedTuple

from django.contrib.auth.hashers import check_password, make_password

DEVICE_CREDENTIAL_PREFIX = "gms-device:v1:"
_SELECTOR = r"[A-Za-z0-9_-]{16,64}"
_SECRET = r"[A-Za-z0-9_-]{43,128}"
_CREDENTIAL = re.compile(rf"^{re.escape(DEVICE_CREDENTIAL_PREFIX)}({_SELECTOR})\.({_SECRET})$")
_DUMMY_HASH: str | None = None


class ParsedDeviceCredential(NamedTuple):
    selector: str
    secret: str


def format_device_credential(*, selector: str, secret: str) -> str:
    return f"{DEVICE_CREDENTIAL_PREFIX}{selector}.{secret}"


def parse_device_credential(value: str) -> ParsedDeviceCredential | None:
    """Return the selector and secret, or None when the shape is not ours."""

    if len(value) > 220:
        return None
    matched = _CREDENTIAL.fullmatch(value)
    if matched is None:
        return None
    return ParsedDeviceCredential(matched.group(1), matched.group(2))


def hash_device_secret(secret: str) -> str:
    return make_password(secret, hasher="pbkdf2_sha256")


def new_device_selector() -> str:
    return secrets.token_urlsafe(18)


def new_device_secret() -> str:
    return secrets.token_urlsafe(32)


def device_secret_matches(secret: str, encoded: str) -> bool:
    return check_password(secret, encoded)


def reject_unknown_device_secret(secret: str) -> None:
    """Spend a PBKDF2 verification when the selector does not exist."""

    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_device_secret(secrets.token_urlsafe(32))
    check_password(secret, _DUMMY_HASH)
