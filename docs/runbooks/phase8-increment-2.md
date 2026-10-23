# Phase 8 increment 2 — release supply chain

This increment adds a separate image-release workflow. It does not deploy.
It does not push to GHCR from this workspace. Phase 8 is not complete.
Increment 3 later extends this same workflow to publish `gym-postgres` with
the API and web images. The manifest schema is now version 2 and prints three
digest lines.

Images stay unpublished until the workflow runs on a real tag. Branch
protection, tag protection, and required reviewers are not created by this
file and are not proven here.

## Trigger

`.github/workflows/image-release.yml` runs on a `vMAJOR.MINOR.PATCH` tag push,
or on `workflow_dispatch` only when that same tag is selected and the typed
commit SHA equals the checked-out commit. Pull requests, branch pushes, and
scheduled runs do not publish.

## Jobs

`validate` and `build-scan` have `contents: read`. They check the Increment 1
artifacts, build the API, web, and PostgreSQL images for `linux/amd64`, and export OCI
layout directories with `tar=false`. Trivy 0.70 reads an OCI directory or a
Docker archive; it does not open the OCI tar that Buildx writes by default.
Each layout's `index.json` and manifest blob are checked before Trivy scans
that same directory by digest. The scans write CycloneDX SBOMs and fail on
high or critical findings. They do not scan a mutable tag.

`publish` is the only job with `packages: write`, `id-token: write`, and
`attestations: write`. It copies the scanned layouts with
`skopeo copy --preserve-digests oci:<path>` and the tag
`sha-<40 character commit>`. The registry digest must equal the scanned
digest. `actions/attest-build-provenance@v3` then records build provenance for
each published image. The job writes `release-manifest.json` and does not rebuild the
images.

The manifest identifies the git commit, `ghcr.io/<owner>/gym-api@sha256:…`,
`ghcr.io/<owner>/gym-web@sha256:…`, and, since increment 3,
`ghcr.io/<owner>/gym-postgres@sha256:…`, the workflow file, run id, run attempt,
UTC timestamp, and the approved runtime baseline. The owner is lowercased.
No image is tagged `latest`. Build args and build secrets are not passed.

SBOM, scan, layout, and manifest artifacts are kept for 7 days. The committed
Compose fixture is not rewritten.

## Compose digests

After a real workflow run, copy the manifest out of the artifact and print
the digest lines:

```bash
python scripts/release_supply_chain.py compose-env --manifest release-manifest.json
```

Put those lines in the uncommitted host env file used by Increment 1. The
command refuses to overwrite `infra/environments/production/compose.env.example`.

## Still open

- No GHCR push has been performed here.
- Repository protections and deployment approvals are unproven.
- Images are unpublished until the workflow runs.
- Deployment, host bootstrap, backup, restore, observability, load, soak,
  penetration testing, and the live provider gates remain open.

Phase 8 is not complete.
