"""Bounded two-gym isolation smoke for the disposable staging rehearsal.

The command inserts ephemeral gym rows with the runtime role and reads them
back under forced RLS. It prints counts only. It does not log credentials or
claim a production deploy.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from apps.common.db.tenant_context import clear_gym_context, set_gym_context
from apps.tenants.models import Gym, GymSettings

GYM_SPECS = (
    ("rehearsa", "Rehearsal A"),
    ("rehearsb", "Rehearsal B"),
)


def judge_isolation_counts(
    *,
    visible_a: int,
    visible_b: int,
    empty_rows: int,
    cross_a: int,
    cross_b: int,
    cross_update: int,
    schema_denied: bool,
) -> dict[str, object]:
    """Return the smoke result. Live proof flags stay false."""

    isolated = (
        visible_a == 1
        and visible_b == 1
        and empty_rows == 0
        and cross_a == 0
        and cross_b == 0
        and cross_update == 0
        and schema_denied
    )
    return {
        "isolated": isolated,
        "gyms": 2,
        "visible_with_context": 1 if isolated else visible_a,
        "empty_context_rows": empty_rows,
        "cross_gym_rows": cross_a + cross_b,
        "cross_update_rows": cross_update,
        "runtime_schema_write": "denied" if schema_denied else "allowed",
        "production_deploy_proven": False,
        "ghcr_proven": False,
        "protected_environment_proven": False,
        "offsite_backup_proven": False,
        "external_provider_proven": False,
        "rpo_proven": False,
        "rto_proven": False,
    }


def _require_rehearsal() -> None:
    if settings.SETTINGS_MODULE != "config.settings.staging_rehearsal":
        raise CommandError("staging isolation smoke requires the rehearsal settings")
    if getattr(settings, "STAGING_REHEARSAL", False) is not True:
        raise CommandError("staging isolation smoke requires the rehearsal flag")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        row = cursor.fetchone()
    if row is None or row[0] != "gym_runtime":
        raise CommandError("staging isolation smoke requires the runtime role")


def _schema_write_denied() -> bool:
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE staging_rehearsal_forbidden (id integer)")
            raise RuntimeError("rollback")
    except RuntimeError:
        return False
    except DatabaseError:
        return True


def _ensure_gym(code: str, name: str) -> Gym:
    existing = Gym.objects.filter(code=code).first()
    if existing is not None:
        return existing
    gym_id = uuid.uuid4()
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        gym = Gym(
            id=gym_id,
            code=code,
            name=name,
            status=Gym.Status.ACTIVE,
            timezone="Asia/Yangon",
            currency="THB",
        )
        gym.save()
        GymSettings.objects.create(gym=gym)
    return gym


def _visible_ids(gym_id: uuid.UUID) -> list[uuid.UUID]:
    with transaction.atomic():
        set_gym_context(gym_id=gym_id)
        return list(GymSettings.objects.values_list("gym_id", flat=True))


def run_isolation() -> dict[str, object]:
    _require_rehearsal()
    first = _ensure_gym(*GYM_SPECS[0])
    second = _ensure_gym(*GYM_SPECS[1])
    visible_a = _visible_ids(first.id)
    visible_b = _visible_ids(second.id)
    with transaction.atomic():
        clear_gym_context()
        empty_rows = GymSettings.objects.count()
    with transaction.atomic():
        set_gym_context(gym_id=first.id)
        cross_a = GymSettings.objects.filter(gym_id=second.id).count()
    with transaction.atomic():
        set_gym_context(gym_id=second.id)
        cross_b = GymSettings.objects.filter(gym_id=first.id).count()
        cross_update = GymSettings.objects.filter(gym_id=first.id).update(week_starts_on=3)
    return judge_isolation_counts(
        visible_a=len(visible_a) if visible_a == [first.id] else len(visible_a) + 1,
        visible_b=len(visible_b) if visible_b == [second.id] else len(visible_b) + 1,
        empty_rows=empty_rows,
        cross_a=cross_a,
        cross_b=cross_b,
        cross_update=cross_update,
        schema_denied=_schema_write_denied(),
    )


class Command(BaseCommand):
    help = "Prove two rehearsal gyms stay isolated under the runtime role. Not a production proof."

    def handle(self, *args: Any, **options: Any) -> None:
        report = run_isolation()
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report["isolated"] is not True:
            raise CommandError("staging isolation smoke failed")
