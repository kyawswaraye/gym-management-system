"""Pure rules for the disposable cash commission-payout characterization.

This module does not open sockets, import Django, or read credentials. The
measured path opens one till, pays one accrued PT commission in cash, reads
the expected closing, and reconciles that till. A local pass is not a
production load test, soak, capacity proof, SLO, or financial proof.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from apps.common.load_characterization import (
    CONNECTION_CEILING,
    MAX_CONNECTIONS,
    MAX_DURATION_SECONDS,
    MAX_OPERATIONS,
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    MAX_WORKERS,
    PROOF_FLAGS,
    PROVISIONAL_P95_MS,
    assert_rehearsal_context,
    connection_within_ceiling,
    material_leaked,
    percentile_ms,
)
from apps.common.pt_characterization import same_gym_link_mismatches

SCHEMA = "gym-staging-pt-cash/v1"
PROJECT = "gym-staging-rehearsal"
EVIDENCE_NAME = "pt-cash.json"
PROFILE_NAME = "pt-cash"
KIND = "local-disposable-pt-cash-characterization"
GROSS_FEE = "100.00"
COMMISSION_AMOUNT = "10.00"
OPENING_CASH = "100.00"
EXPECTED_CLOSING = "90.00"
DIFFERENCE = "0.00"
REGISTER_CODE = "PTCASH"
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
_KEY = re.compile(r"\A[A-Za-z0-9._:-]{16,128}\Z")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_MONEY = re.compile(r"\A\d+\.\d{2}\Z")
_SALE = re.compile(rf"\A/api/operations/pt-sales(?:/{_UUID}(?:/(?:submit|approve))?)?\Z")
_BOOK = re.compile(rf"\A/api/operations/pt-bookings(?:/{_UUID}/attend)?\Z")
_PAY = re.compile(rf"\A/api/operations/pt-commissions/{_UUID}/record-payment\Z")
_CASH_OPEN = "/api/operations/cash-sessions/open"
_CASH_READ = re.compile(rf"\A/api/operations/cash-sessions/{_UUID}\Z")
_CASH_CLOSE = re.compile(rf"\A/api/operations/cash-sessions/{_UUID}/reconcile\Z")
_REPORT = re.compile(
    r"\A/api/operations/reports/pt-sessions\?date_from=(\d{4}-\d{2}-\d{2})&date_to=\1\Z"
)
_STATIC_PATHS = frozenset({"/api/auth/csrf", "/api/auth/login"})
_KINDS = frozenset(
    {
        "cashopen",
        "sale",
        "submit",
        "self",
        "approve",
        "book",
        "attend",
        "payself",
        "payout",
        "close",
        "close2",
        "xsale",
        "xattend",
        "xpayout",
        "xread",
        "xclose",
    }
)
_SUCCESS = (
    "auth_login_owner",
    "auth_login_approver",
    "cash_open",
    "pt_sale_create",
    "pt_sale_submit",
    "pt_sale_approve",
    "pt_sale_approve_replay",
    "pt_booking_create",
    "pt_booking_attend",
    "pt_cash_payout",
    "pt_cash_payout_replay",
    "cash_expected",
    "cash_close",
    "cash_close_replay",
    "pt_report",
)
_REJECTION = (
    "pt_sale_self_approve",
    "pt_payout_self",
    "cash_close_immutable",
    "cross_pt_sale",
    "cross_pt_attend",
    "cross_pt_payout",
    "cross_cash_read",
    "cross_cash_close",
)
_FIXED = (
    ("auth_login_owner", "POST", (200,)),
    ("auth_login_approver", "POST", (200,)),
    ("cash_open", "POST", (201,)),
    ("pt_sale_create", "POST", (201,)),
    ("pt_sale_submit", "POST", (200,)),
    ("pt_sale_self_approve", "POST", (403,)),
    ("pt_sale_approve", "POST", (200,)),
    ("pt_sale_approve_replay", "POST", (200,)),
    ("pt_booking_create", "POST", (201,)),
    ("pt_booking_attend", "POST", (200,)),
    ("pt_payout_self", "POST", (403,)),
    ("pt_cash_payout", "POST", (200,)),
    ("pt_cash_payout_replay", "POST", (200,)),
    ("cash_expected", "GET", (200,)),
    ("cash_close", "POST", (200,)),
    ("cash_close_replay", "POST", (200,)),
    ("cash_close_immutable", "POST", (409,)),
    ("pt_report", "GET", (200,)),
)
_CROSS = (
    ("cross_pt_sale", "POST", (404,)),
    ("cross_pt_attend", "POST", (404,)),
    ("cross_pt_payout", "POST", (404,)),
    ("cross_cash_read", "GET", (404,)),
    ("cross_cash_close", "POST", (404,)),
)
_RECORD_KEYS = (
    "sales_posted",
    "sale_gross",
    "commissions_paid",
    "commission_paid",
    "payouts_posted",
    "payout_method",
    "cash_out_count",
    "cash_out_amount",
    "cash_sessions_closed",
    "opening_cash",
    "cash_sales",
    "cash_expenses",
    "manual_net",
    "expected_closing",
    "actual_closing",
    "difference",
    "bookings_attended",
    "ledger_attended",
    "grants",
    "granted_sessions",
    "expenses_posted",
    "gym_mismatch",
)
_OUTCOME_KEYS = (
    "scenario",
    "phase",
    "transport",
    "status",
    "classification",
    "latency_ms",
    "leak",
    "credential",
)
RULES = {name: expected for name, _method, expected in (*_FIXED, *_CROSS)}
COVERAGE = (
    {
        "name": "cash_open",
        "transport": "http",
        "note": "The owner opens one till before the cash commission payout.",
    },
    {
        "name": "pt_sale",
        "transport": "http",
        "note": "Create, submit, reject creator approval, approve, and replay one package sale.",
    },
    {
        "name": "pt_session",
        "transport": "http",
        "note": "Book the granted session and mark it attended.",
    },
    {
        "name": "pt_cash_payout",
        "transport": "http",
        "note": "The commission creator is rejected. The owner pays CASH from the open till.",
    },
    {
        "name": "cash_close",
        "transport": "http",
        "note": "Read the expected closing, reconcile once, replay, and reject a second close.",
    },
    {
        "name": "pt_report",
        "transport": "http",
        "note": "Read the PT session report for the gym-local sale date.",
    },
    {
        "name": "trainer_member_package_assignment",
        "transport": "service-only",
        "note": "Gym, owner, manager, trainer, member, package, and assignment setup.",
    },
    {
        "name": "reconciliation",
        "transport": "service-only",
        "note": (
            "ORM counts and same-gym links for sale, grant, booking, commission, "
            "cash payout, expense, and till."
        ),
    },
    {
        "name": "soak",
        "transport": "uncovered",
        "note": "No soak profile is defined.",
    },
)


class PtCashSpecError(ValueError):
    """The cash payout characterization request is outside the fixed profile."""


@dataclass(frozen=True)
class Profile:
    name: str
    workers: int
    duration_seconds: int
    timeout_seconds: float
    operations: int

    def validate(self) -> None:
        if self.name != PROFILE_NAME:
            raise PtCashSpecError("pt-cash profile is not one of the fixed profiles")
        if type(self.workers) is not int or self.workers != 1 or self.workers > MAX_WORKERS:
            raise PtCashSpecError("pt-cash worker count exceeds the fixed cap")
        if (
            type(self.duration_seconds) is not int
            or self.duration_seconds <= 0
            or self.duration_seconds > MAX_DURATION_SECONDS
        ):
            raise PtCashSpecError("pt-cash duration exceeds the fixed cap")
        timeout = self.timeout_seconds
        if type(timeout) is not float or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
            raise PtCashSpecError("pt-cash timeout exceeds the fixed cap")
        planned = len(build_plan())
        operations = self.operations
        if type(operations) is not int or operations != planned or operations > MAX_OPERATIONS:
            raise PtCashSpecError("pt-cash operation count exceeds the fixed cap")


def closing_after_cash_payout(opening: str, cash_expenses: str) -> str:
    """Return opening minus cash expenses as a two-decimal string."""

    if type(opening) is not str or type(cash_expenses) is not str:
        raise PtCashSpecError("cash amounts must be decimal strings")
    if _MONEY.fullmatch(opening) is None or _MONEY.fullmatch(cash_expenses) is None:
        raise PtCashSpecError("cash amounts must be decimal strings")
    value = (Decimal(opening) - Decimal(cash_expenses)).quantize(Decimal("0.01"))
    if value < 0:
        raise PtCashSpecError("cash closing cannot be negative for this profile")
    return f"{value:.2f}"


def build_plan() -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for gym in (0, 1):
        for scenario, method, expected in _FIXED:
            rows.append(_step("fixed", gym, scenario, method, expected))
    for gym in (0, 1):
        for scenario, method, expected in _CROSS:
            rows.append(_step("cross", gym, scenario, method, expected))
    return tuple(rows)


def _step(
    phase: str,
    gym: int,
    scenario: str,
    method: str,
    expected: tuple[int, ...],
) -> dict[str, object]:
    return {
        "phase": phase,
        "gym": gym,
        "scenario": scenario,
        "method": method,
        "expected": expected,
    }


def resolve_profile(name: str) -> Profile:
    if name != PROFILE_NAME:
        raise PtCashSpecError("pt-cash profile is not one of the fixed profiles")
    profile = Profile(
        name=PROFILE_NAME,
        workers=1,
        duration_seconds=180,
        timeout_seconds=8.0,
        operations=len(build_plan()),
    )
    profile.validate()
    return profile


def assert_pt_cash_path(path: str) -> None:
    if not path.startswith("/") or path.endswith("/") or "//" in path:
        raise PtCashSpecError("pt-cash path is outside the fixed allowlist")
    if path.startswith("/api/auth/") and path not in _STATIC_PATHS:
        raise PtCashSpecError("pt-cash path is outside the fixed allowlist")
    if "?" in path and _REPORT.fullmatch(path) is None:
        raise PtCashSpecError("pt-cash path is outside the fixed allowlist")
    if path in _STATIC_PATHS or path == _CASH_OPEN or _REPORT.fullmatch(path):
        return
    if (
        _SALE.fullmatch(path)
        or _BOOK.fullmatch(path)
        or _PAY.fullmatch(path)
        or _CASH_READ.fullmatch(path)
        or _CASH_CLOSE.fullmatch(path)
    ):
        return
    raise PtCashSpecError("pt-cash path is outside the fixed allowlist")


def make_key(kind: str, gym: int, ordinal: int) -> str:
    if (
        kind not in _KINDS
        or type(gym) is not int
        or gym not in (0, 1)
        or type(ordinal) is not int
        or ordinal < 0
        or ordinal > 9999
    ):
        raise PtCashSpecError("pt-cash idempotency key is outside the fixed allowlist")
    key = f"ptc-{kind}-g{gym}-{ordinal:04d}"
    if _KEY.fullmatch(key) is None:
        raise PtCashSpecError("pt-cash idempotency key is outside the fixed allowlist")
    return key


def classify_status(scenario: str, status: object) -> str:
    expected = RULES.get(scenario)
    if expected is None or type(status) is not int or status >= 500:
        return "unexpected"
    if status not in expected:
        return "unexpected"
    if status >= 400:
        return "expected_rejection"
    return "success"


def scenarios_match(
    plan: Sequence[Mapping[str, object]], outcomes: Sequence[Mapping[str, object]]
) -> bool:
    if len(plan) != len(outcomes):
        return False
    expected = [(str(step.get("phase")), str(step.get("scenario"))) for step in plan]
    actual = [(str(item.get("phase")), str(item.get("scenario"))) for item in outcomes]
    return actual == expected


def expected_record() -> dict[str, object]:
    closing = closing_after_cash_payout(OPENING_CASH, COMMISSION_AMOUNT)
    return {
        "sales_posted": 1,
        "sale_gross": GROSS_FEE,
        "commissions_paid": 1,
        "commission_paid": COMMISSION_AMOUNT,
        "payouts_posted": 1,
        "payout_method": "CASH",
        "cash_out_count": 1,
        "cash_out_amount": COMMISSION_AMOUNT,
        "cash_sessions_closed": 1,
        "opening_cash": OPENING_CASH,
        "cash_sales": "0.00",
        "cash_expenses": COMMISSION_AMOUNT,
        "manual_net": "0.00",
        "expected_closing": closing,
        "actual_closing": closing,
        "difference": DIFFERENCE,
        "bookings_attended": 1,
        "ledger_attended": 1,
        "grants": 1,
        "granted_sessions": 1,
        "expenses_posted": 1,
        "gym_mismatch": 0,
    }


def records_match(snapshot: Mapping[str, object]) -> bool:
    """Return whether one gym's stored cash-payout rows match this profile exactly."""

    return all(snapshot.get(key) == value for key, value in expected_record().items())


def public_outcome(outcome: Mapping[str, object]) -> dict[str, object]:
    """Drop response bodies, cookies, and any key that is not a metric."""

    return {key: outcome[key] for key in _OUTCOME_KEYS if key in outcome}


def _count(items: Sequence[Mapping[str, object]], scenario: str, label: str) -> int:
    return sum(
        1
        for item in items
        if item.get("scenario") == scenario and item.get("classification") == label
    )


def _planned(plan: Sequence[Mapping[str, object]], scenario: str) -> int:
    return sum(1 for step in plan if step.get("scenario") == scenario)


def _positive_mismatch(snapshot: Mapping[str, object]) -> bool:
    value = snapshot.get("gym_mismatch")
    return type(value) is int and value > 0


def _zero_snapshot(snapshot: Mapping[str, object]) -> bool:
    counts = {
        "sales_posted",
        "commissions_paid",
        "payouts_posted",
        "cash_out_count",
        "cash_sessions_closed",
        "bookings_attended",
        "ledger_attended",
        "grants",
        "granted_sessions",
        "expenses_posted",
        "gym_mismatch",
    }
    zeros: dict[str, object] = {}
    for key in _RECORD_KEYS:
        if key in counts:
            zeros[key] = 0
        elif key == "payout_method":
            zeros[key] = ""
        else:
            zeros[key] = "0.00"
    return all(snapshot.get(key) == value for key, value in zeros.items())


def assess_pt_cash(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    snapshots: Sequence[Mapping[str, object]] | None,
) -> tuple[str, bool, bool]:
    """Return local status, matched, and data_corruption.

    An early stop with empty deltas is not-run. An unfinished plan that has
    not broken a same-gym relation is fail with data_corruption false.
    data_corruption is true when the finished plan disagrees with the stored
    rows, or when gym_mismatch is already nonzero.
    """

    if snapshots is None:
        return "not-run", False, False
    http_complete = all(
        _count(outcomes, name, "success") == _planned(plan, name) for name in _SUCCESS
    ) and all(
        _count(outcomes, name, "expected_rejection") == _planned(plan, name) for name in _REJECTION
    )
    matched = len(snapshots) == 2 and all(records_match(row) for row in snapshots)
    activity = (
        _count(outcomes, "cash_open", "success")
        + _count(outcomes, "pt_cash_payout", "success")
        + _count(outcomes, "cash_close", "success")
    )
    vacuous = activity == 0 and all(_zero_snapshot(row) for row in snapshots)
    exercised = (
        _planned(plan, "cash_open") >= 1
        and _planned(plan, "pt_cash_payout") >= 1
        and _planned(plan, "cash_close") >= 1
    )
    relation_broken = any(_positive_mismatch(row) for row in snapshots)
    if http_complete and matched and exercised and not relation_broken:
        return "pass", True, False
    if not http_complete and vacuous and not relation_broken:
        return "not-run", False, False
    if not http_complete:
        return "fail", False, relation_broken
    return "fail", False, (not matched) or relation_broken


def summarize_scenario(
    scenario: str,
    outcomes: Sequence[Mapping[str, object]],
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
    return {
        "scenario": scenario,
        "transport": "http",
        "count": len(outcomes),
        "successes": successes,
        "expected_rejections": expected,
        "unexpected_errors": unexpected,
        "status": statuses,
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
    if report.get("local_pt_cash_status") != "pass":
        reasons.append("pt_cash")
    if report.get("local_soak_status") != "not-run":
        reasons.append("soak")
    if report.get("cross_gym_leakage") != 0:
        reasons.append("cross_gym_leakage")
    if report.get("credential_exposure") != 0:
        reasons.append("credential_exposure")
    if report.get("unexpected_errors") != 0:
        reasons.append("unexpected_errors")
    if report.get("api_unexpected_5xx") != 0:
        reasons.append("api_unexpected_5xx")
    observed = report.get("caddy_metrics_observed")
    delta = report.get("caddy_5xx_delta")
    if observed is True:
        if type(delta) is not int or delta != 0:
            reasons.append("caddy_5xx")
    elif observed is False:
        if delta is not None:
            reasons.append("caddy_false_zero")
    else:
        reasons.append("caddy_metrics")
    if report.get("reconciliation_matched") is not True:
        reasons.append("reconciliation")
    if report.get("data_corruption") is True:
        reasons.append("data_corruption")
    if not connection_within_ceiling(
        report.get("postgres_connection_peak"),
        report.get("postgres_max_connections"),
    ):
        reasons.append("connection_ceiling")
    if report.get("cancelled_operations") != 0:
        reasons.append("cancelled")
    if report.get("required_complete") is not True:
        reasons.append("required_steps")
    latency = report.get("http_p95_ms")
    if type(latency) is int and latency > PROVISIONAL_P95_MS:
        reasons.append("provisional_latency_ceiling")
    for flag in PROOF_FLAGS:
        if report.get(flag) is True:
            reasons.append("false_proof")
            break
    if report.get("thresholds_are_approved_slos") is not False:
        reasons.append("false_proof")
    status = "pass" if not reasons else "fail"
    return status, reasons


def failure_document(profile: str, stage: str) -> dict[str, Any]:
    if profile != PROFILE_NAME:
        raise PtCashSpecError("pt-cash profile is not one of the fixed profiles")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise PtCashSpecError("failure stage is invalid")
    body = {
        "kind": KIND,
        "profile": profile,
        "local_pt_cash_status": "fail",
        "local_isolation_status": "not-run",
        "local_financial_reconciliation_status": "not-run",
        "local_soak_status": "not-run",
        "thresholds_are_approved_slos": False,
        "failure_stage": stage,
        "cross_gym_leakage": 0,
        "credential_exposure": 0,
        "unexpected_errors": 0,
        "api_unexpected_5xx": 0,
        "caddy_metrics_observed": False,
        "caddy_5xx_delta": None,
        "reconciliation_matched": False,
        "data_corruption": False,
        "postgres_connection_peak": None,
        "postgres_max_connections": None,
        "cancelled_operations": 0,
        "required_complete": False,
        "http_p95_ms": None,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [],
        "reconciliation": {"transport": "service-only", "matched": False, "gyms": []},
        "evidence_name": EVIDENCE_NAME,
    }
    for flag in PROOF_FLAGS:
        body[flag] = False
    return body


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in PROOF_FLAGS:
        if body.get(flag) is True:
            raise PtCashSpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("thresholds_are_approved_slos") is not False:
        raise PtCashSpecError("provisional thresholds were described as approved SLOs")
    if body.get("local_soak_status") != "not-run":
        raise PtCashSpecError("local soak cannot be claimed by this profile")
    if body.get("schema_version") not in (None, SCHEMA):
        raise PtCashSpecError("pt-cash evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["provisional_thresholds"] = True
    body["approved_slo"] = False
    body["evidence_name"] = EVIDENCE_NAME
    for key in (
        "local_pt_cash_status",
        "local_isolation_status",
        "local_financial_reconciliation_status",
    ):
        if body.get(key) not in LOCAL_STATUSES:
            raise PtCashSpecError("local status must be pass, fail, or not-run")
    if body.get("local_pt_cash_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise PtCashSpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets):
        raise PtCashSpecError("pt-cash evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise PtCashSpecError("pt-cash evidence contains secret material")
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
        "soak_test_proven",
        "production_capacity_proven",
        "slo_proven",
        "financial_reconciliation_proven",
    ):
        if phrase not in command:
            errors.append(f"pt-cash command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "pt-cash.json",
        "pt.json",
        "load.json",
        "finance.json",
        "PT sale",
        "PT session",
        "CASH",
        "cash session",
        "PT report",
        "service-only",
        "uncovered",
        "soak",
        "not an approved SLO",
        "caddy_metrics_observed",
        "unavailable",
        "local_pt_cash_status",
        "local_soak_status",
        "financial_reconciliation_proven",
    ):
        if phrase not in runbook:
            errors.append(f"pt-cash runbook is missing {phrase}")
    for phrase in (
        "soak test is proven",
        "load test is proven",
        "production capacity is proven",
        "SLO is proven",
        "financial reconciliation is proven",
    ):
        if phrase in runbook:
            errors.append(f"pt-cash runbook claims {phrase}")
    for phrase in (
        "pt-cash.json",
        "staging_pt_cash_characterization",
        "does not replace pt.json",
        "does not replace load.json",
        "does not replace finance.json",
    ):
        if phrase not in harness:
            errors.append(f"staging harness is missing {phrase}")
    return errors


__all__ = [
    "COMMISSION_AMOUNT",
    "CONNECTION_CEILING",
    "COVERAGE",
    "DIFFERENCE",
    "EVIDENCE_NAME",
    "EXPECTED_CLOSING",
    "GROSS_FEE",
    "KIND",
    "MAX_CONNECTIONS",
    "MAX_RESPONSE_BYTES",
    "OPENING_CASH",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PROVISIONAL_P95_MS",
    "REGISTER_CODE",
    "RULES",
    "SCHEMA",
    "PtCashSpecError",
    "assert_pt_cash_path",
    "assert_rehearsal_context",
    "assess_pt_cash",
    "build_plan",
    "classify_status",
    "closing_after_cash_payout",
    "expected_record",
    "failure_document",
    "judge",
    "lint_texts",
    "make_key",
    "public_outcome",
    "records_match",
    "resolve_profile",
    "same_gym_link_mismatches",
    "scenarios_match",
    "seal_evidence",
    "summarize_scenario",
    "verify_checksum",
]
