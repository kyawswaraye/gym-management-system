from django.db import migrations

# Member Google sign-in looks up a contact email before any user is linked.
# gym_runtime cannot read members_member across gyms, so this function returns
# only ids, status, and whether the gym can host a session. It does not return
# the email, name, or phone. gym_auth_bootstrap gains SELECT on email and no
# other new column.

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_member_email_candidates(p_email text)
RETURNS TABLE (
  member_id uuid,
  gym_id uuid,
  status text,
  linked_user_id uuid,
  authz_version integer,
  gym_eligible boolean
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
BEGIN
  IF p_email IS NULL
     OR p_email IS DISTINCT FROM lower(btrim(p_email))
     OR p_email ~ '[[:space:][:upper:]]'
     OR length(p_email) > 254
     OR length(p_email) < 3
     OR length(p_email) - length(replace(p_email, '@', '')) <> 1
  THEN
    RETURN;
  END IF;
  RETURN QUERY
    SELECT m.id,
           m.gym_id,
           m.status::text,
           m.linked_user_id,
           m.authz_version,
           (g.status = 'ACTIVE' AND g.subscription_status IN ('TRIAL', 'ACTIVE'))
    FROM public.members_member m
    JOIN public.tenants_gym g ON g.id = m.gym_id
    WHERE m.email IS NOT NULL
      AND m.email = p_email
    ORDER BY m.id;
END;
$$;

REVOKE ALL ON FUNCTION app_member_email_candidates(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_member_email_candidates(text) TO gym_runtime;

GRANT USAGE, CREATE ON SCHEMA public TO gym_auth_bootstrap;
ALTER FUNCTION app_member_email_candidates(text) OWNER TO gym_auth_bootstrap;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;

GRANT SELECT (email) ON public.members_member TO gym_auth_bootstrap;
"""

REVERSE_SQL = r"""
REVOKE SELECT (email) ON public.members_member FROM gym_auth_bootstrap;
DROP FUNCTION IF EXISTS app_member_email_candidates(text);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0035_subscription_currency_catalog"),
        ("members", "0026_member_device_rls_and_guards"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
