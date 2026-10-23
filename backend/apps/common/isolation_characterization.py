"""Pure rules for the disposable local tenant-isolation characterization.

This module does not open sockets, import Django, or read credentials. The
staging management command and the rehearsal harness apply these rules. A
local pass is project-authored verification on the rehearsal stack. It is
not independent tenant-isolation proof, a penetration test, a formal
security review, or production proof.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from apps.common.load_characterization import MAX_RESPONSE_BYTES, PROOF_FLAGS, material_leaked
from apps.common.security_characterization import (
    metrics_delta,
    valid_resource_id,
)

SCHEMA = "gym-staging-isolation/v1"
PROJECT = "gym-staging-rehearsal"
KIND = "local-disposable-isolation-characterization"
PROFILE_NAME = "isolation"
EVIDENCE_NAME = "isolation.json"
SETTINGS_MODULE = "config.settings.staging_rehearsal"
DURATION_SECONDS = 180
TIMEOUT_SECONDS = 8.0
PUBLIC_ORIGIN = "https://localhost:8443"
PUBLIC_HOST = "localhost"
CONNECT_HOST = "caddy"
REQUEST_PORT = 443
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
LOCAL_ISOLATION_FLAGS = (
    "penetration_test_proven",
    "formal_review_proven",
    "production_security_proven",
)
AUDIT_ROWS_CAP = 32
_UUID_CANONICAL = "[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_AUDIT_QUERY = "module=cashbook&limit=25"

RUNTIME: tuple[str, ...] = (
    "schema_denied",
    "grant_denied",
    "missing_context_read",
    "missing_context_write",
    "rollback_context",
    "context_reuse",
)
SETUP: tuple[str, ...] = ("auth_login",)
HTTP: tuple[str, ...] = (
    "members_list",
    "expenses_list",
    "pt_sales_list",
    "cash_list",
    "audit_list",
    "report_daily",
    "idor_member_read",
    "idor_expense_read",
    "idor_pt_read",
    "idor_cash_read",
    "idor_member_patch",
    "idor_expense_approve",
)
RLS: tuple[str, ...] = (
    "rls_own_counts",
    "rls_cross_read",
    "rls_cross_update",
    "rls_file_cross",
    "rls_audit_cross",
    "rls_aggregate",
)
PLAN_OPERATIONS = len(RUNTIME) + 2 * (len(SETUP) + len(HTTP) + len(RLS))
_POSITIVE = frozenset((*RUNTIME, *SETUP, *HTTP[:6], *RLS))
SEED_COUNTS: dict[str, int] = {
    "members": 1,
    "expenses": 1,
    "expenses_pending": 1,
    "expenses_approved": 0,
    "checkins": 1,
    "cash_sessions": 1,
    "cash_open": 1,
    "pt_sales": 1,
    "pt_sales_pending": 1,
    "pt_sales_posted": 0,
    "files": 1,
    "gym_settings": 1,
    "gym_mismatch": 0,
    "expense_sum_cross_cents": 0,
    "file_cross": 0,
    "audit_cross": 0,
}
BFF_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/api/auth/login", "none"),
    ("GET", "/api/operations/members", "none"),
    ("GET", "/api/operations/expenses", "none"),
    ("GET", "/api/operations/pt-sales", "none"),
    ("GET", "/api/operations/cash-sessions", "none"),
    ("GET", "/api/operations/audit-logs", "audit"),
    ("GET", "/api/operations/reports/daily", "date"),
    ("GET", "/api/operations/members/{id}", "none"),
    ("GET", "/api/operations/expenses/{id}", "none"),
    ("GET", "/api/operations/pt-sales/{id}", "none"),
    ("GET", "/api/operations/cash-sessions/{id}", "none"),
    ("PATCH", "/api/operations/members/{id}", "none"),
    ("POST", "/api/operations/expenses/{id}/approve", "none"),
)
COVERAGE: tuple[dict[str, object], ...] = (
    {
        "id": "runtime_schema_grant",
        "covered": True,
        "note": "The runtime role cannot CREATE TABLE or GRANT on the public schema.",
    },
    {
        "id": "missing_context",
        "covered": True,
        "note": "Forced RLS hides tenant rows and blocks writes when gym context is empty.",
    },
    {
        "id": "rollback_reuse",
        "covered": True,
        "note": "A nested context change rolls back, and a new transaction does not reuse it.",
    },
    {
        "id": "bff_list_report",
        "covered": True,
        "note": "Own-gym member, expense, PT, cash, audit lists and the daily report stay local.",
    },
    {
        "id": "bff_idor",
        "covered": True,
        "note": (
            "Cross-gym member, expense, PT, and cash reads plus member patch and expense approve."
        ),
    },
    {
        "id": "rls_orm",
        "covered": True,
        "note": "Own counts, cross-gym reads/updates, file, audit, and expense aggregates.",
    },
)
UNCOVERED: tuple[dict[str, object], ...] = (
    {
        "id": "object_storage_key_guessing",
        "covered": False,
        "note": "Direct MinIO or S3 object-key access is not probed.",
    },
    {
        "id": "file_upload_scan_download",
        "covered": False,
        "note": "Upload, ClamAV scan, and stored-object download are not part of this matrix.",
    },
    {
        "id": "file_bff_download_url",
        "covered": False,
        "note": (
            "BFF file download-url is not probed. Seeded metadata has no stored object, "
            "so a 404 would not prove isolation. File RLS metadata is covered."
        ),
    },
    {
        "id": "receipt_csv_export",
        "covered": False,
        "note": (
            "Receipt download and report CSV export stay uncovered. "
            "The daily report read is covered."
        ),
    },
    {
        "id": "member_published_api",
        "covered": False,
        "note": "Published member-auth isolation is not this staff and RLS matrix.",
    },
    {
        "id": "replica_read",
        "covered": False,
        "note": "Replica or lagged-read isolation is not exercised.",
    },
    {
        "id": "bypassrls_superuser",
        "covered": False,
        "note": "Table-owner, BYPASSRLS, and superuser paths are not claimed.",
    },
    {
        "id": "cache_http",
        "covered": False,
        "note": "HTTP cache and Redis isolation are not this characterization.",
    },
    {
        "id": "retail_salary_inventory_booking",
        "covered": False,
        "note": "Retail, salary, inventory, and booking isolation are not seeded here.",
    },
    {
        "id": "platform_admin",
        "covered": False,
        "note": "Platform administration routes are not probed.",
    },
    {
        "id": "raw_sql_mutation",
        "covered": False,
        "note": "Unsafe raw SQL mutation that bypasses application invariants is refused.",
    },
)
_UNCOVERED_IDS = tuple(str(item["id"]) for item in UNCOVERED)
_COVERAGE_IDS = tuple(str(item["id"]) for item in COVERAGE)


class IsolationSpecError(ValueError):
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
        raise IsolationSpecError("scenario success and rejection overlap")
    if 500 in success or 500 in rejection:
        raise IsolationSpecError("scenario treats an internal error as expected")
    if transport not in {"runtime", "bff"}:
        raise IsolationSpecError("scenario transport is outside the catalog")
    return ScenarioRule(success, rejection, transport, phase)


def _catalog() -> dict[str, ScenarioRule]:
    runtime = {name: _rule(frozenset({200}), frozenset(), "runtime", "runtime") for name in RUNTIME}
    setup = {"auth_login": _rule(frozenset({200}), frozenset(), "bff", "setup")}
    lists = {
        "members_list": _rule(frozenset({200}), frozenset(), "bff", "http"),
        "expenses_list": _rule(frozenset({200}), frozenset(), "bff", "http"),
        "pt_sales_list": _rule(frozenset({200}), frozenset(), "bff", "http"),
        "cash_list": _rule(frozenset({200}), frozenset(), "bff", "http"),
        "audit_list": _rule(frozenset({200}), frozenset(), "bff", "http"),
        "report_daily": _rule(frozenset({200}), frozenset(), "bff", "http"),
    }
    idor = {
        "idor_member_read": _rule(frozenset(), frozenset({404}), "bff", "http"),
        "idor_expense_read": _rule(frozenset(), frozenset({404}), "bff", "http"),
        "idor_pt_read": _rule(frozenset(), frozenset({404}), "bff", "http"),
        "idor_cash_read": _rule(frozenset(), frozenset({404}), "bff", "http"),
        "idor_member_patch": _rule(frozenset(), frozenset({404}), "bff", "http"),
        "idor_expense_approve": _rule(frozenset(), frozenset({404}), "bff", "http"),
    }
    rls = {name: _rule(frozenset({200}), frozenset(), "runtime", "rls") for name in RLS}
    rules = {**runtime, **setup, **lists, **idor, **rls}
    names = (*RUNTIME, *SETUP, *HTTP, *RLS)
    if tuple(rules) != names or len(rules) != len(names):
        raise IsolationSpecError("scenario catalog drifted")
    return rules


RULES = _catalog()


def require_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise IsolationSpecError("integer bound was not an integer")
    if value < minimum or value > maximum:
        raise IsolationSpecError("integer bound was outside the fixed range")
    return value


def resolve_profile(name: object) -> str:
    if name != PROFILE_NAME:
        raise IsolationSpecError("isolation profile is not the fixed profile")
    require_int(PLAN_OPERATIONS, minimum=1, maximum=80)
    require_int(DURATION_SECONDS, minimum=1, maximum=180)
    if type(TIMEOUT_SECONDS) is not float or TIMEOUT_SECONDS != 8.0:
        raise IsolationSpecError("isolation timeout is not the fixed timeout")
    if len(UNCOVERED) != len(_UNCOVERED_IDS) or len(set(_UNCOVERED_IDS)) != len(_UNCOVERED_IDS):
        raise IsolationSpecError("uncovered claims drifted")
    if len(COVERAGE) != len(_COVERAGE_IDS) or len(set(_COVERAGE_IDS)) != len(_COVERAGE_IDS):
        raise IsolationSpecError("coverage claims drifted")
    if set(_COVERAGE_IDS) & set(_UNCOVERED_IDS):
        raise IsolationSpecError("coverage and uncovered claims overlap")
    return PROFILE_NAME


def build_plan() -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    for scenario in RUNTIME:
        plan.append({"scenario": scenario, "gym": None, "phase": "runtime"})
    for gym in (0, 1):
        for scenario in SETUP:
            plan.append({"scenario": scenario, "gym": gym, "phase": "setup"})
        for scenario in HTTP:
            plan.append({"scenario": scenario, "gym": gym, "phase": "http"})
        for scenario in RLS:
            plan.append({"scenario": scenario, "gym": gym, "phase": "rls"})
    if len(plan) != PLAN_OPERATIONS:
        raise IsolationSpecError("isolation plan drifted")
    return plan


def _path_shape_rejected(path: str) -> bool:
    if not path.isascii() or not path.startswith("/") or path.startswith("//"):
        return True
    if ".." in path or "://" in path or "@" in path or "\\" in path or "#" in path:
        return True
    return any(char in path for char in "% \t\r\n\x00")


def _route_pattern(template: str) -> re.Pattern[str]:
    if template.count("{id}") > 1 or "{" in template.replace("{id}", ""):
        raise IsolationSpecError("isolation route template is invalid")
    pieces = template.split("{id}")
    body = _UUID_CANONICAL.join(re.escape(piece) for piece in pieces)
    return re.compile(rf"{body}\Z")


_COMPILED_BFF_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (method, _route_pattern(template), query) for method, template, query in BFF_ROUTES
)
if len(_COMPILED_BFF_ROUTES) != len(BFF_ROUTES):
    raise IsolationSpecError("isolation route catalog is invalid")


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


def _exact_audit_query(query: str) -> bool:
    return query == _AUDIT_QUERY


def assert_isolation_route(method: object, path: object) -> None:
    if type(method) is not str or type(path) is not str or _path_shape_rejected(path):
        raise IsolationSpecError("isolation path is outside the catalog")
    if path.count("?") > 1:
        raise IsolationSpecError("isolation path is outside the catalog")
    base, separator, query = path.partition("?")
    for rule_method, pattern, query_mode in _COMPILED_BFF_ROUTES:
        if rule_method != method or pattern.fullmatch(base) is None:
            continue
        if query_mode == "none":
            if separator:
                raise IsolationSpecError("isolation query is outside the catalog")
            return
        if query_mode == "date":
            if not separator or not _exact_date_query(query):
                raise IsolationSpecError("isolation query is outside the catalog")
            return
        if query_mode == "audit":
            if not separator or not _exact_audit_query(query):
                raise IsolationSpecError("isolation query is outside the catalog")
            return
        raise IsolationSpecError("isolation query is outside the catalog")
    raise IsolationSpecError("isolation path is outside the catalog")


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
        raise IsolationSpecError("isolation scenario is outside the catalog")
    if scenario in RUNTIME and gym is not None:
        raise IsolationSpecError("runtime scenario was bound to a gym")
    if scenario not in RUNTIME and gym not in (0, 1):
        raise IsolationSpecError("gym scenario is missing its gym")
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


def runtime_status(passed: bool) -> int:
    return 200 if passed is True else 409


def _int_field(row: Mapping[str, object], key: str) -> int | None:
    value = row.get(key)
    if type(value) is not int or isinstance(value, bool):
        return None
    return value


def _seed_match(row: Mapping[str, object]) -> bool:
    for key, expected in SEED_COUNTS.items():
        if _int_field(row, key) != expected:
            return False
    audit = _int_field(row, "audit_rows")
    if audit is None or audit < 1 or audit > AUDIT_ROWS_CAP:
        return False
    if row.get("member_status") != "ACTIVE" or row.get("member_notes_blank") is not True:
        return False
    if row.get("member_gym_match") is not True or row.get("owner_is_owner") is not True:
        return False
    return True


def _protected_changed(row: Mapping[str, object]) -> bool:
    if row.get("owner_is_owner") is not True:
        return True
    if row.get("member_status") != "ACTIVE" or row.get("member_notes_blank") is not True:
        return True
    if row.get("member_gym_match") is not True:
        return True
    approved = _int_field(row, "expenses_approved")
    posted = _int_field(row, "pt_sales_posted")
    if approved is None or posted is None or approved > 0 or posted > 0:
        return True
    mismatch = _int_field(row, "gym_mismatch")
    if mismatch is None or mismatch > 0:
        return True
    for key in ("file_cross", "audit_cross", "expense_sum_cross_cents"):
        value = _int_field(row, key)
        if value is None or value > 0:
            return True
    return False


def _stored_exceeds(row: Mapping[str, object]) -> bool:
    pairs = (
        ("members", 1),
        ("expenses", 1),
        ("checkins", 1),
        ("cash_sessions", 1),
        ("pt_sales", 1),
        ("files", 1),
        ("gym_settings", 1),
    )
    for key, expected in pairs:
        stored = _int_field(row, key)
        if stored is None or stored > expected:
            return True
    audit = _int_field(row, "audit_rows")
    if audit is None or audit > AUDIT_ROWS_CAP:
        return True
    return False


def assess_isolation(
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
    *,
    cancelled: int,
) -> tuple[str, bool, bool]:
    """Return state status, exact match, and data_corruption.

    Seeded rows are the baseline. A partial HTTP/RLS run that still matches
    those counts is fail with data_corruption false. Extra rows or a changed
    protected field are corruption. An early stop that still matches the seed
    baseline is not-run.
    """

    if type(cancelled) is not int or cancelled < 0:
        raise IsolationSpecError("cancelled count is invalid")
    if not snapshots:
        return "not-run", False, False
    if len(snapshots) != 2:
        return "fail", False, True
    corruption = False
    matched = True
    for row in snapshots:
        gym = row.get("gym")
        if type(gym) is not int or gym not in (0, 1):
            return "fail", False, True
        if _protected_changed(row) or _stored_exceeds(row):
            corruption = True
            matched = False
        elif not _seed_match(row):
            matched = False
    if corruption:
        return "fail", False, True
    if not outcomes and matched:
        return "not-run", False, False
    if matched and cancelled == 0 and len(outcomes) == PLAN_OPERATIONS:
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
        if scenario in _POSITIVE:
            if item.get("status") not in rule.success:
                return False
        elif item.get("status") not in rule.rejection:
            return False
    return True


def _claims_ok(rows: object, expected: Sequence[Mapping[str, object]], *, covered: bool) -> bool:
    if not isinstance(rows, list) or len(rows) != len(expected):
        return False
    for want, row in zip(expected, rows, strict=True):
        if not isinstance(row, Mapping):
            return False
        if row.get("id") != want["id"] or row.get("covered") is not covered:
            return False
        if row.get("note") != want["note"]:
            return False
    return True


def judge(body: Mapping[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if body.get("kind") != KIND or body.get("profile") != PROFILE_NAME:
        reasons.append("profile")
    if body.get("project") != PROJECT or body.get("evidence_name") != EVIDENCE_NAME:
        reasons.append("project")
    if any(body.get(flag) is True for flag in (*PROOF_FLAGS, *LOCAL_ISOLATION_FLAGS)):
        reasons.append("proof")
    if body.get("tenant_isolation_proven") is not False:
        reasons.append("proof")
    if body.get("local_security_status") != "not-run":
        reasons.append("security_status")
    if body.get("local_identity_status") != "not-run":
        reasons.append("identity_status")
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
    elif reconciliation.get("transport") != "service-only":
        reasons.append("reconciliation")
    if not _claims_ok(body.get("coverage"), COVERAGE, covered=True):
        reasons.append("coverage")
    if not _claims_ok(body.get("uncovered"), UNCOVERED, covered=False):
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
        "local_isolation_status": "not-run",
        "local_security_status": "not-run",
        "local_identity_status": "not-run",
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
        "uncovered": [dict(item) for item in UNCOVERED],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "failure_stage": "",
        "failure_detail": "",
        "gate_reasons": [],
    }
    for flag in (*PROOF_FLAGS, *LOCAL_ISOLATION_FLAGS):
        body[flag] = False
    return body


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    if profile != PROFILE_NAME:
        raise IsolationSpecError("isolation profile is not the fixed profile")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise IsolationSpecError("failure stage is invalid")
    body = _base_fields()
    body["local_isolation_status"] = "fail"
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
    state, matched, corruption = assess_isolation(outcomes, snapshots, cancelled=cancelled)
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
        return body
    if state == "pass" and leaked == 0 and credentials == 0 and not corruption:
        body["local_isolation_status"] = "pass"
    else:
        body["local_isolation_status"] = "fail"
    status, reasons = judge(body)
    if state == "pass" and status == "pass":
        body["local_isolation_status"] = "pass"
    else:
        body["local_isolation_status"] = "fail"
        body["gate_reasons"] = reasons or [state]
    return body


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def evidence_has_identity_marker(encoded: str) -> bool:
    if type(encoded) is not str:
        return True
    if "@" in encoded:
        return True
    if "ISOGYM" in encoded or "owner-iso-" in encoded:
        return True
    return bool(re.search(_UUID_CANONICAL, encoded))


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in (*PROOF_FLAGS, *LOCAL_ISOLATION_FLAGS):
        if body.get(flag) is True:
            raise IsolationSpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("schema_version") not in (None, SCHEMA):
        raise IsolationSpecError("isolation evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["evidence_name"] = EVIDENCE_NAME
    if body.get("local_security_status") != "not-run":
        raise IsolationSpecError("increment 12 security status cannot be claimed here")
    if body.get("local_identity_status") != "not-run":
        raise IsolationSpecError("increment 13 identity status cannot be claimed here")
    if body.get("local_soak_status") != "not-run":
        raise IsolationSpecError("this profile cannot claim a soak")
    if body.get("tenant_isolation_proven") is not False:
        raise IsolationSpecError("tenant isolation cannot be claimed by this profile")
    if body.get("local_isolation_status") not in LOCAL_STATUSES:
        raise IsolationSpecError("local status must be pass, fail, or not-run")
    if body.get("local_isolation_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise IsolationSpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets) or evidence_has_identity_marker(encoded):
        raise IsolationSpecError("isolation evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise IsolationSpecError("isolation evidence contains secret material")
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
        "assert_isolation_route",
        "with transaction.atomic():",
        "set_gym_context(",
    ):
        if phrase not in command:
            errors.append(f"isolation command is missing {phrase}")
    if "add_argument" in command or "argparse" in command:
        errors.append("isolation command exposes arbitrary arguments")
    snapshot = command[command.index("def _snapshot") : command.index("def _scrape")]
    if snapshot.index("with transaction.atomic():") > snapshot.index("set_gym_context("):
        errors.append("isolation snapshot sets gym context outside a transaction")
    for phrase in (
        "Phase 8 is not complete",
        "isolation.json",
        "local_isolation_status",
        "tenant_isolation_proven",
        "not independent tenant-isolation proof",
        "not a penetration test",
        "formal security review",
        "production",
        "staging_isolation_smoke",
        "object_storage_key_guessing",
        "file_upload_scan_download",
        "file_bff_download_url",
        "receipt_csv_export",
        "raw_sql_mutation",
        "does not wait for ClamAV TCP",
        "44",
        "180",
        "caddy_metrics_observed",
        "unavailable",
    ):
        if phrase not in runbook:
            errors.append(f"isolation runbook is missing {phrase}")
    for phrase in (
        "tenant isolation is proven",
        "penetration test is proven",
        "formal security review is proven",
        "production security is proven",
        "tenant_isolation_proven=true",
    ):
        if phrase in runbook:
            errors.append(f"isolation runbook claims {phrase}")
    for phrase in (
        "isolation.json",
        "staging_isolation_characterization",
        "does not replace identity-security.json",
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
    "AUDIT_ROWS_CAP",
    "BFF_ROUTES",
    "COVERAGE",
    "DURATION_SECONDS",
    "EVIDENCE_NAME",
    "HTTP",
    "KIND",
    "LOCAL_ISOLATION_FLAGS",
    "MAX_RESPONSE_BYTES",
    "PLAN_OPERATIONS",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PUBLIC_ORIGIN",
    "RLS",
    "RULES",
    "RUNTIME",
    "SCHEMA",
    "SEED_COUNTS",
    "SETUP",
    "TIMEOUT_SECONDS",
    "UNCOVERED",
    "IsolationSpecError",
    "assemble",
    "assert_isolation_route",
    "assess_isolation",
    "build_plan",
    "checksum_for",
    "classify_outcome",
    "evidence_has_identity_marker",
    "failure_document",
    "judge",
    "lint_texts",
    "metrics_delta",
    "public_outcome",
    "require_int",
    "resolve_profile",
    "runtime_status",
    "seal_evidence",
    "valid_resource_id",
    "verify_checksum",
]
