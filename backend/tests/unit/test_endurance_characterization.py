from __future__ import annotations

from dataclasses import replace

import pytest

from apps.common.endurance_characterization import (
    DURATION_SECONDS,
    LOOP_OPERATIONS,
    PACE_SECONDS,
    PLAN_OPERATIONS,
    WORKERS,
    EnduranceSpecError,
    assert_endurance_path,
    assess_endurance,
    assess_isolation,
    build_plan,
    caddy_metrics_observation,
    classify_status,
    clip_body,
    expected_writes,
    failure_document,
    judge,
    lint_texts,
    loop_queue_cap,
    make_key,
    members_required,
    metrics_delta,
    operation_cap,
    pace_delay,
    resolve_profile,
    schedule_match,
    seal_evidence,
    summarize_resources,
    throughput,
    validate_profile,
    verify_checksum,
)
from apps.common.load_characterization import LoadSpecError

_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _outcome(step: dict[str, object], *, classification: str, status: object) -> dict[str, object]:
    return {
        "scenario": step["scenario"],
        "phase": step["phase"],
        "gym": step["gym"],
        "ordinal": step["ordinal"],
        "classification": classification,
        "status": status,
        "leak": False,
        "credential": False,
        "latency_ms": 20,
    }


def _classified(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for step in plan:
        scenario = str(step["scenario"])
        if scenario.startswith("cross_gym_"):
            outcomes.append(_outcome(step, classification="expected_rejection", status=404))
        elif scenario in {"checkin_create", "expense_setup", "expense_create", "cash_open"}:
            outcomes.append(_outcome(step, classification="success", status=201))
        else:
            outcomes.append(_outcome(step, classification="success", status=200))
    return outcomes


def _rows(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "gym": gym,
            **expected_writes(plan, gym),
            "gym_mismatch": 0,
        }
        for gym in (0, 1)
    ]


def _passing() -> dict[str, object]:
    body = failure_document("endurance", "local")
    body.update(
        {
            "failure_stage": "",
            "local_endurance_status": "pass",
            "local_isolation_status": "pass",
            "local_financial_reconciliation_status": "pass",
            "measured_phase_seconds": 137.5,
            "wall_time_seconds": 150.0,
            "completed_operations": PLAN_OPERATIONS,
            "cancelled_operations": 0,
            "peak_active_workers": WORKERS,
            "reconciliation_matched": True,
            "data_corruption": False,
            "required_complete": True,
            "postgres_connection_peak": 4,
            "postgres_max_connections": 40,
            "http_p95_ms": 259,
            "caddy_metrics_observed": True,
            "caddy_5xx_delta": 0,
        }
    )
    return body


def test_profile_rejects_bools_and_foreign_caps() -> None:
    profile = resolve_profile("endurance")
    assert profile.duration_seconds == DURATION_SECONDS
    assert profile.workers == WORKERS
    assert profile.pace_seconds == PACE_SECONDS
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, duration_seconds=True))  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, workers=True))  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, pace_seconds=True))  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, timeout_seconds=True))  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, repeats=True))  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        validate_profile(replace(profile, duration_seconds=10))
    with pytest.raises(EnduranceSpecError):
        resolve_profile("short")
    with pytest.raises(EnduranceSpecError):
        operation_cap(True)  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        operation_cap(PLAN_OPERATIONS + 1)
    with pytest.raises(EnduranceSpecError):
        loop_queue_cap(LOOP_OPERATIONS - 1)
    assert operation_cap(PLAN_OPERATIONS) == PLAN_OPERATIONS
    assert loop_queue_cap(LOOP_OPERATIONS) == LOOP_OPERATIONS


def test_schedule_is_deterministic_and_interleaved() -> None:
    plan = build_plan()
    assert len(plan) == PLAN_OPERATIONS
    assert sum(1 for step in plan if step["phase"] == "loop") == LOOP_OPERATIONS
    assert [step["gym"] for step in plan[:2]] == [0, 1]
    assert members_required(plan) == (6, 6)
    assert expected_writes(plan, 0) == {
        "checkins": 6,
        "expenses": 6,
        "cash_sessions": 1,
    }
    assert expected_writes(plan, 1) == expected_writes(plan, 0)
    crosses = [index for index, step in enumerate(plan) if step["scenario"] == "cross_gym_checkin"]
    assert crosses[0] < len(plan) // 2
    assert len(crosses) == 10
    assert {plan[index]["gym"] for index in crosses[:2]} == {0, 1}
    assert plan[-1]["scenario"] == "cross_gym_cash"
    assert not any(
        step["scenario"] in {"cash_close", "pt_cash_payout", "auth_failure"} for step in plan
    )
    assert schedule_match(plan, list(reversed(plan)))
    changed = [dict(step) for step in plan]
    changed[0]["gym"] = 1
    assert schedule_match(plan, changed) is False
    assert schedule_match(plan, plan[:-1]) is False


def test_keys_paths_and_classification_reject_malformed_input() -> None:
    assert make_key("checkin", 0, 3) == "end-checkin-g0-0003"
    with pytest.raises(EnduranceSpecError):
        make_key("checkin", True, 0)  # type: ignore[arg-type]
    with pytest.raises(EnduranceSpecError):
        make_key("checkin", 0, True)  # type: ignore[arg-type]
    assert_endurance_path("GET", "/api/operations/dashboard?range=today")
    assert_endurance_path("GET", "/api/operations/reports/daily?date=2026-10-21")
    assert_endurance_path(
        "GET",
        "/api/operations/reports/pt-sessions?date_from=2026-10-21&date_to=2026-10-21",
    )
    assert_endurance_path("GET", f"/api/operations/expenses/{_UUID}")
    assert_endurance_path("GET", f"/api/operations/cash-sessions/{_UUID}")
    with pytest.raises(EnduranceSpecError):
        assert_endurance_path("GET", f"/api/operations/expenses/{_UUID.upper()}")
    with pytest.raises(EnduranceSpecError):
        assert_endurance_path("GET", "/api/operations/expenses/" + ("-" * 36))
    with pytest.raises(EnduranceSpecError):
        assert_endurance_path("POST", "https://example.com/api/auth/login")
    with pytest.raises(EnduranceSpecError):
        assert_endurance_path("DELETE", "/api/operations/check-ins")
    assert classify_status("cross_gym_cash", 404) == "expected_rejection"
    assert classify_status("cross_gym_cash", 500) == "unexpected"
    assert classify_status("checkin_create", True) == "unexpected"
    assert classify_status("checkin_create", 201) == "success"


def test_pacing_clip_and_metrics_keep_failed_samples_null() -> None:
    assert pace_delay(earliest=10.0, now=8.0, deadline=20.0) == 2.0
    assert pace_delay(earliest=8.0, now=9.0, deadline=20.0) == 0.0
    assert pace_delay(earliest=19.0, now=18.0, deadline=19.0) is None
    with pytest.raises(EnduranceSpecError):
        pace_delay(earliest=True, now=1.0, deadline=2.0)  # type: ignore[arg-type]
    clipped, truncated = clip_body(b"x" * (16_384 + 5))
    assert truncated is True
    assert len(clipped) == 16_384
    with pytest.raises(LoadSpecError):
        clip_body(b"x", cap=10)
    assert caddy_metrics_observation("") == (False, None)
    assert caddy_metrics_observation("<html></html>") == (False, None)
    assert caddy_metrics_observation("caddy_http_requests_total 4\n") == (True, 0)
    observed = caddy_metrics_observation('caddy_http_requests_total{code="500"} 2\n')
    assert observed == (True, 2)
    assert metrics_delta((False, None), (True, 0)) == (False, None)
    assert metrics_delta((True, 0), (True, 0)) == (True, 0)
    assert metrics_delta((True, 1), (True, 4)) == (True, 3)
    missing = summarize_resources(
        [{"memory_bytes": None, "cpu_usage_usec": None, "disk_free_bytes": None}]
    )
    assert missing["resource_samples_observed"] is False
    assert missing["container_memory_bytes_peak"] is None
    assert missing["docker_memory_bytes"] is None
    zero = summarize_resources(
        [{"memory_bytes": 0, "cpu_usage_usec": None, "disk_free_bytes": None}]
    )
    assert zero["resource_samples_observed"] is True
    assert zero["container_memory_bytes_peak"] == 0
    with pytest.raises(EnduranceSpecError):
        throughput(True, 1.0)  # type: ignore[arg-type]
    assert throughput(4, 0.0) is None
    assert throughput(4, 2.0) == 2.0


def test_reconciliation_distinguishes_partial_cancelled_and_corruption() -> None:
    plan = build_plan()
    zeros = [
        {"gym": gym, "checkins": 0, "expenses": 0, "cash_sessions": 0, "gym_mismatch": 0}
        for gym in (0, 1)
    ]
    assert assess_endurance(plan, [], zeros, cancelled=0) == ("not-run", False, False)
    partial = [
        _outcome(plan[0], classification="success", status=201)
        | {"scenario": "checkin_create", "gym": 0, "ordinal": 0}
    ]
    matched_partial = [
        {"gym": 0, "checkins": 1, "expenses": 0, "cash_sessions": 0, "gym_mismatch": 0},
        {"gym": 1, "checkins": 0, "expenses": 0, "cash_sessions": 0, "gym_mismatch": 0},
    ]
    assert assess_endurance(plan, partial, matched_partial, cancelled=1) == ("fail", False, False)
    contradicted = [
        {"gym": 0, "checkins": 2, "expenses": 0, "cash_sessions": 0, "gym_mismatch": 0},
        matched_partial[1],
    ]
    assert assess_endurance(plan, partial, contradicted, cancelled=1) == ("fail", False, True)
    broken = [
        {"gym": 0, "checkins": 1, "expenses": 0, "cash_sessions": 0, "gym_mismatch": 1},
        matched_partial[1],
    ]
    assert assess_endurance(plan, partial, broken, cancelled=1) == ("fail", False, True)
    outcomes = _classified(plan)
    assert assess_endurance(plan, outcomes, _rows(plan), cancelled=0) == ("pass", True, False)
    assert assess_isolation(plan, outcomes) == "pass"
    leaked = [dict(item) for item in outcomes]
    leaked[-1]["classification"] = "success"
    leaked[-1]["leak"] = True
    assert assess_isolation(plan, leaked) == "fail"
    assert assess_isolation(plan, outcomes[:4]) == "not-run"
    with pytest.raises(EnduranceSpecError):
        assess_endurance(plan, [], zeros, cancelled=True)  # type: ignore[arg-type]


def test_seal_rejects_false_proof_and_false_zero() -> None:
    sealed = seal_evidence(_passing(), [])
    assert sealed["local_soak_status"] == "not-run"
    assert sealed["soak_test_proven"] is False
    assert sealed["load_test_proven"] is False
    assert verify_checksum(sealed) is True
    soaked = _passing()
    soaked["local_soak_status"] = "pass"
    with pytest.raises(EnduranceSpecError):
        seal_evidence(soaked, [])
    flagged = _passing()
    flagged["soak_test_proven"] = True
    with pytest.raises(EnduranceSpecError):
        seal_evidence(flagged, [])
    false_zero = _passing()
    false_zero["caddy_metrics_observed"] = False
    false_zero["caddy_5xx_delta"] = 0
    with pytest.raises(EnduranceSpecError):
        seal_evidence(false_zero, [])
    short = _passing()
    short["measured_phase_seconds"] = 5.0
    with pytest.raises(EnduranceSpecError):
        seal_evidence(short, [])
    resource = _passing()
    resource["container_memory_bytes_peak"] = 0
    with pytest.raises(EnduranceSpecError):
        seal_evidence(resource, [])
    leaky = _passing()
    leaky["failure_detail"] = "owner@rehearsal.invalid"
    with pytest.raises(EnduranceSpecError):
        seal_evidence(leaky, ["owner@rehearsal.invalid"])
    status, reasons = judge(false_zero)
    assert status == "fail"
    assert "caddy_false_zero" in reasons


def test_repository_texts_describe_local_endurance() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[3]
    errors = lint_texts(
        command=(
            root / "backend/apps/tenants/management/commands/staging_endurance_characterization.py"
        ).read_text(encoding="utf-8"),
        runbook=(root / "docs/runbooks/phase8-increment-11.md").read_text(encoding="utf-8"),
        harness=(root / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
    )
    assert errors == []
