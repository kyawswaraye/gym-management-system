#!/usr/bin/env python3
"""Isolated restore rehearsal. This does not prove RPO or RTO.

The production compose project and its postgres_data volume are refused.
Secrets are read from files. Images must be repository digests.
Verification runs after restore and a nonzero verification exits nonzero.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "infra" / "postgres"))

import backup_policy as policy  # noqa: E402


def _reject_production(
    project: str, volume: str, repo_volume: str, compose_file: Path
) -> None:
    if (
        project == "gym-production"
        or volume == "postgres_data"
        or repo_volume == "postgres_data"
    ):
        raise policy.BackupPolicyError(
            "restore cannot target the production project or volume"
        )
    if compose_file.name == "docker-compose.prod.yml" or "gym-production" in str(
        compose_file
    ):
        raise policy.BackupPolicyError("restore cannot use the production compose file")


def _env_file(directory: Path, values: dict[str, str], name: str) -> Path:
    lines = []
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise policy.BackupPolicyError(f"{key} is not safe in an env file")
        lines.append(f"{key}={value}")
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, ("\n".join(lines) + "\n").encode("utf-8"))
    os.close(fd)
    return path


def _captured_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if not isinstance(completed.stdout, str):
        completed.stdout = ""
    if not isinstance(completed.stderr, str):
        completed.stderr = ""
    return completed


def _volume_exists(name: str) -> bool:
    inspected = _run(["docker", "volume", "inspect", name])
    return inspected.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--volume", required=True)
    parser.add_argument("--repo-volume", default="")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--stanza", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--db-password-file", required=True, type=Path)
    parser.add_argument("--django-secret-file", required=True, type=Path)
    parser.add_argument("--cipher-file", required=True, type=Path)
    parser.add_argument("--access-key-file", type=Path)
    parser.add_argument("--secret-key-file", type=Path)
    parser.add_argument("--repo-mode", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--uri-style", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=REPO / "infra" / "compose" / "docker-compose.restore.yml",
    )
    args = parser.parse_args(argv)
    try:
        policy.validate_digest_image(args.postgres_image)
        policy.validate_digest_image(args.api_image)
        db_password = policy.read_bounded_secret(
            args.db_password_file, "restore database password"
        )
        django_secret = policy.read_bounded_secret(
            args.django_secret_file, "restore django secret"
        )
        _reject_production(
            args.project, args.volume, args.repo_volume, args.compose_file
        )
        request = {
            "RESTORE_CONFIRMATION": args.confirmation,
            "RESTORE_TARGET": "disposable",
            "RESTORE_PROJECT": args.project,
            "RESTORE_VOLUME": args.volume,
            "RESTORE_REPO_VOLUME": args.repo_volume,
            "RESTORE_STANZA": args.stanza,
            "RESTORE_REPOSITORY_ID": args.repository_id,
            "BACKUP_REPO_MODE": args.repo_mode,
            "BACKUP_S3_ENDPOINT": args.endpoint,
            "BACKUP_S3_REGION": args.region,
            "BACKUP_S3_BUCKET": args.bucket,
            "BACKUP_S3_URI_STYLE": args.uri_style,
            "BACKUP_REPO_PATH": args.repo_path,
            "BACKUP_STANZA": args.stanza,
            "BACKUP_PGDATA": policy.RESTORE_PGDATA,
        }
        policy.validate_restore_request(request)
        key_files = policy.restore_storage_key_files(
            args.repo_mode, args.access_key_file, args.secret_key_file
        )
        actions = policy.prepare_restore_volumes(
            mode=args.repo_mode,
            target=args.volume,
            repo=args.repo_volume,
            volume_exists=_volume_exists,
        )
        labels = policy.restore_volume_labels(args.project, policy.new_restore_nonce())
        for action, name in actions:
            if action != "create":
                raise policy.BackupPolicyError("restore volume action is not create")
            created = _run(policy.build_volume_create_command(name, labels))
            if created.returncode != 0:
                raise policy.BackupPolicyError(
                    "restore target volume was not created for this restore"
                )
            inspected = _run(policy.build_volume_inspect_command(name))
            policy.accept_volume_inspect(inspected, labels)
        restore_command = policy.build_restore_command(policy.CONFIG_PATH)
        policy.assert_restore_command(restore_command)
        compose_text = args.compose_file.read_text(encoding="utf-8")
        if "verify_restore_rehearsal" not in compose_text:
            raise policy.BackupPolicyError("restore compose skips verification")
        if policy.lint_restore_compose(compose_text):
            raise policy.BackupPolicyError(
                "restore compose is not a credential-free internal target"
            )
        chowned = _run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--entrypoint",
                "chown",
                "-v",
                f"{args.volume}:{policy.RESTORE_PGDATA}",
                args.postgres_image,
                "postgres:postgres",
                policy.RESTORE_PGDATA,
            ]
        )
        if chowned.returncode != 0:
            raise policy.BackupPolicyError(
                _captured_text(chowned.stderr) or "volume ownership failed"
            )
    except (policy.BackupPolicyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="gym-restore-") as temporary:
        one_shot_values = {
            "BACKUP_REPO_MODE": args.repo_mode,
            "BACKUP_S3_ENDPOINT": args.endpoint,
            "BACKUP_S3_REGION": args.region,
            "BACKUP_S3_BUCKET": args.bucket,
            "BACKUP_S3_URI_STYLE": args.uri_style,
            "BACKUP_REPO_PATH": args.repo_path,
            "BACKUP_TIMEOUT_SECONDS": "14400",
            "RESTORE_CONFIRMATION": args.confirmation,
            "RESTORE_TARGET": "disposable",
            "RESTORE_PROJECT": args.project,
            "RESTORE_VOLUME": args.volume,
            "RESTORE_STANZA": args.stanza,
            "RESTORE_REPOSITORY_ID": args.repository_id,
            "BACKUP_STANZA": args.stanza,
            "BACKUP_PG_USER": "gym_admin",
            "BACKUP_PG_DATABASE": "gym_platform",
            "BACKUP_PGDATA": policy.RESTORE_PGDATA,
            "BACKUP_ARCHIVE_PUSH_TIMEOUT_SECONDS": "60",
            "PGBACKREST_CONFIG": policy.CONFIG_PATH,
            "PGDATA": policy.RESTORE_PGDATA,
        }
        if args.repo_volume:
            one_shot_values["RESTORE_REPO_VOLUME"] = args.repo_volume
        one_shot_env = _env_file(Path(temporary), one_shot_values, "one-shot.env")
        compose_env = _env_file(
            Path(temporary),
            {
                "POSTGRES_IMAGE": args.postgres_image,
                "API_IMAGE": args.api_image,
                "POSTGRES_DB": "gym_platform",
                "RESTORE_PROJECT": args.project,
                "RESTORE_VOLUME": args.volume,
                "RESTORE_DB_USER": "gym_admin",
                "RESTORE_DB_PASSWORD": db_password,
                "RESTORE_DJANGO_SECRET_KEY": django_secret,
            },
            "compose.env",
        )
        mounts = [f"{args.volume}:{policy.RESTORE_PGDATA}"]
        if args.repo_mode == policy.REHEARSAL_MODE:
            mounts.append(f"{args.repo_volume}:/var/lib/pgbackrest:ro")
        network = "none" if args.repo_mode == policy.REHEARSAL_MODE else "bridge"
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--env-file",
            str(one_shot_env),
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/run/pgbackrest:uid=999,gid=999,mode=0700",
            "--tmpfs",
            "/var/spool/pgbackrest:uid=999,gid=999,mode=0700",
            "--tmpfs",
            "/var/log/pgbackrest:uid=999,gid=999,mode=0700",
            "--tmpfs",
            "/var/run/postgresql",
        ]
        for mount in mounts:
            command.extend(["-v", mount])
        command.extend(
            [
                "-v",
                f"{args.cipher_file}:/run/secrets/{policy.CIPHER_SECRET}:ro",
            ]
        )
        command.extend(policy.storage_key_mounts(key_files))
        command.extend([args.postgres_image, "gym-backup", "restore"])
        restored = _run(command)
        if restored.returncode != 0:
            print(_captured_text(restored.stderr) or "restore failed", file=sys.stderr)
            return restored.returncode or 1
        started = _run(
            [
                "docker",
                "compose",
                "--project-name",
                args.project,
                "--env-file",
                str(compose_env),
                "-f",
                str(args.compose_file),
                "up",
                "-d",
                "postgres",
            ]
        )
        if started.returncode != 0:
            print(
                _captured_text(started.stderr) or "disposable database did not start",
                file=sys.stderr,
            )
            return started.returncode or 1
        verified = _run(
            [
                "docker",
                "compose",
                "--project-name",
                args.project,
                "--env-file",
                str(compose_env),
                "-f",
                str(args.compose_file),
                "--profile",
                "verify",
                "run",
                "--rm",
                "verify",
            ]
        )
    stdout = _captured_text(verified.stdout)
    if stdout:
        print(stdout)
    if verified.returncode != 0:
        detail = _captured_text(verified.stderr)
        if detail:
            print(detail, file=sys.stderr)
    return verified.returncode


if __name__ == "__main__":
    sys.exit(main())
