from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from apps.common.identity_security_characterization import (
    BFF_ROUTES,
    CADDY_ROUTES,
    PLAN_OPERATIONS,
    PRIVATE_FORGOT,
    PRIVATE_ROUTES,
    PROOF_FLAGS,
    RULES,
    UNCOVERED,
    IdentitySpecError,
    assemble,
    assert_identity_route,
    assert_private_target,
    assess_identity,
    build_plan,
    evidence_has_credential_material,
    failure_document,
    lint_texts,
    location_allowed,
    presented_material_echoed,
    public_outcome,
    require_int,
    resolve_profile,
    seal_evidence,
    unexpected_credential,
    valid_identity_id,
    valid_presented_token,
    verify_checksum,
)

ROOT = Path(__file__).resolve().parents[3]
_RESOURCE_ID = "123e4567-e89b-42d3-a456-426614174000"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"
_MEMBER_BODY = (
    f'{{"access_token":"{_JWT}","refresh_token":"member-refresh-token-value-0001"}}'
).encode()
_ENROLL_BODY = (
    b'{"secret":"JBSWY3DPEHPK3PXP","enrollment_token":"enrollment-token-value-0001",'
    b'"otpauth_uri":"otpauth://totp/example"}'
)


def _baseline(gym: int) -> dict[str, object]:
    return {
        "gym": gym,
        "member_status": "ACTIVE",
        "member_linked": True,
        "member_live_sessions": 0,
        "owner_password_changed": False,
        "owner_live_sessions": 0,
        "reset_live": 0,
        "reset_consumed": False,
        "invitation_pending": 0,
        "invitation_accepted": 0,
        "accepted_owner": False,
        "accepted_trainer": False,
        "owner_is_owner": True,
        "manager_is_owner": False,
        "mfa_enabled": False,
    }


def _finished(gym: int) -> dict[str, object]:
    row = _baseline(gym)
    row.update(
        {
            "member_live_sessions": 0,
            "owner_password_changed": True,
            "owner_live_sessions": 0,
            "reset_live": 0,
            "reset_consumed": True,
            "invitation_pending": 1,
            "invitation_accepted": 1,
            "accepted_trainer": True,
            "mfa_enabled": True,
        }
    )
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
        token_sources_used=True,
    )


def test_plan_bounds_and_profile_are_fixed() -> None:
    assert resolve_profile("identity-security") == "identity-security"
    assert PLAN_OPERATIONS == 72
    assert require_int(72, minimum=1, maximum=80) == 72
    with pytest.raises(IdentitySpecError):
        resolve_profile("security")
    with pytest.raises(IdentitySpecError):
        require_int(True, minimum=0, maximum=10)
    plan = build_plan()
    assert len(plan) == 72
    edge = [row for row in plan if row["phase"] == "edge"]
    gyms = [row for row in plan if row["phase"] == "gym"]
    assert len(edge) == 18
    assert all(row["gym"] is None for row in edge)
    assert [row["gym"] for row in gyms].count(0) == 21
    assert [row["gym"] for row in gyms].count(1) == 21
    assert gyms[0]["scenario"] == "member_login"
    later = [row for row in plan if row["phase"] in {"logout", "mfa"}]
    assert [row["scenario"] for row in later[:3]] == [
        "member_logout_login",
        "member_logout",
        "member_logout_replay",
    ]
    assert len(later) == 12
    assert all(row["gym"] == 0 for row in later)


def test_route_catalog_rejects_anything_outside_the_fixed_paths() -> None:
    assert CADDY_ROUTES == (
        ("POST", "/api/v1/member-auth/login/"),
        ("POST", "/api/v1/member-auth/refresh/"),
        ("POST", "/api/v1/member-auth/logout/"),
        ("GET", "/api/v1/member/me/"),
        ("GET", "/api/v1/members/"),
    )
    assert ("POST", "/api/auth/password/forgot") in BFF_ROUTES
    assert ("POST", "/api/auth/mfa/challenge") in BFF_ROUTES
    assert ("POST", "/api/operations/auth/mfa/enroll") in BFF_ROUTES
    assert PRIVATE_ROUTES == (
        ("POST", "/api/v1/auth/password/forgot/"),
        ("POST", "/api/v1/auth/password/reset/"),
        ("POST", "/api/v1/staff/invitations/accept/"),
    )
    for transport, routes in (
        ("caddy", CADDY_ROUTES),
        ("bff", BFF_ROUTES),
        ("private", PRIVATE_ROUTES),
    ):
        for method, path in routes:
            assert_identity_route(method, path, transport)
    rejected = (
        ("GET", "/api/v1/member-auth/login/", "caddy"),
        ("POST", "/api/v1/member-auth/login/?next=1", "caddy"),
        ("POST", "/api/v1/member-auth/login/#fragment", "caddy"),
        ("POST", "/api/v1/../member-auth/login/", "caddy"),
        ("POST", "https://untrusted.example/api/auth/login", "bff"),
        ("POST", PRIVATE_FORGOT, "caddy"),
        ("POST", "/api/v1/auth/login/", "private"),
        (None, "/api/auth/login", "bff"),
    )
    for method, path, transport in rejected:
        with pytest.raises(IdentitySpecError):
            assert_identity_route(method, path, transport)
    assert_private_target("api", 8000, PRIVATE_FORGOT, ["127.0.0.1"])
    with pytest.raises(IdentitySpecError):
        assert_private_target("localhost", 8000, PRIVATE_FORGOT, ["127.0.0.1"])
    with pytest.raises(IdentitySpecError):
        assert_private_target("api", 8000, "/api/v1/auth/login/", ["10.0.0.8"])
    with pytest.raises(IdentitySpecError):
        assert_private_target("api", 8000, PRIVATE_FORGOT, ["8.8.8.8"])


def test_identity_validators_reject_noncanonical_material() -> None:
    generated = str(uuid.uuid4())
    assert valid_identity_id(generated) == generated
    assert valid_identity_id(_RESOURCE_ID) == _RESOURCE_ID
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
        assert valid_identity_id(value) == ""
    assert valid_presented_token("a" * 32) is True
    assert valid_presented_token("a" * 128) is True
    assert valid_presented_token("eyJ" + "a" * 29) is False
    assert valid_presented_token("a" * 31) is False
    assert valid_presented_token("a" * 32 + "@") is False
    assert valid_presented_token("a" * 16 + " " + "b" * 16) is False
    assert location_allowed("/reset-password") is True
    assert location_allowed("https://localhost:8443/reset-password") is True
    assert location_allowed("https://untrusted.example/reset-password") is False
    assert location_allowed("https://user:secret@localhost:8443/reset-password") is False
    assert location_allowed("https://localhost:8443/reset-password?token=abc") is False
    assert location_allowed("//untrusted.example/reset-password") is False


def test_credential_detection_allows_only_the_contract_bodies() -> None:
    assert unexpected_credential(_MEMBER_BODY, "member_login") is False
    assert unexpected_credential(_MEMBER_BODY, "member_refresh") is False
    assert unexpected_credential(_MEMBER_BODY, "member_logout_login") is False
    assert unexpected_credential(_MEMBER_BODY, "forgot_unknown") is True
    assert unexpected_credential(_ENROLL_BODY, "mfa_enroll") is False
    assert unexpected_credential(_ENROLL_BODY, "forgot_unknown") is True
    assert unexpected_credential(b'{"invitation_token":"guess"}', "invite_guess") is True
    assert unexpected_credential(_JWT.encode(), "member_me") is True
    assert unexpected_credential(_JWT.encode(), "member_login") is False
    assert presented_material_echoed(b"prefix-secret-value-suffix", [b"secret-value"]) is True
    assert presented_material_echoed(b"short", [b"short"]) is False
    assert presented_material_echoed("not-bytes", [b"secret-value"]) is True  # type: ignore[arg-type]
    assert evidence_has_credential_material("access_token") is True
    assert evidence_has_credential_material("scenario member_login") is False


def test_empty_baseline_is_not_run_and_a_partial_matrix_fails() -> None:
    snapshots = [_baseline(0), _baseline(1)]
    status, matched, corruption = assess_identity([], snapshots, cancelled=72)
    assert status == "not-run"
    assert matched is False
    assert corruption is False
    report = assemble(
        outcomes=[],
        snapshots=snapshots,
        cancelled=0,
        wall_seconds=0,
        metrics=(False, None),
        token_sources_used=False,
    )
    assert report["local_identity_status"] == "not-run"
    assert report["local_security_status"] == "not-run"
    assert report["local_soak_status"] == "not-run"
    assert report["local_isolation_status"] == "not-run"
    assert all(report[flag] is False for flag in PROOF_FLAGS)
    sealed = seal_evidence(report, [])
    assert sealed["local_identity_status"] == "not-run"
    assert verify_checksum(sealed) is True
    edge = _passing()[:18]
    status, matched, corruption = assess_identity(edge, snapshots, cancelled=0)
    assert status == "fail"
    assert matched is True
    assert corruption is False


def test_full_matrix_passes_only_with_finished_counts_and_mailbox_source() -> None:
    report = _pass_body()
    assert report["local_identity_status"] == "pass"
    assert report["completed_operations"] == 72
    assert report["cancelled_operations"] == 0
    assert report["credential_exposure"] == 0
    assert report["cross_gym_leakage"] == 0
    assert report["data_corruption"] is False
    assert report["token_sources"] == {
        "password_reset": "service-local-mailbox",
        "staff_invitation_accept": "service-local-mailbox",
    }
    assert report["local_security_status"] == "not-run"
    assert [row["id"] for row in report["uncovered"]] == [item["id"] for item in UNCOVERED]
    assert all(row["covered"] is False for row in report["uncovered"])
    sealed = seal_evidence(report, [])
    assert verify_checksum(sealed) is True
    sealed["checksum"] = "sha256:" + "00" * 32
    assert verify_checksum(sealed) is False
    changed = seal_evidence(_pass_body(), [])
    changed["wall_time_seconds"] = 2
    assert verify_checksum(changed) is False


def test_protected_rows_are_corruption_and_a_forged_pass_is_refused() -> None:
    outcomes = _passing()
    pending = _finished(0)
    pending["invitation_pending"] = 3
    status, _matched, corruption = assess_identity(outcomes, [pending, _finished(1)], cancelled=0)
    assert status == "fail"
    assert corruption is True
    flipped = _finished(0)
    flipped["owner_is_owner"] = False
    status, _matched, corruption = assess_identity(outcomes, [flipped, _finished(1)], cancelled=0)
    assert corruption is True
    early = _baseline(0)
    early["owner_password_changed"] = True
    status, _matched, corruption = assess_identity([], [early, _baseline(1)], cancelled=0)
    assert status == "fail"
    assert corruption is True
    with pytest.raises(IdentitySpecError):
        failure_document("security", "characterization")
    forged = assemble(
        outcomes=[],
        snapshots=[_baseline(0), _baseline(1)],
        cancelled=0,
        wall_seconds=0,
        metrics=(False, None),
        token_sources_used=False,
    )
    forged["local_identity_status"] = "pass"
    with pytest.raises(IdentitySpecError, match="disagrees"):
        seal_evidence(forged, [])
    leaked = _pass_body()
    leaked["failure_detail"] = "owner@rehearsal.invalid"
    with pytest.raises(IdentitySpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = _JWT
    with pytest.raises(IdentitySpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = "access_token"
    with pytest.raises(IdentitySpecError, match="secret"):
        seal_evidence(leaked, [])
    leaked["failure_detail"] = ""
    leaked["penetration_test_proven"] = True
    with pytest.raises(IdentitySpecError, match="proof"):
        seal_evidence(leaked, [])
    leaked["penetration_test_proven"] = False
    leaked["local_security_status"] = "pass"
    with pytest.raises(IdentitySpecError, match="security status"):
        seal_evidence(leaked, [])


def test_lint_wires_the_command_runbook_and_harness() -> None:
    command = (
        ROOT
        / "backend/apps/tenants/management/commands/staging_identity_security_characterization.py"
    ).read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/phase8-increment-13.md").read_text(encoding="utf-8")
    harness = (ROOT / "scripts/staging_rehearsal.py").read_text(encoding="utf-8")
    assert "add_argument" not in command
    assert "argparse" not in command
    assert "mfa_secret_encrypted=encrypt_secret(generate_totp_secret())" in command
    assert "filter(id=closed_user_id).update(mfa_enabled=True)" not in command
    snapshot = command[command.index("def _snapshot") : command.index("def _scrape")]
    assert snapshot.index("with transaction.atomic():") < snapshot.index("set_gym_context(")
    assert lint_texts(command=command, runbook=runbook, harness=harness) == []
    assert "does not replace security.json" in harness
    assert "staging_identity_security_characterization" in harness
