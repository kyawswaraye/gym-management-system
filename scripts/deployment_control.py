"""Deployment control for Phase 8 increment 5.

Workflow and host commands fail closed. A completed command is not proof of a
protected GitHub environment, a GHCR push, a VPS deploy, backup freshness,
restore, RPO, RTO, or an authorized rollback.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import deployment_smoke as smoke

WORKFLOW_NAME = "deploy.yml"
RELEASE_WORKFLOW = "image-release.yml"
RELEASE_PATH = ".github/workflows/image-release.yml"
STAGING_PROJECT = "gym-staging"
PRODUCTION_PROJECT = "gym-production"
STAGING_ENVIRONMENT = "gym-staging"
PRODUCTION_ENVIRONMENT = "gym-production"
TEARDOWN_TOKEN = "destroy-temporary-staging"
TARGETS = frozenset({"staging", "production"})
OPERATIONS = frozenset({"deploy", "rollback-app"})
SCHEMA_CHOICES = frozenset({"not-applicable", "reviewed-authorized"})
INCOMING_PARENT = "/var/lib/gym-deploy/incoming"
HOST_LOCK_NAME = "host.lock"
ACTIVE_STATE_NAME = "active-deployment.json"
BUNDLE_NAME = "gym-deploy-bundle.tar"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SEMVER_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
RUN_ID_RE = re.compile(r"[1-9][0-9]{0,18}\Z")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)
ALERT_EMAIL_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\Z")
USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
HOST_KEY_RE = re.compile(
    r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|"
    r"ecdsa-sha2-nistp521) [A-Za-z0-9+/]+={0,3}\Z"
)
ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
SERVER_NAME_RE = re.compile(r"(?m)^(?P<indent>[ \t]*)server_name:[ \t]*\S+[ \t]*$")
PROOF_KEYS = (
    "deploy_proven",
    "ghcr_push_proven",
    "protected_environment_proven",
    "backup_proven",
    "restore_proven",
    "rpo_proven",
    "rto_proven",
    "rollback_proven",
    "smoke_proven",
    "attestation_proven",
    "staging_proven",
)
MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "git_commit",
        "git_ref",
        "git_ref_name",
        "images",
        "build",
        "runtime_baseline",
    }
)
BUILD_KEYS = frozenset({"workflow", "run_id", "run_attempt", "timestamp"})
IMAGE_KEYS = frozenset({"api", "web", "postgres"})
SLSA_PREFIX = "https://slsa.dev/provenance/"
MIN_FREE_DISK_BYTES = 10 * 1024**3
MIN_MEM_AVAILABLE_BYTES = 2 * 1024**3
MAX_MEMBER_BYTES = 2_000_000
MAX_BUNDLE_BYTES = 8_000_000
BUNDLE_MANIFEST = "bundle-manifest.json"
RENDERED_BLACKBOX = "blackbox.rendered.yml"
FORBIDDEN_WORKFLOW = (
    "StrictHostKeyChecking=no",
    "sshpass",
    "ssh-keyscan",
    "curl |",
    "curl|",
    "docker.sock",
    ":latest",
    "UserKnownHostsFile=/dev/null",
    "docker login",
    "--password-stdin",
    "--password",
    "GHCR_TOKEN",
    TEARDOWN_TOKEN,
)
JOB_PERMISSIONS = {
    "contents": "read",
    "actions": "read",
    "attestations": "read",
    "packages": "read",
}
THIRD_PARTY_IMAGES = {
    "CADDY_IMAGE": "caddy",
    "CLAMAV_IMAGE": "clamav/clamav",
    "PROMETHEUS_IMAGE": "prom/prometheus",
    "ALERTMANAGER_IMAGE": "prom/alertmanager",
    "GRAFANA_IMAGE": "grafana/grafana",
    "NODE_EXPORTER_IMAGE": "prom/node-exporter",
    "BLACKBOX_EXPORTER_IMAGE": "prom/blackbox-exporter",
    "POSTGRES_EXPORTER_IMAGE": "prometheuscommunity/postgres-exporter",
}
SERVICE_IMAGE_KEYS = (
    ("caddy", "CADDY_IMAGE"),
    ("web", "WEB_IMAGE"),
    ("api", "API_IMAGE"),
    ("postgres", "POSTGRES_IMAGE"),
    ("clamav", "CLAMAV_IMAGE"),
    ("prometheus", "PROMETHEUS_IMAGE"),
    ("alertmanager", "ALERTMANAGER_IMAGE"),
    ("grafana", "GRAFANA_IMAGE"),
    ("node-exporter", "NODE_EXPORTER_IMAGE"),
    ("blackbox", "BLACKBOX_EXPORTER_IMAGE"),
    ("postgres-exporter", "POSTGRES_EXPORTER_IMAGE"),
)
INFRA_PATHS = (
    "infra/compose/docker-compose.prod.yml",
    "infra/caddy/Caddyfile",
    "infra/postgres/init/001-create-roles.sh",
    "infra/observability/alertmanager/alertmanager.yml",
    "infra/observability/alertmanager/entrypoint.sh",
    "infra/observability/alertmanager/non-production-sink.url",
    "infra/observability/blackbox/blackbox.yml",
    "infra/observability/grafana/dashboards/gym-baseline.json",
    "infra/observability/grafana/provisioning/dashboards/dashboards.yml",
    "infra/observability/grafana/provisioning/datasources/prometheus.yml",
    "infra/observability/postgres/postgres_exporter.yml",
    "infra/observability/postgres/queries.yaml",
    "infra/observability/prometheus/prometheus.yml",
    "infra/observability/prometheus/rules/gym-baseline.yml",
)
CONTROL_PATHS = (
    "scripts/deployment_control.py",
    "scripts/deployment_smoke.py",
)
GENERATED_PATHS = (
    "release/bound-release.json",
    "release/release-manifest.json",
)
REQUIRED_RUNBOOK = (
    "Phase 8 is not complete",
    "not proven",
    TEARDOWN_TOKEN,
    STAGING_PROJECT,
    PRODUCTION_PROJECT,
    RENDERED_BLACKBOX,
    "stop-forward-fix-or-restore",
    "rpo_proven",
    "StrictHostKeyChecking=yes",
    "default branch",
    "active deployment state",
    "credential helper",
    "/-/healthy",
    "incoming/staging",
)
BANNED_RUNBOOK = (
    "deployment is proven",
    "protected environment is proven",
    "backup is proven",
    "restore is proven",
    "rollback is proven",
    "staging deploy is proven",
)
FORWARD_BOUNDARY = "forward-only-until-schema-review"
STOP_BOUNDARY = "stop-forward-fix-or-restore"
Runner = Callable[..., "CommandResult"]


class DeployError(RuntimeError):
    """A deployment control check failed closed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class HostRequest:
    target: str
    operation: str
    schema_compatibility: str
    bundle: Path
    state_dir: Path
    env_file: Path
    host_root: Path
    running_projects: tuple[str, ...]
    free_disk_bytes: int
    mem_available_bytes: int
    fetch: Callable[[str], tuple[int, bytes, str]] | None = None
    sleeper: Callable[[float], None] | None = None
    clock: str = "20261021T000000Z"
    process_env: dict[str, str] = field(default_factory=dict)


def project_name(target: str) -> str:
    if target == "staging":
        return STAGING_PROJECT
    if target == "production":
        return PRODUCTION_PROJECT
    raise DeployError("deployment target is not staging or production")


def incoming_dir(target: str) -> str:
    project_name(target)
    return f"{INCOMING_PARENT}/{target}"


def host_lock_path(host_root: Path) -> Path:
    return host_root / "var" / "lib" / "gym-deploy" / HOST_LOCK_NAME


def assert_operation_schema(operation: str, schema_compatibility: str) -> None:
    if operation not in OPERATIONS or schema_compatibility not in SCHEMA_CHOICES:
        raise DeployError("deployment operation is not an allowlisted choice")
    if operation == "deploy" and schema_compatibility != "not-applicable":
        raise DeployError(
            "forward deploy does not take a schema-compatibility approval"
        )
    if operation == "rollback-app" and schema_compatibility != "reviewed-authorized":
        raise DeployError(STOP_BOUNDARY)


def deployment_steps(
    target: str, operation: str, schema_compatibility: str
) -> tuple[str, ...]:
    project_name(target)
    if operation == "rollback-app" and schema_compatibility != "reviewed-authorized":
        return (
            "acquire-host-lock",
            "acquire-lock",
            "verify-bundle",
            "preflight",
            "refuse-rollback",
        )
    if operation == "rollback-app":
        return (
            "acquire-host-lock",
            "acquire-lock",
            "verify-bundle",
            "preflight",
            "render-blackbox",
            "check-resources",
            "verify-postgres-identity",
            "pull-app-digests",
            "start-app",
            "smoke",
            "verify-running-images",
            "write-evidence",
            "release-lock",
        )
    if operation != "deploy":
        raise DeployError("deployment operation is not an allowlisted choice")
    backup = "backup-checkpoint" if target == "production" else "skip-backup-checkpoint"
    return (
        "acquire-host-lock",
        "acquire-lock",
        "verify-bundle",
        "preflight",
        "render-blackbox",
        "check-resources",
        "pull-digests",
        "start-postgres",
        backup,
        "migrate",
        "start-services",
        "smoke",
        "verify-running-images",
        "write-evidence",
        "release-lock",
    )


def require_sha(value: str, label: str) -> str:
    if SHA_RE.fullmatch(value) is None:
        raise DeployError(f"{label} must be 40 lowercase hex characters")
    return value


def require_tag(value: str) -> str:
    if SEMVER_RE.fullmatch(value) is None:
        raise DeployError("release tag must be vMAJOR.MINOR.PATCH")
    return value


def require_run_id(value: str) -> str:
    if RUN_ID_RE.fullmatch(value) is None:
        raise DeployError("release run id is invalid")
    return value


def require_repository(value: str) -> str:
    if REPO_RE.fullmatch(value) is None:
        raise DeployError("repository name is invalid")
    return value


def normalize_public_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if HOST_RE.fullmatch(host) is None or "*" in host:
        raise DeployError("PUBLIC_HOST is not a DNS name")
    return host


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def seal_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in PROOF_KEYS:
        if payload.get(key) is True:
            raise DeployError("evidence cannot claim a live proof")
    sealed = dict(payload)
    for key in PROOF_KEYS:
        sealed[key] = False
    return sealed


def new_evidence(**fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {key: False for key in PROOF_KEYS}
    payload["commands_completed"] = False
    payload["attestation_command"] = "not-run"
    payload["host_attestation_recheck"] = "not-rechecked-on-host"
    payload.update(fields)
    return seal_evidence(payload)


def image_name(reference: str) -> str:
    repository = reference.rsplit("@", 1)[0]
    if repository.startswith("docker.io/"):
        repository = repository[len("docker.io/") :]
    return repository


def assert_digest_reference(reference: str) -> str:
    if reference.count("@") != 1:
        raise DeployError("image reference must be a single digest")
    repository, digest = reference.rsplit("@", 1)
    if DIGEST_RE.fullmatch(digest) is None or ":" in repository.rsplit("/", 1)[-1]:
        raise DeployError("image reference is mutable or not sha256")
    if reference.endswith(":latest") or ":latest@" in reference:
        raise DeployError("image reference is mutable or not sha256")
    return reference


def render_blackbox_text(source: str, public_host: str) -> str:
    host = normalize_public_host(public_host)
    if "insecure_skip_verify: false" not in source:
        raise DeployError("blackbox TLS verification must stay enabled")
    if re.search(r"(?m)^[ \t]*insecure_skip_verify:[ \t]*true[ \t]*$", source):
        raise DeployError("blackbox must not disable certificate verification")
    matches = list(SERVER_NAME_RE.finditer(source))
    if len(matches) != 1:
        raise DeployError("blackbox config must contain one server_name")
    rendered = SERVER_NAME_RE.sub(
        lambda match: f"{match.group('indent')}server_name: {host}",
        source,
        count=1,
    )
    if (
        f"server_name: {host}" not in rendered
        or "insecure_skip_verify: false" not in rendered
    ):
        raise DeployError("rendered blackbox config lost TLS verification")
    if len(SERVER_NAME_RE.findall(rendered)) != 1:
        raise DeployError("rendered blackbox config must contain one server_name")
    return rendered


def write_rendered_blackbox(source: Path, destination: Path, public_host: str) -> str:
    if source.resolve() == destination.resolve():
        raise DeployError(
            "rendered blackbox config must not replace the tracked source"
        )
    if source.is_symlink() or not source.is_file():
        raise DeployError("blackbox source must be a regular file")
    original = source.read_text(encoding="utf-8")
    rendered = render_blackbox_text(original, public_host)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise DeployError("rendered blackbox path is a symlink")
    destination.write_text(rendered, encoding="utf-8")
    os.chmod(destination, 0o644)
    if source.read_text(encoding="utf-8") != original:
        raise DeployError("rendering changed the tracked blackbox source")
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _release_helpers() -> Any:
    import release_supply_chain

    return release_supply_chain


def _statement(item: object) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    verification = item.get("verificationResult")
    if isinstance(verification, dict) and isinstance(
        verification.get("statement"), dict
    ):
        statement = verification["statement"]
        return statement if isinstance(statement, dict) else None
    statement = item.get("statement")
    if isinstance(statement, dict):
        return statement
    payload = item.get("payload")
    if not isinstance(payload, str):
        return None
    try:
        decoded = base64.b64decode(payload, validate=True)
        statement = json.loads(decoded)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return statement if isinstance(statement, dict) else None


def _repository_values(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "repository" and isinstance(item, str):
                found.append(item)
            else:
                _repository_values(item, found)
    elif isinstance(value, list):
        for item in value:
            _repository_values(item, found)


def judge_attestation_payload(
    payload: object, *, image_ref: str, repository: str
) -> dict[str, str]:
    if not isinstance(payload, list) or not payload:
        raise DeployError("attestation verification returned no bundles")
    image_ref = assert_digest_reference(image_ref)
    repository_name, digest = image_ref.rsplit("@", 1)
    hexdigest = digest.split(":", 1)[1]
    predicate = ""
    for item in payload:
        statement = _statement(item)
        if statement is None:
            continue
        kind = statement.get("predicateType")
        subjects = statement.get("subject")
        if not isinstance(kind, str) or not kind.startswith(SLSA_PREFIX):
            continue
        if not isinstance(subjects, list):
            continue
        for subject in subjects:
            if not isinstance(subject, dict):
                continue
            name = subject.get("name")
            digests = subject.get("digest")
            if not isinstance(digests, dict) or digests.get("sha256") != hexdigest:
                continue
            if name not in {repository_name, image_ref}:
                continue
            predicate = kind
    if not predicate:
        raise DeployError("attestation subject does not match the image digest")
    found: list[str] = []
    _repository_values(payload, found)
    if repository not in found:
        raise DeployError("attestation is not bound to the repository")
    return {
        "digest": digest,
        "predicate_type": predicate,
        "repository": repository,
    }


def attestation_command(image_ref: str, repository: str) -> tuple[str, ...]:
    image_ref = assert_digest_reference(image_ref)
    require_repository(repository)
    if not image_ref.startswith("ghcr.io/"):
        raise DeployError("attestation subject must be a GHCR digest")
    return (
        "cosign",
        "verify-attestation",
        "--type",
        "slsaprovenance",
        "--certificate-identity-regexp",
        (
            rf"^https://github\.com/{re.escape(repository)}/\.github/workflows/"
            rf"{RELEASE_WORKFLOW}@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$"
        ),
        "--certificate-oidc-issuer",
        "https://token.actions.githubusercontent.com",
        "--output",
        "json",
        image_ref,
    )


def verify_image_attestation(
    image_ref: str, repository: str, runner: Runner
) -> dict[str, str]:
    result = runner(attestation_command(image_ref, repository), timeout=120)
    if result.returncode != 0:
        raise DeployError("attestation verification failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        try:
            payload = [
                json.loads(line)
                for line in result.stdout.splitlines()
                if line.strip()
            ]
        except json.JSONDecodeError:
            raise DeployError("attestation verification output is invalid") from exc
    if isinstance(payload, dict):
        payload = [payload]
    return judge_attestation_payload(
        payload, image_ref=image_ref, repository=repository
    )


def select_release_artifact(artifacts: object) -> dict[str, Any]:
    if not isinstance(artifacts, dict):
        raise DeployError("release artifact list is invalid")
    items = artifacts.get("artifacts")
    if not isinstance(items, list):
        raise DeployError("release artifact list is invalid")
    matches = []
    for item in items:
        if not isinstance(item, dict) or item.get("name") != "release-manifest":
            continue
        if item.get("expired") is True:
            raise DeployError("release manifest artifact is expired")
        matches.append(item)
    if len(matches) != 1:
        raise DeployError("release manifest artifact is missing")
    return matches[0]


def bind_release(
    *,
    manifest: object,
    run: object,
    tag_commit_sha: str,
    repository: str,
    expected_sha: str,
    expected_tag: str,
    expected_run_id: str,
    caller_sha: str,
    caller_ref: str,
    default_branch: str,
    control_head_sha: str,
    attestations: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    repository = require_repository(repository)
    expected_sha = require_sha(expected_sha, "release SHA")
    caller_sha = require_sha(caller_sha, "caller SHA")
    control_head_sha = require_sha(control_head_sha, "control SHA")
    expected_tag = require_tag(expected_tag)
    expected_run_id = require_run_id(expected_run_id)
    tag_commit_sha = require_sha(tag_commit_sha, "tag commit")
    default_branch = _require_branch(default_branch)
    if caller_ref != f"refs/heads/{default_branch}":
        raise DeployError("deployment workflow ref is not the default branch")
    if caller_sha != control_head_sha:
        raise DeployError(
            "caller SHA is not the default branch head; refusing an untrusted ref "
            "or a moved default branch"
        )
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise DeployError("release manifest schema is invalid")
    if manifest.get("schema_version") != 2:
        raise DeployError("release manifest schema is invalid")
    if not isinstance(run, dict):
        raise DeployError("release run metadata is invalid")
    head_repository = run.get("head_repository")
    head_name = (
        head_repository.get("full_name") if isinstance(head_repository, dict) else ""
    )
    if str(head_name).casefold() != repository.casefold():
        raise DeployError("release run is not from this repository")
    if run.get("path") != RELEASE_PATH or run.get("event") not in {
        "push",
        "workflow_dispatch",
    }:
        raise DeployError("release run is not the image-release workflow")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise DeployError("release run is not a successful completed run")
    if str(run.get("id")) != expected_run_id:
        raise DeployError("release run id does not match the requested run")
    if str(run.get("head_sha")) != expected_sha or tag_commit_sha != expected_sha:
        raise DeployError("release SHA does not match the workflow run and tag")
    if str(run.get("head_branch")) != expected_tag:
        raise DeployError("release run is not the expected tag")
    images = manifest.get("images")
    build = manifest.get("build")
    if not isinstance(images, dict) or set(images) != IMAGE_KEYS:
        raise DeployError("release manifest images are invalid")
    if not isinstance(build, dict) or set(build) != BUILD_KEYS:
        raise DeployError("release manifest build identity is invalid")
    if build.get("workflow") != RELEASE_WORKFLOW:
        raise DeployError("release manifest workflow does not match the run")
    if str(build.get("run_id")) != expected_run_id:
        raise DeployError("release manifest run id does not match the run")
    if str(build.get("run_attempt")) != str(run.get("run_attempt")):
        raise DeployError("release manifest run attempt does not match the run")
    if manifest.get("git_commit") != expected_sha:
        raise DeployError("release manifest commit does not match the run")
    if manifest.get("git_ref_name") != expected_tag or manifest.get("git_ref") != (
        f"refs/tags/{expected_tag}"
    ):
        raise DeployError("release manifest tag does not match the run")
    if TIMESTAMP_RE.fullmatch(str(build.get("timestamp"))) is None:
        raise DeployError("release manifest timestamp is invalid")
    helpers = _release_helpers()
    if manifest.get("runtime_baseline") != helpers.runtime_baseline():
        raise DeployError("runtime baseline does not match the release module")
    owner = helpers.normalize_owner(repository.split("/", 1)[0])
    bound_images: dict[str, str] = {}
    try:
        for name in ("api", "web", "postgres"):
            image_name_key = {
                "api": "gym-api",
                "web": "gym-web",
                "postgres": "gym-postgres",
            }[name]
            reference = images.get(name)
            if not isinstance(reference, str) or reference.count("@") != 1:
                raise DeployError("release manifest images are invalid")
            repository_name, digest = reference.rsplit("@", 1)
            if repository_name != helpers.image_repository(owner, image_name_key):
                raise DeployError("image repository does not match the release owner")
            bound_images[name] = helpers.digest_reference(repository_name, digest)
            if f"sha-{caller_sha}" in reference or ":latest" in reference:
                raise DeployError("image identity must be a digest")
    except helpers.ReleaseError as exc:
        raise DeployError(str(exc)) from exc
    if set(attestations) != IMAGE_KEYS:
        raise DeployError("attestation results must cover api, web, and postgres")
    checked: dict[str, dict[str, str]] = {}
    for name, image in bound_images.items():
        record = dict(attestations[name])
        expected = judge_attestation_payload(
            [
                {
                    "verificationResult": {
                        "statement": {
                            "predicateType": record.get("predicate_type"),
                            "subject": [
                                {
                                    "name": image.rsplit("@", 1)[0],
                                    "digest": {"sha256": image.rsplit(":", 1)[1]},
                                }
                            ],
                            "predicate": {
                                "buildDefinition": {
                                    "externalParameters": {
                                        "workflow": {"repository": repository}
                                    }
                                }
                            },
                        }
                    }
                }
            ],
            image_ref=image,
            repository=repository,
        )
        if (
            record.get("digest") != expected["digest"]
            or record.get("repository") != repository
        ):
            raise DeployError("attestation subject does not match the image digest")
        checked[name] = expected
    bound = new_evidence(
        schema_version=1,
        repository=repository,
        release_sha=expected_sha,
        caller_sha=caller_sha,
        control_sha=caller_sha,
        control_ref=caller_ref,
        default_branch=default_branch,
        release_tag=expected_tag,
        run_id=expected_run_id,
        run_attempt=str(run.get("run_attempt")),
        workflow=RELEASE_WORKFLOW,
        images=bound_images,
        attestations=checked,
        release_manifest=manifest,
        attestation_command="succeeded",
    )
    if bound["release_sha"] == bound["caller_sha"]:
        bound["sha_relationship"] = "caller-matches-release"
    else:
        bound["sha_relationship"] = "caller-is-workflow-sha"
    return seal_evidence(bound)


def confirm_attestations(bound: Mapping[str, Any], runner: Runner) -> None:
    images = bound.get("images")
    repository = bound.get("repository")
    attestations = bound.get("attestations")
    if not isinstance(images, dict) or not isinstance(attestations, dict):
        raise DeployError("bound release is missing attestation records")
    if not isinstance(repository, str):
        raise DeployError("bound release repository is invalid")
    for name in ("api", "web", "postgres"):
        fresh = verify_image_attestation(str(images[name]), repository, runner)
        if fresh != dict(attestations[name]):
            raise DeployError("attestation changed after the release binding")


def _member_name(name: str) -> str:
    if not name or name.startswith("/") or "\\" in name or len(name) > 100:
        raise DeployError("bundle path is unsafe")
    parts = Path(name).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise DeployError("bundle path is unsafe")
    lowered = name.lower()
    if (
        ".example" in lowered
        or "/environments/" in f"/{lowered}"
        or lowered.endswith(".env")
    ):
        raise DeployError("bundle contains a secret example")
    return name


def _read_regular(path: Path, root: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise DeployError("bundle source must be a regular file")
    resolved = path.resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise DeployError("bundle source escapes its root")
    data = path.read_bytes()
    if len(data) > MAX_MEMBER_BYTES:
        raise DeployError("bundle source is too large")
    markers = (
        b"BEGIN " + b"OPENSSH PRIVATE KEY",
        b"BEGIN " + b"PRIVATE KEY",
        b"BEGIN " + b"RSA PRIVATE KEY",
    )
    if any(marker in data for marker in markers):
        raise DeployError("bundle contains a private key")
    return data


def bundle_members(
    source_repo: Path, control_root: Path, bound: Mapping[str, Any]
) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for relative in INFRA_PATHS:
        members[relative] = _read_regular(source_repo / relative, source_repo)
    for relative in CONTROL_PATHS:
        members[relative] = _read_regular(control_root / relative, control_root)
    sealed = seal_evidence(bound)
    manifest = sealed.get("release_manifest")
    if not isinstance(manifest, dict):
        raise DeployError("bound release is missing the release manifest")
    members["release/bound-release.json"] = canonical_json(sealed)
    members["release/release-manifest.json"] = canonical_json(manifest)
    return members


def load_strict_json(data: bytes) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in pairs:
            if key in parsed:
                raise DeployError("bundle checksum manifest repeats a key")
            parsed[key] = value
        return parsed

    try:
        return json.loads(data.decode("ascii"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeployError("bundle checksum manifest is invalid") from exc


def _checksum_document(members: Mapping[str, bytes]) -> bytes:
    files = [
        {
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": "0644",
        }
        for name, data in sorted(members.items())
    ]
    return canonical_json({"schema_version": 1, "files": files})


def write_bundle(members: Mapping[str, bytes], output: Path) -> None:
    payload = dict(members)
    payload[BUNDLE_MANIFEST] = _checksum_document(members)
    if sum(len(item) for item in payload.values()) > MAX_BUNDLE_BYTES:
        raise DeployError("deployment bundle is too large")
    if output.is_symlink():
        raise DeployError("bundle output is a symlink")
    with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(payload):
            data = payload[name]
            info = tarfile.TarInfo(_member_name(name))
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, fileobj=io.BytesIO(data))
    os.chmod(output, 0o644)


def build_bundle(
    source_repo: Path, control_root: Path, bound: Mapping[str, Any], output: Path
) -> None:
    write_bundle(bundle_members(source_repo, control_root, bound), output)


def read_bundle(path: Path) -> dict[str, tuple[bytes, int]]:
    if path.is_symlink() or not path.is_file():
        raise DeployError("deployment bundle must be a regular file")
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise DeployError("deployment bundle is too large")
    members: dict[str, tuple[bytes, int]] = {}
    with tarfile.open(path, "r:") as archive:
        for member in archive.getmembers():
            name = _member_name(member.name)
            if member.issym() or member.islnk() or not member.isfile():
                raise DeployError("bundle member must be a regular file")
            mode = member.mode & 0o7777
            if mode != 0o644:
                raise DeployError("bundle member mode is not allowlisted")
            if member.size > MAX_MEMBER_BYTES:
                raise DeployError("bundle member is too large")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise DeployError("bundle member is unreadable")
            data = extracted.read()
            if len(data) != member.size:
                raise DeployError("bundle member size does not match its header")
            if name in members:
                raise DeployError("bundle member is duplicated")
            members[name] = (data, mode)
    return members


def verify_bundle_members(members: Mapping[str, tuple[bytes, int]]) -> dict[str, Any]:
    if BUNDLE_MANIFEST not in members:
        raise DeployError("bundle checksum manifest is missing")
    document = load_strict_json(members[BUNDLE_MANIFEST][0])
    if not isinstance(document, dict):
        raise DeployError("bundle checksum manifest is invalid")
    files = document.get("files")
    if document.get("schema_version") != 1 or not isinstance(files, list):
        raise DeployError("bundle checksum manifest is invalid")
    listed = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise DeployError("bundle checksum entry is invalid")
        name = entry.get("path")
        if not isinstance(name, str):
            raise DeployError("bundle checksum entry is invalid")
        checked = _member_name(name)
        if checked in listed:
            raise DeployError("bundle checksum manifest repeats a path")
        listed[checked] = entry
    expected = set(listed) | {BUNDLE_MANIFEST}
    allowed = (
        set(INFRA_PATHS) | set(CONTROL_PATHS) | set(GENERATED_PATHS) | {BUNDLE_MANIFEST}
    )
    if set(members) != allowed or set(members) != expected:
        raise DeployError("bundle files do not match the checksum manifest")
    for name, entry in listed.items():
        data, mode = members[name]
        digest = hashlib.sha256(data).hexdigest()
        if entry.get("sha256") != digest or entry.get("size") != len(data):
            raise DeployError("bundle file checksum does not match")
        if entry.get("mode") != "0644" or mode != 0o644:
            raise DeployError("bundle member mode is not allowlisted")
    compose = members["infra/compose/docker-compose.prod.yml"][0].decode("utf-8")
    if "./blackbox.rendered.yml:/etc/blackbox/blackbox.yml:ro" not in compose:
        raise DeployError("bundle compose does not mount the rendered blackbox config")
    if "docker.sock" in compose or ":latest" in compose:
        raise DeployError("bundle compose is unsafe")
    blackbox = members["infra/observability/blackbox/blackbox.yml"][0].decode("utf-8")
    if "insecure_skip_verify: false" not in blackbox:
        raise DeployError("bundle blackbox source disables certificate verification")
    bound = json.loads(members["release/bound-release.json"][0].decode("utf-8"))
    manifest = json.loads(members["release/release-manifest.json"][0].decode("utf-8"))
    if not isinstance(bound, dict) or bound.get("release_manifest") != manifest:
        raise DeployError("bundle release manifest does not match its binding")
    seal_evidence(bound)
    images = bound.get("images")
    if not isinstance(images, dict):
        raise DeployError("bundle images are missing")
    for reference in images.values():
        assert_digest_reference(str(reference))
    return bound


def verify_bundle(path: Path) -> dict[str, Any]:
    return verify_bundle_members(read_bundle(path))


def extract_bundle(path: Path, destination: Path) -> dict[str, Any]:
    members = read_bundle(path)
    bound = verify_bundle_members(members)
    if destination.is_symlink():
        raise DeployError("bundle destination is a symlink")
    if destination.exists():
        for child in destination.rglob("*"):
            if child.is_symlink():
                raise DeployError("bundle destination contains a symlink")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, (data, mode) in members.items():
        target = destination / name
        if destination.resolve() not in target.resolve().parents:
            raise DeployError("bundle extract escapes the destination")
        if target.is_symlink():
            raise DeployError("bundle extract target is a symlink")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, mode)
    return bound


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise DeployError("host env line is invalid")
        key, value = line.split("=", 1)
        if ENV_NAME_RE.fullmatch(key) is None:
            raise DeployError("host env name is invalid")
        if any(token in value for token in ("$(", "`", "\n", "\r")):
            raise DeployError("host env value is not a literal")
        values[key] = value
    return values


def assert_owner_only(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise DeployError("host file must be a regular file")
    _assert_private_owner(path)


def _assert_private_owner(path: Path) -> None:
    info = path.stat()
    if info.st_mode & 0o077:
        raise DeployError("host file must be owner-only")
    if info.st_uid != os.geteuid() and info.st_uid != 0:
        raise DeployError("host file owner must be the deployment user or root")


def _secret_files(env_file: Path, host_env: Mapping[str, str], state_dir: Path) -> None:
    for key, value in host_env.items():
        if not key.endswith("_FILE"):
            continue
        secret = Path(value)
        if not secret.is_absolute() or secret.name.endswith(".example"):
            raise DeployError("secret file path is not a host-managed absolute path")
        assert_owner_only(secret)
        try:
            secret.relative_to(state_dir.resolve())
        except ValueError:
            pass
        else:
            raise DeployError("secret file must stay outside the deployment state")
        text = secret.read_text(encoding="utf-8")
        if not text.strip() or text.strip() == "replace-from-secret-store":
            raise DeployError("secret file is still a placeholder")
        del text


def assert_target_isolation(
    *,
    target: str,
    host_root: Path,
    running_projects: tuple[str, ...],
    public_host: str,
) -> None:
    production_env = host_root / "etc" / "gym" / "production"
    staging_env = host_root / "etc" / "gym" / "staging"
    if target == "staging" and (
        production_env.exists() or PRODUCTION_PROJECT in running_projects
    ):
        raise DeployError("staging cannot run beside production")
    if target == "production" and (
        staging_env.exists() or STAGING_PROJECT in running_projects
    ):
        raise DeployError("production cannot run beside temporary staging")
    other = production_env / "compose.env"
    if target == "staging" and other.is_file() and not other.is_symlink():
        other_host = parse_env_file(other.read_text(encoding="utf-8")).get(
            "PUBLIC_HOST", ""
        )
        if other_host and normalize_public_host(other_host) == public_host:
            raise DeployError("staging hostname must differ from production")


def overlay_images(
    host_env: Mapping[str, str],
    bound: Mapping[str, Any],
    operation: str,
    active: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    merged = dict(host_env)
    images = bound.get("images")
    if not isinstance(images, dict):
        raise DeployError("bound release images are missing")
    merged["API_IMAGE"] = assert_digest_reference(str(images["api"]))
    merged["WEB_IMAGE"] = assert_digest_reference(str(images["web"]))
    if operation == "rollback-app" and active is not None:
        active_images = active.get("images")
        if not isinstance(active_images, dict):
            raise DeployError("active deployment state images are invalid")
        for service, key in SERVICE_IMAGE_KEYS:
            if service in {"api", "web"}:
                continue
            merged[key] = assert_digest_reference(str(active_images[service]))
        if merged["POSTGRES_IMAGE"] != assert_digest_reference(
            str(active["postgres_image"])
        ):
            raise DeployError("active deployment state PostgreSQL digest disagrees")
    elif operation == "rollback-app":
        merged["POSTGRES_IMAGE"] = assert_digest_reference(
            str(host_env.get("POSTGRES_IMAGE", ""))
        )
    else:
        merged["POSTGRES_IMAGE"] = assert_digest_reference(str(images["postgres"]))
    for key, expected_name in THIRD_PARTY_IMAGES.items():
        reference = assert_digest_reference(str(merged.get(key, "")))
        merged[key] = reference
        if image_name(reference) != expected_name:
            raise DeployError(f"{key} repository is not allowlisted")
    for key in ("API_IMAGE", "WEB_IMAGE", "POSTGRES_IMAGE"):
        assert_digest_reference(str(merged[key]))
    return merged


def preflight(
    request: HostRequest, host_env: Mapping[str, str], bound: Mapping[str, Any]
) -> dict[str, str]:
    if not (
        request.operation == "rollback-app"
        and request.schema_compatibility != "reviewed-authorized"
    ):
        assert_operation_schema(request.operation, request.schema_compatibility)
    project = project_name(request.target)
    if (
        request.state_dir.name != request.target
        or request.target not in request.env_file.parts
    ):
        raise DeployError("state and env paths must name the deployment target")
    other = "production" if request.target == "staging" else "staging"
    if other in request.state_dir.parts or other in request.env_file.parts:
        raise DeployError("target paths must not use the other environment")
    assert_owner_only(request.env_file)
    public_host = normalize_public_host(str(host_env.get("PUBLIC_HOST", "")))
    assert_target_isolation(
        target=request.target,
        host_root=request.host_root,
        running_projects=request.running_projects,
        public_host=public_host,
    )
    if host_env.get("COMPOSE_PROJECT_NAME", project) != project:
        raise DeployError("compose project name does not match the target")
    for key, expected in (
        ("POSTGRES_DB", "gym_platform"),
        ("POSTGRES_ADMIN_USER", "gym_admin"),
        ("MIGRATION_DB_USER", "gym_migration"),
        ("RUNTIME_DB_USER", "gym_runtime"),
    ):
        if host_env.get(key) != expected:
            raise DeployError(
                "database identity does not match the production baseline"
            )
    passwords = [
        host_env.get("POSTGRES_ADMIN_PASSWORD", ""),
        host_env.get("MIGRATION_DB_PASSWORD", ""),
        host_env.get("RUNTIME_DB_PASSWORD", ""),
    ]
    if any(not item or item == "replace-from-secret-store" for item in passwords):
        raise DeployError("database passwords are placeholders")
    if len(set(passwords)) != 3:
        raise DeployError("database role passwords must differ")
    alert_mode = host_env.get("ALERT_DELIVERY_MODE", "")
    if request.target == "production":
        if alert_mode != "resend-email":
            raise DeployError("production alert delivery must use resend-email")
        for key in ("ALERT_EMAIL_FROM", "ALERT_EMAIL_TO"):
            if ALERT_EMAIL_RE.fullmatch(host_env.get(key, "")) is None:
                raise DeployError(f"{key} is not a valid production alert address")
    elif alert_mode != "non-production":
        raise DeployError("staging alert delivery must stay non-production")
    _secret_files(request.env_file, host_env, request.state_dir)
    _assert_ghcr_config(host_env, request.state_dir)
    active = None
    if (
        request.operation == "rollback-app"
        and request.schema_compatibility == "reviewed-authorized"
    ):
        active = read_active_state(
            request.state_dir / ACTIVE_STATE_NAME, request.target
        )
    return overlay_images(host_env, bound, request.operation, active)


def check_resources(free_disk_bytes: int, mem_available_bytes: int) -> None:
    if free_disk_bytes < MIN_FREE_DISK_BYTES:
        raise DeployError("free disk is below the deployment reserve")
    if mem_available_bytes < MIN_MEM_AVAILABLE_BYTES:
        raise DeployError("available memory is below the deployment reserve")


def parse_mem_available(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            return int(parts[1]) * 1024
    raise DeployError("MemAvailable is missing")


def acquire_deploy_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise DeployError("deployment lock is held") from exc
    return descriptor


def release_deploy_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def compose_argv(project: str, compose_file: Path, *args: str) -> tuple[str, ...]:
    if project not in {STAGING_PROJECT, PRODUCTION_PROJECT}:
        raise DeployError("compose project is not allowlisted")
    if any(
        arg.startswith("-e") or "PASSWORD" in arg or "SECRET" in arg for arg in args
    ):
        raise DeployError("compose command must not carry secret arguments")
    return (
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(compose_file.parent),
        "-f",
        str(compose_file),
        *args,
    )


def compose_process_env(host_env: Mapping[str, str]) -> dict[str, str]:
    merged = {"LANG": "C.UTF-8"}
    for key in ("PATH", "HOME"):
        value = os.environ.get(key)
        if value:
            merged[key] = value
    merged.update(host_env)
    return merged


def ssh_process_env() -> dict[str, str]:
    return {
        key: value
        for key in ("PATH", "HOME", "LANG", "USER", "LOGNAME")
        if (value := os.environ.get(key))
    }


def _assert_ghcr_config(host_env: Mapping[str, str], state_dir: Path) -> None:
    raw = host_env.get("DOCKER_CONFIG", "")
    directory = Path(raw)
    if not raw or not directory.is_absolute():
        raise DeployError(
            "GHCR read credentials are not configured in the host credential helper"
        )
    if directory.is_symlink() or not directory.is_dir():
        raise DeployError(
            "GHCR read credentials are not configured in the host credential helper"
        )
    try:
        directory.resolve().relative_to(state_dir.resolve())
    except ValueError:
        pass
    else:
        raise DeployError("GHCR docker config must stay outside the deployment state")
    _assert_private_owner(directory)
    config = directory / "config.json"
    assert_owner_only(config)
    document = load_strict_json(config.read_bytes())
    if not isinstance(document, dict):
        raise DeployError(
            "GHCR read credentials are not configured in the host credential helper"
        )
    helpers = document.get("credHelpers")
    helper = helpers.get("ghcr.io") if isinstance(helpers, dict) else None
    store = document.get("credsStore")
    if not (isinstance(helper, str) and helper) and not (
        isinstance(store, str) and store
    ):
        raise DeployError(
            "GHCR read credentials are not configured in the host credential helper"
        )
    auths = document.get("auths", {})
    if auths not in ({}, None):
        raise DeployError("GHCR config must not store inline credentials")


def active_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = {
        "schema_version": 1,
        "target": payload["target"],
        "project": payload["project"],
        "release_sha": payload["release_sha"],
        "release_tag": payload["release_tag"],
        "run_id": payload["run_id"],
        "public_host": payload["public_host"],
        "postgres_image": payload["postgres_image"],
        "images": payload["images"],
    }
    return {
        "schema_version": 1,
        "payload": body,
        "sha256": hashlib.sha256(canonical_json(body)).hexdigest(),
    }


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:])
        if written <= 0:
            raise DeployError("active deployment state write was incomplete")
        offset += written


def write_active_state(path: Path, payload: Mapping[str, Any]) -> None:
    if path.is_symlink():
        raise DeployError("active deployment state is a symlink")
    parent = path.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise DeployError("active deployment state directory is unsafe")
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise DeployError("active deployment state directory is unsafe")
    data = canonical_json(active_document(payload))
    temporary = parent / f".{path.name}.{os.getpid()}.tmp"
    if temporary.is_symlink() or parent.resolve() not in temporary.resolve().parents:
        raise DeployError("active deployment state temporary file is unsafe")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except FileExistsError as exc:
        raise DeployError("active deployment state temporary file exists") from exc
    try:
        _write_all(descriptor, data)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        if not temporary.is_symlink():
            temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    if temporary.is_symlink() or temporary.stat().st_mode & 0o077:
        if not temporary.is_symlink():
            temporary.unlink()
        raise DeployError("active deployment state temporary file is unsafe")
    if path.is_symlink():
        temporary.unlink()
        raise DeployError("active deployment state is a symlink")
    os.replace(temporary, path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise DeployError("active deployment state is unsafe")
    directory = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_active_state(path: Path, target: str) -> dict[str, Any]:
    assert_owner_only(path)
    document = load_strict_json(path.read_bytes())
    if not isinstance(document, dict):
        raise DeployError("active deployment state is invalid")
    payload = document.get("payload")
    digest = document.get("sha256")
    if not isinstance(payload, dict) or not isinstance(digest, str):
        raise DeployError("active deployment state is invalid")
    if digest != hashlib.sha256(canonical_json(payload)).hexdigest():
        raise DeployError("active deployment state checksum does not match")
    if payload.get("target") != target or payload.get("project") != project_name(
        target
    ):
        raise DeployError("active deployment state is for another target")
    images = payload.get("images")
    if not isinstance(images, dict) or set(images) != {
        name for name, _key in SERVICE_IMAGE_KEYS
    }:
        raise DeployError("active deployment state images are invalid")
    postgres = assert_digest_reference(str(payload.get("postgres_image", "")))
    if postgres != assert_digest_reference(str(images.get("postgres", ""))):
        raise DeployError("active deployment state PostgreSQL digest disagrees")
    for name, _key in SERVICE_IMAGE_KEYS:
        images[name] = assert_digest_reference(str(images[name]))
    payload["postgres_image"] = postgres
    return payload


def _running_container(project: str, service: str, runner: Runner) -> tuple[str, str]:
    allowed = {name for name, _key in SERVICE_IMAGE_KEYS}
    if service not in allowed or project not in {STAGING_PROJECT, PRODUCTION_PROJECT}:
        raise DeployError("running service is not allowlisted")
    result = runner(
        (
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--filter",
            "status=running",
            "--no-trunc",
            "--format",
            "{{.ID}}",
        ),
        timeout=30,
    )
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(ids) != 1:
        raise DeployError("running service container count is not one")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ids[0]) is None:
        raise DeployError("running service container count is not one")
    inspected = runner(
        ("docker", "inspect", "--format", "{{.Config.Image}}", ids[0]),
        timeout=30,
    )
    if inspected.returncode != 0:
        raise DeployError("running image is not the expected digest")
    return ids[0], assert_digest_reference(inspected.stdout.strip())


def verify_running_images(
    project: str, expected: Mapping[str, str], runner: Runner
) -> dict[str, str]:
    found: dict[str, str] = {}
    for service, key in SERVICE_IMAGE_KEYS:
        _container_id, image = _running_container(project, service, runner)
        if image != assert_digest_reference(str(expected[key])):
            raise DeployError("running image is not the expected digest")
        found[service] = image
    return found


def assert_pulled(reference: str, runner: Runner) -> None:
    reference = assert_digest_reference(reference)
    result = runner(
        ("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", reference),
        timeout=60,
    )
    if (
        result.returncode != 0
        or reference not in result.stdout
        or ":latest" in result.stdout
    ):
        raise DeployError("pulled image does not match the digest")


def _run_checked(
    runner: Runner, command: tuple[str, ...], timeout: int
) -> CommandResult:
    if "login" in command and "docker" in command:
        raise DeployError("registry login must not take a command-line password")
    for part in command:
        if part in {"--password", "--password-stdin"} or part.startswith("--password="):
            raise DeployError("registry login must not take a command-line password")
        if "replace-from-secret-store" in part or part.startswith("eyJ"):
            raise DeployError("command argument contains a secret")
    result = runner(command, timeout=timeout)
    if result.returncode != 0:
        raise DeployError("deployment command failed")
    return result


def _redact(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if len(secret) >= 8:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _write_evidence(path: Path, payload: Mapping[str, Any], secrets: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DeployError("evidence path is a symlink")
    text = _redact(canonical_json(seal_evidence(payload)).decode("ascii"), secrets)
    path.write_text(text, encoding="ascii")
    os.chmod(path, 0o640)


def execute_deployment(request: HostRequest, runner: Runner) -> dict[str, Any]:
    steps = deployment_steps(
        request.target, request.operation, request.schema_compatibility
    )
    evidence = new_evidence(
        target=request.target,
        operation=request.operation,
        schema_compatibility=request.schema_compatibility,
        rollback_boundary=FORWARD_BOUNDARY,
        outcome="started",
    )
    secrets: list[str] = []
    descriptor: int | None = None
    host_descriptor: int | None = None
    failed: DeployError | None = None
    try:
        for name in steps:
            evidence["current_step"] = name
            if name == "acquire-host-lock":
                host_descriptor = acquire_deploy_lock(host_lock_path(request.host_root))
                continue
            if name == "acquire-lock":
                descriptor = acquire_deploy_lock(request.state_dir / "deploy.lock")
                continue
            if name == "verify-bundle":
                evidence["bound"] = {
                    key: extract_bundle(request.bundle, request.state_dir / "src")[key]
                    for key in (
                        "release_sha",
                        "caller_sha",
                        "release_tag",
                        "run_id",
                        "images",
                    )
                }
                _assert_running_script(request.state_dir / "src")
                continue
            if name == "preflight":
                host_env = parse_env_file(request.env_file.read_text(encoding="utf-8"))
                secrets.extend(
                    value
                    for key, value in host_env.items()
                    if "PASSWORD" in key or "SECRET" in key or key.endswith("_KEY")
                )
                bound = json.loads(
                    (
                        request.state_dir / "src" / "release" / "bound-release.json"
                    ).read_text(encoding="utf-8")
                )
                overlay = preflight(request, host_env, bound)
                request.process_env.update(overlay)
                evidence["images"] = {
                    key: overlay[key]
                    for key in (
                        "PUBLIC_HOST",
                        "API_IMAGE",
                        "WEB_IMAGE",
                        "POSTGRES_IMAGE",
                        *THIRD_PARTY_IMAGES,
                    )
                }
                evidence["project"] = project_name(request.target)
                continue
            if name == "refuse-rollback":
                evidence["rollback_boundary"] = STOP_BOUNDARY
                evidence["outcome"] = "refused"
                raise DeployError(STOP_BOUNDARY)
            if name == "render-blackbox":
                source = (
                    request.state_dir
                    / "src"
                    / "infra"
                    / "observability"
                    / "blackbox"
                    / "blackbox.yml"
                )
                destination = (
                    request.state_dir / "src" / "infra" / "compose" / RENDERED_BLACKBOX
                )
                host_env = evidence["images"]
                evidence["blackbox_sha256"] = write_rendered_blackbox(
                    source, destination, str(host_env.get("PUBLIC_HOST", ""))
                )
                continue
            if name == "check-resources":
                check_resources(request.free_disk_bytes, request.mem_available_bytes)
                continue
            if name in {
                "pull-digests",
                "pull-app-digests",
                "start-postgres",
                "backup-checkpoint",
                "skip-backup-checkpoint",
                "migrate",
                "start-services",
                "start-app",
            }:
                _execute_compose_step(name, request, evidence, runner)
                continue
            if name == "verify-postgres-identity":
                _verify_postgres_identity(request, evidence, runner)
                continue
            if name == "smoke":
                _execute_smoke(request, evidence, runner)
                continue
            if name == "verify-running-images":
                _verify_and_record_images(request, evidence, runner)
                continue
            if name == "write-evidence":
                evidence["commands_completed"] = True
                evidence["outcome"] = "commands-completed"
                continue
            if name == "release-lock":
                if descriptor is not None:
                    release_deploy_lock(descriptor)
                    descriptor = None
                continue
            raise DeployError("unknown deployment step")
    except DeployError as exc:
        failed = exc
        evidence["outcome"] = (
            evidence.get("outcome")
            if evidence.get("outcome") == "refused"
            else "failed"
        )
        evidence["failed_step"] = evidence.get("current_step", "")
        if evidence["current_step"] in {
            "smoke",
            "migrate",
            "start-services",
            "start-app",
        }:
            evidence["rollback_boundary"] = STOP_BOUNDARY
    except smoke.SmokeError as exc:
        failed = DeployError(str(exc))
        evidence["outcome"] = "failed"
        evidence["failed_step"] = evidence.get("current_step", "")
        evidence["rollback_boundary"] = STOP_BOUNDARY
    finally:
        if descriptor is not None:
            release_deploy_lock(descriptor)
        if host_descriptor is not None:
            release_deploy_lock(host_descriptor)
        stamp = request.clock
        _write_evidence(
            request.state_dir / "evidence" / f"{stamp}.json", evidence, secrets
        )
        _write_evidence(
            request.state_dir / "evidence" / "latest.json", evidence, secrets
        )
    if failed is not None:
        raise failed
    return evidence


def _assert_running_script(root: Path) -> None:
    current = Path(__file__).resolve()
    bundled = root / "scripts" / "deployment_control.py"
    sibling = current.parent / "deployment_smoke.py"
    bundled_smoke = root / "scripts" / "deployment_smoke.py"
    if (
        current.read_bytes() != bundled.read_bytes()
        or sibling.read_bytes() != bundled_smoke.read_bytes()
    ):
        raise DeployError("running deployment control does not match the bundle")


def _execute_compose_step(
    name: str, request: HostRequest, evidence: Mapping[str, Any], runner: Runner
) -> None:
    project = project_name(request.target)
    compose_file = (
        request.state_dir / "src" / "infra" / "compose" / "docker-compose.prod.yml"
    )
    images = evidence["images"]
    if not isinstance(images, dict):
        raise DeployError("image overlay is missing")
    if name == "pull-digests":
        command = compose_argv(project, compose_file, "pull")
        for part in command:
            if "replace-from-secret-store" in part or part.startswith("eyJ"):
                raise DeployError("command argument contains a secret")
        result = runner(command, timeout=900)
        if result.returncode != 0:
            raise DeployError(
                "digest pull failed before migration; host GHCR read authorization "
                "is absent or unproven"
            )
        for key in ("API_IMAGE", "WEB_IMAGE", "POSTGRES_IMAGE"):
            assert_pulled(str(images[key]), runner)
        return
    if name == "pull-app-digests":
        for key in ("API_IMAGE", "WEB_IMAGE"):
            reference = str(images[key])
            _run_checked(runner, ("docker", "pull", reference), 900)
            assert_pulled(reference, runner)
        return
    if name == "start-postgres":
        _run_checked(
            runner,
            compose_argv(
                project, compose_file, "up", "-d", "--no-deps", "--wait", "postgres"
            ),
            900,
        )
        return
    if name == "skip-backup-checkpoint":
        return
    if name == "backup-checkpoint":
        backup_service = (
            "backup-full"
            if (request.state_dir / ACTIVE_STATE_NAME).is_file()
            else "backup-bootstrap"
        )
        profile = "backup" if backup_service == "backup-full" else "bootstrap"
        _run_checked(
            runner,
            compose_argv(
                project,
                compose_file,
                "--profile",
                profile,
                "run",
                "--rm",
                "--no-deps",
                backup_service,
            ),
            14700,
        )
        return
    if name == "migrate":
        command = compose_argv(
            project,
            compose_file,
            "--profile",
            "migrate",
            "run",
            "--rm",
            "--no-deps",
            "migrate",
        )
        if "DATABASE_ROLE_MODE=runtime" in command:
            raise DeployError("migration command must keep the migration identity")
        _run_checked(runner, command, 1800)
        return
    if name == "start-services":
        _run_checked(
            runner,
            compose_argv(
                project, compose_file, "up", "-d", "--wait", "--remove-orphans"
            ),
            900,
        )
        return
    if name == "start-app":
        command = compose_argv(
            project,
            compose_file,
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "api",
            "web",
            "caddy",
        )
        if "postgres" in command or "migrate" in command:
            raise DeployError("app rollback must not mutate PostgreSQL")
        _run_checked(runner, command, 900)
        return
    raise DeployError("unknown compose step")


def _verify_postgres_identity(
    request: HostRequest, evidence: dict[str, Any], runner: Runner
) -> None:
    active = read_active_state(request.state_dir / ACTIVE_STATE_NAME, request.target)
    images = evidence.get("images")
    if not isinstance(images, dict):
        raise DeployError("image overlay is missing")
    if images.get("POSTGRES_IMAGE") != active["postgres_image"]:
        raise DeployError("rollback PostgreSQL image is not the active digest")
    container_id, image = _running_container(
        project_name(request.target), "postgres", runner
    )
    if image != active["postgres_image"]:
        raise DeployError(
            "running PostgreSQL digest does not match the active deployment state"
        )
    evidence["postgres_container_id"] = container_id


def _verify_and_record_images(
    request: HostRequest, evidence: dict[str, Any], runner: Runner
) -> None:
    images = evidence.get("images")
    if not isinstance(images, dict):
        raise DeployError("image overlay is missing")
    project = project_name(request.target)
    if request.operation == "rollback-app":
        container_id, image = _running_container(project, "postgres", runner)
        active = read_active_state(
            request.state_dir / ACTIVE_STATE_NAME, request.target
        )
        if (
            container_id != evidence.get("postgres_container_id")
            or image != active["postgres_image"]
        ):
            raise DeployError("rollback restarted or changed PostgreSQL")
    verify_running_images(project, images, runner)
    if request.operation not in {"deploy", "rollback-app"}:
        return
    bound = evidence.get("bound")
    if not isinstance(bound, dict):
        raise DeployError("bound release is missing")
    service_images = {
        service: assert_digest_reference(str(images[key]))
        for service, key in SERVICE_IMAGE_KEYS
    }
    write_active_state(
        request.state_dir / ACTIVE_STATE_NAME,
        {
            "target": request.target,
            "project": project,
            "release_sha": bound["release_sha"],
            "release_tag": bound["release_tag"],
            "run_id": bound["run_id"],
            "public_host": images["PUBLIC_HOST"],
            "postgres_image": service_images["postgres"],
            "images": service_images,
        },
    )


def _execute_smoke(
    request: HostRequest, evidence: dict[str, Any], runner: Runner
) -> None:
    project = project_name(request.target)
    compose_file = (
        request.state_dir / "src" / "infra" / "compose" / "docker-compose.prod.yml"
    )
    prefix = compose_argv(project, compose_file)
    images = evidence["images"]
    host = (
        normalize_public_host(str(images.get("PUBLIC_HOST", "")))
        if isinstance(images, dict)
        else ""
    )
    fetch = request.fetch or smoke.fetch_https
    sleeper = request.sleeper or time.sleep
    smoke.run_public_probes(host, fetch, sleeper)
    result = _run_checked(
        runner, smoke.posture_command(prefix, "gym_admin", "gym_platform"), 60
    )
    posture = smoke.interpret_posture(json.loads(result.stdout.strip()))
    evidence["posture"] = posture
    _run_checked(runner, smoke.api_role_command(prefix), 60)
    _run_checked(runner, smoke.web_login_command(prefix), 60)
    for command in smoke.observability_commands(prefix):
        _run_checked(runner, command, 30)
    status = _run_checked(
        runner, compose_argv(project, compose_file, "ps", "--format", "json"), 60
    )
    smoke.interpret_compose_ps(status.stdout)
    evidence["smoke_commands_completed"] = True


def plan_teardown(
    *, target: str, confirmation: str, compose_file: Path
) -> tuple[str, ...]:
    if target != "staging" or project_name(target) != STAGING_PROJECT:
        raise DeployError("teardown is limited to temporary staging")
    if confirmation != TEARDOWN_TOKEN:
        raise DeployError("teardown requires the confirmation token")
    if PRODUCTION_PROJECT in compose_file.parts or "production" in compose_file.parts:
        raise DeployError("teardown must not select production files")
    return compose_argv(
        STAGING_PROJECT,
        compose_file,
        "down",
        "-v",
        "--remove-orphans",
    )


def ssh_options(known_hosts: Path, identity: Path) -> tuple[str, ...]:
    if known_hosts.name == "null" or known_hosts == Path("/dev/null"):
        raise DeployError("known hosts file must be a pinned file")
    checking = os.environ.get("SSH_STRICT_HOST_KEY_CHECKING", "yes")
    if checking != "yes":
        raise DeployError("SSH host key checking must be yes")
    return (
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=20",
        "-i",
        str(identity),
    )


def validate_ssh_material(
    host: str, user: str, private_key: str, host_key: str
) -> None:
    if HOST_RE.fullmatch(host) is None and not _ipv4(host):
        raise DeployError("SSH host is invalid")
    if USER_RE.fullmatch(user) is None:
        raise DeployError("SSH user is invalid")
    if (
        "\x00" in private_key
        or "BEGIN " not in private_key
        or "PRIVATE KEY" not in private_key
    ):
        raise DeployError("SSH private key is invalid")
    if HOST_KEY_RE.fullmatch(host_key.strip()) is None or host_key.strip().startswith(
        "*"
    ):
        raise DeployError("SSH host key is not a pinned key")


def write_ssh_files(
    directory: Path, host: str, user: str, private_key: str, host_key: str
) -> dict[str, Path]:
    validate_ssh_material(host, user, private_key, host_key)
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)
    identity = directory / "id"
    known_hosts = directory / "known_hosts"
    identity.write_text(
        private_key if private_key.endswith("\n") else private_key + "\n",
        encoding="utf-8",
    )
    known_hosts.write_text(f"{host} {host_key.strip()}\n", encoding="utf-8")
    os.chmod(identity, 0o600)
    os.chmod(known_hosts, 0o600)
    return {
        "user": Path(user),
        "host": Path(host),
        "identity": identity,
        "known_hosts": known_hosts,
    }


def transfer_commands(
    *,
    bundle: Path,
    script: Path,
    smoke_script: Path,
    host: str,
    user: str,
    known_hosts: Path,
    identity: Path,
    target: str,
    operation: str,
    schema_compatibility: str,
) -> tuple[tuple[str, ...], ...]:
    assert_operation_schema(operation, schema_compatibility)
    options = ssh_options(known_hosts, identity)
    destination = f"{user}@{host}"
    remote_dir = incoming_dir(target)
    remote = (
        f"python3 {remote_dir}/deployment_control.py host-apply "
        f"--bundle {remote_dir}/{BUNDLE_NAME} --target {target} "
        f"--operation {operation} --schema-compatibility {schema_compatibility}"
    )
    return (
        ("ssh", *options, destination, "mkdir", "-p", "--", remote_dir),
        (
            "scp",
            *options,
            str(script),
            f"{destination}:{remote_dir}/deployment_control.py",
        ),
        (
            "scp",
            *options,
            str(smoke_script),
            f"{destination}:{remote_dir}/deployment_smoke.py",
        ),
        ("scp", *options, str(bundle), f"{destination}:{remote_dir}/{BUNDLE_NAME}"),
        ("ssh", *options, destination, remote),
    )


def assert_control_checkout(control_root: Path, control_sha: str) -> None:
    control_sha = require_sha(control_sha, "control SHA")
    completed = subprocess.run(
        ["git", "-C", str(control_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stdout.strip() != control_sha:
        raise DeployError(
            "control checkout does not match the bound default-branch SHA"
        )


def assert_source_checkout(source_repo: Path, release_sha: str) -> None:
    release_sha = require_sha(release_sha, "release SHA")
    completed = subprocess.run(
        ["git", "-C", str(source_repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stdout.strip() != release_sha:
        raise DeployError("release checkout does not match the bound SHA")


def default_runner(command: tuple[str, ...], *, timeout: int) -> CommandResult:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _function_body(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.find(marker)
    if start < 0:
        return ""
    rest = source[start:]
    next_def = rest.find("\ndef ", 1)
    return rest if next_def < 0 else rest[:next_def]


def lint_ssh_source(source: str) -> list[str]:
    body = _function_body(source, "ssh_options")
    if '"StrictHostKeyChecking=yes"' not in body:
        return ["SSH host key checking must be yes"]
    if any(
        token in body
        for token in ("StrictHostKeyChecking=no", "sshpass", "ssh-keyscan")
    ):
        return ["SSH host key checking must be yes"]
    return []


def lint_deploy_workflow_text(text: str) -> list[str]:
    import yaml

    errors: list[str] = []
    for banned in FORBIDDEN_WORKFLOW:
        if banned in text:
            errors.append(f"deploy workflow contains {banned}")
    if "StrictHostKeyChecking=yes" not in text:
        errors.append("SSH host key checking must be yes")
    if "SSH_STRICT_HOST_KEY_CHECKING" not in text or ': "yes"' not in text:
        errors.append("SSH host key checking must be yes")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [*errors, f"deploy workflow YAML: {exc}"]
    if not isinstance(document, dict) or "on" not in document:
        return [*errors, "deploy workflow trigger key must be the string on"]
    triggers = document["on"]
    if set(triggers) != {"workflow_dispatch"}:
        errors.append("deploy workflow must be manual workflow_dispatch only")
    inputs = (
        (triggers.get("workflow_dispatch") or {}).get("inputs")
        if isinstance(triggers, dict)
        else None
    )
    errors.extend(_lint_inputs(inputs))
    if document.get("permissions") != {"contents": "read"}:
        errors.append("top-level permissions must be contents read")
    concurrency = document.get("concurrency")
    if (
        not isinstance(concurrency, dict)
        or concurrency.get("cancel-in-progress") is not False
    ):
        errors.append("deploy concurrency must not cancel an active deployment")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or set(jobs) != {
        "resolve",
        "deploy-staging",
        "deploy-production",
    }:
        return [
            *errors,
            "deploy jobs must be resolve, deploy-staging, and deploy-production",
        ]
    errors.extend(_lint_job(jobs["resolve"], "resolve"))
    errors.extend(_lint_job(jobs["deploy-staging"], "deploy-staging"))
    errors.extend(_lint_job(jobs["deploy-production"], "deploy-production"))
    errors.extend(_lint_checkout(jobs))
    errors.extend(_lint_run_scripts(jobs))
    staging_runs = _run_text(jobs["deploy-staging"])
    production_runs = _run_text(jobs["deploy-production"])
    if staging_runs != production_runs:
        errors.append("staging and production deploy commands must match")
    errors.extend(_lint_target_env(jobs["deploy-staging"], "staging"))
    errors.extend(_lint_target_env(jobs["deploy-production"], "production"))
    outputs = jobs["resolve"].get("outputs")
    if not isinstance(outputs, dict) or "control_sha" not in outputs:
        errors.append("resolve must bind the default-branch control SHA")
    if "DEPLOY_CALLER_REF" not in text:
        errors.append("deployment workflow must record the caller ref")
    return errors


def lint_repository_deployment(repo: Path) -> list[str]:
    errors: list[str] = []
    workflow = repo / ".github" / "workflows" / WORKFLOW_NAME
    script = repo / "scripts" / "deployment_control.py"
    runbook = repo / "docs" / "runbooks" / "phase8-increment-5.md"
    compose = repo / "infra" / "compose" / "docker-compose.prod.yml"
    blackbox = repo / "infra" / "observability" / "blackbox" / "blackbox.yml"
    if not workflow.is_file():
        errors.append("deploy workflow is missing")
    else:
        errors.extend(lint_deploy_workflow_text(workflow.read_text(encoding="utf-8")))
    if script.is_file():
        source = script.read_text(encoding="utf-8")
        errors.extend(lint_ssh_source(source))
        if re.search(r"(?m)^\s+\w*proven\w*\s*=\s*True\s*$", source):
            errors.append("deployment control assigns a live proof flag")
        if "git/ref/tags/" not in source or "git/ref/heads/" not in source:
            errors.append(
                "deployment control must resolve refs through the Git refs API"
            )
        ref_body = (
            _function_body(source, "resolve_tag_commit")
            + _function_body(source, "resolve_release")
            + _function_body(source, "resolve_branch_head")
        )
        if "commits" in ref_body:
            errors.append(
                "deployment control must not resolve a tag through the commits API"
            )
        if 'f"{INCOMING_PARENT}/{target}"' not in source or "host.lock" not in source:
            errors.append("incoming paths or the host lock drifted")
    else:
        errors.append("deployment control script is missing")
    if not runbook.is_file():
        errors.append("increment 5 runbook is missing")
    else:
        text = runbook.read_text(encoding="utf-8")
        errors.extend(_lint_runbook(text))
    if compose.is_file():
        compose_text = compose.read_text(encoding="utf-8")
        if "./blackbox.rendered.yml:/etc/blackbox/blackbox.yml:ro" not in compose_text:
            errors.append("compose must mount the rendered blackbox config")
        if "../observability/blackbox/blackbox.yml:" in compose_text:
            errors.append("compose must not mount the tracked blackbox source")
    else:
        errors.append("production compose is missing")
    if blackbox.is_file():
        config = blackbox.read_text(encoding="utf-8")
        if (
            "insecure_skip_verify: false" not in config
            or "server_name: app.example.com" not in config
        ):
            errors.append("tracked blackbox source must keep the example server name")
    else:
        errors.append("tracked blackbox source is missing")
    for relative in (*INFRA_PATHS, *CONTROL_PATHS):
        if not (repo / relative).is_file():
            errors.append(f"deployment bundle source is missing: {relative}")
    return errors


def _lint_runbook(text: str) -> list[str]:
    errors = []
    for phrase in REQUIRED_RUNBOOK:
        if phrase not in text:
            errors.append(f"increment 5 runbook is missing {phrase}")
    for phrase in BANNED_RUNBOOK:
        if phrase in text:
            errors.append("increment 5 runbook claims a live proof")
    return errors


def _lint_inputs(inputs: object) -> list[str]:
    expected = {
        "target": ["staging", "production"],
        "operation": ["deploy", "rollback-app"],
        "schema_compatibility": ["not-applicable", "reviewed-authorized"],
    }
    strings = {"release_run_id", "expected_sha", "expected_tag"}
    if not isinstance(inputs, dict) or set(inputs) != set(expected) | strings:
        return [
            "deploy inputs must be the bounded target, release, and rollback choices"
        ]
    errors = []
    for name, options in expected.items():
        item = inputs.get(name)
        if not isinstance(item, dict):
            errors.append(f"{name} must be a bounded choice")
            continue
        if item.get("type") != "choice" or item.get("options") != options:
            errors.append(f"{name} must be a bounded choice")
        if item.get("required") is not True:
            errors.append(f"{name} must be required")
    for name in strings:
        item = inputs.get(name)
        if (
            not isinstance(item, dict)
            or item.get("type") != "string"
            or item.get("required") is not True
        ):
            errors.append(f"{name} must be a required string")
    return errors


def _lint_job(job: object, name: str) -> list[str]:
    if not isinstance(job, dict):
        return [f"{name} job is invalid"]
    errors = []
    if job.get("permissions") != JOB_PERMISSIONS:
        errors.append(f"{name} permissions exceed the deployment read set")
    concurrency = job.get("concurrency")
    if (
        not isinstance(concurrency, dict)
        or concurrency.get("cancel-in-progress") is not False
    ):
        errors.append(f"{name} concurrency must not cancel an active deployment")
    if name == "resolve":
        if "environment" in job:
            errors.append("resolve must not select a deployment environment")
    elif job.get("environment") != name.removeprefix("deploy-") and job.get(
        "environment"
    ) != ("gym-staging" if name == "deploy-staging" else "gym-production"):
        errors.append(f"{name} environment is not the protected name")
    if name == "deploy-staging" and job.get("environment") != "gym-staging":
        errors.append("deploy-staging environment must be gym-staging")
    if name == "deploy-production" and job.get("environment") != "gym-production":
        errors.append("deploy-production environment must be gym-production")
    if (
        name == "deploy-staging"
        and job.get("if") != "needs.resolve.outputs.target == 'staging'"
    ):
        errors.append("deploy-staging must run only for the staging choice")
    if (
        name == "deploy-production"
        and job.get("if") != "needs.resolve.outputs.target == 'production'"
    ):
        errors.append("deploy-production must run only for the production choice")
    return errors


def _lint_checkout(jobs: Mapping[str, Any]) -> list[str]:
    errors = []
    controls: set[str] = set()
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if not isinstance(step, dict) or not str(step.get("uses", "")).startswith(
                "actions/checkout@"
            ):
                continue
            raw_options = step.get("with")
            options = raw_options if isinstance(raw_options, dict) else {}
            if options.get("persist-credentials") is not False:
                errors.append(f"{name} checkout persists credentials")
            ref = options.get("ref")
            path = options.get("path")
            if (
                path == "release-source"
                and ref != "${{ needs.resolve.outputs.release_sha }}"
            ):
                errors.append("release checkout must use the bound release SHA")
            if path == "control-source":
                if ref != "${{ needs.resolve.outputs.control_sha }}":
                    errors.append(
                        "control checkout must use the bound default-branch SHA"
                    )
                else:
                    controls.add(name)
            if name == "resolve" and ref not in (None, ""):
                errors.append("resolve checkout must stay on the workflow caller SHA")
    for name in ("deploy-staging", "deploy-production"):
        if name in jobs and name not in controls:
            errors.append("control checkout must use the bound default-branch SHA")
    return errors


def _lint_run_scripts(jobs: Mapping[str, Any]) -> list[str]:
    errors = []
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if isinstance(script, str) and "${{" in script:
                errors.append(
                    f"{name} interpolates a GitHub expression inside the shell"
                )
    rendered = json.dumps(jobs)
    allowed = (
        rendered.count("secrets.GITHUB_TOKEN")
        + rendered.count("secrets.DEPLOY_SSH_PRIVATE_KEY")
        + rendered.count("secrets.DEPLOY_SSH_HOST_KEY")
    )
    if rendered.count("secrets.") != allowed:
        errors.append(
            "deploy workflow secrets must be the token and the SSH environment values"
        )
    return errors


def _lint_target_env(job: object, expected: str) -> list[str]:
    if not isinstance(job, dict):
        return ["deploy target env is missing"]
    found: list[str] = []
    for step in job.get("steps") or []:
        env = step.get("env") if isinstance(step, dict) else None
        if isinstance(env, dict) and "DEPLOY_TARGET" in env:
            found.append(str(env["DEPLOY_TARGET"]))
    if found != [expected]:
        return [f"DEPLOY_TARGET must be {expected}"]
    return []


def _run_text(job: object) -> str:
    if not isinstance(job, dict):
        return ""
    runs = []
    for step in job.get("steps") or []:
        if isinstance(step, dict) and isinstance(step.get("run"), str):
            runs.append(step["run"])
    return "\n".join(runs)


def _ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def _require_branch(value: str) -> str:
    if (
        not value
        or ".." in value
        or value.startswith("/")
        or value.endswith("/")
        or re.fullmatch(r"[A-Za-z0-9._/-]+", value) is None
    ):
        raise DeployError("repository default branch is invalid")
    return value


def resolve_repository(repository: str, fetch: Callable[[str], Any]) -> dict[str, str]:
    repository = require_repository(repository)
    payload = fetch(f"repos/{repository}")
    if not isinstance(payload, dict):
        raise DeployError("repository metadata is invalid")
    if str(payload.get("full_name", "")).casefold() != repository.casefold():
        raise DeployError("repository metadata does not match")
    branch = payload.get("default_branch")
    if not isinstance(branch, str):
        raise DeployError("repository default branch is invalid")
    return {"default_branch": _require_branch(branch), "full_name": repository}


def resolve_branch_head(
    repository: str, branch: str, fetch: Callable[[str], Any]
) -> str:
    repository = require_repository(repository)
    branch = _require_branch(branch)
    payload = fetch(f"repos/{repository}/git/ref/heads/{branch}")
    if not isinstance(payload, dict):
        raise DeployError("default branch head is invalid")
    obj = payload.get("object")
    if not isinstance(obj, dict) or obj.get("type") != "commit":
        raise DeployError("default branch head is not a commit")
    if not isinstance(obj.get("sha"), str):
        raise DeployError("default branch head is not a commit")
    return require_sha(obj["sha"], "control SHA")


def resolve_tag_commit(repository: str, tag: str, fetch: Callable[[str], Any]) -> str:
    repository = require_repository(repository)
    tag = require_tag(tag)
    payload = fetch(f"repos/{repository}/git/ref/tags/{tag}")
    if not isinstance(payload, dict) or payload.get("ref") != f"refs/tags/{tag}":
        raise DeployError("tag ref is not the expected tag")
    obj = payload.get("object")
    if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
        raise DeployError("tag object is invalid")
    sha = require_sha(obj["sha"], "tag object")
    kind = obj.get("type")
    if kind == "commit":
        return sha
    if kind != "tag":
        raise DeployError("tag object is not a commit or annotated tag")
    for _ in range(8):
        peeled = fetch(f"repos/{repository}/git/tags/{sha}")
        if not isinstance(peeled, dict):
            raise DeployError("annotated tag does not peel to a commit")
        inner = peeled.get("object")
        if not isinstance(inner, dict) or not isinstance(inner.get("sha"), str):
            raise DeployError("annotated tag does not peel to a commit")
        sha = require_sha(inner["sha"], "peeled tag")
        if inner.get("type") == "commit":
            return sha
        if inner.get("type") != "tag":
            raise DeployError("annotated tag does not peel to a commit")
    raise DeployError("annotated tag peel exceeded the bound")


def confirm_control_plane(bound: Mapping[str, Any], runner: Runner) -> None:
    repository = require_repository(str(bound.get("repository", "")))
    if os.environ.get("DEPLOY_CALLER_REF") != bound.get("control_ref"):
        raise DeployError("deployment workflow ref is not the default branch")
    if os.environ.get("DEPLOY_CONTROL_SHA") != bound.get("control_sha"):
        raise DeployError("control checkout is not the bound default-branch SHA")

    def fetch(path: str) -> Any:
        result = runner(("gh", "api", path), timeout=60)
        if result.returncode != 0:
            raise DeployError("GitHub API request failed")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DeployError("GitHub API response is invalid") from exc

    metadata = resolve_repository(repository, fetch)
    if bound.get("control_ref") != f"refs/heads/{metadata['default_branch']}":
        raise DeployError("deployment workflow ref is not the default branch")
    head = resolve_branch_head(repository, metadata["default_branch"], fetch)
    if head != bound.get("control_sha"):
        raise DeployError(
            "default branch moved after resolve; refusing the deployment race"
        )


def _gh_json(path: str) -> Any:
    result = default_runner(("gh", "api", path), timeout=60)
    if result.returncode != 0:
        raise DeployError("GitHub API request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeployError("GitHub API response is invalid") from exc


def _download_manifest(repository: str, run_id: str, directory: Path) -> dict[str, Any]:
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, mode=0o700)
    result = default_runner(
        (
            "gh",
            "run",
            "download",
            run_id,
            "--repo",
            repository,
            "--name",
            "release-manifest",
            "--dir",
            str(directory),
        ),
        timeout=120,
    )
    if result.returncode != 0:
        raise DeployError("release manifest download failed")
    files = [
        item
        for item in directory.rglob("*")
        if item.is_file() and not item.is_symlink()
    ]
    if len(files) != 1 or files[0].name != "release-manifest.json":
        raise DeployError("release manifest artifact is not a single JSON file")
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DeployError("release manifest artifact is not an object")
    return payload


def resolve_release(output: Path) -> dict[str, Any]:
    if os.environ.get("DEPLOY_EVENT") != "workflow_dispatch":
        raise DeployError("deployment runs only from workflow_dispatch")
    if os.environ.get("SSH_STRICT_HOST_KEY_CHECKING", "yes") != "yes":
        raise DeployError("SSH host key checking must be yes")
    target = os.environ["DEPLOY_TARGET"]
    operation = os.environ["DEPLOY_OPERATION"]
    schema_compatibility = os.environ["DEPLOY_SCHEMA_COMPATIBILITY"]
    project_name(target)
    assert_operation_schema(operation, schema_compatibility)
    repository = require_repository(os.environ["DEPLOY_REPOSITORY"])
    run_id = require_run_id(os.environ["DEPLOY_RELEASE_RUN_ID"])
    expected_sha = require_sha(os.environ["DEPLOY_EXPECTED_SHA"], "release SHA")
    caller_sha = require_sha(os.environ["DEPLOY_CALLER_SHA"], "caller SHA")
    caller_ref = os.environ["DEPLOY_CALLER_REF"]
    expected_tag = require_tag(os.environ["DEPLOY_EXPECTED_TAG"])
    metadata = resolve_repository(repository, _gh_json)
    control_head = resolve_branch_head(repository, metadata["default_branch"], _gh_json)
    if (
        caller_ref != f"refs/heads/{metadata['default_branch']}"
        or caller_sha != control_head
    ):
        raise DeployError(
            "caller SHA is not the default branch head; refusing an untrusted ref "
            "or a moved default branch"
        )
    tag_commit = resolve_tag_commit(repository, expected_tag, _gh_json)
    run = _gh_json(f"repos/{repository}/actions/runs/{run_id}")
    artifacts = _gh_json(f"repos/{repository}/actions/runs/{run_id}/artifacts")
    select_release_artifact(artifacts)
    manifest = _download_manifest(
        repository, run_id, output.parent / "release-manifest-artifact"
    )
    images = manifest.get("images") if isinstance(manifest, dict) else None
    if not isinstance(images, dict):
        raise DeployError("release manifest images are invalid")
    attestations = {
        name: verify_image_attestation(str(images[name]), repository, default_runner)
        for name in ("api", "web", "postgres")
    }
    bound = bind_release(
        manifest=manifest,
        run=run,
        tag_commit_sha=tag_commit,
        repository=repository,
        expected_sha=expected_sha,
        expected_tag=expected_tag,
        expected_run_id=run_id,
        caller_sha=caller_sha,
        caller_ref=caller_ref,
        default_branch=metadata["default_branch"],
        control_head_sha=control_head,
        attestations=attestations,
    )
    output.write_bytes(canonical_json(bound))
    _write_github_output("release_sha", bound["release_sha"])
    _write_github_output("control_sha", bound["control_sha"])
    _write_github_output("target", target)
    _write_github_output("operation", operation)
    _write_github_output("schema_compatibility", schema_compatibility)
    return bound


def _write_github_output(name: str, value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name) or re.search(r"[\r\n]", value):
        raise DeployError("GitHub output is invalid")
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def read_running_projects(runner: Runner) -> tuple[str, ...]:
    result = runner(
        ("docker", "compose", "ls", "--all", "--format", "json"), timeout=60
    )
    if result.returncode != 0:
        raise DeployError("cannot read running compose projects")
    text = result.stdout.strip() or "[]"
    if text.startswith("["):
        payload = json.loads(text)
        rows = payload if isinstance(payload, list) else [payload]
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("Name"), str):
            names.append(str(row["Name"]))
    return tuple(names)


def host_apply(
    *,
    bundle: Path,
    target: str,
    operation: str,
    schema_compatibility: str,
    host_root: Path,
    runner: Runner | None = None,
) -> dict[str, Any]:
    project_name(target)
    state_dir = host_root / "var" / "lib" / "gym-deploy" / target
    env_file = host_root / "etc" / "gym" / target / "compose.env"
    state_dir.mkdir(parents=True, exist_ok=True)
    meminfo = host_root / "proc" / "meminfo"
    if not meminfo.is_file():
        raise DeployError("MemAvailable is missing")
    host_env = parse_env_file(env_file.read_text(encoding="utf-8"))
    process_env = compose_process_env(host_env)
    active = runner or _env_runner(process_env)
    request = HostRequest(
        target=target,
        operation=operation,
        schema_compatibility=schema_compatibility,
        bundle=bundle,
        state_dir=state_dir,
        env_file=env_file,
        host_root=host_root,
        running_projects=read_running_projects(active),
        free_disk_bytes=shutil.disk_usage(state_dir).free,
        mem_available_bytes=parse_mem_available(meminfo.read_text(encoding="utf-8")),
        process_env=process_env,
    )
    return execute_deployment(request, active)


def _env_runner(process_env: dict[str, str]) -> Runner:
    def run(command: tuple[str, ...], *, timeout: int) -> CommandResult:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=process_env,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    return run


def transfer_and_apply(source_repo: Path, bound_path: Path, bundle_path: Path) -> None:
    if os.environ.get("SSH_STRICT_HOST_KEY_CHECKING") != "yes":
        raise DeployError("SSH host key checking must be yes")
    bound = json.loads(bound_path.read_text(encoding="utf-8"))
    seal_evidence(bound)
    target = os.environ["DEPLOY_TARGET"]
    operation = os.environ["DEPLOY_OPERATION"]
    schema_compatibility = os.environ["DEPLOY_SCHEMA_COMPATIBILITY"]
    if target not in TARGETS or bound.get("release_sha") != os.environ["RELEASE_SHA"]:
        raise DeployError("transfer target does not match the bound release")
    if os.environ.get("DEPLOY_CALLER_SHA") != bound.get("caller_sha"):
        raise DeployError("caller SHA does not match the bound workflow SHA")
    confirm_control_plane(bound, default_runner)
    assert_control_checkout(
        Path(__file__).resolve().parents[1], str(bound["control_sha"])
    )
    assert_source_checkout(source_repo, str(bound["release_sha"]))
    confirm_attestations(bound, default_runner)
    control_root = Path(__file__).resolve().parents[1]
    build_bundle(source_repo, control_root, bound, bundle_path)
    verify_bundle(bundle_path)
    material = write_ssh_files(
        Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "gym-deploy-ssh",
        os.environ["DEPLOY_SSH_HOST"],
        os.environ["DEPLOY_SSH_USER"],
        os.environ["DEPLOY_SSH_PRIVATE_KEY"],
        os.environ["DEPLOY_SSH_HOST_KEY"],
    )
    commands = transfer_commands(
        bundle=bundle_path,
        script=Path(__file__).resolve(),
        smoke_script=Path(smoke.__file__).resolve(),
        host=os.environ["DEPLOY_SSH_HOST"],
        user=os.environ["DEPLOY_SSH_USER"],
        known_hosts=material["known_hosts"],
        identity=material["identity"],
        target=target,
        operation=operation,
        schema_compatibility=schema_compatibility,
    )
    env = ssh_process_env()
    for command in commands:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
        )
        if completed.returncode != 0:
            raise DeployError("deployment transfer failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve-release")
    resolve.add_argument("--output", type=Path, required=True)
    transfer = commands.add_parser("transfer-and-apply")
    transfer.add_argument("--source-repo", type=Path, required=True)
    transfer.add_argument("--bound", type=Path, required=True)
    transfer.add_argument("--bundle", type=Path, required=True)
    build = commands.add_parser("build-bundle")
    build.add_argument("--source-repo", type=Path, required=True)
    build.add_argument("--control-root", type=Path, required=True)
    build.add_argument("--bound", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-bundle")
    verify.add_argument("--bundle", type=Path, required=True)
    lint = commands.add_parser("lint")
    lint.add_argument("--repo", type=Path, default=Path("."))
    plan = commands.add_parser("plan")
    plan.add_argument("--target", required=True)
    plan.add_argument("--operation", required=True)
    plan.add_argument("--schema-compatibility", required=True)
    teardown = commands.add_parser("teardown-staging")
    teardown.add_argument("--confirmation-file", type=Path, required=True)
    teardown.add_argument("--compose-file", type=Path, required=True)
    render = commands.add_parser("render-blackbox")
    render.add_argument("--source", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--public-host", required=True)
    apply = commands.add_parser("host-apply")
    apply.add_argument("--bundle", type=Path, required=True)
    apply.add_argument("--target", required=True)
    apply.add_argument("--operation", required=True)
    apply.add_argument("--schema-compatibility", required=True)
    apply.add_argument("--host-root", type=Path, default=Path("/"))
    args = parser.parse_args(argv)
    try:
        if args.command == "resolve-release":
            resolve_release(args.output)
        elif args.command == "transfer-and-apply":
            transfer_and_apply(args.source_repo, args.bound, args.bundle)
        elif args.command == "build-bundle":
            bound = json.loads(args.bound.read_text(encoding="utf-8"))
            build_bundle(args.source_repo, args.control_root, bound, args.output)
        elif args.command == "verify-bundle":
            verify_bundle(args.bundle)
        elif args.command == "lint":
            errors = lint_repository_deployment(args.repo)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
        elif args.command == "plan":
            print(
                json.dumps(
                    list(
                        deployment_steps(
                            args.target, args.operation, args.schema_compatibility
                        )
                    )
                )
            )
        elif args.command == "teardown-staging":
            confirmation = args.confirmation_file.read_text(encoding="utf-8").strip()
            command = plan_teardown(
                target="staging",
                confirmation=confirmation,
                compose_file=args.compose_file,
            )
            result = default_runner(command, timeout=900)
            if result.returncode != 0:
                raise DeployError("teardown failed")
        elif args.command == "render-blackbox":
            write_rendered_blackbox(args.source, args.output, args.public_host)
        elif args.command == "host-apply":
            host_apply(
                bundle=args.bundle,
                target=args.target,
                operation=args.operation,
                schema_compatibility=args.schema_compatibility,
                host_root=args.host_root,
            )
    except (DeployError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 75 if str(exc) == "deployment lock is held" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
