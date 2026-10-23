from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.common.load_characterization import (
    AUTH_THROTTLE_NOTE,
    COVERAGE,
    MAX_CONNECTIONS,
    MAX_DURATION_SECONDS,
    MAX_OPERATIONS,
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    MAX_WORKERS,
    PROOF_FLAGS,
    LoadSpecError,
    assert_metrics_target,
    assert_rehearsal_context,
    assert_request_target,
    assess_financial,
    build_plan,
    caddy_5xx_total,
    caddy_metrics_observation,
    classify_status,
    clip_body,
    connection_within_ceiling,
    deadline_action,
    failure_document,
    judge,
    lint_texts,
    make_idempotency_key,
    marker_present,
    material_leaked,
    members_required,
    percentile_ms,
    reconciliation_matches,
    redirect_allowed,
    redirect_follow,
    resolve_profile,
    scenarios_match,
    seal_evidence,
    validate_bounds,
    verify_checksum,
)

ROOT = Path(__file__).resolve().parents[3]


def _passing() -> dict[str, object]:
    return {
        "kind": "local-disposable-load-characterization",
        "profile": "short",
        "local_load_status": "pass",
        "local_isolation_status": "pass",
        "local_financial_reconciliation_status": "pass",
        "thresholds_are_approved_slos": False,
        "cross_gym_leakage": 0,
        "credential_exposure": 0,
        "unexpected_errors": 0,
        "api_unexpected_5xx": 0,
        "caddy_5xx_delta": 0,
        "reconciliation_matched": True,
        "data_corruption": False,
        "postgres_connection_peak": 4,
        "postgres_max_connections": MAX_CONNECTIONS,
        "auth_rate_limit_observed": True,
        "cancelled_operations": 0,
        "required_complete": True,
        "http_p95_ms": 100,
        "coverage": [dict(item) for item in COVERAGE],
        "scenarios": [
            {"scenario": "dashboard_read", "transport": "http"},
        ],
        "reconciliation": {"transport": "service-only", "matched": True},
    }


def test_production_settings_and_hosts_are_refused() -> None:
    with pytest.raises(LoadSpecError, match="staging rehearsal settings"):
        assert_rehearsal_context(
            settings_module="config.settings.production",
            staging_flag=True,
            project="gym-staging-rehearsal",
            public_url="https://localhost:8443",
            database_host="postgres",
            database_user="gym_runtime",
            allowed_hosts=("localhost",),
        )
    with pytest.raises(LoadSpecError, match="disposable project"):
        assert_rehearsal_context(
            settings_module="config.settings.staging_rehearsal",
            staging_flag=True,
            project="gym-production",
            public_url="https://localhost:8443",
            database_host="postgres",
            database_user="gym_runtime",
            allowed_hosts=("localhost",),
        )
    with pytest.raises(LoadSpecError, match="staging rehearsal settings"):
        assert_rehearsal_context(
            settings_module="config.settings.staging_rehearsal",
            staging_flag=False,
            project="gym-staging-rehearsal",
            public_url="https://localhost:8443",
            database_host="postgres",
            database_user="gym_runtime",
            allowed_hosts=("localhost",),
        )
    with pytest.raises(LoadSpecError, match="non-rehearsal"):
        assert_rehearsal_context(
            settings_module="config.settings.staging_rehearsal",
            staging_flag=True,
            project="gym-staging-rehearsal",
            public_url="https://gym.example",
            database_host="prod-db",
            database_user="gym_runtime",
            allowed_hosts=("gym.example",),
        )


def test_caps_reject_overflow_and_bools() -> None:
    validate_bounds(
        duration_seconds=MAX_DURATION_SECONDS,
        workers=MAX_WORKERS,
        operations=MAX_OPERATIONS,
        timeout_seconds=MAX_TIMEOUT_SECONDS,
    )
    with pytest.raises(LoadSpecError, match="duration"):
        validate_bounds(duration_seconds=601, workers=1, operations=1, timeout_seconds=1)
    with pytest.raises(LoadSpecError, match="worker"):
        validate_bounds(duration_seconds=1, workers=17, operations=1, timeout_seconds=1)
    with pytest.raises(LoadSpecError, match="operation"):
        validate_bounds(duration_seconds=1, workers=1, operations=10_001, timeout_seconds=1)
    with pytest.raises(LoadSpecError, match="timeout"):
        validate_bounds(duration_seconds=1, workers=1, operations=1, timeout_seconds=11)
    with pytest.raises(LoadSpecError, match="duration"):
        validate_bounds(duration_seconds=True, workers=1, operations=1, timeout_seconds=1)  # type: ignore[arg-type]
    with pytest.raises(LoadSpecError, match="fixed profiles"):
        resolve_profile("unlimited")


def test_targets_redirects_and_body_cap() -> None:
    assert_request_target("caddy", 443, "localhost", ["10.0.1.8", "127.0.0.1"])
    assert_metrics_target("caddy", 2020, ["172.18.0.4"])
    with pytest.raises(LoadSpecError, match="allowlist"):
        assert_request_target("example.com", 443, "localhost", ["127.0.0.1"])
    with pytest.raises(LoadSpecError, match="not loopback or private"):
        assert_request_target("caddy", 443, "localhost", ["8.8.8.8"])
    with pytest.raises(LoadSpecError, match="metrics"):
        assert_metrics_target("caddy", 443, ["127.0.0.1"])
    assert redirect_allowed("/login", "localhost") is True
    assert redirect_allowed("https://localhost/login", "localhost") is True
    assert redirect_allowed("//localhost/login", "localhost") is False
    assert redirect_allowed("https://example.com/login", "localhost") is False
    assert redirect_allowed("http://localhost/login", "localhost") is False
    assert redirect_allowed("/login", "example.com") is False
    clipped, truncated = clip_body(b"abc")
    assert clipped == b"abc" and truncated is False
    clipped, truncated = clip_body(b"x" * (MAX_RESPONSE_BYTES + 5))
    assert len(clipped) == MAX_RESPONSE_BYTES and truncated is True
    with pytest.raises(LoadSpecError, match="response cap"):
        clip_body(b"abc", cap=32)


def test_percentiles_classification_and_cancellation() -> None:
    assert percentile_ms([], 50) is None
    assert percentile_ms([10, 20, 30, 40], 50) == 20
    assert percentile_ms([10, 20, 30, 40], 95) == 40
    assert percentile_ms([10, 20, 30, 40], 100) == 40
    with pytest.raises(LoadSpecError, match="percentile"):
        percentile_ms([1], 0)
    assert classify_status("auth_failure", 401) == "expected_rejection"
    assert classify_status("auth_failure", 429) == "expected_rejection"
    assert classify_status("auth_failure", 500) == "unexpected"
    assert classify_status("auth_failure", 503) == "unexpected"
    assert classify_status("cross_gym_checkin", 404) == "expected_rejection"
    assert classify_status("cross_gym_checkin", 200) == "unexpected"
    assert classify_status("cross_gym_checkin", 403) == "unexpected"
    assert classify_status("expense_create", 201) == "success"
    assert classify_status("expense_create", 422) == "unexpected"
    assert classify_status("dashboard_read", "timeout") == "unexpected"
    assert classify_status("dashboard_read", True) == "unexpected"
    assert deadline_action(deadline=10, now=10, pending=2) == "cancel"
    assert deadline_action(deadline=10, now=9, pending=2) == "run"
    assert deadline_action(deadline=10, now=20, pending=0) == "done"


def test_leakage_reconciliation_and_connection_ceiling() -> None:
    assert marker_present(b'{"id":"other"}', [b"other"]) is True
    assert marker_present(b"not found", [b"other-gym"]) is False
    assert material_leaked("owner@rehearsal.invalid", []) is True
    assert material_leaked("eyJabcdefgh.abcdefgh", []) is True
    assert material_leaked("BEGIN PRIVATE KEY", []) is True
    assert material_leaked("prefix super-secret-value suffix", ["super-secret-value"]) is True
    assert material_leaked("local pass", ["short"]) is False
    assert reconciliation_matches({"expenses": 2, "checkins": 1}, {"expenses": 2}) is True
    assert reconciliation_matches({"expenses": 1}, {"expenses": 2}) is False
    assert connection_within_ceiling(32, 40) is True
    assert connection_within_ceiling(33, 40) is False
    assert connection_within_ceiling(4, 100) is False
    assert connection_within_ceiling(True, 40) is False
    assert caddy_5xx_total('caddy_http_requests_total{code="500"} 2\n') == 2
    assert caddy_5xx_total('caddy_http_requests_total{code="200"} 9\n') is None
    assert caddy_metrics_observation("") == (False, None)
    assert caddy_metrics_observation("<html>missing</html>") == (False, None)
    assert caddy_metrics_observation('caddy_http_requests_total{code="200"} 9\n') == (True, 0)
    assert caddy_metrics_observation('caddy_http_requests_total{code="500"} 2\n') == (True, 2)


def test_evidence_refuses_false_proof_secrets_and_bad_checksums() -> None:
    sealed = seal_evidence(_passing(), [])
    assert sealed["checksum"].startswith("sha256:")
    assert verify_checksum(sealed) is True
    for flag in PROOF_FLAGS:
        assert sealed[flag] is False
    assert sealed["approved_slo"] is False
    assert sealed["provisional_thresholds"] is True
    assert sealed["project"] == "gym-staging-rehearsal"
    tampered = dict(sealed)
    tampered["http_p95_ms"] = 1
    assert verify_checksum(tampered) is False
    claimed = _passing()
    claimed["load_test_proven"] = True
    with pytest.raises(LoadSpecError, match="live proof"):
        seal_evidence(claimed, [])
    claimed = _passing()
    claimed["slo_proven"] = True
    with pytest.raises(LoadSpecError, match="live proof"):
        seal_evidence(claimed, [])
    claimed = _passing()
    claimed["production_capacity_proven"] = True
    with pytest.raises(LoadSpecError, match="live proof"):
        seal_evidence(claimed, [])
    slo = _passing()
    slo["thresholds_are_approved_slos"] = True
    with pytest.raises(LoadSpecError, match="approved SLOs"):
        seal_evidence(slo, [])
    leaked = _passing()
    leaked["failure_detail"] = "owner@rehearsal.invalid"
    with pytest.raises(LoadSpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked = _passing()
    leaked["local_load_status"] = "fail"
    leaked["note"] = "eyJabcdefgh.abcdefgh"
    with pytest.raises(LoadSpecError, match="secret"):
        seal_evidence(leaked, ["other-secret-value"])
    slow = _passing()
    slow["http_p95_ms"] = 8001
    status, reasons = judge(slow)
    assert status == "fail" and "provisional_latency_ceiling" in reasons
    with pytest.raises(LoadSpecError, match="disagrees"):
        seal_evidence(slow, [])
    leaked_gym = _passing()
    leaked_gym["cross_gym_leakage"] = 1
    status, reasons = judge(leaked_gym)
    assert "cross_gym_leakage" in reasons
    broken = _passing()
    broken["reconciliation_matched"] = False
    broken["local_financial_reconciliation_status"] = "fail"
    status, reasons = judge(broken)
    assert "reconciliation" in reasons and "financial_reconciliation" in reasons
    busy = _passing()
    busy["postgres_connection_peak"] = 33
    status, reasons = judge(busy)
    assert "connection_ceiling" in reasons
    stopped = _passing()
    stopped["cancelled_operations"] = 1
    status, reasons = judge(stopped)
    assert "cancelled" in reasons
    failure = seal_evidence(failure_document("short", "rehearsal"), [])
    assert failure["local_load_status"] == "fail"
    assert failure["load_test_proven"] is False
    assert failure["soak_test_proven"] is False
    assert verify_checksum(failure) is True


def test_plan_is_fixed_and_coverage_names_gaps() -> None:
    first = build_plan("short")
    second = build_plan("short")
    assert first == second
    assert len(first) <= MAX_OPERATIONS
    assert {step["phase"] for step in first} == {"fixed", "loop", "final"}
    assert sum(1 for step in first if step["scenario"] == "checkin_replay") == 2
    setup_done = max(
        index
        for index, step in enumerate(first)
        if step["scenario"] in {"cash_open", "expense_setup"} and step["phase"] == "fixed"
    )
    first_cross = min(
        index
        for index, step in enumerate(first)
        if step["scenario"] in {"cross_gym_expense", "cross_gym_cash"}
    )
    assert setup_done < first_cross
    assert resolve_profile("bounded").workers == 4
    assert resolve_profile("bounded").duration_seconds <= MAX_DURATION_SECONDS
    key = make_idempotency_key("expense", 1, 12)
    assert key == "load-expense-g1-0012"
    with pytest.raises(LoadSpecError, match="kind"):
        make_idempotency_key("payment", 0, 1)
    needed = members_required(first)
    assert needed[0] >= 1 and needed[1] >= 1
    uncovered = [item["id"] for item in COVERAGE if item["covered"] is False]
    assert uncovered == ["receipt_issue", "receipt_download", "pt_sale_or_session", "pt_report"]
    assert all(item["transport"] == "uncovered" for item in COVERAGE if item["covered"] is False)
    assert all(item["transport"] == "http" for item in COVERAGE if item["covered"] is True)
    encoded = json.dumps([dict(item) for item in COVERAGE])
    assert "service-only" not in encoded


def _financial_plan() -> list[dict[str, object]]:
    return [
        {"scenario": "expense_setup", "phase": "fixed"},
        {"scenario": "expense_replay", "phase": "fixed"},
        {"scenario": "expense_read", "phase": "fixed"},
        {"scenario": "expense_create", "phase": "loop"},
        {"scenario": "cash_open", "phase": "fixed"},
        {"scenario": "cash_replay", "phase": "fixed"},
        {"scenario": "cash_read", "phase": "loop"},
        {"scenario": "checkin_create", "phase": "loop"},
        {"scenario": "checkin_replay", "phase": "final"},
    ]


def _financial_outcomes(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"scenario": step["scenario"], "phase": step["phase"], "classification": "success"}
        for step in plan
    ]


def test_missing_429_does_not_fail_and_unexpected_status_still_does() -> None:
    observed = _passing()
    observed["auth_rate_limit_observed"] = False
    status, reasons = judge(observed)
    assert status == "pass"
    assert "auth_rate_limit" not in reasons
    assert "LocMem" in AUTH_THROTTLE_NOTE
    assert "WAF" in AUTH_THROTTLE_NOTE
    unexpected = _passing()
    unexpected["unexpected_errors"] = 1
    unexpected["api_unexpected_5xx"] = 1
    status, reasons = judge(unexpected)
    assert status == "fail"
    assert "unexpected_errors" in reasons
    assert "api_unexpected_5xx" in reasons


def test_empty_financial_delta_is_not_a_pass() -> None:
    plan = _financial_plan()
    status, matched, corruption = assess_financial(
        plan, [], {"expenses": 0, "checkins": 0, "cash": 0}
    )
    assert (status, matched, corruption) == ("not-run", False, False)
    incomplete = _financial_outcomes(plan)[:1]
    status, matched, corruption = assess_financial(
        plan, incomplete, {"expenses": 1, "checkins": 0, "cash": 0}
    )
    assert status == "fail"
    assert matched is False
    assert corruption is False
    complete = _financial_outcomes(plan)
    status, matched, corruption = assess_financial(
        plan, complete, {"expenses": 2, "checkins": 1, "cash": 1}
    )
    assert (status, matched, corruption) == ("pass", True, False)
    status, matched, corruption = assess_financial(
        plan, complete, {"expenses": 0, "checkins": 0, "cash": 0}
    )
    assert status == "fail"
    assert matched is False
    assert corruption is True
    assert assess_financial(plan, [], None) == ("not-run", False, False)


def test_scenario_multiset_rejects_same_count_substitution() -> None:
    plan = build_plan("short")
    outcomes = [
        {"scenario": step["scenario"], "phase": step["phase"], "classification": "success"}
        for step in plan
    ]
    assert scenarios_match(plan, outcomes) is True
    wrong = [dict(item) for item in outcomes]
    wrong[0]["scenario"] = wrong[1]["scenario"]
    assert len(wrong) == len(plan)
    assert scenarios_match(plan, wrong) is False
    duplicate = [dict(item) for item in outcomes]
    replaced = False
    for item in duplicate:
        if item["scenario"] == "checkin_create":
            item["scenario"] = "checkin_duplicate"
            replaced = True
            break
    assert replaced is True
    assert scenarios_match(plan, duplicate) is True
    extra = [dict(item) for item in outcomes]
    extra[0]["scenario"] = "checkin_duplicate"
    assert scenarios_match(plan, extra) is False


def test_redirect_follow_preserves_only_temporary_redirects() -> None:
    kept = redirect_follow(
        status=307,
        location="https://localhost/api/auth/login",
        method="POST",
        redirects_followed=0,
    )
    assert kept == {
        "method": "POST",
        "target": "/api/auth/login",
        "preserve_body": True,
        "preserve_csrf": True,
        "preserve_idempotency": True,
    }
    also_kept = redirect_follow(
        status=308,
        location="/api/operations/expenses?limit=5",
        method="POST",
        redirects_followed=0,
    )
    assert also_kept is not None
    assert also_kept["method"] == "POST"
    assert also_kept["preserve_body"] is True
    assert also_kept["preserve_csrf"] is True
    assert also_kept["preserve_idempotency"] is True
    switched = redirect_follow(
        status=302,
        location="https://localhost/login",
        method="POST",
        redirects_followed=0,
    )
    assert switched is not None
    assert switched["method"] == "GET"
    assert switched["preserve_body"] is False
    assert switched["preserve_csrf"] is False
    assert switched["preserve_idempotency"] is False
    assert (
        redirect_follow(status=301, location="/login", method="POST", redirects_followed=0)[
            "method"
        ]
        == "GET"
    )
    assert (
        redirect_follow(
            status=303,
            location="https://localhost/login",
            method="POST",
            redirects_followed=0,
        )["method"]
        == "GET"
    )
    assert (
        redirect_follow(
            status=308,
            location="https://localhost/api/auth/login",
            method="POST",
            redirects_followed=1,
        )
        is None
    )
    assert (
        redirect_follow(
            status=307,
            location="https://example.com/api/auth/login",
            method="POST",
            redirects_followed=0,
        )
        is None
    )
    rejected = redirect_follow(
        status=302,
        location="//localhost/login",
        method="GET",
        redirects_followed=0,
    )
    assert rejected is None


def test_covered_bff_paths_have_no_trailing_slash() -> None:
    for item in COVERAGE:
        route = str(item["route"])
        if not item["covered"]:
            continue
        if route.startswith("/api/auth/") or route.startswith("/api/operations/"):
            assert not route.split("?", 1)[0].endswith("/")
    command = (
        ROOT / "backend/apps/tenants/management/commands/staging_load_characterization.py"
    ).read_text(encoding="utf-8")
    assert '"/api/auth/login/"' not in command
    assert '"/api/operations/dashboard/"' not in command
    assert '"/api/operations/check-ins/"' not in command
    assert '"/api/operations/expenses/"' not in command
    assert '"/api/operations/cash-sessions/open/"' not in command


def test_lint_texts_reject_missing_guards_and_false_claims() -> None:
    errors = lint_texts(command="staging", runbook="notes", harness="run")
    assert any("load command is missing" in item for item in errors)
    assert any("load runbook is missing" in item for item in errors)
    assert any("fixed load profile" in item for item in errors)
    claim = lint_texts(
        command="config.settings.staging_rehearsal STAGING_REHEARSAL gym-staging-rehearsal "
        "load_test_proven slo_proven production_capacity_proven",
        runbook=(
            "Phase 8 is not complete provisional load_test_proven soak_test_proven "
            "production_capacity_proven slo_proven local_load_status local_isolation_status "
            "local_financial_reconciliation_status not an approved SLO receipt issuance PT "
            "transport SLO is proven"
        ),
        harness="load short",
    )
    assert any("claims SLO is proven" in item for item in claim)
