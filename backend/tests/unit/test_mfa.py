import time
from types import SimpleNamespace

import pytest
from django.test import override_settings

from apps.tenants.exceptions import MFARequired
from apps.users.mfa import (
    decrypt_secret,
    encrypt_secret,
    generate_totp_secret,
    session_mfa_recent,
    verify_totp,
)
from apps.users.permissions import RequiresRecentMFA


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


def test_local_development_skips_step_up() -> None:
    request = SimpleNamespace(user=SimpleNamespace(mfa_enabled=False), refresh_session=None)
    with override_settings(MFA_ENFORCED=False):
        assert session_mfa_recent(None) is True
        assert RequiresRecentMFA().has_permission(request, object()) is True


def test_step_up_stays_required_when_mfa_is_enforced() -> None:
    request = SimpleNamespace(user=SimpleNamespace(mfa_enabled=False), refresh_session=None)
    with override_settings(MFA_ENFORCED=True):
        assert session_mfa_recent(None) is False
        with pytest.raises(MFARequired):
            RequiresRecentMFA().has_permission(request, object())
