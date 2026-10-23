from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DIGEST_A = "sha256:" + "ab" * 32
DIGEST_B = "sha256:" + "cd" * 32


def load():
    scripts = str(ROOT / "scripts")
    postgres = str(ROOT / "infra" / "postgres")
    for entry in (scripts, postgres):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    path = ROOT / "scripts" / "staging_rehearsal.py"
    spec = importlib.util.spec_from_file_location("staging_rehearsal", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("staging rehearsal script is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules["staging_rehearsal"] = module
    spec.loader.exec_module(module)
    return module


def test_repository_lint_passes() -> None:
    module = load()
    assert module.lint_repository(ROOT) == []


def test_preflight_keeps_proof_flags_false() -> None:
    module = load()
    payload = module.run_preflight(ROOT)
    for key in module.PROOF_FLAGS:
        assert payload[key] is False
    assert payload["checks"]["config_validation"]["kind"] == "config"
    assert payload["checks"]["commands_completed"]["status"] == "not-run"
    assert payload["checks"]["runtime_checks"]["kind"] == "runtime"
    assert payload["commands_completed"] is False


def test_compose_env_file_overrides_the_callers_shell(tmp_path: Path) -> None:
    module = load()
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "DJANGO_SECRET_KEY=generated-secret\nRUNTIME_DB_PASSWORD=generated-password\n",
        encoding="utf-8",
    )
    env = {
        "PATH": "/usr/bin",
        "DJANGO_SECRET_KEY": "developer-secret",
        "RUNTIME_DB_PASSWORD": "developer-password",
    }

    command = module._compose(ROOT, env_file, env, ["config", "--quiet"])

    assert env == {"PATH": "/usr/bin"}
    assert command[-2:] == ["config", "--quiet"]


def test_wrong_project_is_refused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="disposable project"):
        module.assert_project("gym-production")


def test_production_state_is_refused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="production"):
        module.assert_host_clear(["gym-production_postgres_data"], [])
    with pytest.raises(module.RehearsalError, match="production"):
        module.assert_host_clear(["gym-staging_api"], [])
    with pytest.raises(module.RehearsalError, match="production directory"):
        module.assert_host_clear([], ["/etc/gym/production"])


def test_existing_rehearsal_volume_is_not_reused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="cleanup is explicit"):
        module.assert_fresh(["gym-staging-rehearsal_postgres_data"])


def test_mutable_image_and_digest_substitution_are_refused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="mutable"):
        module.assert_digest_reference("gym-api:latest")
    with pytest.raises(module.RehearsalError, match="mutable"):
        module.assert_digest_reference("ghcr.io/example/gym-api@" + DIGEST_A)
    expected = {"api": "127.0.0.1:5000/gym-api@" + DIGEST_A}
    with pytest.raises(module.RehearsalError, match="substituted"):
        module.assert_digest_match(expected, {"api": "127.0.0.1:5000/gym-api@" + DIGEST_B})
    module.assert_digest_match(
        expected, {"api": "127.0.0.1:5000/gym-api@" + DIGEST_A, "extra": "ignored"}
    )


def test_unsafe_cleanup_path_and_token_are_refused(tmp_path: Path) -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="token"):
        module.authorize_cleanup(
            token_text="destroy-temporary-staging",
            state_dir=ROOT / ".staging-rehearsal",
            repo=ROOT,
            project=module.PROJECT,
        )
    outside = tmp_path / "state"
    outside.mkdir()
    with pytest.raises(module.RehearsalError, match="allowed"):
        module.authorize_cleanup(
            token_text=module.CLEANUP_TOKEN,
            state_dir=outside,
            repo=ROOT,
            project=module.PROJECT,
        )
    with pytest.raises(module.RehearsalError, match="traversal"):
        module.resolve_state_dir(Path("/tmp/../etc/gym/production"), ROOT)


def test_secret_leak_and_false_proof_flags_are_refused() -> None:
    module = load()
    body = {"checks": {}, "commands_completed": False}
    body["production_deploy_proven"] = True
    with pytest.raises(module.RehearsalError, match="proof flag"):
        module.seal_evidence(body, [])
    leaked = {"kind": "local-disposable-staging-rehearsal", "detail": "canary-secret-value"}
    with pytest.raises(module.RehearsalError, match="secret leak"):
        module.seal_evidence(leaked, ["canary-secret-value"])
    clean = module.seal_evidence(
        {"kind": "local-disposable-staging-rehearsal", "commands_completed": False}, []
    )
    for key in module.PROOF_FLAGS:
        assert clean[key] is False
    encoded = json.dumps(
        {key: value for key, value in clean.items() if key != "checksum"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    assert clean["checksum"] == "sha256:" + digest


def test_occupied_ports_and_public_bindings_are_refused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="occupied"):
        module.assert_ports_free({("127.0.0.1", 8443)}, {("127.0.0.1", 8443)})
    with pytest.raises(module.RehearsalError, match="loopback"):
        module.assert_published_bindings([("0.0.0.0", 80, "caddy")])
    with pytest.raises(module.RehearsalError, match="private"):
        module.assert_published_bindings(
            [
                ("127.0.0.1", 8080, "caddy"),
                ("127.0.0.1", 8443, "caddy"),
                ("127.0.0.1", 5432, "postgres"),
            ]
        )


def test_compose_url_binding_stays_loopback() -> None:
    module = load()
    module._assert_ps_ports(
        json.dumps(
            {
                "Service": "caddy",
                "Publishers": [
                    {"URL": "127.0.0.1:8080", "PublishedPort": 8080},
                    {"URL": "127.0.0.1:8443", "PublishedPort": 8443},
                ],
            }
        )
    )
    with pytest.raises(module.RehearsalError, match="loopback"):
        module._assert_ps_ports(
            json.dumps(
                {
                    "Service": "caddy",
                    "Publishers": [{"URL": "0.0.0.0:80", "PublishedPort": 80}],
                }
            )
        )


def test_generated_reset_keeps_evidence(tmp_path: Path) -> None:
    module = load()
    state = tmp_path / "state"
    for name in ("secrets", "certs", "docker", "evidence"):
        directory = state / name
        directory.mkdir(parents=True)
        os.chmod(directory, 0o700)
    secret = state / "secrets" / "compose.env"
    secret.write_text("X=1\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    evidence = state / "evidence" / "rehearsal.json"
    evidence.write_text("{}\n", encoding="utf-8")
    os.chmod(evidence, 0o600)
    link = state / "secrets" / "link"
    link.symlink_to(secret)
    with pytest.raises(module.RehearsalError, match="symlink"):
        module.reset_generated_files(state)
    link.unlink()
    plugins = state / "docker" / "cli-plugins"
    plugins.symlink_to(tmp_path, target_is_directory=True)
    module.reset_generated_files(state)
    assert not secret.exists()
    assert not plugins.exists()
    assert evidence.read_text(encoding="utf-8") == "{}\n"


def test_remote_docker_host_is_refused() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="unix socket"):
        module.assert_local_docker_host("tcp://127.0.0.1:2375")
    with pytest.raises(module.RehearsalError, match="unix socket"):
        module.assert_local_docker_host("unix:///tmp/../var/run/docker.sock")
    assert (
        module.assert_local_docker_host("unix:///var/run/docker.sock")
        == "unix:///var/run/docker.sock"
    )


def test_staging_blackbox_keeps_tls_verification() -> None:
    module = load()
    source = (ROOT / "infra/observability/blackbox/blackbox.yml").read_text(encoding="utf-8")
    rendered = module.render_staging_blackbox(source)
    assert "server_name: localhost" in rendered
    assert "insecure_skip_verify: false" in rendered
    assert "insecure_skip_verify: true" not in rendered
    assert "app.example.com" not in rendered
    assert module.host_manifest_missing(
        "no matching manifest for linux/arm64/v8 in the manifest list entries"
    )
    assert not module.host_manifest_missing("network timeout")
    assert "application/vnd.oci.image.index.v1+json" in module.MANIFEST_ACCEPT
    assert "manifest.list.v2+json" in module.MANIFEST_ACCEPT
    with pytest.raises(module.RehearsalError, match="TLS verification"):
        module.render_staging_blackbox(
            source.replace("insecure_skip_verify: false", "insecure_skip_verify: true")
        )


def test_missing_local_tls_trust_is_refused(tmp_path: Path) -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="local TLS trust"):
        module.load_rehearsal_ca(None)
    missing = tmp_path / "missing.crt"
    with pytest.raises(module.RehearsalError, match="local TLS trust"):
        module.load_rehearsal_ca(missing)


def test_migration_and_isolation_failures_do_not_set_proof() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="migration failed"):
        module.interpret_migration(
            {
                "exit_code": 1,
                "pending": 0,
                "schema_owner": "gym_migration",
                "runtime_owned_tables": 0,
                "migration_owned_tables": 3,
                "runtime_schema_create": False,
                "duration_seconds": 1,
                "settings_module": "config.settings.migration",
                "role_mode": "MIGRATION",
                "role_user": "gym_migration",
            }
        )
    with pytest.raises(module.RehearsalError, match="duration"):
        module.interpret_migration(
            {
                "exit_code": 0,
                "pending": 0,
                "schema_owner": "gym_migration",
                "runtime_owned_tables": 0,
                "migration_owned_tables": 3,
                "runtime_schema_create": False,
                "duration_seconds": module.MIGRATION_LIMIT_SECONDS + 1,
                "settings_module": "config.settings.migration",
                "role_mode": "MIGRATION",
                "role_user": "gym_migration",
            }
        )
    with pytest.raises(module.RehearsalError, match="isolation"):
        module.interpret_isolation(
            {
                "isolated": False,
                "cross_gym_rows": 1,
                "empty_context_rows": 0,
                "cross_update_rows": 0,
                "runtime_schema_write": "denied",
            }
        )
    passed = module.interpret_migration(
        {
            "exit_code": 0,
            "pending": 0,
            "schema_owner": "gym_migration",
            "runtime_owned_tables": 0,
            "migration_owned_tables": 3,
            "runtime_schema_create": False,
            "duration_seconds": 12,
            "settings_module": "config.settings.migration",
            "role_mode": "MIGRATION",
            "role_user": "gym_migration",
        }
    )
    assert passed["kind"] == "runtime"
    assert passed["status"] == "pass"


def test_command_arguments_cannot_carry_secrets() -> None:
    module = load()
    with pytest.raises(module.RehearsalError, match="secret leak"):
        module.assert_command_has_no_secrets(
            ["docker", "run", "-e", "PASSWORD=canary-secret-value"],
            ["canary-secret-value"],
        )


def test_concurrent_lock_is_refused(tmp_path: Path) -> None:
    module = load()
    lock_path = tmp_path / "rehearsal.lock"
    held = module.acquire_rehearsal_lock(lock_path)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import importlib.util,sys; "
                "spec=importlib.util.spec_from_file_location('staging_rehearsal', sys.argv[1]); "
                "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                "module.acquire_rehearsal_lock(__import__('pathlib').Path(sys.argv[2]))",
                str(ROOT / "scripts" / "staging_rehearsal.py"),
                str(lock_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            _stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("concurrent lock was not refused") from None
        assert proc.returncode != 0
        assert "already running" in stderr
    finally:
        held.close()


def test_local_s3_rehearsal_mode_is_not_offsite(tmp_path: Path) -> None:
    import backup_policy as policy

    env = {
        "BACKUP_REPO_MODE": policy.LOCAL_S3_MODE,
        "COMPOSE_PROJECT_NAME": policy.LOCAL_S3_PROJECT,
        "BACKUP_S3_ENDPOINT": policy.LOCAL_S3_ENDPOINT,
        "BACKUP_S3_REGION": "us-east-1",
        "BACKUP_S3_BUCKET": "gym-staging-rehearsal-backup",
        "BACKUP_S3_URI_STYLE": "path",
        "BACKUP_REPO_PATH": "/gym-platform",
        "BACKUP_STANZA": "gym",
        "BACKUP_PG_USER": "gym_admin",
        "BACKUP_PG_DATABASE": "gym_platform",
    }
    rendered = policy.render_pgbackrest_config(
        env,
        cipher_pass="dedicated-cipher-passphrase",
        access_key="dedicated-access-key-value",
        secret_key="dedicated-secret-key-value",
    )
    assert "repo1-storage-verify-tls=y" in rendered
    assert "repo1-storage-ca-file=/run/secrets/staging_object_ca" in rendered
    assert "repo1-storage-verify-tls=n" not in rendered
    with pytest.raises(policy.BackupPolicyError, match="disposable staging"):
        policy.render_pgbackrest_config(
            {**env, "COMPOSE_PROJECT_NAME": "gym-production"},
            cipher_pass="dedicated-cipher-passphrase",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    with pytest.raises(policy.BackupPolicyError, match="not a restore source"):
        policy.prepare_restore_volumes(
            mode=policy.LOCAL_S3_MODE,
            target="gym-restore-data-abcdef0123456789",
            repo="",
            volume_exists=lambda _name: False,
        )
    ca = tmp_path / "ca.crt"
    ca.write_text("local-ca", encoding="utf-8")
    os.chmod(ca, 0o644)
    policy._require_local_ca(ca)
    world = tmp_path / "world.crt"
    world.write_text("local-ca", encoding="utf-8")
    os.chmod(world, 0o666)
    with pytest.raises(policy.BackupPolicyError, match="unsafe"):
        policy._require_local_ca(world)


def test_owner_only_files_reject_group_access(tmp_path: Path) -> None:
    module = load()
    path = tmp_path / "secret"
    path.write_text("value", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
    with pytest.raises(module.RehearsalError, match="owner-only"):
        module.assert_owner_only(path, directory=False)


def test_symlink_state_is_refused(tmp_path: Path) -> None:
    module = load()
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(module.RehearsalError, match="symlink"):
        module._reject_symlink_components(link)


def test_isolation_judge_rejects_cross_gym_rows() -> None:
    from apps.tenants.management.commands.staging_isolation_smoke import judge_isolation_counts

    failed = judge_isolation_counts(
        visible_a=2,
        visible_b=1,
        empty_rows=0,
        cross_a=1,
        cross_b=0,
        cross_update=0,
        schema_denied=True,
    )
    assert failed["isolated"] is False
    assert failed["production_deploy_proven"] is False
    passed = judge_isolation_counts(
        visible_a=1,
        visible_b=1,
        empty_rows=0,
        cross_a=0,
        cross_b=0,
        cross_update=0,
        schema_denied=True,
    )
    assert passed["isolated"] is True
    assert passed["rpo_proven"] is False


def test_caddy_pull_tag_matches_increment_4() -> None:
    module = load()
    assert module.THIRD_PARTY_TAGS["CADDY_IMAGE"] == "caddy:2.11.7"


def test_rehearsal_image_refs_are_exact() -> None:
    module = load()
    assert module.rehearsal_ref_is_exact("gym-staging-rehearsal/gym-api:build")
    assert module.rehearsal_ref_is_exact("127.0.0.1:32774/gym-web:rehearsal")
    assert not module.rehearsal_ref_is_exact("127.0.0.1:0/gym-api:rehearsal")
    assert not module.rehearsal_ref_is_exact("caddy:2.11.7")
    assert not module.rehearsal_ref_is_exact("gym-api:local")
    with pytest.raises(module.RehearsalError, match="shared image"):
        module.labeled_image_ids_to_remove([("sha256:" + "ab" * 32, "postgres:18.6")])
    dangling = module.labeled_image_ids_to_remove([("sha256:" + "cd" * 32, "<none>:<none>")])
    assert dangling == ["sha256:" + "cd" * 32]


def test_cleanup_token_file_must_be_owner_only(tmp_path: Path) -> None:
    module = load()
    token = tmp_path / "token"
    token.write_text(module.CLEANUP_TOKEN + "\n", encoding="utf-8")
    os.chmod(token, 0o644)
    with pytest.raises(module.RehearsalError, match="unsafe"):
        module.assert_cleanup_token_file(token)
    os.chmod(token, 0o600)
    module.assert_cleanup_token_file(token)
    with pytest.raises(module.RehearsalError, match="empty"):
        module.cleanup_redaction_values("PUBLIC_HOST=localhost\n")
    values = module.cleanup_redaction_values(
        "JWT_KEY_ID=staging-rehearsal\nPUBLIC_HOST=localhost\n"
        "POSTGRES_ADMIN_PASSWORD=" + ("ab" * 32) + "\n"
    )
    assert values == ["ab" * 32]


def test_image_records_are_kept_before_runtime() -> None:
    module = load()
    digest = "sha256:" + "ab" * 32
    records = module.image_records(
        {"API_IMAGE": "127.0.0.1:5000/gym-api@" + digest},
        {"CADDY_IMAGE": "caddy@" + digest},
        {"CADDY_IMAGE": "host", "REGISTRY_IMAGE": "host", "OBJECT_STORE_IMAGE": "host"},
        "registry@" + digest,
    )
    assert records
    assert records[0]["registry_kind"] == "loopback-rehearsal"
    assert any(item["role"] == "registry" for item in records)
    body = {
        "kind": "local-disposable-staging-rehearsal",
        "commands_completed": False,
        "images": records,
        "checks": {},
    }
    sealed = module.seal_evidence(body, [])
    assert sealed["images"]
    assert sealed["production_deploy_proven"] is False
    assert sealed["ghcr_proven"] is False


def test_backup_s3_files_use_the_object_root_credentials() -> None:
    module = load()
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert '("backup_object_access", "backup-s3-access-key")' in text
    assert '("backup_object_secret", "backup-s3-secret-key")' in text
    assert '"backup_access"' not in text
    assert '"backup_secret"' not in text


def test_clamav_pong_accepts_only_the_ping_reply() -> None:
    module = load()
    assert module.clamav_pong(b"PONG\x00") is True
    assert module.clamav_pong(b"PONG\n") is True
    assert module.clamav_pong(b"") is False
    assert module.clamav_pong(b"ERROR") is False
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "nPING" in text
    assert "nc -w 5 127.0.0.1 3310" in text
    assert "socket.create_connection(('clamav',3310),5)" in text
    assert "execute_run(repo, state_dir, require_clamav_tcp=False)" in text
    assert "require_clamav_tcp: bool = True" in text
    assert "require_clamav_tcp=require_clamav_tcp" in text
    assert "CLAMAV_TCP_TIMEOUT" in text
    overlay = (ROOT / "infra/compose/docker-compose.staging-rehearsal.yml").read_text(
        encoding="utf-8"
    )
    assert "CLAMAV_NO_FRESHCLAMD" in overlay
    assert "CLAMD_CONF_TCPAddr" not in overlay


def test_observability_and_backup_must_pass() -> None:
    module = load()
    healthy = {
        "prometheus_ready": True,
        "alert_delivery_proven": False,
        "targets": [f"{job}:up" for job in module.REQUIRED_TARGET_JOBS],
    }
    module.interpret_observability(healthy)
    with pytest.raises(module.RehearsalError, match="not healthy"):
        module.interpret_observability(
            {**healthy, "targets": [f"{job}:down" for job in module.REQUIRED_TARGET_JOBS]}
        )
    local_backup = {
        "bootstrap_exit": 0,
        "full_exit": 0,
        "local_only": True,
        "offsite_backup_proven": False,
        "rpo_proven": False,
        "rto_proven": False,
    }
    module.interpret_backup(local_backup)
    with pytest.raises(module.RehearsalError, match="local backup"):
        module.interpret_backup({**local_backup, "full_exit": 1})


def test_write_all_retries_short_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    path = tmp_path / "out"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    original = os.write

    def short(target: int, data: bytes) -> int:
        if target == fd:
            return original(target, data[:1])
        return original(target, data)

    monkeypatch.setattr(module.os, "write", short)
    try:
        module._write_all(fd, b"abcdef")
    finally:
        os.close(fd)
    assert path.read_bytes() == b"abcdef"


def test_rehearsal_gate_failure_keeps_passing_pt(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    original = '{"local_pt_status": "pass"}\n'
    target = evidence / "pt.json"
    target.write_text(original, encoding="utf-8")
    module._seal_pt_failure(tmp_path, object(), "rehearsal")
    assert target.read_text(encoding="utf-8") == original


def test_pt_cash_rehearsal_sidecar_keeps_prior_seals(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = '{"checksum": "sha256:prior"}\n'
    (evidence / "rehearsal.json").write_text(prior, encoding="utf-8")
    (evidence / "rehearsal.json").write_text('{"checksum": "sha256:fresh"}\n', encoding="utf-8")
    module._restore_prior_rehearsal(tmp_path, prior, sidecar="rehearsal-pt-cash.json")
    assert (evidence / "rehearsal.json").read_text(encoding="utf-8") == prior
    assert "fresh" in (evidence / "rehearsal-pt-cash.json").read_text(encoding="utf-8")
    assert not (evidence / "rehearsal-pt.json").exists()
    original = '{"local_pt_cash_status": "pass"}\n'
    cash = evidence / "pt-cash.json"
    cash.write_text(original, encoding="utf-8")
    module._seal_pt_cash_failure(tmp_path, object(), "rehearsal")
    assert cash.read_text(encoding="utf-8") == original


def test_endurance_rehearsal_sidecar_keeps_prior_seals(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = '{"checksum": "sha256:prior"}\n'
    (evidence / "rehearsal.json").write_text(prior, encoding="utf-8")
    (evidence / "rehearsal.json").write_text('{"checksum": "sha256:fresh"}\n', encoding="utf-8")
    module._restore_prior_rehearsal(tmp_path, prior, sidecar="rehearsal-endurance.json")
    assert (evidence / "rehearsal.json").read_text(encoding="utf-8") == prior
    assert "fresh" in (evidence / "rehearsal-endurance.json").read_text(encoding="utf-8")
    assert not (evidence / "rehearsal-pt.json").exists()
    original = '{"local_endurance_status": "pass"}\n'
    target = evidence / "endurance.json"
    target.write_text(original, encoding="utf-8")
    module._seal_endurance_failure(tmp_path, object(), "rehearsal")
    assert target.read_text(encoding="utf-8") == original


def test_security_rehearsal_sidecar_keeps_prior_seals(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = '{"checksum": "sha256:prior"}\n'
    (evidence / "rehearsal.json").write_text(prior, encoding="utf-8")
    (evidence / "rehearsal.json").write_text('{"checksum": "sha256:fresh"}\n', encoding="utf-8")
    module._restore_prior_rehearsal(tmp_path, prior, sidecar="rehearsal-security.json")
    assert (evidence / "rehearsal.json").read_text(encoding="utf-8") == prior
    assert "fresh" in (evidence / "rehearsal-security.json").read_text(encoding="utf-8")
    assert not (evidence / "rehearsal-endurance.json").exists()
    original = '{"local_security_status": "pass"}\n'
    target = evidence / "security.json"
    target.write_text(original, encoding="utf-8")
    module._seal_security_failure(tmp_path, object(), "rehearsal")
    assert target.read_text(encoding="utf-8") == original


def test_identity_rehearsal_sidecar_keeps_prior_seals(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = '{"checksum": "sha256:prior"}\n'
    (evidence / "rehearsal.json").write_text(prior, encoding="utf-8")
    (evidence / "rehearsal.json").write_text('{"checksum": "sha256:fresh"}\n', encoding="utf-8")
    module._restore_prior_rehearsal(tmp_path, prior, sidecar="rehearsal-identity-security.json")
    assert (evidence / "rehearsal.json").read_text(encoding="utf-8") == prior
    assert "fresh" in (evidence / "rehearsal-identity-security.json").read_text(encoding="utf-8")
    assert not (evidence / "rehearsal-security.json").exists()
    original = '{"local_identity_status": "pass"}\n'
    target = evidence / "identity-security.json"
    security = evidence / "security.json"
    target.write_text(original, encoding="utf-8")
    security.write_text('{"local_security_status": "pass"}\n', encoding="utf-8")
    module._seal_identity_failure(tmp_path, object(), "rehearsal")
    assert target.read_text(encoding="utf-8") == original
    assert security.read_text(encoding="utf-8") == '{"local_security_status": "pass"}\n'

    class _Rules:
        class IdentitySpecError(Exception):
            pass

        def failure_document(self, profile: str, stage: str) -> dict[str, str]:
            assert profile == "identity-security"
            return {"local_identity_status": "fail", "failure_stage": stage}

        def seal_evidence(self, body: dict[str, str], secrets: list[str]) -> dict[str, str]:
            assert secrets == []
            body["checksum"] = "sha256:" + "ab" * 32
            return body

    target.unlink()
    module._seal_identity_failure(tmp_path, _Rules(), "characterization")
    written = evidence / "identity-security.json"
    assert written.stat().st_mode & 0o777 == 0o600
    assert "characterization" in written.read_text(encoding="utf-8")
    assert security.read_text(encoding="utf-8") == '{"local_security_status": "pass"}\n'
    for name in ("secrets", "certs", "docker"):
        directory = tmp_path / name
        directory.mkdir()
        os.chmod(directory, 0o700)
    module.remove_generated_trees(tmp_path)
    assert written.is_file()
    assert security.is_file()
    assert not (tmp_path / "secrets").exists()


def test_isolation_rehearsal_sidecar_keeps_prior_seals(tmp_path: Path) -> None:
    module = load()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prior = '{"checksum": "sha256:prior"}\n'
    (evidence / "rehearsal.json").write_text(prior, encoding="utf-8")
    (evidence / "rehearsal.json").write_text('{"checksum": "sha256:fresh"}\n', encoding="utf-8")
    module._restore_prior_rehearsal(tmp_path, prior, sidecar="rehearsal-isolation.json")
    assert (evidence / "rehearsal.json").read_text(encoding="utf-8") == prior
    assert "fresh" in (evidence / "rehearsal-isolation.json").read_text(encoding="utf-8")
    assert not (evidence / "rehearsal-identity-security.json").exists()
    original = '{"local_isolation_status": "pass"}\n'
    target = evidence / "isolation.json"
    identity = evidence / "identity-security.json"
    target.write_text(original, encoding="utf-8")
    identity.write_text('{"local_identity_status": "pass"}\n', encoding="utf-8")
    module._seal_isolation_failure(tmp_path, object(), "rehearsal")
    assert target.read_text(encoding="utf-8") == original
    assert identity.read_text(encoding="utf-8") == '{"local_identity_status": "pass"}\n'

    class _Rules:
        class IsolationSpecError(Exception):
            pass

        def failure_document(self, profile: str, stage: str) -> dict[str, str]:
            assert profile == "isolation"
            return {"local_isolation_status": "fail", "failure_stage": stage}

        def seal_evidence(self, body: dict[str, str], secrets: list[str]) -> dict[str, str]:
            assert secrets == []
            body["checksum"] = "sha256:" + "ab" * 32
            return body

    target.unlink()
    module._seal_isolation_failure(tmp_path, _Rules(), "characterization")
    written = evidence / "isolation.json"
    assert written.stat().st_mode & 0o777 == 0o600
    assert "characterization" in written.read_text(encoding="utf-8")
    assert identity.read_text(encoding="utf-8") == '{"local_identity_status": "pass"}\n'
    for name in ("secrets", "certs", "docker"):
        directory = tmp_path / name
        directory.mkdir()
        os.chmod(directory, 0o700)
    module.remove_generated_trees(tmp_path)
    assert written.is_file()
    assert identity.is_file()
    assert not (tmp_path / "secrets").exists()
