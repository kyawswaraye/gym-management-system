from __future__ import annotations

import importlib
import inspect

from apps.tenants.delegation import HARD_RESERVED_APPROVALS

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


def test_historical_catalog_omits_pt_schedule_codes() -> None:
    schedule = importlib.import_module(
        "apps.tenants.migrations.0026_pt_schedule_permission_catalog"
    )
    for code, *_rest in schedule.NEW_PERMISSIONS:
        assert code not in seed.PERMISSION_CODES
    assert schedule.TEMPLATE_GRANTS["OWNER"] == (
        "pt.schedule.view",
        "pt.schedule.manage",
        "pt.sessions.adjust",
    )
    assert schedule.TEMPLATE_GRANTS["MANAGER"] == schedule.TEMPLATE_GRANTS["OWNER"]
    assert schedule.TEMPLATE_GRANTS["RECEPTIONIST"] == (
        "pt.schedule.view",
        "pt.schedule.manage",
    )
    assert schedule.TEMPLATE_GRANTS["ACCOUNTANT"] == ("pt.schedule.view",)
    assert schedule.TEMPLATE_GRANTS["TRAINER"] == (
        "pt.schedule.view",
        "pt.schedule.manage",
    )
    assert "pt.sessions.adjust" not in schedule.TEMPLATE_GRANTS["RECEPTIONIST"]
    assert "pt.sessions.adjust" not in schedule.TEMPLATE_GRANTS["ACCOUNTANT"]
    assert "pt.sessions.adjust" not in schedule.TEMPLATE_GRANTS["TRAINER"]
    assert "pt.schedule.manage" not in schedule.TEMPLATE_GRANTS["ACCOUNTANT"]


def test_class_permissions_follow_the_matrix_and_stay_out_of_the_historical_seed() -> None:
    catalog = importlib.import_module("apps.tenants.migrations.0028_class_permission_catalog")
    for code, *_rest in catalog.NEW_PERMISSIONS:
        assert code not in seed.PERMISSION_CODES
    assert catalog.TEMPLATE_GRANTS["OWNER"] == (
        "classes.view",
        "classes.manage",
        "classes.book",
        "classes.attendance",
    )
    assert catalog.TEMPLATE_GRANTS["MANAGER"] == catalog.TEMPLATE_GRANTS["OWNER"]
    assert catalog.TEMPLATE_GRANTS["RECEPTIONIST"] == catalog.TEMPLATE_GRANTS["OWNER"]
    assert catalog.TEMPLATE_GRANTS["ACCOUNTANT"] == ("classes.view",)
    assert catalog.TEMPLATE_GRANTS["TRAINER"] == catalog.TEMPLATE_GRANTS["OWNER"]
    assert "classes.manage" not in catalog.TEMPLATE_GRANTS["ACCOUNTANT"]


def test_announcement_permissions_follow_the_matrix() -> None:
    catalog = importlib.import_module(
        "apps.tenants.migrations.0030_announcement_permission_catalog"
    )
    for code, *_rest in catalog.NEW_PERMISSIONS:
        assert code not in seed.PERMISSION_CODES
    assert catalog.TEMPLATE_GRANTS["OWNER"] == ("announcements.view", "announcements.manage")
    assert catalog.TEMPLATE_GRANTS["MANAGER"] == catalog.TEMPLATE_GRANTS["OWNER"]
    assert catalog.TEMPLATE_GRANTS["RECEPTIONIST"] == ("announcements.view",)
    assert catalog.TEMPLATE_GRANTS["ACCOUNTANT"] == ("announcements.view",)
    assert catalog.TEMPLATE_GRANTS["TRAINER"] == ("announcements.view",)
    assert "announcements.manage" not in catalog.TEMPLATE_GRANTS["RECEPTIONIST"]


def test_pt_commission_pay_is_hard_reserved_after_the_historical_grant() -> None:
    reservation = importlib.import_module(
        "apps.tenants.migrations.0027_pt_commission_pay_reservation"
    )
    assert "pt.commissions.pay" in seed.ACCOUNTANT
    assert "pt.commissions.pay" in seed.OWNER
    assert "pt.commissions.pay" in seed.MANAGER
    assert "pt.commissions.pay" not in seed.RECEPTIONIST
    assert "pt.commissions.pay" not in seed.TRAINER
    assert "pt.commissions.pay" in HARD_RESERVED_APPROVALS
    assert reservation.FORWARD_SQL.count("'pt.commissions.pay'") == 2
    assert "'pt.commissions.pay'" not in reservation.REVERSE_SQL
    source = inspect.getsource(reservation.strip_unsafe_pt_commission_pay)
    assert 'role__key="OWNER"' in source
    assert 'role__source_template__key="OWNER"' in source
    assert 'role__key="MANAGER"' in source
    assert 'role__source_template__key="MANAGER"' in source
    assert "ACCOUNTANT" in source


def test_historical_catalog_retains_retired_platform_settings_code() -> None:
    assert "platform.settings.manage" in seed.PERMISSION_CODES
    assert "platform.settings.manage" not in seed.OWNER
    assert "platform.settings.manage" not in seed.MANAGER
