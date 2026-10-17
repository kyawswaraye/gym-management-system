from django.db import migrations

RETIRED_CODE = "platform.settings.manage"
RETIRED_DESCRIPTION = (
    "Retired in Phase 1B. Historical catalog code with no approved HTTP route. "
    "Inactive and never authorizes tenant settings."
)


def retire_platform_settings_manage(apps, schema_editor):
    """Mark the historical platform-settings code inactive.

    Migration 0003 remains the historical catalog and still inserts this code
    as active. This later operation is the only retirement. A missing row is
    left missing; repeating the operation updates that same row and does not
    recreate it. Reverse is a no-op because reactivation requires an approved
    route decision.
    """

    Permission = apps.get_model("tenants", "Permission")
    Permission.objects.filter(code=RETIRED_CODE).update(
        is_active=False,
        description=RETIRED_DESCRIPTION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0019_role_permission_null_template_guard"),
    ]

    operations = [
        migrations.RunPython(retire_platform_settings_manage, migrations.RunPython.noop),
    ]
