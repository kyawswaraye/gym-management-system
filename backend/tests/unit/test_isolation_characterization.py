from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from apps.common.isolation_characterization import (
    BFF_ROUTES,
    COVERAGE,
    PLAN_OPERATIONS,
    PROOF_FLAGS,
    RULES,
    SEED_COUNTS,
    UNCOVERED,
    IsolationSpecError,
    assemble,
    assert_isolation_route,
    assess_isolation,
    build_plan,
    evidence_has_identity_marker,
    failure_document,
    lint_texts,
    public_outcome,
    require_int,
    resolve_profile,
    runtime_status,
    seal_evidence,
    valid_resource_id,
    verify_checksum,
)

ROOT = Path(__file__).resolve().parents[3]
_RESOURCE_ID = "123e4567-e89b-42d3-a456-426614174000"


def _finished(gym: int) -> dict[str, object]:
    row: dict[str, object] = {
        "gym": gym,
        "member_status": "ACTIVE",
        "member_notes_blank": True,
        "member_gym_match": True,
        "owner_is_owner": True,
        "audit_rows": 8,
    }
    row.update(SEED_COUNTS)
    return row


def _status(scenario: str) -> int:
    rule = RULES[scenario]
    return min(rule.success or rule.rejection)


def _passing() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for step in build_plan():
        scenario = str(step["scenario"])
        gym = step["gym"]
        rows.append(
            public_outcome(
                scenario=scenario,
                gym=gym if gym is None or type(gym) is int else None,
                status=_status(scenario),
                leak=False,
                credential=False,
                redirect_ok=True,
                cookie_ok=True,
                controls_ok=True,
            )
        )
    return rows


def _pass_body() -> dict[str, object]:
    return assemble(
        outcomes=_passing(),
        snapshots=[_finished(0), _finished(1)],
        cancelled=0,
        wall_seconds=1,
        metrics=(True, 0),
    )


def test_plan_bounds_and_profile_are_fixed() -> None:
    assert resolve_profile("isolation") == "isolation"
    assert PLAN_OPERATIONS == 44
    assert require_int(44, minimum=1, maximum=80) == 44
    with pytest.raises(IsolationSpecError):
        resolve_profile("security")
    with pytest.raises(IsolationSpecError):
        require_int(True, minimum=0, maximum=10)
    plan = build_plan()
    assert len(plan) == 44
    runtime = [row for row in plan if row["phase"] == "runtime"]
    assert len(runtime) == 6
    assert all(row["gym"] is None for row in runtime)
    assert [row["scenario"] for row in runtime] == [
        "schema_denied",
        "grant_denied",
        "missing_context_read",
        "missing_context_write",
        "rollback_context",
        "context_reuse",
    ]
    gyms = [row for row in plan if row["phase"] != "runtime"]
    assert [row["gym"] for row in gyms].count(0) == 19
    assert [row["gym"] for row in gyms].count(1) == 19
    first = [row["scenario"] for row in gyms if row["gym"] == 0]
    assert first[0] == "auth_login"
    assert first[1] == "members_list"
    assert first[-1] == "rls_aggregate"
    assert gyms[0]["scenario"] == "auth_login"
    assert gyms[19]["gym"] == 1
    assert gyms[19]["scenario"] == "auth_login"


def test_route_catalog_rejects_anything_outside_the_fixed_paths() -> None:
    assert ("POST", "/api/auth/login", "none") in BFF_ROUTES
    assert ("GET", "/api/operations/audit-logs", "audit") in BFF_ROUTES
    assert ("GET", "/api/operations/cash-sessions/{id}", "none") in BFF_ROUTES
    assert_isolation_route("GET", "/api/operations/members")
    assert_isolation_route("GET", f"/api/operations/members/{_RESOURCE_ID}")
    assert_isolation_route("GET", "/api/operations/audit-logs?module=cashbook&limit=25")
    assert_isolation_route("GET", "/api/operations/reports/daily?date=2026-10-22")
    assert_isolation_route("GET", f"/api/operations/cash-sessions/{_RESOURCE_ID}")
    rejected = (
        ("GET", "/api/operations/members?gym=1"),
        ("POST", "/api/auth/login?next=1"),
        ("GET", "/api/operations/audit-logs?module=roles&limit=25"),
        ("GET", "/api/operations/reports/daily?date=2026-13-01"),
        ("GET", "/api/v1/members/"),
        ("GET", "/api/operations/../members"),
        ("GET", f"/api/operations/files/{_RESOURCE_ID}/download-url"),
        ("POST", "/api/operations/expenses"),
        (None, "/api/auth/login"),
    )
    for method, path in rejected:
        with pytest.raises(IsolationSpecError):
            assert_isolation_route(method, path)


def test_resource_ids_and_runtime_status_are_strict() -> None:
    generated = str(uuid.uuid4())
    assert valid_resource_id(generated) == generated
    assert valid_resource_id(_RESOURCE_ID) == _RESOURCE_ID
    assert valid_resource_id(_RESOURCE_ID.upper()) == ""
    assert valid_resource_id(None) == ""
    assert runtime_status(True) == 200
    assert runtime_status(False) == 409


def test_empty_baseline_is_not_run_and_a_partial_matrix_fails() -> None:
    snapshots = [_finished(0), _finished(1)]
    status, matched, corruption = assess_isolation([], snapshots, cancelled=46)
    assert status == "not-run"
    assert matched is False
    assert corruption is False
    report = assemble(
        outcomes=[],
        snapshots=snapshots,
        cancelled=0,
        wall_seconds=0,
        metrics=(False, None),
    )
    assert report["local_isolation_status"] == "not-run"
    assert report["local_security_status"] == "not-run"
    assert report["local_identity_status"] == "not-run"
    assert report["local_soak_status"] == "not-run"
    assert all(report[flag] is False for flag in PROOF_FLAGS)
    assert report["tenant_isolation_proven"] is False
    sealed = seal_evidence(report, [])
    assert sealed["local_isolation_status"] == "not-run"
    assert verify_checksum(sealed) is True
    runtime = _passing()[:6]
    status, matched, corruption = assess_isolation(runtime, snapshots, cancelled=0)
    assert status == "fail"
    assert matched is True
    assert corruption is False


def test_full_matrix_passes_only_with_seed_counts_and_forced_false_proofs() -> None:
    report = _pass_body()
    assert report["local_isolation_status"] == "pass"
    assert report["completed_operations"] == 44
    assert report["cancelled_operations"] == 0
    assert report["credential_exposure"] == 0
    assert report["cross_gym_leakage"] == 0
    assert report["data_corruption"] is False
    assert report["local_security_status"] == "not-run"
    assert report["local_identity_status"] == "not-run"
    assert report["tenant_isolation_proven"] is False
    assert [row["id"] for row in report["coverage"]] == [item["id"] for item in COVERAGE]
    assert all(row["covered"] is True for row in report["coverage"])
    assert [row["id"] for row in report["uncovered"]] == [item["id"] for item in UNCOVERED]
    assert all(row["covered"] is False for row in report["uncovered"])
    sealed = seal_evidence(report, [])
    assert verify_checksum(sealed) is True
    sealed["checksum"] = "sha256:" + "00" * 32
    assert verify_checksum(sealed) is False
    changed = seal_evidence(_pass_body(), [])
    changed["wall_time_seconds"] = 2
    assert verify_checksum(changed) is False


def test_leakage_corruption_and_forged_pass_are_refused() -> None:
    outcomes = _passing()
    extra = _finished(0)
    extra["expenses"] = 4
    status, _matched, corruption = assess_isolation(outcomes, [extra, _finished(1)], cancelled=0)
    assert status == "fail"
    assert corruption is True
    flipped = _finished(0)
    flipped["owner_is_owner"] = False
    status, _matched, corruption = assess_isolation(outcomes, [flipped, _finished(1)], cancelled=0)
    assert corruption is True
    leaked_row = _finished(0)
    leaked_row["file_cross"] = 1
    status, _matched, corruption = assess_isolation(
        outcomes, [leaked_row, _finished(1)], cancelled=0
    )
    assert corruption is True
    with pytest.raises(IsolationSpecError):
        failure_document("security", "characterization")
    forged = assemble(
        outcomes=[],
        snapshots=[_finished(0), _finished(1)],
        cancelled=0,
        wall_seconds=0,
        metrics=(False, None),
    )
    forged["local_isolation_status"] = "pass"
    with pytest.raises(IsolationSpecError, match="disagrees"):
        seal_evidence(forged, [])
    leaked = _pass_body()
    leaked["failure_detail"] = "owner-iso-a@rehearsal.invalid"
    with pytest.raises(IsolationSpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = _RESOURCE_ID
    with pytest.raises(IsolationSpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = "ISOGYMA"
    with pytest.raises(IsolationSpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = ""
    leaked["tenant_isolation_proven"] = True
    with pytest.raises(IsolationSpecError, match="proof"):
        seal_evidence(leaked, [])
    leaked["tenant_isolation_proven"] = False
    leaked["local_security_status"] = "pass"
    with pytest.raises(IsolationSpecError, match="security status"):
        seal_evidence(leaked, [])
    leak_outcomes = _passing()
    leak_outcomes[7]["leak"] = True
    leak_outcomes[7]["classification"] = "fail"
    report = assemble(
        outcomes=leak_outcomes,
        snapshots=[_finished(0), _finished(1)],
        cancelled=0,
        wall_seconds=1,
        metrics=(True, 0),
    )
    assert report["local_isolation_status"] == "fail"
    assert report["cross_gym_leakage"] == 1
    assert evidence_has_identity_marker("ISOGYMA") is True
    assert evidence_has_identity_marker("local_isolation_status") is False


def test_lint_wires_the_command_runbook_and_harness() -> None:
    command = (
        ROOT / "backend/apps/tenants/management/commands/staging_isolation_characterization.py"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/phase8-increment-14.md").read_text(encoding="utf-8")
    harness = (ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8")
    assert "add_argument" not in command
    assert "argparse" not in command
    snapshot = command[command.index("def _snapshot") : command.index("def _scrape")]
    assert snapshot.index("with transaction.atomic():") < snapshot.index("set_gym_context(")
    assert lint_texts(command=command, runbook=runbook, harness=harness) == []
    assert "does not replace identity-security.json" in harness
    assert "staging_isolation_characterization" in harness
    assert "require_clamav_tcp=False" in harness
