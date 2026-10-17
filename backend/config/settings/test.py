import os

from .base import *  # noqa: F403

DEBUG = False
# MD5 keeps ordinary password setup fast. PBKDF2 stays installed so tests can
# verify invitation passwords, which the application always stores with it.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
BFF_SHARED_SECRET = os.environ.get("BFF_SHARED_SECRET", "test-bff-shared-secret-32-chars-min")
MFA_TOKEN_PEPPER = "test-mfa-token-pepper"
MFA_ENCRYPTION_KEY = "test-mfa-encryption-key"
INVITATION_TOKEN_PEPPER = "test-invitation-token-pepper"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Disposable verification uses a CREATEDB role, typically gym_admin. Runtime
# RLS proofs still SET LOCAL ROLE gym_runtime inside PostgreSQL tests.
if os.environ.get("TEST_DB_USER"):
    DATABASES["default"]["USER"] = os.environ["TEST_DB_USER"]  # noqa: F405
    DATABASES["default"]["PASSWORD"] = os.environ.get("TEST_DB_PASSWORD", "")  # noqa: F405
