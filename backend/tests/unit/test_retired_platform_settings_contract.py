from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
from collections.abc import Iterator

from django.db import migrations
from django.urls import URLPattern, URLResolver, get_resolver

RETIRED_CODE = "platform.settings.manage"
PERMISSION_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
PERMISSION_ATTRIBUTES = ("required_permission", "required_permissions", "action_permissions")


def test_retirement_migration_is_irreversible() -> None:
    migration = importlib.import_module(
        "apps.tenants.migrations.0020_retire_platform_settings_manage"
    )

    assert migration.RETIRED_CODE == RETIRED_CODE
    assert "retired" in migration.RETIRED_DESCRIPTION.lower()
    assert "no approved http route" in migration.RETIRED_DESCRIPTION.lower()
    assert len(migration.RETIRED_DESCRIPTION) <= 255
    assert migration.Migration.dependencies == [
        ("tenants", "0019_role_permission_null_template_guard"),
    ]
    assert len(migration.Migration.operations) == 1
    operation = migration.Migration.operations[0]
    assert operation.code is migration.retire_platform_settings_manage
    assert operation.reverse_code is migrations.RunPython.noop


def test_retirement_function_is_idempotent_and_tolerates_a_missing_row() -> None:
    migration = importlib.import_module(
        "apps.tenants.migrations.0020_retire_platform_settings_manage"
    )
    rows = _PermissionRows()
    apps = _Apps(rows)

    migration.retire_platform_settings_manage(apps, None)
    migration.retire_platform_settings_manage(apps, None)
    rows.present = False
    migration.retire_platform_settings_manage(apps, None)
    migrations.RunPython.noop(apps, None)

    assert rows.filters == [{"code": RETIRED_CODE}] * 3
    assert rows.updates == [{"is_active": False, "description": migration.RETIRED_DESCRIPTION}] * 3
    assert rows.created == []


def test_registered_routes_do_not_declare_retired_platform_settings_permission() -> None:
    declared = set()
    views = list(_registered_views())
    assert views
    for view in views:
        declared.update(_declared_permission_codes(view))

    assert RETIRED_CODE not in declared
    assert {
        "platform.dashboard.view",
        "platform.gyms.view",
        "platform.subscriptions.manage",
        "platform.audit.view",
        "settings.manage",
    } <= declared
    assert not any("admin/settings" in path or "platform/settings" in path for path in _paths())


class _PermissionRows:
    def __init__(self) -> None:
        self.present = True
        self.filters: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []
        self.created: list[object] = []

    def filter(self, **kwargs: object) -> _PermissionRows:
        self.filters.append(kwargs)
        return self

    def update(self, **kwargs: object) -> int:
        self.updates.append(kwargs)
        return 1 if self.present else 0

    def create(self, **kwargs: object) -> object:
        self.created.append(kwargs)
        return kwargs


class _PermissionModel:
    def __init__(self, rows: _PermissionRows) -> None:
        self.objects = rows


class _Apps:
    def __init__(self, rows: _PermissionRows) -> None:
        self._model = _PermissionModel(rows)

    def get_model(self, app_label: str, model_name: str) -> _PermissionModel:
        assert (app_label, model_name) == ("tenants", "Permission")
        return self._model


def _registered_views() -> Iterator[type]:
    seen: set[type] = set()

    def walk(patterns: list[URLPattern | URLResolver]) -> Iterator[type]:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                yield from walk(pattern.url_patterns)
                continue
            view = getattr(pattern.callback, "view_class", None)
            if isinstance(view, type) and view not in seen:
                seen.add(view)
                yield view

    yield from walk(get_resolver().url_patterns)


def _paths() -> list[str]:
    found: list[str] = []

    def walk(patterns: list[URLPattern | URLResolver], prefix: str) -> None:
        for pattern in patterns:
            path = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, path)
            else:
                found.append(path)

    walk(get_resolver().url_patterns, "")
    return found


def _declared_permission_codes(view: type) -> set[str]:
    found: set[str] = set()
    for cls in view.__mro__:
        for name in PERMISSION_ATTRIBUTES:
            if name not in cls.__dict__:
                continue
            value = cls.__dict__[name]
            if isinstance(value, property):
                if value.fget is not None:
                    found.update(_permission_literals(inspect.getsource(value.fget)))
                continue
            _collect(value, found)
        method = cls.__dict__.get("get_permissions")
        if callable(method):
            found.update(_permission_literals(inspect.getsource(method)))
    return found


def _collect(value: object, found: set[str]) -> None:
    if isinstance(value, str):
        if PERMISSION_CODE.fullmatch(value):
            found.add(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect(item, found)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _collect(item, found)


def _permission_literals(source: str) -> set[str]:
    tree = ast.parse(textwrap.dedent(source))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and PERMISSION_CODE.fullmatch(node.value)
    }
