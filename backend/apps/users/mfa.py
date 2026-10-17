from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.api.exceptions import AuthenticationFailed

from .models import MFAChallenge, MFAEnrollment, RefreshSession, User


def _hash_token(token: str) -> str:
    pepper = settings.MFA_TOKEN_PEPPER
    if not pepper:
        raise RuntimeError("MFA_TOKEN_PEPPER is not configured")
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    if not settings.MFA_ENCRYPTION_KEY:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.MFA_ENCRYPTION_KEY.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise AuthenticationFailed() from exc


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, *, at_time: int | None = None) -> bool:
    return matching_totp_step(secret, code, at_time=at_time) is not None


def matching_totp_step(secret: str, code: str, *, at_time: int | None = None) -> int | None:
    if len(code) != 6 or not code.isdigit():
        return None
    counter = (at_time if at_time is not None else int(time.time())) // 30
    for drift in (-1, 0, 1):
        step = counter + drift
        if hmac.compare_digest(_totp(secret, step), code):
            return step
    return None


@dataclass(frozen=True)
class EnrollmentResult:
    token: str
    secret: str
    otpauth_uri: str
    expires_at: datetime


@transaction.atomic
def begin_enrollment(*, user: User, current_password: str) -> EnrollmentResult:
    if not user.check_password(current_password):
        raise AuthenticationFailed()
    now = timezone.now()
    MFAEnrollment.objects.filter(user=user, confirmed_at__isnull=True, expires_at__gt=now).delete()
    token = secrets.token_urlsafe(48)
    secret = generate_totp_secret()
    expires_at = now + timedelta(seconds=settings.MFA_ENROLLMENT_TTL_SECONDS)
    MFAEnrollment.objects.create(
        user=user,
        token_hash=_hash_token(token),
        secret_encrypted=encrypt_secret(secret),
        expires_at=expires_at,
    )
    issuer = quote(settings.MFA_ISSUER)
    account = quote(user.normalized_email)
    uri = (
        f"otpauth://totp/{issuer}:{account}?secret={secret}"
        f"&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    )
    return EnrollmentResult(token, secret, uri, expires_at)


@transaction.atomic
def confirm_enrollment(
    *, user: User, enrollment_token: str, code: str, current_session_id: uuid.UUID
) -> None:
    now = timezone.now()
    try:
        enrollment = MFAEnrollment.objects.select_for_update().get(
            user=user,
            token_hash=_hash_token(enrollment_token),
            confirmed_at__isnull=True,
            expires_at__gt=now,
        )
    except MFAEnrollment.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    locked_user = User.objects.select_for_update().get(id=user.id)
    secret = decrypt_secret(enrollment.secret_encrypted)
    used_step = matching_totp_step(secret, code)
    if used_step is None:
        raise AuthenticationFailed()
    enrollment.confirmed_at = now
    enrollment.save(update_fields=["confirmed_at", "updated_at"])
    locked_user.mfa_secret_encrypted = enrollment.secret_encrypted
    locked_user.mfa_enabled = True
    locked_user.mfa_last_used_step = used_step
    locked_user.save(
        update_fields=[
            "mfa_secret_encrypted",
            "mfa_enabled",
            "mfa_last_used_step",
            "updated_at",
        ]
    )
    RefreshSession.objects.filter(user=locked_user, revoked_at__isnull=True).exclude(
        id=current_session_id
    ).update(revoked_at=now, revocation_reason="MFA_ENABLED")
    RefreshSession.objects.filter(id=current_session_id, user=locked_user).update(
        mfa_verified_at=now
    )


def create_mfa_challenge(
    *,
    user: User,
    device_name: str,
    ip_address: str | None,
    user_agent: str,
    request_id: str,
) -> tuple[str, datetime]:
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise AuthenticationFailed()
    token = secrets.token_urlsafe(48)
    expires_at = timezone.now() + timedelta(seconds=settings.MFA_CHALLENGE_TTL_SECONDS)
    MFAChallenge.objects.create(
        user=user,
        token_hash=_hash_token(token),
        expires_at=expires_at,
        max_attempts=settings.MFA_CHALLENGE_MAX_ATTEMPTS,
        device_name=device_name[:255],
        ip_address=ip_address,
        user_agent=user_agent[:512],
        request_id=request_id[:128],
    )
    return token, expires_at


@transaction.atomic
def consume_mfa_challenge(*, token: str, code: str) -> MFAChallenge:
    now = timezone.now()
    try:
        challenge = (
            MFAChallenge.objects.select_for_update()
            .select_related("user")
            .get(token_hash=_hash_token(token))
        )
    except MFAChallenge.DoesNotExist as exc:
        raise AuthenticationFailed() from exc
    if (
        challenge.consumed_at
        or challenge.expires_at <= now
        or challenge.attempt_count >= challenge.max_attempts
        or not challenge.user.is_active
        or not challenge.user.mfa_enabled
        or not challenge.user.mfa_secret_encrypted
    ):
        raise AuthenticationFailed()
    challenge.attempt_count += 1
    locked_user = User.objects.select_for_update().get(id=challenge.user_id)
    used_step = matching_totp_step(
        decrypt_secret(locked_user.mfa_secret_encrypted or ""),
        code,
    )
    if used_step is None or (
        locked_user.mfa_last_used_step is not None and used_step <= locked_user.mfa_last_used_step
    ):
        challenge.save(update_fields=["attempt_count", "updated_at"])
        raise AuthenticationFailed()
    locked_user.mfa_last_used_step = used_step
    locked_user.save(update_fields=["mfa_last_used_step", "updated_at"])
    challenge.user = locked_user
    challenge.consumed_at = now
    challenge.save(update_fields=["attempt_count", "consumed_at", "updated_at"])
    return challenge


def has_recent_mfa(session: RefreshSession | None) -> bool:
    if session is None or session.mfa_verified_at is None:
        return False
    return session.mfa_verified_at >= timezone.now() - timedelta(
        seconds=settings.MFA_STEP_UP_TTL_SECONDS
    )
