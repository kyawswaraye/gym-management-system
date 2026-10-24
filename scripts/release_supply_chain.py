"""Offline checks and release helpers for the Phase 8 image workflow.

The workflow file is validated here. This module does not push images, contact
GHCR, or prove that repository protections exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

WORKFLOW_NAME = "image-release.yml"
FIXTURE_ENV = Path("infra/environments/production/compose.env.example")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SEMVER_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
OWNER_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\Z")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
IMAGE_NAMES = ("gym-api", "gym-web", "gym-postgres")
OCI_LAYOUTS = ("release/api", "release/web", "release/postgres")
OCI_OUTPUTS = (
    "type=oci,dest=release/api,tar=false",
    "type=oci,dest=release/web,tar=false",
    "type=oci,dest=release/postgres,tar=false",
)
MAX_INDEX_BYTES = 1_048_576
MAX_MANIFEST_BYTES = 20 * 1_048_576
PUBLISH_PERMISSIONS = {
    "contents": "read",
    "packages": "write",
    "id-token": "write",
    "attestations": "write",
}
READ_PERMISSIONS = {"contents": "read"}
RUNTIME_BASELINE = {
    "python": "3.14.7",
    "django": "5.2.17",
    "drf": "3.18.2",
    "node": "24.20.0",
    "next": "16.4.1",
    "typescript": "7.0.2",
    "postgresql": "18.6",
    "pgbackrest": "2.60.0",
}
DEPLOY_MARKERS = (
    "ssh ",
    "ssh://",
    "scp ",
    "rsync ",
    "kubectl",
    "terraform apply",
    "docker compose up",
    "appleboy/ssh-action",
    "aws-actions/",
    "azure/login",
    "google-github-actions/auth",
)
FORBIDDEN_TRIGGERS = (
    "pull_request",
    "pull_request_target",
    "schedule",
    "workflow_run",
    "workflow_call",
    "repository_dispatch",
)


class ReleaseError(RuntimeError):
    """A release input or published digest failed a closed check."""


def normalize_owner(value: str) -> str:
    owner = value.strip().lower()
    if not OWNER_RE.fullmatch(owner):
        raise ReleaseError("repository owner is not a safe GHCR name")
    return owner


def immutable_tag(sha: str) -> str:
    if not SHA_RE.fullmatch(sha):
        raise ReleaseError("commit SHA must be 40 lowercase hex characters")
    return f"sha-{sha}"


def image_repository(owner: str, name: str) -> str:
    if name not in IMAGE_NAMES:
        raise ReleaseError("image name must be gym-api, gym-web, or gym-postgres")
    return f"ghcr.io/{normalize_owner(owner)}/{name}"


def digest_reference(repository: str, digest: str) -> str:
    if not DIGEST_RE.fullmatch(digest):
        raise ReleaseError("image digest must be sha256 and 64 lowercase hex characters")
    if "@" in repository or ":" in repository.rsplit("/", 1)[-1]:
        raise ReleaseError("image repository must not include a tag or digest")
    return f"{repository}@{digest}"


def check_ref(
    *,
    event: str,
    ref_type: str,
    ref_name: str,
    sha: str,
    input_sha: str,
) -> None:
    if not SHA_RE.fullmatch(sha):
        raise ReleaseError("release commit must be a 40-character lowercase SHA")
    if ref_type != "tag" or SEMVER_RE.fullmatch(ref_name) is None:
        raise ReleaseError("releases run only from a vMAJOR.MINOR.PATCH tag")
    if event == "push":
        return
    if event == "workflow_dispatch":
        if input_sha != sha:
            raise ReleaseError("workflow_dispatch git_sha must equal the tag commit")
        return
    raise ReleaseError("unsupported release event")


def _layout_file(layout: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ReleaseError("OCI layout member path is unsafe")
    current = layout
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseError("OCI layout member path is unsafe")
    return current


def _read_layout_file(path: Path, limit: int) -> bytes:
    size = path.stat().st_size
    if size < 0 or size > limit:
        raise ReleaseError("OCI layout member is too large")
    data = path.read_bytes()
    if len(data) > limit:
        raise ReleaseError("OCI layout member is too large")
    return data


def oci_layout_manifest_digest(layout: Path) -> str:
    if layout.is_symlink() or not layout.is_dir():
        raise ReleaseError("OCI layout must be a directory")
    index_path = _layout_file(layout, "index.json")
    if not index_path.is_file():
        raise ReleaseError("OCI layout is missing index.json")
    try:
        index = json.loads(_read_layout_file(index_path, MAX_INDEX_BYTES).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("OCI index is invalid") from exc
    manifests = index.get("manifests") if isinstance(index, dict) else None
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise ReleaseError("OCI layout must contain exactly one image manifest")
    entry = manifests[0]
    digest = entry.get("digest") if isinstance(entry, dict) else None
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise ReleaseError("OCI manifest digest is invalid")
    blob_path = _layout_file(layout, f"blobs/sha256/{digest.split(':', 1)[1]}")
    if not blob_path.is_file():
        raise ReleaseError("OCI manifest blob is missing")
    blob = _read_layout_file(blob_path, MAX_MANIFEST_BYTES)
    if hashlib.sha256(blob).hexdigest() != digest.split(":", 1)[1]:
        raise ReleaseError("OCI manifest blob does not match its digest")
    return digest


def bind_digest(*, digest: str, layout: Path, output: Path) -> str:
    if DIGEST_RE.fullmatch(digest) is None:
        raise ReleaseError("build digest must be sha256 and 64 lowercase hex characters")
    actual = oci_layout_manifest_digest(layout)
    if actual != digest:
        raise ReleaseError("OCI layout digest does not match the build digest")
    output.write_text(f"{actual}\n", encoding="utf-8")
    return actual


def assert_same_digest(expected: str, actual: str) -> None:
    if DIGEST_RE.fullmatch(expected) is None or actual != expected:
        raise ReleaseError("registry digest does not match the scanned digest")


def runtime_baseline() -> dict[str, str]:
    return dict(RUNTIME_BASELINE)


def build_manifest(
    *,
    git_commit: str,
    git_ref: str,
    git_ref_name: str,
    owner: str,
    api_digest: str,
    web_digest: str,
    postgres_digest: str,
    workflow: str,
    run_id: str,
    run_attempt: str,
    timestamp: str,
) -> dict[str, Any]:
    check_ref(
        event="push",
        ref_type="tag",
        ref_name=git_ref_name,
        sha=git_commit,
        input_sha="",
    )
    if git_ref != f"refs/tags/{git_ref_name}":
        raise ReleaseError("git ref must be the tag ref of the release")
    if workflow != WORKFLOW_NAME or not run_id or not run_attempt:
        raise ReleaseError("release manifest needs the workflow identity")
    if TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ReleaseError("release timestamp must be UTC RFC3339")
    normalized = normalize_owner(owner)
    return {
        "schema_version": 2,
        "git_commit": git_commit,
        "git_ref": git_ref,
        "git_ref_name": git_ref_name,
        "images": {
            "api": digest_reference(image_repository(normalized, "gym-api"), api_digest),
            "web": digest_reference(image_repository(normalized, "gym-web"), web_digest),
            "postgres": digest_reference(
                image_repository(normalized, "gym-postgres"), postgres_digest
            ),
        },
        "build": {
            "workflow": workflow,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "timestamp": timestamp,
        },
        "runtime_baseline": runtime_baseline(),
    }


def compose_env_lines(manifest: dict[str, Any]) -> str:
    images = manifest.get("images")
    if not isinstance(images, dict):
        raise ReleaseError("release manifest is missing image digests")
    lines = []
    for key, env_name in (
        ("api", "API_IMAGE"),
        ("web", "WEB_IMAGE"),
        ("postgres", "POSTGRES_IMAGE"),
    ):
        value = images.get(key)
        if not isinstance(value, str) or "@sha256:" not in value:
            raise ReleaseError(f"{key} image must be a repository digest")
        repository, digest = value.rsplit("@", 1)
        lines.append(f"{env_name}={digest_reference(repository, digest)}")
    return "\n".join(lines) + "\n"


def refuse_fixture_output(path: Path, repo: Path) -> None:
    fixture = (repo / FIXTURE_ENV).resolve()
    if path.resolve() == fixture:
        raise ReleaseError("refusing to rewrite the committed Compose fixture")


def lint_release_workflow_text(text: str) -> list[str]:
    import yaml

    errors: list[str] = []
    if re.search(r"(?<![-A-Za-z0-9])latest(?![-A-Za-z0-9])", text):
        errors.append("release workflow must not publish or scan a latest tag")
    if "github.sha" not in text:
        errors.append("release is not bound to the git commit")
    for match in re.finditer(r"ghcr\.io/[A-Za-z0-9_./:@-]+", text):
        reference = match.group(0)
        name = reference.rsplit("/", 1)[-1]
        if ":" in name and "@sha256:" not in reference:
            errors.append("tag-only image reference is not a release identity")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [*errors, f"release workflow YAML: {exc}"]
    if not isinstance(document, dict):
        return [*errors, "release workflow must be a mapping"]
    if "on" not in document:
        errors.append("release workflow trigger key must be the string on")
        return errors
    errors.extend(_lint_triggers(document["on"]))
    errors.extend(_lint_permissions(document))
    errors.extend(_lint_jobs(document.get("jobs")))
    errors.extend(_lint_shell(document))
    return errors


def lint_release_workflow(path: Path) -> list[str]:
    if not path.is_file():
        return ["image release workflow is missing"]
    errors = lint_release_workflow_text(path.read_text(encoding="utf-8"))
    errors.extend(_lint_baseline_sources(path.parents[2]))
    return errors


def _function_body(source: str, name: str) -> str:
    marker = f"def {name}("
    start = source.find(marker)
    if start < 0:
        return ""
    rest = source[start:]
    next_def = rest.find("\ndef ", 1)
    return rest if next_def < 0 else rest[:next_def]


def lint_publish_transport(source: str) -> list[str]:
    errors: list[str] = []
    publish = _function_body(source, "_publish_image")
    if "--preserve-digests" not in publish or 'f"oci:{layout}"' not in publish:
        errors.append("publish must copy the OCI layout with --preserve-digests")
    if "oci-archive:" in publish:
        errors.append("publish must not use the OCI archive transport")
    return errors


def lint_repository_release(repo: Path) -> list[str]:
    errors = lint_release_workflow(repo / ".github" / "workflows" / WORKFLOW_NAME)
    helper = repo / "scripts" / "release_supply_chain.py"
    if helper.is_file():
        errors.extend(lint_publish_transport(helper.read_text(encoding="utf-8")))
    else:
        errors.append("release helper is missing")
    return errors


def _lint_baseline_sources(repo: Path) -> list[str]:
    errors: list[str] = []
    backend = (repo / "backend/Dockerfile").read_text(encoding="utf-8")
    web = (repo / "web/Dockerfile").read_text(encoding="utf-8")
    requirements = (repo / "backend/requirements/base.txt").read_text(encoding="utf-8")
    package = (repo / "web/package.json").read_text(encoding="utf-8")
    ci = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    checks = (
        (f"FROM python:{RUNTIME_BASELINE['python']}-slim\n" in backend, "python"),
        (f"node:{RUNTIME_BASELINE['node']}-slim" in web, "node"),
        (f"Django=={RUNTIME_BASELINE['django']}" in requirements, "django"),
        (f"djangorestframework=={RUNTIME_BASELINE['drf']}" in requirements, "drf"),
        (f'"next": "{RUNTIME_BASELINE["next"]}"' in package, "next"),
        (f'"typescript": "{RUNTIME_BASELINE["typescript"]}"' in package, "typescript"),
        (f"postgres:{RUNTIME_BASELINE['postgresql']}" in ci, "postgresql"),
        (
            f"pgBackRest {RUNTIME_BASELINE['pgbackrest']}"
            in (repo / "infra/postgres/Dockerfile").read_text(encoding="utf-8"),
            "pgbackrest",
        ),
    )
    for ok, name in checks:
        if not ok:
            errors.append(f"runtime baseline drifted from {name}")
    return errors


def _lint_triggers(triggers: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(triggers, dict):
        return ["release triggers must be a mapping"]
    for name in FORBIDDEN_TRIGGERS:
        if name in triggers:
            errors.append(f"release trigger bypasses the tag control: {name}")
    push = triggers.get("push")
    if not isinstance(push, dict) or "branches" in push or "paths" in push:
        errors.append("release push trigger must be tags only")
    else:
        tags = push.get("tags")
        if tags != ["v*.*.*"]:
            errors.append("release tags must be the v*.*.* filter plus the semver gate")
    dispatch = triggers.get("workflow_dispatch")
    if not isinstance(dispatch, dict):
        errors.append("workflow_dispatch must require the tag commit")
    else:
        git_sha = (dispatch.get("inputs") or {}).get("git_sha")
        if not isinstance(git_sha, dict) or git_sha.get("required") is not True:
            errors.append("workflow_dispatch must require git_sha")
    return errors


def _lint_permissions(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("permissions") != READ_PERMISSIONS:
        errors.append("top-level permissions must be contents read")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return errors
    for name, job in jobs.items():
        if not isinstance(job, dict):
            errors.append(f"{name} job is not a mapping")
            continue
        permissions = job.get("permissions")
        if name == "publish":
            if permissions != PUBLISH_PERMISSIONS:
                errors.append("publish permissions must be the attestation and package set")
        elif permissions != READ_PERMISSIONS:
            errors.append(f"{name} permissions must be contents read")
    return errors


def _lint_jobs(jobs: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(jobs, dict):
        return ["release jobs are missing"]
    expected = {"validate", "build-scan", "publish"}
    if set(jobs) != expected:
        errors.append("release jobs must be validate, build-scan, and publish")
        return errors
    if jobs["build-scan"].get("needs") != "validate":
        errors.append("build-scan must follow the read-only validation job")
    if jobs["publish"].get("needs") != ["validate", "build-scan"]:
        errors.append("publish must follow validation and the digest scan")
    steps = _steps(jobs)
    errors.extend(_lint_validate_steps(steps["validate"]))
    errors.extend(_lint_build_steps(steps["build-scan"]))
    errors.extend(_lint_publish_steps(steps["publish"]))
    joined = "\n".join(
        json.dumps(step, sort_keys=True) for group in steps.values() for step in group
    )
    for marker in DEPLOY_MARKERS:
        if marker in joined:
            errors.append(f"release workflow contains a deployment step: {marker.strip()}")
    return errors


def _steps(jobs: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for name, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        result[name] = (
            [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
        )
    return result


def _uses(steps: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    return [step for step in steps if str(step.get("uses", "")).startswith(prefix)]


def _raw_options(step: dict[str, Any]) -> dict[str, Any]:
    raw_options = step.get("with")
    if not isinstance(raw_options, dict):
        return {}
    options: dict[str, Any] = {}
    for key, value in raw_options.items():
        if isinstance(key, str):
            options[key] = value
    return options


def _lint_validate_steps(steps: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    rendered = json.dumps(steps)
    if "actions/checkout@v4" not in rendered or "persist-credentials" not in rendered:
        errors.append("validate must check out the release commit without persisted credentials")
    if "scripts/production_artifacts.py" not in rendered or "--require-docker" not in rendered:
        errors.append("validate must run the Increment 1 artifact checks")
    if "check-ref" not in rendered:
        errors.append("validate must apply the semver tag gate")
    if "test_release_supply_chain.py" not in rendered:
        errors.append("validate must run the release supply-chain tests")
    if "test_backup_policy.py" not in rendered:
        errors.append("validate must run the backup policy tests")
    return errors


def _lint_build_steps(steps: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    builds = _uses(steps, "docker/build-push-action@v")
    if len(builds) != 3:
        errors.append("build-scan must build the API, web, and PostgreSQL images")
    contexts = {
        str((build.get("with") or {}).get("context"))
        for build in builds
        if isinstance(build.get("with"), dict)
    }
    if {"backend", "web", "infra/postgres"} - contexts:
        errors.append("image builds must use the API, web, and PostgreSQL contexts")
    for build in builds:
        options = build.get("with")
        if not isinstance(options, dict):
            errors.append("image build options are missing")
            continue
        if options.get("push") is not False:
            errors.append("image builds must not push")
        if options.get("provenance") is not False or options.get("sbom") is not False:
            errors.append("BuildKit provenance must stay off; GitHub attestations run after push")
        if "build-args" in options or "secrets" in options or "tags" in options:
            errors.append("image builds must not take tags, build args, or secrets")
        outputs = str(options.get("outputs", ""))
        if outputs not in OCI_OUTPUTS:
            errors.append("image builds must export OCI layouts with tar=false")
    scans = _uses(steps, "aquasecurity/trivy-action@v")
    if len(scans) != 6:
        errors.append("build-scan must write three SBOMs and three vulnerability results")
    seen_digest_inputs: set[str] = set()
    for scan in scans:
        options = _raw_options(scan)
        image_ref = str(options.get("image-ref", ""))
        if "outputs.digest" not in image_ref or "github.sha" in image_ref:
            errors.append("vulnerability scans must use the build digest")
        seen_digest_inputs.add(image_ref)
        if str(options.get("exit-code")) != "1":
            errors.append("vulnerability scans must fail closed")
        if options.get("ignore-unfixed") is not True:
            errors.append("vulnerability scans must ignore findings without an available fix")
        if options.get("severity") != "HIGH,CRITICAL":
            errors.append("vulnerability scans must gate high and critical findings")
        if not str(options.get("version", "")).startswith("v"):
            errors.append("Trivy version must be pinned")
        if str(options.get("input", "")) not in OCI_LAYOUTS:
            errors.append("vulnerability scans must read the OCI layouts")
    if not any("api.outputs.digest" in item for item in seen_digest_inputs):
        errors.append("API scan input is not the API build digest")
    if not any("web.outputs.digest" in item for item in seen_digest_inputs):
        errors.append("web scan input is not the web build digest")
    if not any("postgres.outputs.digest" in item for item in seen_digest_inputs):
        errors.append("PostgreSQL scan input is not the PostgreSQL build digest")
    inputs = [str((scan.get("with") or {}).get("input")) for scan in scans]
    if (
        inputs.count("release/api") != 2
        or inputs.count("release/web") != 2
        or inputs.count("release/postgres") != 2
    ):
        errors.append("each OCI layout must be scanned for an SBOM and vulnerabilities")
    formats = [str((scan.get("with") or {}).get("format")) for scan in scans]
    if formats.count("cyclonedx") != 3 or formats.count("json") != 3:
        errors.append("release outputs need CycloneDX SBOMs and JSON scan results")
    rendered = json.dumps(steps)
    if rendered.count("tar=false") != 3:
        errors.append("image builds must set tar=false")
    if rendered.count("bind-digest") != 3 or any(name not in rendered for name in OCI_LAYOUTS):
        errors.append("each OCI layout must be bound to its build digest before scanning")
    uploads = _uses(steps, "actions/upload-artifact@v")
    if len(uploads) < 2:
        errors.append("release artifacts must be uploaded")
    for upload in uploads:
        options = _raw_options(upload)
        if options.get("retention-days") != 7:
            errors.append("release artifacts need a 7 day retention")
    return errors


def _lint_publish_steps(steps: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    rendered = json.dumps(steps)
    if rendered.count("publish-image") != 3 or any(name not in rendered for name in OCI_LAYOUTS):
        errors.append("publish must copy the scanned API, web, and PostgreSQL layouts")
    if "gym-api" not in rendered or "gym-web" not in rendered or "gym-postgres" not in rendered:
        errors.append("publish must name the API, web, and PostgreSQL images")
    if "normalize-owner" not in rendered or "immutable-tag" not in rendered:
        errors.append("publish must normalize the owner and bind the commit tag")
    attests = _uses(steps, "actions/attest-build-provenance@v3")
    if len(attests) != 3:
        errors.append("publish must record build provenance for each image digest")
    if any(str(step.get("uses", "")).startswith("actions/attest@") for step in steps):
        errors.append("generic attestation is not build provenance")
    for attest in attests:
        options = _raw_options(attest)
        if options.get("push-to-registry") is not True:
            errors.append("image attestations must be pushed to the registry")
        if options.get("create-storage-record") is not False:
            errors.append("image attestations must not require organization storage records")
        digest = str(options.get("subject-digest", ""))
        if "outputs.digest" not in digest:
            errors.append("attestations must use the verified registry digest")
        if ":" in str(options.get("subject-name", "")):
            errors.append("attestation subject names must not include a tag")
    if "write-manifest" not in rendered or "postgres-digest-file" not in rendered:
        errors.append("publish must write the digest manifest, including PostgreSQL")
    if "compose.env.example" in rendered:
        errors.append("publish must not rewrite the committed Compose fixture")
    if "docker/build-push-action" in rendered:
        errors.append("publish must not rebuild images")
    return errors


def _lint_shell(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return errors
    for name, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if isinstance(script, str) and "${{" in script:
                errors.append(f"{name} interpolates a GitHub expression inside the shell")
            options = step.get("with")
            if isinstance(options, dict):
                for key, value in options.items():
                    if isinstance(value, str) and ":latest" in value:
                        errors.append(f"{name} uses a latest tag in {key}")
                    if key in {"build-args", "secrets"} and "build-push-action" in str(
                        step.get("uses", "")
                    ):
                        errors.append(f"{name} passes build secrets")
    secret_text = json.dumps(document)
    if "secrets." in secret_text and secret_text.count("secrets.GITHUB_TOKEN") != secret_text.count(
        "secrets."
    ):
        errors.append("release workflow may use only GITHUB_TOKEN")
    return errors


def _manifest_from_env() -> dict[str, Any]:
    return build_manifest(
        git_commit=os.environ["RELEASE_SHA"],
        git_ref=os.environ["RELEASE_REF"],
        git_ref_name=os.environ["RELEASE_REF_NAME"],
        owner=os.environ["REPOSITORY_OWNER"],
        api_digest=Path(os.environ["API_DIGEST_FILE"]).read_text(encoding="utf-8").strip(),
        web_digest=Path(os.environ["WEB_DIGEST_FILE"]).read_text(encoding="utf-8").strip(),
        postgres_digest=Path(os.environ["POSTGRES_DIGEST_FILE"])
        .read_text(encoding="utf-8")
        .strip(),
        workflow=os.environ["GITHUB_WORKFLOW_FILE"],
        run_id=os.environ["GITHUB_RUN_ID"],
        run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
        timestamp=os.environ["RELEASE_TIMESTAMP"],
    )


def _publish_image(layout: Path, name: str) -> str:
    owner = normalize_owner(os.environ["REPOSITORY_OWNER"])
    tag = immutable_tag(os.environ["RELEASE_SHA"])
    repository = image_repository(owner, name)
    expected = Path(os.environ["EXPECTED_DIGEST_FILE"]).read_text(encoding="utf-8").strip()
    actual = oci_layout_manifest_digest(layout)
    assert_same_digest(expected, actual)
    destination = f"docker://{repository}:{tag}"
    credentials = f"{os.environ['GHCR_USER']}:{os.environ['GHCR_TOKEN']}"
    _run(
        [
            "skopeo",
            "copy",
            "--preserve-digests",
            "--dest-creds",
            credentials,
            f"oci:{layout}",
            destination,
        ]
    )
    inspected = _run(
        [
            "skopeo",
            "inspect",
            "--creds",
            credentials,
            "--format",
            "{{.Digest}}",
            f"docker://{repository}:{tag}",
        ]
    ).strip()
    assert_same_digest(expected, inspected)
    return digest_reference(repository, inspected)


def _run(command: list[str]) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise ReleaseError(detail)
    return result.stdout


def _read_digest_output(value: str) -> str:
    digest = value.strip()
    if DIGEST_RE.fullmatch(digest) is None:
        raise ReleaseError("digest output is invalid")
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check-ref")
    owner = commands.add_parser("normalize-owner")
    owner.add_argument("--owner", required=True)
    tag = commands.add_parser("immutable-tag")
    tag.add_argument("--sha", required=True)
    repository = commands.add_parser("image-repository")
    repository.add_argument("--owner", required=True)
    repository.add_argument("--name", required=True)
    bind = commands.add_parser("bind-digest")
    bind.add_argument("--digest", required=True)
    bind.add_argument("--layout", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("publish-image")
    publish.add_argument("--layout", type=Path, required=True)
    publish.add_argument("--name", required=True)
    manifest = commands.add_parser("write-manifest")
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("--api-digest-file", type=Path, required=True)
    manifest.add_argument("--web-digest-file", type=Path, required=True)
    manifest.add_argument("--postgres-digest-file", type=Path, required=True)
    compose = commands.add_parser("compose-env")
    compose.add_argument("--manifest", type=Path, required=True)
    compose.add_argument("--output", type=Path)
    compose.add_argument("--repo", type=Path, default=Path("."))

    args = parser.parse_args(argv)
    try:
        if args.command == "check-ref":
            check_ref(
                event=os.environ["RELEASE_EVENT"],
                ref_type=os.environ["RELEASE_REF_TYPE"],
                ref_name=os.environ["RELEASE_REF_NAME"],
                sha=os.environ["RELEASE_SHA"],
                input_sha=os.environ.get("RELEASE_INPUT_SHA", ""),
            )
        elif args.command == "normalize-owner":
            print(normalize_owner(args.owner))
        elif args.command == "immutable-tag":
            print(immutable_tag(args.sha))
        elif args.command == "image-repository":
            print(image_repository(args.owner, args.name))
        elif args.command == "bind-digest":
            print(
                bind_digest(
                    digest=_read_digest_output(args.digest),
                    layout=args.layout,
                    output=args.output,
                )
            )
        elif args.command == "publish-image":
            print(_publish_image(args.layout, args.name))
        elif args.command == "write-manifest":
            os.environ["API_DIGEST_FILE"] = str(args.api_digest_file)
            os.environ["WEB_DIGEST_FILE"] = str(args.web_digest_file)
            os.environ["POSTGRES_DIGEST_FILE"] = str(args.postgres_digest_file)
            payload = _manifest_from_env()
            args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(args.output)
        elif args.command == "compose-env":
            if args.output is not None:
                refuse_fixture_output(args.output, args.repo.resolve())
            lines = compose_env_lines(json.loads(args.manifest.read_text(encoding="utf-8")))
            if args.output is None:
                sys.stdout.write(lines)
            else:
                args.output.write_text(lines, encoding="utf-8")
    except (ReleaseError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
