"""Pure rules for the disposable local identity-security characterization.

This module does not open sockets, import Django, or read credentials. The
staging management command and the rehearsal harness apply these rules. A
local pass is project-authored verification on the rehearsal stack. It is
not a penetration test, a formal security review, production security proof,
or a live-provider proof.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.common.load_characterization import MAX_RESPONSE_BYTES, PROOF_FLAGS, material_leaked
from apps.common.security_characterization import (
    metrics_delta,
    redirect_accepted,
    valid_resource_id,
)

SCHEMA = "gym-staging-identity-security/v1"
PROJECT = "gym-staging-rehearsal"
KIND = "local-disposable-identity-security-characterization"
PROFILE_NAME = "identity-security"
EVIDENCE_NAME = "identity-security.json"
SETTINGS_MODULE = "config.settings.staging_rehearsal"
DURATION_SECONDS = 180
TIMEOUT_SECONDS = 8.0
MFA_WAIT_SECONDS = 35
PUBLIC_ORIGIN = "https://localhost:8443"
PUBLIC_HOST = "localhost"
PRIVATE_API_HOST = "api"
PRIVATE_API_PORT = 8000
CONNECT_HOST = "caddy"
REQUEST_PORT = 443
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
TOKEN_SOURCE = "service-local-mailbox"
LOCAL_IDENTITY_FLAGS = (
    "penetration_test_proven",
    "formal_review_proven",
    "production_security_proven",
)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_JWT_BYTES = re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_EVIDENCE_MARKERS = (
    "access_token",
    "refresh_token",
    "mfa_challenge_token",
    "invitation_token",
    "reset_token",
    "enrollment_token",
    "otpauth://",
    "gms_access=",
    "gms_refresh=",
    "gms_mfa_challenge=",
    "token=",
    "password=",
)
_BODY_MARKERS = (
    b"access_token",
    b"refresh_token",
    b"gym_selection_ticket",
    b"mfa_challenge_token",
    b"invitation_token",
    b"reset_token",
    b"enrollment_token",
    b"otpauth://",
    b'"secret"',
    b"token=",
    b"password=",
)
_MEMBER_TOKEN_OK = frozenset({b"access_token", b"refresh_token"})
_ENROLL_OK = frozenset({b"enrollment_token", b"otpauth://", b'"secret"'})

MEMBER_LOGIN = "/api/v1/member-auth/login/"
MEMBER_REFRESH = "/api/v1/member-auth/refresh/"
MEMBER_LOGOUT = "/api/v1/member-auth/logout/"
MEMBER_ME = "/api/v1/member/me/"
STAFF_COLLECTION = "/api/v1/members/"
FORGOT = "/api/auth/password/forgot"
RESET = "/api/auth/password/reset"
INVITE_ACCEPT = "/api/auth/accept-staff-invitation"
OWNER_ACCEPT = "/api/auth/accept-owner-invitation"
MFA_CHALLENGE = "/api/auth/mfa/challenge"
STAFF_LOGIN = "/api/auth/login"
STAFF_LOGOUT = "/api/auth/logout"
STAFF_ME = "/api/auth/me"
STAFF_REFRESH = "/api/auth/refresh"
MFA_ENROLL = "/api/operations/auth/mfa/enroll"
MFA_CONFIRM = "/api/operations/auth/mfa/enroll/confirm"
INVITE_CREATE = "/api/operations/staff/invitations"
PRIVATE_FORGOT = "/api/v1/auth/password/forgot/"
PRIVATE_RESET = "/api/v1/auth/password/reset/"
PRIVATE_INVITE = "/api/v1/staff/invitations/accept/"

CADDY_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", MEMBER_LOGIN),
    ("POST", MEMBER_REFRESH),
    ("POST", MEMBER_LOGOUT),
    ("GET", MEMBER_ME),
    ("GET", STAFF_COLLECTION),
)
BFF_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", FORGOT),
    ("POST", RESET),
    ("POST", INVITE_ACCEPT),
    ("POST", OWNER_ACCEPT),
    ("POST", MFA_CHALLENGE),
    ("POST", STAFF_LOGIN),
    ("POST", STAFF_LOGOUT),
    ("GET", STAFF_ME),
    ("POST", STAFF_REFRESH),
    ("POST", MFA_ENROLL),
    ("POST", MFA_CONFIRM),
    ("POST", INVITE_CREATE),
)
PRIVATE_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", PRIVATE_FORGOT),
    ("POST", PRIVATE_RESET),
    ("POST", PRIVATE_INVITE),
)
_ROUTES = {
    "caddy": CADDY_ROUTES,
    "bff": BFF_ROUTES,
    "private": PRIVATE_ROUTES,
}

EDGE: tuple[str, ...] = (
    "member_login_unknown",
    "member_login_shape",
    "member_mfa_closed",
    "member_me_absent",
    "member_staff_blocked",
    "forgot_csrf",
    "forgot_unknown",
    "forgot_private",
    "reset_guess",
    "reset_shape",
    "reset_private",
    "invite_guess",
    "invite_guess_replay",
    "invite_shape",
    "invite_csrf",
    "invite_private",
    "owner_invite_guess",
    "mfa_absent",
)
GYM: tuple[str, ...] = (
    "member_login",
    "member_me",
    "member_wrong",
    "member_staff_login",
    "member_on_staff_me",
    "member_on_staff_refresh",
    "staff_login",
    "staff_on_member_me",
    "staff_on_member_refresh",
    "member_refresh",
    "member_refresh_replay",
    "member_access_revoked",
    "invite_create",
    "invite_accept",
    "invite_replay",
    "invite_session",
    "forgot_known",
    "reset_consume",
    "reset_replay",
    "reset_old_session",
    "reset_old_login",
)
LOGOUT: tuple[str, ...] = (
    "member_logout_login",
    "member_logout",
    "member_logout_replay",
)
MFA: tuple[str, ...] = (
    "mfa_login",
    "mfa_enroll",
    "mfa_confirm",
    "mfa_relogin",
    "mfa_bypass",
    "mfa_me_blocked",
    "mfa_wrong",
    "mfa_ok",
    "mfa_replay",
)
PLAN_OPERATIONS = len(EDGE) + 2 * len(GYM) + len(LOGOUT) + len(MFA)
_EXPECTED_MATERIAL = {
    "member_login": _MEMBER_TOKEN_OK,
    "member_refresh": _MEMBER_TOKEN_OK,
    "member_logout_login": _MEMBER_TOKEN_OK,
    "mfa_enroll": _ENROLL_OK,
}
UNCOVERED: tuple[dict[str, object], ...] = (
    {
        "id": "http_forgot_mailbox_roundtrip",
        "covered": False,
        "note": (
            "The published forgot response does not return a reset credential. "
            "The API worker mailbox is not shared, so that emailed round trip is not read."
        ),
    },
    {
        "id": "http_invitation_mailbox_roundtrip",
        "covered": False,
        "note": (
            "The published invitation create response does not return an invitation "
            "credential. The API worker mailbox is not shared."
        ),
    },
    {
        "id": "owner_invitation_acceptance",
        "covered": False,
        "note": "Owner invitation guessing is covered. A real owner invitation is not issued.",
    },
    {
        "id": "member_mfa_challenge",
        "covered": False,
        "note": "Member login fails closed when MFA is enabled. No member challenge route exists.",
    },
    {
        "id": "edge_rate_limit",
        "covered": False,
        "note": "Edge and WAF rate limits are not sampled.",
    },
    {
        "id": "platform_admin",
        "covered": False,
        "note": "Platform administration routes are not probed.",
    },
    {
        "id": "live_provider",
        "covered": False,
        "note": "Live email delivery is not proven.",
    },
)
_UNCOVERED_IDS = tuple(str(item["id"]) for item in UNCOVERED)


class IdentitySpecError(ValueError):
    """A fixed profile or evidence document violated a local rule."""


@dataclass(frozen=True)
class ScenarioRule:
    success: frozenset[int]
    rejection: frozenset[int]
    transport: str
    phase: str


def _rule(
    success: frozenset[int],
    rejection: frozenset[int],
    transport: str,
    phase: str,
) -> ScenarioRule:
    if success & rejection:
        raise IdentitySpecError("scenario success and rejection overlap")
    if 500 in success or 500 in rejection:
        raise IdentitySpecError("scenario treats an internal error as expected")
    if transport not in _ROUTES:
        raise IdentitySpecError("scenario transport is outside the catalog")
    return ScenarioRule(success, rejection, transport, phase)


def _catalog() -> dict[str, ScenarioRule]:
    edge = {
        "member_login_unknown": _rule(frozenset(), frozenset({401}), "caddy", "edge"),
        "member_login_shape": _rule(frozenset(), frozenset({400}), "caddy", "edge"),
        "member_mfa_closed": _rule(frozenset(), frozenset({401}), "caddy", "edge"),
        "member_me_absent": _rule(frozenset(), frozenset({401, 403}), "caddy", "edge"),
        "member_staff_blocked": _rule(frozenset(), frozenset({404}), "caddy", "edge"),
        "forgot_csrf": _rule(frozenset(), frozenset({403}), "bff", "edge"),
        "forgot_unknown": _rule(frozenset({200}), frozenset(), "bff", "edge"),
        "forgot_private": _rule(frozenset(), frozenset({403}), "private", "edge"),
        "reset_guess": _rule(frozenset(), frozenset({401}), "bff", "edge"),
        "reset_shape": _rule(frozenset(), frozenset({400}), "bff", "edge"),
        "reset_private": _rule(frozenset(), frozenset({403}), "private", "edge"),
        "invite_guess": _rule(frozenset(), frozenset({401}), "bff", "edge"),
        "invite_guess_replay": _rule(frozenset(), frozenset({401}), "bff", "edge"),
        "invite_shape": _rule(frozenset(), frozenset({400}), "bff", "edge"),
        "invite_csrf": _rule(frozenset(), frozenset({403}), "bff", "edge"),
        "invite_private": _rule(frozenset(), frozenset({403}), "private", "edge"),
        "owner_invite_guess": _rule(frozenset(), frozenset({401}), "bff", "edge"),
        "mfa_absent": _rule(frozenset(), frozenset({401}), "bff", "edge"),
    }
    gym = {
        "member_login": _rule(frozenset({200}), frozenset(), "caddy", "gym"),
        "member_me": _rule(frozenset({200}), frozenset(), "caddy", "gym"),
        "member_wrong": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "member_staff_login": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "member_on_staff_me": _rule(frozenset(), frozenset({401}), "bff", "gym"),
        "member_on_staff_refresh": _rule(frozenset(), frozenset({401}), "bff", "gym"),
        "staff_login": _rule(frozenset({200}), frozenset(), "bff", "gym"),
        "staff_on_member_me": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "staff_on_member_refresh": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "member_refresh": _rule(frozenset({200}), frozenset(), "caddy", "gym"),
        "member_refresh_replay": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "member_access_revoked": _rule(frozenset(), frozenset({401}), "caddy", "gym"),
        "invite_create": _rule(frozenset({201}), frozenset(), "bff", "gym"),
        "invite_accept": _rule(frozenset({200}), frozenset(), "bff", "gym"),
        "invite_replay": _rule(frozenset(), frozenset({401}), "bff", "gym"),
        "invite_session": _rule(frozenset({200}), frozenset(), "bff", "gym"),
        "forgot_known": _rule(frozenset({200}), frozenset(), "bff", "gym"),
        "reset_consume": _rule(frozenset({204}), frozenset(), "bff", "gym"),
        "reset_replay": _rule(frozenset(), frozenset({401}), "bff", "gym"),
        "reset_old_session": _rule(frozenset(), frozenset({401}), "bff", "gym"),
        "reset_old_login": _rule(frozenset(), frozenset({401}), "bff", "gym"),
    }
    logout = {
        "member_logout_login": _rule(frozenset({200}), frozenset(), "caddy", "logout"),
        "member_logout": _rule(frozenset({204}), frozenset(), "caddy", "logout"),
        "member_logout_replay": _rule(frozenset(), frozenset({401}), "caddy", "logout"),
    }
    mfa = {
        "mfa_login": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_enroll": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_confirm": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_relogin": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_bypass": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_me_blocked": _rule(frozenset(), frozenset({401}), "bff", "mfa"),
        "mfa_wrong": _rule(frozenset(), frozenset({401}), "bff", "mfa"),
        "mfa_ok": _rule(frozenset({200}), frozenset(), "bff", "mfa"),
        "mfa_replay": _rule(frozenset(), frozenset({401}), "bff", "mfa"),
    }
    rules = {**edge, **gym, **logout, **mfa}
    names = (*EDGE, *GYM, *LOGOUT, *MFA)
    if tuple(rules) != names or len(rules) != len(names):
        raise IdentitySpecError("scenario catalog drifted")
    return rules


RULES = _catalog()


def require_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise IdentitySpecError("integer bound was not an integer")
    if value < minimum or value > maximum:
        raise IdentitySpecError("integer bound was outside the fixed range")
    return value


def resolve_profile(name: object) -> str:
    if name != PROFILE_NAME:
        raise IdentitySpecError("identity profile is not the fixed profile")
    require_int(PLAN_OPERATIONS, minimum=1, maximum=80)
    require_int(DURATION_SECONDS, minimum=1, maximum=180)
    require_int(MFA_WAIT_SECONDS, minimum=1, maximum=35)
    if len(UNCOVERED) != len(_UNCOVERED_IDS) or len(set(_UNCOVERED_IDS)) != len(_UNCOVERED_IDS):
        raise IdentitySpecError("uncovered claims drifted")
    return PROFILE_NAME


def build_plan() -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    for scenario in EDGE:
        plan.append({"scenario": scenario, "gym": None, "phase": "edge"})
    for gym in (0, 1):
        for scenario in GYM:
            plan.append({"scenario": scenario, "gym": gym, "phase": "gym"})
    for scenario in LOGOUT:
        plan.append({"scenario": scenario, "gym": 0, "phase": "logout"})
    for scenario in MFA:
        plan.append({"scenario": scenario, "gym": 0, "phase": "mfa"})
    if len(plan) != PLAN_OPERATIONS:
        raise IdentitySpecError("identity plan drifted")
    return plan


def _path_shape_rejected(path: str) -> bool:
    if not path.startswith("/") or path.startswith("//"):
        return True
    lowered = path.lower()
    if any(marker in lowered for marker in ("%", "\\", "#", "@", " ", "\n", "\r")):
        return True
    return ".." in path


def assert_identity_route(method: object, path: object, transport: object) -> None:
    if type(method) is not str or type(path) is not str or type(transport) is not str:
        raise IdentitySpecError("identity path is outside the catalog")
    if transport not in _ROUTES or _path_shape_rejected(path) or "?" in path:
        raise IdentitySpecError("identity path is outside the catalog")
    if (method, path) not in _ROUTES[transport]:
        raise IdentitySpecError("identity path is outside the catalog")


def assert_private_target(host: str, port: int, path: str, resolved: Sequence[str]) -> None:
    if host != PRIVATE_API_HOST or port != PRIVATE_API_PORT:
        raise IdentitySpecError("private probe is outside the identity catalog")
    if ("POST", path) not in PRIVATE_ROUTES:
        raise IdentitySpecError("private probe is outside the identity catalog")
    if not resolved or any(not _ip_is_local(item) for item in resolved):
        raise IdentitySpecError("private probe address is not loopback or private")


def _ip_is_local(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return parsed.is_loopback or parsed.is_private


def valid_identity_id(value: object) -> str:
    return valid_resource_id(value)


def valid_presented_token(value: object) -> bool:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        return False
    if value.startswith("eyJ") or "@" in value:
        return False
    return True


def location_allowed(location: str) -> bool:
    if type(location) is not str:
        return False
    return redirect_accepted(location)


def unexpected_credential(payload: bytes, scenario: str) -> bool:
    if type(payload) is not bytes or scenario not in RULES:
        return True
    expected = _EXPECTED_MATERIAL.get(scenario, frozenset())
    if _JWT_BYTES.search(payload) is not None and b"access_token" not in expected:
        return True
    lowered = payload.lower()
    for marker in _BODY_MARKERS:
        if marker in lowered and marker not in expected:
            return True
    return False


def presented_material_echoed(payload: bytes, material: Sequence[bytes]) -> bool:
    if type(payload) is not bytes:
        return True
    for item in material:
        if type(item) is bytes and len(item) >= 8 and item in payload:
            return True
    return False


def evidence_has_credential_material(encoded: str) -> bool:
    if type(encoded) is not str:
        return True
    lowered = encoded.lower()
    if any(marker in lowered for marker in _EVIDENCE_MARKERS):
        return True
    return _JWT.search(encoded) is not None


def classify_outcome(
    *,
    scenario: str,
    status: object,
    leak: bool,
    credential: bool,
    redirect_ok: bool,
    cookie_ok: bool,
    controls_ok: bool,
) -> str:
    rule = RULES.get(scenario)
    if rule is None or type(leak) is not bool or type(credential) is not bool:
        return "fail"
    if (
        type(redirect_ok) is not bool
        or type(cookie_ok) is not bool
        or type(controls_ok) is not bool
    ):
        return "fail"
    if leak or credential or not redirect_ok or not cookie_ok or not controls_ok:
        return "fail"
    if type(status) is not int:
        return "fail"
    if status in rule.success or status in rule.rejection:
        return "pass"
    return "fail"


def public_outcome(
    *,
    scenario: str,
    gym: int | None,
    status: object,
    leak: bool,
    credential: bool,
    redirect_ok: bool,
    cookie_ok: bool,
    controls_ok: bool,
) -> dict[str, object]:
    rule = RULES.get(scenario)
    if rule is None:
        raise IdentitySpecError("identity scenario is outside the catalog")
    if scenario in EDGE and gym is not None:
        raise IdentitySpecError("edge scenario was bound to a gym")
    if scenario in GYM and gym not in (0, 1):
        raise IdentitySpecError("gym scenario is missing its gym")
    if scenario in (*LOGOUT, *MFA) and gym != 0:
        raise IdentitySpecError("single-gym scenario was not bound to gym 0")
    return {
        "scenario": scenario,
        "gym": gym,
        "phase": rule.phase,
        "transport": rule.transport,
        "status": status if type(status) is int else "unavailable",
        "classification": classify_outcome(
            scenario=scenario,
            status=status,
            leak=leak,
            credential=credential,
            redirect_ok=redirect_ok,
            cookie_ok=cookie_ok,
            controls_ok=controls_ok,
        ),
        "leak": leak is True,
        "credential": credential is True,
        "redirect_ok": redirect_ok is True,
        "cookie_ok": cookie_ok is True,
    }


def _passes(outcomes: Sequence[Mapping[str, object]], scenario: str, gym: int) -> int:
    total = 0
    for item in outcomes:
        if (
            item.get("scenario") == scenario
            and item.get("gym") == gym
            and item.get("classification") == "pass"
        ):
            total += 1
    return total


def _int_field(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if type(value) is not int or isinstance(value, bool):
        return None
    return value


def _baseline(row: Mapping[str, object]) -> bool:
    return (
        row.get("member_status") == "ACTIVE"
        and row.get("member_linked") is True
        and _int_field(row, "member_live_sessions") == 0
        and row.get("owner_password_changed") is False
        and _int_field(row, "owner_live_sessions") == 0
        and _int_field(row, "reset_live") == 0
        and row.get("reset_consumed") is False
        and _int_field(row, "invitation_pending") == 0
        and _int_field(row, "invitation_accepted") == 0
        and row.get("accepted_owner") is False
        and row.get("accepted_trainer") is False
        and row.get("owner_is_owner") is True
        and row.get("manager_is_owner") is False
        and row.get("mfa_enabled") is False
    )


def _protected_changed(
    row: Mapping[str, object],
    outcomes: Sequence[Mapping[str, object]],
    gym: int,
) -> bool:
    if row.get("owner_is_owner") is not True or row.get("manager_is_owner") is True:
        return True
    if row.get("accepted_owner") is True:
        return True
    if row.get("member_status") != "ACTIVE" or row.get("member_linked") is not True:
        return True
    pending = _int_field(row, "invitation_pending")
    accepted = _int_field(row, "invitation_accepted")
    member_live = _int_field(row, "member_live_sessions")
    owner_live = _int_field(row, "owner_live_sessions")
    reset_live = _int_field(row, "reset_live")
    if None in (pending, accepted, member_live, owner_live, reset_live):
        return True
    assert pending is not None
    assert accepted is not None
    assert member_live is not None
    assert owner_live is not None
    assert reset_live is not None
    if pending > 2 or accepted > 1 or member_live > 2 or owner_live > 1 or reset_live > 1:
        return True
    if row.get("owner_password_changed") is True and _passes(outcomes, "reset_consume", gym) == 0:
        return True
    mfa_pass = _passes(outcomes, "mfa_confirm", 0) == 1
    return row.get("mfa_enabled") is True and not mfa_pass


def _member_live_expected(outcomes: Sequence[Mapping[str, object]], gym: int) -> int | None:
    replay = _passes(outcomes, "member_refresh_replay", gym) == 1
    logout_login = _passes(outcomes, "member_logout_login", gym) == 1
    logout_replay = _passes(outcomes, "member_logout_replay", gym) == 1
    if gym == 0 and logout_login and not logout_replay:
        return 1
    if replay or logout_replay:
        return 0
    if _passes(outcomes, "member_login", gym) == 1:
        return None
    return 0


def _counts_match(
    row: Mapping[str, object],
    outcomes: Sequence[Mapping[str, object]],
    gym: int,
) -> bool:
    pending = 1 if _passes(outcomes, "invite_create", gym) else 0
    accepted = 1 if _passes(outcomes, "invite_accept", gym) else 0
    reset = _passes(outcomes, "reset_consume", gym) == 1
    staff = _passes(outcomes, "staff_login", gym) == 1
    forgot = _passes(outcomes, "forgot_known", gym) == 1
    owner_live = 0 if reset or not staff else 1
    reset_live = 1 if forgot and not reset else 0
    member_live = _member_live_expected(outcomes, gym)
    observed_member = _int_field(row, "member_live_sessions")
    if observed_member is None:
        return False
    if member_live is None:
        member_ok = 0 <= observed_member <= 2
    else:
        member_ok = observed_member == member_live
    mfa_pass = _passes(outcomes, "mfa_confirm", 0) == 1
    return (
        _int_field(row, "invitation_pending") == pending
        and _int_field(row, "invitation_accepted") == accepted
        and row.get("accepted_trainer") is (accepted == 1)
        and row.get("accepted_owner") is False
        and row.get("owner_password_changed") is reset
        and row.get("reset_consumed") is reset
        and _int_field(row, "owner_live_sessions") == owner_live
        and _int_field(row, "reset_live") == reset_live
        and member_ok
        and row.get("mfa_enabled") is mfa_pass
        and row.get("member_status") == "ACTIVE"
        and row.get("member_linked") is True
        and row.get("owner_is_owner") is True
        and row.get("manager_is_owner") is False
    )


def assess_identity(
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    *,
    cancelled: int,
) -> tuple[str, bool, bool]:
    """Return state status, exact match, and data_corruption."""

    if type(cancelled) is not int or isinstance(cancelled, bool) or cancelled < 0:
        raise IdentitySpecError("cancelled count is invalid")
    if not snapshots:
        return "not-run", False, False
    if len(snapshots) != 2:
        return "fail", False, True
    corruption = False
    matched = True
    baseline = True
    seen: set[int] = set()
    for row in snapshots:
        gym = row.get("gym")
        if type(gym) is not int or isinstance(gym, bool) or gym not in (0, 1) or gym in seen:
            return "fail", False, True
        seen.add(gym)
        if _protected_changed(row, outcomes, gym):
            corruption = True
            matched = False
        elif not _counts_match(row, outcomes, gym):
            matched = False
        if not _baseline(row):
            baseline = False
    if corruption:
        return "fail", False, True
    if not outcomes and baseline:
        return "not-run", False, False
    complete = cancelled == 0 and len(outcomes) == PLAN_OPERATIONS and _outcomes_pass(outcomes)
    if matched and complete:
        return "pass", True, False
    return "fail", matched, False


def _outcomes_pass(outcomes: Sequence[Mapping[str, object]]) -> bool:
    if len(outcomes) != PLAN_OPERATIONS:
        return False
    plan = build_plan()
    for expected, item in zip(plan, outcomes, strict=True):
        if item.get("scenario") != expected["scenario"] or item.get("gym") != expected["gym"]:
            return False
        rule = RULES[str(expected["scenario"])]
        if item.get("phase") != expected["phase"] or item.get("transport") != rule.transport:
            return False
        if item.get("classification") != "pass":
            return False
        if item.get("leak") is not False or item.get("credential") is not False:
            return False
        if item.get("redirect_ok") is not True or item.get("cookie_ok") is not True:
            return False
        scenario = str(expected["scenario"])
        if scenario in _EXPECTED_MATERIAL or rule.success:
            if item.get("status") not in rule.success:
                return False
        elif item.get("status") not in rule.rejection:
            return False
    return True


def _uncovered_ok(body: Mapping[str, Any]) -> bool:
    rows = body.get("uncovered")
    if not isinstance(rows, list) or len(rows) != len(UNCOVERED):
        return False
    for expected, row in zip(UNCOVERED, rows, strict=True):
        if not isinstance(row, Mapping):
            return False
        if row.get("id") != expected["id"] or row.get("covered") is not False:
            return False
        if row.get("note") != expected["note"]:
            return False
    return True


def judge(body: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if body.get("kind") != KIND or body.get("profile") != PROFILE_NAME:
        reasons.append("profile")
    if body.get("project") != PROJECT or body.get("evidence_name") != EVIDENCE_NAME:
        reasons.append("project")
    if any(body.get(flag) is True for flag in (*PROOF_FLAGS, *LOCAL_IDENTITY_FLAGS)):
        reasons.append("proof")
    if body.get("local_security_status") != "not-run":
        reasons.append("security_status")
    if (
        body.get("local_isolation_status") != "not-run"
        or body.get("local_soak_status") != "not-run"
    ):
        reasons.append("other_status")
    outcomes = body.get("scenarios")
    if not isinstance(outcomes, list) or not _outcomes_pass(outcomes):
        reasons.append("scenarios")
    if body.get("cancelled_operations") != 0 or body.get("completed_operations") != PLAN_OPERATIONS:
        reasons.append("completion")
    reconciliation = body.get("reconciliation")
    if not isinstance(reconciliation, Mapping) or reconciliation.get("matched") is not True:
        reasons.append("reconciliation")
    elif reconciliation.get("transport") != "service-only":
        reasons.append("reconciliation")
    sources = body.get("token_sources")
    if not isinstance(sources, Mapping):
        reasons.append("token_source")
    elif (
        sources.get("password_reset") != TOKEN_SOURCE
        or sources.get("staff_invitation_accept") != TOKEN_SOURCE
    ):
        reasons.append("token_source")
    if not _uncovered_ok(body):
        reasons.append("uncovered")
    if body.get("data_corruption") is not False:
        reasons.append("corruption")
    if body.get("cross_gym_leakage") != 0 or body.get("unexpected_errors") != 0:
        reasons.append("leakage")
    if body.get("credential_exposure") != 0:
        reasons.append("credential")
    wall = body.get("wall_time_seconds")
    if isinstance(wall, bool) or not isinstance(wall, (int, float)):
        reasons.append("duration")
    elif wall < 0 or wall > DURATION_SECONDS:
        reasons.append("duration")
    observed = body.get("caddy_metrics_observed")
    delta = body.get("caddy_5xx_delta")
    if observed is True and delta != 0:
        reasons.append("caddy")
    if observed is not True and delta is not None:
        reasons.append("caddy")
    if reasons:
        return "fail", reasons
    return "pass", []


def _base_fields() -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "kind": KIND,
        "profile": PROFILE_NAME,
        "project": PROJECT,
        "evidence_name": EVIDENCE_NAME,
        "local_identity_status": "not-run",
        "local_security_status": "not-run",
        "local_isolation_status": "not-run",
        "local_soak_status": "not-run",
        "duration_seconds": DURATION_SECONDS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "mfa_wait_seconds": MFA_WAIT_SECONDS,
        "planned_operations": PLAN_OPERATIONS,
        "completed_operations": 0,
        "cancelled_operations": 0,
        "wall_time_seconds": 0,
        "cross_gym_leakage": 0,
        "unexpected_errors": 0,
        "credential_exposure": 0,
        "data_corruption": False,
        "caddy_metrics_observed": False,
        "caddy_5xx_delta": None,
        "token_sources": {
            "password_reset": "not-run",
            "staff_invitation_accept": "not-run",
        },
        "uncovered": [dict(item) for item in UNCOVERED],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "failure_stage": "",
        "failure_detail": "",
        "gate_reasons": [],
    }
    for flag in (*PROOF_FLAGS, *LOCAL_IDENTITY_FLAGS):
        body[flag] = False
    return body


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    if profile != PROFILE_NAME:
        raise IdentitySpecError("identity profile is not the fixed profile")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise IdentitySpecError("failure stage is invalid")
    body = _base_fields()
    body["local_identity_status"] = "fail"
    body["failure_stage"] = stage
    return body


def assemble(
    *,
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    cancelled: int,
    wall_seconds: float,
    metrics: tuple[bool, int | None],
    token_sources_used: bool,
    failure_stage: str = "",
    failure_detail: str = "",
) -> dict[str, Any]:
    state, matched, corruption = assess_identity(outcomes, snapshots, cancelled=cancelled)
    body = _base_fields()
    leaked = sum(1 for item in outcomes if item.get("leak") is True)
    credentials = sum(1 for item in outcomes if item.get("credential") is True)
    unexpected = sum(1 for item in outcomes if item.get("classification") != "pass")
    body["scenarios"] = [dict(item) for item in outcomes]
    body["completed_operations"] = len(outcomes)
    body["cancelled_operations"] = cancelled
    body["wall_time_seconds"] = round(wall_seconds, 3)
    body["cross_gym_leakage"] = leaked
    body["credential_exposure"] = credentials
    body["unexpected_errors"] = unexpected
    body["data_corruption"] = corruption
    body["caddy_metrics_observed"] = metrics[0] is True
    body["caddy_5xx_delta"] = metrics[1] if metrics[0] is True else None
    body["failure_stage"] = failure_stage
    body["failure_detail"] = failure_detail
    if token_sources_used:
        body["token_sources"] = {
            "password_reset": TOKEN_SOURCE,
            "staff_invitation_accept": TOKEN_SOURCE,
        }
    body["reconciliation"] = {
        "transport": "service-only",
        "matched": matched,
        "gyms": [dict(row) for row in snapshots] if snapshots else [],
    }
    if state == "not-run":
        body["local_identity_status"] = "not-run"
        return body
    status, reasons = judge(body)
    if state == "pass" and status == "pass":
        body["local_identity_status"] = "pass"
    else:
        body["local_identity_status"] = "fail"
        body["gate_reasons"] = reasons or [state]
    return body


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in (*PROOF_FLAGS, *LOCAL_IDENTITY_FLAGS):
        if body.get(flag) is True:
            raise IdentitySpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("schema_version") not in (None, SCHEMA):
        raise IdentitySpecError("identity evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["evidence_name"] = EVIDENCE_NAME
    if body.get("local_security_status") != "not-run":
        raise IdentitySpecError("increment 12 security status cannot be claimed here")
    if (
        body.get("local_soak_status") != "not-run"
        or body.get("local_isolation_status") != "not-run"
    ):
        raise IdentitySpecError("this profile cannot claim soak or tenant isolation")
    if body.get("local_identity_status") not in LOCAL_STATUSES:
        raise IdentitySpecError("local status must be pass, fail, or not-run")
    if body.get("local_identity_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise IdentitySpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets) or evidence_has_credential_material(encoded):
        raise IdentitySpecError("identity evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise IdentitySpecError("identity evidence contains secret material")
    body["checksum"] = checksum_for(body)
    return body


def verify_checksum(body: Mapping[str, Any]) -> bool:
    expected = body.get("checksum")
    if not isinstance(expected, str):
        return False
    return expected == checksum_for(body)


def lint_texts(*, command: str, runbook: str, harness: str) -> list[str]:
    errors: list[str] = []
    for phrase in (
        "config.settings.staging_rehearsal",
        "STAGING_REHEARSAL",
        "gym-staging-rehearsal",
        "penetration_test_proven",
        "tenant_isolation_proven",
        "formal_review_proven",
        "production_security_proven",
        "service-local-mailbox",
        "assert_identity_route",
    ):
        if phrase not in command:
            errors.append(f"identity command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "identity-security.json",
        "local_identity_status",
        "penetration_test_proven",
        "not a penetration test",
        "formal security review",
        "production security",
        "service-local-mailbox",
        "http_forgot_mailbox_roundtrip",
        "member_mfa_challenge",
        "owner_invitation_acceptance",
        "edge_rate_limit",
        "live_provider",
        "does not wait for ClamAV TCP",
        "72",
        "180",
    ):
        if phrase not in runbook:
            errors.append(f"identity runbook is missing {phrase}")
    for phrase in (
        "penetration test is proven",
        "formal security review is proven",
        "production security is proven",
        "tenant isolation is proven",
        "live provider is proven",
    ):
        if phrase in runbook:
            errors.append(f"identity runbook claims {phrase}")
    for phrase in (
        "identity-security.json",
        "staging_identity_security_characterization",
        "does not replace security.json",
        "does not replace endurance.json",
        "does not replace pt.json",
        "does not replace pt-cash.json",
        "does not replace load.json",
        "does not replace finance.json",
        "does not wait for ClamAV TCP",
    ):
        if phrase not in harness:
            errors.append(f"staging harness is missing {phrase}")
    return errors


__all__ = [
    "BFF_ROUTES",
    "CADDY_ROUTES",
    "DURATION_SECONDS",
    "EDGE",
    "EVIDENCE_NAME",
    "GYM",
    "KIND",
    "LOCAL_IDENTITY_FLAGS",
    "LOGOUT",
    "MAX_RESPONSE_BYTES",
    "MFA",
    "MFA_WAIT_SECONDS",
    "PLAN_OPERATIONS",
    "PRIVATE_ROUTES",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PUBLIC_ORIGIN",
    "RULES",
    "SCHEMA",
    "TIMEOUT_SECONDS",
    "TOKEN_SOURCE",
    "UNCOVERED",
    "IdentitySpecError",
    "assemble",
    "assert_identity_route",
    "assert_private_target",
    "assess_identity",
    "build_plan",
    "checksum_for",
    "classify_outcome",
    "evidence_has_credential_material",
    "failure_document",
    "judge",
    "lint_texts",
    "location_allowed",
    "metrics_delta",
    "presented_material_echoed",
    "public_outcome",
    "require_int",
    "resolve_profile",
    "seal_evidence",
    "unexpected_credential",
    "valid_identity_id",
    "valid_presented_token",
    "verify_checksum",
]
