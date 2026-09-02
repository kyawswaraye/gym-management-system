from django.db import migrations


FORWARD_SQL = """
ALTER TABLE audit_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_audit_log_tenant_select ON audit_audit_log
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY audit_audit_log_tenant_insert ON audit_audit_log
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY audit_audit_log_tenant_update ON audit_audit_log
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY audit_audit_log_tenant_delete ON audit_audit_log
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE audit_audit_log
  ADD CONSTRAINT audit_actor_membership_same_gym_fk
  FOREIGN KEY (gym_id, actor_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE audit_audit_log
  ADD CONSTRAINT audit_impersonator_membership_same_gym_fk
  FOREIGN KEY (gym_id, impersonator_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION reject_audit_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'audit records are append-only'; END $$;
CREATE TRIGGER audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_audit_log
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
CREATE TRIGGER platform_audit_log_append_only
BEFORE UPDATE OR DELETE ON audit_platform_audit_log
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();

GRANT SELECT, INSERT ON audit_audit_log TO gym_runtime;
GRANT INSERT ON audit_platform_audit_log TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON audit_audit_log, audit_platform_audit_log FROM gym_runtime;
DROP TRIGGER IF EXISTS platform_audit_log_append_only ON audit_platform_audit_log;
DROP TRIGGER IF EXISTS audit_log_append_only ON audit_audit_log;
DROP FUNCTION IF EXISTS reject_audit_mutation();
ALTER TABLE audit_audit_log
  DROP CONSTRAINT IF EXISTS audit_impersonator_membership_same_gym_fk;
ALTER TABLE audit_audit_log DROP CONSTRAINT IF EXISTS audit_actor_membership_same_gym_fk;
DROP POLICY IF EXISTS audit_audit_log_tenant_select ON audit_audit_log;
DROP POLICY IF EXISTS audit_audit_log_tenant_insert ON audit_audit_log;
DROP POLICY IF EXISTS audit_audit_log_tenant_update ON audit_audit_log;
DROP POLICY IF EXISTS audit_audit_log_tenant_delete ON audit_audit_log;
ALTER TABLE audit_audit_log NO FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_audit_log DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_initial"),
        ("tenants", "0004_rls_integrity_and_grants"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
