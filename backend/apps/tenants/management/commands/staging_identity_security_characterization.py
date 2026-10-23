"""Bounded local identity-security characterization for the disposable rehearsal.

The command refuses every settings module other than the staging rehearsal.
It seeds synthetic gyms, keeps passwords and tokens in memory, and sends a
fixed matrix through Caddy and the private API. It prints JSON only. It does
not prove a penetration test, a formal security review, production security,
or a live provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import socket
import ssl
import struct
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.common.identity_security_characterization import (
    DURATION_SECONDS,
    FORGOT,
    INVITE_ACCEPT,
    INVITE_CREATE,
    LOCAL_IDENTITY_FLAGS,
    MEMBER_LOGIN,
    MEMBER_LOGOUT,
    MEMBER_ME,
    MEMBER_REFRESH,
    MFA_CHALLENGE,
    MFA_CONFIRM,
    MFA_ENROLL,
    MFA_WAIT_SECONDS,
    OWNER_ACCEPT,
    PRIVATE_API_HOST,
    PRIVATE_API_PORT,
    PRIVATE_FORGOT,
    PRIVATE_INVITE,
    PRIVATE_RESET,
    PROFILE_NAME,
    PUBLIC_ORIGIN,
    RESET,
    STAFF_COLLECTION,
    STAFF_LOGIN,
    STAFF_LOGOUT,
    STAFF_ME,
    STAFF_REFRESH,
    TIMEOUT_SECONDS,
    assemble,
    assert_identity_route,
    assert_private_target,
    build_plan,
    failure_document,
    location_allowed,
    metrics_delta,
    presented_material_echoed,
    public_outcome,
    resolve_profile,
    unexpected_credential,
    valid_identity_id,
    valid_presented_token,
)
from apps.common.load_characterization import (
    MAX_RESPONSE_BYTES,
    PROOF_FLAGS,
    assert_metrics_target,
    assert_rehearsal_context,
    caddy_metrics_observation,
    clip_body,
    material_leaked,
)
from apps.common.security_characterization import auth_cookie_flags, required_auth_cookies
from apps.members.models import Member
from apps.members.services import create_member, gym_today, link_member_identity
from apps.tenants.management.commands.staging_load_characterization import (
    Client,
    Jar,
    _json_body,
    _refresh_csrf,
    _resolve,
)
from apps.tenants.management.commands.staging_pt_characterization import (
    _install_owner,
    _install_staff,
)
from apps.tenants.models import GymMembership, GymMembershipRole, Role, StaffInvitation
from apps.tenants.services import provision_gym
from apps.tenants.staff_services import invite_staff
from apps.users.identity import get_or_create_invited_identity
from apps.users.mfa import encrypt_secret, generate_totp_secret
from apps.users.models import PasswordResetToken, RefreshSession, User
from apps.users.services import (
    hash_refresh_token,
    request_password_reset,
    reset_password,
    revoke_all_user_sessions,
)

_REQUIRED_SETTINGS = "config.settings.staging_rehearsal"
_REFUSED_CLAIMS = (
    "penetration_test_proven",
    "formal_review_proven",
    "production_security_proven",
    "tenant_isolation_proven",
)
_REQUIRED_PROJECT = "gym-staging-rehearsal"
_GYMS = (
    ("IDGYMA", "Identity Gym A", "owner-id-a@rehearsal.invalid"),
    ("IDGYMB", "Identity Gym B", "owner-id-b@rehearsal.invalid"),
)
_GUESS_RESET = "identity-reset-guess-token-not-issued-0001"
_GUESS_INVITE = "identity-invite-guess-token-not-issued-0001"
_GUESS_OWNER = "identity-owner-guess-token-not-issued-0001"
_UNKNOWN_EMAIL = "nobody-id@rehearsal.invalid"
_SHAPE_PASSWORD = "Identity-Rehearsal-Shape-1"
_COOKIES = {"gms_access", "gms_refresh", "gms_csrf", "gms_mfa_challenge", "gms_selection_ticket"}
_RESET_MARKER = "reset-password?token="
_INVITE_MARKER = "accept-staff-invitation?token="


class IdentityError(CommandError):
    """The identity characterization could not finish a fixed step."""


def _safe_text(exc: BaseException) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "@" in detail or any(word in lowered for word in ("password", "token", "secret", "cookie")):
        return type(exc).__name__
    if len(detail) > 180:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail}"


def _totp(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def _capture_token(marker: str) -> str:
    box = getattr(mail, "outbox", None)
    if not isinstance(box, list):
        raise IdentityError("mailbox")
    for index in range(len(box) - 1, -1, -1):
        body = getattr(box[index], "body", "")
        if not isinstance(body, str) or marker not in body:
            continue
        token = body.split(marker, 1)[1].split()[0].strip()
        del box[index]
        if not valid_presented_token(token):
            raise IdentityError("mailbox")
        return token
    raise IdentityError("mailbox")


def _install_password(email: str, password: str) -> None:
    request_password_reset(
        email=email,
        request_id="identity-seed",
        ip_address=None,
        user_agent="identity",
    )
    token = _capture_token(_RESET_MARKER)
    try:
        reset_password(
            reset_token=token,
            password=password,
            request_id="identity-seed",
            ip_address=None,
            user_agent="identity",
        )
    finally:
        del token


@dataclass
class GymIdentity:
    index: int
    gym_id: uuid.UUID
    gym_code: str
    owner_email: str
    owner_password: str
    owner_user_id: uuid.UUID
    owner_membership_id: uuid.UUID
    manager_email: str
    manager_password: str
    manager_user_id: uuid.UUID | None
    member_email: str
    member_password: str
    member_id: str
    member_user_id: uuid.UUID
    closed_email: str
    closed_password: str
    closed_member_id: str
    trainer_role_id: str
    business_date: str
    owner_jar: Jar = field(default_factory=Jar)
    manager_jar: Jar = field(default_factory=Jar)
    accept_jar: Jar = field(default_factory=Jar)
    member_access: str = ""
    member_refresh: str = ""
    prior_refresh: str = ""
    refreshed_access: str = ""
    logout_refresh: str = ""
    reset_password: str = ""
    reset_token: str = ""
    invite_password: str = ""
    invite_token: str = ""
    enrollment_secret: str = ""
    enrollment_token: str = ""
    mfa_code: str = ""
    mfa_challenge: str = ""
    used_step: int = -1
    secrets: list[str] = field(default_factory=list)

    def remember(self, value: str) -> None:
        if len(value) >= 8:
            self.secrets.append(value)

    def blank(self) -> None:
        self.owner_password = ""
        self.manager_password = ""
        self.member_password = ""
        self.closed_password = ""
        self.member_access = ""
        self.member_refresh = ""
        self.prior_refresh = ""
        self.refreshed_access = ""
        self.logout_refresh = ""
        self.reset_password = ""
        self.reset_token = ""
        self.invite_password = ""
        self.invite_token = ""
        self.enrollment_secret = ""
        self.enrollment_token = ""
        self.mfa_code = ""
        self.mfa_challenge = ""
        self.secrets.clear()
        self.owner_jar.cookies.clear()
        self.manager_jar.cookies.clear()
        self.accept_jar.cookies.clear()


def _linked_member(
    gym_id: uuid.UUID,
    actor: GymMembership,
    *,
    email: str,
    password: str,
    first_name: str,
    today: date,
) -> tuple[str, uuid.UUID]:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        member = create_member(
            gym=actor.gym,
            actor=actor,
            member_number="",
            first_name=first_name,
            last_name="Member",
            phone=None,
            email=None,
            gender=None,
            date_of_birth=None,
            address={},
            join_date=today,
            emergency_contact_name="",
            emergency_contact_phone="",
            notes="",
            profile_photo_asset_id=None,
        )
        clear_gym_context()
    user = get_or_create_invited_identity(
        email=email, first_name=first_name, last_name="Member", phone=""
    )
    _install_password(email, password)
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        link_member_identity(gym=actor.gym, member_id=member.id, user=user)
        clear_gym_context()
    return str(member.id), user.id


def _seed() -> list[GymIdentity]:
    import secrets

    states: list[GymIdentity] = []
    for index, (code, name, owner_email) in enumerate(_GYMS):
        suffix = code[-1].lower()
        owner_password = secrets.token_urlsafe(18)
        manager_password = secrets.token_urlsafe(18)
        member_password = secrets.token_urlsafe(18)
        closed_password = secrets.token_urlsafe(18)
        manager_email = f"manager-id-{suffix}@rehearsal.invalid"
        member_email = f"member-id-{suffix}@rehearsal.invalid"
        closed_email = f"member-mfa-{suffix}@rehearsal.invalid"
        try:
            provisioned = provision_gym(code=code, name=name, owner_email=owner_email)
            owner = _install_owner(provisioned.owner_membership, owner_email, owner_password)
            manager_user_id: uuid.UUID | None = None
            if index == 0:
                _install_staff(
                    owner,
                    email=manager_email,
                    password=manager_password,
                    role_key="MANAGER",
                    staff_code=f"IDMG{code[-1]}",
                    first_name="Identity",
                    last_name="Manager",
                    display_name="Identity Manager",
                    title="Manager",
                    commission=Decimal("0"),
                )
                manager_user_id = User.objects.get(normalized_email=manager_email).id
            with transaction.atomic():
                set_gym_context(gym_id=provisioned.gym.id)
                today = gym_today(provisioned.gym)
                role = Role.objects.get(
                    gym_id=provisioned.gym.id,
                    key="TRAINER",
                    is_active=True,
                    source_template__key="TRAINER",
                )
                clear_gym_context()
            member_id, member_user_id = _linked_member(
                provisioned.gym.id,
                owner,
                email=member_email,
                password=member_password,
                first_name="Identity",
                today=today,
            )
            closed_member_id = ""
            if index == 0:
                closed_member_id, closed_user_id = _linked_member(
                    provisioned.gym.id,
                    owner,
                    email=closed_email,
                    password=closed_password,
                    first_name="Closed",
                    today=today,
                )
                User.objects.filter(id=closed_user_id).update(
                    mfa_enabled=True,
                    mfa_secret_encrypted=encrypt_secret(generate_totp_secret()),
                    mfa_last_used_step=None,
                )
            role_id = valid_identity_id(str(role.id))
            if not role_id:
                raise IdentityError("trainer role id was not canonical")
            revoke_all_user_sessions(owner.user_id, "SEED")
            revoke_all_user_sessions(member_user_id, "SEED")
            if manager_user_id is not None:
                revoke_all_user_sessions(manager_user_id, "SEED")
            if index == 0:
                revoke_all_user_sessions(closed_user_id, "SEED")
        except IdentityError:
            raise
        except Exception as exc:
            raise IdentityError(f"staging identity seed failed: {_safe_text(exc)}") from None
        state = GymIdentity(
            index=index,
            gym_id=provisioned.gym.id,
            gym_code=code,
            owner_email=owner_email,
            owner_password=owner_password,
            owner_user_id=owner.user_id,
            owner_membership_id=owner.id,
            manager_email=manager_email if index == 0 else "",
            manager_password=manager_password if index == 0 else "",
            manager_user_id=manager_user_id,
            member_email=member_email,
            member_password=member_password,
            member_id=member_id,
            member_user_id=member_user_id,
            closed_email=closed_email if index == 0 else "",
            closed_password=closed_password if index == 0 else "",
            closed_member_id=closed_member_id,
            trainer_role_id=role_id,
            business_date=today.isoformat(),
        )
        state.remember(owner_password)
        state.remember(manager_password)
        state.remember(member_password)
        state.remember(closed_password)
        states.append(state)
    connection.close()
    return states


def _envelope(payload: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(payload or b"{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _error_signature(payload: bytes) -> tuple[str, str] | None:
    parsed = _envelope(payload)
    if parsed is None:
        return None
    error = parsed.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    message = error.get("message")
    if type(code) is not str or type(message) is not str:
        return None
    return code, message


def _data(payload: bytes) -> dict[str, Any]:
    parsed = _envelope(payload)
    if parsed is None:
        return {}
    data = parsed.get("data")
    if not isinstance(data, dict):
        return {}
    return data


def _apply_cookies(jar: Jar, headers: Sequence[str]) -> None:
    for header in headers:
        parts = [part.strip() for part in header.split(";") if part.strip()]
        if not parts or "=" not in parts[0]:
            continue
        name, _, value = parts[0].partition("=")
        if name not in _COOKIES:
            continue
        if value == "" or "max-age=0" in header.lower():
            jar.cookies.pop(name, None)
        else:
            jar.cookies[name] = value


def _staff_session_absent(headers: Sequence[str]) -> bool:
    for header in headers:
        pair = header.split(";", 1)[0]
        name, separator, value = pair.partition("=")
        if not separator or name not in {"gms_access", "gms_refresh"}:
            continue
        if value and "max-age=0" not in header.lower():
            return False
    return True


def _challenge_cookie_ok(headers: Sequence[str]) -> bool:
    found = False
    for header in headers:
        if not header.lower().startswith("gms_mfa_challenge="):
            continue
        if "max-age=0" in header.lower():
            continue
        lowered = header.lower()
        if "httponly" not in lowered or "secure" not in lowered:
            return False
        if "samesite=lax" not in lowered or "path=/" not in lowered:
            return False
        found = True
    return found


def _location_ok(location: str | None) -> bool:
    if location is None or location == "":
        return True
    return location_allowed(location)


class Probe:
    def __init__(self, client: Client, states: list[GymIdentity], secrets: list[str]) -> None:
        self.client = client
        self.states = states
        self.secrets = secrets
        self.member_error: tuple[str, str] | None = None
        self.forgot_message: str | None = None
        self.reset_error: str | None = None
        self.invite_error: str | None = None
        self.reset_issued = 0
        self.invite_issued = 0
        self.deadline = 0.0

    def run(self, scenario: str, gym: int | None) -> dict[str, object]:
        try:
            if gym is None:
                return self._edge(scenario)
            state = self.states[gym]
            if scenario in {
                "member_logout_login",
                "member_logout",
                "member_logout_replay",
            }:
                return self._logout(scenario, state)
            if scenario.startswith("mfa_"):
                return self._mfa(scenario, state)
            return self._gym(scenario, state)
        except Exception as exc:
            _ = _safe_text(exc)
            return _unavailable(scenario, gym)

    def _edge(self, scenario: str) -> dict[str, object]:
        state = self.states[0]
        if scenario == "member_login_unknown":
            status, payload, headers, location = self._caddy(
                "POST",
                MEMBER_LOGIN,
                _json_body(
                    {
                        "email": _UNKNOWN_EMAIL,
                        "password": _SHAPE_PASSWORD,
                        "device_name": "identity",
                    }
                ),
            )
            self.member_error = _error_signature(payload)
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=(
                    self.member_error is not None
                    and self.member_error[0] == "AUTHENTICATION_FAILED"
                ),
                needles=[_UNKNOWN_EMAIL.encode(), _SHAPE_PASSWORD.encode()],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_login_shape":
            status, payload, headers, location = self._caddy(
                "POST",
                MEMBER_LOGIN,
                _json_body({"email": _UNKNOWN_EMAIL, "password": _SHAPE_PASSWORD, "gym_id": "x"}),
            )
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 400,
                needles=[_SHAPE_PASSWORD.encode()],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_mfa_closed":
            status, payload, headers, location = self._caddy(
                "POST",
                MEMBER_LOGIN,
                _json_body(
                    {
                        "email": state.closed_email,
                        "password": state.closed_password,
                        "device_name": "identity",
                    }
                ),
            )
            signature = _error_signature(payload)
            hidden = state.closed_member_id.encode()
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=(
                    signature == self.member_error
                    and hidden not in payload
                    and b"requires_mfa" not in payload
                    and b"mfa_" not in payload
                ),
                needles=[state.closed_password.encode(), state.closed_email.encode(), hidden],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_me_absent":
            status, payload, headers, location = self._caddy("GET", MEMBER_ME, None)
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status in {401, 403},
                staff_cookies_forbidden=True,
            )
        if scenario == "member_staff_blocked":
            status, payload, headers, location = self._caddy("GET", STAFF_COLLECTION, None)
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 404,
                staff_cookies_forbidden=True,
            )
        if scenario == "forgot_csrf":
            status, payload, headers, location = self._bff(
                "POST",
                FORGOT,
                Jar(),
                _json_body({"email": _UNKNOWN_EMAIL}),
                csrf=False,
            )
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 403 and b"CSRF_FAILED" in payload,
            )
        if scenario == "forgot_unknown":
            status, payload, headers, location = self._bff(
                "POST", FORGOT, Jar(), _json_body({"email": _UNKNOWN_EMAIL})
            )
            parsed = _envelope(payload)
            message = parsed.get("message") if parsed else None
            data = parsed.get("data") if parsed else None
            self.forgot_message = message if type(message) is str else None
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 200 and data is None and self.forgot_message is not None,
                needles=[_UNKNOWN_EMAIL.encode()],
            )
        if scenario == "forgot_private":
            return self._private(
                scenario, PRIVATE_FORGOT, _json_body({"email": _UNKNOWN_EMAIL}), [_UNKNOWN_EMAIL]
            )
        if scenario == "reset_guess":
            body = _reset_body(_GUESS_RESET, _SHAPE_PASSWORD)
            status, payload, headers, location = self._bff("POST", RESET, Jar(), body)
            self.reset_error = (_error_signature(payload) or ("", ""))[0]
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and self.reset_error == "AUTHENTICATION_FAILED",
                needles=[_GUESS_RESET.encode(), _SHAPE_PASSWORD.encode()],
            )
        if scenario == "reset_shape":
            body = _json_body(
                {
                    "reset_token": _GUESS_RESET,
                    "password": _SHAPE_PASSWORD,
                    "password_confirmation": _SHAPE_PASSWORD,
                    "is_owner": True,
                }
            )
            status, payload, headers, location = self._bff("POST", RESET, Jar(), body)
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 400,
                needles=[_GUESS_RESET.encode(), _SHAPE_PASSWORD.encode()],
            )
        if scenario == "reset_private":
            return self._private(
                scenario,
                PRIVATE_RESET,
                _reset_body(_GUESS_RESET, _SHAPE_PASSWORD),
                [_GUESS_RESET, _SHAPE_PASSWORD],
            )
        if scenario == "invite_guess":
            return self._invite_guess(scenario, record=True)
        if scenario == "invite_guess_replay":
            return self._invite_guess(scenario, record=False)
        if scenario == "invite_shape":
            body = _invite_body(_GUESS_INVITE, _SHAPE_PASSWORD, extra={"is_owner": True})
            status, payload, headers, location = self._bff("POST", INVITE_ACCEPT, Jar(), body)
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 400,
                needles=[_GUESS_INVITE.encode(), _SHAPE_PASSWORD.encode()],
            )
        if scenario == "invite_csrf":
            status, payload, headers, location = self._bff(
                "POST",
                INVITE_ACCEPT,
                Jar(),
                _invite_body(_GUESS_INVITE, _SHAPE_PASSWORD),
                csrf=False,
            )
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 403 and b"CSRF_FAILED" in payload,
                needles=[_GUESS_INVITE.encode(), _SHAPE_PASSWORD.encode()],
            )
        if scenario == "invite_private":
            return self._private(
                scenario,
                PRIVATE_INVITE,
                _invite_body(_GUESS_INVITE, _SHAPE_PASSWORD),
                [_GUESS_INVITE, _SHAPE_PASSWORD],
            )
        if scenario == "owner_invite_guess":
            status, payload, headers, location = self._bff(
                "POST",
                OWNER_ACCEPT,
                Jar(),
                _json_body(
                    {
                        "invitation_token": _GUESS_OWNER,
                        "password": _SHAPE_PASSWORD,
                        "password_confirmation": _SHAPE_PASSWORD,
                    }
                ),
            )
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and b"INVALID_INVITATION" in payload,
                needles=[_GUESS_OWNER.encode(), _SHAPE_PASSWORD.encode()],
            )
        if scenario == "mfa_absent":
            status, payload, headers, location = self._bff(
                "POST", MFA_CHALLENGE, Jar(), _json_body({"code": "000000"})
            )
            return self._outcome(
                scenario,
                None,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _staff_session_absent(headers),
            )
        return _unavailable(scenario, None)

    def _invite_guess(self, scenario: str, *, record: bool) -> dict[str, object]:
        status, payload, headers, location = self._bff(
            "POST", INVITE_ACCEPT, Jar(), _invite_body(_GUESS_INVITE, _SHAPE_PASSWORD)
        )
        code = (_error_signature(payload) or ("", ""))[0]
        if record:
            self.invite_error = code
        return self._outcome(
            scenario,
            None,
            status,
            payload,
            headers,
            location,
            controls=status == 401 and code == "INVALID_INVITATION" and code == self.invite_error,
            needles=[_GUESS_INVITE.encode(), _SHAPE_PASSWORD.encode()],
        )

    def _gym(self, scenario: str, state: GymIdentity) -> dict[str, object]:
        other = self.states[1 - state.index]
        markers = (
            other.gym_code.encode(),
            str(other.gym_id).encode(),
            other.member_id.encode(),
            other.owner_email.encode(),
        )
        if scenario == "member_login":
            return self._member_login(state, scenario, markers)
        if scenario == "member_me":
            return self._member_me(state, state.member_access, scenario, markers, expect_ok=True)
        if scenario == "member_wrong":
            status, payload, headers, location = self._caddy(
                "POST",
                MEMBER_LOGIN,
                _json_body(
                    {
                        "email": state.member_email,
                        "password": _SHAPE_PASSWORD,
                        "device_name": "identity",
                    }
                ),
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=_error_signature(payload) == self.member_error,
                needles=[state.member_email.encode(), _SHAPE_PASSWORD.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_staff_login":
            status, payload, headers, location = self._caddy(
                "POST",
                MEMBER_LOGIN,
                _json_body(
                    {
                        "email": state.owner_email,
                        "password": state.owner_password,
                        "device_name": "identity",
                    }
                ),
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=_error_signature(payload) == self.member_error,
                needles=[state.owner_email.encode(), state.owner_password.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_on_staff_me":
            jar = Jar()
            jar.cookies["gms_access"] = state.member_access
            status, payload, headers, location = self._bff("GET", STAFF_ME, jar, None)
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401,
                needles=[state.member_access.encode(), *markers],
            )
        if scenario == "member_on_staff_refresh":
            jar = Jar()
            jar.cookies["gms_refresh"] = state.member_refresh
            status, payload, headers, location = self._bff("POST", STAFF_REFRESH, jar, b"{}")
            live = _session_live(state.member_refresh)
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and live,
                needles=[state.member_refresh.encode(), *markers],
            )
        if scenario == "staff_login":
            return self._staff_login(state, scenario, markers)
        if scenario == "staff_on_member_me":
            return self._bearer_me(
                scenario,
                state,
                state.owner_jar.cookies.get("gms_access", ""),
                markers,
                live_token=state.owner_jar.cookies.get("gms_refresh", ""),
                live_expected=True,
            )
        if scenario == "staff_on_member_refresh":
            token = state.owner_jar.cookies.get("gms_refresh", "")
            status, payload, headers, location = self._caddy(
                "POST", MEMBER_REFRESH, _json_body({"refresh_token": token})
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _session_live(token),
                needles=[token.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_refresh":
            return self._member_refresh(state, scenario, markers)
        if scenario == "member_refresh_replay":
            status, payload, headers, location = self._caddy(
                "POST", MEMBER_REFRESH, _json_body({"refresh_token": state.prior_refresh})
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and not _session_live(state.member_refresh),
                needles=[state.prior_refresh.encode(), state.member_refresh.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_access_revoked":
            return self._member_me(
                state, state.refreshed_access, scenario, markers, expect_ok=False
            )
        if scenario == "invite_create":
            return self._invite_create(state, scenario, markers)
        if scenario == "invite_accept":
            return self._invite_accept(state, scenario, markers)
        if scenario == "invite_replay":
            return self._invite_replay(state, scenario, markers)
        if scenario == "invite_session":
            status, payload, headers, location = self._bff("GET", STAFF_ME, state.accept_jar, None)
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 200 and state.gym_code.encode() in payload,
                needles=markers,
            )
        if scenario == "forgot_known":
            status, payload, headers, location = self._bff(
                "POST", FORGOT, Jar(), _json_body({"email": state.owner_email})
            )
            parsed = _envelope(payload)
            message = parsed.get("message") if parsed else None
            data = parsed.get("data") if parsed else None
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=(status == 200 and data is None and message == self.forgot_message),
                needles=[state.owner_email.encode(), *markers],
            )
        if scenario == "reset_consume":
            return self._reset_consume(state, scenario, markers)
        if scenario == "reset_replay":
            return self._reset_replay(state, scenario, markers)
        if scenario == "reset_old_session":
            status, payload, headers, location = self._bff("GET", STAFF_ME, state.owner_jar, None)
            live = RefreshSession.objects.filter(
                user_id=state.owner_user_id, revoked_at__isnull=True
            ).exists()
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and not live,
                needles=markers,
            )
        if scenario == "reset_old_login":
            status, payload, headers, location = self._bff(
                "POST",
                STAFF_LOGIN,
                Jar(),
                _json_body(
                    {
                        "email": state.owner_email,
                        "password": state.owner_password,
                        "device_name": "identity",
                    }
                ),
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _staff_session_absent(headers),
                needles=[state.owner_email.encode(), state.owner_password.encode(), *markers],
            )
        return _unavailable(scenario, state.index)

    def _logout(self, scenario: str, state: GymIdentity) -> dict[str, object]:
        other = self.states[1 - state.index]
        markers = (
            other.gym_code.encode(),
            str(other.gym_id).encode(),
            other.member_id.encode(),
            other.owner_email.encode(),
        )
        if scenario == "member_logout_login":
            return self._member_login(state, scenario, markers, logout=True)
        if scenario == "member_logout":
            status, payload, headers, location = self._caddy(
                "POST", MEMBER_LOGOUT, _json_body({"refresh_token": state.logout_refresh})
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 204 and not _session_live(state.logout_refresh),
                needles=[state.logout_refresh.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        if scenario == "member_logout_replay":
            status, payload, headers, location = self._caddy(
                "POST", MEMBER_REFRESH, _json_body({"refresh_token": state.logout_refresh})
            )
            return self._outcome(
                scenario,
                state.index,
                status,
                payload,
                headers,
                location,
                controls=status == 401,
                needles=[state.logout_refresh.encode(), *markers],
                staff_cookies_forbidden=True,
            )
        return _unavailable(scenario, state.index)

    def _mfa(self, scenario: str, state: GymIdentity) -> dict[str, object]:
        if scenario == "mfa_login":
            return self._staff_login(state, scenario, (), manager=True)
        if scenario == "mfa_enroll":
            status, payload, headers, location = self._bff(
                "POST",
                MFA_ENROLL,
                state.manager_jar,
                _json_body({"current_password": state.manager_password}),
            )
            data = _data(payload)
            secret = data.get("secret")
            token = data.get("enrollment_token")
            if type(secret) is str and type(token) is str and valid_presented_token(token):
                state.enrollment_secret = secret
                state.enrollment_token = token
                state.remember(secret)
                state.remember(token)
                state.used_step = int(time.time()) // 30
                state.mfa_code = _totp(secret, state.used_step)
                state.remember(state.mfa_code)
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=bool(state.enrollment_secret and state.mfa_code),
                needles=[state.manager_password.encode()],
            )
        if scenario == "mfa_confirm":
            status, payload, headers, location = self._bff(
                "POST",
                MFA_CONFIRM,
                state.manager_jar,
                _json_body({"enrollment_token": state.enrollment_token, "code": state.mfa_code}),
            )
            confirmed = status == 200 and _data(payload).get("mfa_enabled") is True
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=confirmed and state.enrollment_token.encode() not in payload,
                needles=[state.enrollment_token.encode(), state.enrollment_secret.encode()],
            )
        if scenario == "mfa_relogin":
            self._logout_jar(state.manager_jar)
            return self._mfa_login(state, scenario, bypass=False)
        if scenario == "mfa_bypass":
            return self._mfa_login(state, scenario, bypass=True)
        if scenario == "mfa_me_blocked":
            jar = Jar()
            if state.mfa_challenge:
                jar.cookies["gms_mfa_challenge"] = state.mfa_challenge
            status, payload, headers, location = self._bff("GET", STAFF_ME, jar, None)
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _staff_session_absent(headers),
                needles=[state.mfa_challenge.encode()],
            )
        if scenario == "mfa_wrong":
            wrong = "111111" if state.mfa_code == "000000" else "000000"
            status, payload, headers, location = self._challenge(state.manager_jar, wrong)
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _staff_session_absent(headers),
            )
        if scenario == "mfa_ok":
            code = self._next_code(state)
            if code is None:
                return _unavailable(scenario, 0)
            state.mfa_code = code
            state.remember(code)
            jar = Jar()
            jar.cookies["gms_mfa_challenge"] = state.mfa_challenge
            _refresh_csrf(self.client, jar)
            status, payload, headers, location = self._challenge(jar, code)
            _apply_cookies(state.manager_jar, headers)
            state.manager_jar.cookies.pop("gms_mfa_challenge", None)
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=status == 200 and b"requires_mfa" not in payload,
                needles=[code.encode(), state.mfa_challenge.encode()],
                cookie_headers=headers,
                require_session=True,
            )
        if scenario == "mfa_replay":
            jar = Jar()
            jar.cookies["gms_mfa_challenge"] = state.mfa_challenge
            status, payload, headers, location = self._challenge(jar, state.mfa_code)
            return self._outcome(
                scenario,
                0,
                status,
                payload,
                headers,
                location,
                controls=status == 401 and _staff_session_absent(headers),
                needles=[state.mfa_code.encode(), state.mfa_challenge.encode()],
            )
        return _unavailable(scenario, 0)

    def _next_code(self, state: GymIdentity) -> str | None:
        if not state.enrollment_secret or state.used_step < 0:
            return None
        deadline = min(time.monotonic() + MFA_WAIT_SECONDS, self.deadline)
        while time.monotonic() < deadline:
            step = int(time.time()) // 30
            if step > state.used_step:
                return _totp(state.enrollment_secret, step)
            time.sleep(0.5)
        return None

    def _mfa_login(self, state: GymIdentity, scenario: str, *, bypass: bool) -> dict[str, object]:
        body: dict[str, object] = {
            "email": state.manager_email,
            "password": state.manager_password,
            "device_name": "identity",
        }
        if bypass:
            body["mfa_code"] = state.mfa_code or "000000"
        jar = Jar()
        status, payload, headers, location = self._bff("POST", STAFF_LOGIN, jar, _json_body(body))
        _apply_cookies(state.manager_jar, headers)
        challenge = state.manager_jar.cookies.get("gms_mfa_challenge", "")
        if challenge:
            state.mfa_challenge = challenge
            state.remember(challenge)
        data = _data(payload)
        return self._outcome(
            scenario,
            0,
            status,
            payload,
            headers,
            location,
            controls=(
                status == 200
                and data.get("requires_mfa") is True
                and bool(challenge)
                and _staff_session_absent(headers)
                and _challenge_cookie_ok(headers)
            ),
            needles=[state.manager_password.encode(), challenge.encode()],
        )

    def _logout_jar(self, jar: Jar) -> None:
        self._bff("POST", STAFF_LOGOUT, jar, b"{}")
        jar.cookies.pop("gms_access", None)
        jar.cookies.pop("gms_refresh", None)

    def _challenge(self, jar: Jar, code: str) -> tuple[int | str, bytes, list[str], str | None]:
        return self._bff("POST", MFA_CHALLENGE, jar, _json_body({"code": code}))

    def _member_login(
        self,
        state: GymIdentity,
        scenario: str,
        markers: tuple[bytes, ...],
        *,
        logout: bool = False,
    ) -> dict[str, object]:
        status, payload, headers, location = self._caddy(
            "POST",
            MEMBER_LOGIN,
            _json_body(
                {
                    "email": state.member_email,
                    "password": state.member_password,
                    "device_name": "identity",
                }
            ),
        )
        data = _data(payload)
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        token_ok = (
            type(access) is str
            and type(refresh) is str
            and access.startswith("eyJ")
            and len(refresh) >= 16
            and data.get("member_id") == state.member_id
            and data.get("gym_id") == str(state.gym_id)
        )
        if token_ok and type(access) is str and type(refresh) is str:
            state.remember(access)
            state.remember(refresh)
            if logout:
                state.logout_refresh = refresh
            else:
                state.member_access = access
                state.member_refresh = refresh
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=token_ok and state.member_password.encode() not in payload,
            needles=[state.member_password.encode(), *markers],
            staff_cookies_forbidden=True,
        )

    def _member_refresh(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        state.prior_refresh = state.member_refresh
        status, payload, headers, location = self._caddy(
            "POST", MEMBER_REFRESH, _json_body({"refresh_token": state.member_refresh})
        )
        data = _data(payload)
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        token_ok = type(access) is str and type(refresh) is str and access.startswith("eyJ")
        if token_ok and type(access) is str and type(refresh) is str:
            state.remember(access)
            state.remember(refresh)
            state.refreshed_access = access
            state.member_refresh = refresh
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 200 and token_ok and state.prior_refresh.encode() not in payload,
            needles=[state.prior_refresh.encode(), *markers],
            staff_cookies_forbidden=True,
        )

    def _member_me(
        self,
        state: GymIdentity,
        access: str,
        scenario: str,
        markers: tuple[bytes, ...],
        *,
        expect_ok: bool,
    ) -> dict[str, object]:
        status, payload, headers, location = self._caddy(
            "GET", MEMBER_ME, None, authorization=access
        )
        own = state.member_id.encode() in payload
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=(status == 200 and own) if expect_ok else status == 401,
            needles=[access.encode(), *markers],
            staff_cookies_forbidden=True,
        )

    def _bearer_me(
        self,
        scenario: str,
        state: GymIdentity,
        access: str,
        markers: tuple[bytes, ...],
        *,
        live_token: str,
        live_expected: bool,
    ) -> dict[str, object]:
        status, payload, headers, location = self._caddy(
            "GET", MEMBER_ME, None, authorization=access
        )
        live = _session_live(live_token) if live_token else False
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 401 and live is live_expected,
            needles=[access.encode(), *markers],
            staff_cookies_forbidden=True,
        )

    def _staff_login(
        self,
        state: GymIdentity,
        scenario: str,
        markers: tuple[bytes, ...],
        *,
        manager: bool = False,
    ) -> dict[str, object]:
        email = state.manager_email if manager else state.owner_email
        password = state.manager_password if manager else state.owner_password
        jar = state.manager_jar if manager else state.owner_jar
        status, payload, headers, location = self._bff(
            "POST",
            STAFF_LOGIN,
            jar,
            _json_body({"email": email, "password": password, "device_name": "identity"}),
        )
        _apply_cookies(jar, headers)
        for value in jar.cookies.values():
            state.remember(value)
        return self._outcome(
            scenario,
            0 if manager else state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 200 and b"requires_mfa" not in payload,
            needles=[password.encode(), *markers],
            cookie_headers=headers,
            require_session=True,
        )

    def _invite_create(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        suffix = state.gym_code[-1]
        body = _json_body(
            {
                "email": f"guest-http-{suffix.lower()}@rehearsal.invalid",
                "first_name": "Identity",
                "last_name": "Guest",
                "staff_code": f"IDHT{suffix}",
                "display_name": "Identity Guest",
                "joined_date": state.business_date,
                "employment_title": "Desk",
                "default_commission_percent": "0.00",
                "role_ids": [state.trainer_role_id],
            }
        )
        status, payload, headers, location = self._bff("POST", INVITE_CREATE, state.owner_jar, body)
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 201 and b"PENDING" in payload,
            needles=markers,
        )

    def _invite_accept(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        import secrets

        suffix = state.gym_code[-1]
        email = f"guest-accept-{suffix.lower()}@rehearsal.invalid"
        password = secrets.token_urlsafe(18)
        state.invite_password = password
        state.remember(password)
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            today = gym_today(GymMembership.objects.get(id=state.owner_membership_id).gym)
            role_id = uuid.UUID(state.trainer_role_id)
            clear_gym_context()
        invite_staff(
            gym_id=state.gym_id,
            actor=User.objects.get(id=state.owner_user_id),
            actor_membership_id=state.owner_membership_id,
            staff_data={
                "email": email,
                "first_name": "Accept",
                "last_name": "Guest",
                "staff_code": f"IDAC{suffix}",
                "display_name": "Accept Guest",
                "joined_date": today,
                "employment_title": "Desk",
                "default_commission_percent": Decimal("0"),
            },
            role_ids=[role_id],
            request_id="identity-invite",
            user_agent="identity",
        )
        token = _capture_token(_INVITE_MARKER)
        state.invite_token = token
        state.remember(token)
        self.invite_issued += 1
        status, payload, headers, location = self._bff(
            "POST", INVITE_ACCEPT, state.accept_jar, _invite_body(token, password)
        )
        _apply_cookies(state.accept_jar, headers)
        for value in state.accept_jar.cookies.values():
            state.remember(value)
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 200 and _staff_session_absent(headers) is False,
            needles=[email.encode(), password.encode(), *markers],
            cookie_headers=headers,
            require_session=True,
        )

    def _invite_replay(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        token = state.invite_token
        if not token:
            return _unavailable(scenario, state.index)
        status, payload, headers, location = self._bff(
            "POST",
            INVITE_ACCEPT,
            Jar(),
            _invite_body(token, state.invite_password),
        )
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 401 and b"INVALID_INVITATION" in payload,
            needles=[token.encode(), state.invite_password.encode(), *markers],
        )

    def _reset_consume(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        import secrets

        password = secrets.token_urlsafe(18)
        state.reset_password = password
        state.remember(password)
        request_password_reset(
            email=state.owner_email,
            request_id="identity-reset",
            ip_address=None,
            user_agent="identity",
        )
        token = _capture_token(_RESET_MARKER)
        state.reset_token = token
        state.remember(token)
        self.reset_issued += 1
        status, payload, headers, location = self._bff(
            "POST", RESET, Jar(), _reset_body(token, password)
        )
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 204,
            needles=[password.encode(), state.owner_email.encode(), *markers],
        )

    def _reset_replay(
        self, state: GymIdentity, scenario: str, markers: tuple[bytes, ...]
    ) -> dict[str, object]:
        token = state.reset_token
        if not token:
            return _unavailable(scenario, state.index)
        status, payload, headers, location = self._bff(
            "POST", RESET, Jar(), _reset_body(token, state.reset_password)
        )
        owner = User.objects.get(id=state.owner_user_id)
        changed = owner.check_password(state.reset_password) and not owner.check_password(
            state.owner_password
        )
        return self._outcome(
            scenario,
            state.index,
            status,
            payload,
            headers,
            location,
            controls=status == 401 and changed,
            needles=[token.encode(), state.reset_password.encode(), *markers],
        )

    def _private(
        self,
        scenario: str,
        path: str,
        body: bytes,
        needles: Sequence[str],
    ) -> dict[str, object]:
        assert_identity_route("POST", path, "private")
        status, payload, location = _private_call(path, body)
        encoded = [item.encode() for item in needles]
        return self._outcome(
            scenario,
            None,
            status,
            payload,
            [],
            location,
            controls=status == 403 and b"BFF_REQUIRED" in payload,
            needles=encoded,
        )

    def _outcome(
        self,
        scenario: str,
        gym: int | None,
        status: int | str,
        payload: bytes,
        headers: Sequence[str],
        location: str | None,
        *,
        controls: bool,
        needles: Sequence[bytes] = (),
        staff_cookies_forbidden: bool = False,
        cookie_headers: Sequence[str] | None = None,
        require_session: bool = False,
    ) -> dict[str, object]:
        leaks, secrets = _split_needles(needles)
        credential = unexpected_credential(payload, scenario) or presented_material_echoed(
            payload, secrets
        )
        cookie_ok = True
        if staff_cookies_forbidden:
            cookie_ok = _staff_session_absent(headers)
        if require_session:
            cookie_ok = required_auth_cookies(
                auth_cookie_flags(list(cookie_headers or headers)), secure=True
            )
        leak = False
        if type(status) is int and 200 <= status < 300:
            leak = any(marker and marker in payload for marker in leaks)
        return public_outcome(
            scenario=scenario,
            gym=gym,
            status=status,
            leak=leak,
            credential=credential,
            redirect_ok=_location_ok(location),
            cookie_ok=cookie_ok,
            controls_ok=controls,
        )

    def _caddy(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        authorization: str = "",
    ) -> tuple[int | str, bytes, list[str], str | None]:
        assert_identity_route(method, path, "caddy")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authorization:
            headers["Authorization"] = f"Bearer {authorization}"
        return self._raw(method, path, headers=headers, body=body)

    def _bff(
        self,
        method: str,
        path: str,
        jar: Jar,
        body: bytes | None,
        *,
        csrf: bool = True,
    ) -> tuple[int | str, bytes, list[str], str | None]:
        assert_identity_route(method, path, "bff")
        if csrf and method != "GET":
            _refresh_csrf(self.client, jar)
        headers = {
            "Origin": PUBLIC_ORIGIN,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Cookie": jar.header(),
        }
        if csrf and method != "GET":
            headers["x-csrf-token"] = jar.cookies.get("gms_csrf", "")
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, payload, set_cookies, location = self._raw(method, path, headers=headers, body=body)
        _apply_cookies(jar, set_cookies)
        return status, payload, set_cookies, location

    def _raw(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int | str, bytes, list[str], str | None]:
        conn: http.client.HTTPSConnection | None = None
        try:
            conn = http.client.HTTPSConnection("localhost", 443, timeout=self.client.timeout)
            sock = socket.create_connection((self.client._ip, 443), self.client.timeout)
            tls = self.client._ssl.wrap_socket(sock, server_hostname="localhost")
            conn.sock = tls
            request_headers = {"Host": "localhost", "Connection": "close", **headers}
            conn.request(method, path, body=body, headers=request_headers)
            response = conn.getresponse()
            status = int(response.status)
            location = response.getheader("Location")
            set_cookies = [
                value for key, value in response.getheaders() if key.lower() == "set-cookie"
            ]
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            clipped, _truncated = clip_body(payload)
            return status, clipped, set_cookies, location
        except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException):
            return "timeout", b"", [], None
        finally:
            if conn is not None:
                conn.close()


def _split_needles(needles: Sequence[bytes]) -> tuple[list[bytes], list[bytes]]:
    leaks: list[bytes] = []
    secrets: list[bytes] = []
    for item in needles:
        if type(item) is not bytes:
            continue
        text = item.decode("utf-8", "replace")
        cross = (
            "@" in text or text.startswith("IDGYM") or (len(text) == 36 and text.count("-") == 4)
        )
        if cross:
            leaks.append(item)
        else:
            secrets.append(item)
    return leaks, secrets


def _reset_body(token: str, password: str) -> bytes:
    return _json_body(
        {
            "reset_token": token,
            "password": password,
            "password_confirmation": password,
        }
    )


def _invite_body(token: str, password: str, extra: dict[str, object] | None = None) -> bytes:
    body: dict[str, object] = {
        "invitation_token": token,
        "password": password,
        "password_confirmation": password,
        "first_name": "Identity",
        "last_name": "Guest",
    }
    if extra:
        body.update(extra)
    return _json_body(body)


def _session_live(token: str) -> bool:
    if not token:
        return False
    return RefreshSession.objects.filter(
        token_hash=hash_refresh_token(token), revoked_at__isnull=True
    ).exists()


def _unavailable(scenario: str, gym: int | None) -> dict[str, object]:
    return public_outcome(
        scenario=scenario,
        gym=gym,
        status="unavailable",
        leak=False,
        credential=False,
        redirect_ok=True,
        cookie_ok=True,
        controls_ok=False,
    )


def _private_call(path: str, body: bytes) -> tuple[int | str, bytes, str | None]:
    ips = _resolve(PRIVATE_API_HOST, PRIVATE_API_PORT)
    assert_private_target(PRIVATE_API_HOST, PRIVATE_API_PORT, path, ips)
    last: tuple[int | str, bytes, str | None] = ("timeout", b"", None)
    for ip in ips:
        result = _private_once(ip, path, body)
        if result[0] != "timeout":
            return result
        last = result
    return last


def _private_once(ip: str, path: str, body: bytes) -> tuple[int | str, bytes, str | None]:
    headers = {
        "Host": PRIVATE_API_HOST,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "X-Forwarded-Proto": "https",
    }
    conn: http.client.HTTPConnection | None = None
    try:
        conn = http.client.HTTPConnection(ip, PRIVATE_API_PORT, timeout=TIMEOUT_SECONDS)
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        clipped, _truncated = clip_body(payload)
        return int(response.status), clipped, response.getheader("Location")
    except (TimeoutError, OSError, http.client.HTTPException):
        return "timeout", b"", None
    finally:
        if conn is not None:
            conn.close()


def _snapshot(states: Sequence[GymIdentity]) -> list[dict[str, object]]:
    manager_id = next((state.manager_user_id for state in states if state.manager_user_id), None)
    mfa_enabled = False
    if manager_id is not None:
        mfa_enabled = User.objects.filter(id=manager_id, mfa_enabled=True).exists()
    rows: list[dict[str, object]] = []
    for state in states:
        with transaction.atomic():
            set_gym_context(gym_id=state.gym_id)
            try:
                member = Member.objects.get(id=state.member_id, gym_id=state.gym_id)
                pending = StaffInvitation.objects.filter(
                    gym_id=state.gym_id, status=StaffInvitation.Status.PENDING
                ).count()
                accepted_rows = list(
                    StaffInvitation.objects.filter(
                        gym_id=state.gym_id,
                        status=StaffInvitation.Status.ACCEPTED,
                        staffinvitationrole__role__key="TRAINER",
                    )
                    .distinct()
                    .values_list("gym_membership_id", flat=True)
                )
                accepted_owner = False
                accepted_trainer = False
                if accepted_rows:
                    accepted_owner = GymMembership.objects.filter(
                        id__in=accepted_rows, is_owner=True
                    ).exists()
                    accepted_trainer = (
                        GymMembershipRole.objects.filter(
                            gym_membership_id__in=accepted_rows, role__key="TRAINER"
                        ).count()
                        == 1
                        and len(accepted_rows) == 1
                    )
                owner_is_owner = GymMembership.objects.filter(
                    id=state.owner_membership_id, is_owner=True
                ).exists()
                manager_is_owner = False
                if state.manager_user_id is not None:
                    manager_is_owner = GymMembership.objects.filter(
                        gym_id=state.gym_id,
                        user_id=state.manager_user_id,
                        is_owner=True,
                    ).exists()
            finally:
                clear_gym_context()
        owner = User.objects.get(id=state.owner_user_id)
        changed = False
        if state.reset_password:
            changed = owner.check_password(state.reset_password) and not owner.check_password(
                state.owner_password
            )
        reset_live = PasswordResetToken.objects.filter(
            user_id=state.owner_user_id, consumed_at__isnull=True, revoked_at__isnull=True
        ).count()
        reset_consumed = PasswordResetToken.objects.filter(
            user_id=state.owner_user_id, consumed_at__isnull=False
        ).exists()
        member_live = RefreshSession.objects.filter(
            user_id=state.member_user_id,
            principal_type=RefreshSession.PrincipalType.MEMBER,
            revoked_at__isnull=True,
        ).count()
        owner_live = RefreshSession.objects.filter(
            user_id=state.owner_user_id, revoked_at__isnull=True
        ).count()
        rows.append(
            {
                "gym": state.index,
                "member_status": member.status,
                "member_linked": member.linked_user_id == state.member_user_id,
                "member_live_sessions": member_live,
                "owner_password_changed": changed,
                "owner_live_sessions": owner_live,
                "reset_live": reset_live,
                "reset_consumed": reset_consumed,
                "invitation_pending": pending,
                "invitation_accepted": len(accepted_rows),
                "accepted_owner": accepted_owner,
                "accepted_trainer": accepted_trainer,
                "owner_is_owner": owner_is_owner,
                "manager_is_owner": manager_is_owner,
                "mfa_enabled": mfa_enabled,
            }
        )
    return rows


def _scrape() -> tuple[bool, int | None]:
    try:
        ips = _resolve("caddy", 2020)
        assert_metrics_target("caddy", 2020, ips)
        conn = http.client.HTTPConnection(ips[0], 2020, timeout=3)
        try:
            conn.request("GET", "/metrics", headers={"Host": "caddy", "Connection": "close"})
            response = conn.getresponse()
            if response.status != 200:
                return False, None
            text = response.read(1_000_000).decode("utf-8", "replace")
        finally:
            conn.close()
    except (OSError, ValueError):
        return False, None
    return caddy_metrics_observation(text)


def _blank(states: Sequence[GymIdentity]) -> None:
    for state in states:
        state.blank()


def run_characterization(cafile: Path) -> dict[str, Any]:
    if settings.SETTINGS_MODULE != _REQUIRED_SETTINGS:
        raise IdentityError(
            "staging identity characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_REHEARSAL") != "1":
        raise IdentityError(
            "staging identity characterization requires the staging rehearsal settings"
        )
    if os.environ.get("STAGING_IDENTITY_PROJECT") != _REQUIRED_PROJECT:
        raise IdentityError("staging identity characterization requires the disposable project")
    if os.environ.get("STAGING_IDENTITY_PROFILE") != PROFILE_NAME:
        raise IdentityError("staging identity characterization requires the fixed profile")
    for flag in (*PROOF_FLAGS, *LOCAL_IDENTITY_FLAGS, *_REFUSED_CLAIMS):
        if os.environ.get(flag) == "1":
            raise IdentityError("staging identity characterization refuses a live proof claim")
    assert_rehearsal_context(
        settings_module=settings.SETTINGS_MODULE,
        staging_flag=bool(getattr(settings, "STAGING_REHEARSAL", False)),
        project=os.environ.get("STAGING_IDENTITY_PROJECT", ""),
        public_url=str(getattr(settings, "APP_PUBLIC_URL", "")),
        database_host=str(settings.DATABASES["default"].get("HOST", "")),
        database_user=str(settings.DATABASES["default"].get("USER", "")),
        allowed_hosts=tuple(settings.ALLOWED_HOSTS),
    )
    resolve_profile(PROFILE_NAME)
    plan = build_plan()
    states = _seed()
    secrets = [_GUESS_RESET, _GUESS_INVITE, _GUESS_OWNER, _SHAPE_PASSWORD, _UNKNOWN_EMAIL]
    for state in states:
        secrets.extend(state.secrets)
        secrets.extend(
            item
            for item in (
                state.owner_email,
                state.manager_email,
                state.member_email,
                state.closed_email,
            )
            if item
        )
    try:
        client = Client(cafile, TIMEOUT_SECONDS)
        probe = Probe(client, states, secrets)
        before = _scrape()
        started = time.perf_counter()
        probe.deadline = started + DURATION_SECONDS - TIMEOUT_SECONDS
        outcomes: list[dict[str, object]] = []
        cancelled = 0
        for ordinal, step in enumerate(plan):
            if time.perf_counter() - started > DURATION_SECONDS:
                cancelled = len(plan) - ordinal
                break
            scenario = step["scenario"]
            gym = step["gym"]
            if type(scenario) is not str or (gym is not None and type(gym) is not int):
                raise IdentityError("identity plan step is invalid")
            outcomes.append(probe.run(scenario, gym if type(gym) is int else None))
        wall = time.perf_counter() - started
        after = _scrape()
        for state in states:
            secrets.extend(state.secrets)
            secrets.extend(state.owner_jar.cookies.values())
            secrets.extend(state.manager_jar.cookies.values())
            secrets.extend(state.accept_jar.cookies.values())
        snapshots = _snapshot(states)
        report = assemble(
            outcomes=outcomes,
            snapshots=snapshots,
            cancelled=cancelled,
            wall_seconds=wall,
            metrics=metrics_delta(before, after),
            token_sources_used=probe.reset_issued == 2 and probe.invite_issued == 2,
        )
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(rendered, secrets):
            raise IdentityError("identity evidence contains secret material")
        return report
    finally:
        _blank(states)
        secrets.clear()


def characterize(cafile: Path) -> dict[str, Any]:
    try:
        return run_characterization(cafile)
    except Exception as exc:
        report = failure_document(PROFILE_NAME, "characterization")
        report["failure_detail"] = _safe_text(exc)
        report["gate_reasons"] = ["characterization"]
        return report


class Command(BaseCommand):
    help = (
        "Run the fixed local identity-security characterization. "
        "Not a penetration test. service-local-mailbox tokens stay in memory."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        cafile = Path(os.environ.get("STAGING_IDENTITY_CA_FILE", ""))
        report = characterize(cafile)
        rendered = json.dumps(report, sort_keys=True)
        if material_leaked(
            rendered,
            (_GUESS_RESET, _GUESS_INVITE, _GUESS_OWNER, _SHAPE_PASSWORD, _UNKNOWN_EMAIL),
        ):
            report = failure_document(PROFILE_NAME, "evidence")
            report["failure_detail"] = "secret material"
            rendered = json.dumps(report, sort_keys=True)
        self.stdout.write(rendered)
