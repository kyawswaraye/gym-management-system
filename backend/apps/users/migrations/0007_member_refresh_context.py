from django.db import migrations, models


# MEMBER refresh rows already require selected_gym and member_id. This adds the
# composite same-gym foreign key Django cannot express, the partial lookup
# index, and a trigger that keeps a replacement in the same family, user,
# principal, and selected context. Staff and platform rows keep null member_id,
# so MATCH SIMPLE does not apply the member foreign key to them.

FORWARD_SQL = r"""
ALTER TABLE users_refresh_session
  ADD CONSTRAINT refresh_member_same_gym_fk
  FOREIGN KEY (selected_gym_id, member_id)
  REFERENCES members_member (gym_id, id)
  ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION app_refresh_replacement_same_context()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $body$
DECLARE
  replacement public.users_refresh_session%ROWTYPE;
BEGIN
  IF NEW.replaced_by_session_id IS NULL
     OR NEW.replaced_by_session_id IS NOT DISTINCT FROM OLD.replaced_by_session_id THEN
    RETURN NEW;
  END IF;
  SELECT * INTO replacement
  FROM public.users_refresh_session
  WHERE id = NEW.replaced_by_session_id;
  IF NOT FOUND
     OR replacement.family_id IS DISTINCT FROM NEW.family_id
     OR replacement.user_id IS DISTINCT FROM NEW.user_id
     OR replacement.principal_type IS DISTINCT FROM NEW.principal_type
     OR replacement.selected_gym_id IS DISTINCT FROM NEW.selected_gym_id
     OR replacement.gym_membership_id IS DISTINCT FROM NEW.gym_membership_id
     OR replacement.member_id IS DISTINCT FROM NEW.member_id THEN
    RAISE EXCEPTION 'refresh replacement context mismatch'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$body$;

REVOKE ALL ON FUNCTION app_refresh_replacement_same_context() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_refresh_replacement_same_context() TO gym_runtime;

DROP TRIGGER IF EXISTS refresh_replacement_context_trg ON users_refresh_session;
CREATE TRIGGER refresh_replacement_context_trg
  BEFORE UPDATE OF replaced_by_session_id ON users_refresh_session
  FOR EACH ROW
  EXECUTE FUNCTION app_refresh_replacement_same_context();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS refresh_replacement_context_trg ON users_refresh_session;
DROP FUNCTION IF EXISTS app_refresh_replacement_same_context();
ALTER TABLE users_refresh_session
  DROP CONSTRAINT IF EXISTS refresh_member_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_runtime_identity_least_privilege"),
        ("members", "0007_member_profile_foundation"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="refreshsession",
            index=models.Index(
                condition=models.Q(("member_id__isnull", False)),
                fields=["selected_gym", "member_id"],
                name="refresh_member_ctx_idx",
            ),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
