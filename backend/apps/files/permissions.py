"""Purpose-aware gates for the shared private file routes.

Profile photos keep the view's ``required_permissions`` pair. Expense receipts
use the operation's file permission and leave ``expenses.create`` or
``expenses.update`` to the service. Receipt delete does not require
``files.delete``, so the accountant template is not given a generic file-delete
grant.
"""

from __future__ import annotations

from uuid import UUID

from django.http import Http404
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.files.constants import EXPENSE_RECEIPT_PURPOSE, PROFILE_PHOTO_PURPOSE
from apps.files.models import FileAsset

_OPERATIONS = frozenset({"upload", "complete", "download", "delete"})


def _granted(request: Request) -> frozenset[str] | set[str] | None:
    granted: object = getattr(request, "permission_codes", frozenset())
    if isinstance(granted, str) or not isinstance(granted, frozenset | set):
        return None
    return granted


def _profile_codes(view: APIView) -> tuple[str, ...] | None:
    raw: object = getattr(view, "required_permissions", ())
    if not isinstance(raw, tuple) or len(raw) == 0:
        return None
    codes: list[str] = []
    for code in raw:
        if not isinstance(code, str) or not code:
            return None
        codes.append(code)
    return tuple(codes)


def _has_expense_capability(granted: frozenset[str] | set[str]) -> bool:
    return "expenses.create" in granted or "expenses.update" in granted


def _expense_file_gate(granted: frozenset[str] | set[str], operation: str) -> bool:
    if operation in {"upload", "complete"}:
        return "files.upload" in granted
    if operation == "download":
        return "files.view" in granted
    return operation == "delete"


def _declared_purpose(request: Request) -> str | None:
    try:
        payload = request.data
    except APIException:
        return None
    if not isinstance(payload, dict):
        return None
    purpose = payload.get("purpose")
    return purpose if isinstance(purpose, str) else None


def _stored_purpose(request: Request, view: APIView) -> str | None:
    gym = getattr(request, "gym", None)
    raw_id = view.kwargs.get("id")
    gym_id = getattr(gym, "id", None)
    if not isinstance(gym_id, UUID) or raw_id is None:
        return None
    if isinstance(raw_id, UUID):
        asset_id = raw_id
    else:
        try:
            asset_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            return None
    purpose = (
        FileAsset.objects.filter(gym_id=gym_id, id=asset_id)
        .values_list("purpose", flat=True)
        .first()
    )
    return purpose if isinstance(purpose, str) else None


def _close_trainer_finance(request: Request) -> None:
    """404 a protected trainer-only caller on an expense-receipt file operation."""

    actor = getattr(request, "gym_membership", None)
    gym = getattr(request, "gym", None)
    gym_id = getattr(gym, "id", None)
    if actor is None or not isinstance(gym_id, UUID):
        return
    from apps.trainers.selectors import deny_trainer_only

    deny_trainer_only(gym_id=gym_id, actor=actor)


def _trainer_only_profile_delete_hidden(
    request: Request,
    view: APIView,
    purpose: str | None,
) -> bool:
    """404 a trainer-only profile-photo delete that is outside assignment scope.

    An expense receipt keeps its own gate. A missing asset is also hidden so it
    does not look different from an unassigned member photo. An assigned photo
    stays on the permission check, which still requires ``files.delete``.
    """

    actor = getattr(request, "gym_membership", None)
    gym = getattr(request, "gym", None)
    gym_id = getattr(gym, "id", None)
    if actor is None or not isinstance(gym_id, UUID):
        return False
    from apps.trainers.selectors import (
        gym_local_date,
        member_is_assigned,
        trainer_member_scope,
    )

    scope = trainer_member_scope(gym_id=gym_id, actor=actor)
    if not scope.restricted:
        return False
    if purpose == EXPENSE_RECEIPT_PURPOSE:
        return False
    if purpose != PROFILE_PHOTO_PURPOSE or gym is None:
        return True
    raw_id = view.kwargs.get("id")
    if isinstance(raw_id, UUID):
        asset_id = raw_id
    else:
        try:
            asset_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            return True
    owner_id = (
        FileAsset.objects.filter(gym_id=gym_id, id=asset_id)
        .values_list("owner_id", flat=True)
        .first()
    )
    if not isinstance(owner_id, UUID):
        return True
    return not member_is_assigned(
        gym_id=gym_id,
        profile_ids=scope.profile_ids,
        member_id=owner_id,
        as_of=gym_local_date(gym),
    )


class PurposeAwareFilePermission(BasePermission):
    """Choose the file permission pair from the asset purpose.

    ``required_permissions`` is the ``PROFILE_PHOTO`` pair and is still required
    before profile-photo body handling. ``EXPENSE_RECEIPT`` does not require
    the member permission in that pair. A missing asset stays denied unless the
    caller could be authorized for one of the two purposes, so the service can
    return ``404`` for an unknown or cross-gym id. A trainer-only delete of a
    missing asset or an unassigned profile photo is ``404`` here; an assigned
    profile photo still follows ``files.delete``. A trainer-only expense-receipt
    operation is ``404`` before that asset is mutated. When the caller lacks the
    profile pair and the expense gate would allow the call, that ``404`` happens
    before the asset lookup. A missing file permission stays ``403``.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        granted = _granted(request)
        profile = _profile_codes(view)
        operation = getattr(view, "file_operation", "")
        if granted is None or profile is None or not isinstance(operation, str):
            return False
        if operation not in _OPERATIONS:
            return False
        if operation == "upload":
            if "files.upload" not in granted:
                return False
            if _declared_purpose(request) == EXPENSE_RECEIPT_PURPOSE and _has_expense_capability(
                granted
            ):
                _close_trainer_finance(request)
            if _declared_purpose(request) == EXPENSE_RECEIPT_PURPOSE:
                return True
            return all(code in granted for code in profile)
        purpose = _stored_purpose(request, view)
        if operation == "delete" and _trainer_only_profile_delete_hidden(request, view, purpose):
            raise Http404
        if purpose == PROFILE_PHOTO_PURPOSE:
            return all(code in granted for code in profile)
        if purpose == EXPENSE_RECEIPT_PURPOSE:
            if not _expense_file_gate(granted, operation):
                return False
            if _has_expense_capability(granted):
                _close_trainer_finance(request)
            return True
        if _has_expense_capability(granted) and (
            all(code in granted for code in profile) or _expense_file_gate(granted, operation)
        ):
            _close_trainer_finance(request)
        return all(code in granted for code in profile) or (
            _has_expense_capability(granted) and _expense_file_gate(granted, operation)
        )
