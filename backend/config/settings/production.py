from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

required = {
    "DJANGO_SECRET_KEY": SECRET_KEY,  # noqa: F405
    "RUNTIME_DB_PASSWORD": DATABASES["default"]["PASSWORD"],  # noqa: F405
    "JWT_KEY_ID": JWT_KEY_ID,  # noqa: F405
    "JWT_PRIVATE_KEY": JWT_PRIVATE_KEY,  # noqa: F405
    "JWT_PUBLIC_KEYS": JWT_PUBLIC_KEYS,  # noqa: F405
    "REFRESH_TOKEN_PEPPER": REFRESH_TOKEN_PEPPER,  # noqa: F405
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

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
