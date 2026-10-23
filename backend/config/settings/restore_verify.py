"""Fail-closed settings for a disposable restore verification process.

The process needs a superuser connection to the restored database. It does not
load JWT, mail, push, object-storage, ClamAV, or application backup secrets.
"""

import os
import re

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if os.environ.get("DATABASE_ROLE_MODE", "").upper() != "RESTORE_VERIFY":
    raise ImproperlyConfigured("Restore verification requires DATABASE_ROLE_MODE=RESTORE_VERIFY")
if os.environ.get("RESTORE_TARGET") != "disposable":
    raise ImproperlyConfigured("Restore verification requires a disposable target")
if os.environ.get("RESTORE_CONFIRMATION") != "restore-disposable":
    raise ImproperlyConfigured("Restore verification requires the disposable confirmation")
_project = os.environ.get("RESTORE_PROJECT", "")
if re.fullmatch(r"gym-restore-[a-z0-9]{8,32}", _project) is None or "production" in _project:
    raise ImproperlyConfigured("Restore verification requires a disposable project")
if os.environ.get("RESTORE_DB_HOST") != "postgres":
    raise ImproperlyConfigured("Restore verification must use the disposable postgres service")
_user = os.environ.get("RESTORE_DB_USER", "")
if _user in {"", "gym_runtime", "gym_migration", "gym_auth_bootstrap"}:
    raise ImproperlyConfigured("Restore verification user must be the disposable superuser")
_password = os.environ.get("RESTORE_DB_PASSWORD", "")
if _password in {"", "replace-from-secret-store"}:
    raise ImproperlyConfigured("RESTORE_DB_PASSWORD is required")
if os.environ.get("DB_NAME", "gym_platform") != "gym_platform":
    raise ImproperlyConfigured("Restore verification database must be gym_platform")
for _name in (
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_SESSION_TOKEN",
    "JWT_PRIVATE_KEY",
    "RESEND_API_KEY",
    "FCM_SERVICE_ACCOUNT_JSON",
    "BFF_SHARED_SECRET",
    "BACKUP_REPO_CIPHER_PASS",
    "BACKUP_S3_ACCESS_KEY",
    "BACKUP_S3_SECRET_KEY",
    "RUNTIME_DB_PASSWORD",
    "MIGRATION_DB_PASSWORD",
    "POSTGRES_ADMIN_PASSWORD",
):
    if os.environ.get(_name):
        raise ImproperlyConfigured(f"restore verification must not receive {_name}")
if not SECRET_KEY or SECRET_KEY == "unsafe-local-only":  # noqa: F405
    raise ImproperlyConfigured("a disposable DJANGO_SECRET_KEY is required")

DATABASES["default"]["NAME"] = "gym_platform"  # noqa: F405
DATABASES["default"]["USER"] = _user  # noqa: F405
DATABASES["default"]["PASSWORD"] = _password  # noqa: F405
DATABASES["default"]["HOST"] = "postgres"  # noqa: F405
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
DATABASES["default"]["OPTIONS"] = {  # noqa: F405
    "connect_timeout": 5,
    "options": "-c default_transaction_read_only=on",
}
