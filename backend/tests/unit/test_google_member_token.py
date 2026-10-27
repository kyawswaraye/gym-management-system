from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.common.api.exceptions import (
    AuthenticationFailed,
    GoogleSignInNotConfigured,
    MemberAccountAmbiguous,
    MemberAccountInactive,
    MemberAccountMissing,
)
from apps.users.google_login import (
    MemberEmailCandidate,
    select_google_member,
    verify_google_id_token,
)

CLIENT_ID = "member-app.apps.googleusercontent.com"


def _candidate(
    *,
    status: str = "ACTIVE",
    gym_eligible: bool = True,
    member_id: uuid.UUID | None = None,
) -> MemberEmailCandidate:
    return MemberEmailCandidate(
        member_id=member_id or uuid.uuid4(),
        gym_id=uuid.uuid4(),
        status=status,
        linked_user_id=None,
        authz_version=1,
        gym_eligible=gym_eligible,
    )


def _token(private_key: object, **claims: object) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-subject",
        "email": "Ada@Example.test",
        "email_verified": True,
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    encoded = jwt.encode(payload, private_key, algorithm="RS256")
    assert isinstance(encoded, str)
    return encoded


def test_select_google_member_accepts_one_active_gym() -> None:
    chosen = _candidate()
    suspended = _candidate(status="SUSPENDED")
    assert select_google_member([suspended, chosen]) == chosen


def test_select_google_member_rejects_missing_inactive_and_ambiguous() -> None:
    active = _candidate()
    with pytest.raises(MemberAccountMissing):
        select_google_member([])
    with pytest.raises(MemberAccountInactive):
        select_google_member([_candidate(status="SUSPENDED")])
    with pytest.raises(MemberAccountInactive):
        select_google_member([_candidate(gym_eligible=False)])
    with pytest.raises(MemberAccountAmbiguous):
        select_google_member([active, _candidate()])


def test_google_token_returns_the_verified_lowercase_email(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.GOOGLE_OAUTH_CLIENT_IDS = (CLIENT_ID,)  # type: ignore[attr-defined]
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "apps.users.google_login._signing_key",
        lambda token: private_key.public_key(),
    )
    assert verify_google_id_token(_token(private_key)) == "ada@example.test"


def test_google_token_rejects_an_unverified_or_wrong_audience(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.GOOGLE_OAUTH_CLIENT_IDS = (CLIENT_ID,)  # type: ignore[attr-defined]
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "apps.users.google_login._signing_key",
        lambda token: private_key.public_key(),
    )
    with pytest.raises(AuthenticationFailed):
        verify_google_id_token(_token(private_key, email_verified=False))
    with pytest.raises(AuthenticationFailed):
        verify_google_id_token(_token(private_key, aud="other.apps.googleusercontent.com"))


def test_google_token_does_not_fetch_keys_when_sign_in_is_not_configured(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.GOOGLE_OAUTH_CLIENT_IDS = ()  # type: ignore[attr-defined]

    def fail(token: str) -> object:
        raise AssertionError(token)

    monkeypatch.setattr("apps.users.google_login._signing_key", fail)
    with pytest.raises(GoogleSignInNotConfigured):
        verify_google_id_token("header.payload.signature")
