"""Disposable staging-rehearsal settings.

This module is selected only by the staging Compose overlay. It keeps the
runtime database role, private ClamAV host, and TLS-behind-Caddy flags. Mail
and push stay on non-delivery backends. The object store is the private
rehearsal service. It is not the production settings module and it does not
prove a provider, a deploy, or an offsite backup.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from apps.common.production import require_private_clamav_host

from .base import *  # noqa: F403

STAGING_REHEARSAL = True
PRODUCTION_DEPLOY_PROVEN = False

if os.environ.get("STAGING_REHEARSAL") != "1":
    raise ImproperlyConfigured("Staging rehearsal settings require STAGING_REHEARSAL=1")
if os.environ.get("DATABASE_ROLE_MODE", "runtime").upper() != "RUNTIME":
    raise ImproperlyConfigured("Staging rehearsal API settings require the runtime role")
if DATABASES["default"]["USER"] != "gym_runtime":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal API settings require gym_runtime")
if not SECRET_KEY or SECRET_KEY == "unsafe-local-only":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
if not BFF_SHARED_SECRET or len(BFF_SHARED_SECRET) < 32:  # noqa: F405
    raise ImproperlyConfigured("BFF_SHARED_SECRET must be at least 32 characters")

_LOCMAIL = "django.core.mail.backends.locmem.EmailBackend"
if EMAIL_BACKEND != _LOCMAIL:  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal mail must be the non-delivery locmem backend")
if PUSH_BACKEND != "locmem":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal push must be the non-delivery locmem backend")

_OBJECT_ORIGIN = "http://object-store:9000"
if MINIO_ENDPOINT_URL != _OBJECT_ORIGIN or MINIO_PUBLIC_URL != _OBJECT_ORIGIN:  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal object storage must use the private service")
if MINIO_ADDRESSING_STYLE != "path":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal object storage must use path-style requests")
if MINIO_REGION != "us-east-1":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal object region is the local service region")
if MINIO_BUCKET != "gym-staging-rehearsal-app":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal object bucket is not the rehearsal bucket")
require_private_clamav_host(CLAMAV_HOST)  # noqa: F405
if CLAMAV_HOST != "clamav" or CLAMAV_PORT != 3310:  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal ClamAV must be the private daemon")
if APP_PUBLIC_URL != "https://localhost:8443":  # noqa: F405
    raise ImproperlyConfigured("Staging rehearsal public URL must be the loopback TLS origin")


def _pem_file(name: str) -> str:
    raw = os.environ.get(name, "")
    path = Path(raw)
    if not raw or path.is_symlink() or not path.is_file():
        raise ImproperlyConfigured(f"{name} must be a regular rehearsal file")
    text = path.read_text(encoding="utf-8")
    if "BEGIN " not in text or len(text) > 16384:
        raise ImproperlyConfigured(f"{name} is not a rehearsal key file")
    return text


_private = _pem_file("JWT_PRIVATE_KEY_FILE")
_public = _pem_file("JWT_PUBLIC_KEYS_FILE")
if not JWT_KEY_ID or JWT_KEY_ID == "file-backed":  # noqa: F405
    raise ImproperlyConfigured("JWT_KEY_ID is required")
JWT_PRIVATE_KEY = _private
JWT_PUBLIC_KEYS = {JWT_KEY_ID: _public}  # noqa: F405

_distinct = {
    "DJANGO_SECRET_KEY": SECRET_KEY,  # noqa: F405
    "RUNTIME_DB_PASSWORD": DATABASES["default"]["PASSWORD"],  # noqa: F405
    "REFRESH_TOKEN_PEPPER": REFRESH_TOKEN_PEPPER,  # noqa: F405
    "PASSWORD_RESET_TOKEN_PEPPER": PASSWORD_RESET_TOKEN_PEPPER,  # noqa: F405
    "MFA_TOKEN_PEPPER": MFA_TOKEN_PEPPER,  # noqa: F405
    "MFA_ENCRYPTION_KEY": MFA_ENCRYPTION_KEY,  # noqa: F405
    "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": FINANCE_IDEMPOTENCY_ENCRYPTION_KEY,  # noqa: F405
    "INVITATION_TOKEN_PEPPER": INVITATION_TOKEN_PEPPER,  # noqa: F405
    "BFF_SHARED_SECRET": BFF_SHARED_SECRET,  # noqa: F405
    "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,  # noqa: F405
    "MINIO_SECRET_KEY": MINIO_SECRET_KEY,  # noqa: F405
}
_seen: set[str] = set()
for _name, _value in _distinct.items():
    if not isinstance(_value, str) or len(_value) < 32 or _value in _seen:
        raise ImproperlyConfigured("Staging rehearsal secrets must be distinct and high entropy")
    _seen.add(_value)

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
