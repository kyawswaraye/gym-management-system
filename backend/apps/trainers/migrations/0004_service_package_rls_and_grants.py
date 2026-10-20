"""Lock PT package definitions to the creating gym.

``class_type_id`` stays a nullable uuid with no foreign key until ClassType
exists. The PT-only check can be dropped when that catalog is published.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_code_ck
  CHECK (code ~ '^[A-Z][A-Z0-9_]{0,31}$');
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_name_ck
  CHECK (
    name <> ''
    AND name = regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g')
    AND normalized_name = upper(name)
  );
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_currency_ck
  CHECK (currency ~ '^[A-Z]{3}$');

CREATE FUNCTION app_guard_service_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'service_package_guard: package definitions are immutable';
  END IF;
  IF NEW.type <> 'PT' OR NEW.class_type_id IS NOT NULL THEN
    RAISE EXCEPTION 'service_package_guard: only PT packages without a class type can be stored';
  END IF;
  IF NOT NEW.is_active THEN
    RAISE EXCEPTION 'service_package_guard: a new package must be active';
  END IF;
  IF NEW.code !~ '^[A-Z][A-Z0-9_]{0,31}$' THEN
    RAISE EXCEPTION 'service_package_guard: code is not an uppercase token';
  END IF;
  IF NEW.name IS DISTINCT FROM regexp_replace(btrim(NEW.name), '[[:space:]]+', ' ', 'g')
     OR NEW.normalized_name IS DISTINCT FROM upper(NEW.name)
     OR NEW.name = '' THEN
    RAISE EXCEPTION 'service_package_guard: name is not normalized';
  END IF;
  IF NEW.session_count < 1 OR NEW.validity_value < 1 THEN
    RAISE EXCEPTION 'service_package_guard: sessions and validity must be positive';
  END IF;
  IF NEW.validity_unit NOT IN ('DAY', 'WEEK', 'MONTH', 'YEAR') THEN
    RAISE EXCEPTION 'service_package_guard: validity unit is not a calendar unit';
  END IF;
  IF NEW.price < 0 THEN
    RAISE EXCEPTION 'service_package_guard: price must be nonnegative';
  END IF;
  IF NEW.currency !~ '^[A-Z]{3}$'
     OR NEW.currency IS DISTINCT FROM (
       SELECT currency FROM tenants_gym WHERE id = NEW.gym_id
     ) THEN
    RAISE EXCEPTION 'service_package_guard: currency must match the gym';
  END IF;
  IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
    RAISE EXCEPTION 'service_package_guard: actors must match on create';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_service_package_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'service_package_guard: package definitions cannot be deleted';
END;
$guard$;

CREATE TRIGGER service_package_guard_trg
BEFORE INSERT OR UPDATE ON trainers_service_package
FOR EACH ROW EXECUTE FUNCTION app_guard_service_package();

CREATE TRIGGER service_package_delete_trg
BEFORE DELETE ON trainers_service_package
FOR EACH ROW EXECUTE FUNCTION app_guard_service_package_delete();

ALTER TABLE trainers_service_package ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_service_package FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_service_package_select ON trainers_service_package
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_service_package_insert ON trainers_service_package
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_service_package_update ON trainers_service_package
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_service_package_delete ON trainers_service_package
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_service_package FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE trainers_service_package TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_service_package() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_service_package_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_service_package() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_service_package_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_service_package_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_service_package() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_service_package FROM gym_runtime;
DROP POLICY IF EXISTS trainers_service_package_delete ON trainers_service_package;
DROP POLICY IF EXISTS trainers_service_package_update ON trainers_service_package;
DROP POLICY IF EXISTS trainers_service_package_insert ON trainers_service_package;
DROP POLICY IF EXISTS trainers_service_package_select ON trainers_service_package;
ALTER TABLE trainers_service_package NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_service_package DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS service_package_delete_trg ON trainers_service_package;
DROP TRIGGER IF EXISTS service_package_guard_trg ON trainers_service_package;
DROP FUNCTION IF EXISTS app_guard_service_package_delete();
DROP FUNCTION IF EXISTS app_guard_service_package();
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_currency_ck;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_name_ck;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_code_ck;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_updated_by_same_gym_fk;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_created_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0003_service_package"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
