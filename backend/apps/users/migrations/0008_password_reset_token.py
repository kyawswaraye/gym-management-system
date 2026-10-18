import uuid

from django.db import migrations, models
import django.db.models.deletion


# gym_runtime may insert and revoke reset-token rows. It cannot UPDATE
# users_user.password. The password write and the cross-tenant authz bump
# go through security-definer functions in 0009.

FORWARD_SQL = r"""
GRANT SELECT, INSERT, UPDATE ON users_password_reset_token TO gym_runtime;
"""

REVERSE_SQL = r"""
REVOKE ALL ON users_password_reset_token FROM gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0007_member_refresh_context"),
    ]

    operations = [
        migrations.CreateModel(
            name="PasswordResetToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("issued_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("request_id", models.CharField(blank=True, max_length=128)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=512)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="password_reset_tokens",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "db_table": "users_password_reset_token",
            },
        ),
        migrations.AddIndex(
            model_name="passwordresettoken",
            index=models.Index(
                fields=["user", "expires_at"], name="pwdreset_user_exp_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="passwordresettoken",
            constraint=models.CheckConstraint(
                condition=models.Q(("expires_at__gt", models.F("issued_at"))),
                name="pwdreset_expiry_after_issue_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="passwordresettoken",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("consumed_at__isnull", True), ("revoked_at__isnull", True)
                ),
                fields=("user",),
                name="pwdreset_one_live_per_user",
            ),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
