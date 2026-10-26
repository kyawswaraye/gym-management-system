"""Store one private logo image per gym."""

from django.db import migrations, models


FORWARD_SQL = """
ALTER TABLE tenants_gym_logo ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants_gym_logo FORCE ROW LEVEL SECURITY;
CREATE POLICY tenants_gym_logo_tenant_select ON tenants_gym_logo
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_logo_tenant_insert ON tenants_gym_logo
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_logo_tenant_update ON tenants_gym_logo
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_logo_tenant_delete ON tenants_gym_logo
  FOR DELETE USING (gym_id = app_current_gym_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON tenants_gym_logo TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON tenants_gym_logo FROM gym_runtime;
DROP POLICY IF EXISTS tenants_gym_logo_tenant_select ON tenants_gym_logo;
DROP POLICY IF EXISTS tenants_gym_logo_tenant_insert ON tenants_gym_logo;
DROP POLICY IF EXISTS tenants_gym_logo_tenant_update ON tenants_gym_logo;
DROP POLICY IF EXISTS tenants_gym_logo_tenant_delete ON tenants_gym_logo;
ALTER TABLE tenants_gym_logo NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants_gym_logo DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0032_receipt_prefix_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="GymLogo",
            fields=[
                (
                    "gym",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        primary_key=True,
                        related_name="brand_logo",
                        serialize=False,
                        to="tenants.gym",
                    ),
                ),
                ("content_type", models.CharField(max_length=32)),
                ("data", models.BinaryField()),
            ],
            options={
                "db_table": "tenants_gym_logo",
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
