"""Encrypt FCM tokens and fingerprint them for uniqueness.

The raw token, the ciphertext, and the peppers never belong in logs, audits,
errors, or API responses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

_FORBIDDEN_SETTING_NAMES = (
    "MFA_ENCRYPTION_KEY",
    "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY",
    "PASSWORD_RESET_TOKEN_PEPPER",
)


def _secret(name: str) -> str:
    value = getattr(settings, name, "")
    if not isinstance(value, str) or len(value) < 16 or value == "unsafe-local-only":
        raise RuntimeError(f"{name} is not configured")
    for other in _FORBIDDEN_SETTING_NAMES:
        other_value = getattr(settings, other, "")
        if isinstance(other_value, str) and other_value and value == other_value:
            raise RuntimeError(f"{name} must not match {other}")
    return value


def _fernet() -> Fernet:
    material = _secret("PUSH_TOKEN_ENCRYPTION_KEY")
    pepper = _secret("PUSH_TOKEN_FINGERPRINT_PEPPER")
    if material == pepper:
        raise RuntimeError("PUSH_TOKEN_ENCRYPTION_KEY must not match the fingerprint pepper")
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt_push_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_push_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("The stored push token could not be read.") from exc


def fingerprint_push_token(token: str) -> str:
    pepper = _secret("PUSH_TOKEN_FINGERPRINT_PEPPER")
    encryption_key = _secret("PUSH_TOKEN_ENCRYPTION_KEY")
    if pepper == encryption_key:
        raise RuntimeError("PUSH_TOKEN_FINGERPRINT_PEPPER must not match the encryption key")
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
