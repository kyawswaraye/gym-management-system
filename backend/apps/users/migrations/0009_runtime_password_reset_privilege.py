from django.db import migrations

# gym_runtime cannot UPDATE users_user.password. This function is the only
# password replacement for an already-usable identity. It stays invitation-
# independent, does not change MFA, and does not require gym context.
# Cross-tenant authz bumps use gym_auth_bootstrap so FORCE RLS cannot hide
# memberships in other gyms.

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_accept_password_reset(
  p_token_hash text,
  p_password_hash text,
  p_changed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = on
AS $fn$
DECLARE
  token_user uuid;
  token_expires timestamptz;
  token_consumed timestamptz;
  token_revoked timestamptz;
  identity_active boolean;
BEGIN
  IF p_token_hash IS NULL
     OR p_token_hash !~ '^[0-9a-f]{64}$'
     OR p_changed_at IS NULL
     OR p_changed_at > clock_timestamp() + interval '2 minutes'
     OR p_changed_at < clock_timestamp() - interval '5 minutes'
     OR p_password_hash IS NULL
     OR length(p_password_hash) < 20
     OR length(p_password_hash) > 128
     OR left(p_password_hash, 1) = '!'
     OR p_password_hash ~ '[[:space:]]'
     OR p_password_hash !~ '^pbkdf2_sha256\$[1-9][0-9]{6,}\$[A-Za-z0-9]{22}\$[A-Za-z0-9+/]{43}=$'
  THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;

  SELECT t.user_id, t.expires_at, t.consumed_at, t.revoked_at
    INTO token_user, token_expires, token_consumed, token_revoked
  FROM public.users_password_reset_token t
  WHERE t.token_hash = p_token_hash
  FOR UPDATE;
  IF NOT FOUND
     OR token_consumed IS NOT NULL
     OR token_revoked IS NOT NULL
     OR token_expires <= clock_timestamp()
  THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;

  SELECT u.is_active
    INTO identity_active
  FROM public.users_user u
  WHERE u.id = token_user
  FOR UPDATE;
  IF NOT FOUND OR identity_active IS NOT TRUE THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;

  UPDATE public.users_user
  SET password = p_password_hash,
      password_changed_at = p_changed_at,
      updated_at = p_changed_at
  WHERE id = token_user
    AND is_active IS TRUE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;

  UPDATE public.users_password_reset_token
  SET consumed_at = p_changed_at,
      updated_at = p_changed_at
  WHERE token_hash = p_token_hash
    AND consumed_at IS NULL
    AND revoked_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;
  RETURN true;
END;
$fn$;

CREATE OR REPLACE FUNCTION app_bump_password_reset_authz(p_user_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $fn$
BEGIN
  IF p_user_id IS NULL THEN
    RAISE EXCEPTION 'password reset transition denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.tenants_gym_membership
  SET authz_version = authz_version + 1,
      updated_at = clock_timestamp()
  WHERE user_id = p_user_id;
  UPDATE public.members_member
  SET authz_version = authz_version + 1,
      updated_at = clock_timestamp()
  WHERE linked_user_id = p_user_id;
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION app_accept_password_reset(text, text, timestamptz)
  FROM PUBLIC, gym_runtime, gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_bump_password_reset_authz(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_bump_password_reset_authz(uuid) TO gym_runtime;

DO $$
DECLARE
  table_owner name;
BEGIN
  SELECT pg_get_userbyid(c.relowner)
    INTO table_owner
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = 'users_user';
  EXECUTE format(
    'ALTER FUNCTION app_accept_password_reset(text, text, timestamptz) OWNER TO %I',
    table_owner
  );
END $$;

GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
ALTER FUNCTION app_bump_password_reset_authz(uuid) OWNER TO gym_auth_bootstrap;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;

GRANT EXECUTE ON FUNCTION app_accept_password_reset(text, text, timestamptz) TO gym_runtime;

GRANT UPDATE (authz_version, updated_at) ON public.tenants_gym_membership TO gym_auth_bootstrap;
GRANT UPDATE (authz_version, updated_at) ON public.members_member TO gym_auth_bootstrap;
GRANT SELECT (id, user_id, authz_version, updated_at)
  ON public.tenants_gym_membership TO gym_auth_bootstrap;
GRANT SELECT (id, linked_user_id, authz_version, updated_at)
  ON public.members_member TO gym_auth_bootstrap;

DO $$
DECLARE
  table_owner name;
BEGIN
  SELECT pg_get_userbyid(c.relowner)
    INTO table_owner
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = 'users_user';

  IF pg_get_userbyid((
       SELECT proowner FROM pg_proc
       WHERE oid = 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure
     ))
     IS DISTINCT FROM table_owner
     OR table_owner IN ('gym_runtime', 'gym_auth_bootstrap')
  THEN
    RAISE EXCEPTION 'app_accept_password_reset is not owned by the users_user table owner';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc
    WHERE oid = 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure
      AND prosecdef
      AND proconfig @> ARRAY['search_path=pg_catalog, public', 'row_security=on']
      AND NOT (proconfig @> ARRAY['row_security=off'])
  ) THEN
    RAISE EXCEPTION 'app_accept_password_reset is missing its locked security-definer configuration';
  END IF;
  IF has_function_privilege('public', 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure, 'EXECUTE')
     OR has_function_privilege('gym_auth_bootstrap', 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure, 'EXECUTE')
     OR NOT has_function_privilege('gym_runtime', 'public.app_accept_password_reset(text,text,timestamptz)'::regprocedure, 'EXECUTE')
  THEN
    RAISE EXCEPTION 'app_accept_password_reset has the wrong execute grant';
  END IF;

  IF has_column_privilege('gym_runtime', 'users_user', 'password', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'password_changed_at', 'UPDATE')
  THEN
    RAISE EXCEPTION 'gym_runtime can update a password column directly';
  END IF;
END $$;
"""

REVERSE_SQL = r"""
REVOKE UPDATE (authz_version, updated_at) ON public.tenants_gym_membership FROM gym_auth_bootstrap;
REVOKE UPDATE (authz_version, updated_at) ON public.members_member FROM gym_auth_bootstrap;
DROP FUNCTION IF EXISTS app_bump_password_reset_authz(uuid);
DROP FUNCTION IF EXISTS app_accept_password_reset(text, text, timestamptz);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0008_password_reset_token"),
        ("members", "0021_retail_product_refund"),
        ("tenants", "0023_member_bootstrap"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
