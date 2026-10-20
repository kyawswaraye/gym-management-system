"""Same-gym actor keys, content freeze, forced RLS, and runtime grants."""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcements_announcement_created_by_membership_id_fkey;
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcements_announcement_published_by_membership_id_fkey;
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcements_announcement_cancelled_by_membership_id_fkey;

ALTER TABLE announcements_announcement
  ADD CONSTRAINT announcement_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE announcements_announcement
  ADD CONSTRAINT announcement_published_by_same_gym_fk
  FOREIGN KEY (gym_id, published_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE announcements_announcement
  ADD CONSTRAINT announcement_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_announcement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id THEN
      RAISE EXCEPTION 'announcement_guard: identity columns are immutable';
    END IF;
    IF OLD.status IS DISTINCT FROM 'DRAFT'
       AND (
         NEW.type IS DISTINCT FROM OLD.type
         OR NEW.title IS DISTINCT FROM OLD.title
         OR NEW.body IS DISTINCT FROM OLD.body
         OR NEW.audience IS DISTINCT FROM OLD.audience
         OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
         OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
       ) THEN
      RAISE EXCEPTION 'announcement_guard: content is frozen';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_announcement_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'announcement_guard: announcements cannot be deleted';
END;
$guard$;

CREATE TRIGGER announcement_guard_trg
BEFORE INSERT OR UPDATE ON announcements_announcement
FOR EACH ROW EXECUTE FUNCTION app_guard_announcement();

CREATE TRIGGER announcement_delete_trg
BEFORE DELETE ON announcements_announcement
FOR EACH ROW EXECUTE FUNCTION app_guard_announcement_delete();

ALTER TABLE announcements_announcement ENABLE ROW LEVEL SECURITY;
ALTER TABLE announcements_announcement FORCE ROW LEVEL SECURITY;
CREATE POLICY announcements_announcement_tenant_select
  ON announcements_announcement
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY announcements_announcement_tenant_insert
  ON announcements_announcement
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY announcements_announcement_tenant_update
  ON announcements_announcement
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY announcements_announcement_tenant_delete
  ON announcements_announcement
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE announcements_announcement FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE announcements_announcement TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_announcement() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_announcement_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_announcement() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_announcement_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_announcement_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_announcement() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE announcements_announcement FROM gym_runtime;
DROP POLICY IF EXISTS announcements_announcement_tenant_delete
  ON announcements_announcement;
DROP POLICY IF EXISTS announcements_announcement_tenant_update
  ON announcements_announcement;
DROP POLICY IF EXISTS announcements_announcement_tenant_insert
  ON announcements_announcement;
DROP POLICY IF EXISTS announcements_announcement_tenant_select
  ON announcements_announcement;
ALTER TABLE announcements_announcement NO FORCE ROW LEVEL SECURITY;
ALTER TABLE announcements_announcement DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS announcement_delete_trg ON announcements_announcement;
DROP TRIGGER IF EXISTS announcement_guard_trg ON announcements_announcement;
DROP FUNCTION IF EXISTS app_guard_announcement_delete();
DROP FUNCTION IF EXISTS app_guard_announcement();
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcement_cancelled_by_same_gym_fk;
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcement_published_by_same_gym_fk;
ALTER TABLE announcements_announcement
  DROP CONSTRAINT IF EXISTS announcement_created_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("announcements", "0001_phase7_notifications"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
