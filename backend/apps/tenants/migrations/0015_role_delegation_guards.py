from django.db import migrations, models


def backfill_normalized_role_names(apps, schema_editor):
    schema_editor.execute(
        """
        UPDATE tenants_role
        SET normalized_name = upper(
          regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g')
        )
        """
    )


FORWARD_SQL = """
GRANT DELETE ON tenants_role_permission, tenants_gym_membership_role TO gym_runtime;

ALTER TABLE tenants_role
  ADD CONSTRAINT role_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE tenants_role_permission
  ADD CONSTRAINT roleperm_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE tenants_gym_membership_role
  ADD CONSTRAINT memberrole_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION app_protect_role_identity()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id THEN
    RAISE EXCEPTION 'role_guard: gym is immutable';
  END IF;
  IF NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id THEN
    RAISE EXCEPTION 'role_guard: created by is immutable';
  END IF;
  IF NEW.source_template_id IS DISTINCT FROM OLD.source_template_id
     OR NEW.source_template_version IS DISTINCT FROM OLD.source_template_version THEN
    RAISE EXCEPTION 'role_guard: source template is immutable';
  END IF;
  IF NEW.key = 'OWNER' AND OLD.key IS DISTINCT FROM 'OWNER' THEN
    RAISE EXCEPTION 'role_guard: role cannot become OWNER';
  END IF;
  IF OLD.key = 'OWNER' OR OLD.source_template_id IS NOT NULL THEN
    IF NEW.key IS DISTINCT FROM OLD.key
       OR NEW.name IS DISTINCT FROM OLD.name
       OR NEW.normalized_name IS DISTINCT FROM OLD.normalized_name
       OR NEW.is_active IS DISTINCT FROM OLD.is_active THEN
      RAISE EXCEPTION 'role_guard: protected role identity is immutable';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

GRANT SELECT ON public.tenants_role,
  public.tenants_system_role_template,
  public.tenants_system_role_template_permission
TO gym_auth_bootstrap;
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
  on_template boolean;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'role_guard: role permissions are replaced, not updated';
  END IF;
  IF TG_OP = 'DELETE' THEN
    SELECT r.key INTO role_key
    FROM public.tenants_role r
    WHERE r.id = OLD.role_id AND r.gym_id = OLD.gym_id;
    IF role_key = 'OWNER' THEN
      RAISE EXCEPTION 'role_guard: OWNER role permissions are immutable';
    END IF;
    RETURN OLD;
  END IF;
  SELECT r.key, t.key INTO role_key, template_key
  FROM public.tenants_role r
  LEFT JOIN public.tenants_system_role_template t ON t.id = r.source_template_id
  WHERE r.id = NEW.role_id AND r.gym_id = NEW.gym_id;
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
  END IF;
  RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION app_protect_membership_role_owner()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
SET row_security = off
AS $$
DECLARE
  role_key text;
  owner_membership boolean;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'role_guard: membership roles are replaced, not updated';
  END IF;
  IF TG_OP = 'DELETE' THEN
    SELECT r.key INTO role_key
    FROM public.tenants_role r
    WHERE r.id = OLD.role_id AND r.gym_id = OLD.gym_id;
    IF role_key = 'OWNER' THEN
      RAISE EXCEPTION 'role_guard: OWNER role assignment cannot be removed';
    END IF;
    RETURN OLD;
  END IF;
  SELECT r.key INTO role_key
  FROM public.tenants_role r
  WHERE r.id = NEW.role_id AND r.gym_id = NEW.gym_id;
  SELECT m.is_owner INTO owner_membership
  FROM public.tenants_gym_membership m
  WHERE m.id = NEW.gym_membership_id AND m.gym_id = NEW.gym_id;
  IF role_key = 'OWNER' AND owner_membership IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'role_guard: OWNER role can be assigned only to an owner membership';
  END IF;
  IF owner_membership IS TRUE AND role_key IS DISTINCT FROM 'OWNER' THEN
    RAISE EXCEPTION 'role_guard: owner memberships can hold only the OWNER role';
  END IF;
  RETURN NEW;
END
$$;
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;

CREATE TRIGGER role_identity_guard
BEFORE UPDATE ON tenants_role
FOR EACH ROW EXECUTE FUNCTION app_protect_role_identity();
CREATE TRIGGER role_permission_owner_guard
BEFORE INSERT OR UPDATE OR DELETE ON tenants_role_permission
FOR EACH ROW EXECUTE FUNCTION app_protect_owner_role_permissions();
CREATE TRIGGER membership_role_owner_guard
BEFORE INSERT OR UPDATE OR DELETE ON tenants_gym_membership_role
FOR EACH ROW EXECUTE FUNCTION app_protect_membership_role_owner();

REVOKE ALL ON FUNCTION app_protect_role_identity() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_protect_role_identity() TO gym_runtime;
SET LOCAL ROLE gym_auth_bootstrap;
REVOKE ALL ON FUNCTION app_protect_owner_role_permissions() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_protect_membership_role_owner() FROM PUBLIC;
RESET ROLE;
"""

REVERSE_SQL = """
REVOKE ALL ON FUNCTION app_protect_role_identity() FROM gym_runtime;
DROP TRIGGER IF EXISTS membership_role_owner_guard ON tenants_gym_membership_role;
DROP TRIGGER IF EXISTS role_permission_owner_guard ON tenants_role_permission;
DROP TRIGGER IF EXISTS role_identity_guard ON tenants_role;
DROP FUNCTION IF EXISTS app_protect_role_identity();
SET LOCAL ROLE gym_auth_bootstrap;
DROP FUNCTION IF EXISTS app_protect_membership_role_owner();
DROP FUNCTION IF EXISTS app_protect_owner_role_permissions();
RESET ROLE;
REVOKE SELECT ON public.tenants_system_role_template,
  public.tenants_system_role_template_permission
FROM gym_auth_bootstrap;

ALTER TABLE tenants_gym_membership_role
  DROP CONSTRAINT IF EXISTS memberrole_created_by_same_gym_fk;
ALTER TABLE tenants_role_permission
  DROP CONSTRAINT IF EXISTS roleperm_created_by_same_gym_fk;
ALTER TABLE tenants_role
  DROP CONSTRAINT IF EXISTS role_created_by_same_gym_fk;
REVOKE DELETE ON tenants_role_permission, tenants_gym_membership_role FROM gym_runtime;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0014_staff_profile_name_cursor_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="normalized_name",
            field=models.CharField(default="", editable=False, max_length=100),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_normalized_role_names, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="role",
            index=models.Index(fields=["gym", "key", "id"], name="role_gym_key_cursor_idx"),
        ),
        migrations.AddConstraint(
            model_name="role",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("gym", "normalized_name"),
                name="role_active_name_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="role",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("key", ""), _negated=True),
                    models.Q(("name", ""), _negated=True),
                    models.Q(("normalized_name", ""), _negated=True),
                ),
                name="role_identity_nonblank_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="role",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("source_template__isnull", True),
                        ("source_template_version__isnull", True),
                    ),
                    models.Q(
                        ("source_template__isnull", False),
                        ("source_template_version__gt", 0),
                    ),
                    _connector="OR",
                ),
                name="role_source_pair_ck",
            ),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
