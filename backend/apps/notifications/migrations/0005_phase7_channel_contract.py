"""Replace the email-only contract and lock push attempts to the gym."""

from django.db import migrations

_EXPIRY_PAYLOAD = r"""
  AND template_key = 'membership_expiry'
  AND template_version = 1
  AND membership_id IS NOT NULL
  AND announcement_id IS NULL
  AND jsonb_typeof(payload) = 'object'
  AND payload - ARRAY[
    'member_name', 'plan_name', 'expiry_date', 'warning_day', 'gym_name', 'gym_email', 'gym_phone'
  ] = '{}'::jsonb
  AND payload ?& ARRAY['member_name', 'plan_name', 'expiry_date', 'warning_day', 'gym_name']
  AND jsonb_typeof(payload -> 'member_name') = 'string'
  AND char_length(payload ->> 'member_name') BETWEEN 1 AND 201
  AND (payload ->> 'member_name') !~ '[[:cntrl:]]'
  AND (payload ->> 'member_name') = btrim(payload ->> 'member_name')
  AND jsonb_typeof(payload -> 'plan_name') = 'string'
  AND char_length(payload ->> 'plan_name') BETWEEN 1 AND 100
  AND (payload ->> 'plan_name') !~ '[[:cntrl:]]'
  AND (payload ->> 'plan_name') = btrim(payload ->> 'plan_name')
  AND jsonb_typeof(payload -> 'expiry_date') = 'string'
  AND (payload ->> 'expiry_date') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  AND jsonb_typeof(payload -> 'warning_day') = 'number'
  AND (payload ->> 'warning_day') ~ '^[0-9]+$'
  AND (payload ->> 'warning_day')::int BETWEEN 0 AND 365
  AND jsonb_typeof(payload -> 'gym_name') = 'string'
  AND char_length(payload ->> 'gym_name') BETWEEN 1 AND 150
  AND (payload ->> 'gym_name') !~ '[[:cntrl:]]'
  AND (payload ->> 'gym_name') = btrim(payload ->> 'gym_name')
  AND (
    NOT (payload ? 'gym_email')
    OR (
      jsonb_typeof(payload -> 'gym_email') = 'string'
      AND char_length(payload ->> 'gym_email') BETWEEN 3 AND 254
      AND (payload ->> 'gym_email') ~ '^[^[:space:]@]+@[^[:space:]@]+$'
      AND (payload ->> 'gym_email') !~ '[[:cntrl:]]'
    )
  )
  AND (
    NOT (payload ? 'gym_phone')
    OR (
      jsonb_typeof(payload -> 'gym_phone') = 'string'
      AND (payload ->> 'gym_phone') ~ '^\+?[0-9]{1,32}$'
    )
  )
  AND NOT (payload ? 'email')
  AND NOT (payload ? 'member_email')
  AND NOT (payload ? 'to')
  AND NOT (payload ? 'destination')
  AND char_length(payload::text) <= 2000
"""

FORWARD_SQL = rf"""
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notification_expiry_contract_ck;
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notifications_notification_announcement_id_fkey;
ALTER TABLE notifications_notification
  ADD CONSTRAINT notification_announcement_same_gym_fk
  FOREIGN KEY (gym_id, announcement_id)
  REFERENCES announcements_announcement (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE notifications_notification
  ADD CONSTRAINT notification_channel_contract_ck
  CHECK (
    (
      type = 'MEMBERSHIP_EXPIRY'
      AND channel = 'EMAIL'
      AND read_at IS NULL
      AND deduplication_key ~ '^membership-expiry:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}:[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}:[0-9]{{1,3}}:v1$'
      AND split_part(deduplication_key, ':', 2)::uuid = membership_id
      AND split_part(deduplication_key, ':', 3) = payload->>'expiry_date'
      AND split_part(deduplication_key, ':', 4)::int BETWEEN 0 AND 365
      AND split_part(deduplication_key, ':', 4)::int = (payload->>'warning_day')::int
      AND split_part(deduplication_key, ':', 5) = 'v1'
      {_EXPIRY_PAYLOAD}
    )
    OR (
      type = 'MEMBERSHIP_EXPIRY'
      AND channel = 'IN_APP'
      AND deduplication_key ~ '^in_app:membership_expiry:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}:[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}:[0-9]{{1,3}}:v1$'
      AND split_part(deduplication_key, ':', 3)::uuid = membership_id
      AND split_part(deduplication_key, ':', 4) = payload->>'expiry_date'
      AND split_part(deduplication_key, ':', 5)::int = (payload->>'warning_day')::int
      AND split_part(deduplication_key, ':', 6) = 'v1'
      {_EXPIRY_PAYLOAD}
    )
    OR (
      type = 'MEMBERSHIP_EXPIRY'
      AND channel = 'PUSH'
      AND read_at IS NULL
      AND deduplication_key ~ '^push:membership_expiry:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}:[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}:[0-9]{{1,3}}:v1$'
      AND split_part(deduplication_key, ':', 3)::uuid = membership_id
      AND split_part(deduplication_key, ':', 4) = payload->>'expiry_date'
      AND split_part(deduplication_key, ':', 5)::int = (payload->>'warning_day')::int
      AND split_part(deduplication_key, ':', 6) = 'v1'
      {_EXPIRY_PAYLOAD}
    )
    OR (
      type = 'ANNOUNCEMENT'
      AND channel IN ('IN_APP', 'PUSH')
      AND membership_id IS NULL
      AND announcement_id IS NOT NULL
      AND template_key = 'announcement'
      AND template_version = 1
      AND (channel = 'IN_APP' OR read_at IS NULL)
      AND (
        (
          channel = 'IN_APP'
          AND deduplication_key ~ '^in_app:announcement:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
        )
        OR (
          channel = 'PUSH'
          AND deduplication_key ~ '^push:announcement:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}:[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$'
        )
      )
      AND split_part(deduplication_key, ':', 3)::uuid = announcement_id
      AND split_part(deduplication_key, ':', 4)::uuid = recipient_member_id
      AND jsonb_typeof(payload) = 'object'
      AND payload - ARRAY['title', 'body', 'announcement_type'] = '{{}}'::jsonb
      AND payload ?& ARRAY['title', 'body', 'announcement_type']
      AND jsonb_typeof(payload -> 'title') = 'string'
      AND char_length(payload ->> 'title') BETWEEN 1 AND 120
      AND (payload ->> 'title') !~ '[[:cntrl:]]'
      AND (payload ->> 'title') = btrim(payload ->> 'title')
      AND jsonb_typeof(payload -> 'body') = 'string'
      AND char_length(payload ->> 'body') BETWEEN 1 AND 4000
      AND (payload ->> 'body') !~ '[[:cntrl:]]'
      AND (payload ->> 'body') = btrim(payload ->> 'body')
      AND (payload ->> 'announcement_type') IN ('EVENT', 'PROMOTION', 'GENERAL')
      AND char_length(payload::text) <= 32000
    )
  );

ALTER TABLE notifications_push_attempt
  DROP CONSTRAINT IF EXISTS notifications_push_attempt_notification_id_fkey;
ALTER TABLE notifications_push_attempt
  DROP CONSTRAINT IF EXISTS notifications_push_attempt_member_device_id_fkey;
ALTER TABLE notifications_push_attempt
  ADD CONSTRAINT push_attempt_notification_same_gym_fk
  FOREIGN KEY (gym_id, notification_id)
  REFERENCES notifications_notification (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE notifications_push_attempt
  ADD CONSTRAINT push_attempt_device_same_gym_fk
  FOREIGN KEY (gym_id, member_device_id)
  REFERENCES members_member_device (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_push_attempt_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'push_attempt_guard: push attempts cannot be deleted';
END;
$guard$;

CREATE TRIGGER push_attempt_delete_trg
BEFORE DELETE ON notifications_push_attempt
FOR EACH ROW EXECUTE FUNCTION app_guard_push_attempt_delete();

ALTER TABLE notifications_push_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications_push_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY notifications_push_attempt_tenant_select
  ON notifications_push_attempt
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY notifications_push_attempt_tenant_insert
  ON notifications_push_attempt
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY notifications_push_attempt_tenant_update
  ON notifications_push_attempt
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY notifications_push_attempt_tenant_delete
  ON notifications_push_attempt
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE notifications_push_attempt FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE notifications_push_attempt TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_push_attempt_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_push_attempt_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_push_attempt_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE notifications_push_attempt FROM gym_runtime;
DROP POLICY IF EXISTS notifications_push_attempt_tenant_delete ON notifications_push_attempt;
DROP POLICY IF EXISTS notifications_push_attempt_tenant_update ON notifications_push_attempt;
DROP POLICY IF EXISTS notifications_push_attempt_tenant_insert ON notifications_push_attempt;
DROP POLICY IF EXISTS notifications_push_attempt_tenant_select ON notifications_push_attempt;
ALTER TABLE notifications_push_attempt NO FORCE ROW LEVEL SECURITY;
ALTER TABLE notifications_push_attempt DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS push_attempt_delete_trg ON notifications_push_attempt;
DROP FUNCTION IF EXISTS app_guard_push_attempt_delete();
ALTER TABLE notifications_push_attempt
  DROP CONSTRAINT IF EXISTS push_attempt_device_same_gym_fk;
ALTER TABLE notifications_push_attempt
  DROP CONSTRAINT IF EXISTS push_attempt_notification_same_gym_fk;
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notification_channel_contract_ck;
ALTER TABLE notifications_notification
  DROP CONSTRAINT IF EXISTS notification_announcement_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0004_phase7_notifications"),
        ("members", "0026_member_device_rls_and_guards"),
        ("announcements", "0002_announcement_rls_and_guards"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
