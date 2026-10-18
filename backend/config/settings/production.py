from django.core.exceptions import ImproperlyConfigured

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

from .base import *  # noqa: F403

required = {
    "DJANGO_SECRET_KEY": SECRET_KEY,  # noqa: F405
    "RUNTIME_DB_PASSWORD": DATABASES["default"]["PASSWORD"],  # noqa: F405
    "JWT_KEY_ID": JWT_KEY_ID,  # noqa: F405
    "JWT_PRIVATE_KEY": JWT_PRIVATE_KEY,  # noqa: F405
    "JWT_PUBLIC_KEYS": JWT_PUBLIC_KEYS,  # noqa: F405
    "REFRESH_TOKEN_PEPPER": REFRESH_TOKEN_PEPPER,  # noqa: F405
    "PASSWORD_RESET_TOKEN_PEPPER": PASSWORD_RESET_TOKEN_PEPPER,  # noqa: F405
    "RESEND_API_KEY": RESEND_API_KEY,  # noqa: F405
}
missing = [name for name, value in required.items() if not value or value == "unsafe-local-only"]
if missing:
    raise ImproperlyConfigured(f"Missing production configuration: {', '.join(missing)}")
if JWT_ACCESS_TTL_SECONDS != 600:  # noqa: F405
    raise ImproperlyConfigured("Staff access TTL must be exactly 600 seconds")
if not BFF_SHARED_SECRET or len(BFF_SHARED_SECRET) < 32:  # noqa: F405
    raise ImproperlyConfigured("BFF_SHARED_SECRET must be at least 32 characters")
if DJANGO_LISTEN_HOST not in {"127.0.0.1", "localhost"}:  # noqa: F405
    raise ImproperlyConfigured("Django must listen only on a loopback address")
require_resend_backend(EMAIL_BACKEND)  # noqa: F405
require_resend_api_key(RESEND_API_KEY)  # noqa: F405
require_verified_sender(DEFAULT_FROM_EMAIL)  # noqa: F405
require_https_public_url(APP_PUBLIC_URL)  # noqa: F405
require_dedicated_reset_pepper(
    reset_pepper=PASSWORD_RESET_TOKEN_PEPPER,  # noqa: F405
    other_secrets={
        "REFRESH_TOKEN_PEPPER": REFRESH_TOKEN_PEPPER,  # noqa: F405
        "MFA_TOKEN_PEPPER": MFA_TOKEN_PEPPER,  # noqa: F405
        "MFA_ENCRYPTION_KEY": MFA_ENCRYPTION_KEY,  # noqa: F405
        "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": FINANCE_IDEMPOTENCY_ENCRYPTION_KEY,  # noqa: F405
        "INVITATION_TOKEN_PEPPER": INVITATION_TOKEN_PEPPER,  # noqa: F405
    },
)
require_reset_ttl_seconds(PASSWORD_RESET_TTL_SECONDS)  # noqa: F405
require_scheduler_commands(PRODUCTION_SCHEDULER_COMMANDS)
require_https_endpoint(MINIO_ENDPOINT_URL, name="MINIO_ENDPOINT_URL")  # noqa: F405
require_https_endpoint(MINIO_PUBLIC_URL, name="MINIO_PUBLIC_URL")  # noqa: F405
require_aws_singapore_region(MINIO_REGION)  # noqa: F405
require_allowlisted_value(MINIO_BUCKET, OBJECT_STORE_BUCKET_ALLOWLIST, name="MINIO_BUCKET")  # noqa: F405
require_allowlisted_value(
    MINIO_PUBLIC_URL.rstrip("/"),  # noqa: F405
    OBJECT_STORE_ORIGIN_ALLOWLIST,  # noqa: F405
    name="MINIO_PUBLIC_URL",
)
if MINIO_ADDRESSING_STYLE != "virtual":  # noqa: F405
    raise ImproperlyConfigured("Production object storage must use virtual-hosted addressing")
require_private_clamav_host(CLAMAV_HOST)  # noqa: F405
if CLAMAV_PORT != 3310:  # noqa: F405
    raise ImproperlyConfigured("Production ClamAV must use the private daemon port 3310")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
