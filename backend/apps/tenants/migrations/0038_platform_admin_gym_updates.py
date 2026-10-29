from django.db import migrations

FORWARD_SQL = """
GRANT UPDATE (
  status,
  subscription_plan_id,
  subscription_status,
  subscription_starts_at,
  subscription_ends_at
) ON tenants_gym TO gym_runtime;

GRANT UPDATE (
  name,
  status,
  price,
  billing_interval,
  updated_at
) ON tenants_subscription_plan TO gym_runtime;

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
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'tenant gym updates cannot change code or created_at'
      USING ERRCODE = '42501';
  END IF;
  IF NULLIF(current_setting('app.platform_admin', true), '') = '1' THEN
    RETURN NEW;
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status
    OR NEW.subscription_plan_id IS DISTINCT FROM OLD.subscription_plan_id
    OR NEW.subscription_status IS DISTINCT FROM OLD.subscription_status
    OR NEW.subscription_starts_at IS DISTINCT FROM OLD.subscription_starts_at
    OR NEW.subscription_ends_at IS DISTINCT FROM OLD.subscription_ends_at
  THEN
    RAISE EXCEPTION 'tenant gym updates cannot change lifecycle or subscription'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION app_guard_runtime_subscription_plan_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
  IF current_user <> 'gym_runtime' THEN
    RETURN NEW;
  END IF;
  IF NULLIF(current_setting('app.platform_admin', true), '') IS DISTINCT FROM '1' THEN
    RAISE EXCEPTION 'subscription plan updates require platform admin context'
      USING ERRCODE = '42501';
  END IF;
  IF NEW.code IS DISTINCT FROM OLD.code
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  THEN
    RAISE EXCEPTION 'subscription plan code, currency, and created_at are immutable'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_runtime_subscription_plan_update ON tenants_subscription_plan;
CREATE TRIGGER guard_runtime_subscription_plan_update
BEFORE UPDATE ON tenants_subscription_plan
FOR EACH ROW EXECUTE FUNCTION app_guard_runtime_subscription_plan_update();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS guard_runtime_subscription_plan_update ON tenants_subscription_plan;
DROP FUNCTION IF EXISTS app_guard_runtime_subscription_plan_update();

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

REVOKE UPDATE (
  name,
  status,
  price,
  billing_interval,
  updated_at
) ON tenants_subscription_plan FROM gym_runtime;

REVOKE UPDATE (
  status,
  subscription_plan_id,
  subscription_status,
  subscription_starts_at,
  subscription_ends_at
) ON tenants_gym FROM gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [("tenants", "0037_printer_settings")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
