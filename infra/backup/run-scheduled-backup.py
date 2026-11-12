#!/usr/bin/env python3
"""Run one encrypted offsite backup with the active production image digests.

``compose.env`` keeps the API, web, and PostgreSQL image lines at the zero
digest. Deployment overlays the real digests from ``active-deployment.json``.
This command does the same overlay and then runs the existing Compose backup
service. It does not start or recreate the application stack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATE_PATH = Path("/var/lib/gym-deploy/production/active-deployment.json")
COMPOSE_FILE = Path(
    "/var/lib/gym-deploy/production/src/infra/compose/docker-compose.prod.yml"
)
OVERRIDE_FILE = Path("/usr/local/lib/gym-deploy/override.yml")
ENV_FILE = Path("/etc/gym/production/compose.env")
PROJECT = "gym-production"
TARGET = "production"

IMAGE_ENV = {
    "api": "API_IMAGE",
    "web": "WEB_IMAGE",
    "postgres": "POSTGRES_IMAGE",
    "caddy": "CADDY_IMAGE",
    "clamav": "CLAMAV_IMAGE",
    "prometheus": "PROMETHEUS_IMAGE",
    "alertmanager": "ALERTMANAGER_IMAGE",
    "grafana": "GRAFANA_IMAGE",
    "node-exporter": "NODE_EXPORTER_IMAGE",
    "blackbox": "BLACKBOX_EXPORTER_IMAGE",
    "postgres-exporter": "POSTGRES_EXPORTER_IMAGE",
}
SERVICES = {
    "full": ("backup", "backup-full"),
    "diff": ("backup", "backup-diff"),
    "check": ("backup", "backup-check"),
    "bootstrap": ("bootstrap", "backup-bootstrap"),
}
REFERENCE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
ZERO_DIGEST = "sha256:" + ("0" * 64)


def fail(message: str) -> None:
    print(f"gym-scheduled-backup: {message}", file=sys.stderr)
    raise SystemExit(1)


def overlay_from_payload(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict):
        fail("active deployment payload is missing")
    if payload.get("target") != TARGET or payload.get("project") != PROJECT:
        fail("active deployment is not the production project")
    images = payload.get("images")
    if not isinstance(images, dict):
        fail("active deployment images are missing")
    overlay: dict[str, str] = {}
    for key, env_name in IMAGE_ENV.items():
        reference = images.get(key)
        if not isinstance(reference, str) or REFERENCE_RE.fullmatch(reference) is None:
            fail(f"{env_name} is not a repository digest")
        digest = reference.rsplit("@", 1)[1]
        if digest == ZERO_DIGEST:
            fail(f"{env_name} is the zero digest")
        overlay[env_name] = reference
    return overlay


def load_overlay() -> dict[str, str]:
    for path in (STATE_PATH, COMPOSE_FILE, OVERRIDE_FILE, ENV_FILE):
        if not path.is_file():
            fail(f"required path is missing: {path.name}")
    try:
        document = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("active deployment file cannot be read")
    if not isinstance(document, dict):
        fail("active deployment file is not an object")
    payload = document.get("payload", document)
    return overlay_from_payload(payload)


def compose_base(profile: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(ENV_FILE),
        "-f",
        str(COMPOSE_FILE),
        "-f",
        str(OVERRIDE_FILE),
        "--profile",
        profile,
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {*SERVICES, "--resolve"}:
        fail("usage: run-scheduled-backup.py full|diff|check|bootstrap")
    overlay = load_overlay()
    env = os.environ.copy()
    env.update(overlay)
    if argv[1] == "--resolve":
        return resolve(env, overlay)
    operation = argv[1]
    profile, service = SERVICES[operation]
    digest = overlay["POSTGRES_IMAGE"].rsplit("@", 1)[1]
    print(
        f"gym-scheduled-backup operation={operation} postgres={digest[:19]}",
        flush=True,
    )
    completed = subprocess.run(
        [
            *compose_base(profile),
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "never",
            service,
        ],
        cwd=COMPOSE_FILE.parent,
        env=env,
        check=False,
    )
    return completed.returncode


def resolve(env: dict[str, str], overlay: dict[str, str]) -> int:
    completed = subprocess.run(
        [*compose_base("backup"), "config", "--format", "json"],
        cwd=COMPOSE_FILE.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        fail("compose config failed")
    try:
        document = json.loads(completed.stdout)
        image = document["services"]["backup-check"]["image"]
    except (json.JSONDecodeError, KeyError, TypeError):
        fail("compose config did not resolve backup-check")
    if not isinstance(image, str) or image != overlay["POSTGRES_IMAGE"]:
        fail("backup-check image is not the active postgres digest")
    if ZERO_DIGEST in image:
        fail("resolved image is the zero digest")
    print("gym-scheduled-backup resolve=ok service=backup-check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
