from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0016_manager_retail_approve"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="role",
            name="role_source_pair_ck",
        ),
        migrations.AddConstraint(
            model_name="role",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("source_template__isnull", True),
                        ("source_template_version__isnull", True),
                    ),
                    models.Q(
                        ("source_template__isnull", False),
                        ("source_template_version__gt", 0),
                        ("source_template_version__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="role_source_pair_ck",
            ),
        ),
    ]
