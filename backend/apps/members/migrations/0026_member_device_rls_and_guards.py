"""Device RLS, same-gym member key, five-active limit, and status revocation."""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE members_member_device
  DROP CONSTRAINT IF EXISTS members_member_device_member_id_fkey;
ALTER TABLE members_member_device
  ADD CONSTRAINT member_device_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_member_device()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.installation_id IS DISTINCT FROM OLD.installation_id
       OR NEW.platform IS DISTINCT FROM OLD.platform THEN
      RAISE EXCEPTION 'member_device_guard: identity columns are immutable';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_member_device_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'member_device_guard: devices cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_member_device_active_limit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.revoked_at IS NULL AND (
    SELECT count(*)
    FROM public.members_member_device
    WHERE gym_id = NEW.gym_id
      AND member_id = NEW.member_id
      AND revoked_at IS NULL
  ) > 5 THEN
    RAISE EXCEPTION 'member_device_guard: a member can have at most five active devices'
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE FUNCTION app_revoke_member_devices()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  reason text;
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    reason := CASE NEW.status
      WHEN 'ARCHIVED' THEN 'MEMBER_ARCHIVED'
      WHEN 'SUSPENDED' THEN 'MEMBER_SUSPENDED'
      WHEN 'CANCELLED' THEN 'MEMBER_CANCELLED'
      ELSE NULL
    END;
    IF reason IS NOT NULL THEN
      UPDATE public.members_member_device
      SET revoked_at = pg_catalog.clock_timestamp(),
          revocation_reason = reason,
          token_ciphertext = NULL,
          updated_at = pg_catalog.clock_timestamp()
      WHERE gym_id = NEW.gym_id
        AND member_id = NEW.id
        AND revoked_at IS NULL;
    END IF;
  END IF;
  IF NEW.linked_user_id IS DISTINCT FROM OLD.linked_user_id THEN
    UPDATE public.members_member_device
    SET revoked_at = pg_catalog.clock_timestamp(),
        revocation_reason = 'MEMBER_UNLINKED',
        token_ciphertext = NULL,
        updated_at = pg_catalog.clock_timestamp()
    WHERE gym_id = NEW.gym_id
      AND member_id = NEW.id
      AND revoked_at IS NULL;
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE TRIGGER member_device_guard_trg
BEFORE INSERT OR UPDATE ON members_member_device
FOR EACH ROW EXECUTE FUNCTION app_guard_member_device();

CREATE TRIGGER member_device_delete_trg
BEFORE DELETE ON members_member_device
FOR EACH ROW EXECUTE FUNCTION app_guard_member_device_delete();

CREATE CONSTRAINT TRIGGER member_device_active_limit_trg
AFTER INSERT OR UPDATE ON members_member_device
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_member_device_active_limit();

CREATE TRIGGER member_device_revoke_trg
AFTER UPDATE ON members_member
FOR EACH ROW EXECUTE FUNCTION app_revoke_member_devices();

ALTER TABLE members_member_device ENABLE ROW LEVEL SECURITY;
ALTER TABLE members_member_device FORCE ROW LEVEL SECURITY;
CREATE POLICY members_member_device_tenant_select
  ON members_member_device
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY members_member_device_tenant_insert
  ON members_member_device
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY members_member_device_tenant_update
  ON members_member_device
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY members_member_device_tenant_delete
  ON members_member_device
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE members_member_device FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE members_member_device TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_member_device() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_member_device_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_member_device_active_limit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_revoke_member_devices() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_member_device() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_member_device_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_member_device_active_limit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_revoke_member_devices() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_revoke_member_devices() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_member_device_active_limit() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_member_device_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_member_device() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE members_member_device FROM gym_runtime;
DROP POLICY IF EXISTS members_member_device_tenant_delete ON members_member_device;
DROP POLICY IF EXISTS members_member_device_tenant_update ON members_member_device;
DROP POLICY IF EXISTS members_member_device_tenant_insert ON members_member_device;
DROP POLICY IF EXISTS members_member_device_tenant_select ON members_member_device;
ALTER TABLE members_member_device NO FORCE ROW LEVEL SECURITY;
ALTER TABLE members_member_device DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS member_device_revoke_trg ON members_member;
DROP TRIGGER IF EXISTS member_device_active_limit_trg ON members_member_device;
DROP TRIGGER IF EXISTS member_device_delete_trg ON members_member_device;
DROP TRIGGER IF EXISTS member_device_guard_trg ON members_member_device;
DROP FUNCTION IF EXISTS app_revoke_member_devices();
DROP FUNCTION IF EXISTS app_member_device_active_limit();
DROP FUNCTION IF EXISTS app_guard_member_device_delete();
DROP FUNCTION IF EXISTS app_guard_member_device();
ALTER TABLE members_member_device
  DROP CONSTRAINT IF EXISTS member_device_member_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0025_phase7_notifications"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
