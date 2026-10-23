"""Offline rules for the disposable receipt and cash-close characterization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.common.finance_characterization import (
    PLANNED_CLOSING,
    RULES,
    FinanceSpecError,
    assert_finance_path,
    assess_finance,
    build_plan,
    classify_status,
    closing_matches,
    failure_document,
    judge,
    lint_texts,
    make_key,
    public_outcome,
    records_match,
    resolve_profile,
    scenarios_match,
    seal_evidence,
    validate_bounds,
    verify_checksum,
)
from apps.common.load_characterization import MAX_DURATION_SECONDS, clip_body

ROOT = Path(__file__).resolve().parents[3]


def _outcomes(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for step in plan:
        scenario = str(step["scenario"])
        rule = RULES[scenario]
        status = next(iter(rule.success or rule.expected_rejection))
        rows.append(
            {
                "scenario": scenario,
                "phase": step["phase"],
                "transport": "http",
                "status": status,
                "classification": classify_status(scenario, status),
                "latency_ms": 12,
                "leak": False,
                "credential": False,
            }
        )
    return rows


def _observed() -> dict[str, int]:
    return {
        "receipts_issued": 2,
        "payments_posted": 2,
        "cash_sessions": 2,
        "cash_closed": 2,
        "difference_nonzero": 0,
        "distinct_receipt_numbers": 2,
        "gym_mismatch": 0,
    }


def _zeros() -> dict[str, int]:
    return {key: 0 for key in _observed()}


def _passing() -> dict[str, object]:
    plan = build_plan("receipt")
    report = failure_document("receipt", "http")
    report.update(
        {
            "local_finance_status": "pass",
            "local_isolation_status": "pass",
            "local_financial_reconciliation_status": "pass",
            "local_soak_status": "not-run",
            "cross_gym_leakage": 0,
            "credential_exposure": 0,
            "unexpected_errors": 0,
            "api_unexpected_5xx": 0,
            "caddy_metrics_observed": False,
            "caddy_5xx_delta": None,
            "reconciliation_matched": True,
            "data_corruption": False,
            "postgres_connection_peak": 4,
            "postgres_max_connections": 40,
            "cancelled_operations": 0,
            "required_complete": True,
            "http_p95_ms": 12,
            "failure_stage": "",
        }
    )
    status, reasons = judge(report)
    assert status == "pass", reasons
    assert scenarios_match(plan, _outcomes(plan))
    return report


def test_caps_reject_bools_and_unknown_profiles() -> None:
    with pytest.raises(FinanceSpecError, match="duration"):
        validate_bounds(duration_seconds=True, workers=1, operations=1, timeout_seconds=1)  # type: ignore[arg-type]
    with pytest.raises(FinanceSpecError, match="duration"):
        validate_bounds(
            duration_seconds=MAX_DURATION_SECONDS + 1,
            workers=1,
            operations=1,
            timeout_seconds=1,
        )
    with pytest.raises(FinanceSpecError, match="single-threaded"):
        validate_bounds(duration_seconds=30, workers=2, operations=1, timeout_seconds=1)
    with pytest.raises(FinanceSpecError, match="profile"):
        resolve_profile("soak")
    profile = resolve_profile("receipt")
    assert profile.workers == 1
    assert profile.duration_seconds <= MAX_DURATION_SECONDS


def test_plan_order_and_same_count_substitution() -> None:
    plan = build_plan("receipt")
    assert plan == build_plan("receipt")

    def index(scenario: str, gym: int) -> int:
        return next(
            position
            for position, step in enumerate(plan)
            if step["scenario"] == scenario and step["gym"] == gym
        )

    for gym in (0, 1):
        assert index("payment_self_approve", gym) < index("payment_approve", gym)
        assert index("payment_approve", gym) < index("receipt_download", gym)
        assert index("receipt_download", gym) < index("cash_close", gym)
        assert index("cash_close", gym) < index("cash_close_immutable", gym)
    last_close = max(index("cash_close", gym) for gym in (0, 1))
    first_cross = min(position for position, step in enumerate(plan) if step["phase"] == "cross")
    assert last_close < first_cross
    outcomes = _outcomes(plan)
    assert scenarios_match(plan, outcomes)
    swapped = [dict(item) for item in outcomes]
    swapped[0]["scenario"] = "cash_close"
    assert len(swapped) == len(plan)
    assert scenarios_match(plan, swapped) is False


def test_paths_statuses_and_clipping() -> None:
    assert_finance_path("/api/auth/login")
    assert_finance_path("/api/operations/payments")
    assert_finance_path("/api/operations/receipts/11111111-1111-1111-1111-111111111111/download")
    for path in (
        "/api/operations/payments/",
        "/api/operations/pt-sales",
        "/api/v1/pt-sales/",
        "/api/operations/payments?token=secret",
        "https://example.com/api/auth/login",
        "//localhost/api/auth/login",
    ):
        with pytest.raises(FinanceSpecError, match="path"):
            assert_finance_path(path)
    assert classify_status("payment_self_approve", 403) == "expected_rejection"
    assert classify_status("cash_close_immutable", 409) == "expected_rejection"
    assert classify_status("cross_receipt_download", 404) == "expected_rejection"
    assert classify_status("payment_approve", 500) == "unexpected"
    assert classify_status("receipt_download", "timeout") == "unexpected"
    clipped, truncated = clip_body(b"x" * 20_000)
    assert truncated is True
    assert len(clipped) < 20_000
    hidden = public_outcome(
        {
            "scenario": "receipt_download",
            "phase": "fixed",
            "transport": "http",
            "status": 200,
            "classification": "success",
            "latency_ms": 1,
            "leak": False,
            "credential": False,
            "body": b"Internal receipt secret",
            "cookie": "gms_access=secret",
        }
    )
    assert "body" not in hidden
    assert "cookie" not in hidden
    assert closing_matches(PLANNED_CLOSING)
    assert closing_matches("0.00") is False
    with pytest.raises(FinanceSpecError, match="kind"):
        make_key("void", 0, 1)


def test_reconciliation_is_non_vacuous() -> None:
    plan = build_plan("receipt")
    status, matched, corruption = assess_finance(plan, [], _zeros())
    assert (status, matched, corruption) == ("not-run", False, False)
    status, matched, corruption = assess_finance(plan, [], None)
    assert (status, matched, corruption) == ("not-run", False, False)
    one = [
        {
            "scenario": "payment_approve",
            "phase": "fixed",
            "classification": "success",
        }
    ]
    status, matched, corruption = assess_finance(plan, one, _zeros())
    assert status == "fail"
    assert matched is False
    assert corruption is True
    status, matched, corruption = assess_finance(plan, _outcomes(plan), _observed())
    assert (status, matched, corruption) == ("pass", True, False)
    status, matched, corruption = assess_finance(plan, _outcomes(plan), _zeros())
    assert status == "fail"
    assert corruption is True
    assert records_match(
        {
            "receipt_count": 1,
            "receipt_status": "ISSUED",
            "payment_count": 1,
            "payment_status": "POSTED",
            "payment_method": "CASH",
            "payment_amount": "1.00",
            "session_count": 1,
            "session_status": "CLOSED",
            "opening_cash": "0.00",
            "cash_sales": "1.00",
            "expected_closing": "1.00",
            "actual_closing": "1.00",
            "difference": "0.00",
            "closed": "yes",
            "receipt_number_present": "yes",
        }
    )
    assert records_match({"session_status": "OPEN", "difference": "1.00"}) is False


def test_evidence_refuses_proof_soak_caddy_zero_and_secrets() -> None:
    sealed = seal_evidence(_passing(), [])
    assert sealed["load_test_proven"] is False
    assert sealed["soak_test_proven"] is False
    assert sealed["financial_reconciliation_proven"] is False
    assert sealed["evidence_name"] == "finance.json"
    assert verify_checksum(sealed) is True
    claimed = _passing()
    claimed["soak_test_proven"] = True
    with pytest.raises(FinanceSpecError, match="proof"):
        seal_evidence(claimed, [])
    soaked = _passing()
    soaked["local_soak_status"] = "pass"
    with pytest.raises(FinanceSpecError, match="soak"):
        seal_evidence(soaked, [])
    zero = _passing()
    zero["caddy_5xx_delta"] = 0
    status, reasons = judge(zero)
    assert status == "fail"
    assert "caddy_false_zero" in reasons
    measured = _passing()
    measured["caddy_metrics_observed"] = True
    measured["caddy_5xx_delta"] = 2
    status, reasons = judge(measured)
    assert "caddy_5xx" in reasons
    leaked = _passing()
    leaked["failure_detail"] = "owner fin-owner-a@rehearsal.invalid"
    with pytest.raises(FinanceSpecError, match="secret"):
        seal_evidence(leaked, ["fin-owner-a@rehearsal.invalid"])
    unavailable = failure_document("receipt", "rehearsal")
    assert unavailable["caddy_metrics_observed"] is False
    assert unavailable["caddy_5xx_delta"] is None
    assert unavailable["local_soak_status"] == "not-run"


def test_repository_texts_and_separate_evidence_name() -> None:
    command = (
        ROOT / "backend/apps/tenants/management/commands/staging_finance_characterization.py"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/phase8-increment-8.md").read_text(encoding="utf-8")
    harness = (ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8")
    assert lint_texts(command=command, runbook=runbook, harness=harness) == []
    assert "finance.json" in harness
    assert "does not replace load.json" in harness
    encoded = json.dumps({"route": "/api/operations/pt-sales/"})
    assert "pt-sales" in encoded
