"""Use a readable receipt prefix for new gym settings.

The gym code stays the gym id. New settings default to RCPT, so a receipt
reads RCPT-2026-000001. Existing sequences keep the prefix they were created
with because that prefix is immutable.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0031_member_restore_permission"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gymsettings",
            name="receipt_number_prefix",
            field=models.CharField(default="RCPT", max_length=20),
        ),
    ]
