"""Pure rules for the disposable local security characterization.

This module does not open sockets, import Django, or read credentials. The
staging management command and the rehearsal harness apply these rules. A
local pass is project-authored verification on the rehearsal stack. It is
not a penetration test, a formal security review, or production security proof.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import re
import urllib.parse
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.common.load_characterization import (
    MAX_RESPONSE_BYTES,
    PROOF_FLAGS,
    material_leaked,
)

SCHEMA = "gym-staging-security/v1"
PROJECT = "gym-staging-rehearsal"
KIND = "local-disposable-security-characterization"
PROFILE_NAME = "security"
EVIDENCE_NAME = "security.json"
SETTINGS_MODULE = "config.settings.staging_rehearsal"
DURATION_SECONDS = 180
TIMEOUT_SECONDS = 8.0
PUBLIC_ORIGIN = "https://localhost:8443"
PUBLIC_HOST = "localhost"
UNTRUSTED_ORIGIN = "https://untrusted.example"
PRIVATE_API_HOST = "api"
PRIVATE_API_PORT = 8000
PRIVATE_API_PATH = "/api/v1/auth/login/"
CONNECT_HOST = "caddy"
REQUEST_PORT = 443
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
LOCAL_SECURITY_FLAGS = (
    "penetration_test_proven",
    "formal_review_proven",
    "production_security_proven",
)
_UUID_CANONICAL = "[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_UUID = re.compile(rf"{_UUID_CANONICAL}\Z")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_JWT_MATERIAL = re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_CREDENTIAL_MARKERS = (
    b"access_token",
    b"refresh_token",
    b"gym_selection_ticket",
    b"mfa_challenge_token",
)
_CREDENTIAL_QUERY = frozenset(
    {"access_token", "refresh_token", "token", "cookie", "gms_access", "gms_refresh"}
)
_AUTH_COOKIES = ("gms_access", "gms_refresh")
KEY_KINDS = frozenset(
    {
        "cash-open",
        "checkin",
        "checkin-cross",
        "expense",
        "expense-mass",
        "expense-submit",
        "idor-expense",
        "idor-pt",
        "manager-expense",
        "manager-expense-submit",
        "manager-self",
        "member-patch",
        "pt-sale",
        "pt-submit",
        "self-expense",
        "self-pt",
        "trainer-expense",
    }
)
BFF_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/v1/members/", "none"),
    ("POST", "/api/auth/login", "none"),
    ("GET", "/api/operations/members", "none"),
    ("GET", "/api/operations/members/{id}", "none"),
    ("PATCH", "/api/operations/members/{id}", "none"),
    ("POST", "/api/operations/expenses", "none"),
    ("GET", "/api/operations/expenses/{id}", "none"),
    ("POST", "/api/operations/expenses/{id}/submit", "none"),
    ("POST", "/api/operations/expenses/{id}/approve", "none"),
    ("POST", "/api/operations/cash-sessions/open", "none"),
    ("GET", "/api/operations/cash-sessions/{id}", "none"),
    ("POST", "/api/operations/check-ins", "none"),
    ("GET", "/api/operations/check-ins/{id}", "none"),
    ("POST", "/api/operations/pt-sales", "none"),
    ("GET", "/api/operations/pt-sales/{id}", "none"),
    ("POST", "/api/operations/pt-sales/{id}/submit", "none"),
    ("POST", "/api/operations/pt-sales/{id}/approve", "none"),
    ("GET", "/api/operations/reports/daily", "date"),
    ("PUT", "/api/operations/gym-memberships/{id}/roles", "none"),
)
_WRITE_CREATES = (
    "expense_create",
    "manager_expense_create",
    "cash_open",
    "checkin_create",
    "pt_sale_create",
)

EDGE: tuple[str, ...] = (
    "edge_staff_blocked",
    "csrf_absent",
    "csrf_mismatch",
    "origin_untrusted",
    "session_absent",
    "session_invalid",
    "bff_secret_missing",
    "bff_secret_forged",
    "redirect_offline",
)
SETUP: tuple[str, ...] = (
    "auth_login",
    "manager_login",
    "trainer_login",
    "expense_create",
    "expense_submit",
    "manager_expense_create",
    "manager_expense_submit",
    "cash_open",
    "checkin_create",
    "pt_sale_create",
    "pt_sale_submit",
)
PROBES: tuple[str, ...] = (
    "idor_member_read",
    "idor_expense_read",
    "idor_cash_read",
    "idor_checkin_read",
    "idor_pt_sale_read",
    "report_read",
    "idor_member_patch",
    "idor_checkin_write",
    "idor_expense_approve",
    "idor_pt_approve",
    "mass_expense",
    "mass_member",
    "self_expense_approve",
    "manager_self_approve",
    "trainer_expense_approve",
    "self_pt_approve",
    "role_escalation",
)
PLAN_OPERATIONS = len(EDGE) + 2 * (len(SETUP) + len(PROBES))
_LOGINS = frozenset({"auth_login", "manager_login", "trainer_login"})
_POSITIVE = frozenset(
    {
        "redirect_offline",
        *_LOGINS,
        "expense_create",
        "expense_submit",
        "manager_expense_create",
        "manager_expense_submit",
        "cash_open",
        "checkin_create",
        "pt_sale_create",
        "pt_sale_submit",
        "report_read",
    }
)


class SecuritySpecError(ValueError):
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
        raise SecuritySpecError("scenario success and rejection overlap")
    if 500 in success or 500 in rejection:
        raise SecuritySpecError("scenario treats an internal error as expected")
    return ScenarioRule(success, rejection, transport, phase)


RULES: dict[str, ScenarioRule] = {
    "edge_staff_blocked": _rule(frozenset(), frozenset({404}), "bff", "edge"),
    "csrf_absent": _rule(frozenset(), frozenset({403}), "bff", "edge"),
    "csrf_mismatch": _rule(frozenset(), frozenset({403}), "bff", "edge"),
    "origin_untrusted": _rule(frozenset(), frozenset({403}), "bff", "edge"),
    "session_absent": _rule(frozenset(), frozenset({401}), "bff", "edge"),
    "session_invalid": _rule(frozenset(), frozenset({401}), "bff", "edge"),
    "bff_secret_missing": _rule(frozenset(), frozenset({403}), "private", "edge"),
    "bff_secret_forged": _rule(frozenset(), frozenset({403}), "private", "edge"),
    "redirect_offline": _rule(frozenset({200}), frozenset(), "offline", "edge"),
    "auth_login": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "manager_login": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "trainer_login": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "expense_create": _rule(frozenset({201}), frozenset(), "bff", "setup"),
    "expense_submit": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "manager_expense_create": _rule(frozenset({201}), frozenset(), "bff", "setup"),
    "manager_expense_submit": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "cash_open": _rule(frozenset({201}), frozenset(), "bff", "setup"),
    "checkin_create": _rule(frozenset({201}), frozenset(), "bff", "setup"),
    "pt_sale_create": _rule(frozenset({201}), frozenset(), "bff", "setup"),
    "pt_sale_submit": _rule(frozenset({200}), frozenset(), "bff", "setup"),
    "idor_member_read": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_expense_read": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_cash_read": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_checkin_read": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_pt_sale_read": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "report_read": _rule(frozenset({200}), frozenset(), "bff", "probe"),
    "idor_member_patch": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_checkin_write": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_expense_approve": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "idor_pt_approve": _rule(frozenset(), frozenset({404}), "bff", "probe"),
    "mass_expense": _rule(frozenset(), frozenset({400}), "bff", "probe"),
    "mass_member": _rule(frozenset(), frozenset({400}), "bff", "probe"),
    "self_expense_approve": _rule(frozenset(), frozenset({403}), "bff", "probe"),
    "manager_self_approve": _rule(frozenset(), frozenset({403}), "bff", "probe"),
    "trainer_expense_approve": _rule(frozenset(), frozenset({403}), "bff", "probe"),
    "self_pt_approve": _rule(frozenset(), frozenset({403}), "bff", "probe"),
    "role_escalation": _rule(frozenset(), frozenset({403}), "bff", "probe"),
}

COVERAGE: tuple[dict[str, object], ...] = (
    {
        "id": "edge_staff_api",
        "method": "GET",
        "route": "/api/v1/members/",
        "transport": "bff",
        "covered": True,
        "note": "Caddy returns 404 for a direct staff API path. The member API stays separate.",
    },
    {
        "id": "csrf_origin",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "bff",
        "covered": True,
        "note": "Absent CSRF, mismatched CSRF, and an untrusted Origin are rejected.",
    },
    {
        "id": "bff_secret",
        "method": "POST",
        "route": PRIVATE_API_PATH,
        "transport": "private",
        "covered": True,
        "note": "Missing and forged BFF secrets are rejected on the private API auth path.",
    },
    {
        "id": "auth_cookies",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "bff",
        "covered": True,
        "note": "Access and refresh cookies are Secure, HttpOnly, SameSite=Lax, and Path=/.",
    },
    {
        "id": "redirect_offline",
        "method": "",
        "route": "fixed-host redirect validator",
        "transport": "offline",
        "covered": True,
        "note": "External and credential-bearing redirects are rejected without a live request.",
    },
    {
        "id": "two_gym_idor",
        "method": "GET",
        "route": "/api/operations/members/{id}",
        "transport": "bff",
        "covered": True,
        "note": "Cross-gym member, expense, cash, check-in, and PT sale reads and writes.",
    },
    {
        "id": "mass_assignment",
        "method": "POST",
        "route": "/api/operations/expenses",
        "transport": "bff",
        "covered": True,
        "note": "Unknown gym, status, owner, and approval fields are rejected.",
    },
    {
        "id": "approval_separation",
        "method": "POST",
        "route": "/api/operations/expenses/{id}/approve",
        "transport": "bff",
        "covered": True,
        "note": "Creators cannot approve their own expense or PT sale. A trainer cannot escalate.",
    },
    {
        "id": "member_api",
        "method": "",
        "route": "/api/v1/member-auth",
        "transport": "uncovered",
        "covered": False,
        "note": "The published member API is not part of this staff-boundary matrix.",
    },
    {
        "id": "receipt_export",
        "method": "",
        "route": "receipt download or report export",
        "transport": "uncovered",
        "covered": False,
        "note": (
            "Receipt download and report export stay uncovered. The daily report read is covered."
        ),
    },
    {
        "id": "payout_close",
        "method": "",
        "route": "commission payout or cash close",
        "transport": "uncovered",
        "covered": False,
        "note": "Payout and till close stay in the finance and PT evidence files.",
    },
    {
        "id": "rate_limit",
        "method": "",
        "route": "unknown-account login",
        "transport": "uncovered",
        "covered": False,
        "note": "Unknown-account sampling and edge or WAF rate limits stay unproven.",
    },
)


def require_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise SecuritySpecError("bounded integer was rejected")
    return value


def resolve_profile(name: object) -> str:
    if name != PROFILE_NAME or type(name) is not str:
        raise SecuritySpecError("security profile is not the fixed profile")
    require_int(DURATION_SECONDS, minimum=60, maximum=180)
    require_int(PLAN_OPERATIONS, minimum=1, maximum=80)
    if type(TIMEOUT_SECONDS) is not float or TIMEOUT_SECONDS != 8.0:
        raise SecuritySpecError("security timeout is not the fixed timeout")
    if set(RULES) != set(EDGE) | set(SETUP) | set(PROBES):
        raise SecuritySpecError("security catalog does not match the rules")
    return PROFILE_NAME


def build_plan() -> list[dict[str, object]]:
    resolve_profile(PROFILE_NAME)
    plan: list[dict[str, object]] = []
    for scenario in EDGE:
        plan.append({"scenario": scenario, "gym": None, "phase": "edge"})
    for gym in (0, 1):
        for scenario in SETUP:
            plan.append({"scenario": scenario, "gym": gym, "phase": "setup"})
    for gym in (0, 1):
        for scenario in PROBES:
            plan.append({"scenario": scenario, "gym": gym, "phase": "probe"})
    if len(plan) != PLAN_OPERATIONS:
        raise SecuritySpecError("security plan length drifted")
    return plan


def _route_pattern(template: str) -> re.Pattern[str]:
    if template.count("{id}") > 1 or "{" in template.replace("{id}", ""):
        raise SecuritySpecError("security route template is invalid")
    pieces = template.split("{id}")
    body = _UUID_CANONICAL.join(re.escape(piece) for piece in pieces)
    return re.compile(rf"{body}\Z")


_COMPILED_BFF_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (method, _route_pattern(template), query)
    for method, template, query in BFF_ROUTES
    if query in {"none", "date"}
)
if len(_COMPILED_BFF_ROUTES) != len(BFF_ROUTES):
    raise SecuritySpecError("security route catalog is invalid")


def make_key(kind: object, gym: object, ordinal: object) -> str:
    if type(kind) is not str or kind not in KEY_KINDS:
        raise SecuritySpecError("idempotency key kind is not fixed")
    if type(gym) is not int or gym not in (0, 1):
        raise SecuritySpecError("idempotency key ordinal is outside the profile")
    if type(ordinal) is not int or ordinal < 0 or ordinal > 20:
        raise SecuritySpecError("idempotency key ordinal is outside the profile")
    return f"sec-{kind}-g{gym}-{ordinal:04d}"


def _path_shape_rejected(path: str) -> bool:
    if not path.isascii() or not path.startswith("/") or path.startswith("//"):
        return True
    if ".." in path or "://" in path or "@" in path or "\\" in path or "#" in path:
        return True
    return any(char in path for char in "% \t\r\n\x00")


def _exact_date_query(query: str) -> bool:
    if not query or "&" in query or ";" in query or "%" in query or query.count("=") != 1:
        return False
    key, value = query.split("=", 1)
    if key != "date" or _ISO_DATE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def assert_bff_path(method: object, path: object) -> None:
    if type(method) is not str or type(path) is not str or _path_shape_rejected(path):
        raise SecuritySpecError("security path is outside the catalog")
    if path.count("?") > 1:
        raise SecuritySpecError("security path is outside the catalog")
    base, separator, query = path.partition("?")
    for rule_method, pattern, query_mode in _COMPILED_BFF_ROUTES:
        if rule_method != method or pattern.fullmatch(base) is None:
            continue
        if query_mode == "none":
            if separator:
                raise SecuritySpecError("security query is outside the catalog")
            return
        if not separator or not _exact_date_query(query):
            raise SecuritySpecError("security query is outside the catalog")
        return
    raise SecuritySpecError("security path is outside the catalog")


def assert_private_api_target(host: str, port: int, path: str, resolved: Sequence[str]) -> None:
    if host != PRIVATE_API_HOST or port != PRIVATE_API_PORT or path != PRIVATE_API_PATH:
        raise SecuritySpecError("private probe is outside the auth path")
    if not resolved or any(not _ip_is_local(item) for item in resolved):
        raise SecuritySpecError("private probe address is not loopback or private")


def _ip_is_local(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return parsed.is_loopback or parsed.is_private


def fixed_host_redirect(location: str) -> bool:
    if location.startswith("/") and not location.startswith("//"):
        return True
    parsed = urllib.parse.urlparse(location)
    return (
        parsed.scheme == "https"
        and parsed.hostname == PUBLIC_HOST
        and parsed.port in (None, 8443)
        and parsed.username is None
        and parsed.password is None
    )


def credential_in_location(location: str) -> bool:
    parsed = urllib.parse.urlparse(location)
    if parsed.username or parsed.password:
        return True
    names = {
        name.lower()
        for name, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    }
    return bool(names & _CREDENTIAL_QUERY)


def redirect_accepted(location: str) -> bool:
    return fixed_host_redirect(location) and not credential_in_location(location)


def offline_redirect_cases() -> tuple[tuple[str, bool], ...]:
    return (
        ("/login", True),
        (f"{PUBLIC_ORIGIN}/login", True),
        ("https://untrusted.example/login", False),
        (f"{PUBLIC_ORIGIN}/login?access_token=abc", False),
        ("https://user:secret@localhost:8443/login", False),
        ("//untrusted.example/login", False),
        ("http://localhost:8443/login", False),
    )


def offline_redirects_pass() -> bool:
    cases = offline_redirect_cases()
    if len(cases) != 7:
        return False
    return all(redirect_accepted(location) is expected for location, expected in cases)


def origin_reflected(header: object, origin: str) -> bool:
    if header is None:
        return False
    if type(header) is not str:
        return True
    value = header.strip().lower()
    return value in {"*", origin.lower()}


def credential_in_body(payload: bytes) -> bool:
    if type(payload) is not bytes:
        return True
    if _JWT_MATERIAL.search(payload) is not None:
        return True
    lowered = payload.lower()
    return any(marker in lowered for marker in _CREDENTIAL_MARKERS)


def bff_required(payload: bytes) -> bool:
    return b"BFF_REQUIRED" in payload and not credential_in_body(payload)


def csrf_failed(payload: bytes) -> bool:
    return b"CSRF_FAILED" in payload and not credential_in_body(payload)


def auth_cookie_flags(headers: Sequence[str]) -> dict[str, dict[str, object]]:
    found: dict[str, dict[str, object]] = {}
    for header in headers:
        parts = [part.strip() for part in header.split(";") if part.strip()]
        if not parts or "=" not in parts[0]:
            continue
        name, _value = parts[0].split("=", 1)
        if name not in _AUTH_COOKIES:
            continue
        flags = {part.split("=", 1)[0].strip().lower(): part for part in parts[1:]}
        same = ""
        for part in parts[1:]:
            key, sep, raw = part.partition("=")
            if key.strip().lower() == "samesite" and sep:
                same = raw.strip().lower()
        path = ""
        for part in parts[1:]:
            key, sep, raw = part.partition("=")
            if key.strip().lower() == "path" and sep:
                path = raw.strip()
        found[name] = {
            "httponly": "httponly" in flags,
            "secure": "secure" in flags,
            "samesite": same,
            "path": path,
        }
    return found


def required_auth_cookies(flags: Mapping[str, Mapping[str, object]], *, secure: bool) -> bool:
    if secure is not True:
        return False
    for name in _AUTH_COOKIES:
        item = flags.get(name)
        if not isinstance(item, Mapping):
            return False
        if item.get("httponly") is not True or item.get("secure") is not True:
            return False
        if item.get("samesite") != "lax" or item.get("path") != "/":
            return False
    return True


def marker_in_success(status: object, payload: bytes, markers: Sequence[bytes]) -> bool:
    if type(status) is not int or status < 200 or status >= 300:
        return False
    return any(marker and marker in payload for marker in markers)


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
        raise SecuritySpecError("security scenario is outside the catalog")
    if scenario in EDGE and gym is not None:
        raise SecuritySpecError("edge scenario was bound to a gym")
    if scenario not in EDGE and gym not in (0, 1):
        raise SecuritySpecError("gym scenario is missing its gym")
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
            if scenario in _POSITIVE:
                total += 1
    return total


def _snapshot_int(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if type(value) is not int:
        return None
    return value


def _baseline(row: Mapping[str, object]) -> bool:
    roles_trainer = row.get("trainer_roles")
    roles_manager = row.get("manager_roles")
    return (
        _snapshot_int(row, "expenses") == 0
        and _snapshot_int(row, "checkins") == 0
        and _snapshot_int(row, "cash_sessions") == 0
        and _snapshot_int(row, "pt_sales") == 0
        and _snapshot_int(row, "expenses_approved") == 0
        and _snapshot_int(row, "pt_sales_posted") == 0
        and _snapshot_int(row, "audit_expense_draft") == 0
        and _snapshot_int(row, "audit_expense_submitted") == 0
        and _snapshot_int(row, "audit_expense_approved") == 0
        and _snapshot_int(row, "audit_pt_draft") == 0
        and _snapshot_int(row, "audit_pt_submitted") == 0
        and _snapshot_int(row, "audit_pt_approved") == 0
        and _snapshot_int(row, "audit_checkin") == 0
        and _snapshot_int(row, "gym_mismatch") == 0
        and row.get("member_notes_blank") is True
        and row.get("member_status") == "ACTIVE"
        and row.get("member_gym_match") is True
        and row.get("trainer_is_owner") is False
        and row.get("manager_is_owner") is False
        and row.get("owner_is_owner") is True
        and roles_trainer == ["TRAINER"]
        and roles_manager == ["MANAGER"]
    )


def _protected_changed(row: Mapping[str, object]) -> bool:
    if row.get("trainer_is_owner") is True or row.get("manager_is_owner") is True:
        return True
    if row.get("owner_is_owner") is not True:
        return True
    if row.get("member_notes_blank") is not True or row.get("member_status") != "ACTIVE":
        return True
    if row.get("member_gym_match") is not True:
        return True
    if row.get("trainer_roles") != ["TRAINER"] or row.get("manager_roles") != ["MANAGER"]:
        return True
    mismatch = _snapshot_int(row, "gym_mismatch")
    if mismatch is None or mismatch > 0:
        return True
    for key in (
        "expenses_approved",
        "pt_sales_posted",
        "audit_expense_approved",
        "audit_pt_approved",
    ):
        value = _snapshot_int(row, key)
        if value is None or value > 0:
            return True
    return False


def _counts_match(
    row: Mapping[str, object], outcomes: Sequence[Mapping[str, object]], gym: int
) -> bool:
    expenses = _passes(outcomes, "expense_create", gym) + _passes(
        outcomes, "manager_expense_create", gym
    )
    submits = _passes(outcomes, "expense_submit", gym) + _passes(
        outcomes, "manager_expense_submit", gym
    )
    expected = {
        "expenses": expenses,
        "expenses_pending": submits,
        "checkins": _passes(outcomes, "checkin_create", gym),
        "cash_sessions": _passes(outcomes, "cash_open", gym),
        "cash_open": _passes(outcomes, "cash_open", gym),
        "pt_sales": _passes(outcomes, "pt_sale_create", gym),
        "pt_sales_pending": _passes(outcomes, "pt_sale_submit", gym),
        "audit_expense_draft": expenses,
        "audit_expense_submitted": submits,
        "audit_pt_draft": _passes(outcomes, "pt_sale_create", gym),
        "audit_pt_submitted": _passes(outcomes, "pt_sale_submit", gym),
        "audit_checkin": _passes(outcomes, "checkin_create", gym),
    }
    return all(_snapshot_int(row, key) == value for key, value in expected.items())


def _stored_exceeds(
    row: Mapping[str, object], outcomes: Sequence[Mapping[str, object]], gym: int
) -> bool:
    pairs = (
        (
            "expenses",
            _passes(outcomes, "expense_create", gym)
            + _passes(outcomes, "manager_expense_create", gym),
        ),
        ("checkins", _passes(outcomes, "checkin_create", gym)),
        ("cash_sessions", _passes(outcomes, "cash_open", gym)),
        ("pt_sales", _passes(outcomes, "pt_sale_create", gym)),
    )
    for key, expected in pairs:
        stored = _snapshot_int(row, key)
        if stored is None or stored > expected:
            return True
    return False


def assess_security(
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    *,
    cancelled: int,
) -> tuple[str, bool, bool]:
    """Return state status, exact match, and data_corruption.

    A partial run that still matches the writes which succeeded is fail with
    data_corruption false. Extra rows or a changed protected field are
    corruption. A run that never wrote, and still matches the seed baseline,
    is not-run.
    """

    if type(cancelled) is not int or cancelled < 0:
        raise SecuritySpecError("cancelled count is invalid")
    if not snapshots:
        return "not-run", False, False
    if len(snapshots) != 2:
        return "fail", False, True
    writes = any(_passes(outcomes, scenario, gym) for scenario in _WRITE_CREATES for gym in (0, 1))
    corruption = False
    matched = True
    baseline = True
    for row in snapshots:
        gym = row.get("gym")
        if type(gym) is not int or gym not in (0, 1):
            return "fail", False, True
        if _protected_changed(row) or _stored_exceeds(row, outcomes, gym):
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
    if matched and writes and cancelled == 0 and len(outcomes) == PLAN_OPERATIONS:
        return "pass", True, False
    return "fail", matched, False


def metrics_delta(
    before: tuple[bool, int | None],
    after: tuple[bool, int | None],
) -> tuple[bool, int | None]:
    if (
        len(before) != 2
        or len(after) != 2
        or type(before[0]) is not bool
        or type(after[0]) is not bool
    ):
        raise SecuritySpecError("metrics observation is invalid")
    if before[0] is not True or after[0] is not True:
        return False, None
    if type(before[1]) is not int or type(after[1]) is not int:
        return False, None
    return True, after[1] - before[1]


def _outcomes_pass(outcomes: Sequence[Mapping[str, object]]) -> bool:
    if len(outcomes) != PLAN_OPERATIONS:
        return False
    plan = build_plan()
    for expected, item in zip(plan, outcomes, strict=True):
        if item.get("scenario") != expected["scenario"] or item.get("gym") != expected["gym"]:
            return False
        if (
            item.get("phase") != expected["phase"]
            or item.get("transport") != RULES[str(expected["scenario"])].transport
        ):
            return False
        if item.get("classification") != "pass":
            return False
        if item.get("leak") is not False or item.get("credential") is not False:
            return False
        if item.get("redirect_ok") is not True or item.get("cookie_ok") is not True:
            return False
        scenario = str(expected["scenario"])
        if scenario in _POSITIVE and item.get("status") not in RULES[scenario].success:
            return False
        if scenario not in _POSITIVE and item.get("status") not in RULES[scenario].rejection:
            return False
    return True


def judge(body: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if body.get("kind") != KIND or body.get("profile") != PROFILE_NAME:
        reasons.append("profile")
    if body.get("project") != PROJECT or body.get("evidence_name") != EVIDENCE_NAME:
        reasons.append("project")
    if any(body.get(flag) is True for flag in PROOF_FLAGS):
        reasons.append("proof")
    if any(body.get(flag) is True for flag in LOCAL_SECURITY_FLAGS):
        reasons.append("security_proof")
    if body.get("local_soak_status") != "not-run":
        reasons.append("soak")
    outcomes = body.get("scenarios")
    if not isinstance(outcomes, list) or not _outcomes_pass(outcomes):
        reasons.append("scenarios")
    if body.get("cancelled_operations") != 0 or body.get("completed_operations") != PLAN_OPERATIONS:
        reasons.append("completion")
    reconciliation = body.get("reconciliation")
    if not isinstance(reconciliation, Mapping) or reconciliation.get("matched") is not True:
        reasons.append("reconciliation")
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
    if body.get("local_isolation_status") != "pass":
        reasons.append("isolation")
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
        "local_security_status": "not-run",
        "local_isolation_status": "not-run",
        "local_soak_status": "not-run",
        "duration_seconds": DURATION_SECONDS,
        "timeout_seconds": TIMEOUT_SECONDS,
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
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "failure_stage": "",
        "failure_detail": "",
        "gate_reasons": [],
    }
    for flag in PROOF_FLAGS:
        body[flag] = False
    for flag in LOCAL_SECURITY_FLAGS:
        body[flag] = False
    return body


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    if profile != PROFILE_NAME:
        raise SecuritySpecError("security profile is not the fixed profile")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise SecuritySpecError("failure stage is invalid")
    body = _base_fields()
    body["local_security_status"] = "fail"
    body["failure_stage"] = stage
    return body


def assemble(
    *,
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    cancelled: int,
    wall_seconds: float,
    metrics: tuple[bool, int | None],
    failure_stage: str = "",
    failure_detail: str = "",
) -> dict[str, Any]:
    state, matched, corruption = assess_security(outcomes, snapshots, cancelled=cancelled)
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
    body["reconciliation"] = {
        "transport": "service-only",
        "matched": matched,
        "gyms": [dict(row) for row in snapshots] if snapshots else [],
    }
    if state == "not-run":
        body["local_isolation_status"] = "not-run"
        body["local_security_status"] = "not-run"
        return body
    if state == "pass" and leaked == 0 and credentials == 0 and not corruption:
        body["local_isolation_status"] = "pass"
    else:
        body["local_isolation_status"] = "fail"
    status, reasons = judge(body)
    if state == "pass" and status == "pass":
        body["local_security_status"] = "pass"
    else:
        body["local_security_status"] = "fail"
        body["gate_reasons"] = reasons or [state]
    return body


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in (*PROOF_FLAGS, *LOCAL_SECURITY_FLAGS):
        if body.get(flag) is True:
            raise SecuritySpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("schema_version") not in (None, SCHEMA):
        raise SecuritySpecError("security evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["evidence_name"] = EVIDENCE_NAME
    if body.get("local_soak_status") != "not-run":
        raise SecuritySpecError("local soak cannot be claimed by this profile")
    for key in ("local_security_status", "local_isolation_status"):
        if body.get(key) not in LOCAL_STATUSES:
            raise SecuritySpecError("local status must be pass, fail, or not-run")
    if body.get("local_security_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise SecuritySpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets):
        raise SecuritySpecError("security evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise SecuritySpecError("security evidence contains secret material")
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
        "load_test_proven",
        "formal_review_proven",
        "production_security_proven",
    ):
        if phrase not in command:
            errors.append(f"security command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "security.json",
        "local_security_status",
        "penetration_test_proven",
        "not a penetration test",
        "formal security review",
        "production security",
        "receipt download",
        "rate limit",
        "member-auth",
        "caddy_metrics_observed",
        "unavailable",
    ):
        if phrase not in runbook:
            errors.append(f"security runbook is missing {phrase}")
    for phrase in (
        "penetration test is proven",
        "formal security review is proven",
        "production security is proven",
        "tenant isolation is proven",
        "soak test is proven",
    ):
        if phrase in runbook:
            errors.append(f"security runbook claims {phrase}")
    for phrase in (
        "security.json",
        "staging_security_characterization",
        "does not replace endurance.json",
        "does not replace pt.json",
        "does not replace pt-cash.json",
        "does not replace load.json",
        "does not replace finance.json",
    ):
        if phrase not in harness:
            errors.append(f"staging harness is missing {phrase}")
    return errors


def valid_resource_id(value: object) -> str:
    if type(value) is not str or _UUID.fullmatch(value) is None:
        return ""
    parsed = uuid.UUID(value)
    version = parsed.version
    if str(parsed) != value or parsed.variant != uuid.RFC_4122:
        return ""
    if not isinstance(version, int) or isinstance(version, bool) or version < 1 or version > 8:
        return ""
    return value


__all__ = [
    "BFF_ROUTES",
    "COVERAGE",
    "DURATION_SECONDS",
    "EDGE",
    "EVIDENCE_NAME",
    "KEY_KINDS",
    "KIND",
    "LOCAL_SECURITY_FLAGS",
    "MAX_RESPONSE_BYTES",
    "PLAN_OPERATIONS",
    "PRIVATE_API_PATH",
    "PROBES",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PUBLIC_ORIGIN",
    "RULES",
    "SCHEMA",
    "SETUP",
    "TIMEOUT_SECONDS",
    "UNTRUSTED_ORIGIN",
    "SecuritySpecError",
    "assemble",
    "assert_bff_path",
    "assert_private_api_target",
    "assess_security",
    "auth_cookie_flags",
    "bff_required",
    "build_plan",
    "checksum_for",
    "classify_outcome",
    "credential_in_body",
    "csrf_failed",
    "failure_document",
    "fixed_host_redirect",
    "judge",
    "lint_texts",
    "make_key",
    "marker_in_success",
    "metrics_delta",
    "offline_redirects_pass",
    "origin_reflected",
    "public_outcome",
    "redirect_accepted",
    "require_int",
    "required_auth_cookies",
    "resolve_profile",
    "seal_evidence",
    "valid_resource_id",
    "verify_checksum",
]
