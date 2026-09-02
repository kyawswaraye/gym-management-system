from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models
import django.db.models.deletion
import uuid


FORWARD_SQL = """
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_expires_five_minutes_ck
  CHECK (expires_at = issued_at + INTERVAL '5 minutes');
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_max_attempts_ck
  CHECK (max_attempts BETWEEN 1 AND 10);
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_attempt_bounds_ck
  CHECK (attempt_count >= 0 AND attempt_count <= max_attempts);
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_terminal_exclusive_ck
  CHECK (consumed_at IS NULL OR revoked_at IS NULL);
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_selected_when_consumed_ck
  CHECK (
    (consumed_at IS NULL AND selected_gym_membership_id IS NULL)
    OR (consumed_at IS NOT NULL AND selected_gym_membership_id IS NOT NULL)
  );
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_selected_in_snapshot_ck
  CHECK (
    selected_gym_membership_id IS NULL
    OR selected_gym_membership_id = ANY (eligible_gym_membership_ids)
  );
ALTER TABLE users_gym_selection_ticket
  ADD CONSTRAINT gymsel_two_memberships_ck
  CHECK (cardinality(eligible_gym_membership_ids) >= 2);
GRANT SELECT, INSERT, UPDATE ON users_gym_selection_ticket TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON users_gym_selection_ticket FROM gym_runtime;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_two_memberships_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_selected_in_snapshot_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_selected_when_consumed_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_terminal_exclusive_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_attempt_bounds_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_max_attempts_ck;
ALTER TABLE users_gym_selection_ticket DROP CONSTRAINT IF EXISTS gymsel_expires_five_minutes_ck;
"""


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tenants", "0004_rls_integrity_and_grants"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="GymSelectionTicket",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("ticket_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("eligible_gym_membership_ids", ArrayField(base_field=models.UUIDField())),
                ("issued_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("selected_gym_membership_id", models.UUIDField(blank=True, null=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=5)),
                ("revocation_reason", models.CharField(blank=True, max_length=64)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=512)),
                ("request_id", models.CharField(blank=True, max_length=128)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="gym_selection_tickets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "users_gym_selection_ticket"},
        ),
        migrations.AddIndex(
            model_name="gymselectionticket",
            index=models.Index(fields=["user", "expires_at"], name="gymsel_user_expires_idx"),
        ),
        migrations.AddIndex(
            model_name="gymselectionticket",
            index=models.Index(fields=["expires_at"], name="gymsel_expires_idx"),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
