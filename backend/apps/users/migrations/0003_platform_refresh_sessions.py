import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0004_rls_integrity_and_grants"),
        ("users", "0002_gym_selection_ticket"),
    ]

    operations = [
        migrations.AlterField(
            model_name="refreshsession",
            name="principal_type",
            field=models.CharField(
                choices=[
                    ("PLATFORM", "Platform"),
                    ("STAFF", "Staff"),
                    ("MEMBER", "Member"),
                ],
                default="STAFF",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="refreshsession",
            name="selected_gym",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                to="tenants.gym",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="refreshsession",
            name="refresh_principal_context_ck",
        ),
        migrations.AddConstraint(
            model_name="refreshsession",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        principal_type="PLATFORM",
                        selected_gym__isnull=True,
                        gym_membership__isnull=True,
                        member_id__isnull=True,
                    )
                    | models.Q(
                        principal_type="STAFF",
                        selected_gym__isnull=False,
                        gym_membership__isnull=False,
                        member_id__isnull=True,
                    )
                    | models.Q(
                        principal_type="MEMBER",
                        selected_gym__isnull=False,
                        gym_membership__isnull=True,
                        member_id__isnull=False,
                    )
                ),
                name="refresh_principal_context_ck",
            ),
        ),
    ]
