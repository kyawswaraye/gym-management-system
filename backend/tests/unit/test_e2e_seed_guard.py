import pytest
from django.core.management.base import CommandError

from apps.tenants.e2e import assert_e2e_seed_allowed


def test_seed_allows_only_the_dedicated_e2e_database() -> None:
    assert_e2e_seed_allowed(
        database_name="gym_platform_e2e",
        settings_module="config.settings.e2e",
        seed_flag="1",
    )


def test_seed_refuses_the_application_database() -> None:
    with pytest.raises(CommandError, match="gym_platform_e2e"):
        assert_e2e_seed_allowed(
            database_name="gym_platform",
            settings_module="config.settings.e2e",
            seed_flag="1",
        )


def test_seed_refuses_to_run_without_the_explicit_flag() -> None:
    with pytest.raises(CommandError, match="E2E_SEED"):
        assert_e2e_seed_allowed(
            database_name="gym_platform_e2e",
            settings_module="config.settings.e2e",
            seed_flag="",
        )


def test_seed_refuses_local_and_production_settings() -> None:
    with pytest.raises(CommandError, match=r"config\.settings\.e2e"):
        assert_e2e_seed_allowed(
            database_name="gym_platform_e2e",
            settings_module="config.settings.local",
            seed_flag="1",
        )
