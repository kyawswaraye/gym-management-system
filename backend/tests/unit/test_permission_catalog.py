from __future__ import annotations

import importlib

seed = importlib.import_module("apps.tenants.migrations.0003_seed_authorization")


def test_permission_codes_are_unique() -> None:
    assert len(seed.PERMISSION_CODES) == len(set(seed.PERMISSION_CODES))


def test_reception_cash_grants_are_conditional_baseline_only() -> None:
    reception = set(seed.RECEPTIONIST)
    assert {"cashbook.view", "cashbook.open", "cashbook.reconcile"} <= reception
    assert "cashbook.adjust" not in reception
    assert (
        not {
            "payments.approve",
            "expenses.approve",
            "salaries.approve",
            "pt.sales.approve",
        }
        & reception
    )


def test_historical_catalog_omits_retail_approve() -> None:
    assert "retail.approve" not in seed.PERMISSION_CODES
    assert "retail.approve" not in seed.OWNER
    assert "retail.approve" not in seed.MANAGER


def test_platform_permissions_are_not_tenant_owner_permissions() -> None:
    assert not {code for code in seed.OWNER if code.startswith("platform.")}


def test_historical_catalog_retains_retired_platform_settings_code() -> None:
    assert "platform.settings.manage" in seed.PERMISSION_CODES
    assert "platform.settings.manage" not in seed.OWNER
    assert "platform.settings.manage" not in seed.MANAGER
