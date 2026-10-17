import time

from django.test import override_settings

from apps.users.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_totp_secret,
    verify_totp,
)


@override_settings(MFA_ENCRYPTION_KEY="unit-test-encryption-key")
def test_mfa_secret_is_encrypted_and_round_trips() -> None:
    secret = generate_totp_secret()
    encrypted = encrypt_secret(secret)

    assert encrypted != secret
    assert secret not in encrypted
    assert decrypt_secret(encrypted) == secret


def test_totp_accepts_only_current_bounded_window() -> None:
    from apps.users.mfa import _totp

    secret = generate_totp_secret()
    now = int(time.time())
    current = _totp(secret, now // 30)
    stale = _totp(secret, now // 30 - 3)

    assert verify_totp(secret, current, at_time=now)
    assert not verify_totp(secret, stale, at_time=now)
    assert not verify_totp(secret, "not-a-code", at_time=now)
