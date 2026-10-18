from __future__ import annotations

import os
import socket
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.mail.resend import RESEND_ENDPOINT_HOST
from apps.common.production import (
    PRODUCTION_SCHEDULER_COMMANDS,
    require_allowlisted_value,
    require_aws_singapore_region,
    require_dedicated_reset_pepper,
    require_https_endpoint,
    require_https_public_url,
    require_private_clamav_host,
    require_resend_api_key,
    require_resend_backend,
    require_reset_ttl_seconds,
    require_scheduler_commands,
    require_verified_sender,
)


class Command(BaseCommand):
    help = (
        "Validate production email, password-reset, S3, and ClamAV configuration. "
        "Live TCP or HTTPS probes run only when --live is set and credentials exist."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--live",
            action="store_true",
            help="Open live sockets to Resend, S3, and ClamAV. Requires real credentials.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            require_resend_backend(str(settings.EMAIL_BACKEND))
            require_resend_api_key(str(settings.RESEND_API_KEY))
            require_verified_sender(str(settings.DEFAULT_FROM_EMAIL))
            require_https_public_url(str(settings.APP_PUBLIC_URL))
            require_dedicated_reset_pepper(
                reset_pepper=str(settings.PASSWORD_RESET_TOKEN_PEPPER),
                other_secrets={
                    "REFRESH_TOKEN_PEPPER": str(settings.REFRESH_TOKEN_PEPPER),
                    "MFA_TOKEN_PEPPER": str(settings.MFA_TOKEN_PEPPER),
                    "MFA_ENCRYPTION_KEY": str(settings.MFA_ENCRYPTION_KEY),
                    "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": str(
                        settings.FINANCE_IDEMPOTENCY_ENCRYPTION_KEY
                    ),
                    "INVITATION_TOKEN_PEPPER": str(settings.INVITATION_TOKEN_PEPPER),
                },
            )
            require_reset_ttl_seconds(int(settings.PASSWORD_RESET_TTL_SECONDS))
            require_scheduler_commands(PRODUCTION_SCHEDULER_COMMANDS)
            require_https_endpoint(str(settings.MINIO_ENDPOINT_URL), name="MINIO_ENDPOINT_URL")
            require_https_endpoint(str(settings.MINIO_PUBLIC_URL), name="MINIO_PUBLIC_URL")
            require_aws_singapore_region(str(settings.MINIO_REGION))
            require_allowlisted_value(
                str(settings.MINIO_BUCKET),
                tuple(settings.OBJECT_STORE_BUCKET_ALLOWLIST),
                name="MINIO_BUCKET",
            )
            require_allowlisted_value(
                str(settings.MINIO_PUBLIC_URL).rstrip("/"),
                tuple(settings.OBJECT_STORE_ORIGIN_ALLOWLIST),
                name="MINIO_PUBLIC_URL",
            )
            require_private_clamav_host(str(settings.CLAMAV_HOST))
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("Production provider configuration is internally consistent.")
        if not options["live"]:
            return
        if os.environ.get("LIVE_PROVIDER_SMOKE") != "1":
            raise CommandError("Set LIVE_PROVIDER_SMOKE=1 to run live provider probes")
        self._probe_host(RESEND_ENDPOINT_HOST, 443, "Resend")
        self._probe_host(urlsplit(str(settings.MINIO_ENDPOINT_URL)).hostname or "", 443, "S3")
        self._probe_host(str(settings.CLAMAV_HOST), int(settings.CLAMAV_PORT), "ClamAV")
        self.stdout.write("Live provider sockets accepted a connection.")

    def _probe_host(self, host: str, port: int, name: str) -> None:
        if not host:
            raise CommandError(f"{name} host is missing")
        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError as exc:
            raise CommandError(f"{name} is unreachable") from exc
