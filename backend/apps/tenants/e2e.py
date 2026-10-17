from __future__ import annotations

from django.core.management.base import CommandError

E2E_DATABASE_NAME = "gym_platform_e2e"
E2E_SETTINGS_MODULE = "config.settings.e2e"
PLATFORM_EMAIL = "e2e-platform@example.test"
OWNER_EMAIL = "e2e-owner@example.test"
MEMBER_EMAIL = "e2e-member@example.test"
SEEDED_GYM_CODE = "E2ESEED"
SEEDED_GYM_NAME = "E2E Seed Gym"
GYM_CODE_PREFIX = "E2E"
USER_EMAIL_PREFIX = "e2e-"
USER_EMAIL_SUFFIX = "@example.test"


def assert_e2e_seed_allowed(
    *,
    database_name: str,
    settings_module: str,
    seed_flag: str,
) -> None:
    """Refuse to seed anything except the dedicated disposable E2E database."""

    if seed_flag != "1":
        raise CommandError("Set E2E_SEED=1 to run the E2E seed command.")
    if settings_module != E2E_SETTINGS_MODULE:
        raise CommandError("seed_e2e requires DJANGO_SETTINGS_MODULE=config.settings.e2e.")
    if database_name != E2E_DATABASE_NAME:
        raise CommandError("seed_e2e only runs against the dedicated gym_platform_e2e database.")
