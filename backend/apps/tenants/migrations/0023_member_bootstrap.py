from django.db import migrations

# After global user validation, runtime may ask this function which active
# member records are linked to app.user_id. It returns only member id, gym id,
# and authz version. It does not return a name, phone, email, or note. The
# caller then sets gym context and reloads that member under forced RLS.
# gym_auth_bootstrap can read only the columns this function needs.

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_linked_members()
RETURNS TABLE (member_id uuid, gym_id uuid, authz_version integer)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
BEGIN
  RETURN QUERY
  SELECT m.id, m.gym_id, m.authz_version
  FROM public.members_member m
  JOIN public.tenants_gym g ON g.id = m.gym_id
  WHERE m.linked_user_id = (
      SELECT CASE
        WHEN current_setting('app.user_id', true)
             ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        THEN current_setting('app.user_id', true)::uuid
        ELSE NULL
      END
    )
    AND m.status = 'ACTIVE'
    AND g.status = 'ACTIVE'
    AND g.subscription_status IN ('TRIAL', 'ACTIVE')
  ORDER BY m.id;
END;
$$;

REVOKE ALL ON FUNCTION app_linked_members() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_linked_members() TO gym_runtime;

GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
ALTER FUNCTION app_linked_members() OWNER TO gym_auth_bootstrap;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;

GRANT SELECT (id, gym_id, linked_user_id, status, authz_version)
  ON public.members_member TO gym_auth_bootstrap;
"""

REVERSE_SQL = r"""
REVOKE SELECT (id, gym_id, linked_user_id, status, authz_version)
  ON public.members_member FROM gym_auth_bootstrap;
DROP FUNCTION IF EXISTS app_linked_members();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0022_check_in_challenge_permission"),
        ("members", "0007_member_profile_foundation"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
