from django.db import migrations

FORWARD_SQL = """
GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
SET LOCAL ROLE gym_auth_bootstrap;
CREATE OR REPLACE FUNCTION app_protect_owner_role_permissions()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
DECLARE
  role_key text;
  template_key text;
  permission_code text;
  on_template boolean;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'role_guard: role permissions are replaced, not updated';
  END IF;

  IF TG_OP = 'DELETE' THEN
    SELECT r.key, t.key, p.code
      INTO role_key, template_key, permission_code
    FROM public.tenants_role r
    LEFT JOIN public.tenants_system_role_template t ON t.id = r.source_template_id
    LEFT JOIN public.tenants_permission p ON p.id = OLD.permission_id
    WHERE r.id = OLD.role_id AND r.gym_id = OLD.gym_id;
    IF role_key = 'OWNER' THEN
      RAISE EXCEPTION 'role_guard: OWNER role permissions are immutable';
    END IF;
    IF role_key = 'MANAGER'
       AND template_key = 'MANAGER'
       AND permission_code IN (
         'payments.approve',
         'expenses.approve',
         'retail.approve',
         'pt.sales.approve',
         'salaries.approve'
       ) THEN
      RAISE EXCEPTION 'role_guard: protected MANAGER reserved approvals cannot be removed';
    END IF;
    RETURN OLD;
  END IF;

  SELECT r.key, t.key, p.code
    INTO role_key, template_key, permission_code
  FROM public.tenants_role r
  LEFT JOIN public.tenants_system_role_template t ON t.id = r.source_template_id
  LEFT JOIN public.tenants_permission p ON p.id = NEW.permission_id
  WHERE r.id = NEW.role_id AND r.gym_id = NEW.gym_id;

  IF permission_code LIKE 'platform.%' THEN
    RAISE EXCEPTION 'role_guard: platform permissions cannot be assigned to a tenant role';
  END IF;

  IF role_key = 'OWNER' THEN
    SELECT EXISTS (
      SELECT 1
      FROM public.tenants_system_role_template_permission tp
      JOIN public.tenants_system_role_template owner_template
        ON owner_template.id = tp.system_role_template_id
      WHERE owner_template.key = 'OWNER'
        AND tp.permission_id = NEW.permission_id
    ) INTO on_template;
    IF template_key IS DISTINCT FROM 'OWNER' OR NOT COALESCE(on_template, false) THEN
      RAISE EXCEPTION 'role_guard: OWNER role permissions are immutable';
    END IF;
    RETURN NEW;
  END IF;

  IF permission_code = 'settings.manage' THEN
    RAISE EXCEPTION 'role_guard: settings.manage is reserved to OWNER';
  END IF;

  IF permission_code IN (
       'payments.approve',
       'expenses.approve',
       'retail.approve',
       'pt.sales.approve',
       'salaries.approve'
     )
     AND (
       role_key IS DISTINCT FROM 'MANAGER'
       OR template_key IS DISTINCT FROM 'MANAGER'
     ) THEN
    RAISE EXCEPTION 'role_guard: hard-reserved approvals belong only to protected OWNER or MANAGER';
  END IF;

  RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION app_protect_owner_role_permissions() FROM PUBLIC;
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;
"""

REVERSE_SQL = """
GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
SET LOCAL ROLE gym_auth_bootstrap;
CREATE OR REPLACE FUNCTION app_protect_owner_role_permissions()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
DECLARE
  role_key text;
  template_key text;
  permission_code text;
  on_template boolean;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'role_guard: role permissions are replaced, not updated';
  END IF;

  IF TG_OP = 'DELETE' THEN
    SELECT r.key, t.key, p.code
      INTO role_key, template_key, permission_code
    FROM public.tenants_role r
    LEFT JOIN public.tenants_system_role_template t ON t.id = r.source_template_id
    LEFT JOIN public.tenants_permission p ON p.id = OLD.permission_id
    WHERE r.id = OLD.role_id AND r.gym_id = OLD.gym_id;
    IF role_key = 'OWNER' THEN
      RAISE EXCEPTION 'role_guard: OWNER role permissions are immutable';
    END IF;
    IF role_key = 'MANAGER'
       AND template_key = 'MANAGER'
       AND permission_code IN (
         'payments.approve',
         'expenses.approve',
         'retail.approve',
         'pt.sales.approve',
         'salaries.approve'
       ) THEN
      RAISE EXCEPTION 'role_guard: protected MANAGER reserved approvals cannot be removed';
    END IF;
    RETURN OLD;
  END IF;

  SELECT r.key, t.key, p.code
    INTO role_key, template_key, permission_code
  FROM public.tenants_role r
  LEFT JOIN public.tenants_system_role_template t ON t.id = r.source_template_id
  LEFT JOIN public.tenants_permission p ON p.id = NEW.permission_id
  WHERE r.id = NEW.role_id AND r.gym_id = NEW.gym_id;

  IF permission_code LIKE 'platform.%' THEN
    RAISE EXCEPTION 'role_guard: platform permissions cannot be assigned to a tenant role';
  END IF;

  IF role_key = 'OWNER' THEN
    SELECT EXISTS (
      SELECT 1
      FROM public.tenants_system_role_template_permission tp
      JOIN public.tenants_system_role_template owner_template
        ON owner_template.id = tp.system_role_template_id
      WHERE owner_template.key = 'OWNER'
        AND tp.permission_id = NEW.permission_id
    ) INTO on_template;
    IF template_key IS DISTINCT FROM 'OWNER' OR NOT COALESCE(on_template, false) THEN
      RAISE EXCEPTION 'role_guard: OWNER role permissions are immutable';
    END IF;
    RETURN NEW;
  END IF;

  IF permission_code = 'settings.manage' THEN
    RAISE EXCEPTION 'role_guard: settings.manage is reserved to OWNER';
  END IF;

  IF permission_code IN (
       'payments.approve',
       'expenses.approve',
       'retail.approve',
       'pt.sales.approve',
       'salaries.approve'
     )
     AND NOT (role_key = 'MANAGER' AND template_key = 'MANAGER') THEN
    RAISE EXCEPTION 'role_guard: hard-reserved approvals belong only to protected OWNER or MANAGER';
  END IF;

  RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION app_protect_owner_role_permissions() FROM PUBLIC;
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0018_role_permission_reservation_guard"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
