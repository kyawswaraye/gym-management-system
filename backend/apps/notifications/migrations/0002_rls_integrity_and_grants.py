from django.db import migrations


FORWARD_SQL = """
ALTER TABLE notifications_notification ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications_notification FORCE ROW LEVEL SECURITY;
CREATE POLICY notifications_notification_tenant_select
  ON notifications_notification
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY notifications_notification_tenant_insert
  ON notifications_notification
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY notifications_notification_tenant_update
  ON notifications_notification
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY notifications_notification_tenant_delete
  ON notifications_notification
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE notifications_notification
  ADD CONSTRAINT notification_member_same_gym_fk
  FOREIGN KEY (gym_id, recipient_member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE notifications_notification
  ADD CONSTRAINT notification_membership_same_gym_fk
  FOREIGN KEY (gym_id, membership_id)
  REFERENCES members_membership (gym_id, id) ON DELETE RESTRICT;

GRANT SELECT, INSERT, UPDATE ON notifications_notification TO gym_runtime;
"""

REVERSE_SQL = """
REVOKE ALL ON notifications_notification FROM gym_runtime;
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notification_membership_same_gym_fk;
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notification_member_same_gym_fk;
DROP POLICY IF EXISTS notifications_notification_tenant_delete
  ON notifications_notification;
DROP POLICY IF EXISTS notifications_notification_tenant_update
  ON notifications_notification;
DROP POLICY IF EXISTS notifications_notification_tenant_insert
  ON notifications_notification;
DROP POLICY IF EXISTS notifications_notification_tenant_select
  ON notifications_notification;
ALTER TABLE notifications_notification NO FORCE ROW LEVEL SECURITY;
ALTER TABLE notifications_notification DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0001_initial"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
