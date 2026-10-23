from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "image-release.yml"
API_DIGEST = "sha256:" + "ab" * 32
WEB_DIGEST = "sha256:" + "cd" * 32
POSTGRES_DIGEST = "sha256:" + "ef" * 32


def load():
    path = ROOT / "scripts" / "release_supply_chain.py"
    spec = importlib.util.spec_from_file_location("release_supply_chain", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("release supply chain module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_passes_static_checks() -> None:
    assert load().lint_repository_release(ROOT) == []


def test_latest_and_tag_only_references_fail() -> None:
    module = load()
    assert module.lint_release_workflow_text(workflow_text() + "\n# latest\n")
    tagged = workflow_text() + "\n# ghcr.io/example/gym-api:v1.2.3\n"
    assert any("tag-only" in error for error in module.lint_release_workflow_text(tagged))


def test_overbroad_permissions_fail() -> None:
    module = load()
    text = workflow_text().replace(
        "permissions:\n  contents: read\n",
        "permissions:\n  contents: write\n  packages: write\n",
        1,
    )
    assert any("top-level" in error for error in module.lint_release_workflow_text(text))


def test_trigger_bypasses_fail() -> None:
    module = load()
    pull_request = workflow_text().replace('"on":\n', '"on":\n  pull_request:\n', 1)
    assert any("pull_request" in error for error in module.lint_release_workflow_text(pull_request))
    branches = workflow_text().replace(
        "  push:\n    tags:\n",
        "  push:\n    branches:\n      - main\n    tags:\n",
        1,
    )
    assert any("tags only" in error for error in module.lint_release_workflow_text(branches))


def test_missing_commit_binding_attestations_sbom_and_scan_fail() -> None:
    module = load()
    unbound = workflow_text().replace("github.sha", "github.ref")
    assert any("commit" in error for error in module.lint_release_workflow_text(unbound))
    unattested = workflow_text().replace(
        "actions/attest-build-provenance@v3",
        "actions/attest@v4",
        1,
    )
    assert any(
        "build provenance" in error or "generic attestation" in error
        for error in module.lint_release_workflow_text(unattested)
    )
    no_manifest = workflow_text().replace("write-manifest", "skip-manifest")
    assert any("manifest" in error for error in module.lint_release_workflow_text(no_manifest))
    open_scan = workflow_text().replace('exit-code: "1"', 'exit-code: "0"', 1)
    assert any("fail closed" in error for error in module.lint_release_workflow_text(open_scan))
    no_sbom = workflow_text().replace("cyclonedx", "table")
    assert any("CycloneDX" in error for error in module.lint_release_workflow_text(no_sbom))


def test_deployment_step_fails() -> None:
    module = load()
    deployed = workflow_text().replace(
        "sudo apt-get update && sudo apt-get install -y skopeo",
        "ssh deploy@host",
    )
    assert any("deployment" in error for error in module.lint_release_workflow_text(deployed))


def test_manifest_feeds_digest_only_compose_env(tmp_path: Path) -> None:
    module = load()
    manifest = module.build_manifest(
        git_commit="a" * 40,
        git_ref="refs/tags/v1.2.3",
        git_ref_name="v1.2.3",
        owner="AcmeOrg",
        api_digest=API_DIGEST,
        web_digest=WEB_DIGEST,
        postgres_digest=POSTGRES_DIGEST,
        workflow="image-release.yml",
        run_id="42",
        run_attempt="1",
        timestamp="2026-10-21T02:08:00Z",
    )
    assert manifest["images"]["api"] == f"ghcr.io/acmeorg/gym-api@{API_DIGEST}"
    assert manifest["images"]["web"] == f"ghcr.io/acmeorg/gym-web@{WEB_DIGEST}"
    assert manifest["images"]["postgres"] == f"ghcr.io/acmeorg/gym-postgres@{POSTGRES_DIGEST}"
    assert manifest["schema_version"] == 2
    assert manifest["git_commit"] == "a" * 40
    assert manifest["runtime_baseline"]["python"] == "3.14.7"
    assert manifest["runtime_baseline"]["pgbackrest"] == "2.60.0"
    lines = module.compose_env_lines(manifest)
    assert lines == (
        f"API_IMAGE=ghcr.io/acmeorg/gym-api@{API_DIGEST}\n"
        f"WEB_IMAGE=ghcr.io/acmeorg/gym-web@{WEB_DIGEST}\n"
        f"POSTGRES_IMAGE=ghcr.io/acmeorg/gym-postgres@{POSTGRES_DIGEST}\n"
    )
    assert "rpo_proven" not in json.dumps(manifest)
    with pytest.raises(module.ReleaseError):
        module.compose_env_lines({"images": {"api": "ghcr.io/acmeorg/gym-api:v1.2.3"}})
    fixture = tmp_path / "infra/environments/production/compose.env.example"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("API_IMAGE=placeholder\n", encoding="utf-8")
    with pytest.raises(module.ReleaseError):
        module.refuse_fixture_output(fixture, tmp_path)
    path = tmp_path / "release-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    destination = tmp_path / "operator.env"
    assert (
        module.main(
            [
                "compose-env",
                "--manifest",
                str(path),
                "--output",
                str(destination),
                "--repo",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert destination.read_text(encoding="utf-8") == lines


def test_release_ref_and_owner_controls() -> None:
    module = load()
    assert module.normalize_owner("AcmeOrg") == "acmeorg"
    assert module.immutable_tag("a" * 40) == "sha-" + "a" * 40
    module.check_ref(
        event="push",
        ref_type="tag",
        ref_name="v1.2.3",
        sha="a" * 40,
        input_sha="",
    )
    module.check_ref(
        event="workflow_dispatch",
        ref_type="tag",
        ref_name="v1.2.3",
        sha="b" * 40,
        input_sha="b" * 40,
    )
    with pytest.raises(module.ReleaseError):
        module.check_ref(
            event="workflow_dispatch",
            ref_type="branch",
            ref_name="main",
            sha="b" * 40,
            input_sha="b" * 40,
        )
    with pytest.raises(module.ReleaseError):
        module.check_ref(
            event="workflow_dispatch",
            ref_type="tag",
            ref_name="v1.2.3",
            sha="b" * 40,
            input_sha="c" * 40,
        )
    with pytest.raises(module.ReleaseError):
        module.normalize_owner("../Acme")


def write_layout(
    path: Path,
    *,
    blob: bytes = b'{"schemaVersion":2}',
    manifests: list[dict[str, str]] | None = None,
    index_bytes: bytes | None = None,
    include_index: bool = True,
    include_blob: bool = True,
    symlink_index: bool = False,
    symlink_blob: bool = False,
    symlink_blobs: bool = False,
) -> str:
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    if manifests is None:
        manifests = [{"digest": digest}]
    path.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps({"schemaVersion": 2, "manifests": manifests}).encode()
        if index_bytes is None
        else index_bytes
    )
    if include_index:
        if symlink_index:
            outside = path.parent / "outside-index.json"
            outside.write_bytes(payload)
            (path / "index.json").symlink_to(outside)
        else:
            (path / "index.json").write_bytes(payload)
    if include_blob and manifests and isinstance(manifests[0].get("digest"), str):
        hex_digest = manifests[0]["digest"].split(":", 1)[-1]
        if len(hex_digest) == 64 and all(char in "0123456789abcdef" for char in hex_digest):
            if symlink_blobs:
                outside = path.parent / "outside-blobs" / "sha256"
                outside.mkdir(parents=True)
                (outside / hex_digest).write_bytes(blob)
                (path / "blobs").symlink_to(path.parent / "outside-blobs")
            else:
                blob_dir = path / "blobs" / "sha256"
                blob_dir.mkdir(parents=True)
                target = blob_dir / hex_digest
                if symlink_blob:
                    outside_blob = path.parent / "outside-blob"
                    outside_blob.write_bytes(blob)
                    target.symlink_to(outside_blob)
                else:
                    target.write_bytes(blob)
    (path / "ingest").mkdir(exist_ok=True)
    (path / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n', encoding="utf-8")
    return digest


def test_layout_digest_matches_manifest_blob(tmp_path: Path) -> None:
    module = load()
    layout = tmp_path / "api"
    blob = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
    digest = write_layout(layout, blob=blob)
    output = tmp_path / "api-digest.txt"
    assert module.bind_digest(digest=digest, layout=layout, output=output) == digest
    assert output.read_text(encoding="utf-8") == f"{digest}\n"
    with pytest.raises(module.ReleaseError, match="does not match the build digest"):
        module.bind_digest(digest=WEB_DIGEST, layout=layout, output=output)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"include_index": False}, "missing index.json"),
        ({"index_bytes": b"{"}, "OCI index is invalid"),
        ({"manifests": []}, "exactly one image manifest"),
        (
            {"manifests": [{"digest": API_DIGEST}, {"digest": WEB_DIGEST}]},
            "exactly one image manifest",
        ),
        ({"manifests": [{"digest": "sha256:../" + ("ab" * 30)}]}, "invalid"),
        ({"symlink_index": True}, "unsafe"),
        ({"symlink_blob": True}, "unsafe"),
        ({"symlink_blobs": True}, "unsafe"),
        ({"include_blob": False}, "manifest blob is missing"),
        (
            {"manifests": [{"digest": API_DIGEST}], "blob": b"not-the-declared-manifest"},
            "does not match its digest",
        ),
    ],
)
def test_malformed_oci_layouts_are_rejected(tmp_path: Path, kwargs: dict, match: str) -> None:
    module = load()
    layout = tmp_path / "api"
    write_layout(layout, **kwargs)
    with pytest.raises(module.ReleaseError, match=match):
        module.oci_layout_manifest_digest(layout)


def test_oci_tar_is_not_a_layout(tmp_path: Path) -> None:
    module = load()
    archive = tmp_path / "api.oci.tar"
    archive.write_bytes(b"this is not an OCI layout directory")
    with pytest.raises(module.ReleaseError, match="must be a directory"):
        module.oci_layout_manifest_digest(archive)


def test_layout_transport_and_provenance_cannot_regress() -> None:
    module = load()
    source = (ROOT / "scripts" / "release_supply_chain.py").read_text(encoding="utf-8")
    assert module.lint_publish_transport(source) == []
    without_preserve = source.replace('"--preserve-digests",', '"--all",', 1)
    assert any(
        "--preserve-digests" in error for error in module.lint_publish_transport(without_preserve)
    )
    archive_transport = source.replace(
        'f"oci:{layout}",\n            destination,',
        'f"oci-archive:{layout}",\n            destination,',
        1,
    )
    archive_errors = module.lint_publish_transport(archive_transport)
    assert any("OCI archive transport" in error for error in archive_errors)
    generic = workflow_text().replace(
        "actions/attest-build-provenance@v3",
        "actions/attest@v4",
    )
    errors = module.lint_release_workflow_text(generic)
    assert any("generic attestation is not build provenance" in error for error in errors)
    default_tar = workflow_text().replace(",tar=false", "", 1)
    export_errors = module.lint_release_workflow_text(default_tar)
    assert any("tar=false" in error for error in export_errors)
