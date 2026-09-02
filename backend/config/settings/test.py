import os

from .base import *  # noqa: F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
BFF_SHARED_SECRET = os.environ.get("BFF_SHARED_SECRET", "test-bff-shared-secret-32-chars-min")

# Disposable verification uses a CREATEDB role, typically gym_admin. Runtime
# RLS proofs still SET LOCAL ROLE gym_runtime inside PostgreSQL tests.
if os.environ.get("TEST_DB_USER"):
    DATABASES["default"]["USER"] = os.environ["TEST_DB_USER"]  # noqa: F405
    DATABASES["default"]["PASSWORD"] = os.environ.get("TEST_DB_PASSWORD", "")  # noqa: F405
