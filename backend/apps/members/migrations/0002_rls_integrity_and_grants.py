from django.db import migrations


TABLES = (
    "members_member",
    "members_membership_plan",
    "members_membership",
    "members_payment",
    "members_receipt",
)

POLICIES = "\n".join(
    f"""
ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
CREATE POLICY {table}_tenant_select ON {table}
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_insert ON {table}
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_update ON {table}
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY {table}_tenant_delete ON {table}
  FOR DELETE USING (gym_id = app_current_gym_id());
"""
    for table in TABLES
)

DROP_POLICIES = "\n".join(
    f"""
DROP POLICY IF EXISTS {table}_tenant_select ON {table};
DROP POLICY IF EXISTS {table}_tenant_insert ON {table};
DROP POLICY IF EXISTS {table}_tenant_update ON {table};
DROP POLICY IF EXISTS {table}_tenant_delete ON {table};
ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
"""
    for table in reversed(TABLES)
)

FORWARD_SQL = f"""
{POLICIES}
ALTER TABLE members_membership ADD CONSTRAINT membership_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id) REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_membership ADD CONSTRAINT membership_plan_same_gym_fk
  FOREIGN KEY (gym_id, plan_id)
  REFERENCES members_membership_plan (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment ADD CONSTRAINT payment_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id) REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment ADD CONSTRAINT payment_membership_same_gym_fk
  FOREIGN KEY (gym_id, membership_id)
  REFERENCES members_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_payment_same_gym_fk
  FOREIGN KEY (gym_id, payment_id) REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
GRANT SELECT, INSERT, UPDATE ON {", ".join(TABLES)} TO gym_runtime;
"""

REVERSE_SQL = f"""
REVOKE ALL ON {", ".join(TABLES)} FROM gym_runtime;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_payment_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_membership_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_member_same_gym_fk;
ALTER TABLE members_membership DROP CONSTRAINT IF EXISTS membership_plan_same_gym_fk;
ALTER TABLE members_membership DROP CONSTRAINT IF EXISTS membership_member_same_gym_fk;
{DROP_POLICIES}
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0001_initial"),
        ("tenants", "0006_gymsettings_rls"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
