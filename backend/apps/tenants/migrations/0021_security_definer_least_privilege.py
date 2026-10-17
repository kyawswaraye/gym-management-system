from django.db import migrations

# Idempotent lock for fresh installs and databases that already applied the
# earlier function bodies. Reversing keeps the same lock: returning these
# functions to gym_migration makes row_security=off fail under forced RLS.
LOCK_SQL = """
GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;

DO $$
DECLARE
  fn regprocedure;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'public.app_staff_memberships()'::regprocedure,
    'public.app_owner_invitation(text)'::regprocedure,
    'public.app_staff_invitation(text)'::regprocedure,
    'public.enforce_invitation_role_owner_boundary()'::regprocedure,
    'public.app_protect_owner_role_permissions()'::regprocedure,
    'public.app_protect_membership_role_owner()'::regprocedure
  ]
  LOOP
    IF pg_get_userbyid((SELECT proowner FROM pg_proc WHERE oid = fn))
       IS DISTINCT FROM 'gym_auth_bootstrap' THEN
      EXECUTE format('ALTER FUNCTION %s OWNER TO gym_auth_bootstrap', fn);
    END IF;
  END LOOP;
END $$;

REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM gym_auth_bootstrap;
GRANT SELECT ON
  public.tenants_gym,
  public.tenants_gym_membership,
  public.tenants_staff_invitation,
  public.tenants_staff_invitation_role,
  public.tenants_role,
  public.tenants_permission,
  public.tenants_system_role_template,
  public.tenants_system_role_template_permission
TO gym_auth_bootstrap;

SET LOCAL ROLE gym_auth_bootstrap;
REVOKE ALL ON FUNCTION public.app_staff_memberships() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_owner_invitation(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_staff_invitation(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.enforce_invitation_role_owner_boundary() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_protect_owner_role_permissions() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_protect_membership_role_owner() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.enforce_invitation_role_owner_boundary() FROM gym_runtime;
REVOKE ALL ON FUNCTION public.app_protect_owner_role_permissions() FROM gym_runtime;
REVOKE ALL ON FUNCTION public.app_protect_membership_role_owner() FROM gym_runtime;
GRANT EXECUTE ON FUNCTION public.app_staff_memberships() TO gym_runtime;
GRANT EXECUTE ON FUNCTION public.app_owner_invitation(text) TO gym_runtime;
GRANT EXECUTE ON FUNCTION public.app_staff_invitation(text) TO gym_runtime;
RESET ROLE;

DO $$
DECLARE
  fn text;
  direct text[] := ARRAY[
    'public.app_staff_memberships()',
    'public.app_owner_invitation(text)',
    'public.app_staff_invitation(text)'
  ];
  triggers text[] := ARRAY[
    'public.enforce_invitation_role_owner_boundary()',
    'public.app_protect_owner_role_permissions()',
    'public.app_protect_membership_role_owner()'
  ];
  allowed text[] := ARRAY[
    'tenants_gym',
    'tenants_gym_membership',
    'tenants_staff_invitation',
    'tenants_staff_invitation_role',
    'tenants_role',
    'tenants_permission',
    'tenants_system_role_template',
    'tenants_system_role_template_permission'
  ];
  extra text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname = 'gym_auth_bootstrap'
      AND NOT rolsuper
      AND rolbypassrls
      AND NOT rolcanlogin
      AND NOT rolinherit
  ) THEN
    RAISE EXCEPTION 'gym_auth_bootstrap must be NOLOGIN NOINHERIT BYPASSRLS';
  END IF;

  FOREACH fn IN ARRAY direct || triggers LOOP
    IF pg_get_userbyid((SELECT proowner FROM pg_proc WHERE oid = fn::regprocedure))
       IS DISTINCT FROM 'gym_auth_bootstrap' THEN
      RAISE EXCEPTION '% is not owned by gym_auth_bootstrap', fn;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_proc
      WHERE oid = fn::regprocedure
        AND prosecdef
        AND proconfig @> ARRAY['search_path=pg_catalog, public', 'row_security=off']
    ) THEN
      RAISE EXCEPTION '% is missing locked security-definer configuration', fn;
    END IF;
    IF has_function_privilege('public', fn::regprocedure, 'EXECUTE') THEN
      RAISE EXCEPTION '% is executable by PUBLIC', fn;
    END IF;
  END LOOP;

  FOREACH fn IN ARRAY direct LOOP
    IF NOT has_function_privilege('gym_runtime', fn::regprocedure, 'EXECUTE') THEN
      RAISE EXCEPTION 'gym_runtime cannot execute %', fn;
    END IF;
  END LOOP;

  FOREACH fn IN ARRAY triggers LOOP
    IF has_function_privilege('gym_runtime', fn::regprocedure, 'EXECUTE') THEN
      RAISE EXCEPTION 'trigger function % is directly executable by gym_runtime', fn;
    END IF;
  END LOOP;

  SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
    INTO extra
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public'
    AND c.relkind = 'r'
    AND (
      (
        has_table_privilege('gym_auth_bootstrap', c.oid, 'SELECT')
        AND c.relname <> ALL (allowed)
      )
      OR has_table_privilege('gym_auth_bootstrap', c.oid, 'INSERT')
      OR has_table_privilege('gym_auth_bootstrap', c.oid, 'UPDATE')
      OR has_table_privilege('gym_auth_bootstrap', c.oid, 'DELETE')
    );
  IF extra IS NOT NULL THEN
    RAISE EXCEPTION 'gym_auth_bootstrap has excess table privileges: %', extra;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM unnest(allowed) AS required(relname)
    WHERE NOT has_table_privilege(
      'gym_auth_bootstrap',
      format('public.%I', required.relname),
      'SELECT'
    )
  ) THEN
    RAISE EXCEPTION 'gym_auth_bootstrap is missing a required SELECT grant';
  END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0020_retire_platform_settings_manage"),
    ]

    operations = [
        migrations.RunSQL(LOCK_SQL, LOCK_SQL),
    ]
