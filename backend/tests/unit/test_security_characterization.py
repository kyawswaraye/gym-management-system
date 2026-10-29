"""Offline rules for the disposable local security characterization."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from apps.common.security_characterization import (
    BFF_ROUTES,
    KEY_KINDS,
    PLAN_OPERATIONS,
    RULES,
    SecuritySpecError,
    assemble,
    assert_bff_path,
    assert_private_api_target,
    assess_security,
    auth_cookie_flags,
    bff_required,
    build_plan,
    credential_in_body,
    csrf_failed,
    failure_document,
    lint_texts,
    make_key,
    offline_redirects_pass,
    origin_reflected,
    public_outcome,
    redirect_accepted,
    require_int,
    required_auth_cookies,
    resolve_profile,
    seal_evidence,
    valid_resource_id,
    verify_checksum,
)

_RESOURCE_ID = "123e4567-e89b-42d3-a456-426614174000"
_BARE_JWT = (
    b"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    b"eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    b"dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)

ROOT = Path(__file__).resolve().parents[3]


def _row(gym: int, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "gym": gym,
        "expenses": 2,
        "expenses_pending": 2,
        "expenses_approved": 0,
        "checkins": 1,
        "cash_sessions": 1,
        "cash_open": 1,
        "pt_sales": 1,
        "pt_sales_pending": 1,
        "pt_sales_posted": 0,
        "member_status": "ACTIVE",
        "member_notes_blank": True,
        "member_gym_match": True,
        "owner_is_owner": True,
        "manager_is_owner": False,
        "trainer_is_owner": False,
        "manager_roles": ["MANAGER"],
        "trainer_roles": ["TRAINER"],
        "audit_expense_draft": 2,
        "audit_expense_submitted": 2,
        "audit_expense_approved": 0,
        "audit_pt_draft": 1,
        "audit_pt_submitted": 1,
        "audit_pt_approved": 0,
        "audit_checkin": 1,
        "gym_mismatch": 0,
    }
    row.update(overrides)
    return row


def _baseline(gym: int) -> dict[str, object]:
    return _row(
        gym,
        expenses=0,
        expenses_pending=0,
        checkins=0,
        cash_sessions=0,
        cash_open=0,
        pt_sales=0,
        pt_sales_pending=0,
        audit_expense_draft=0,
        audit_expense_submitted=0,
        audit_pt_draft=0,
        audit_pt_submitted=0,
        audit_checkin=0,
    )


def _passing_outcomes() -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for step in build_plan():
        scenario = str(step["scenario"])
        rule = RULES[scenario]
        status = next(iter(rule.success or rule.rejection))
        gym = step["gym"]
        outcomes.append(
            public_outcome(
                scenario=scenario,
                gym=gym if type(gym) is int else None,
                status=status,
                leak=False,
                credential=False,
                redirect_ok=True,
                cookie_ok=True,
                controls_ok=True,
            )
        )
    return outcomes


def test_plan_is_fixed_and_probes_follow_both_gyms() -> None:
    resolve_profile("security")
    plan = build_plan()
    assert len(plan) == PLAN_OPERATIONS == 65
    assert [item["gym"] for item in plan if item["phase"] == "edge"] == [None] * 9
    setup = [item["gym"] for item in plan if item["phase"] == "setup"]
    assert setup == [0] * 11 + [1] * 11
    probe_at = next(index for index, item in enumerate(plan) if item["phase"] == "probe")
    assert probe_at == 9 + 22
    assert RULES["idor_member_read"].rejection == frozenset({404})
    assert RULES["mass_expense"].rejection == frozenset({400})
    assert RULES["self_expense_approve"].success == frozenset({200})
    assert RULES["self_pt_approve"].success == frozenset({200})
    assert RULES["role_escalation"].rejection == frozenset({403})
    with pytest.raises(SecuritySpecError):
        resolve_profile("load")
    with pytest.raises(SecuritySpecError):
        require_int(True, minimum=0, maximum=10)


def test_redirects_stay_on_the_fixed_host_without_credentials() -> None:
    assert offline_redirects_pass() is True
    assert redirect_accepted("/login") is True
    assert redirect_accepted("https://localhost:8443/login") is True
    assert redirect_accepted("https://untrusted.example/login") is False
    assert redirect_accepted("https://localhost:8443/login?access_token=abc") is False
    assert redirect_accepted("https://user:secret@localhost:8443/login") is False
    assert redirect_accepted("//untrusted.example/login") is False
    assert origin_reflected("*", "https://untrusted.example") is True
    assert origin_reflected(None, "https://untrusted.example") is False


def test_auth_cookies_require_secure_httponly_samesite_and_path() -> None:
    headers = [
        "gms_access=value; HttpOnly; Secure; SameSite=Lax; Path=/",
        "gms_refresh=value; HttpOnly; Secure; SameSite=Lax; Path=/",
    ]
    assert required_auth_cookies(auth_cookie_flags(headers), secure=True) is True
    missing = ["gms_access=value; Secure; SameSite=Lax; Path=/"]
    assert required_auth_cookies(auth_cookie_flags(missing), secure=True) is False
    assert credential_in_body(b'{"access_token":"x"}') is True
    assert bff_required(b'{"error":{"code":"BFF_REQUIRED"}}') is True
    assert csrf_failed(b'{"error":{"code":"CSRF_FAILED"}}') is True
    assert make_key("expense", 0, 1) == "sec-expense-g0-0001"
    with pytest.raises(SecuritySpecError):
        assert_bff_path("POST", "https://untrusted.example/api/auth/login")
    with pytest.raises(SecuritySpecError):
        assert_private_api_target("api", 8000, "/api/v1/auth/login/", ["8.8.8.8"])


def _rendered_route(method: str, template: str, query: str) -> tuple[str, str]:
    path = template.replace("{id}", _RESOURCE_ID)
    if query == "date":
        path = f"{path}?date=2026-10-21"
    return method, path


def test_bff_path_catalog_matches_live_routes() -> None:
    assert BFF_ROUTES == (
        ("GET", "/api/v1/members/", "none"),
        ("POST", "/api/auth/login", "none"),
        ("GET", "/api/operations/members", "none"),
        ("GET", "/api/operations/members/{id}", "none"),
        ("PATCH", "/api/operations/members/{id}", "none"),
        ("POST", "/api/operations/expenses", "none"),
        ("GET", "/api/operations/expenses/{id}", "none"),
        ("POST", "/api/operations/expenses/{id}/submit", "none"),
        ("POST", "/api/operations/expenses/{id}/approve", "none"),
        ("POST", "/api/operations/cash-sessions/open", "none"),
        ("GET", "/api/operations/cash-sessions/{id}", "none"),
        ("POST", "/api/operations/check-ins", "none"),
        ("GET", "/api/operations/check-ins/{id}", "none"),
        ("POST", "/api/operations/pt-sales", "none"),
        ("GET", "/api/operations/pt-sales/{id}", "none"),
        ("POST", "/api/operations/pt-sales/{id}/submit", "none"),
        ("POST", "/api/operations/pt-sales/{id}/approve", "none"),
        ("GET", "/api/operations/reports/daily", "date"),
        ("PUT", "/api/operations/gym-memberships/{id}/roles", "none"),
    )
    command = (
        ROOT / "backend/apps/tenants/management/commands/staging_security_characterization.py"
    ).read_text(encoding="utf-8")
    for method, template, query in BFF_ROUTES:
        assert template.split("{id}", 1)[0] in command
        assert_bff_path(*_rendered_route(method, template, query))
    assert command.count('"/api/v1/') == 1


def test_bff_path_rejects_targets_outside_the_catalog() -> None:
    rejected = (
        ("GET", "/api/evil"),
        ("POST", "/api/v1/members/"),
        ("GET", "/api/v1/members"),
        ("GET", "/api/auth/login"),
        ("POST", "/api/auth/login/"),
        ("DELETE", "/api/operations/members"),
        ("POST", "/api/operations/members"),
        ("GET", "/api/operations/members/"),
        ("GET", f"/api/operations/members/{_RESOURCE_ID}/"),
        ("PATCH", f"/api/operations/members/{_RESOURCE_ID.upper()}"),
        ("GET", "/api/operations/members/not-a-uuid"),
        ("GET", "/api/operations/members/123e4567-e89b-02d3-a456-426614174000"),
        ("GET", f"/api/operations/members/{_RESOURCE_ID}?page=1"),
        ("GET", "/api/operations/reports/daily"),
        ("GET", "/api/operations/reports/daily?date=2026-10-21&limit=1"),
        ("GET", "/api/operations/reports/daily?date=2026-10-21&date=2026-10-22"),
        ("GET", "/api/operations/reports/daily?date=2026-99-99"),
        ("GET", "/api/operations/reports/daily?date=2026-02-31"),
        ("GET", "/api/operations/reports/daily?Date=2026-10-21"),
        ("POST", "/api/operations/reports/daily?date=2026-10-21"),
        ("POST", "/api/operations/pt-sales/" + _RESOURCE_ID + "/delete"),
        ("POST", "/api/v1/auth/login/"),
        ("POST", "https://localhost/api/auth/login"),
        ("POST", "https://user:pass@localhost/api/auth/login"),
        ("POST", "//localhost/api/auth/login"),
        ("POST", "/api/operations/../expenses"),
        ("POST", "/api/operations/%2e%2e/expenses"),
        ("POST", "/api/auth/login#fragment"),
        ("GET", "/api/operations/reports/daily?date=2026-10-21#x"),
        ("GET", "/api/operations/members?date=2026-10-21"),
        ("PUT", "/api/operations/gym-memberships/" + _RESOURCE_ID),
    )
    for method, path in rejected:
        with pytest.raises(SecuritySpecError):
            assert_bff_path(method, path)
    with pytest.raises(SecuritySpecError):
        assert_bff_path(True, "/api/auth/login")
    with pytest.raises(SecuritySpecError):
        assert_bff_path("POST", None)


def test_make_key_uses_the_command_kind_catalog() -> None:
    command = (
        ROOT / "backend/apps/tenants/management/commands/staging_security_characterization.py"
    ).read_text(encoding="utf-8")
    assert KEY_KINDS == {
        "cash-open",
        "checkin",
        "checkin-cross",
        "expense",
        "expense-mass",
        "expense-submit",
        "idor-expense",
        "idor-pt",
        "manager-expense",
        "manager-expense-submit",
        "manager-self",
        "member-patch",
        "pt-sale",
        "pt-submit",
        "self-expense",
        "self-pt",
        "trainer-expense",
    }
    for kind in sorted(KEY_KINDS):
        assert f'"{kind}"' in command
        assert make_key(kind, 1, 0) == f"sec-{kind}-g1-0000"
    assert make_key("expense", 0, 1) == "sec-expense-g0-0001"
    literals = set(re.findall(r'make_key\(\s*"([^"]+)"', command))
    assert literals <= KEY_KINDS
    for gym in (True, False, "0", 2, None, 1.0):
        with pytest.raises(SecuritySpecError):
            make_key("expense", gym, 0)
    for ordinal in (True, False, -1, 21, "0", None):
        with pytest.raises(SecuritySpecError):
            make_key("expense", 0, ordinal)
    with pytest.raises(SecuritySpecError):
        make_key("not-a-kind", 0, 0)
    with pytest.raises(SecuritySpecError):
        make_key(True, 0, 0)


def test_credential_in_body_detects_jwt_material_and_ignores_benign_text() -> None:
    assert credential_in_body(_BARE_JWT) is True
    assert credential_in_body(b'{"detail":"' + _BARE_JWT + b'"}') is True
    assert credential_in_body(b'{"access_token":"x"}') is True
    assert credential_in_body(b'{"detail":"The member is active."}') is False
    assert credential_in_body(b"version 1.2.3 is current") is False
    assert credential_in_body(b"eyJshort.short.short") is False
    assert credential_in_body("not-bytes") is True  # type: ignore[arg-type]


def test_valid_resource_id_requires_a_canonical_uuid() -> None:
    generated = str(uuid.uuid4())
    assert valid_resource_id(generated) == generated
    assert valid_resource_id(_RESOURCE_ID) == _RESOURCE_ID
    malformed = (
        "",
        "not-a-uuid",
        _RESOURCE_ID.upper(),
        _RESOURCE_ID.replace("-", ""),
        "{" + _RESOURCE_ID + "}",
        "urn:uuid:" + _RESOURCE_ID,
        _RESOURCE_ID + " ",
        "123e4567-e89b-02d3-a456-426614174000",
        "123e4567-e89b-42d3-7456-426614174000",
        _RESOURCE_ID + "/../evil",
        "123e4567-e89b-42d3-a456-42661417400",
        None,
        True,
        1,
    )
    for value in malformed:
        assert valid_resource_id(value) == ""


def test_empty_seed_baseline_is_not_a_pass() -> None:
    status, matched, corruption = assess_security([], [_baseline(0), _baseline(1)], cancelled=0)
    assert status == "not-run"
    assert matched is False
    assert corruption is False
    report = assemble(
        outcomes=[],
        snapshots=[_baseline(0), _baseline(1)],
        cancelled=0,
        wall_seconds=0,
        metrics=(False, None),
    )
    assert report["local_security_status"] == "not-run"
    sealed = seal_evidence(report, [])
    assert sealed["penetration_test_proven"] is False
    assert verify_checksum(sealed) is True


def test_completed_matrix_can_pass_and_rejects_proof_flags() -> None:
    outcomes = _passing_outcomes()
    report = assemble(
        outcomes=outcomes,
        snapshots=[_row(0), _row(1)],
        cancelled=0,
        wall_seconds=1.5,
        metrics=(True, 0),
    )
    assert report["local_security_status"] == "pass"
    assert report["local_isolation_status"] == "pass"
    assert report["local_soak_status"] == "not-run"
    assert report["data_corruption"] is False
    sealed = seal_evidence(report, [])
    assert verify_checksum(sealed) is True
    sealed["penetration_test_proven"] = True
    with pytest.raises(SecuritySpecError):
        seal_evidence(sealed, [])


def test_partial_run_is_fail_without_corruption() -> None:
    outcomes = _passing_outcomes()[:9]
    status, matched, corruption = assess_security(
        outcomes, [_baseline(0), _baseline(1)], cancelled=PLAN_OPERATIONS - 9
    )
    assert status == "fail"
    assert matched is True
    assert corruption is False


def test_extra_row_or_changed_field_is_corruption() -> None:
    outcomes = _passing_outcomes()
    extra, matched, corruption = assess_security(
        outcomes, [_row(0, expenses=3), _row(1)], cancelled=0
    )
    assert extra == "fail"
    assert matched is False
    assert corruption is True
    changed, _matched, notes = assess_security(
        outcomes, [_row(0, member_notes_blank=False), _row(1)], cancelled=0
    )
    assert changed == "fail"
    assert notes is True
    escalated, _matched, roles = assess_security(
        outcomes, [_row(0, trainer_roles=["OWNER", "TRAINER"]), _row(1)], cancelled=0
    )
    assert escalated == "fail"
    assert roles is True


def test_secret_material_and_failure_document_are_refused() -> None:
    report = failure_document("security", "characterization")
    report["failure_detail"] = "owner@rehearsal.invalid"
    with pytest.raises(SecuritySpecError):
        seal_evidence(report, ["owner@rehearsal.invalid"])
    with pytest.raises(SecuritySpecError):
        failure_document("load", "characterization")


def test_repository_texts_describe_local_security_verification() -> None:
    errors = lint_texts(
        command=(
            ROOT / "backend/apps/tenants/management/commands/staging_security_characterization.py"
        ).read_text(encoding="utf-8"),
        runbook=(ROOT / "docs/runbooks/phase8-increment-12.md").read_text(encoding="utf-8"),
        harness=(ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8"),
    )
    assert errors == []
