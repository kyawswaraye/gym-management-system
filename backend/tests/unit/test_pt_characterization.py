"""Offline rules for the disposable PT characterization."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.common.load_characterization import MAX_DURATION_SECONDS, clip_body
from apps.common.pt_characterization import (
    COMMISSION_AMOUNT,
    GROSS_FEE,
    RULES,
    Profile,
    PtSpecError,
    assert_pt_path,
    assess_pt,
    build_plan,
    classify_status,
    expected_record,
    failure_document,
    judge,
    lint_texts,
    make_key,
    public_outcome,
    records_match,
    resolve_profile,
    same_gym_link_mismatches,
    scenarios_match,
    seal_evidence,
    verify_checksum,
)

ROOT = Path(__file__).resolve().parents[3]


def _outcomes(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for step in plan:
        scenario = str(step["scenario"])
        status = RULES[scenario][0]
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


def _zeros() -> dict[str, object]:
    return {
        "sales_posted": 0,
        "sale_gross": "0.00",
        "commissions_paid": 0,
        "commission_paid": "0.00",
        "payouts_posted": 0,
        "bookings_attended": 0,
        "ledger_attended": 0,
        "grants": 0,
        "granted_sessions": 0,
        "expenses_posted": 0,
        "gym_mismatch": 0,
    }


def _passing() -> dict[str, object]:
    report = failure_document("pt", "http")
    report.update(
        {
            "local_pt_status": "pass",
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
    return report


def test_caps_reject_unknown_profiles() -> None:
    with pytest.raises(PtSpecError, match="profile"):
        resolve_profile("soak")
    profile = resolve_profile("pt")
    assert profile.workers == 1
    assert profile.duration_seconds <= MAX_DURATION_SECONDS
    assert profile.operations == len(build_plan())


def test_plan_order_and_same_count_substitution() -> None:
    plan = list(build_plan())

    def index(scenario: str, gym: int) -> int:
        return next(
            position
            for position, step in enumerate(plan)
            if step["scenario"] == scenario and step["gym"] == gym
        )

    for gym in (0, 1):
        assert index("pt_sale_self_approve", gym) < index("pt_sale_approve", gym)
        assert index("pt_sale_approve", gym) < index("pt_booking_attend", gym)
        assert index("pt_booking_attend", gym) < index("pt_payout", gym)
        assert index("pt_payout", gym) < index("pt_report", gym)
    last_report = max(index("pt_report", gym) for gym in (0, 1))
    first_cross = min(position for position, step in enumerate(plan) if step["phase"] == "cross")
    assert last_report < first_cross
    outcomes = _outcomes(plan)
    assert scenarios_match(plan, outcomes)
    swapped = [dict(item) for item in outcomes]
    swapped[0]["scenario"] = "pt_payout"
    assert len(swapped) == len(plan)
    assert scenarios_match(plan, swapped) is False


def test_paths_statuses_and_redaction() -> None:
    sale = "/api/operations/pt-sales/11111111-1111-1111-1111-111111111111"
    assert_pt_path("/api/auth/login")
    assert_pt_path("/api/operations/pt-sales")
    assert_pt_path(f"{sale}/approve")
    assert_pt_path("/api/operations/pt-bookings/11111111-1111-1111-1111-111111111111/attend")
    assert_pt_path(
        "/api/operations/pt-commissions/11111111-1111-1111-1111-111111111111/record-payment"
    )
    assert_pt_path("/api/operations/reports/pt-sessions?date_from=2026-10-21&date_to=2026-10-21")
    malformed = "-" * 36
    upper = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee".upper()
    for path in (
        f"/api/operations/pt-sales/{malformed}",
        f"/api/operations/pt-sales/{malformed}/approve",
        f"/api/operations/pt-bookings/{malformed}/attend",
        f"/api/operations/pt-commissions/{malformed}/record-payment",
        f"/api/operations/pt-sales/{upper}/approve",
        f"/api/operations/pt-bookings/{upper}/attend",
        f"/api/operations/pt-commissions/{upper}/record-payment",
    ):
        with pytest.raises(PtSpecError):
            assert_pt_path(path)
    for path in (
        "/api/operations/pt-sales/",
        "/api/operations/pt-sales?member_id=1",
        "https://example.test/api/operations/pt-sales",
        "/api/operations/reports/pt-sessions?date_from=2026-10-21&date_to=2026-10-22",
        "/api/operations/cash-sessions/open",
    ):
        with pytest.raises(PtSpecError):
            assert_pt_path(path)
    assert classify_status("pt_sale_approve", 200) == "success"
    assert classify_status("pt_sale_self_approve", 403) == "expected_rejection"
    assert classify_status("cross_pt_payout", 404) == "expected_rejection"
    assert classify_status("pt_sale_approve", 500) == "unexpected"
    assert classify_status("pt_sale_approve", True) == "unexpected"
    assert make_key("sale", 0, 0) == "ptx-sale-g0-0000"
    with pytest.raises(PtSpecError):
        make_key("cash", 0, 0)
    with pytest.raises(PtSpecError):
        make_key("sale", True, 0)  # type: ignore[arg-type]
    with pytest.raises(PtSpecError):
        make_key("sale", 0, True)  # type: ignore[arg-type]
    planned = len(build_plan())
    with pytest.raises(PtSpecError):
        Profile(
            name="pt",
            workers=True,  # type: ignore[arg-type]
            duration_seconds=180,
            timeout_seconds=8.0,
            operations=planned,
        ).validate()
    with pytest.raises(PtSpecError):
        Profile(
            name="pt",
            workers=1,
            duration_seconds=True,  # type: ignore[arg-type]
            timeout_seconds=8.0,
            operations=planned,
        ).validate()
    with pytest.raises(PtSpecError):
        Profile(
            name="pt",
            workers=1,
            duration_seconds=180,
            timeout_seconds=True,  # type: ignore[arg-type]
            operations=planned,
        ).validate()
    clipped = clip_body(b"x" * 20000)
    assert len(clipped) <= 16384
    assert "body" not in public_outcome({"scenario": "pt_sale_create", "body": "secret"})


def test_reconciliation_is_exact_and_non_vacuous() -> None:
    plan = list(build_plan())
    outcomes = _outcomes(plan)
    snapshots = [expected_record(), expected_record()]
    status, matched, corruption = assess_pt(plan, outcomes, snapshots)
    assert (status, matched, corruption) == ("pass", True, False)
    assert records_match(expected_record())
    assert expected_record()["sale_gross"] == GROSS_FEE
    assert expected_record()["commission_paid"] == COMMISSION_AMOUNT
    status, matched, corruption = assess_pt(plan, [], [_zeros(), _zeros()])
    assert (status, matched, corruption) == ("not-run", False, False)
    partial = [item for item in outcomes if item["scenario"] == "pt_sale_approve"][:1]
    status, matched, corruption = assess_pt(plan, partial, [_zeros(), _zeros()])
    assert (status, matched, corruption) == ("fail", False, False)
    progressed = dict(_zeros())
    progressed["sales_posted"] = 1
    progressed["sale_gross"] = GROSS_FEE
    status, matched, corruption = assess_pt(plan, partial, [progressed, _zeros()])
    assert (status, matched, corruption) == ("fail", False, False)
    broken = dict(_zeros())
    broken["gym_mismatch"] = 1
    status, matched, corruption = assess_pt(plan, partial, [broken, _zeros()])
    assert (status, matched, corruption) == ("fail", False, True)
    wrong = expected_record()
    wrong["payouts_posted"] = 2
    status, matched, corruption = assess_pt(plan, outcomes, [wrong, expected_record()])
    assert status == "fail"
    assert matched is False
    assert corruption is True
    assert same_gym_link_mismatches([]) == 0
    assert same_gym_link_mismatches([("gym-a", "gym-a")]) == 0
    assert same_gym_link_mismatches([("gym-a", "gym-b"), ("gym-a", None)]) == 2


def test_seal_refuses_proof_soak_and_secrets() -> None:
    sealed = seal_evidence(_passing(), [])
    assert sealed["checksum"].startswith("sha256:")
    assert verify_checksum(sealed)
    assert sealed["local_soak_status"] == "not-run"
    assert sealed["financial_reconciliation_proven"] is False
    flagged = _passing()
    flagged["load_test_proven"] = True
    with pytest.raises(PtSpecError, match="proof"):
        seal_evidence(flagged, [])
    soaked = _passing()
    soaked["local_soak_status"] = "pass"
    with pytest.raises(PtSpecError, match="soak"):
        seal_evidence(soaked, [])
    leaked = _passing()
    leaked["failure_detail"] = "owner-a@rehearsal.invalid"
    with pytest.raises(PtSpecError, match="secret"):
        seal_evidence(leaked, ["owner-a@rehearsal.invalid"])


def test_caddy_null_is_not_zero() -> None:
    unavailable = _passing()
    status, reasons = judge(unavailable)
    assert status == "pass", reasons
    false_zero = _passing()
    false_zero["caddy_5xx_delta"] = 0
    status, reasons = judge(false_zero)
    assert "caddy_false_zero" in reasons
    measured = _passing()
    measured["caddy_metrics_observed"] = True
    measured["caddy_5xx_delta"] = 1
    status, reasons = judge(measured)
    assert "caddy_5xx" in reasons


def test_repository_lint_reads_the_pt_sources() -> None:
    errors = lint_texts(
        command=(
            ROOT / "backend/apps/tenants/management/commands/staging_pt_characterization.py"
        ).read_text(encoding="utf-8"),
        runbook=(ROOT / "docs/runbooks/phase8-increment-9.md").read_text(encoding="utf-8"),
        harness=(ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
    )
    assert errors == []
