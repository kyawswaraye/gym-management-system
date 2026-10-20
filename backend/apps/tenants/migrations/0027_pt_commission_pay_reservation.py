"""Hard-reserve pt.commissions.pay to protected owner and manager.

Migration 0003 remains the historical catalog and still grants this code to
ACCOUNTANT. This forward migration removes that template grant, removes every
copied or custom grant that is not on a protected OWNER or MANAGER role, and
extends the role-permission guard so the code cannot be delegated back.
Protected OWNER and MANAGER keep the grant. Membership authorization versions
are not bumped: permissions are read from the current role grants.
"""

from django.db import connection, migrations

PAY_CODE = "pt.commissions.pay"

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
         'salaries.approve',
         'pt.commissions.pay'
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
       'salaries.approve',
       'pt.commissions.pay'
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
REVOKE ALL ON FUNCTION app_protect_owner_role_permissions() FROM gym_runtime;
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
REVOKE ALL ON FUNCTION app_protect_owner_role_permissions() FROM gym_runtime;
RESET ROLE;
REVOKE CREATE ON SCHEMA public FROM gym_auth_bootstrap;
"""


def _execute(schema_editor, sql: str, params: list[object] | None = None) -> None:
    if schema_editor is not None:
        schema_editor.execute(sql, params if params is not None else ())
        return
    with connection.cursor() as cursor:
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)


def strip_unsafe_pt_commission_pay(apps, schema_editor) -> None:
    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    RolePermission = apps.get_model("tenants", "RolePermission")
    Gym = apps.get_model("tenants", "Gym")
    permission = Permission.objects.filter(code=PAY_CODE).first()
    if permission is None:
        return
    _execute(
        schema_editor,
        """
        DELETE FROM tenants_system_role_template_permission AS link
        USING tenants_permission AS permission,
              tenants_system_role_template AS template
        WHERE link.permission_id = permission.id
          AND link.system_role_template_id = template.id
          AND permission.code = %s
          AND template.key NOT IN ('OWNER', 'MANAGER')
        """,
        [PAY_CODE],
    )
    if (
        Join.objects.filter(permission=permission)
        .exclude(system_role_template__key__in=["OWNER", "MANAGER"])
        .exists()
    ):
        raise RuntimeError("pt.commissions.pay remains on a non-management template.")
    removed = True
    template = Template.objects.filter(key="ACCOUNTANT").first()
    if template is not None and removed:
        template.version += 1
        template.save(update_fields=["version", "updated_at"])
    _execute(schema_editor, "ALTER TABLE tenants_role DISABLE TRIGGER role_identity_guard")
    try:
        for gym_id in Gym.objects.order_by("id").values_list("id", flat=True):
            _execute(
                schema_editor,
                "SELECT set_config('app.gym_id', %s, true)",
                [str(gym_id)],
            )
            RolePermission.objects.filter(gym_id=gym_id, permission=permission).exclude(
                role__key="OWNER",
                role__source_template__key="OWNER",
            ).exclude(
                role__key="MANAGER",
                role__source_template__key="MANAGER",
            ).delete()
            if template is None or not removed:
                continue
            _execute(
                schema_editor,
                """
                UPDATE tenants_role AS role
                SET source_template_version = template.version,
                    updated_at = NOW()
                FROM tenants_system_role_template AS template
                WHERE role.gym_id = %s
                  AND role.source_template_id = template.id
                  AND role.key = 'ACCOUNTANT'
                  AND template.key = 'ACCOUNTANT'
                """,
                [gym_id],
            )
    finally:
        _execute(schema_editor, "ALTER TABLE tenants_role ENABLE TRIGGER role_identity_guard")
        _execute(schema_editor, "SELECT set_config('app.gym_id', '', true)")


def restore_accountant_pt_commission_pay(apps, schema_editor) -> None:
    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    Role = apps.get_model("tenants", "Role")
    RolePermission = apps.get_model("tenants", "RolePermission")
    Gym = apps.get_model("tenants", "Gym")
    permission = Permission.objects.filter(code=PAY_CODE).first()
    template = Template.objects.filter(key="ACCOUNTANT").first()
    if permission is None or template is None:
        return
    Join.objects.get_or_create(system_role_template=template, permission=permission)
    for gym_id in Gym.objects.order_by("id").values_list("id", flat=True):
        _execute(
            schema_editor,
            "SELECT set_config('app.gym_id', %s, true)",
            [str(gym_id)],
        )
        for role in Role.objects.filter(
            gym_id=gym_id,
            key="ACCOUNTANT",
            source_template_id=template.id,
        ):
            RolePermission.objects.get_or_create(
                gym_id=role.gym_id,
                role=role,
                permission=permission,
                defaults={"created_by_membership_id": role.created_by_membership_id},
            )
    _execute(schema_editor, "SELECT set_config('app.gym_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0026_pt_schedule_permission_catalog"),
    ]

    operations = [
        migrations.RunPython(strip_unsafe_pt_commission_pay, restore_accountant_pt_commission_pay),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
