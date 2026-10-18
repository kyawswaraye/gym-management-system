from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

RESEND_BACKEND = "apps.common.mail.resend.EmailBackend"
PRODUCTION_SCHEDULER_COMMANDS = (
    "schedule_membership_expiry_emails",
    "deliver_membership_expiry_emails",
)
AWS_PRODUCTION_REGION = "ap-southeast-1"


def require_resend_backend(email_backend: str) -> None:
    if email_backend != RESEND_BACKEND:
        raise ImproperlyConfigured("Production mail must use the Resend HTTP backend")


def require_resend_api_key(api_key: str) -> None:
    if not api_key or api_key == "unsafe-local-only" or len(api_key) < 20:
        raise ImproperlyConfigured("RESEND_API_KEY must be a production API key")


def require_verified_sender(from_email: str) -> None:
    sender = from_email.strip()
    if (
        sender.count("@") != 1
        or sender.startswith("@")
        or sender.endswith("@")
        or "localhost" in sender.lower()
        or " " in sender.split("@", 1)[1]
    ):
        raise ImproperlyConfigured("DEFAULT_FROM_EMAIL must be a verified-style production sender")


def require_https_public_url(public_url: str) -> None:
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ImproperlyConfigured("APP_PUBLIC_URL must be an https public origin")


def require_dedicated_reset_pepper(*, reset_pepper: str, other_secrets: Mapping[str, str]) -> None:
    if not reset_pepper or reset_pepper == "unsafe-local-only" or len(reset_pepper) < 32:
        raise ImproperlyConfigured("PASSWORD_RESET_TOKEN_PEPPER must be at least 32 characters")
    values = [reset_pepper, *other_secrets.values()]
    if len(set(values)) != len(values):
        raise ImproperlyConfigured(
            "PASSWORD_RESET_TOKEN_PEPPER must be distinct from the refresh, MFA, "
            "invitation, and finance keys"
        )


def require_reset_ttl_seconds(ttl_seconds: int) -> None:
    if ttl_seconds != 30 * 60:
        raise ImproperlyConfigured("Password reset tokens must expire after 30 minutes")


def require_scheduler_commands(commands: tuple[str, ...]) -> None:
    if commands != PRODUCTION_SCHEDULER_COMMANDS:
        raise ImproperlyConfigured("Production scheduler commands must stay the documented pair")


def require_https_endpoint(url: str, *, name: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ImproperlyConfigured(f"{name} must be an https endpoint")


def require_aws_singapore_region(region: str) -> None:
    if region != AWS_PRODUCTION_REGION:
        raise ImproperlyConfigured("Production object storage must use ap-southeast-1")


def require_allowlisted_value(value: str, allowlist: tuple[str, ...], *, name: str) -> None:
    if value not in allowlist:
        raise ImproperlyConfigured(f"{name} is not in the production allowlist")


def require_private_clamav_host(host: str) -> None:
    if not host or host in {"0.0.0.0", "::", "*"}:
        raise ImproperlyConfigured("CLAMAV_HOST must be a private production address")
    if host in {"clamav.example.com"}:
        raise ImproperlyConfigured("CLAMAV_HOST must be a private production address")
