"""Loopback browser-smoke settings.

This module is selected only by the E2E scripts. It does not skip MFA, relax
the BFF secret, or change password hashing. Mail is kept in memory so the real
owner-invitation send can succeed without printing the acceptance token.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def _present(value: object, minimum: int) -> bool:
    if isinstance(value, str):
        return len(value.strip()) >= minimum and value != "unsafe-local-only"
    return bool(value)


required: dict[str, tuple[object, int]] = {
    "DJANGO_SECRET_KEY": (SECRET_KEY, 50),  # noqa: F405
    "database password": (DATABASES["default"]["PASSWORD"], 1),  # noqa: F405
    "JWT_KEY_ID": (JWT_KEY_ID, 1),  # noqa: F405
    "JWT_PRIVATE_KEY": (JWT_PRIVATE_KEY, 1),  # noqa: F405
    "JWT_PUBLIC_KEYS": (JWT_PUBLIC_KEYS, 1),  # noqa: F405
    "REFRESH_TOKEN_PEPPER": (REFRESH_TOKEN_PEPPER, 16),  # noqa: F405
    "MFA_TOKEN_PEPPER": (MFA_TOKEN_PEPPER, 16),  # noqa: F405
    "MFA_ENCRYPTION_KEY": (MFA_ENCRYPTION_KEY, 16),  # noqa: F405
    "FINANCE_IDEMPOTENCY_ENCRYPTION_KEY": (FINANCE_IDEMPOTENCY_ENCRYPTION_KEY, 16),  # noqa: F405
    "INVITATION_TOKEN_PEPPER": (INVITATION_TOKEN_PEPPER, 16),  # noqa: F405
    "BFF_SHARED_SECRET": (BFF_SHARED_SECRET, 32),  # noqa: F405
}
missing = [name for name, (value, minimum) in required.items() if not _present(value, minimum)]
if missing:
    raise ImproperlyConfigured("Missing E2E configuration: " + ", ".join(missing))
if MFA_ENCRYPTION_KEY == FINANCE_IDEMPOTENCY_ENCRYPTION_KEY:  # noqa: F405
    raise ImproperlyConfigured("Finance idempotency encryption must not reuse the MFA key")
if DJANGO_LISTEN_HOST not in {"127.0.0.1", "localhost"}:  # noqa: F405
    raise ImproperlyConfigured("Django must listen only on a loopback address")
