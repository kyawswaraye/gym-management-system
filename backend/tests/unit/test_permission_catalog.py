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


def test_historical_catalog_omits_check_in_challenge_issue() -> None:
    assert "checkins.challenge.issue" not in seed.PERMISSION_CODES
    for key in ("OWNER", "MANAGER", "RECEPTIONIST", "ACCOUNTANT", "TRAINER"):
        assert "checkins.challenge.issue" not in seed.TEMPLATES[key]


def test_historical_catalog_omits_retail_approve() -> None:
    assert "retail.approve" not in seed.PERMISSION_CODES
    assert "retail.approve" not in seed.OWNER
    assert "retail.approve" not in seed.MANAGER


def test_platform_permissions_are_not_tenant_owner_permissions() -> None:
    assert not {code for code in seed.OWNER if code.startswith("platform.")}


def test_historical_catalog_omits_phase4_finance_codes() -> None:
    finance = importlib.import_module("apps.tenants.migrations.0025_finance_permission_catalog")
    assert "retail.approve" not in finance.TEMPLATE_GRANTS["OWNER"]
    assert "expense_categories.manage" in finance.TEMPLATE_GRANTS["OWNER"]
    assert "expense_categories.manage" in finance.TEMPLATE_GRANTS["MANAGER"]
    assert "expense_categories.manage" in finance.TEMPLATE_GRANTS["ACCOUNTANT"]
    assert "expense_categories.manage" not in finance.TEMPLATE_GRANTS["RECEPTIONIST"]
    assert "TRAINER" not in finance.TEMPLATE_GRANTS
    assert "inventory.adjust" not in finance.TEMPLATE_GRANTS["RECEPTIONIST"]
    assert "inventory.manage" not in finance.TEMPLATE_GRANTS["ACCOUNTANT"]
    assert "retail.void" in finance.TEMPLATE_GRANTS["ACCOUNTANT"]
    for code, *_rest in finance.NEW_PERMISSIONS:
        assert code not in seed.PERMISSION_CODES
        assert not code.startswith("expenses.")
        assert code != "retail.approve"


def test_historical_catalog_retains_retired_platform_settings_code() -> None:
    assert "platform.settings.manage" in seed.PERMISSION_CODES
    assert "platform.settings.manage" not in seed.OWNER
    assert "platform.settings.manage" not in seed.MANAGER
