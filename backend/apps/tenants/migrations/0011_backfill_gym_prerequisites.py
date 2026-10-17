"""Backfill gym prerequisites created before provisioning seeded them.

Each gym is committed separately. Re-running the operation skips rows that
already exist, so a failed gym can be resumed without rewriting completed
tenants or resetting receipt counters. Transaction-local tenant context keeps
the inserts valid while row-level security stays forced. Runtime grants are
unchanged.
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import migrations, transaction

PROTECTED_EXPENSE_CATEGORIES = (
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
_PREFIX_RE = re.compile(r"^[A-Z0-9-]{1,20}$")


def _receipt_prefix(code: str) -> str:
    candidate = code.strip().upper()
    if _PREFIX_RE.fullmatch(candidate):
        return candidate
    return "RECEIPT"


def _set_gym_context(connection, gym_id) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.gym_id', %s, true)", [str(gym_id)])


def _actor_membership_id(membership_model, gym_id):
    owner_id = (
        membership_model.objects.filter(gym_id=gym_id, is_owner=True, status="ACTIVE")
        .order_by("created_at", "id")
        .values_list("id", flat=True)
        .first()
    )
    if owner_id is not None:
        return owner_id
    return (
        membership_model.objects.filter(gym_id=gym_id)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
        .first()
    )


def _ensure_category(category_model, *, gym_id, code, name, display_order, actor_id) -> None:
    if category_model.objects.filter(gym_id=gym_id, system_key=code).exists():
        return
    existing = category_model.objects.filter(gym_id=gym_id, code=code).first()
    if existing is not None:
        if existing.system_key not in (None, ""):
            raise RuntimeError(
                f"Gym {gym_id} already uses expense category code {code} "
                "for a different protected key."
            )
        existing.system_key = code
        existing.is_active = True
        existing.save(update_fields=["system_key", "is_active", "updated_at"])
        return
    normalized_name = name.strip().upper()
    if category_model.objects.filter(gym_id=gym_id, normalized_name=normalized_name).exists():
        raise RuntimeError(
            f"Gym {gym_id} cannot receive protected category {code}: "
            "its display name is already used."
        )
    category_model.objects.create(
        gym_id=gym_id,
        code=code,
        name=name,
        normalized_name=normalized_name,
        system_key=code,
        display_order=display_order,
        is_active=True,
        created_by_membership_id=actor_id,
    )


def _backfill_gym(apps, connection, gym_id) -> None:
    gym_model = apps.get_model("tenants", "Gym")
    settings_model = apps.get_model("tenants", "GymSettings")
    category_model = apps.get_model("tenants", "ExpenseCategory")
    sequence_model = apps.get_model("tenants", "TenantSequence")
    membership_model = apps.get_model("tenants", "GymMembership")

    with transaction.atomic(using=connection.alias):
        gym = gym_model.objects.select_for_update().get(pk=gym_id)
        _set_gym_context(connection, gym.id)
        try:
            year = datetime.now(ZoneInfo(gym.timezone)).year
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(f"Gym {gym.id} has an unsupported timezone.") from exc
        actor_id = _actor_membership_id(membership_model, gym.id)
        settings, _created = settings_model.objects.get_or_create(
            gym_id=gym.id,
            defaults={
                "week_starts_on": 1,
                "allow_manual_member_numbers": False,
                "member_number_prefix": "MEM",
                "receipt_number_prefix": _receipt_prefix(gym.code),
                "check_in_duplicate_window_seconds": 300,
                "check_in_challenge_ttl_seconds": 120,
                "check_in_challenge_max_uses": 1,
                "membership_expiry_warning_days": [0, 3, 7, 30],
                "membership_expiry_email_enabled": True,
                "membership_expiry_push_enabled": False,
                "class_cancellation_cutoff_minutes": 60,
                "class_no_show_consumes_session": True,
                "require_separate_finance_approval": True,
                "next_member_number": 1,
                "created_by_membership_id": actor_id,
            },
        )
        for display_order, (code, name) in enumerate(PROTECTED_EXPENSE_CATEGORIES, start=1):
            _ensure_category(
                category_model,
                gym_id=gym.id,
                code=code,
                name=name,
                display_order=display_order,
                actor_id=actor_id,
            )
        sequence_model.objects.get_or_create(
            gym_id=gym.id,
            sequence_key=f"RECEIPT:{year}",
            defaults={
                "prefix": settings.receipt_number_prefix,
                "next_value": 1,
                "padding": 6,
                "created_by_membership_id": actor_id,
            },
        )


def backfill_gym_prerequisites(apps, schema_editor) -> None:
    connection = schema_editor.connection
    gym_model = apps.get_model("tenants", "Gym")
    gym_ids = list(
        gym_model.objects.using(connection.alias).order_by("id").values_list("id", flat=True)
    )
    for gym_id in gym_ids:
        _backfill_gym(apps, connection, gym_id)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("tenants", "0010_owner_invitations")]

    operations = [
        migrations.RunPython(backfill_gym_prerequisites, migrations.RunPython.noop, atomic=False),
    ]
