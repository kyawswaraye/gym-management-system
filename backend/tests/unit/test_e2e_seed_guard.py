import inspect

import pytest
from django.core.management.base import CommandError

from apps.tenants.e2e import assert_e2e_seed_allowed
from apps.tenants.e2e_runtime import e2e_reuses_storage_keys
from apps.tenants.management.commands.seed_e2e import Command, _delete_gym_tree


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


def test_seed_refuses_reused_object_keys_and_drops_members_first() -> None:
    assert e2e_reuses_storage_keys() is False
    assert "e2e_reuses_storage_keys" in inspect.getsource(Command.handle)
    source = inspect.getsource(_delete_gym_tree)
    assert source.index("CheckIn.objects.filter") < source.index("Member.objects.filter")
    assert source.index("CheckIn.objects.filter") < source.index("Membership.objects.filter")
    assert source.index("CheckIn.objects.filter") < source.index("CheckInDevice.objects.filter")
    assert source.index("CheckInChallenge.objects.filter") < source.index(
        "CheckInDevice.objects.filter"
    )
    assert source.index("CheckInDevice.objects.filter") < source.index(
        "GymMembership.objects.filter"
    )
    handle = inspect.getsource(Command.handle)
    assert "E2E_MEMBER_PASSWORD" in handle
    assert "e2e-member@example.test" not in handle
    assert source.index("Member.objects.filter") < source.index("FileAsset.objects.filter")


def test_seed_refuses_local_and_production_settings() -> None:
    with pytest.raises(CommandError, match=r"config\.settings\.e2e"):
        assert_e2e_seed_allowed(
            database_name="gym_platform_e2e",
            settings_module="config.settings.local",
            seed_flag="1",
        )
