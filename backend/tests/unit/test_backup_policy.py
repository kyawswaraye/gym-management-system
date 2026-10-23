from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load():
    path = ROOT / "infra" / "postgres" / "backup_policy.py"
    spec = importlib.util.spec_from_file_location("backup_policy", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("backup policy module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def production_env() -> dict[str, str]:
    return {
        "BACKUP_STANZA": "gym",
        "BACKUP_PG_USER": "gym_admin",
        "BACKUP_PG_DATABASE": "gym_platform",
        "BACKUP_REPO_PATH": "/gym-platform",
        "BACKUP_S3_ENDPOINT": "https://backup.object-store.test",
        "BACKUP_S3_REGION": "ap-southeast-1",
        "BACKUP_S3_BUCKET": "gym-backup-bucket",
        "BACKUP_S3_URI_STYLE": "host",
    }


def restore_env() -> dict[str, str]:
    return {
        "RESTORE_CONFIRMATION": "restore-disposable",
        "RESTORE_TARGET": "disposable",
        "RESTORE_PROJECT": "gym-restore-rehearsal01",
        "RESTORE_VOLUME": "gym-restore-data-rehearsal01",
        "RESTORE_REPO_VOLUME": "gym-restore-repo-rehearsal01",
        "RESTORE_STANZA": "gym",
        "RESTORE_REPOSITORY_ID": "posix:rehearsal",
        "BACKUP_REPO_MODE": "rehearsal-posix",
        "BACKUP_PGDATA": "/var/lib/gym-restore",
    }


def test_repository_backup_baseline_passes() -> None:
    assert load().lint_backup_baseline(ROOT, _compose()) == []


def _compose() -> dict:
    artifacts = ROOT / "scripts" / "production_artifacts.py"
    spec = importlib.util.spec_from_file_location("production_artifacts", artifacts)
    if spec is None or spec.loader is None:
        raise RuntimeError("production artifact module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_simple_yaml(
        (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    )


def test_archive_mode_and_posix_and_credential_reuse_fail() -> None:
    module = load()
    document = _compose()
    text = (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    command = document["services"]["postgres"]["command"]
    command[command.index("archive_mode=on")] = "archive_mode=off"
    assert any("archive_mode" in error for error in module.lint_production_compose(text, document))
    document = _compose()
    assert any(
        "filesystem" in error
        for error in module.lint_production_compose(text + "\n# rehearsal-posix\n", document)
    )
    document["services"]["backup-full"]["environment"]["MINIO_SECRET_KEY"] = (
        "${MINIO_SECRET_KEY:?Set MINIO_SECRET_KEY}"
    )
    errors = module.lint_production_compose(text, document)
    assert any("MINIO_SECRET_KEY" in error or "allow-list" in error for error in errors)
    document = _compose()
    document["services"]["backup-check"]["environment"]["POSTGRES_ADMIN_PASSWORD"] = (
        "${POSTGRES_ADMIN_PASSWORD:?Set POSTGRES_ADMIN_PASSWORD}"
    )
    assert any(
        "POSTGRES_ADMIN_PASSWORD" in error or "allow-list" in error
        for error in module.lint_production_compose(text, document)
    )


def test_tool_version_and_mutable_base_fail() -> None:
    module = load()
    text = (ROOT / "infra/postgres/Dockerfile").read_text(encoding="utf-8")
    assert module.lint_postgres_dockerfile(text) == []
    drifted = text.replace("2.60.0", "2.59.0")
    assert any("drifted" in error for error in module.lint_postgres_dockerfile(drifted))
    mutable = "FROM postgres:latest\n" + text
    assert module.lint_postgres_dockerfile(mutable)


def test_encrypted_offsite_config_rejects_local_and_reused_secrets(tmp_path: Path) -> None:
    module = load()
    config = module.render_pgbackrest_config(
        production_env(),
        cipher_pass="dedicated-cipher-passphrase",
        access_key="dedicated-access-key-value",
        secret_key="dedicated-secret-key-value",
    )
    assert "repo1-type=s3" in config
    assert "repo1-cipher-type=aes-256-cbc" in config
    assert "repo1-retention-full=14" in config
    assert "dedicated-cipher-passphrase" in config
    leaked = production_env()
    leaked["MINIO_SECRET_KEY"] = "application-secret"
    with pytest.raises(module.BackupPolicyError, match="application"):
        module.render_pgbackrest_config(
            leaked,
            cipher_pass="dedicated-cipher-passphrase",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    local = production_env()
    local["BACKUP_S3_ENDPOINT"] = "https://minio:9000"
    with pytest.raises(module.BackupPolicyError):
        module.render_pgbackrest_config(
            local,
            cipher_pass="dedicated-cipher-passphrase",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    with pytest.raises(module.BackupPolicyError, match="placeholder"):
        module.render_pgbackrest_config(
            production_env(),
            cipher_pass="replace-from-secret-store",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    database = production_env()
    database["POSTGRES_PASSWORD"] = "database-admin-password-value"
    rendered = module.render_pgbackrest_config(
        database,
        cipher_pass="dedicated-cipher-passphrase",
        access_key="dedicated-access-key-value",
        secret_key="dedicated-secret-key-value",
        database_passwords=True,
    )
    assert "database-admin-password-value" not in rendered
    with pytest.raises(module.BackupPolicyError, match="application"):
        module.render_pgbackrest_config(
            database,
            cipher_pass="dedicated-cipher-passphrase",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    target = tmp_path / "config"
    target.mkdir()
    written = module.write_config_file(target, config)
    assert oct(written.stat().st_mode & 0o777) == "0o600"


def test_freshness_fails_closed_without_claiming_rpo() -> None:
    module = load()
    info = [
        {
            "name": "gym",
            "cipher": "aes-256-cbc",
            "backup": [
                {
                    "type": "full",
                    "error": False,
                    "label": "20261021-000000F",
                    "timestamp": {"start": 100, "stop": 200},
                }
            ],
        }
    ]
    fresh = module.evaluate_freshness(
        info=info,
        archiver={"archive_mode": "on", "last_archived_epoch": 950, "archived_count": 2},
        now=1000,
        max_backup_age=86400,
        max_wal_age=900,
        version_line=module.PGBACKREST_VERSION_LINE,
        repo_mode="s3",
    )
    assert fresh["result"] == "ok"
    assert fresh["rpo_proven"] is False
    assert fresh["rto_proven"] is False
    assert fresh["offsite"] is True
    stale = module.evaluate_freshness(
        info=info,
        archiver={"archive_mode": "on", "last_archived_epoch": 1, "archived_count": 1},
        now=5000,
        max_backup_age=86400,
        max_wal_age=900,
        version_line=module.PGBACKREST_VERSION_LINE,
        repo_mode="s3",
    )
    assert stale["result"] == "stale"
    assert module.status_exit(stale) == 1
    disabled = module.evaluate_freshness(
        info=info,
        archiver={"archive_mode": "off", "last_archived_epoch": 990, "archived_count": 1},
        now=1000,
        max_backup_age=86400,
        max_wal_age=900,
        version_line=module.PGBACKREST_VERSION_LINE,
        repo_mode="s3",
    )
    assert disabled["result"] == "failed"
    drifted = module.evaluate_freshness(
        info=info,
        archiver={"archive_mode": "on", "last_archived_epoch": 990, "archived_count": 1},
        now=1000,
        max_backup_age=86400,
        max_wal_age=900,
        version_line="pgBackRest 2.59.0",
        repo_mode="s3",
    )
    assert drifted["result"] == "failed"
    plain = module.evaluate_freshness(
        info=[{"name": "gym", "cipher": "none", "backup": info[0]["backup"]}],
        archiver={"archive_mode": "on", "last_archived_epoch": 990, "archived_count": 1},
        now=1000,
        max_backup_age=86400,
        max_wal_age=900,
        version_line=module.PGBACKREST_VERSION_LINE,
        repo_mode="s3",
    )
    assert plain["result"] == "failed"


def test_restore_refuses_production_and_destructive_flags(tmp_path: Path) -> None:
    module = load()
    module.validate_restore_request(restore_env())
    production = restore_env()
    production["RESTORE_PROJECT"] = "gym-production"
    with pytest.raises(module.BackupPolicyError, match="project"):
        module.validate_restore_request(production)
    volume = restore_env()
    volume["RESTORE_VOLUME"] = "postgres_data"
    with pytest.raises(module.BackupPolicyError, match="volume"):
        module.validate_restore_request(volume)
    missing = restore_env()
    missing["RESTORE_CONFIRMATION"] = ""
    with pytest.raises(module.BackupPolicyError, match="confirmation"):
        module.validate_restore_request(missing)
    command = module.build_restore_command("/run/pgbackrest/pgbackrest.conf")
    module.assert_restore_command(command)
    with pytest.raises(module.BackupPolicyError):
        module.assert_restore_command([*command, "--force"])
    with pytest.raises(module.BackupPolicyError):
        module.assert_restore_command([*command, "--delta"])
    occupied = tmp_path / "data"
    occupied.mkdir()
    (occupied / "PG_VERSION").write_text("18\n", encoding="utf-8")
    with pytest.raises(module.BackupPolicyError, match="not empty"):
        module.assert_empty_pgdata(occupied)
    script = (ROOT / "scripts" / "restore_rehearsal.py").read_text(encoding="utf-8")
    assert module.lint_restore_script(script) == []
    skipped = script.replace("verify_restore_rehearsal", "skip-verify", 1)
    assert any("verification" in error for error in module.lint_restore_script(skipped))
    forced = script + "\n# --force\n"
    assert any("destructive" in error for error in module.lint_restore_script(forced))


def test_false_recovery_claims_fail() -> None:
    module = load()
    quiet = "RPO <= 15 minutes remains an unproven planning target."
    assert module.lint_recovery_claims(quiet) == []
    assert module.lint_recovery_claims("The RPO <= 15 minute target is met.")


def test_missing_stanza_is_not_ready() -> None:
    module = load()
    assert module._stanza_ready("[]") is False
    ready = '[{"status": {"code": 0}, "db": [{"id": 1}]}]'
    assert module._stanza_ready(ready) is True


def test_wal_path_rejects_traversal() -> None:
    module = load()
    valid = "/var/lib/postgresql/18/docker/pg_wal/000000010000000000000001"
    assert module.validate_wal_path(valid) == valid
    relative = "pg_wal/000000010000000000000001"
    assert module.validate_wal_path(relative) == relative
    with pytest.raises(module.BackupPolicyError):
        module.validate_wal_path("/tmp/000000010000000000000001")
    with pytest.raises(module.BackupPolicyError):
        module.validate_wal_path("/var/lib/postgresql/18/docker/pg_wal/../postgresql.conf")


def _backup_env(monkeypatch: pytest.MonkeyPatch, module) -> None:
    for name in module.FORBIDDEN_PROCESS_ENV:
        monkeypatch.delenv(name, raising=False)
    for key, value in production_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BACKUP_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("BACKUP_MAX_AGE_SECONDS", "86400")
    monkeypatch.setenv("BACKUP_WAL_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("PGBACKREST_CONFIG", "/run/pgbackrest/pgbackrest.conf")


class Scripted:
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.calls: list[list[str]] = []
        self._responses = list(responses)

    def __call__(self, command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(command))
        code, stdout = self._responses.pop(0)
        return subprocess.CompletedProcess(command, code, stdout, "")


def _fresh_info() -> str:
    return json.dumps(
        [
            {
                "name": "gym",
                "cipher": "aes-256-cbc",
                "status": {"code": 0},
                "db": [{"id": 1}],
                "backup": [
                    {
                        "type": "full",
                        "error": False,
                        "label": "20261021-000000F",
                        "timestamp": {"start": 100, "stop": 200},
                    }
                ],
            }
        ]
    )


def test_digest_images_require_exact_references() -> None:
    module = load()
    digest = "sha256:" + ("ab" * 32)
    module.validate_digest_image(f"ghcr.io/acme/gym-postgres@{digest}")
    module.validate_digest_image(f"localhost:5000/gym-api@{digest}")
    rejected = [
        "postgres:18.6",
        f"postgres:18.6@{digest}",
        f"ghcr.io/acme/gym-postgres:18.6@{digest}",
        "ghcr.io/acme/gym-postgres:latest",
        f"ghcr.io/acme/gym-postgres@sha256:{'ab' * 31}",
        f"ghcr.io/acme/gym-postgres@sha256:{'AB' * 32}",
        f"ghcr.io/acme/gym-postgres@{digest}:latest",
    ]
    for value in rejected:
        with pytest.raises(module.BackupPolicyError, match="restore image"):
            module.validate_digest_image(value)


def test_restore_secrets_are_bounded_files(tmp_path: Path) -> None:
    module = load()
    secret = "dedicated-secret-value"
    path = tmp_path / "secret"
    path.write_text(secret + "\n", encoding="utf-8")
    assert module.read_bounded_secret(path, "restore database password") == secret
    link = tmp_path / "linked"
    link.symlink_to(path)
    with pytest.raises(module.BackupPolicyError, match="unsafe") as caught:
        module.read_bounded_secret(link, "restore database password")
    assert secret not in str(caught.value)
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(module.BackupPolicyError, match="empty"):
        module.read_bounded_secret(empty, "restore database password")
    huge = tmp_path / "huge"
    huge.write_text("a" * 4097, encoding="utf-8")
    with pytest.raises(module.BackupPolicyError, match="oversized") as oversized:
        module.read_bounded_secret(huge, "restore database password")
    assert "a" * 32 not in str(oversized.value)
    placeholder = tmp_path / "placeholder"
    placeholder.write_text("replace-from-secret-store\n", encoding="utf-8")
    with pytest.raises(module.BackupPolicyError, match="placeholder") as refused:
        module.read_bounded_secret(placeholder, "restore django secret")
    assert "replace-from-secret-store" not in str(refused.value)


def test_restore_volume_plan_does_not_touch_docker() -> None:
    module = load()
    target = "gym-restore-data-rehearsal01"
    repo = "gym-restore-repo-rehearsal01"
    actions = module.prepare_restore_volumes(
        mode="rehearsal-posix",
        target=target,
        repo=repo,
        volume_exists=lambda name: name == repo,
    )
    assert actions == [("create", target)]
    with pytest.raises(module.BackupPolicyError, match="already exists"):
        module.prepare_restore_volumes(
            mode="rehearsal-posix",
            target=target,
            repo=repo,
            volume_exists=lambda name: True,
        )
    with pytest.raises(module.BackupPolicyError, match="does not exist"):
        module.prepare_restore_volumes(
            mode="rehearsal-posix",
            target=target,
            repo=repo,
            volume_exists=lambda name: False,
        )
    seen: list[str] = []

    def absent(name: str) -> bool:
        seen.append(name)
        return False

    s3_actions = module.prepare_restore_volumes(
        mode="s3",
        target=target,
        repo="",
        volume_exists=absent,
    )
    assert s3_actions == [("create", target)]
    assert seen == [target]
    with pytest.raises(module.BackupPolicyError, match="local repository"):
        module.prepare_restore_volumes(
            mode="s3",
            target=target,
            repo=repo,
            volume_exists=lambda name: False,
        )


def test_repository_identity_binds_the_endpoint() -> None:
    module = load()
    identity = module.repository_identity(production_env())
    expected = (
        "s3://backup.object-store.test:443/ap-southeast-1/gym-backup-bucket/host/gym-platform"
    )
    assert identity == expected
    other = production_env()
    other["BACKUP_S3_ENDPOINT"] = "https://other.object-store.test"
    assert module.repository_identity(other) != identity
    request = {
        "RESTORE_CONFIRMATION": "restore-disposable",
        "RESTORE_TARGET": "disposable",
        "RESTORE_PROJECT": "gym-restore-rehearsal01",
        "RESTORE_VOLUME": "gym-restore-data-rehearsal01",
        "RESTORE_STANZA": "gym",
        "RESTORE_REPOSITORY_ID": identity,
        "BACKUP_PGDATA": "/var/lib/gym-restore",
        **production_env(),
    }
    module.validate_restore_request(request)
    mismatched = dict(request)
    mismatched["BACKUP_S3_ENDPOINT"] = "https://other.object-store.test"
    with pytest.raises(module.BackupPolicyError, match="identity"):
        module.validate_restore_request(mismatched)
    local = dict(request)
    local["RESTORE_REPO_VOLUME"] = "gym-restore-repo-rehearsal01"
    with pytest.raises(module.BackupPolicyError, match="local repository"):
        module.validate_restore_request(local)
    placeholder = production_env()
    placeholder["BACKUP_S3_ENDPOINT"] = "https://backup-storage.example"
    with pytest.raises(module.BackupPolicyError, match="placeholder"):
        module.render_pgbackrest_config(
            placeholder,
            cipher_pass="dedicated-cipher-passphrase",
            access_key="dedicated-access-key-value",
            secret_key="dedicated-secret-key-value",
        )
    for name, value in (
        ("BACKUP_S3_REGION", "replace-with-approved-backup-region"),
        ("BACKUP_S3_BUCKET", "replace-with-approved-backup-bucket"),
        ("BACKUP_REPO_PATH", "/replace-with-approved-path"),
    ):
        bad = production_env()
        bad[name] = value
        with pytest.raises(module.BackupPolicyError, match="placeholder"):
            module.repository_identity(bad)
    example = (ROOT / "infra/environments/production/compose.env.example").read_text(
        encoding="utf-8"
    )
    assert "backup-storage.example" in example
    assert "replace-with-approved-backup-region" in example


def test_full_backup_does_not_create_a_stanza(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    _backup_env(monkeypatch, module)
    runner = Scripted([(0, "[]")])
    with pytest.raises(module.BackupPolicyError, match="bootstrap"):
        module.run_backup("full", runner=runner, lock=False)
    assert runner.calls
    assert all("stanza-create" not in call for call in runner.calls)
    assert "stanza-create" not in inspect.getsource(module.run_backup)
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://backup-storage.example")
    untouched = Scripted([])
    with pytest.raises(module.BackupPolicyError, match="placeholder"):
        module.run_backup("diff", runner=untouched, lock=False)
    assert untouched.calls == []


def test_bootstrap_creates_stanza_and_stays_unproven(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    _backup_env(monkeypatch, module)
    info = _fresh_info()
    archiver = json.dumps({"archive_mode": "on", "last_archived_epoch": 950, "archived_count": 2})
    runner = Scripted(
        [
            (0, "[]"),
            (0, ""),
            (0, ""),
            (0, ""),
            (0, info),
            (0, module.PGBACKREST_VERSION_LINE + "\n"),
            (0, archiver),
        ]
    )
    status = module.run_bootstrap(runner=runner, now=1000, lock=False)
    joined = [" ".join(call) for call in runner.calls]
    assert any("stanza-create" in call for call in joined)
    assert any("backup --type full" in call for call in joined)
    assert any(call.endswith(" check") for call in joined)
    assert status["result"] == "ok"
    assert status["rpo_proven"] is False
    assert status["rto_proven"] is False
    ready = Scripted(
        [
            (0, info),
            (0, ""),
            (0, ""),
            (0, info),
            (0, module.PGBACKREST_VERSION_LINE + "\n"),
            (0, archiver),
        ]
    )
    module.run_bootstrap(runner=ready, now=1000, lock=False)
    assert all("stanza-create" not in " ".join(call) for call in ready.calls)
    entrypoint = ROOT / "infra/postgres/bin/gym-postgres-entrypoint"
    assert "bootstrap" not in entrypoint.read_text(encoding="utf-8")
    compose = (ROOT / "infra/compose/docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "backup-bootstrap" in compose


def test_bootstrap_check_accepts_the_production_backup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load()
    _backup_env(monkeypatch, module)
    monkeypatch.setenv("BACKUP_TIMEOUT_SECONDS", "14400")
    info = _fresh_info()
    archiver = json.dumps({"archive_mode": "on", "last_archived_epoch": 950, "archived_count": 2})
    runner = Scripted(
        [
            (0, info),
            (0, ""),
            (0, ""),
            (0, info),
            (0, module.PGBACKREST_VERSION_LINE + "\n"),
            (0, archiver),
        ]
    )
    status = module.run_bootstrap(runner=runner, now=1000, lock=False)
    assert status["result"] == "ok"
    assert status["rpo_proven"] is False
    assert os.environ["BACKUP_TIMEOUT_SECONDS"] == "14400"


def test_finish_recovery_promotes_before_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load()
    _backup_env(monkeypatch, module)
    runner = Scripted([(0, ""), (0, "f\n"), (0, ""), (2, "")])
    module._finish_recovery(Path("/var/lib/gym-restore"), runner, 60)
    joined = [" ".join(call) for call in runner.calls]
    assert joined[0].endswith(" start")
    assert "pg_is_in_recovery()" in joined[1]
    assert joined[2].endswith(" stop")
    restore = (ROOT / "infra/compose/docker-compose.restore.yml").read_text(encoding="utf-8")
    assert "internal: true" in restore
    assert "internal: false" not in restore
    assert "/var/lib/pgbackrest" not in restore
    assert module.OFFICIAL_POSTGRES_ENTRYPOINT in restore
    assert "BACKUP_" not in restore
    assert "PGBACKREST" not in restore
    assert "secrets:" not in restore
    assert module.lint_restore_compose(restore) == []
    leaked = restore.replace("PGDATA:", "PGDATA:\n      BACKUP_REPO_PATH: /gym-platform", 1)
    assert any("backup repository" in error for error in module.lint_restore_compose(leaked))


def test_restore_volume_nonce_is_checked_after_create() -> None:
    module = load()
    project = "gym-restore-rehearsal01"
    nonce = "ab" * 16
    labels = module.restore_volume_labels(project, nonce)
    command = module.build_volume_create_command("gym-restore-data-rehearsal01", labels)
    assert command[0:3] == ["docker", "volume", "create"]
    assert f"gym.restore.nonce={nonce}" in command
    assert f"gym.restore.project={project}" in command
    assert command[-1] == "gym-restore-data-rehearsal01"
    inspected = module.build_volume_inspect_command(command[-1])
    assert inspected[-1] == command[-1]
    assert "{{json .Labels}}" in inspected
    module.assert_volume_ownership(module.parse_volume_inspect_labels(json.dumps(labels)), labels)
    raced = dict(labels)
    raced["gym.restore.nonce"] = "cd" * 16
    with pytest.raises(module.BackupPolicyError, match="labels") as caught:
        module.assert_volume_ownership(raced, labels)
    assert nonce not in str(caught.value)
    with pytest.raises(module.BackupPolicyError, match="labels") as missing:
        module.parse_volume_inspect_labels("null")
    assert nonce not in str(missing.value)
    with pytest.raises(module.BackupPolicyError, match="labels"):
        module.assert_volume_ownership({"gym.restore.project": project}, labels)
    assert module.NONCE_RE.fullmatch(module.new_restore_nonce())


def test_inspect_result_reaches_label_verification() -> None:
    module = load()
    labels = module.restore_volume_labels("gym-restore-rehearsal01", "ab" * 16)
    good = subprocess.CompletedProcess(
        ["docker", "volume", "inspect"],
        0,
        json.dumps(labels) + "\n",
        "",
    )
    module.accept_volume_inspect(good, labels)
    for stdout in (None, "", "not-json", "null"):
        bad = subprocess.CompletedProcess(["docker", "volume", "inspect"], 0, stdout, None)
        with pytest.raises(module.BackupPolicyError):
            module.accept_volume_inspect(bad, labels)
    failed = subprocess.CompletedProcess(["docker", "volume", "inspect"], 1, None, None)
    with pytest.raises(module.BackupPolicyError, match="inspected"):
        module.accept_volume_inspect(failed, labels)


def test_storage_key_files_follow_repository_mode(tmp_path: Path) -> None:
    module = load()
    access = tmp_path / "access"
    secret = tmp_path / "secret"
    assert module.restore_storage_key_files(module.REHEARSAL_MODE, None, None) is None
    assert module.storage_key_mounts(None) == []
    with pytest.raises(module.BackupPolicyError, match="storage keys"):
        module.restore_storage_key_files(module.REHEARSAL_MODE, access, secret)
    with pytest.raises(module.BackupPolicyError, match="both storage key files"):
        module.restore_storage_key_files("s3", access, None)
    with pytest.raises(module.BackupPolicyError, match="both storage key files"):
        module.restore_storage_key_files("s3", None, secret)
    mounted = module.storage_key_mounts(module.restore_storage_key_files("s3", access, secret))
    assert f"{access}:/run/secrets/{module.ACCESS_SECRET}:ro" in mounted
    assert f"{secret}:/run/secrets/{module.SECRET_KEY_SECRET}:ro" in mounted
    script = (ROOT / "scripts" / "restore_rehearsal.py").read_text(encoding="utf-8")
    assert "capture_output=True" in script
    assert module.lint_restore_script(script) == []
