"""Lock class types to the creating gym.

Code is immutable. Runtime may select, insert, and update. Delete is rejected.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE classes_class_type
  ADD CONSTRAINT class_type_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_type
  ADD CONSTRAINT class_type_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_type
  ADD CONSTRAINT class_type_code_ck
  CHECK (code ~ '^[A-Z][A-Z0-9_]{0,31}$');
ALTER TABLE classes_class_type
  ADD CONSTRAINT class_type_name_ck
  CHECK (
    name <> ''
    AND name = regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g')
    AND normalized_name = upper(name)
  );
ALTER TABLE classes_class_type
  ADD CONSTRAINT class_type_description_ck
  CHECK (description = regexp_replace(btrim(description), '[[:space:]]+', ' ', 'g'));

CREATE FUNCTION app_guard_class_type()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.code IS DISTINCT FROM OLD.code THEN
      RAISE EXCEPTION 'class_type_guard: identity columns are immutable';
    END IF;
  END IF;
  IF NEW.code !~ '^[A-Z][A-Z0-9_]{0,31}$' THEN
    RAISE EXCEPTION 'class_type_guard: code is not an uppercase token';
  END IF;
  IF NEW.name = ''
     OR NEW.name IS DISTINCT FROM regexp_replace(btrim(NEW.name), '[[:space:]]+', ' ', 'g')
     OR NEW.normalized_name IS DISTINCT FROM upper(NEW.name) THEN
    RAISE EXCEPTION 'class_type_guard: name is not normalized';
  END IF;
  IF NEW.description IS DISTINCT FROM regexp_replace(
    btrim(NEW.description), '[[:space:]]+', ' ', 'g'
  ) THEN
    RAISE EXCEPTION 'class_type_guard: description is not normalized';
  END IF;
  IF NEW.default_duration_minutes < 1 OR NEW.default_capacity < 1 THEN
    RAISE EXCEPTION 'class_type_guard: duration and capacity must be positive';
  END IF;
  IF TG_OP = 'INSERT'
     AND NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
    RAISE EXCEPTION 'class_type_guard: actors must match on create';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_class_type_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'class_type_guard: class types cannot be deleted';
END;
$guard$;

CREATE TRIGGER class_type_guard_trg
BEFORE INSERT OR UPDATE ON classes_class_type
FOR EACH ROW EXECUTE FUNCTION app_guard_class_type();

CREATE TRIGGER class_type_delete_trg
BEFORE DELETE ON classes_class_type
FOR EACH ROW EXECUTE FUNCTION app_guard_class_type_delete();

ALTER TABLE classes_class_type ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes_class_type FORCE ROW LEVEL SECURITY;
CREATE POLICY classes_class_type_select ON classes_class_type
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY classes_class_type_insert ON classes_class_type
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_type_update ON classes_class_type
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_type_delete ON classes_class_type
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE classes_class_type FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE classes_class_type TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_class_type() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_type_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_class_type() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_type_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_class_type_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_type() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE classes_class_type FROM gym_runtime;
DROP POLICY IF EXISTS classes_class_type_delete ON classes_class_type;
DROP POLICY IF EXISTS classes_class_type_update ON classes_class_type;
DROP POLICY IF EXISTS classes_class_type_insert ON classes_class_type;
DROP POLICY IF EXISTS classes_class_type_select ON classes_class_type;
ALTER TABLE classes_class_type NO FORCE ROW LEVEL SECURITY;
ALTER TABLE classes_class_type DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS class_type_delete_trg ON classes_class_type;
DROP TRIGGER IF EXISTS class_type_guard_trg ON classes_class_type;
DROP FUNCTION IF EXISTS app_guard_class_type_delete();
DROP FUNCTION IF EXISTS app_guard_class_type();
ALTER TABLE classes_class_type DROP CONSTRAINT IF EXISTS class_type_description_ck;
ALTER TABLE classes_class_type DROP CONSTRAINT IF EXISTS class_type_name_ck;
ALTER TABLE classes_class_type DROP CONSTRAINT IF EXISTS class_type_code_ck;
ALTER TABLE classes_class_type DROP CONSTRAINT IF EXISTS class_type_updated_by_same_gym_fk;
ALTER TABLE classes_class_type DROP CONSTRAINT IF EXISTS class_type_created_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0001_class_type"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
