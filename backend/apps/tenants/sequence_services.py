"""Tenant sequence allocation and idempotent finance catalog seeding.

Allocation must run inside the caller's transaction. It locks one sequence
row, increments ``next_value`` by exactly one, and returns the formatted
number. Rollback restores ``next_value``. The database trigger rejects every
other column change.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import connection

from apps.tenants.models import ExpenseCategory, Gym, GymSettings, TenantSequence

EXPENSE_CATEGORY_CATALOG = (
    ("RENT", "Rent"),
    ("UTILITIES", "Electricity / water"),
    ("INTERNET", "Internet"),
    ("STAFF_SALARY", "Staff salary"),
    ("PT_COMMISSION", "PT commission"),
    ("CLEANING_SUPPLIES", "Cleaning supplies"),
    ("MAINTENANCE", "Maintenance / equipment repair"),
    ("MARKETING", "Marketing"),
    ("WATER_DRINKS", "Water / drinks"),
    ("OTHER", "Other expenses"),
)
_SEQUENCE_KEY = re.compile(r"^[A-Z][A-Z0-9_-]{0,40}:(?:19|20)\d{2}$")


class SequenceAllocationError(RuntimeError):
    """The sequence could not be allocated inside the current transaction."""


class FinanceSeedError(RuntimeError):
    """Protected finance catalog rows could not be seeded idempotently."""


def allocate_formatted_number(*, gym_id: uuid.UUID, sequence_key: str) -> str:
    """Return ``{prefix}-{year}-{padded value}`` and consume one number."""

    if not connection.in_atomic_block:
        raise SequenceAllocationError("Sequence allocation requires an open transaction.")
    if _SEQUENCE_KEY.fullmatch(sequence_key) is None or ":" not in sequence_key:
        raise SequenceAllocationError("Sequence key must be PREFIX:YEAR.")
    year = sequence_key.rsplit(":", 1)[1]
    try:
        sequence = TenantSequence.objects.select_for_update().get(
            gym_id=gym_id,
            sequence_key=sequence_key,
        )
    except TenantSequence.DoesNotExist as exc:
        raise SequenceAllocationError("The sequence does not exist.") from exc
    current = sequence.next_value
    sequence.next_value = current + 1
    sequence.save(update_fields=["next_value", "updated_at"])
    return f"{sequence.prefix}-{year}-{current:0{int(sequence.padding)}d}"


def ensure_protected_finance_seeds(
    *, gym: Gym, actor_membership_id: uuid.UUID | None = None
) -> None:
    """Create missing protected categories and the current receipt sequence.

    Existing rows are kept. A localized protected name is not rewritten, and
    ``next_value`` is not reset.
    """

    if not connection.in_atomic_block:
        raise FinanceSeedError("Finance seeding requires an open transaction.")
    try:
        settings_row = GymSettings.objects.get(gym=gym)
    except GymSettings.DoesNotExist as exc:
        raise FinanceSeedError("Gym settings are required before finance seeding.") from exc
    ensure_protected_expense_categories(gym=gym, actor_membership_id=actor_membership_id)
    ensure_receipt_sequence(
        gym=gym,
        prefix=settings_row.receipt_number_prefix,
        actor_membership_id=actor_membership_id,
    )


def ensure_protected_expense_categories(
    *, gym: Gym, actor_membership_id: uuid.UUID | None
) -> list[ExpenseCategory]:
    categories: list[ExpenseCategory] = []
    for display_order, (code, name) in enumerate(EXPENSE_CATEGORY_CATALOG, start=1):
        categories.append(
            _ensure_category(
                gym=gym,
                code=code,
                name=name,
                display_order=display_order,
                actor_membership_id=actor_membership_id,
            )
        )
    return categories


def ensure_receipt_sequence(
    *, gym: Gym, prefix: str, actor_membership_id: uuid.UUID | None
) -> TenantSequence:
    year = datetime.now(ZoneInfo(gym.timezone)).year
    sequence, _created = TenantSequence.objects.get_or_create(
        gym=gym,
        sequence_key=f"RECEIPT:{year}",
        defaults={
            "prefix": prefix,
            "next_value": 1,
            "padding": 6,
            "created_by_membership_id": actor_membership_id,
        },
    )
    return sequence


def _ensure_category(
    *,
    gym: Gym,
    code: str,
    name: str,
    display_order: int,
    actor_membership_id: uuid.UUID | None,
) -> ExpenseCategory:
    existing_key = ExpenseCategory.objects.filter(gym=gym, system_key=code).first()
    if existing_key is not None:
        return existing_key
    existing_code = ExpenseCategory.objects.filter(gym=gym, code=code).first()
    if existing_code is not None:
        if existing_code.system_key not in (None, ""):
            raise FinanceSeedError("A protected expense category code is already in use.")
        existing_code.system_key = code
        existing_code.is_active = True
        existing_code.save(update_fields=["system_key", "is_active", "updated_at"])
        return existing_code
    normalized_name = name.strip().upper()
    if ExpenseCategory.objects.filter(gym=gym, normalized_name=normalized_name).exists():
        raise FinanceSeedError("A protected expense category name is already in use.")
    return ExpenseCategory.objects.create(
        gym=gym,
        code=code,
        name=name,
        normalized_name=normalized_name,
        system_key=code,
        display_order=display_order,
        is_active=True,
        created_by_membership_id=actor_membership_id,
    )
