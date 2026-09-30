from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import django.contrib.postgres.constraints
from django.db import migrations, models


def normalize_statuses_and_check_overlaps(apps, schema_editor):
    Gym = apps.get_model("tenants", "Gym")
    Membership = apps.get_model("members", "Membership")
    now = datetime.now(UTC)

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT has_function_privilege(
                current_user,
                'app_current_gym_id()',
                'EXECUTE'
            )
            """
        )
        if not cursor.fetchone()[0]:
            raise RuntimeError("The migration database role must execute app_current_gym_id().")

    for gym in Gym.objects.all().iterator(chunk_size=100):
        try:
            today = now.astimezone(ZoneInfo(gym.timezone)).date()
        except ZoneInfoNotFoundError as exc:
            raise RuntimeError(
                f"Gym {gym.id} has an invalid timezone and must be corrected before migration."
            ) from exc
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.gym_id', %s, true)",
                [str(gym.id)],
            )
        Membership.objects.filter(
            gym_id=gym.id,
            status="ACTIVE",
            start_date__gt=today,
        ).update(status="PENDING")
        Membership.objects.filter(
            gym_id=gym.id,
            status__in=["PENDING", "ACTIVE", "SUSPENDED"],
            expiry_date__lt=today,
        ).update(status="EXPIRED")
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT first.id, second.id
                FROM members_membership AS first
                JOIN members_membership AS second
                  ON first.gym_id = second.gym_id
                 AND first.member_id = second.member_id
                 AND first.id < second.id
                 AND DATERANGE(first.start_date, first.expiry_date, '[]')
                     && DATERANGE(second.start_date, second.expiry_date, '[]')
                WHERE first.gym_id = %s
                  AND first.status IN ('PENDING', 'ACTIVE', 'SUSPENDED')
                  AND second.status IN ('PENDING', 'ACTIVE', 'SUSPENDED')
                LIMIT 1
                """,
                [str(gym.id)],
            )
            overlap = cursor.fetchone()
        if overlap:
            raise RuntimeError(
                "Overlapping live memberships must be reconciled before migration: "
                f"{overlap[0]} conflicts with {overlap[1]}."
            )


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0005_payment_correction_integrity"),
    ]

    operations = [
        migrations.RunPython(
            normalize_statuses_and_check_overlaps,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="membership",
            name="membership_no_active_overlap_excl",
        ),
        migrations.AddConstraint(
            model_name="membership",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                condition=models.Q(("status__in", ["PENDING", "ACTIVE", "SUSPENDED"])),
                expressions=[
                    (models.F("gym"), "="),
                    (models.F("member"), "="),
                    (
                        models.Func(
                            models.F("start_date"),
                            models.F("expiry_date"),
                            models.Value("[]"),
                            function="DATERANGE",
                        ),
                        "&&",
                    ),
                ],
                name="membership_no_active_overlap_excl",
            ),
        ),
    ]
