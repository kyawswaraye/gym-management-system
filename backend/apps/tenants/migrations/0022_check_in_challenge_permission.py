from django.db import migrations

CHALLENGE_ISSUE = "checkins.challenge.issue"
PROTECTED_TEMPLATE_KEYS = ("OWNER", "MANAGER")


def grant_check_in_challenge_issue(apps, schema_editor):
    """Add staff challenge issuance to the OWNER and MANAGER baselines.

    Migration 0003 is the historical catalog and does not contain this code.
    Reception and trainer remain conditional in the security matrix, so their
    templates are not granted here. Custom roles are left unchanged.
    """

    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    Role = apps.get_model("tenants", "Role")
    RolePermission = apps.get_model("tenants", "RolePermission")
    permission, _created = Permission.objects.get_or_create(
        code=CHALLENGE_ISSUE,
        defaults={
            "module": "checkins",
            "description": "Issue a short-lived onsite check-in challenge",
            "risk_level": "HIGH",
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
        ("tenants", "0021_security_definer_least_privilege"),
    ]

    operations = [
        migrations.RunPython(grant_check_in_challenge_issue, migrations.RunPython.noop),
    ]
