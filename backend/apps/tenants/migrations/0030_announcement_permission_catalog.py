"""Catalogue announcement permissions for the published announcement routes.

Migration 0003 is the historical catalog and does not contain these codes.
``announcements.view`` is granted to every protected role. ``announcements.manage``
is granted to owner and manager only. Custom roles are left unchanged.
"""

from django.db import connection, migrations

NEW_PERMISSIONS = (
    ("announcements.view", "announcements", "View gym announcements", "LOW"),
    (
        "announcements.manage",
        "announcements",
        "Create, update, publish, and cancel announcements",
        "CRITICAL",
    ),
)

TEMPLATE_GRANTS = {
    "OWNER": ("announcements.view", "announcements.manage"),
    "MANAGER": ("announcements.view", "announcements.manage"),
    "RECEPTIONIST": ("announcements.view",),
    "ACCOUNTANT": ("announcements.view",),
    "TRAINER": ("announcements.view",),
}


def _execute(schema_editor, sql: str, params: list[object] | None = None) -> None:
    if schema_editor is not None:
        schema_editor.execute(sql, params if params is not None else ())
        return
    with connection.cursor() as cursor:
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)


def grant_announcement_permissions(apps, schema_editor) -> None:
    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    Role = apps.get_model("tenants", "Role")
    RolePermission = apps.get_model("tenants", "RolePermission")
    permissions = {}
    for code, module, description, risk_level in NEW_PERMISSIONS:
        permission, _created = Permission.objects.get_or_create(
            code=code,
            defaults={
                "module": module,
                "description": description,
                "risk_level": risk_level,
                "is_active": True,
            },
        )
        permissions[code] = permission
    bumped: list[str] = []
    templates = {}
    for key, codes in TEMPLATE_GRANTS.items():
        template = Template.objects.filter(key=key).first()
        if template is None:
            continue
        added = False
        for code in codes:
            _join, created = Join.objects.get_or_create(
                system_role_template=template,
                permission=permissions[code],
            )
            added = added or created
        if added:
            template.version += 1
            template.save(update_fields=["version", "updated_at"])
            bumped.append(key)
        templates[key] = template
    Gym = apps.get_model("tenants", "Gym")
    gym_ids = list(Gym.objects.order_by("id").values_list("id", flat=True))
    if not gym_ids:
        return
    if bumped:
        _execute(schema_editor, "ALTER TABLE tenants_role DISABLE TRIGGER role_identity_guard")
    try:
        for gym_id in gym_ids:
            _execute(
                schema_editor,
                "SELECT set_config('app.gym_id', %s, true)",
                [str(gym_id)],
            )
            for key, codes in TEMPLATE_GRANTS.items():
                template = templates.get(key)
                if template is None:
                    continue
                for role in Role.objects.filter(
                    gym_id=gym_id,
                    source_template_id=template.id,
                    key=key,
                ):
                    for code in codes:
                        RolePermission.objects.get_or_create(
                            gym_id=role.gym_id,
                            role=role,
                            permission=permissions[code],
                            defaults={"created_by_membership_id": role.created_by_membership_id},
                        )
            if not bumped:
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
                  AND role.key = template.key
                  AND template.key = ANY(%s)
                """,
                [gym_id, bumped],
            )
    finally:
        if bumped:
            _execute(schema_editor, "ALTER TABLE tenants_role ENABLE TRIGGER role_identity_guard")
        _execute(schema_editor, "SELECT set_config('app.gym_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0029_member_self_actors"),
    ]

    operations = [
        migrations.RunPython(grant_announcement_permissions, migrations.RunPython.noop),
    ]
