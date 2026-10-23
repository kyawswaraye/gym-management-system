from django.core.exceptions import ImproperlyConfigured

from apps.common.production import (
    PRODUCTION_SCHEDULER_COMMANDS,
    require_allowlisted_value,
    require_aws_singapore_region,
    require_dedicated_reset_pepper,
    require_https_endpoint,
    require_https_public_url,
    require_private_clamav_host,
    require_push_backend,
    require_resend_api_key,
    require_resend_backend,
    require_reset_ttl_seconds,
    require_scheduler_commands,
    require_verified_sender,
)

from .base import *  # noqa: F403

MIDDLEWARE = [
    *MIDDLEWARE,  # noqa: F405
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

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
require_push_backend(
    backend=PUSH_BACKEND,  # noqa: F405
    project_id=FCM_PROJECT_ID,  # noqa: F405
    service_account_json=FCM_SERVICE_ACCOUNT_JSON,  # noqa: F405
    encryption_key=PUSH_TOKEN_ENCRYPTION_KEY,  # noqa: F405
    fingerprint_pepper=PUSH_TOKEN_FINGERPRINT_PEPPER,  # noqa: F405
    other_secrets={
        "MFA_ENCRYPTION_KEY": MFA_ENCRYPTION_KEY,  # noqa: F405
        "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": FINANCE_IDEMPOTENCY_ENCRYPTION_KEY,  # noqa: F405
        "PASSWORD_RESET_TOKEN_PEPPER": PASSWORD_RESET_TOKEN_PEPPER,  # noqa: F405
    },
)
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

# TLS ends at Cloudflare and Caddy. The private BFF hop is HTTP and marks
# that original browser request with this header. The header is trustworthy
# only while Compose publishes no API port. It does not replace that isolation.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
STRUCTURED_REQUEST_LOG = True
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.common.observability.logging.JsonLogFormatter"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        },
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        "django.request": {
            "handlers": ["stdout"],
            "level": "CRITICAL",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["stdout"],
            "level": "CRITICAL",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["stdout"],
            "level": "WARNING",
            "propagate": False,
        },
        "gym.http": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "apps.audit": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}
