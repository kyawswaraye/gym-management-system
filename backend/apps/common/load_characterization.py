"""Pure rules for the disposable staging load characterization.

This module does not open sockets, import Django, or read credentials. The
staging management command and the rehearsal harness apply these rules. A
local pass is not a production load test, soak, capacity proof, or SLO.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import urllib.parse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SCHEMA = "gym-staging-load/v1"
PROJECT = "gym-staging-rehearsal"
SETTINGS_MODULE = "config.settings.staging_rehearsal"
PUBLIC_URL = "https://localhost:8443"
CONNECT_HOST = "caddy"
EXPECTED_SNI = "localhost"
REQUEST_PORT = 443
METRICS_PORT = 2020
MAX_CONNECTIONS = 40
CONNECTION_CEILING = 32
PROVISIONAL_P95_MS = 8000
MAX_DURATION_SECONDS = 600
MAX_WORKERS = 16
MAX_OPERATIONS = 10_000
MAX_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 16_384
AUTH_FAILURES = 12
AUTH_THROTTLE_NOTE = (
    "Staff authentication failures are measured. HTTP 401 and 429 are expected. "
    "auth_rate_limit_observed records whether a 429 was observed. "
    "A missing 429 does not fail this local characterization. "
    "The application throttle is per-process LocMem with the default 2 Gunicorn workers "
    "and an anonymous limit of 30/min. Edge and WAF rate control remain unproven."
)
_FINANCIAL_SCENARIOS = frozenset(
    {
        "cash_open",
        "cash_replay",
        "cash_read",
        "expense_setup",
        "expense_replay",
        "expense_read",
        "expense_create",
        "checkin_create",
        "checkin_replay",
    }
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_GET_REDIRECTS = frozenset({301, 302, 303})

PROOF_FLAGS = (
    "load_test_proven",
    "soak_test_proven",
    "production_capacity_proven",
    "slo_proven",
    "production_deploy_proven",
    "tenant_isolation_proven",
    "financial_reconciliation_proven",
    "ghcr_proven",
    "protected_environment_proven",
    "offsite_backup_proven",
    "external_provider_proven",
    "rpo_proven",
    "rto_proven",
    "alert_delivery_proven",
    "staging_vps_proven",
    "release_attestation_proven",
    "rollback_proven",
    "backup_proven",
    "restore_proven",
    "smoke_proven",
)
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
LOOP_PATTERN = (
    "dashboard_read",
    "report_read",
    "checkin_create",
    "checkin_list",
    "expense_create",
    "expense_read",
    "cash_read",
    "cross_gym_checkin",
    "cross_gym_expense",
    "cross_gym_cash",
)
_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}\Z")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_CADDY_5XX = re.compile(r'(?:code|status)="(5\d\d)"')
_ALLOWED_DB_HOSTS = frozenset({"postgres"})
_ALLOWED_APP_HOSTS = frozenset({"localhost", "api", "web", "caddy", "127.0.0.1"})


class LoadSpecError(ValueError):
    """A fixed profile or evidence document violated a local rule."""


@dataclass(frozen=True)
class Profile:
    name: str
    duration_seconds: int
    workers: int
    loop_operations: int
    timeout_seconds: int
    auth_failures: int

    def bounds(self) -> None:
        validate_bounds(
            duration_seconds=self.duration_seconds,
            workers=self.workers,
            operations=self.loop_operations + 64,
            timeout_seconds=self.timeout_seconds,
        )


@dataclass(frozen=True)
class ScenarioRule:
    success: frozenset[int]
    expected_rejection: frozenset[int]


PROFILES: dict[str, Profile] = {
    "short": Profile("short", 300, 2, 20, 8, AUTH_FAILURES),
    "bounded": Profile("bounded", 540, 4, 80, 8, AUTH_FAILURES),
}

RULES: dict[str, ScenarioRule] = {
    "public_login_page": ScenarioRule(frozenset({200}), frozenset()),
    "staff_api_blocked": ScenarioRule(frozenset(), frozenset({404})),
    "member_auth_failure": ScenarioRule(frozenset(), frozenset({400, 401, 403, 405, 422, 429})),
    "auth_failure": ScenarioRule(frozenset(), frozenset({401, 429})),
    "auth_login": ScenarioRule(frozenset({200}), frozenset()),
    "dashboard_read": ScenarioRule(frozenset({200}), frozenset()),
    "report_read": ScenarioRule(frozenset({200}), frozenset()),
    "checkin_create": ScenarioRule(frozenset({201}), frozenset()),
    "checkin_replay": ScenarioRule(frozenset({200}), frozenset()),
    "checkin_duplicate": ScenarioRule(frozenset(), frozenset({409})),
    "checkin_list": ScenarioRule(frozenset({200}), frozenset()),
    "cross_gym_checkin": ScenarioRule(frozenset(), frozenset({404})),
    "expense_setup": ScenarioRule(frozenset({201}), frozenset()),
    "expense_create": ScenarioRule(frozenset({201}), frozenset()),
    "expense_replay": ScenarioRule(frozenset({200, 201}), frozenset()),
    "expense_read": ScenarioRule(frozenset({200}), frozenset()),
    "cross_gym_expense": ScenarioRule(frozenset(), frozenset({404})),
    "cash_open": ScenarioRule(frozenset({201}), frozenset()),
    "cash_replay": ScenarioRule(frozenset({200, 201}), frozenset()),
    "cash_read": ScenarioRule(frozenset({200}), frozenset()),
    "cross_gym_cash": ScenarioRule(frozenset(), frozenset({404})),
}

COVERAGE: tuple[dict[str, object], ...] = (
    {
        "id": "public_login_page",
        "method": "GET",
        "route": "/login",
        "transport": "http",
        "covered": True,
        "note": "Browser login document through Caddy.",
    },
    {
        "id": "staff_api_blocked",
        "method": "POST",
        "route": "/api/v1/auth/login/",
        "transport": "http",
        "covered": True,
        "note": "Staff API on the public origin is blocked by Caddy.",
    },
    {
        "id": "member_auth_failure",
        "method": "POST",
        "route": "/api/v1/member-auth/login/",
        "transport": "http",
        "covered": True,
        "note": "One synthetic unknown member login through Caddy.",
    },
    {
        "id": "browser_login",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "http",
        "covered": True,
        "note": "Browser BFF login. Tokens stay in the coordinator memory.",
    },
    {
        "id": "auth_failure_rate_limit",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "http",
        "covered": True,
        "note": "Fixed unknown-account failures. 401 and 429 are expected.",
    },
    {
        "id": "dashboard_read",
        "method": "GET",
        "route": "/api/operations/dashboard",
        "transport": "http",
        "covered": True,
        "note": "Authenticated owner dashboard through the BFF.",
    },
    {
        "id": "daily_report_read",
        "method": "GET",
        "route": "/api/operations/reports/daily",
        "transport": "http",
        "covered": True,
        "note": "Authenticated daily report through the BFF.",
    },
    {
        "id": "checkin_create",
        "method": "POST",
        "route": "/api/operations/check-ins",
        "transport": "http",
        "covered": True,
        "note": "Manual check-in create. Eligibility is the snapshot on that response.",
    },
    {
        "id": "checkin_list",
        "method": "GET",
        "route": "/api/operations/check-ins",
        "transport": "http",
        "covered": True,
        "note": "Authenticated check-in list through the BFF.",
    },
    {
        "id": "expense_draft",
        "method": "POST",
        "route": "/api/operations/expenses",
        "transport": "http",
        "covered": True,
        "note": "Draft create, idempotent replay, and detail read.",
    },
    {
        "id": "cash_session",
        "method": "POST",
        "route": "/api/operations/cash-sessions/open",
        "transport": "http",
        "covered": True,
        "note": "One cash-session open, replay, and list read per gym.",
    },
    {
        "id": "cross_gym",
        "method": "POST",
        "route": "/api/operations/check-ins",
        "transport": "http",
        "covered": True,
        "note": "Negative check-in, expense, and cash probes using the other gym.",
    },
    {
        "id": "receipt_issue",
        "method": "",
        "route": "payment receipt issuance",
        "transport": "uncovered",
        "covered": False,
        "note": "Receipt issuance follows payment posting. This profile does not post a payment.",
    },
    {
        "id": "receipt_download",
        "method": "GET",
        "route": "/api/operations/receipts/{id}/download/",
        "transport": "uncovered",
        "covered": False,
        "note": "No receipt exists to download.",
    },
    {
        "id": "pt_sale_or_session",
        "method": "POST",
        "route": "/api/v1/pt-sales/",
        "transport": "uncovered",
        "covered": False,
        "note": "No trainer, package, or PT session is seeded.",
    },
    {
        "id": "pt_report",
        "method": "GET",
        "route": "/api/operations/reports/pt-sessions/",
        "transport": "uncovered",
        "covered": False,
        "note": "An empty PT report would not characterize PT activity.",
    },
)


def validate_bounds(
    *,
    duration_seconds: int,
    workers: int,
    operations: int,
    timeout_seconds: int,
) -> None:
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise LoadSpecError("duration exceeds the staging cap")
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise LoadSpecError("worker count exceeds the staging cap")
    if type(operations) is not int or not 1 <= operations <= MAX_OPERATIONS:
        raise LoadSpecError("operation count exceeds the staging cap")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise LoadSpecError("timeout exceeds the staging cap")


def resolve_profile(name: str) -> Profile:
    profile = PROFILES.get(name)
    if profile is None:
        raise LoadSpecError("load profile is not one of the fixed profiles")
    profile.bounds()
    return profile


def build_plan(name: str) -> list[dict[str, object]]:
    profile = resolve_profile(name)
    steps: list[dict[str, object]] = [
        _step("public_login_page", None, 0, phase="fixed"),
        _step("staff_api_blocked", None, 0, phase="fixed"),
        _step("member_auth_failure", None, 0, phase="fixed"),
    ]
    for ordinal in range(profile.auth_failures):
        steps.append(_step("auth_failure", None, ordinal, phase="fixed"))
    for gym in (0, 1):
        steps.append(_step("auth_login", gym, 0, phase="fixed"))
    for gym in (0, 1):
        steps.extend(
            (
                _step("cash_open", gym, 0, phase="fixed"),
                _step("cash_replay", gym, 0, phase="fixed"),
                _step("expense_setup", gym, 0, phase="fixed"),
                _step("expense_replay", gym, 0, phase="fixed"),
                _step("expense_read", gym, 0, phase="fixed"),
            )
        )
    # Cross-gym reads need the other gym's ids. The fixed phase is one thread,
    # so both creates finish before any cross-gym probe is queued.
    for gym in (0, 1):
        steps.extend(
            (
                _step("cross_gym_expense", gym, 0, phase="fixed"),
                _step("cross_gym_cash", gym, 0, phase="fixed"),
                _step("cross_gym_checkin", gym, 0, phase="fixed"),
            )
        )
    width = len(LOOP_PATTERN)
    for ordinal in range(profile.loop_operations):
        scenario = LOOP_PATTERN[ordinal % width]
        gym = (ordinal // width) % 2
        steps.append(_step(scenario, gym, ordinal, phase="loop"))
    for gym in (0, 1):
        steps.append(_step("checkin_replay", gym, 0, phase="final"))
    if len(steps) > MAX_OPERATIONS:
        raise LoadSpecError("operation count exceeds the staging cap")
    return steps


def _step(scenario: str, gym: int | None, ordinal: int, *, phase: str) -> dict[str, object]:
    if scenario not in RULES or phase not in {"fixed", "loop", "final"}:
        raise LoadSpecError("scenario is not in the fixed catalog")
    return {
        "scenario": scenario,
        "gym": gym,
        "ordinal": ordinal,
        "required": True,
        "phase": phase,
    }


def members_required(plan: Sequence[Mapping[str, object]]) -> tuple[int, int]:
    counts = [1, 1]
    for step in plan:
        gym = step.get("gym")
        if (
            step.get("scenario") == "checkin_create"
            and isinstance(gym, int)
            and not isinstance(gym, bool)
            and gym in (0, 1)
        ):
            counts[gym] += 1
    return counts[0], counts[1]


def make_idempotency_key(kind: str, gym: int, ordinal: int) -> str:
    if kind not in {"cash-open", "expense", "checkin", "cross-checkin"}:
        raise LoadSpecError("idempotency key kind is not fixed")
    if gym not in (0, 1) or type(ordinal) is not int or ordinal < 0 or ordinal > 100_000:
        raise LoadSpecError("idempotency key ordinal is outside the profile")
    key = f"load-{kind}-g{gym}-{ordinal:04d}"
    if _KEY.fullmatch(key) is None:
        raise LoadSpecError("idempotency key is invalid")
    return key


def classify_status(scenario: str, status: object) -> str:
    """Return success, expected_rejection, or unexpected.

    HTTP 5xx and transport failures are never expected, including when a
    scenario list mistakenly contains them.
    """

    rule = RULES.get(scenario)
    if rule is None or isinstance(status, bool) or not isinstance(status, int):
        return "unexpected"
    if status >= 500:
        return "unexpected"
    if status in rule.success:
        return "success"
    if status in rule.expected_rejection:
        return "expected_rejection"
    return "unexpected"


def percentile_ms(samples: Sequence[int], percent: int) -> int | None:
    if not samples:
        return None
    if type(percent) is not int or not 1 <= percent <= 100:
        raise LoadSpecError("percentile is outside 1..100")
    ordered = sorted(int(sample) for sample in samples)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


def deadline_action(*, deadline: float, now: float, pending: int) -> str:
    if pending <= 0:
        return "done"
    if now >= deadline:
        return "cancel"
    return "run"


def ip_is_local(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        return False
    return parsed.is_loopback or parsed.is_private


def assert_request_target(host: str, port: int, sni: str, resolved: Sequence[str]) -> None:
    if host != CONNECT_HOST or port != REQUEST_PORT or sni != EXPECTED_SNI:
        raise LoadSpecError("load target is outside the rehearsal allowlist")
    if not resolved or any(not ip_is_local(item) for item in resolved):
        raise LoadSpecError("load target address is not loopback or private")


def assert_metrics_target(host: str, port: int, resolved: Sequence[str]) -> None:
    if host != CONNECT_HOST or port != METRICS_PORT:
        raise LoadSpecError("metrics target is outside the rehearsal allowlist")
    if not resolved or any(not ip_is_local(item) for item in resolved):
        raise LoadSpecError("metrics target address is not loopback or private")


def redirect_allowed(location: str, expected_host: str) -> bool:
    if expected_host != EXPECTED_SNI:
        return False
    if location.startswith("/") and not location.startswith("//"):
        return True
    parsed = urllib.parse.urlparse(location)
    return parsed.scheme == "https" and parsed.hostname == expected_host


def clip_body(payload: bytes, cap: int = MAX_RESPONSE_BYTES) -> tuple[bytes, bool]:
    if cap != MAX_RESPONSE_BYTES:
        raise LoadSpecError("response cap is fixed")
    if len(payload) <= cap:
        return payload, False
    return payload[:cap], True


def marker_present(payload: bytes, markers: Sequence[bytes]) -> bool:
    return any(marker and marker in payload for marker in markers)


def caddy_5xx_total(text: str) -> int | None:
    total = 0
    found = False
    for line in text.splitlines():
        if not line or line.startswith("#") or "caddy_http_" not in line:
            continue
        if _CADDY_5XX.search(line) is None:
            continue
        raw = line.rsplit(" ", 1)[-1]
        try:
            value = int(float(raw))
        except ValueError:
            continue
        found = True
        total += value
    return total if found else None


_PROMETHEUS_SAMPLE = re.compile(
    r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{[^}\n]*\})?[ \t]+[-+0-9.eE]+([ \t]+[-+0-9.eE]+)?\Z"
)


def caddy_metrics_observation(text: str) -> tuple[bool, int | None]:
    """Return whether a private metrics document was read, and its 5xx total.

    A Prometheus document with no 5xx series is an observed zero. Text that is
    not a Prometheus document is unobserved. Callers must keep a failed scrape
    unobserved instead of passing empty text through as zero.
    """

    observed = any(
        line and not line.startswith("#") and _PROMETHEUS_SAMPLE.fullmatch(line) is not None
        for line in text.splitlines()
    )
    if not observed:
        return False, None
    total = caddy_5xx_total(text)
    return True, 0 if total is None else total


def connection_within_ceiling(peak: object, maximum: object) -> bool:
    if type(peak) is not int or type(maximum) is not int:
        return False
    return maximum == MAX_CONNECTIONS and 0 <= peak <= CONNECTION_CEILING


def reconciliation_matches(observed: Mapping[str, int], expected: Mapping[str, int]) -> bool:
    return all(observed.get(key) == value for key, value in expected.items())


def redirect_follow(
    *,
    status: int,
    location: str,
    method: str,
    redirects_followed: int,
) -> dict[str, object] | None:
    """Return one safe follow request, or None when the redirect is rejected.

    At most one redirect is followed. 301, 302, and 303 become GET without the
    original body, CSRF header, or idempotency key. 307 and 308 keep all three.
    """

    if type(status) is not int or status not in _REDIRECT_STATUSES:
        raise LoadSpecError("redirect status is not a redirect")
    if redirects_followed >= 1 or not redirect_allowed(location, EXPECTED_SNI):
        return None
    if location.startswith("/") and not location.startswith("//"):
        target = location
    else:
        parsed = urllib.parse.urlparse(location)
        if (
            parsed.scheme != "https"
            or parsed.hostname != EXPECTED_SNI
            or parsed.username
            or parsed.password
            or parsed.port not in (None, REQUEST_PORT)
        ):
            return None
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
    if not target.startswith("/") or target.startswith("//"):
        return None
    switch_to_get = status in _GET_REDIRECTS
    return {
        "method": "GET" if switch_to_get else method,
        "target": target,
        "preserve_body": not switch_to_get,
        "preserve_csrf": not switch_to_get,
        "preserve_idempotency": not switch_to_get,
    }


def scenarios_match(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> bool:
    """Return whether outcomes are the planned phase and scenario multiset.

    A ``checkin_duplicate`` outcome may stand in for a planned ``checkin_create``
    in the same phase. No other substitution keeps the same count.
    """

    if len(outcomes) != len(plan):
        return False
    planned: Counter[tuple[str, str]] = Counter()
    for step in plan:
        phase = step.get("phase")
        scenario = step.get("scenario")
        if not isinstance(phase, str) or not isinstance(scenario, str):
            return False
        planned[(phase, scenario)] += 1
    actual: Counter[tuple[str, str]] = Counter()
    duplicates: Counter[str] = Counter()
    for item in outcomes:
        phase = item.get("phase")
        scenario = item.get("scenario")
        if not isinstance(phase, str) or not phase or not isinstance(scenario, str):
            return False
        if scenario == "checkin_duplicate":
            duplicates[phase] += 1
            continue
        actual[(phase, scenario)] += 1
    for phase, count in duplicates.items():
        key = (phase, "checkin_create")
        if planned.get(key, 0) < count:
            return False
        planned[key] -= count
        if planned[key] == 0:
            del planned[key]
    remaining = Counter({key: value for key, value in planned.items() if value})
    return remaining == actual


def planned_create_counts(plan: Sequence[Mapping[str, object]]) -> dict[str, int]:
    expenses = checkins = cash = 0
    for step in plan:
        scenario = step.get("scenario")
        if scenario in {"expense_setup", "expense_create"}:
            expenses += 1
        elif scenario == "checkin_create":
            checkins += 1
        elif scenario == "cash_open":
            cash += 1
    return {"expenses": expenses, "checkins": checkins, "cash": cash}


def assess_financial(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    observed: Mapping[str, int] | None,
) -> tuple[str, bool, bool]:
    """Return local financial status, matched, and data_corruption.

    A pass requires every planned financial step to succeed, every planned
    expense, check-in, and cash create to succeed, and the service-only deltas
    to equal those successes. Zero equals zero after an early stop is not-run.
    data_corruption is true only when measured counts disagree.
    """

    if observed is None:
        return "not-run", False, False
    planned = planned_create_counts(plan)
    successful = {
        "expenses": _successes(outcomes, "expense_setup") + _successes(outcomes, "expense_create"),
        "checkins": _successes(outcomes, "checkin_create"),
        "cash": _successes(outcomes, "cash_open"),
    }
    keys = ("expenses", "checkins", "cash")
    mismatch = any(observed.get(key) != successful[key] for key in keys)
    vacuous = all(successful[key] == 0 and observed.get(key) == 0 for key in keys)
    corruption = mismatch and not vacuous
    steps_complete = _financial_steps_complete(plan, outcomes)
    exercised = all(planned[key] >= 1 and successful[key] == planned[key] for key in keys)
    if steps_complete and exercised and not mismatch:
        return "pass", True, False
    if not steps_complete and vacuous:
        return "not-run", False, False
    return "fail", False, corruption


def _successes(outcomes: Sequence[Mapping[str, object]], scenario: str) -> int:
    return sum(
        1
        for item in outcomes
        if item.get("scenario") == scenario and item.get("classification") == "success"
    )


def _financial_steps_complete(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
) -> bool:
    planned: Counter[str] = Counter(
        scenario
        for step in plan
        if isinstance((scenario := step.get("scenario")), str) and scenario in _FINANCIAL_SCENARIOS
    )
    if not planned:
        return False
    successes: Counter[str] = Counter(
        scenario
        for item in outcomes
        if item.get("classification") == "success"
        and isinstance((scenario := item.get("scenario")), str)
        and scenario in _FINANCIAL_SCENARIOS
    )
    return successes == planned


def material_leaked(encoded: str, secrets: Sequence[str]) -> bool:
    for secret in secrets:
        if len(secret) >= 8 and secret in encoded:
            return True
    if _EMAIL.search(encoded) or _JWT.search(encoded):
        return True
    return "BEGIN " in encoded and "KEY" in encoded


def assert_rehearsal_context(
    *,
    settings_module: str,
    staging_flag: bool,
    project: str,
    public_url: str,
    database_host: str,
    database_user: str,
    allowed_hosts: Sequence[str],
) -> None:
    if settings_module != SETTINGS_MODULE or staging_flag is not True:
        raise LoadSpecError("load characterization requires the staging rehearsal settings")
    if project != PROJECT:
        raise LoadSpecError("load characterization requires the disposable project")
    if public_url != PUBLIC_URL or database_host not in _ALLOWED_DB_HOSTS:
        raise LoadSpecError("load characterization refused a non-rehearsal target")
    if database_user != "gym_runtime":
        raise LoadSpecError("load characterization requires the runtime role")
    if not allowed_hosts or any(host not in _ALLOWED_APP_HOSTS for host in allowed_hosts):
        raise LoadSpecError("load characterization refused a non-rehearsal host")


def summarize_scenario(
    scenario: str,
    outcomes: Sequence[Mapping[str, object]],
    *,
    elapsed_seconds: float,
) -> dict[str, object]:
    latencies = [
        latency
        for item in outcomes
        if item.get("transport") == "http"
        and isinstance((latency := item.get("latency_ms")), int)
        and not isinstance(latency, bool)
    ]
    statuses: dict[str, int] = {}
    successes = expected = unexpected = 0
    for item in outcomes:
        label = str(item.get("classification"))
        if label == "success":
            successes += 1
        elif label == "expected_rejection":
            expected += 1
        else:
            unexpected += 1
        code = str(item.get("status"))
        statuses[code] = statuses.get(code, 0) + 1
    elapsed = elapsed_seconds if elapsed_seconds > 0 else 0.001
    return {
        "scenario": scenario,
        "transport": "http",
        "count": len(outcomes),
        "successes": successes,
        "expected_rejections": expected,
        "unexpected_errors": unexpected,
        "status": statuses,
        "throughput_per_second": round(len(outcomes) / elapsed, 3),
        "latency_ms": {
            "p50": percentile_ms(latencies, 50),
            "p95": percentile_ms(latencies, 95),
            "p99": percentile_ms(latencies, 99),
            "max": max(latencies) if latencies else None,
        },
    }


def judge(report: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if report.get("local_isolation_status") != "pass":
        reasons.append("isolation")
    if report.get("local_financial_reconciliation_status") != "pass":
        reasons.append("financial_reconciliation")
    if report.get("cross_gym_leakage") != 0:
        reasons.append("cross_gym_leakage")
    if report.get("credential_exposure") != 0:
        reasons.append("credential_exposure")
    if report.get("unexpected_errors") != 0:
        reasons.append("unexpected_errors")
    if report.get("api_unexpected_5xx") != 0:
        reasons.append("api_unexpected_5xx")
    caddy = report.get("caddy_5xx_delta")
    if caddy not in (None, 0):
        reasons.append("caddy_5xx")
    if report.get("reconciliation_matched") is not True:
        reasons.append("reconciliation")
    if report.get("data_corruption") is True:
        reasons.append("data_corruption")
    if not connection_within_ceiling(
        report.get("postgres_connection_peak"),
        report.get("postgres_max_connections"),
    ):
        reasons.append("connection_ceiling")
    if report.get("cancelled_operations", 0) != 0:
        reasons.append("cancelled")
    if report.get("required_complete") is not True:
        reasons.append("required_steps")
    latency = report.get("http_p95_ms")
    if type(latency) is int and latency > PROVISIONAL_P95_MS:
        reasons.append("provisional_latency_ceiling")
    if any(report.get(flag) is True for flag in PROOF_FLAGS):
        reasons.append("false_proof")
    status = "pass" if not reasons else "fail"
    return status, reasons


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    """Return a sealable failure document with no live proof and no secrets."""

    if profile not in PROFILES:
        raise LoadSpecError("load profile is not one of the fixed profiles")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise LoadSpecError("failure stage is invalid")
    return {
        "kind": "local-disposable-load-characterization",
        "profile": profile,
        "local_load_status": "fail",
        "local_isolation_status": "not-run",
        "local_financial_reconciliation_status": "not-run",
        "thresholds_are_approved_slos": False,
        "failure_stage": stage,
        "cross_gym_leakage": 0,
        "credential_exposure": 0,
        "unexpected_errors": 0,
        "api_unexpected_5xx": 0,
        "caddy_5xx_delta": None,
        "reconciliation_matched": False,
        "data_corruption": False,
        "postgres_connection_peak": None,
        "postgres_max_connections": None,
        "auth_rate_limit_observed": False,
        "cancelled_operations": 0,
        "required_complete": False,
        "http_p95_ms": None,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False},
    }


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in PROOF_FLAGS:
        if body.get(flag) is True:
            raise LoadSpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("thresholds_are_approved_slos") is not False:
        raise LoadSpecError("provisional thresholds were described as approved SLOs")
    if body.get("schema_version") not in (None, SCHEMA):
        raise LoadSpecError("load evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["provisional_thresholds"] = True
    body["approved_slo"] = False
    for key in (
        "local_load_status",
        "local_isolation_status",
        "local_financial_reconciliation_status",
    ):
        if body.get(key) not in LOCAL_STATUSES:
            raise LoadSpecError("local status must be pass, fail, or not-run")
    if body.get("local_load_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise LoadSpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets):
        raise LoadSpecError("load evidence contains secret material")
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
        "load_test_proven",
        "slo_proven",
        "production_capacity_proven",
    ):
        if phrase not in command:
            errors.append(f"load command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "provisional",
        "load_test_proven",
        "soak_test_proven",
        "production_capacity_proven",
        "slo_proven",
        "local_load_status",
        "local_isolation_status",
        "local_financial_reconciliation_status",
        "not an approved SLO",
        "receipt issuance",
        "PT",
        "transport",
        "LocMem",
        "auth_rate_limit_observed",
        "WAF",
    ):
        if phrase not in runbook:
            errors.append(f"load runbook is missing {phrase}")
    for phrase in (
        "SLO is proven",
        "production capacity is proven",
        "load test is proven",
        "soak test is proven",
    ):
        if phrase in runbook:
            errors.append(f"load runbook claims {phrase}")
    if "load" not in harness or "short" not in harness:
        errors.append("staging harness does not expose the fixed load profile")
    return errors
