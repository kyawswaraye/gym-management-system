from django.db import migrations


TENANT_TABLES = (
    "tenants_gym_membership",
    "tenants_role",
    "tenants_role_permission",
    "tenants_gym_membership_role",
    "tenants_gym_feature_entitlement",
)

POLICY_SQL = "\n".join(
    f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_tenant_select ON {table}
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_insert ON {table}
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_update ON {table}
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_delete ON {table}
  FOR DELETE USING (gym_id = app_current_gym_id());
"""
    for table in TENANT_TABLES
)

REVERSE_POLICY_SQL = "\n".join(
    f"""
DROP POLICY IF EXISTS {table}_tenant_select ON {table};
DROP POLICY IF EXISTS {table}_tenant_insert ON {table};
DROP POLICY IF EXISTS {table}_tenant_update ON {table};
DROP POLICY IF EXISTS {table}_tenant_delete ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
    for table in reversed(TENANT_TABLES)
)

FORWARD_SQL = f"""
CREATE OR REPLACE FUNCTION app_current_gym_id()
RETURNS uuid LANGUAGE sql STABLE
SET search_path = pg_catalog
AS $$ SELECT NULLIF(current_setting('app.gym_id', true), '')::uuid $$;

{POLICY_SQL}

ALTER TABLE tenants_role_permission
  ADD CONSTRAINT role_permission_same_gym_fk
  FOREIGN KEY (gym_id, role_id) REFERENCES tenants_role (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE tenants_gym_membership_role
  ADD CONSTRAINT membership_role_membership_same_gym_fk
  FOREIGN KEY (gym_id, gym_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE tenants_gym_membership_role
  ADD CONSTRAINT membership_role_role_same_gym_fk
  FOREIGN KEY (gym_id, role_id) REFERENCES tenants_role (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE users_refresh_session
  ADD CONSTRAINT refresh_membership_same_gym_fk
  FOREIGN KEY (selected_gym_id, gym_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION app_staff_memberships()
RETURNS TABLE (
  gym_membership_id uuid,
  gym_id uuid,
  gym_code varchar,
  gym_name varchar,
  membership_status varchar,
  is_owner boolean,
  authz_version integer
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
  SELECT m.id, m.gym_id, g.code, g.name, m.status, m.is_owner, m.authz_version
  FROM public.tenants_gym_membership m
  JOIN public.tenants_gym g ON g.id = m.gym_id
  WHERE m.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    AND m.status = 'ACTIVE'
    AND g.status = 'ACTIVE'
    AND g.subscription_status IN ('TRIAL', 'ACTIVE')
  ORDER BY g.name, m.id
$$;
ALTER FUNCTION app_staff_memberships() OWNER TO gym_auth_bootstrap;

REVOKE ALL ON FUNCTION app_current_gym_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_staff_memberships() FROM PUBLIC;
GRANT SELECT ON tenants_gym_membership, tenants_gym TO gym_auth_bootstrap;
GRANT EXECUTE ON FUNCTION app_current_gym_id() TO gym_runtime;
GRANT EXECUTE ON FUNCTION app_staff_memberships() TO gym_runtime;
GRANT USAGE ON SCHEMA public TO gym_runtime;
GRANT SELECT ON users_user, tenants_gym, tenants_permission, tenants_feature TO gym_runtime;
GRANT SELECT, INSERT, UPDATE ON users_refresh_session TO gym_runtime;
GRANT SELECT, INSERT, UPDATE ON {", ".join(TENANT_TABLES)} TO gym_runtime;
"""

REVERSE_SQL = f"""
REVOKE ALL ON {", ".join(TENANT_TABLES)} FROM gym_runtime;
REVOKE ALL ON users_refresh_session FROM gym_runtime;
REVOKE ALL ON tenants_gym_membership, tenants_gym FROM gym_auth_bootstrap;
DROP FUNCTION IF EXISTS app_staff_memberships();
ALTER TABLE users_refresh_session DROP CONSTRAINT IF EXISTS refresh_membership_same_gym_fk;
ALTER TABLE tenants_gym_membership_role
  DROP CONSTRAINT IF EXISTS membership_role_role_same_gym_fk;
ALTER TABLE tenants_gym_membership_role
  DROP CONSTRAINT IF EXISTS membership_role_membership_same_gym_fk;
ALTER TABLE tenants_role_permission DROP CONSTRAINT IF EXISTS role_permission_same_gym_fk;
{REVERSE_POLICY_SQL}
DROP FUNCTION IF EXISTS app_current_gym_id();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0003_seed_authorization"),
        ("users", "0001_initial"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
