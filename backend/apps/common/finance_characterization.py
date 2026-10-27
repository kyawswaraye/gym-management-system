"""Pure rules for the disposable staging finance characterization.

This module does not open sockets, import Django, or read credentials. Receipt
allocation is the published payment-approval effect. Cash close is the
published reconcile command. A local pass is not a production load test, soak,
capacity proof, SLO, or financial proof.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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

SCHEMA = "gym-staging-finance/v1"
PROJECT = "gym-staging-rehearsal"
EVIDENCE_NAME = "finance.json"
PROFILE_NAME = "receipt"
KIND = "local-disposable-finance-characterization"
PLANNED_OPENING = "0.00"
PLANNED_MOVEMENT = "1.00"
PLANNED_CLOSING = "1.00"
LOCAL_STATUSES = frozenset({"pass", "fail", "not-run"})
_SUCCESS_SCENARIOS = (
    "auth_login_owner",
    "auth_login_approver",
    "cash_open",
    "payment_create",
    "payment_submit",
    "payment_self_approve",
    "receipt_download",
    "cash_expected",
    "cash_close",
    "cash_close_replay",
)
_REJECTION_SCENARIOS = (
    "payment_approve",
    "payment_approve_replay",
    "cash_close_immutable",
    "cross_receipt_download",
    "cross_cash_close",
    "cross_payment_approve",
)
_COUNT_KEYS = (
    "receipts_issued",
    "payments_posted",
    "cash_sessions",
    "cash_closed",
    "difference_nonzero",
    "distinct_receipt_numbers",
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
_PATH = re.compile(
    r"\A(?:"
    r"/api/auth/(?:csrf|login)"
    r"|/api/operations/cash-sessions/open"
    r"|/api/operations/cash-sessions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
    r"|/api/operations/cash-sessions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/reconcile"
    r"|/api/operations/payments"
    r"|/api/operations/payments/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/submit"
    r"|/api/operations/payments/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/approve"
    r"|/api/operations/receipts/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}/download"
    r")\Z"
)
_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}\Z")
_KEY_KINDS = frozenset(
    {
        "cash-open",
        "payment",
        "submit",
        "self-approve",
        "approve",
        "close",
        "close-again",
        "cross-close",
        "cross-approve",
    }
)


class FinanceSpecError(ValueError):
    """A fixed finance profile or evidence document violated a local rule."""


@dataclass(frozen=True)
class ScenarioRule:
    success: frozenset[int]
    expected_rejection: frozenset[int]


@dataclass(frozen=True)
class Profile:
    name: str
    duration_seconds: int
    workers: int
    timeout_seconds: int

    def bounds(self) -> None:
        validate_bounds(
            duration_seconds=self.duration_seconds,
            workers=self.workers,
            operations=len(build_plan(self.name)),
            timeout_seconds=self.timeout_seconds,
        )


RULES: dict[str, ScenarioRule] = {
    "auth_login_owner": ScenarioRule(frozenset({200}), frozenset()),
    "auth_login_approver": ScenarioRule(frozenset({200}), frozenset()),
    "cash_open": ScenarioRule(frozenset({201}), frozenset()),
    "payment_create": ScenarioRule(frozenset({201}), frozenset()),
    "payment_submit": ScenarioRule(frozenset({200}), frozenset()),
    "payment_self_approve": ScenarioRule(frozenset({200}), frozenset()),
    "payment_approve": ScenarioRule(frozenset(), frozenset({409})),
    "payment_approve_replay": ScenarioRule(frozenset(), frozenset({409})),
    "receipt_download": ScenarioRule(frozenset({200}), frozenset()),
    "cash_expected": ScenarioRule(frozenset({200}), frozenset()),
    "cash_close": ScenarioRule(frozenset({200}), frozenset()),
    "cash_close_replay": ScenarioRule(frozenset({200}), frozenset()),
    "cash_close_immutable": ScenarioRule(frozenset(), frozenset({409})),
    "cross_receipt_download": ScenarioRule(frozenset(), frozenset({404})),
    "cross_cash_close": ScenarioRule(frozenset(), frozenset({404})),
    "cross_payment_approve": ScenarioRule(frozenset(), frozenset({404})),
}

PROFILES: dict[str, Profile] = {
    PROFILE_NAME: Profile(PROFILE_NAME, 180, 1, 8),
}

COVERAGE: tuple[dict[str, object], ...] = (
    {
        "id": "browser_login",
        "method": "POST",
        "route": "/api/auth/login",
        "transport": "http",
        "covered": True,
        "note": "Owner and manager browser logins. Tokens stay in memory.",
    },
    {
        "id": "cash_open",
        "method": "POST",
        "route": "/api/operations/cash-sessions/open",
        "transport": "http",
        "covered": True,
        "note": "One open till per gym through the BFF.",
    },
    {
        "id": "payment_draft_submit",
        "method": "POST",
        "route": "/api/operations/payments",
        "transport": "http",
        "covered": True,
        "note": "Other-purpose cash draft and submit. No membership charge.",
    },
    {
        "id": "payment_self_approve",
        "method": "POST",
        "route": "/api/operations/payments/{id}/approve",
        "transport": "http",
        "covered": True,
        "note": "The owner who recorded the payment approves it. HTTP 200 is expected.",
    },
    {
        "id": "receipt_allocation",
        "method": "POST",
        "route": "/api/operations/payments/{id}/approve",
        "transport": "http",
        "covered": True,
        "note": "The owner's approval allocates the receipt.",
    },
    {
        "id": "receipt_download",
        "method": "GET",
        "route": "/api/operations/receipts/{id}/download",
        "transport": "http",
        "covered": True,
        "note": "Browser receipt download. The HTML body is not stored.",
    },
    {
        "id": "cash_close",
        "method": "POST",
        "route": "/api/operations/cash-sessions/{id}/reconcile",
        "transport": "http",
        "covered": True,
        "note": "Reconcile is the close command. A second key expects HTTP 409.",
    },
    {
        "id": "staff_seed",
        "method": "",
        "route": "",
        "transport": "service-only",
        "covered": True,
        "note": "Gym, owner, and manager acceptance have no browser step in this profile.",
    },
    {
        "id": "orm_reconciliation",
        "method": "",
        "route": "",
        "transport": "service-only",
        "covered": True,
        "note": "Posted payment, issued receipt, sequence, and closed-till checks use the ORM.",
    },
    {
        "id": "pt_sale",
        "method": "POST",
        "route": "/api/v1/pt-sales/",
        "transport": "uncovered",
        "covered": False,
        "note": "PT sale needs a trainer, package, and a separate approval chain. It is deferred.",
    },
    {
        "id": "pt_session",
        "method": "POST",
        "route": "/api/v1/pt-bookings/",
        "transport": "uncovered",
        "covered": False,
        "note": "No PT session ledger is seeded. A session is not called.",
    },
    {
        "id": "pt_payout",
        "method": "POST",
        "route": "/api/v1/pt-commission-payments/",
        "transport": "uncovered",
        "covered": False,
        "note": "Commission payout is deferred with the PT sale. It is not called.",
    },
    {
        "id": "pt_report",
        "method": "GET",
        "route": "/api/operations/reports/pt-sessions",
        "transport": "uncovered",
        "covered": False,
        "note": "An empty PT report would not characterize a payout. It is not called.",
    },
    {
        "id": "soak",
        "method": "",
        "route": "",
        "transport": "uncovered",
        "covered": False,
        "note": "No soak profile exists. Repeating a closed till would not be a production soak.",
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
        raise FinanceSpecError("duration exceeds the staging cap")
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise FinanceSpecError("worker count exceeds the staging cap")
    if type(operations) is not int or not 1 <= operations <= MAX_OPERATIONS:
        raise FinanceSpecError("operation count exceeds the staging cap")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise FinanceSpecError("timeout exceeds the staging cap")
    if workers != 1:
        raise FinanceSpecError("finance characterization is single-threaded")


def resolve_profile(name: str) -> Profile:
    profile = PROFILES.get(name)
    if profile is None:
        raise FinanceSpecError("finance profile is not one of the fixed profiles")
    if profile.name != PROFILE_NAME or profile.workers != 1:
        raise FinanceSpecError("finance profile is not one of the fixed profiles")
    profile.bounds()
    return profile


def build_plan(name: str) -> list[dict[str, object]]:
    profile = PROFILES.get(name)
    if profile is None or profile.workers != 1:
        raise FinanceSpecError("finance profile is not one of the fixed profiles")
    steps: list[dict[str, object]] = []
    for gym in (0, 1):
        for scenario in (
            "auth_login_owner",
            "auth_login_approver",
            "cash_open",
            "payment_create",
            "payment_submit",
            "payment_self_approve",
            "payment_approve",
            "payment_approve_replay",
            "receipt_download",
            "cash_expected",
            "cash_close",
            "cash_close_replay",
            "cash_close_immutable",
        ):
            steps.append(_step(scenario, gym, phase="fixed"))
    for gym in (0, 1):
        for scenario in (
            "cross_receipt_download",
            "cross_cash_close",
            "cross_payment_approve",
        ):
            steps.append(_step(scenario, gym, phase="cross"))
    if len(steps) > MAX_OPERATIONS:
        raise FinanceSpecError("operation count exceeds the staging cap")
    return steps


def _step(scenario: str, gym: int, *, phase: str) -> dict[str, object]:
    if scenario not in RULES or phase not in {"fixed", "cross"}:
        raise FinanceSpecError("scenario is not in the fixed catalog")
    return {"scenario": scenario, "gym": gym, "required": True, "phase": phase}


def make_key(kind: str, gym: int, ordinal: int) -> str:
    if kind not in _KEY_KINDS:
        raise FinanceSpecError("idempotency key kind is not fixed")
    if gym not in (0, 1) or type(ordinal) is not int or ordinal < 0 or ordinal > 100_000:
        raise FinanceSpecError("idempotency key ordinal is outside the profile")
    key = f"fin-{kind}-g{gym}-{ordinal:04d}"
    if _KEY.fullmatch(key) is None:
        raise FinanceSpecError("idempotency key is invalid")
    return key


def assert_finance_path(path: str) -> None:
    if not isinstance(path, str) or _PATH.fullmatch(path) is None:
        raise FinanceSpecError("finance path is not in the fixed catalog")


def classify_status(scenario: str, status: object) -> str:
    """Return success, expected_rejection, or unexpected.

    HTTP 5xx and transport failures are never expected.
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


def scenarios_match(
    plan: Sequence[Mapping[str, object]], outcomes: Sequence[Mapping[str, object]]
) -> bool:
    if len(plan) != len(outcomes):
        return False

    def key(item: Mapping[str, object]) -> tuple[str, str]:
        return (str(item.get("phase", "")), str(item.get("scenario", "")))

    return Counter(key(step) for step in plan) == Counter(key(item) for item in outcomes)


def closing_matches(observed: str) -> bool:
    return observed == PLANNED_CLOSING


def records_match(snapshot: Mapping[str, object]) -> bool:
    """Return whether one gym's stored finance rows match this profile exactly."""

    expected = {
        "receipt_count": 1,
        "receipt_status": "ISSUED",
        "payment_count": 1,
        "payment_status": "POSTED",
        "payment_method": "CASH",
        "payment_amount": PLANNED_MOVEMENT,
        "session_count": 1,
        "session_status": "CLOSED",
        "opening_cash": PLANNED_OPENING,
        "cash_sales": PLANNED_MOVEMENT,
        "expected_closing": PLANNED_CLOSING,
        "actual_closing": PLANNED_CLOSING,
        "difference": "0.00",
        "closed": "yes",
        "receipt_number_present": "yes",
    }
    return all(snapshot.get(key) == value for key, value in expected.items())


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


def assess_finance(
    plan: Sequence[Mapping[str, object]],
    outcomes: Sequence[Mapping[str, object]],
    observed: Mapping[str, int] | None,
) -> tuple[str, bool, bool]:
    """Return local status, matched, and data_corruption.

    An early stop with empty deltas is not-run. data_corruption is a measured
    disagreement, not incomplete execution.
    """

    if observed is None:
        return "not-run", False, False
    expected = {
        "receipts_issued": _planned(plan, "payment_self_approve"),
        "payments_posted": _planned(plan, "payment_self_approve"),
        "cash_sessions": _planned(plan, "cash_open"),
        "cash_closed": _planned(plan, "cash_close"),
        "difference_nonzero": 0,
        "distinct_receipt_numbers": _planned(plan, "payment_self_approve"),
        "gym_mismatch": 0,
    }
    http_complete = all(
        _count(outcomes, name, "success") == _planned(plan, name) for name in _SUCCESS_SCENARIOS
    ) and all(
        _count(outcomes, name, "expected_rejection") == _planned(plan, name)
        for name in _REJECTION_SCENARIOS
    )
    mismatch = any(observed.get(key) != value for key, value in expected.items())
    http_activity = (
        _count(outcomes, "payment_self_approve", "success")
        + _count(outcomes, "payment_approve", "success")
        + _count(outcomes, "cash_close", "success")
    )
    vacuous = http_activity == 0 and all(
        observed.get(key, 0) == 0 for key in (*_COUNT_KEYS, "gym_mismatch")
    )
    exercised = expected["receipts_issued"] >= 1 and expected["cash_closed"] >= 1
    if http_complete and exercised and not mismatch:
        return "pass", True, False
    if not http_complete and vacuous:
        return "not-run", False, False
    return "fail", False, mismatch and not vacuous


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
    if report.get("local_finance_status") != "pass":
        reasons.append("finance")
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
        if delta != 0:
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
    if profile not in PROFILES:
        raise FinanceSpecError("finance profile is not one of the fixed profiles")
    if not stage or len(stage) > 80 or any(char.isspace() and char != " " for char in stage):
        raise FinanceSpecError("failure stage is invalid")
    return {
        "kind": KIND,
        "profile": profile,
        "local_finance_status": "fail",
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
        "reconciliation": {"transport": "service-only", "matched": False},
        "evidence_name": EVIDENCE_NAME,
    }


def _canonical(body: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in body.items() if key != "checksum"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def checksum_for(body: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def seal_evidence(body: dict[str, Any], secrets: Sequence[str]) -> dict[str, Any]:
    for flag in PROOF_FLAGS:
        if body.get(flag) is True:
            raise FinanceSpecError("live proof flag cannot be set")
        body[flag] = False
    if body.get("thresholds_are_approved_slos") is not False:
        raise FinanceSpecError("provisional thresholds were described as approved SLOs")
    if body.get("local_soak_status") != "not-run":
        raise FinanceSpecError("local soak cannot be claimed by this profile")
    if body.get("schema_version") not in (None, SCHEMA):
        raise FinanceSpecError("finance evidence schema is not the staging schema")
    body["schema_version"] = SCHEMA
    body["project"] = PROJECT
    body["provisional_thresholds"] = True
    body["approved_slo"] = False
    body["evidence_name"] = EVIDENCE_NAME
    for key in (
        "local_finance_status",
        "local_isolation_status",
        "local_financial_reconciliation_status",
    ):
        if body.get(key) not in LOCAL_STATUSES:
            raise FinanceSpecError("local status must be pass, fail, or not-run")
    if body.get("local_finance_status") == "pass":
        status, reasons = judge(body)
        if status != "pass":
            raise FinanceSpecError("local pass disagrees with the gates: " + ",".join(reasons))
    encoded = _canonical(body)
    if material_leaked(encoded, secrets):
        raise FinanceSpecError("finance evidence contains secret material")
    if "BEGIN " in encoded and "KEY" in encoded:
        raise FinanceSpecError("finance evidence contains secret material")
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
            errors.append(f"finance command is missing {phrase}")
    for phrase in (
        "Phase 8 is not complete",
        "finance.json",
        "load.json",
        "receipt allocation",
        "cash close",
        "service-only",
        "uncovered",
        "PT sale",
        "PT payout",
        "soak",
        "not an approved SLO",
        "caddy_metrics_observed",
        "unavailable",
        "local_finance_status",
        "local_soak_status",
        "financial_reconciliation_proven",
    ):
        if phrase not in runbook:
            errors.append(f"finance runbook is missing {phrase}")
    for phrase in (
        "soak test is proven",
        "load test is proven",
        "production capacity is proven",
        "SLO is proven",
        "financial reconciliation is proven",
    ):
        if phrase in runbook:
            errors.append(f"finance runbook claims {phrase}")
    for phrase in (
        "finance.json",
        "staging_finance_characterization",
        "does not replace load.json",
    ):
        if phrase not in harness:
            errors.append(f"staging harness is missing {phrase}")
    return errors


__all__ = [
    "CONNECTION_CEILING",
    "COVERAGE",
    "EVIDENCE_NAME",
    "MAX_CONNECTIONS",
    "MAX_RESPONSE_BYTES",
    "PLANNED_CLOSING",
    "PROFILE_NAME",
    "PROOF_FLAGS",
    "PROVISIONAL_P95_MS",
    "SCHEMA",
    "FinanceSpecError",
    "assert_finance_path",
    "assert_rehearsal_context",
    "assess_finance",
    "build_plan",
    "classify_status",
    "closing_matches",
    "failure_document",
    "judge",
    "lint_texts",
    "make_key",
    "public_outcome",
    "records_match",
    "resolve_profile",
    "scenarios_match",
    "seal_evidence",
    "summarize_scenario",
    "verify_checksum",
]
