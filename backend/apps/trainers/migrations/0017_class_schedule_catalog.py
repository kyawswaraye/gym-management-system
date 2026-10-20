"""Link CLASS packages to an active same-gym class type.

The existing ``class_type_id`` column stays. PT rows remain null. Reverse
restores the PT-only guard and check from trainers 0004.
"""

import django.db.models.deletion
from django.db import migrations, models

FORWARD_SQL = r"""
ALTER TABLE trainers_service_package
  DROP CONSTRAINT svc_pkg_pt_only_ck;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT trainers_service_package_class_type_id_fkey
  FOREIGN KEY (class_type_id)
  REFERENCES classes_class_type (id)
  ON DELETE RESTRICT;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_class_type_same_gym_fk
  FOREIGN KEY (gym_id, class_type_id)
  REFERENCES classes_class_type (gym_id, id)
  ON DELETE RESTRICT;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_class_link_ck
  CHECK (
    (type = 'PT' AND class_type_id IS NULL)
    OR (type = 'CLASS' AND class_type_id IS NOT NULL)
  );

CREATE OR REPLACE FUNCTION app_guard_service_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'service_package_guard: package definitions are immutable';
  END IF;
  IF NEW.type = 'PT' THEN
    IF NEW.class_type_id IS NOT NULL THEN
      RAISE EXCEPTION 'service_package_guard: a PT package cannot name a class type';
    END IF;
  ELSIF NEW.type = 'CLASS' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM classes_class_type AS class_type
      WHERE class_type.gym_id = NEW.gym_id
        AND class_type.id = NEW.class_type_id
        AND class_type.is_active
    ) THEN
      RAISE EXCEPTION 'service_package_guard: a CLASS package requires an active class type';
    END IF;
  ELSE
    RAISE EXCEPTION 'service_package_guard: package type is not supported';
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

REVOKE ALL ON FUNCTION app_guard_service_package() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_service_package() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_service_package()
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

REVOKE ALL ON FUNCTION app_guard_service_package() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_service_package() TO gym_runtime, gym_migration;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_class_link_ck;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS svc_pkg_class_type_same_gym_fk;
ALTER TABLE trainers_service_package DROP CONSTRAINT IF EXISTS trainers_service_package_class_type_id_fkey;
ALTER TABLE trainers_service_package
  ADD CONSTRAINT svc_pkg_pt_only_ck
  CHECK (type = 'PT' AND class_type_id IS NULL);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0002_class_type_rls_and_grants"),
        ("tenants", "0028_class_permission_catalog"),
        ("trainers", "0016_pt_sale_refund_reversal_link"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="servicepackage",
                    name="svc_pkg_pt_only_ck",
                ),
                migrations.RemoveField(
                    model_name="servicepackage",
                    name="class_type_id",
                ),
                migrations.AddField(
                    model_name="servicepackage",
                    name="class_type",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="service_packages",
                        to="classes.classtype",
                    ),
                ),
                migrations.AddConstraint(
                    model_name="servicepackage",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("class_type__isnull", True), ("type", "PT")),
                            models.Q(("class_type__isnull", False), ("type", "CLASS")),
                            _connector="OR",
                        ),
                        name="svc_pkg_class_link_ck",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
            ],
        )
    ]
