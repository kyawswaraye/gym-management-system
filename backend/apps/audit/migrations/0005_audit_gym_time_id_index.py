"""Add the gym/time keyset index used by tenant audit reads.

The list predicate is always ``gym_id``, ordered by ``occurred_at DESC, id DESC``.
``audit_gym_target_time_idx`` leads with target columns, so it cannot provide
that order. This index lets the planner walk one gym's page and stop.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("audit", "0004_platform_audit_runtime_read")]

    operations = [
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["gym", "-occurred_at", "-id"],
                name="audit_gym_time_id_idx",
            ),
        )
    ]
