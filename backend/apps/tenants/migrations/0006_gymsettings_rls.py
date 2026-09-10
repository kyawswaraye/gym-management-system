from django.db import migrations


FORWARD_SQL = """
ALTER TABLE tenants_gym_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants_gym_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenants_gym_settings_tenant_select ON tenants_gym_settings
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_settings_tenant_insert ON tenants_gym_settings
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_settings_tenant_update ON tenants_gym_settings
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_gym_settings_tenant_delete ON tenants_gym_settings
  FOR DELETE USING (gym_id = app_current_gym_id());
GRANT SELECT, INSERT, UPDATE ON tenants_gym_settings TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON tenants_gym_settings FROM gym_runtime;
DROP POLICY IF EXISTS tenants_gym_settings_tenant_select ON tenants_gym_settings;
DROP POLICY IF EXISTS tenants_gym_settings_tenant_insert ON tenants_gym_settings;
DROP POLICY IF EXISTS tenants_gym_settings_tenant_update ON tenants_gym_settings;
DROP POLICY IF EXISTS tenants_gym_settings_tenant_delete ON tenants_gym_settings;
ALTER TABLE tenants_gym_settings NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants_gym_settings DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("tenants", "0005_gymsettings")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
