"""Offline rules for the disposable cash commission payout characterization."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.common.pt_cash_characterization import (
    COMMISSION_AMOUNT,
    EXPECTED_CLOSING,
    OPENING_CASH,
    RULES,
    Profile,
    PtCashSpecError,
    assert_pt_cash_path,
    assess_pt_cash,
    build_plan,
    classify_status,
    closing_after_cash_payout,
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
    row = dict(expected_record())
    for key, value in row.items():
        if isinstance(value, int):
            row[key] = 0
        elif key == "payout_method":
            row[key] = ""
        else:
            row[key] = "0.00"
    return row


def _passing() -> dict[str, object]:
    report = failure_document("pt-cash", "http")
    report.update(
        {
            "local_pt_cash_status": "pass",
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


def test_profile_rejects_booleans_and_wrong_counts() -> None:
    profile = resolve_profile("pt-cash")
    assert profile.workers == 1
    assert profile.operations == len(build_plan())
    planned = profile.operations
    with pytest.raises(PtCashSpecError):
        Profile(
            name="pt-cash",
            workers=True,  # type: ignore[arg-type]
            duration_seconds=180,
            timeout_seconds=8.0,
            operations=planned,
        ).validate()
    with pytest.raises(PtCashSpecError):
        Profile(
            name="pt-cash",
            workers=1,
            duration_seconds=180,
            timeout_seconds=True,  # type: ignore[arg-type]
            operations=planned,
        ).validate()
    with pytest.raises(PtCashSpecError):
        make_key("payout", True, 0)  # type: ignore[arg-type]
    with pytest.raises(PtCashSpecError):
        make_key("payout", 0, True)  # type: ignore[arg-type]
    with pytest.raises(PtCashSpecError):
        closing_after_cash_payout(True, COMMISSION_AMOUNT)  # type: ignore[arg-type]


def test_paths_reject_malformed_ids() -> None:
    sale = "/api/operations/pt-sales/11111111-1111-1111-1111-111111111111"
    cash = "/api/operations/cash-sessions/11111111-1111-1111-1111-111111111111"
    assert_pt_cash_path("/api/auth/login")
    assert_pt_cash_path("/api/operations/cash-sessions/open")
    assert_pt_cash_path(sale + "/approve")
    assert_pt_cash_path(cash)
    assert_pt_cash_path(cash + "/reconcile")
    assert_pt_cash_path(
        "/api/operations/pt-commissions/11111111-1111-1111-1111-111111111111/record-payment"
    )
    malformed = "-" * 36
    upper = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee".upper()
    for path in (
        f"/api/operations/pt-sales/{malformed}/approve",
        f"/api/operations/pt-bookings/{malformed}/attend",
        f"/api/operations/pt-commissions/{malformed}/record-payment",
        f"/api/operations/cash-sessions/{malformed}",
        f"/api/operations/cash-sessions/{malformed}/reconcile",
        f"/api/operations/cash-sessions/{upper}",
        "/api/operations/cash-sessions/open/extra",
    ):
        with pytest.raises(PtCashSpecError):
            assert_pt_cash_path(path)


def test_plan_order_and_multiset() -> None:
    plan = list(build_plan())
    names = [str(step["scenario"]) for step in plan]
    fixed = names[: names.index("cross_pt_sale")]
    assert fixed.index("pt_sale_self_approve") < fixed.index("pt_sale_approve")
    assert fixed.index("cash_open") < fixed.index("pt_cash_payout")
    assert fixed.index("pt_payout_self") < fixed.index("pt_cash_payout")
    assert fixed.index("pt_cash_payout") < fixed.index("cash_expected")
    assert fixed.index("cash_expected") < fixed.index("cash_close")
    assert fixed.index("cash_close_replay") < fixed.index("cash_close_immutable")
    assert fixed.index("cash_close_immutable") < fixed.index("pt_report")
    assert names.index("pt_report") < names.index("cross_pt_sale")
    outcomes = _outcomes(plan)
    assert scenarios_match(plan, outcomes)
    swapped = list(outcomes)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert scenarios_match(plan, swapped) is False
    assert closing_after_cash_payout(OPENING_CASH, COMMISSION_AMOUNT) == EXPECTED_CLOSING
    assert expected_record()["expected_closing"] == EXPECTED_CLOSING
    assert expected_record()["difference"] == "0.00"
    assert expected_record()["payout_method"] == "CASH"


def test_reconciliation_distinguishes_partial_and_corruption() -> None:
    plan = list(build_plan())
    outcomes = _outcomes(plan)
    snapshots = [expected_record(), expected_record()]
    status, matched, corruption = assess_pt_cash(plan, outcomes, snapshots)
    assert (status, matched, corruption) == ("pass", True, False)
    assert records_match(expected_record())
    status, matched, corruption = assess_pt_cash(plan, [], [_zeros(), _zeros()])
    assert (status, matched, corruption) == ("not-run", False, False)
    partial = [item for item in outcomes if item["scenario"] == "cash_open"][:1]
    status, matched, corruption = assess_pt_cash(plan, partial, [_zeros(), _zeros()])
    assert (status, matched, corruption) == ("fail", False, False)
    progressed = dict(_zeros())
    progressed["cash_sessions_closed"] = 0
    progressed["opening_cash"] = OPENING_CASH
    status, matched, corruption = assess_pt_cash(plan, partial, [progressed, _zeros()])
    assert (status, matched, corruption) == ("fail", False, False)
    broken = dict(_zeros())
    broken["gym_mismatch"] = 1
    status, matched, corruption = assess_pt_cash(plan, partial, [broken, _zeros()])
    assert (status, matched, corruption) == ("fail", False, True)
    wrong = expected_record()
    wrong["cash_out_count"] = 2
    status, matched, corruption = assess_pt_cash(plan, outcomes, [wrong, expected_record()])
    assert (status, matched, corruption) == ("fail", False, True)
    assert same_gym_link_mismatches([("gym-a", "gym-a")]) == 0
    assert same_gym_link_mismatches([("gym-a", "gym-b"), ("gym-a", None)]) == 2
    clipped = public_outcome({"scenario": "cash_open", "body": "secret", "status": 201})
    assert "body" not in clipped


def test_seal_refuses_proof_and_false_zero() -> None:
    sealed = seal_evidence(_passing(), [])
    assert sealed["checksum"].startswith("sha256:")
    assert verify_checksum(sealed)
    assert sealed["evidence_name"] == "pt-cash.json"
    assert sealed["local_soak_status"] == "not-run"
    flagged = _passing()
    flagged["financial_reconciliation_proven"] = True
    with pytest.raises(PtCashSpecError, match="proof"):
        seal_evidence(flagged, [])
    false_zero = _passing()
    false_zero["caddy_5xx_delta"] = 0
    status, reasons = judge(false_zero)
    assert status == "fail"
    assert "caddy_false_zero" in reasons
    measured = _passing()
    measured["caddy_metrics_observed"] = True
    measured["caddy_5xx_delta"] = 0
    status, reasons = judge(measured)
    assert status == "pass", reasons
    leaked = _passing()
    leaked["failure_detail"] = "owner-cash-a@rehearsal.invalid"
    with pytest.raises(PtCashSpecError, match="secret"):
        seal_evidence(leaked, ["owner-cash-a@rehearsal.invalid"])


def test_repository_lint_passes() -> None:
    errors = lint_texts(
        command=(
            ROOT / "backend/apps/tenants/management/commands/staging_pt_cash_characterization.py"
        ).read_text(encoding="utf-8"),
        runbook=(ROOT / "docs/runbooks/phase8-increment-10.md").read_text(encoding="utf-8"),
        harness=(ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
    )
    assert errors == []
