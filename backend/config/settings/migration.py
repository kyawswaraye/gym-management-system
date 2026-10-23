"""Fail-closed settings for the one-shot production migration process.

Schema migration needs the migration database identity and Django's secret.
It does not load JWT, mail, push, object-storage, ClamAV, or BFF secrets.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

if os.environ.get("DATABASE_ROLE_MODE", "").upper() != "MIGRATION":
    raise ImproperlyConfigured("Migration settings require DATABASE_ROLE_MODE=MIGRATION")
if DATABASES["default"]["USER"] != "gym_migration":  # noqa: F405
    raise ImproperlyConfigured("Migration settings require the gym_migration database identity")
if not DATABASES["default"]["PASSWORD"]:  # noqa: F405
    raise ImproperlyConfigured("MIGRATION_DB_PASSWORD is required")
if not SECRET_KEY or SECRET_KEY == "unsafe-local-only":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required")
