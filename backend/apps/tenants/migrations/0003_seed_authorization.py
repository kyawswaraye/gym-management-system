from django.db import migrations


PERMISSION_CODES = """
platform.dashboard.view platform.gyms.view platform.gyms.create platform.gyms.update
platform.gyms.activate platform.gyms.suspend platform.owners.create
platform.subscriptions.manage platform.settings.manage platform.audit.view
gym.view gym.update members.view members.create members.update members.archive
memberships.view memberships.create memberships.update memberships.cancel
checkins.view checkins.create checkins.update payments.view payments.create payments.update
payments.submit payments.approve payments.void payments.refund receipts.view receipts.void
expenses.view expenses.create expenses.update expenses.submit expenses.approve expenses.void
cashbook.view cashbook.open cashbook.reconcile cashbook.adjust pt.assignments.view
pt.assignments.manage pt.sales.view pt.sales.create pt.sales.update pt.sales.submit
pt.sales.approve pt.sales.void pt.commissions.view pt.commissions.pay salaries.view
salaries.create salaries.update salaries.submit salaries.approve salaries.void reports.view
reports.financial reports.export staff.view staff.manage roles.view roles.manage settings.view
settings.manage files.view files.upload files.delete audit.view notifications.view
notifications.manage
""".split()

OWNER = [code for code in PERMISSION_CODES if not code.startswith("platform.")]
MANAGER = """
gym.view gym.update members.view members.create members.update members.archive memberships.view
memberships.create memberships.update memberships.cancel checkins.view checkins.create
checkins.update payments.view payments.create payments.update payments.submit payments.approve
payments.void payments.refund receipts.view receipts.void expenses.view expenses.create
expenses.update expenses.submit expenses.approve expenses.void cashbook.view cashbook.open
cashbook.reconcile cashbook.adjust pt.assignments.view pt.assignments.manage pt.sales.view
pt.sales.create pt.sales.update pt.sales.submit pt.sales.approve pt.sales.void
pt.commissions.view pt.commissions.pay salaries.view salaries.create salaries.update
salaries.submit salaries.approve salaries.void reports.view reports.financial reports.export
staff.view staff.manage roles.view roles.manage settings.view files.view files.upload files.delete
audit.view notifications.view notifications.manage
""".split()
RECEPTIONIST = """
gym.view members.view members.create members.update memberships.view memberships.create
memberships.update memberships.cancel checkins.view checkins.create checkins.update payments.view
payments.create payments.update payments.submit receipts.view expenses.view expenses.create
expenses.update expenses.submit cashbook.view cashbook.open cashbook.reconcile
pt.assignments.view pt.sales.view pt.sales.create pt.sales.update pt.sales.submit salaries.view
salaries.create salaries.update salaries.submit reports.view files.view files.upload
notifications.view
""".split()
ACCOUNTANT = """
gym.view members.view memberships.view payments.view payments.create payments.update
payments.submit payments.void payments.refund receipts.view receipts.void expenses.view
expenses.create expenses.update expenses.submit expenses.void cashbook.view cashbook.open
cashbook.reconcile cashbook.adjust pt.sales.view pt.sales.create pt.sales.update pt.sales.submit
pt.sales.void pt.commissions.view pt.commissions.pay salaries.view salaries.create salaries.update
salaries.submit salaries.void reports.view reports.financial reports.export roles.view
settings.view files.view files.upload audit.view notifications.view
""".split()
TRAINER = """
gym.view members.view members.update memberships.view checkins.view checkins.create
pt.assignments.view pt.assignments.manage pt.sales.view pt.sales.create pt.sales.update
pt.sales.submit pt.commissions.view reports.view files.view files.upload notifications.view
""".split()
TEMPLATES = {
    "OWNER": OWNER,
    "MANAGER": MANAGER,
    "RECEPTIONIST": RECEPTIONIST,
    "ACCOUNTANT": ACCOUNTANT,
    "TRAINER": TRAINER,
}


def seed(apps, schema_editor):
    Permission = apps.get_model("tenants", "Permission")
    Template = apps.get_model("tenants", "SystemRoleTemplate")
    Join = apps.get_model("tenants", "SystemRoleTemplatePermission")
    permissions = {}
    for code in PERMISSION_CODES:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": code.split(".", 1)[0],
                "description": code,
                "risk_level": (
                    "CRITICAL"
                    if code.endswith((".approve", ".adjust", ".manage"))
                    or code in {"payments.refund", "payments.void", "receipts.void"}
                    else "LOW"
                ),
                "is_active": True,
            },
        )
        permissions[code] = permission
    for key, codes in TEMPLATES.items():
        template, _ = Template.objects.update_or_create(
            key=key,
            defaults={
                "name": key.replace("_", " ").title(),
                "description": f"Protected {key} role template",
                "version": 1,
                "is_active": True,
            },
        )
        desired = {permissions[code].id for code in codes}
        Join.objects.filter(system_role_template=template).exclude(permission_id__in=desired).delete()
        Join.objects.bulk_create(
            [
                Join(system_role_template=template, permission_id=permission_id)
                for permission_id in desired
            ],
            ignore_conflicts=True,
        )


class Migration(migrations.Migration):
    dependencies = [("tenants", "0002_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
