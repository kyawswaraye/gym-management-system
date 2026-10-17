from django.db import migrations

# gym_runtime must not insert an arbitrary users_user row or reset a password.
# Invitation creation and the one unusable-to-usable acceptance transition go
# through table-owner security-definer functions. Direct runtime inserts are
# revoked, and a non-definer trigger still rejects a bad row if INSERT is
# restored. The trigger is not security-definer, so current_user stays the
# statement user. These functions keep row_security on and are not owned by
# gym_auth_bootstrap. The password transition accepts only Django's current
# PBKDF2PasswordHasher encoding.

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_runtime_user_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $guard$
BEGIN
  IF current_user IS DISTINCT FROM 'gym_runtime' THEN
    RETURN NEW;
  END IF;
  IF NEW.is_active IS NOT TRUE
     OR NEW.is_platform_admin IS NOT FALSE
     OR NEW.is_superuser IS NOT FALSE
     OR NEW.mfa_enabled IS NOT FALSE
     OR NEW.mfa_secret_encrypted IS NOT NULL
     OR NEW.mfa_last_used_step IS NOT NULL
     OR NEW.last_login IS NOT NULL
     OR NEW.last_login_at IS NOT NULL
     OR NEW.password IS NULL
     OR left(NEW.password, 1) IS DISTINCT FROM '!'
     OR length(NEW.password) < 2
     OR length(NEW.password) > 128
     OR NEW.email IS NULL
     OR NEW.normalized_email IS NULL
     OR NEW.email IS DISTINCT FROM lower(btrim(NEW.email))
     OR NEW.normalized_email IS DISTINCT FROM NEW.email
     OR NEW.email ~ '[[:space:]]'
     OR length(NEW.email) > 254
     OR length(NEW.email) < 3
     OR length(NEW.email) - length(replace(NEW.email, '@', '')) <> 1
     OR left(NEW.email, 1) = '@'
     OR right(NEW.email, 1) = '@'
     OR NEW.first_name IS NULL
     OR NEW.last_name IS NULL
     OR NEW.phone IS NULL
     OR NEW.first_name IS DISTINCT FROM btrim(NEW.first_name)
     OR NEW.last_name IS DISTINCT FROM btrim(NEW.last_name)
     OR NEW.phone IS DISTINCT FROM btrim(NEW.phone)
     OR length(NEW.first_name) > 150
     OR length(NEW.last_name) > 150
     OR length(NEW.phone) > 32
  THEN
    RAISE EXCEPTION 'runtime can insert only an ordinary invited identity'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_guard_runtime_user_update()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $guard$
BEGIN
  IF current_user IS DISTINCT FROM 'gym_runtime' THEN
    RETURN NEW;
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.password IS DISTINCT FROM OLD.password
     OR NEW.password_changed_at IS DISTINCT FROM OLD.password_changed_at
     OR NEW.last_login IS DISTINCT FROM OLD.last_login
     OR NEW.is_superuser IS DISTINCT FROM OLD.is_superuser
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.email IS DISTINCT FROM OLD.email
     OR NEW.normalized_email IS DISTINCT FROM OLD.normalized_email
     OR NEW.first_name IS DISTINCT FROM OLD.first_name
     OR NEW.last_name IS DISTINCT FROM OLD.last_name
     OR NEW.phone IS DISTINCT FROM OLD.phone
     OR NEW.is_active IS DISTINCT FROM OLD.is_active
     OR NEW.is_platform_admin IS DISTINCT FROM OLD.is_platform_admin
     OR NEW.last_login_at IS DISTINCT FROM OLD.last_login_at
  THEN
    RAISE EXCEPTION 'runtime cannot change identity, password, or privilege columns'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_create_invited_identity(
  p_email text,
  p_first_name text,
  p_last_name text,
  p_phone text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = on
AS $fn$
DECLARE
  created_id uuid;
  stamp timestamptz;
BEGIN
  IF p_email IS NULL
     OR p_first_name IS NULL
     OR p_last_name IS NULL
     OR p_phone IS NULL
     OR p_email IS DISTINCT FROM lower(btrim(p_email))
     OR p_email ~ '[[:space:]]'
     OR length(p_email) > 254
     OR length(p_email) < 3
     OR length(p_email) - length(replace(p_email, '@', '')) <> 1
     OR left(p_email, 1) = '@'
     OR right(p_email, 1) = '@'
     OR p_first_name IS DISTINCT FROM btrim(p_first_name)
     OR p_last_name IS DISTINCT FROM btrim(p_last_name)
     OR p_phone IS DISTINCT FROM btrim(p_phone)
     OR length(p_first_name) > 150
     OR length(p_last_name) > 150
     OR length(p_phone) > 32
  THEN
    RAISE EXCEPTION 'invited identity requires a normalized email'
      USING ERRCODE = '23514';
  END IF;
  stamp := clock_timestamp();
  INSERT INTO public.users_user (
    id,
    password,
    last_login,
    is_superuser,
    created_at,
    updated_at,
    email,
    normalized_email,
    first_name,
    last_name,
    phone,
    is_active,
    is_platform_admin,
    mfa_enabled,
    mfa_secret_encrypted,
    mfa_last_used_step,
    last_login_at,
    password_changed_at
  ) VALUES (
    gen_random_uuid(),
    '!' || replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', ''),
    NULL,
    false,
    stamp,
    stamp,
    p_email,
    p_email,
    p_first_name,
    p_last_name,
    p_phone,
    true,
    false,
    false,
    NULL,
    NULL,
    NULL,
    stamp
  )
  RETURNING id INTO created_id;
  RETURN created_id;
END;
$fn$;

CREATE OR REPLACE FUNCTION app_accept_invitation_password(
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
  gym_id uuid;
  invitation_gym uuid;
  invitation_status text;
  invitation_expires timestamptz;
  invitation_membership uuid;
  invitation_accepted timestamptz;
  invitation_revoked timestamptz;
  member_user uuid;
  member_gym uuid;
  identity_active boolean;
  identity_platform boolean;
  identity_super boolean;
  identity_mfa boolean;
  identity_password text;
BEGIN
  BEGIN
    gym_id := NULLIF(current_setting('app.gym_id', true), '')::uuid;
  EXCEPTION
    WHEN invalid_text_representation THEN
      RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END;
  -- Django PBKDF2PasswordHasher: pbkdf2_sha256, at least 1000000 iterations,
  -- a 22-character alphanumeric salt, and a 44-character SHA-256 digest.
  IF gym_id IS NULL
     OR p_token_hash IS NULL
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
    RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END IF;

  SELECT i.gym_id, i.status, i.expires_at, i.gym_membership_id, i.accepted_at, i.revoked_at
    INTO invitation_gym, invitation_status, invitation_expires, invitation_membership,
         invitation_accepted, invitation_revoked
  FROM public.tenants_staff_invitation i
  WHERE i.token_hash = p_token_hash
  FOR UPDATE;
  IF NOT FOUND
     OR invitation_gym IS DISTINCT FROM gym_id
     OR invitation_status IS DISTINCT FROM 'PENDING'
     OR invitation_expires <= clock_timestamp()
     OR invitation_accepted IS NOT NULL
     OR invitation_revoked IS NOT NULL
  THEN
    RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END IF;

  SELECT m.user_id, m.gym_id
    INTO member_user, member_gym
  FROM public.tenants_gym_membership m
  WHERE m.id = invitation_membership
    AND m.gym_id = invitation_gym
  FOR UPDATE;
  IF NOT FOUND OR member_gym IS DISTINCT FROM gym_id OR member_user IS NULL THEN
    RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END IF;

  SELECT u.is_active, u.is_platform_admin, u.is_superuser, u.mfa_enabled, u.password
    INTO identity_active, identity_platform, identity_super, identity_mfa, identity_password
  FROM public.users_user u
  WHERE u.id = member_user
  FOR UPDATE;
  IF NOT FOUND
     OR identity_active IS NOT TRUE
     OR identity_platform IS NOT FALSE
     OR identity_super IS NOT FALSE
     OR identity_mfa IS NOT FALSE
     OR identity_password IS NULL
     OR left(identity_password, 1) IS DISTINCT FROM '!'
  THEN
    RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END IF;

  UPDATE public.users_user
  SET password = p_password_hash,
      password_changed_at = p_changed_at,
      updated_at = p_changed_at
  WHERE id = member_user
    AND is_active IS TRUE
    AND is_platform_admin IS FALSE
    AND is_superuser IS FALSE
    AND mfa_enabled IS FALSE
    AND left(password, 1) = '!';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invitation password transition denied' USING ERRCODE = '42501';
  END IF;
  RETURN true;
END;
$fn$;

CREATE OR REPLACE FUNCTION app_confirm_invited_profile(
  p_token_hash text,
  p_first_name text,
  p_last_name text,
  p_phone text,
  p_changed_at timestamptz
) RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = on
AS $fn$
DECLARE
  gym_id uuid;
  invitation_gym uuid;
  invitation_status text;
  invitation_expires timestamptz;
  invitation_membership uuid;
  invitation_accepted timestamptz;
  invitation_revoked timestamptz;
  member_user uuid;
  member_gym uuid;
  identity_active boolean;
  identity_platform boolean;
  identity_super boolean;
BEGIN
  BEGIN
    gym_id := NULLIF(current_setting('app.gym_id', true), '')::uuid;
  EXCEPTION
    WHEN invalid_text_representation THEN
      RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END;
  IF gym_id IS NULL
     OR p_token_hash IS NULL
     OR p_token_hash !~ '^[0-9a-f]{64}$'
     OR p_changed_at IS NULL
     OR p_changed_at > clock_timestamp() + interval '2 minutes'
     OR p_changed_at < clock_timestamp() - interval '5 minutes'
     OR p_first_name IS NULL
     OR p_last_name IS NULL
     OR p_phone IS NULL
     OR p_first_name IS DISTINCT FROM btrim(p_first_name)
     OR p_last_name IS DISTINCT FROM btrim(p_last_name)
     OR p_phone IS DISTINCT FROM btrim(p_phone)
     OR length(p_first_name) > 150
     OR length(p_last_name) > 150
     OR length(p_phone) > 32
  THEN
    RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END IF;

  SELECT i.gym_id, i.status, i.expires_at, i.gym_membership_id, i.accepted_at, i.revoked_at
    INTO invitation_gym, invitation_status, invitation_expires, invitation_membership,
         invitation_accepted, invitation_revoked
  FROM public.tenants_staff_invitation i
  WHERE i.token_hash = p_token_hash
  FOR UPDATE;
  IF NOT FOUND
     OR invitation_gym IS DISTINCT FROM gym_id
     OR invitation_status IS DISTINCT FROM 'PENDING'
     OR invitation_expires <= clock_timestamp()
     OR invitation_accepted IS NOT NULL
     OR invitation_revoked IS NOT NULL
  THEN
    RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END IF;

  SELECT m.user_id, m.gym_id
    INTO member_user, member_gym
  FROM public.tenants_gym_membership m
  WHERE m.id = invitation_membership
    AND m.gym_id = invitation_gym
  FOR UPDATE;
  IF NOT FOUND OR member_gym IS DISTINCT FROM gym_id OR member_user IS NULL THEN
    RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END IF;

  SELECT u.is_active, u.is_platform_admin, u.is_superuser
    INTO identity_active, identity_platform, identity_super
  FROM public.users_user u
  WHERE u.id = member_user
  FOR UPDATE;
  IF NOT FOUND
     OR identity_active IS NOT TRUE
     OR identity_platform IS NOT FALSE
     OR identity_super IS NOT FALSE
  THEN
    RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END IF;

  UPDATE public.users_user
  SET first_name = p_first_name,
      last_name = p_last_name,
      phone = p_phone,
      updated_at = p_changed_at
  WHERE id = member_user
    AND is_active IS TRUE
    AND is_platform_admin IS FALSE
    AND is_superuser IS FALSE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invited profile confirmation denied' USING ERRCODE = '42501';
  END IF;
  RETURN true;
END;
$fn$;

REVOKE ALL ON FUNCTION app_guard_runtime_user_insert() FROM PUBLIC, gym_runtime, gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_guard_runtime_user_update() FROM PUBLIC, gym_runtime, gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_create_invited_identity(text, text, text, text)
  FROM PUBLIC, gym_runtime, gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_accept_invitation_password(text, text, timestamptz)
  FROM PUBLIC, gym_runtime, gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_confirm_invited_profile(text, text, text, text, timestamptz)
  FROM PUBLIC, gym_runtime, gym_auth_bootstrap;

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
    'ALTER FUNCTION app_create_invited_identity(text, text, text, text) OWNER TO %I',
    table_owner
  );
  EXECUTE format(
    'ALTER FUNCTION app_accept_invitation_password(text, text, timestamptz) OWNER TO %I',
    table_owner
  );
  EXECUTE format(
    'ALTER FUNCTION app_confirm_invited_profile(text, text, text, text, timestamptz) OWNER TO %I',
    table_owner
  );
END $$;

GRANT EXECUTE ON FUNCTION app_create_invited_identity(text, text, text, text) TO gym_runtime;
GRANT EXECUTE ON FUNCTION app_accept_invitation_password(text, text, timestamptz) TO gym_runtime;
GRANT EXECUTE ON FUNCTION app_confirm_invited_profile(text, text, text, text, timestamptz)
  TO gym_runtime;

DROP TRIGGER IF EXISTS guard_runtime_user_insert ON users_user;
DROP TRIGGER IF EXISTS guard_runtime_user_update ON users_user;
CREATE TRIGGER guard_runtime_user_insert
BEFORE INSERT ON users_user
FOR EACH ROW
WHEN (current_user = 'gym_runtime')
EXECUTE FUNCTION app_guard_runtime_user_insert();
CREATE TRIGGER guard_runtime_user_update
BEFORE UPDATE ON users_user
FOR EACH ROW
WHEN (current_user = 'gym_runtime')
EXECUTE FUNCTION app_guard_runtime_user_update();

REVOKE INSERT ON users_user FROM gym_runtime;

DO $$
DECLARE
  fn text;
  identity text[] := ARRAY[
    'public.app_create_invited_identity(text,text,text,text)',
    'public.app_accept_invitation_password(text,text,timestamptz)',
    'public.app_confirm_invited_profile(text,text,text,text,timestamptz)'
  ];
  guards text[] := ARRAY[
    'public.app_guard_runtime_user_insert()',
    'public.app_guard_runtime_user_update()'
  ];
  table_owner name;
BEGIN
  SELECT pg_get_userbyid(c.relowner)
    INTO table_owner
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = 'users_user';

  IF has_table_privilege('gym_runtime', 'users_user', 'INSERT')
     OR has_table_privilege('gym_runtime', 'users_user', 'UPDATE')
     OR has_table_privilege('gym_runtime', 'users_user', 'DELETE')
  THEN
    RAISE EXCEPTION 'gym_runtime retains a broad users_user privilege';
  END IF;
  IF has_column_privilege('gym_runtime', 'users_user', 'password', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'password_changed_at', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_platform_admin', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_superuser', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'is_active', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'email', 'UPDATE')
     OR has_column_privilege('gym_runtime', 'users_user', 'first_name', 'UPDATE')
  THEN
    RAISE EXCEPTION 'gym_runtime can update an identity column directly';
  END IF;

  FOREACH fn IN ARRAY identity LOOP
    IF pg_get_userbyid((SELECT proowner FROM pg_proc WHERE oid = fn::regprocedure))
       IS DISTINCT FROM table_owner
       OR table_owner = 'gym_runtime'
       OR table_owner = 'gym_auth_bootstrap'
    THEN
      RAISE EXCEPTION '% is not owned by the users_user table owner', fn;
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM pg_proc
      WHERE oid = fn::regprocedure
        AND prosecdef
        AND proconfig @> ARRAY['search_path=pg_catalog, public', 'row_security=on']
        AND NOT (proconfig @> ARRAY['row_security=off'])
    ) THEN
      RAISE EXCEPTION '% is missing its locked security-definer configuration', fn;
    END IF;
    IF has_function_privilege('public', fn::regprocedure, 'EXECUTE')
       OR has_function_privilege('gym_auth_bootstrap', fn::regprocedure, 'EXECUTE')
       OR NOT has_function_privilege('gym_runtime', fn::regprocedure, 'EXECUTE')
    THEN
      RAISE EXCEPTION '% has the wrong execute grant', fn;
    END IF;
  END LOOP;

  FOREACH fn IN ARRAY guards LOOP
    IF EXISTS (
      SELECT 1 FROM pg_proc
      WHERE oid = fn::regprocedure AND prosecdef
    ) OR has_function_privilege('public', fn::regprocedure, 'EXECUTE')
       OR has_function_privilege('gym_runtime', fn::regprocedure, 'EXECUTE')
    THEN
      RAISE EXCEPTION 'trigger guard % is executable or security-definer', fn;
    END IF;
  END LOOP;

  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'guard_runtime_user_insert'
      AND tgrelid = 'public.users_user'::regclass
      AND NOT tgisinternal
      AND tgenabled = 'O'
      AND (tgtype & 23) = 7
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname = 'guard_runtime_user_update'
      AND tgrelid = 'public.users_user'::regclass
      AND NOT tgisinternal
      AND tgenabled = 'O'
      AND (tgtype & 23) = 19
  ) THEN
    RAISE EXCEPTION 'users_user runtime guard triggers are missing';
  END IF;
END $$;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_runtime_user_update ON users_user;
DROP TRIGGER IF EXISTS guard_runtime_user_insert ON users_user;
DROP FUNCTION IF EXISTS app_guard_runtime_user_update();
DROP FUNCTION IF EXISTS app_guard_runtime_user_insert();
DROP FUNCTION IF EXISTS app_confirm_invited_profile(text, text, text, text, timestamptz);
DROP FUNCTION IF EXISTS app_accept_invitation_password(text, text, timestamptz);
DROP FUNCTION IF EXISTS app_create_invited_identity(text, text, text, text);
GRANT INSERT ON users_user TO gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_mfa_column_update_privileges"),
        ("tenants", "0010_owner_invitations"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
