from decimal import Decimal

from django.db import migrations


FEATURES = (
    ("members", "Members"),
    ("memberships", "Memberships"),
    ("finance", "Finance"),
    ("reports", "Reports"),
)


def seed_trial_plan(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    Plan = apps.get_model("tenants", "SubscriptionPlan")
    PlanFeature = apps.get_model("tenants", "SubscriptionPlanFeature")

    features = []
    for code, name in FEATURES:
        feature, _ = Feature.objects.update_or_create(
            code=code,
            defaults={"name": name, "description": f"{name} module"},
        )
        features.append(feature)

    plan, _ = Plan.objects.update_or_create(
        code="TRIAL",
        defaults={
            "name": "Trial",
            "status": "ACTIVE",
            "price": Decimal("0.00"),
            "currency": "THB",
            "billing_interval": "TRIAL",
        },
    )
    for feature in features:
        PlanFeature.objects.update_or_create(
            subscription_plan=plan,
            feature=feature,
            defaults={"enabled": True, "limit_value": None},
        )


FORWARD_SQL = """
ALTER TABLE tenants_expense_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants_expense_category FORCE ROW LEVEL SECURITY;
CREATE POLICY tenants_expense_category_tenant_select ON tenants_expense_category
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY tenants_expense_category_tenant_insert ON tenants_expense_category
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_expense_category_tenant_update ON tenants_expense_category
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_expense_category_tenant_delete ON tenants_expense_category
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE tenants_tenant_sequence ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants_tenant_sequence FORCE ROW LEVEL SECURITY;
CREATE POLICY tenants_tenant_sequence_tenant_select ON tenants_tenant_sequence
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY tenants_tenant_sequence_tenant_insert ON tenants_tenant_sequence
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_tenant_sequence_tenant_update ON tenants_tenant_sequence
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY tenants_tenant_sequence_tenant_delete ON tenants_tenant_sequence
  FOR DELETE USING (gym_id = app_current_gym_id());

CREATE OR REPLACE FUNCTION app_protect_system_expense_category()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.system_key IS NOT NULL AND (
    NEW.system_key IS DISTINCT FROM OLD.system_key
    OR NEW.code IS DISTINCT FROM OLD.code
    OR NEW.is_active IS FALSE
  ) THEN
    RAISE EXCEPTION 'protected expense category identity and active state are immutable';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER protect_system_expense_category
BEFORE UPDATE ON tenants_expense_category
FOR EACH ROW EXECUTE FUNCTION app_protect_system_expense_category();

GRANT SELECT, INSERT, UPDATE ON tenants_expense_category TO gym_runtime;
GRANT SELECT, INSERT ON tenants_tenant_sequence TO gym_runtime;
GRANT SELECT, INSERT, UPDATE ON tenants_gym_provisioning_request TO gym_runtime;
GRANT INSERT ON tenants_gym TO gym_runtime;
GRANT INSERT ON users_user TO gym_runtime;
GRANT SELECT ON tenants_subscription_plan TO gym_runtime;
GRANT SELECT ON tenants_subscription_plan_feature TO gym_runtime;
GRANT SELECT ON tenants_system_role_template TO gym_runtime;
GRANT SELECT ON tenants_system_role_template_permission TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON tenants_expense_category FROM gym_runtime;
REVOKE ALL ON tenants_tenant_sequence FROM gym_runtime;
REVOKE ALL ON tenants_gym_provisioning_request FROM gym_runtime;
REVOKE INSERT ON tenants_gym FROM gym_runtime;
REVOKE INSERT ON users_user FROM gym_runtime;
REVOKE SELECT ON tenants_subscription_plan FROM gym_runtime;
REVOKE SELECT ON tenants_subscription_plan_feature FROM gym_runtime;
REVOKE SELECT ON tenants_system_role_template FROM gym_runtime;
REVOKE SELECT ON tenants_system_role_template_permission FROM gym_runtime;

DROP POLICY IF EXISTS tenants_expense_category_tenant_select ON tenants_expense_category;
DROP POLICY IF EXISTS tenants_expense_category_tenant_insert ON tenants_expense_category;
DROP POLICY IF EXISTS tenants_expense_category_tenant_update ON tenants_expense_category;
DROP POLICY IF EXISTS tenants_expense_category_tenant_delete ON tenants_expense_category;
ALTER TABLE tenants_expense_category NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants_expense_category DISABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenants_tenant_sequence_tenant_select ON tenants_tenant_sequence;
DROP POLICY IF EXISTS tenants_tenant_sequence_tenant_insert ON tenants_tenant_sequence;
DROP POLICY IF EXISTS tenants_tenant_sequence_tenant_update ON tenants_tenant_sequence;
DROP POLICY IF EXISTS tenants_tenant_sequence_tenant_delete ON tenants_tenant_sequence;
ALTER TABLE tenants_tenant_sequence NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants_tenant_sequence DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS protect_system_expense_category ON tenants_expense_category;
DROP FUNCTION IF EXISTS app_protect_system_expense_category();
"""


class Migration(migrations.Migration):
    dependencies = [("tenants", "0007_phase1b_provisioning_models")]
    operations = [
        migrations.RunPython(seed_trial_plan, migrations.RunPython.noop),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
