"""Role precedence for a gym that has one person covering several desks.

These checks do not open a database. They pin the six role shapes: owner,
manager plus receptionist, manager only, receptionist only, trainer only, and
manager plus receptionist plus trainer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from django.http import Http404
from rest_framework.exceptions import PermissionDenied

from apps.cashbook import selectors as cashbook_selectors
from apps.expenses import services as expense_services
from apps.salaries import selectors as salary_selectors
from apps.salaries import services as salary_services
from apps.salaries.models import SalaryPeriod
from apps.sales import selectors as sale_selectors
from apps.sales.models import RetailSale
from apps.trainers import package_services, pt_commission_services, pt_sale_services

_OWNER = frozenset({"OWNER"})
_DESK = frozenset({"MANAGER", "RECEPTIONIST"})
_DESK_TRAINER = frozenset({"MANAGER", "RECEPTIONIST", "TRAINER"})
_MANAGER = frozenset({"MANAGER"})
_MANAGER_TRAINER = frozenset({"MANAGER", "TRAINER"})
_RECEPTION = frozenset({"RECEPTIONIST"})
_TRAINER = frozenset({"TRAINER"})
_OWNER_TRAINER = frozenset({"OWNER", "TRAINER"})


def _bind(monkeypatch: pytest.MonkeyPatch, module: object, held: frozenset[str]) -> None:
    monkeypatch.setattr(
        module,
        "holds_protected_role",
        lambda *, gym_id, membership_id, role_key: role_key in held,
    )


def _pair() -> tuple[SimpleNamespace, SimpleNamespace]:
    return SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())


def test_reception_scope_yields_to_owner_and_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = {
        _OWNER: False,
        _DESK: False,
        _DESK_TRAINER: False,
        _MANAGER: False,
        _RECEPTION: True,
        _TRAINER: False,
        _OWNER_TRAINER: False,
        frozenset({"OWNER", "RECEPTIONIST"}): False,
    }
    for held, expected in cases.items():
        _bind(monkeypatch, cashbook_selectors, held)
        assert (
            cashbook_selectors.reception_record_scope_applies(gym_id=uuid4(), membership_id=uuid4())
            is expected
        )


def test_visible_sales_hide_posted_and_void_only_for_a_receptionist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym_id = uuid4()
    actor_id = uuid4()
    base = MagicMock()
    narrowed = MagicMock()
    hidden = MagicMock()
    base.filter.return_value = narrowed
    narrowed.exclude.return_value = hidden
    seen: dict[str, object] = {}

    def capture(**kwargs: object) -> MagicMock:
        seen.update(kwargs)
        return base

    monkeypatch.setattr(RetailSale.objects, "filter", capture)
    monkeypatch.setattr(sale_selectors, "reception_record_scope_applies", lambda **_: True)
    assert sale_selectors.visible_sales(gym_id=gym_id, actor_membership_id=actor_id) is hidden
    assert seen == {"gym_id": gym_id}
    narrowed.exclude.assert_called_once_with(
        status__in=(RetailSale.Status.POSTED, RetailSale.Status.VOID)
    )

    gym_wide = MagicMock()

    def capture_gym_wide(**kwargs: object) -> MagicMock:
        seen.update(kwargs)
        return gym_wide

    monkeypatch.setattr(RetailSale.objects, "filter", capture_gym_wide)
    monkeypatch.setattr(sale_selectors, "reception_record_scope_applies", lambda **_: False)
    other_gym = uuid4()
    assert sale_selectors.visible_sales(gym_id=other_gym, actor_membership_id=actor_id) is gym_wide
    assert seen["gym_id"] == other_gym
    gym_wide.filter.assert_not_called()
    gym_wide.exclude.assert_not_called()


def test_visible_periods_keep_the_same_posted_and_void_hide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gym_id = uuid4()
    base = MagicMock()
    narrowed = MagicMock()
    hidden = MagicMock()
    base.filter.return_value = narrowed
    narrowed.exclude.return_value = hidden

    def capture(**kwargs: object) -> MagicMock:
        assert kwargs == {"gym_id": gym_id}
        return base

    monkeypatch.setattr(SalaryPeriod.objects, "filter", capture)
    monkeypatch.setattr(salary_selectors, "reception_record_scope_applies", lambda **_: True)
    assert salary_selectors.visible_periods(gym_id=gym_id, actor_membership_id=uuid4()) is hidden
    narrowed.exclude.assert_called_once_with(
        status__in=(SalaryPeriod.Status.POSTED, SalaryPeriod.Status.VOID)
    )


def test_lock_visible_period_returns_posted_rows_to_owner_and_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id=uuid4())
    posted = SimpleNamespace(
        id=uuid4(),
        created_by_membership_id=uuid4(),
        submitted_by_membership_id=None,
        status=SalaryPeriod.Status.POSTED,
    )
    monkeypatch.setattr(salary_services, "_lock_period", lambda **_: posted)
    monkeypatch.setattr(salary_services, "reception_record_scope_applies", lambda **_: False)
    assert (
        salary_services._lock_visible_period(gym_id=uuid4(), period_id=posted.id, actor=actor)
        is posted
    )

    monkeypatch.setattr(salary_services, "reception_record_scope_applies", lambda **_: True)
    with pytest.raises(Http404):
        salary_services._lock_visible_period(gym_id=uuid4(), period_id=posted.id, actor=actor)

    own_posted = SimpleNamespace(
        id=uuid4(),
        created_by_membership_id=actor.id,
        submitted_by_membership_id=actor.id,
        status=SalaryPeriod.Status.VOID,
    )
    monkeypatch.setattr(salary_services, "_lock_period", lambda **_: own_posted)
    with pytest.raises(Http404):
        salary_services._lock_visible_period(gym_id=uuid4(), period_id=own_posted.id, actor=actor)


@pytest.mark.parametrize(
    ("held", "allowed"),
    [
        (_OWNER, True),
        (_OWNER_TRAINER, True),
        (_DESK, True),
        (_DESK_TRAINER, True),
        (_MANAGER, True),
        (_MANAGER_TRAINER, False),
        (_RECEPTION, False),
        (_TRAINER, False),
    ],
)
def test_package_adjuster_role_matrix(
    monkeypatch: pytest.MonkeyPatch, held: frozenset[str], allowed: bool
) -> None:
    _bind(monkeypatch, package_services, held)
    gym, actor = _pair()
    if allowed:
        package_services._assert_adjuster(gym=gym, actor=actor)
        return
    with pytest.raises(PermissionDenied):
        package_services._assert_adjuster(gym=gym, actor=actor)


@pytest.mark.parametrize(
    ("held", "allowed"),
    [
        (_OWNER, True),
        (_OWNER_TRAINER, True),
        (_DESK, True),
        (_DESK_TRAINER, True),
        (_MANAGER, True),
        (_MANAGER_TRAINER, False),
        (_RECEPTION, False),
        (_TRAINER, False),
    ],
)
def test_commission_payer_role_matrix(
    monkeypatch: pytest.MonkeyPatch, held: frozenset[str], allowed: bool
) -> None:
    _bind(monkeypatch, pt_commission_services, held)
    _gym, actor = _pair()
    if allowed:
        pt_commission_services._assert_payer(gym_id=_gym.id, actor=actor)
        return
    with pytest.raises(PermissionDenied):
        pt_commission_services._assert_payer(gym_id=_gym.id, actor=actor)


@pytest.mark.parametrize(
    ("held", "allowed"),
    [
        (_OWNER, True),
        (_OWNER_TRAINER, True),
        (_DESK, True),
        (_DESK_TRAINER, True),
        (_MANAGER, True),
        (_MANAGER_TRAINER, False),
        (_RECEPTION, False),
        (_TRAINER, False),
    ],
)
def test_commission_approver_role_matrix(
    monkeypatch: pytest.MonkeyPatch, held: frozenset[str], allowed: bool
) -> None:
    _bind(monkeypatch, expense_services, held)
    _gym, approver = _pair()
    kwargs = {
        "gym_id": _gym.id,
        "approver": approver,
        "created_by_membership_id": uuid4(),
        "submitted_by_membership_id": uuid4(),
    }
    if allowed:
        expense_services._assert_pt_commission_approver(**kwargs)
    else:
        with pytest.raises(PermissionDenied):
            expense_services._assert_pt_commission_approver(**kwargs)

    own = {**kwargs, "created_by_membership_id": approver.id}
    creator_may_post = "OWNER" in held or {"MANAGER", "RECEPTIONIST"} <= held
    if creator_may_post:
        expense_services._assert_pt_commission_approver(**own)
    else:
        with pytest.raises(PermissionDenied):
            expense_services._assert_pt_commission_approver(**own)


@pytest.mark.parametrize(
    ("held", "allowed"),
    [
        (_OWNER, True),
        (_OWNER_TRAINER, True),
        (_DESK, True),
        (_DESK_TRAINER, True),
        (_MANAGER, True),
        (_MANAGER_TRAINER, False),
        (_RECEPTION, False),
        (_TRAINER, False),
    ],
)
def test_pt_sale_approver_matches_the_screen(
    monkeypatch: pytest.MonkeyPatch, held: frozenset[str], allowed: bool
) -> None:
    _bind(monkeypatch, pt_sale_services, held)
    monkeypatch.setattr(pt_sale_services, "membership_has_permission", lambda **_: True)
    gym, actor = _pair()
    if allowed:
        pt_sale_services._assert_approver(gym=gym, actor=actor)
    else:
        with pytest.raises(PermissionDenied):
            pt_sale_services._assert_approver(gym=gym, actor=actor)

    monkeypatch.setattr(pt_sale_services, "membership_has_permission", lambda **_: False)
    with pytest.raises(PermissionDenied):
        pt_sale_services._assert_approver(gym=gym, actor=actor)
