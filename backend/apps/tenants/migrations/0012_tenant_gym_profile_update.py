from django.db import migrations

FORWARD_SQL = """
GRANT UPDATE (
  name,
  legal_name,
  email,
  phone,
  address_line1,
  address_line2,
  city,
  region,
  postal_code,
  country_code,
  timezone,
  currency,
  updated_at
) ON tenants_gym TO gym_runtime;

CREATE OR REPLACE FUNCTION app_guard_runtime_gym_profile_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF current_user <> 'gym_runtime' THEN
    RETURN NEW;
  END IF;
  IF NULLIF(current_setting('app.gym_id', true), '') IS DISTINCT FROM NEW.id::text THEN
    RAISE EXCEPTION 'tenant gym updates require the selected gym context'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.code IS DISTINCT FROM OLD.code
    OR NEW.status IS DISTINCT FROM OLD.status
    OR NEW.subscription_plan_id IS DISTINCT FROM OLD.subscription_plan_id
    OR NEW.subscription_status IS DISTINCT FROM OLD.subscription_status
    OR NEW.subscription_starts_at IS DISTINCT FROM OLD.subscription_starts_at
    OR NEW.subscription_ends_at IS DISTINCT FROM OLD.subscription_ends_at
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'tenant gym updates cannot change code, lifecycle, or subscription'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER guard_runtime_gym_profile_update
BEFORE UPDATE ON tenants_gym
FOR EACH ROW EXECUTE FUNCTION app_guard_runtime_gym_profile_update();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS guard_runtime_gym_profile_update ON tenants_gym;
DROP FUNCTION IF EXISTS app_guard_runtime_gym_profile_update();
REVOKE UPDATE (
  name,
  legal_name,
  email,
  phone,
  address_line1,
  address_line2,
  city,
  region,
  postal_code,
  country_code,
  timezone,
  currency,
  updated_at
) ON tenants_gym FROM gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [("tenants", "0011_backfill_gym_prerequisites")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
