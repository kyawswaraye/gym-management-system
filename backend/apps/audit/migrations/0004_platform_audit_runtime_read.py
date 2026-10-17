from django.db import migrations

GRANT_SQL = "GRANT SELECT ON audit_platform_audit_log TO gym_runtime;"
REVOKE_SQL = "REVOKE SELECT ON audit_platform_audit_log FROM gym_runtime;"


class Migration(migrations.Migration):
    dependencies = [("audit", "0003_rls_append_only")]

    operations = [
        migrations.RunSQL(
            sql=GRANT_SQL,
            reverse_sql=REVOKE_SQL,
        )
    ]
