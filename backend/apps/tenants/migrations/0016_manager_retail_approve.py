from django.db import migrations

RETAIL_APPROVE = "retail.approve"
PROTECTED_TEMPLATE_KEYS = ("OWNER", "MANAGER")


def grant_manager_retail_approve(apps, schema_editor):
    """Add ``retail.approve`` to the protected OWNER and MANAGER baselines.

    Migration 0003 is the historical catalog and does not contain this code.
    This operation is the only forward path that introduces it. It is safe to
    run again: the permission, each protected template join, and each copied
    tenant role permission are created at most once. Custom roles, including a
    role merely named MANAGER, are left unchanged.
    """

    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    Role = apps.get_model("tenants", "Role")
    RolePermission = apps.get_model("tenants", "RolePermission")
    permission, _created = Permission.objects.get_or_create(
        code=RETAIL_APPROVE,
        defaults={
            "module": "retail",
            "description": RETAIL_APPROVE,
            "risk_level": "CRITICAL",
            "is_active": True,
        },
    )
    for key in PROTECTED_TEMPLATE_KEYS:
        template = Template.objects.filter(key=key).first()
        if template is None:
            continue
        Join.objects.get_or_create(system_role_template=template, permission=permission)
        for role in Role.objects.filter(source_template=template, key=key):
            RolePermission.objects.get_or_create(
                gym_id=role.gym_id,
                role=role,
                permission=permission,
                defaults={"created_by_membership_id": role.created_by_membership_id},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0015_role_delegation_guards"),
    ]

    operations = [
        migrations.RunPython(grant_manager_retail_approve, migrations.RunPython.noop),
    ]
