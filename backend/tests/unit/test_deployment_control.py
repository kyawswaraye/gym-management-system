from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
API = "sha256:" + "ab" * 32
WEB = "sha256:" + "cd" * 32
POSTGRES = "sha256:" + "ef" * 32
SHA = "a" * 40
CALLER = "b" * 40
TAG = "v1.2.3"
REPO = "acme/gym"
RUN_ID = "42"
CANARY = "canary-secret-value-should-not-leak"
CONTROL = {
    "caller_ref": "refs/heads/main",
    "default_branch": "main",
    "control_head_sha": CALLER,
}


def load(name: str):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{name} is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def release():
    return load("release_supply_chain")


def deploy():
    return load("deployment_control")


def manifest() -> dict:
    return release().build_manifest(
        git_commit=SHA,
        git_ref=f"refs/tags/{TAG}",
        git_ref_name=TAG,
        owner="acme",
        api_digest=API,
        web_digest=WEB,
        postgres_digest=POSTGRES,
        workflow="image-release.yml",
        run_id=RUN_ID,
        run_attempt="1",
        timestamp="2026-10-21T02:08:00Z",
    )


def run_metadata(**overrides: object) -> dict:
    payload = {
        "id": 42,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/image-release.yml",
        "event": "push",
        "head_sha": SHA,
        "head_branch": TAG,
        "head_repository": {"full_name": REPO},
    }
    payload.update(overrides)
    return payload


def attestation_records(payload: dict) -> dict[str, dict[str, str]]:
    return {
        name: {
            "digest": image.rsplit("@", 1)[1],
            "predicate_type": "https://slsa.dev/provenance/v1",
            "repository": REPO,
        }
        for name, image in payload["images"].items()
    }


def bound(**overrides: object):
    module = deploy()
    payload = manifest()
    records = attestation_records(payload)
    kwargs = {
        "manifest": payload,
        "run": run_metadata(),
        "tag_commit_sha": SHA,
        "repository": REPO,
        "expected_sha": SHA,
        "expected_tag": TAG,
        "expected_run_id": RUN_ID,
        "caller_sha": CALLER,
        "attestations": records,
        **CONTROL,
    }
    kwargs.update(overrides)
    return module.bind_release(**kwargs)


def posture() -> str:
    return json.dumps(
        {
            "database": "gym_platform",
            "migration_rows": 12,
            "runtime_bypassrls": False,
            "runtime_login": True,
            "runtime_super": False,
            "unforced_rls_tables": 0,
            "forced_rls_tables": 4,
        }
    )


def compose_ps() -> str:
    services = [
        "caddy",
        "web",
        "api",
        "postgres",
        "clamav",
        "prometheus",
        "alertmanager",
        "grafana",
        "blackbox",
        "node-exporter",
        "postgres-exporter",
    ]
    rows = []
    for name in services:
        row: dict[str, object] = {"Service": name, "State": "running", "Health": "healthy"}
        if name == "caddy":
            row["Publishers"] = [
                {"PublishedPort": 80, "Protocol": "tcp"},
                {"PublishedPort": 443, "Protocol": "tcp"},
            ]
        rows.append(row)
    return json.dumps(rows)


def fetch(url: str) -> tuple[int, bytes, str]:
    host = url.removeprefix("https://").split("/", 1)[0]
    if url.endswith("/login"):
        return 200, b"<!doctype html><html></html>", host
    if url.endswith("/api/v1/auth/login/") or url.endswith("/api/v1/health/"):
        return 404, b"Not found", host
    if url.endswith("/api/v1/member-auth/login/"):
        return 401, b"{}", host
    raise AssertionError(url)


def service_images() -> dict[str, str]:
    images = {
        "api": f"ghcr.io/acme/gym-api@{API}",
        "web": f"ghcr.io/acme/gym-web@{WEB}",
        "postgres": f"ghcr.io/acme/gym-postgres@{POSTGRES}",
    }
    third = (
        ("caddy", "caddy"),
        ("clamav", "clamav/clamav"),
        ("prometheus", "prom/prometheus"),
        ("alertmanager", "prom/alertmanager"),
        ("grafana", "grafana/grafana"),
        ("node-exporter", "prom/node-exporter"),
        ("blackbox", "prom/blackbox-exporter"),
        ("postgres-exporter", "prometheuscommunity/postgres-exporter"),
    )
    for index, (service, name) in enumerate(third, start=1):
        images[service] = f"{name}@sha256:{index:064x}"
    return images


def write_host(root: Path, target: str, *, ghcr: bool = True) -> Path:
    env_dir = root / "etc" / "gym" / target
    env_dir.mkdir(parents=True)
    secret = env_dir / "cipher"
    secret.write_text("cipher-value-not-placeholder\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    alert_secret = env_dir / "alert-smtp-password"
    alert_secret.write_text("alert-password-not-placeholder\n", encoding="utf-8")
    os.chmod(alert_secret, 0o600)
    third = {
        "CADDY_IMAGE": "caddy",
        "CLAMAV_IMAGE": "clamav/clamav",
        "PROMETHEUS_IMAGE": "prom/prometheus",
        "ALERTMANAGER_IMAGE": "prom/alertmanager",
        "GRAFANA_IMAGE": "grafana/grafana",
        "NODE_EXPORTER_IMAGE": "prom/node-exporter",
        "BLACKBOX_EXPORTER_IMAGE": "prom/blackbox-exporter",
        "POSTGRES_EXPORTER_IMAGE": "prometheuscommunity/postgres-exporter",
    }
    lines = [
        f"PUBLIC_HOST={target}.example.com",
        f"POSTGRES_IMAGE=ghcr.io/acme/gym-postgres@{POSTGRES}",
        "POSTGRES_DB=gym_platform",
        "POSTGRES_ADMIN_USER=gym_admin",
        "MIGRATION_DB_USER=gym_migration",
        "RUNTIME_DB_USER=gym_runtime",
        f"POSTGRES_ADMIN_PASSWORD={CANARY}",
        "MIGRATION_DB_PASSWORD=migration-password-value",
        "RUNTIME_DB_PASSWORD=runtime-password-value",
        f"BACKUP_REPO_CIPHER_PASS_FILE={secret}",
        (
            "ALERT_DELIVERY_MODE=resend-email"
            if target == "production"
            else "ALERT_DELIVERY_MODE=non-production"
        ),
        "ALERT_EMAIL_FROM=alerts@example.com",
        "ALERT_EMAIL_TO=ops@example.com",
        f"ALERT_SMTP_PASSWORD_FILE={alert_secret}",
    ]
    for index, (key, name) in enumerate(third.items(), start=1):
        lines.append(f"{key}={name}@sha256:{index:064x}")
    if ghcr:
        docker_dir = env_dir / "docker"
        docker_dir.mkdir()
        os.chmod(docker_dir, 0o700)
        config = docker_dir / "config.json"
        config.write_text(
            '{"credHelpers":{"ghcr.io":"host-credential-helper"}}\n',
            encoding="utf-8",
        )
        os.chmod(config, 0o600)
        lines.append(f"DOCKER_CONFIG={docker_dir}")
    env_file = env_dir / "compose.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    return env_file


def runner_factory(
    fail: str = "",
    images: dict[str, str] | None = None,
    duplicate: str = "",
    missing: str = "",
):
    calls: list[tuple[str, ...]] = []
    known = service_images()
    if images:
        known.update(images)

    def run(command: tuple[str, ...], timeout: int = 30):
        module = deploy()
        calls.append(command)
        if fail and fail in command:
            return module.CommandResult(1, "", "failed")
        if command[:3] == ("docker", "image", "inspect"):
            return module.CommandResult(0, command[-1], "")
        if command[:2] == ("docker", "ps"):
            service = ""
            for part in command:
                if part.startswith("label=com.docker.compose.service="):
                    service = part.rsplit("=", 1)[-1]
            if service == missing:
                return module.CommandResult(0, "", "")
            if service == duplicate:
                return module.CommandResult(0, "cid-a\ncid-b\n", "")
            if service not in known:
                return module.CommandResult(0, "", "")
            return module.CommandResult(0, f"cid-{service}\n", "")
        if command[:2] == ("docker", "inspect"):
            service = command[-1].removeprefix("cid-")
            image = known.get(service)
            if image is None:
                return module.CommandResult(1, "", "missing")
            return module.CommandResult(0, image + "\n", "")
        if "psql" in command:
            return module.CommandResult(0, posture(), "")
        if "--format" in command and "json" in command:
            return module.CommandResult(0, compose_ps(), "")
        return module.CommandResult(0, "", "")

    return calls, run


def test_repository_lint_passes() -> None:
    assert deploy().lint_repository_deployment(ROOT) == []


def test_manifest_binds_release_and_keeps_caller_sha() -> None:
    payload = bound()
    assert payload["release_sha"] == SHA
    assert payload["caller_sha"] == CALLER
    assert payload["sha_relationship"] == "caller-is-workflow-sha"
    assert payload["attestation_command"] == "succeeded"
    assert payload["deploy_proven"] is False
    assert payload["rpo_proven"] is False
    assert payload["images"]["api"].endswith(API)


def test_rejects_malicious_manifest_and_digest_substitution() -> None:
    module = deploy()
    payload = manifest()
    records = attestation_records(payload)
    payload["images"] = dict(payload["images"])
    payload["images"]["api"] = payload["images"]["api"].replace(API, WEB)
    with pytest.raises(module.DeployError):
        module.bind_release(
            manifest=payload,
            run=run_metadata(),
            tag_commit_sha=SHA,
            repository=REPO,
            expected_sha=SHA,
            expected_tag=TAG,
            expected_run_id=RUN_ID,
            caller_sha=CALLER,
            attestations=records,
            **CONTROL,
        )
    mutable = manifest()
    mutable["images"] = dict(mutable["images"])
    mutable["images"]["web"] = "ghcr.io/acme/gym-web:latest"
    with pytest.raises(module.DeployError):
        module.bind_release(
            manifest=mutable,
            run=run_metadata(),
            tag_commit_sha=SHA,
            repository=REPO,
            expected_sha=SHA,
            expected_tag=TAG,
            expected_run_id=RUN_ID,
            caller_sha=CALLER,
            attestations=records,
            **CONTROL,
        )
    extra = manifest()
    extra["deploy_now"] = True
    with pytest.raises(module.DeployError):
        module.bind_release(
            manifest=extra,
            run=run_metadata(),
            tag_commit_sha=SHA,
            repository=REPO,
            expected_sha=SHA,
            expected_tag=TAG,
            expected_run_id=RUN_ID,
            caller_sha=CALLER,
            attestations=attestation_records(manifest()),
            **CONTROL,
        )


def test_rejects_untrusted_run_metadata() -> None:
    module = deploy()
    payload = manifest()
    records = attestation_records(payload)
    common = {
        "manifest": payload,
        "repository": REPO,
        "expected_sha": SHA,
        "expected_tag": TAG,
        "expected_run_id": RUN_ID,
        "caller_sha": CALLER,
        "attestations": records,
        **CONTROL,
    }
    with pytest.raises(module.DeployError):
        module.bind_release(**common, tag_commit_sha=SHA, run=run_metadata(conclusion="failure"))
    with pytest.raises(module.DeployError):
        module.bind_release(**common, tag_commit_sha=SHA, run=run_metadata(head_sha=CALLER))
    with pytest.raises(module.DeployError):
        module.bind_release(
            **common,
            tag_commit_sha=SHA,
            run=run_metadata(head_repository={"full_name": "attacker/gym"}),
        )
    with pytest.raises(module.DeployError):
        module.bind_release(
            **common,
            tag_commit_sha=SHA,
            run=run_metadata(path=".github/workflows/deploy.yml"),
        )
    with pytest.raises(module.DeployError):
        module.bind_release(**common, tag_commit_sha=CALLER, run=run_metadata())


def test_attestation_verifier_rejects_subject_mismatch() -> None:
    module = deploy()
    image = f"ghcr.io/acme/gym-api@{API}"
    command = module.attestation_command(image, REPO)
    assert command[:2] == ("cosign", "verify-attestation")
    assert "--certificate-identity-regexp" in command
    assert command[-1] == image

    def fake(cmd: tuple[str, ...], timeout: int = 30):
        del cmd, timeout
        statement = {
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [{"name": "ghcr.io/acme/gym-api", "digest": {"sha256": "00" * 32}}],
            "predicate": {
                "buildDefinition": {"externalParameters": {"workflow": {"repository": REPO}}}
            },
        }
        payload = base64.b64encode(json.dumps(statement).encode()).decode()
        body = json.dumps({"payload": payload, "payloadType": "application/vnd.dsse.envelope.v1"})
        return module.CommandResult(0, body)

    with pytest.raises(module.DeployError):
        module.verify_image_attestation(image, REPO, fake)


def test_bundle_rejects_traversal_symlink_and_extra_files(tmp_path: Path) -> None:
    module = deploy()
    payload = bound()
    bundle = tmp_path / "gym-deploy-bundle.tar"
    module.build_bundle(ROOT, ROOT, payload, bundle)
    verified = module.verify_bundle(bundle)
    assert verified["images"]["postgres"].endswith(POSTGRES)
    names = tarfile.open(bundle).getnames()
    assert "infra/environments/production/compose.env.example" not in names
    assert all(".example" not in name for name in names)
    members = module.bundle_members(ROOT, ROOT, payload)
    members["extra.txt"] = b"extra"
    extra = tmp_path / "extra.tar"
    module.write_bundle(members, extra)
    with pytest.raises(module.DeployError):
        module.verify_bundle(extra)
    link = tmp_path / "link.tar"
    with tarfile.open(link, "w") as archive:
        info = tarfile.TarInfo("escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "../secret"
        archive.addfile(info)
    with pytest.raises(module.DeployError):
        module.read_bundle(link)
    traversal = tmp_path / "traversal.tar"
    with tarfile.open(traversal, "w") as archive:
        info = tarfile.TarInfo("../secret")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(module.DeployError):
        module.read_bundle(traversal)


def test_workflow_rejects_unpinned_host_keys() -> None:
    module = deploy()
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "sshpass" not in text
    assert "ssh-keyscan" not in text
    assert module.TEARDOWN_TOKEN not in text
    mutated = text.replace("StrictHostKeyChecking=yes", "StrictHostKeyChecking=no")
    errors = module.lint_deploy_workflow_text(mutated)
    assert any("StrictHostKeyChecking=no" in error or "host key" in error for error in errors)
    caller = text.replace(
        "ref: ${{ needs.resolve.outputs.release_sha }}",
        "ref: ${{ github.sha }}",
    )
    assert any("release SHA" in error for error in module.lint_deploy_workflow_text(caller))
    branch = text.replace(
        "ref: ${{ needs.resolve.outputs.control_sha }}",
        "ref: ${{ github.ref }}",
    )
    assert any("default-branch" in error for error in module.lint_deploy_workflow_text(branch))
    options = module.ssh_options(Path("/tmp/known_hosts"), Path("/tmp/id"))
    assert "StrictHostKeyChecking=yes" in options
    assert "StrictHostKeyChecking=no" not in options
    previous = os.environ.get("SSH_STRICT_HOST_KEY_CHECKING")
    os.environ["SSH_STRICT_HOST_KEY_CHECKING"] = "no"
    try:
        with pytest.raises(module.DeployError):
            module.ssh_options(Path("/tmp/known_hosts"), Path("/tmp/id"))
    finally:
        if previous is None:
            os.environ.pop("SSH_STRICT_HOST_KEY_CHECKING", None)
        else:
            os.environ["SSH_STRICT_HOST_KEY_CHECKING"] = previous


def test_commands_and_evidence_do_not_leak_secrets(tmp_path: Path) -> None:
    module = deploy()
    payload = bound()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, payload, bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "staging")
    calls, run = runner_factory()
    evidence = module.execute_deployment(
        module.HostRequest(
            target="staging",
            operation="deploy",
            schema_compatibility="not-applicable",
            bundle=bundle,
            state_dir=root / "var" / "lib" / "gym-deploy" / "staging",
            env_file=env_file,
            host_root=root,
            running_projects=(),
            free_disk_bytes=module.MIN_FREE_DISK_BYTES,
            mem_available_bytes=module.MIN_MEM_AVAILABLE_BYTES,
            fetch=fetch,
            sleeper=lambda _seconds: None,
        ),
        run,
    )
    rendered = json.dumps(calls)
    assert CANARY not in rendered
    assert CANARY not in (root / "var/lib/gym-deploy/staging/evidence/latest.json").read_text(
        encoding="utf-8"
    )
    assert evidence["commands_completed"] is True
    assert evidence["deploy_proven"] is False
    active = root / "var/lib/gym-deploy/staging/active-deployment.json"
    assert stat.S_IMODE(active.stat().st_mode) == 0o600
    saved = json.loads(active.read_text(encoding="utf-8"))
    assert saved["sha256"]
    assert saved["payload"]["images"]["api"].endswith(API)
    assert evidence["backup_proven"] is False
    assert evidence["rpo_proven"] is False
    assert "backup-full" not in rendered
    assert any("migrate" in command for command in calls)


def test_first_production_checkpoint_bootstraps_before_migration(tmp_path: Path) -> None:
    module = deploy()
    steps = module.deployment_steps("production", "deploy", "not-applicable")
    assert steps.index("acquire-lock") < steps.index("backup-checkpoint")
    assert steps.index("backup-checkpoint") < steps.index("migrate")
    assert steps.index("migrate") < steps.index("start-services")
    payload = bound()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, payload, bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "production")
    calls, run = runner_factory(fail="backup-bootstrap")
    with pytest.raises(module.DeployError):
        module.execute_deployment(
            module.HostRequest(
                target="production",
                operation="deploy",
                schema_compatibility="not-applicable",
                bundle=bundle,
                state_dir=root / "var" / "lib" / "gym-deploy" / "production",
                env_file=env_file,
                host_root=root,
                running_projects=(),
                free_disk_bytes=module.MIN_FREE_DISK_BYTES,
                mem_available_bytes=module.MIN_MEM_AVAILABLE_BYTES,
                fetch=fetch,
                sleeper=lambda _seconds: None,
            ),
            run,
        )
    rendered = json.dumps(calls)
    assert "backup-bootstrap" in rendered
    assert "backup-full" not in rendered
    assert not any(command[-1] == "migrate" for command in calls if command)


def test_rollback_stops_without_schema_review(tmp_path: Path) -> None:
    module = deploy()
    steps = module.deployment_steps("production", "rollback-app", "not-applicable")
    assert "pull-app-digests" not in steps
    assert steps[-1] == "refuse-rollback"
    payload = bound()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, payload, bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "production")
    calls, run = runner_factory()
    with pytest.raises(module.DeployError, match="stop-forward-fix-or-restore"):
        module.execute_deployment(
            module.HostRequest(
                target="production",
                operation="rollback-app",
                schema_compatibility="not-applicable",
                bundle=bundle,
                state_dir=root / "var" / "lib" / "gym-deploy" / "production",
                env_file=env_file,
                host_root=root,
                running_projects=(),
                free_disk_bytes=module.MIN_FREE_DISK_BYTES,
                mem_available_bytes=module.MIN_MEM_AVAILABLE_BYTES,
                fetch=fetch,
                sleeper=lambda _seconds: None,
            ),
            run,
        )
    assert calls == []
    evidence = json.loads(
        (root / "var/lib/gym-deploy/production/evidence/latest.json").read_text(encoding="utf-8")
    )
    assert evidence["rollback_boundary"] == "stop-forward-fix-or-restore"
    assert evidence["rollback_proven"] is False


def test_smoke_failure_does_not_roll_back(tmp_path: Path) -> None:
    module = deploy()
    payload = bound()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, payload, bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "staging")
    calls, run = runner_factory()

    def broken(url: str) -> tuple[int, bytes, str]:
        del url
        return 500, b"no", "staging.example.com"

    with pytest.raises(module.DeployError):
        module.execute_deployment(
            module.HostRequest(
                target="staging",
                operation="deploy",
                schema_compatibility="not-applicable",
                bundle=bundle,
                state_dir=root / "var" / "lib" / "gym-deploy" / "staging",
                env_file=env_file,
                host_root=root,
                running_projects=(),
                free_disk_bytes=module.MIN_FREE_DISK_BYTES,
                mem_available_bytes=module.MIN_MEM_AVAILABLE_BYTES,
                fetch=broken,
                sleeper=lambda _seconds: None,
            ),
            run,
        )
    rendered = json.dumps(calls)
    assert "down" not in rendered
    assert "rollback" not in rendered
    evidence = json.loads(
        (root / "var/lib/gym-deploy/staging/evidence/latest.json").read_text(encoding="utf-8")
    )
    assert evidence["rollback_boundary"] == "stop-forward-fix-or-restore"
    assert evidence["smoke_proven"] is False
    assert evidence["commands_completed"] is False
    assert not (root / "var/lib/gym-deploy/staging/active-deployment.json").exists()


def test_staging_isolation_and_teardown_token(tmp_path: Path) -> None:
    module = deploy()
    root = tmp_path / "host"
    (root / "etc" / "gym" / "production").mkdir(parents=True)
    with pytest.raises(module.DeployError):
        module.assert_target_isolation(
            target="staging",
            host_root=root,
            running_projects=(),
            public_host="staging.example.com",
        )
    with pytest.raises(module.DeployError):
        module.assert_target_isolation(
            target="production",
            host_root=tmp_path / "empty",
            running_projects=("gym-staging",),
            public_host="app.example.com",
        )
    compose = tmp_path / "staging" / "docker-compose.prod.yml"
    compose.parent.mkdir()
    compose.write_text("services: {}\n", encoding="utf-8")
    with pytest.raises(module.DeployError):
        module.plan_teardown(target="staging", confirmation="no", compose_file=compose)
    with pytest.raises(module.DeployError):
        module.plan_teardown(
            target="production",
            confirmation=module.TEARDOWN_TOKEN,
            compose_file=tmp_path / "production" / "docker-compose.prod.yml",
        )
    command = module.plan_teardown(
        target="staging",
        confirmation=module.TEARDOWN_TOKEN,
        compose_file=compose,
    )
    assert "gym-staging" in command
    assert "gym-production" not in command
    assert "down" in command and "-v" in command


def test_blackbox_render_keeps_verification_and_source(tmp_path: Path) -> None:
    module = deploy()
    source = ROOT / "infra" / "observability" / "blackbox" / "blackbox.yml"
    original = source.read_text(encoding="utf-8")
    destination = tmp_path / "blackbox.rendered.yml"
    digest = module.write_rendered_blackbox(source, destination, "staging.example.com")
    rendered = destination.read_text(encoding="utf-8")
    assert "server_name: staging.example.com" in rendered
    assert "insecure_skip_verify: false" in rendered
    assert "insecure_skip_verify: true" not in rendered
    assert source.read_text(encoding="utf-8") == original
    assert len(digest) == 64
    compose = (ROOT / "infra" / "compose" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "./blackbox.rendered.yml:/etc/blackbox/blackbox.yml:ro" in compose
    assert "../observability/blackbox/blackbox.yml:" not in compose
    insecure = original.replace("insecure_skip_verify: false", "insecure_skip_verify: true")
    with pytest.raises(module.DeployError):
        module.render_blackbox_text(insecure, "staging.example.com")


def test_resources_lock_and_false_proof(tmp_path: Path) -> None:
    module = deploy()
    with pytest.raises(module.DeployError):
        module.check_resources(module.MIN_FREE_DISK_BYTES - 1, module.MIN_MEM_AVAILABLE_BYTES)
    with pytest.raises(module.DeployError):
        module.seal_evidence({"rpo_proven": True})
    lock = tmp_path / "deploy.lock"
    first = module.acquire_deploy_lock(lock)
    with pytest.raises(module.DeployError, match="lock"):
        module.acquire_deploy_lock(lock)
    module.release_deploy_lock(first)
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600


def test_public_smoke_boundaries() -> None:
    smoke = load("deployment_smoke")
    smoke.interpret_public("login", 200, b"<html></html>", "app.example.com", "app.example.com")
    smoke.interpret_public("staff-api", 404, b"Not found", "app.example.com", "app.example.com")
    smoke.interpret_public(
        "member-boundary", 401, b"unauthorized", "app.example.com", "app.example.com"
    )
    with pytest.raises(smoke.SmokeError):
        smoke.interpret_public("staff-api", 200, b"ok", "app.example.com", "app.example.com")
    with pytest.raises(smoke.SmokeError):
        smoke.interpret_public(
            "member-boundary",
            200,
            b"eyJhbGciOiJIUzI1NiJ9",
            "app.example.com",
            "app.example.com",
        )
    with pytest.raises(smoke.SmokeError):
        smoke.approve_redirect("https://app.example.com/login", "http://app.example.com/login")
    with pytest.raises(smoke.SmokeError):
        smoke.approve_redirect("https://app.example.com/login", "https://evil.example/login")
    posture_ok = smoke.interpret_posture(json.loads(posture()))
    assert posture_ok["forced_rls_posture"] == "pass"
    assert posture_ok["restore_proven"] is False
    assert "print(" not in smoke.API_ROLE_SCRIPT
    assert "password" not in smoke.POSTURE_SQL.lower()
    commands = smoke.observability_commands(("docker", "compose"))
    rendered = json.dumps(commands)
    urls = [url for _name, url in smoke.OBSERVABILITY_URLS]
    assert "http://blackbox:9115/-/healthy" in urls
    assert "http://blackbox:9115/health" not in urls
    assert "--timeout" not in rendered
    assert "/bin/wget" in rendered
    assert "clamav" in smoke.REQUIRED_SERVICES
    without_clamav = json.loads(compose_ps())
    without_clamav = [row for row in without_clamav if row["Service"] != "clamav"]
    with pytest.raises(smoke.SmokeError):
        smoke.interpret_compose_ps(without_clamav)


def host_request(module, root: Path, bundle: Path, target: str, operation: str, schema: str):
    return module.HostRequest(
        target=target,
        operation=operation,
        schema_compatibility=schema,
        bundle=bundle,
        state_dir=root / "var" / "lib" / "gym-deploy" / target,
        env_file=root / "etc" / "gym" / target / "compose.env",
        host_root=root,
        running_projects=(),
        free_disk_bytes=module.MIN_FREE_DISK_BYTES,
        mem_available_bytes=module.MIN_MEM_AVAILABLE_BYTES,
        fetch=fetch,
        sleeper=lambda _seconds: None,
    )


def test_control_ref_rejects_a_branch_and_a_moved_head() -> None:
    module = deploy()
    payload = manifest()
    base = {
        "manifest": payload,
        "run": run_metadata(),
        "tag_commit_sha": SHA,
        "repository": REPO,
        "expected_sha": SHA,
        "expected_tag": TAG,
        "expected_run_id": RUN_ID,
        "caller_sha": CALLER,
        "attestations": attestation_records(payload),
        **CONTROL,
    }
    with pytest.raises(module.DeployError):
        module.bind_release(**{**base, "caller_ref": "refs/heads/evil"})
    with pytest.raises(module.DeployError):
        module.bind_release(**{**base, "control_head_sha": "c" * 40})


def test_tag_resolution_peels_annotated_tags_and_ignores_commits_api() -> None:
    module = deploy()
    calls: list[str] = []

    def fetch(path: str) -> dict[str, object]:
        calls.append(path)
        if path.endswith("/git/ref/tags/v1.2.3"):
            return {
                "ref": "refs/tags/v1.2.3",
                "object": {"sha": "d" * 40, "type": "tag"},
            }
        if path.endswith("/git/tags/" + ("d" * 40)):
            return {"object": {"sha": SHA, "type": "commit"}}
        raise AssertionError(path)

    assert module.resolve_tag_commit(REPO, TAG, fetch) == SHA
    assert all("/commits/" not in path for path in calls)

    def lightweight(path: str) -> dict[str, object]:
        calls.append(path)
        return {"ref": "refs/tags/v1.2.3", "object": {"sha": SHA, "type": "commit"}}

    assert module.resolve_tag_commit(REPO, TAG, lightweight) == SHA


def test_moved_default_branch_fails_closed_before_transfer() -> None:
    module = deploy()
    payload = bound()

    def run(command: tuple[str, ...], timeout: int = 30):
        del timeout
        path = command[-1]
        if path == f"repos/{REPO}":
            body = {"full_name": REPO, "default_branch": "main"}
            return module.CommandResult(0, json.dumps(body), "")
        if path.endswith("/git/ref/heads/main"):
            body = {"object": {"type": "commit", "sha": "c" * 40}}
            return module.CommandResult(0, json.dumps(body), "")
        return module.CommandResult(1, "", "missing")

    previous_ref = os.environ.get("DEPLOY_CALLER_REF")
    previous_sha = os.environ.get("DEPLOY_CONTROL_SHA")
    os.environ["DEPLOY_CALLER_REF"] = "refs/heads/main"
    os.environ["DEPLOY_CONTROL_SHA"] = CALLER
    try:
        with pytest.raises(module.DeployError, match="moved"):
            module.confirm_control_plane(payload, run)
    finally:
        if previous_ref is None:
            os.environ.pop("DEPLOY_CALLER_REF", None)
        else:
            os.environ["DEPLOY_CALLER_REF"] = previous_ref
        if previous_sha is None:
            os.environ.pop("DEPLOY_CONTROL_SHA", None)
        else:
            os.environ["DEPLOY_CONTROL_SHA"] = previous_sha


def test_checksum_manifest_rejects_duplicate_paths(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    members = module.read_bundle(bundle)
    document = json.loads(members[module.BUNDLE_MANIFEST][0].decode("ascii"))
    document["files"].append(dict(document["files"][0]))
    members[module.BUNDLE_MANIFEST] = (module.canonical_json(document), 0o644)
    with pytest.raises(module.DeployError, match="repeats a path"):
        module.verify_bundle_members(members)
    with pytest.raises(module.DeployError, match="repeats a key"):
        module.load_strict_json(b'{"schema_version":1,"schema_version":2}')


def test_owner_only_rejects_a_different_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = deploy()
    path = tmp_path / "secret"
    path.write_text("value\n", encoding="utf-8")
    os.chmod(path, 0o600)
    module.assert_owner_only(path)
    current = path.stat()

    class Info:
        st_mode = current.st_mode
        st_uid = 0

    monkeypatch.setattr(Path, "stat", lambda self, follow_symlinks=True: Info())
    module.assert_owner_only(path)

    class Wrong:
        st_mode = current.st_mode
        st_uid = 65534 if os.geteuid() != 65534 else 65533

    monkeypatch.setattr(Path, "stat", lambda self, follow_symlinks=True: Wrong())
    with pytest.raises(module.DeployError, match="owner"):
        module.assert_owner_only(path)


def test_running_image_must_match_and_be_unique(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    root = tmp_path / "host"
    write_host(root, "staging")
    wrong = "sha256:" + ("ff" * 32)
    _calls, run = runner_factory(images={"api": f"ghcr.io/acme/gym-api@{wrong}"})
    with pytest.raises(module.DeployError, match="running image"):
        module.execute_deployment(
            host_request(module, root, bundle, "staging", "deploy", "not-applicable"),
            run,
        )
    assert not (root / "var/lib/gym-deploy/staging/active-deployment.json").exists()
    _calls, duplicate = runner_factory(duplicate="web")
    with pytest.raises(module.DeployError, match="container count"):
        module.execute_deployment(
            host_request(module, root, bundle, "staging", "deploy", "not-applicable"),
            duplicate,
        )
    _calls, absent = runner_factory(missing="clamav")
    with pytest.raises(module.DeployError, match="container count"):
        module.execute_deployment(
            host_request(module, root, bundle, "staging", "deploy", "not-applicable"),
            absent,
        )


def test_rollback_keeps_active_postgres_when_host_env_is_stale(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "production")
    active_postgres = "sha256:" + ("11" * 32)
    stale = "sha256:" + ("22" * 32)
    images = service_images()
    images["postgres"] = f"ghcr.io/acme/gym-postgres@{active_postgres}"
    state = root / "var" / "lib" / "gym-deploy" / "production"
    state.mkdir(parents=True)
    module.write_active_state(
        state / "active-deployment.json",
        {
            "target": "production",
            "project": "gym-production",
            "release_sha": "d" * 40,
            "release_tag": "v1.0.0",
            "run_id": "7",
            "public_host": "production.example.com",
            "postgres_image": images["postgres"],
            "images": images,
        },
    )
    before = (state / "active-deployment.json").read_bytes()
    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace(POSTGRES, stale),
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)
    calls, run = runner_factory(images={"postgres": images["postgres"]})
    evidence = module.execute_deployment(
        host_request(module, root, bundle, "production", "rollback-app", "reviewed-authorized"),
        run,
    )
    rendered = json.dumps(calls)
    assert stale not in rendered
    assert "gym-postgres" not in rendered
    assert not any("migrate" in command for command in calls)
    for command in calls:
        if "up" in command or "pull" in command or "restart" in command:
            assert "postgres" not in command
    updated = json.loads((state / "active-deployment.json").read_text(encoding="utf-8"))
    assert updated["payload"]["postgres_image"] == images["postgres"]
    assert updated["payload"]["images"]["caddy"] == images["caddy"]
    assert updated["payload"]["release_sha"] == SHA
    assert updated["payload"]["images"]["api"].endswith(API)
    assert updated["payload"]["images"]["web"].endswith(WEB)
    assert evidence["images"]["POSTGRES_IMAGE"] == images["postgres"]
    assert evidence["images"]["CADDY_IMAGE"] == images["caddy"]
    assert evidence["rollback_proven"] is False
    tampered = json.loads(before.decode("ascii"))
    tampered["sha256"] = "0" * 64
    (state / "active-deployment.json").write_text(json.dumps(tampered), encoding="ascii")
    os.chmod(state / "active-deployment.json", 0o600)
    calls, run = runner_factory(images={"postgres": images["postgres"]})
    with pytest.raises(module.DeployError, match="checksum"):
        module.execute_deployment(
            host_request(module, root, bundle, "production", "rollback-app", "reviewed-authorized"),
            run,
        )
    assert calls == []


def test_rollback_preserves_active_infra_digests(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    root = tmp_path / "host"
    env_file = write_host(root, "production")
    preserved = service_images()
    preserved["caddy"] = "caddy@" + "sha256:" + ("31" * 32)
    preserved["prometheus"] = "prom/prometheus@" + "sha256:" + ("41" * 32)
    preserved["grafana"] = "grafana/grafana@" + "sha256:" + ("51" * 32)
    preserved["postgres"] = "ghcr.io/acme/gym-postgres@" + "sha256:" + ("11" * 32)
    malicious = {
        "caddy": "evil.example/caddy@" + "sha256:" + ("33" * 32),
        "prometheus": "prom/prometheus@" + "sha256:" + ("44" * 32),
        "grafana": "grafana/grafana@" + "sha256:" + ("55" * 32),
        "postgres": "ghcr.io/attacker/gym-postgres@" + "sha256:" + ("22" * 32),
    }
    state = root / "var" / "lib" / "gym-deploy" / "production"
    state.mkdir(parents=True)
    module.write_active_state(
        state / "active-deployment.json",
        {
            "target": "production",
            "project": "gym-production",
            "release_sha": "d" * 40,
            "release_tag": "v1.0.0",
            "run_id": "7",
            "public_host": "production.example.com",
            "postgres_image": preserved["postgres"],
            "images": preserved,
        },
    )
    original = (state / "active-deployment.json").read_bytes()
    text = env_file.read_text(encoding="utf-8")
    text = text.replace("caddy@sha256:" + f"{1:064x}", malicious["caddy"])
    text = text.replace("prom/prometheus@sha256:" + f"{3:064x}", malicious["prometheus"])
    text = text.replace("grafana/grafana@sha256:" + f"{5:064x}", malicious["grafana"])
    text = text.replace(f"ghcr.io/acme/gym-postgres@{POSTGRES}", malicious["postgres"])
    env_file.write_text(text, encoding="utf-8")
    os.chmod(env_file, 0o600)
    calls, run = runner_factory(
        images={
            "caddy": preserved["caddy"],
            "prometheus": preserved["prometheus"],
            "grafana": preserved["grafana"],
            "postgres": preserved["postgres"],
        }
    )
    evidence = module.execute_deployment(
        host_request(module, root, bundle, "production", "rollback-app", "reviewed-authorized"),
        run,
    )
    rendered = json.dumps({"commands": calls, "images": evidence["images"]})
    for stale in malicious.values():
        assert stale not in rendered
    assert evidence["images"]["CADDY_IMAGE"] == preserved["caddy"]
    assert evidence["images"]["PROMETHEUS_IMAGE"] == preserved["prometheus"]
    assert evidence["images"]["GRAFANA_IMAGE"] == preserved["grafana"]
    assert evidence["images"]["POSTGRES_IMAGE"] == preserved["postgres"]
    assert evidence["images"]["API_IMAGE"].endswith(API)
    assert evidence["images"]["WEB_IMAGE"].endswith(WEB)
    saved = json.loads((state / "active-deployment.json").read_text(encoding="utf-8"))
    assert saved["payload"]["release_sha"] == SHA
    assert saved["payload"]["images"]["caddy"] == preserved["caddy"]
    assert saved["payload"]["images"]["prometheus"] == preserved["prometheus"]
    assert saved["payload"]["images"]["grafana"] == preserved["grafana"]
    assert saved["payload"]["images"]["postgres"] == preserved["postgres"]
    assert saved["payload"]["images"]["api"].endswith(API)
    assert saved["payload"]["postgres_image"] == preserved["postgres"]
    assert (state / "active-deployment.json").read_bytes() != original

    (state / "active-deployment.json").write_bytes(original)
    os.chmod(state / "active-deployment.json", 0o600)
    request = host_request(
        module, root, bundle, "production", "rollback-app", "reviewed-authorized"
    )

    def broken(url: str) -> tuple[int, bytes, str]:
        del url
        return 500, b"no", "production.example.com"

    request.fetch = broken
    _calls, failed = runner_factory(
        images={
            "caddy": preserved["caddy"],
            "prometheus": preserved["prometheus"],
            "grafana": preserved["grafana"],
            "postgres": preserved["postgres"],
        }
    )
    with pytest.raises(module.DeployError):
        module.execute_deployment(request, failed)
    assert (state / "active-deployment.json").read_bytes() == original


def test_ghcr_config_rejects_inline_credentials(tmp_path: Path) -> None:
    module = deploy()
    directory = tmp_path / "docker"
    directory.mkdir()
    os.chmod(directory, 0o700)
    config = directory / "config.json"
    records = (
        {"auth": "dGVzdDp0ZXN0"},
        {"password": "secret-value"},
        {"identitytoken": "token-value"},
        {"registrytoken": "token-value"},
    )
    for record in records:
        config.write_text(
            json.dumps(
                {
                    "credHelpers": {"ghcr.io": "host-credential-helper"},
                    "auths": {"ghcr.io": record},
                }
            ),
            encoding="utf-8",
        )
        os.chmod(config, 0o600)
        with pytest.raises(module.DeployError, match="inline"):
            module._assert_ghcr_config({"DOCKER_CONFIG": str(directory)}, tmp_path / "state")
    config.write_text(
        json.dumps({"credsStore": "host-credential-helper"}),
        encoding="utf-8",
    )
    os.chmod(config, 0o600)
    module._assert_ghcr_config({"DOCKER_CONFIG": str(directory)}, tmp_path / "state")
    link = tmp_path / "linked-state.json"
    link.symlink_to(config)
    payload = {
        "target": "production",
        "project": "gym-production",
        "release_sha": SHA,
        "release_tag": TAG,
        "run_id": RUN_ID,
        "public_host": "production.example.com",
        "postgres_image": service_images()["postgres"],
        "images": service_images(),
    }
    with pytest.raises(module.DeployError, match="symlink"):
        module.write_active_state(link, payload)


def test_active_state_write_finishes_partial_os_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = deploy()
    path = tmp_path / "active-deployment.json"
    images = service_images()
    payload = {
        "target": "production",
        "project": "gym-production",
        "release_sha": SHA,
        "release_tag": TAG,
        "run_id": RUN_ID,
        "public_host": "production.example.com",
        "postgres_image": images["postgres"],
        "images": images,
    }
    real_write = os.write

    def short_write(descriptor: int, data: bytes | memoryview) -> int:
        chunk = bytes(data[:1])
        if not chunk:
            return 0
        return real_write(descriptor, chunk)

    monkeypatch.setattr(os, "write", short_write)
    module.write_active_state(path, payload)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["payload"]["release_sha"] == SHA
    assert saved["payload"]["images"]["api"].endswith(API)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.is_symlink()


def test_missing_registry_helper_stops_before_migration(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    root = tmp_path / "host"
    write_host(root, "production", ghcr=False)
    calls, run = runner_factory()
    with pytest.raises(module.DeployError, match="credential helper"):
        module.execute_deployment(
            host_request(module, root, bundle, "production", "deploy", "not-applicable"),
            run,
        )
    assert calls == []


def test_pull_failure_stops_before_migration(tmp_path: Path) -> None:
    module = deploy()
    bundle = tmp_path / "bundle.tar"
    module.build_bundle(ROOT, ROOT, bound(), bundle)
    root = tmp_path / "host"
    write_host(root, "production")
    calls, run = runner_factory(fail="pull")
    with pytest.raises(module.DeployError, match="before migration"):
        module.execute_deployment(
            host_request(module, root, bundle, "production", "deploy", "not-applicable"),
            run,
        )
    assert not any(part == "migrate" for command in calls for part in command)


def test_incoming_directories_do_not_overlap() -> None:
    module = deploy()
    assert module.incoming_dir("staging").endswith("/incoming/staging")
    assert module.incoming_dir("production").endswith("/incoming/production")
    shared = {
        "bundle": Path("bundle.tar"),
        "script": Path("deployment_control.py"),
        "smoke_script": Path("deployment_smoke.py"),
        "host": "app.example.com",
        "user": "deploy",
        "known_hosts": Path("/tmp/known_hosts"),
        "identity": Path("/tmp/id"),
        "operation": "deploy",
        "schema_compatibility": "not-applicable",
    }
    staging = module.transfer_commands(target="staging", **shared)
    production = module.transfer_commands(target="production", **shared)
    assert "/var/lib/gym-deploy/incoming/staging" in staging[-1][-1]
    assert "/var/lib/gym-deploy/incoming/production" in production[-1][-1]
    assert "incoming/staging" not in production[-1][-1]
    steps = module.deployment_steps("production", "deploy", "not-applicable")
    assert steps.index("acquire-host-lock") < steps.index("verify-bundle")
    assert steps.index("verify-bundle") < steps.index("migrate")
