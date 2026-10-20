"""Class booking integrity and the real occurrence booking seams.

Runtime may select, insert, and update. Delete is rejected. Regeneration keeps
an occurrence that has a booked, attended, or no-show row. Occurrence cancel
rejects attended and no-show rows and cancels booked rows without a ledger entry.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_occurrence_same_gym_fk
  FOREIGN KEY (gym_id, class_occurrence_id)
  REFERENCES classes_class_occurrence (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_package_same_gym_fk
  FOREIGN KEY (gym_id, member_service_package_id)
  REFERENCES trainers_member_service_package (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_attended_by_same_gym_fk
  FOREIGN KEY (gym_id, attended_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_no_show_by_same_gym_fk
  FOREIGN KEY (gym_id, no_show_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_booking
  ADD CONSTRAINT class_book_meter_ck
  CHECK (
    (
      unmetered
      AND member_service_package_id IS NULL
      AND btrim(unmetered_reason) <> ''
    )
    OR (
      NOT unmetered
      AND member_service_package_id IS NOT NULL
      AND unmetered_reason = ''
    )
  );

CREATE OR REPLACE FUNCTION app_class_occurrence_blocks_regeneration(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $blocked$
  SELECT EXISTS (
    SELECT 1
    FROM classes_class_booking AS booking
    WHERE booking.gym_id = target_gym
      AND booking.class_occurrence_id = target_occurrence
      AND booking.status IN ('BOOKED', 'ATTENDED', 'NO_SHOW')
  );
$blocked$;

CREATE OR REPLACE FUNCTION app_class_occurrence_has_terminal_booking(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $terminal$
  SELECT EXISTS (
    SELECT 1
    FROM classes_class_booking AS booking
    WHERE booking.gym_id = target_gym
      AND booking.class_occurrence_id = target_occurrence
      AND booking.status IN ('ATTENDED', 'NO_SHOW')
  );
$terminal$;

CREATE OR REPLACE FUNCTION app_cancel_scheduled_class_bookings(
  target_gym uuid,
  target_occurrence uuid,
  actor uuid,
  cancelled_at timestamp with time zone,
  reason text
)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $cancel$
DECLARE
  stamp timestamp with time zone := cancelled_at;
  actor_id uuid := actor;
  stored_reason text := reason;
BEGIN
  UPDATE classes_class_booking AS booking
  SET status = 'CANCELLED',
      cancelled_at = stamp,
      cancelled_by_membership_id = actor_id,
      reason = stored_reason,
      updated_at = stamp,
      updated_by_membership_id = actor_id
  WHERE booking.gym_id = target_gym
    AND booking.class_occurrence_id = target_occurrence
    AND booking.status = 'BOOKED';
END;
$cancel$;

CREATE FUNCTION app_guard_class_booking()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  occurrence classes_class_occurrence%ROWTYPE;
  package trainers_member_service_package%ROWTYPE;
  taken integer;
BEGIN
  SELECT * INTO occurrence
  FROM classes_class_occurrence
  WHERE gym_id = NEW.gym_id AND id = NEW.class_occurrence_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'class_booking_guard: occurrence is missing';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'BOOKED'
       OR NEW.attended_at IS NOT NULL
       OR NEW.attended_by_membership_id IS NOT NULL
       OR NEW.no_show_at IS NOT NULL
       OR NEW.no_show_by_membership_id IS NOT NULL
       OR NEW.cancelled_at IS NOT NULL
       OR NEW.cancelled_by_membership_id IS NOT NULL
       OR btrim(NEW.reason) <> '' THEN
      RAISE EXCEPTION 'class_booking_guard: new bookings start booked';
    END IF;
    IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
      RAISE EXCEPTION 'class_booking_guard: actors must match on create';
    END IF;
    IF occurrence.status <> 'SCHEDULED' THEN
      RAISE EXCEPTION 'class_booking_guard: occurrence is not scheduled';
    END IF;
  ELSE
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.class_occurrence_id IS DISTINCT FROM OLD.class_occurrence_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.member_service_package_id IS DISTINCT FROM OLD.member_service_package_id
       OR NEW.unmetered IS DISTINCT FROM OLD.unmetered
       OR NEW.unmetered_reason IS DISTINCT FROM OLD.unmetered_reason THEN
      RAISE EXCEPTION 'class_booking_guard: booking identity is immutable';
    END IF;
    IF OLD.status <> 'BOOKED' THEN
      RAISE EXCEPTION 'class_booking_guard: closed bookings are immutable';
    END IF;
    IF NEW.status = 'ATTENDED' THEN
      IF NEW.attended_at IS NULL OR NEW.attended_by_membership_id IS NULL
         OR NEW.no_show_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL
         OR btrim(NEW.reason) <> '' THEN
        RAISE EXCEPTION 'class_booking_guard: attendance evidence is incomplete';
      END IF;
    ELSIF NEW.status = 'NO_SHOW' THEN
      IF NEW.no_show_at IS NULL OR NEW.no_show_by_membership_id IS NULL
         OR NEW.attended_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL
         OR btrim(NEW.reason) <> '' THEN
        RAISE EXCEPTION 'class_booking_guard: no-show evidence is incomplete';
      END IF;
    ELSIF NEW.status = 'CANCELLED' THEN
      IF NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
         OR NEW.attended_at IS NOT NULL OR NEW.no_show_at IS NOT NULL THEN
        RAISE EXCEPTION 'class_booking_guard: cancellation evidence is incomplete';
      END IF;
    ELSE
      RAISE EXCEPTION 'class_booking_guard: booking status transition is invalid';
    END IF;
    RETURN NEW;
  END IF;
  IF NOT NEW.unmetered THEN
    SELECT * INTO package
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF NOT FOUND
       OR package.member_id IS DISTINCT FROM NEW.member_id
       OR package.package_type IS DISTINCT FROM 'CLASS'
       OR package.status IS DISTINCT FROM 'ACTIVE'
       OR occurrence.local_date < package.valid_from
       OR occurrence.local_date > package.valid_until
       OR package.service_package_id IS NULL THEN
      RAISE EXCEPTION 'class_booking_guard: package is not eligible';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_service_package AS definition
      WHERE definition.gym_id = package.gym_id
        AND definition.id = package.service_package_id
        AND definition.class_type_id = occurrence.class_type_id
        AND definition.type = 'CLASS'
    ) THEN
      RAISE EXCEPTION 'class_booking_guard: package class type does not match';
    END IF;
    IF COALESCE((
      SELECT sum(session_delta)
      FROM trainers_service_session_ledger AS ledger
      WHERE ledger.gym_id = package.gym_id
        AND ledger.member_service_package_id = package.id
    ), 0) <= 0 THEN
      RAISE EXCEPTION 'class_booking_guard: package has no remaining sessions';
    END IF;
  END IF;
  SELECT count(*) INTO taken
  FROM classes_class_booking AS booking
  WHERE booking.gym_id = NEW.gym_id
    AND booking.class_occurrence_id = NEW.class_occurrence_id
    AND booking.status <> 'CANCELLED'
    AND booking.id IS DISTINCT FROM NEW.id;
  IF taken >= occurrence.capacity THEN
    RAISE EXCEPTION 'class_booking_guard: occurrence is full';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM classes_class_booking AS other
    JOIN classes_class_occurrence AS theirs
      ON theirs.gym_id = other.gym_id AND theirs.id = other.class_occurrence_id
    WHERE other.gym_id = NEW.gym_id
      AND other.member_id = NEW.member_id
      AND other.status <> 'CANCELLED'
      AND other.id IS DISTINCT FROM NEW.id
      AND tstzrange(occurrence.starts_at, occurrence.ends_at, '[)')
          && tstzrange(theirs.starts_at, theirs.ends_at, '[)')
  ) OR EXISTS (
    SELECT 1
    FROM trainers_pt_booking AS personal
    WHERE personal.gym_id = NEW.gym_id
      AND personal.member_id = NEW.member_id
      AND personal.status <> 'CANCELLED'
      AND tstzrange(personal.starts_at, personal.ends_at, '[)')
          && tstzrange(occurrence.starts_at, occurrence.ends_at, '[)')
  ) THEN
    RAISE EXCEPTION 'class_booking_guard: member overlaps another booking';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_class_booking_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'class_booking_guard: bookings cannot be deleted';
END;
$guard$;

CREATE TRIGGER class_booking_guard_trg
BEFORE INSERT OR UPDATE ON classes_class_booking
FOR EACH ROW EXECUTE FUNCTION app_guard_class_booking();
CREATE TRIGGER class_booking_delete_trg
BEFORE DELETE ON classes_class_booking
FOR EACH ROW EXECUTE FUNCTION app_guard_class_booking_delete();

ALTER TABLE classes_class_booking ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes_class_booking FORCE ROW LEVEL SECURITY;
CREATE POLICY classes_class_booking_select ON classes_class_booking
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY classes_class_booking_insert ON classes_class_booking
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_booking_update ON classes_class_booking
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_booking_delete ON classes_class_booking
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE classes_class_booking FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE classes_class_booking TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_class_booking() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_booking_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_class_booking() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_booking_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_class_booking_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_booking() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE classes_class_booking FROM gym_runtime;
DROP POLICY IF EXISTS classes_class_booking_delete ON classes_class_booking;
DROP POLICY IF EXISTS classes_class_booking_update ON classes_class_booking;
DROP POLICY IF EXISTS classes_class_booking_insert ON classes_class_booking;
DROP POLICY IF EXISTS classes_class_booking_select ON classes_class_booking;
ALTER TABLE classes_class_booking NO FORCE ROW LEVEL SECURITY;
ALTER TABLE classes_class_booking DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS class_booking_delete_trg ON classes_class_booking;
DROP TRIGGER IF EXISTS class_booking_guard_trg ON classes_class_booking;
DROP FUNCTION IF EXISTS app_guard_class_booking_delete();
DROP FUNCTION IF EXISTS app_guard_class_booking();

CREATE OR REPLACE FUNCTION app_class_occurrence_blocks_regeneration(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $blocked$
  SELECT false;
$blocked$;

CREATE OR REPLACE FUNCTION app_class_occurrence_has_terminal_booking(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $terminal$
  SELECT false;
$terminal$;

CREATE OR REPLACE FUNCTION app_cancel_scheduled_class_bookings(
  target_gym uuid,
  target_occurrence uuid,
  actor uuid,
  cancelled_at timestamp with time zone,
  reason text
)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $cancel$
BEGIN
  RETURN;
END;
$cancel$;

ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_meter_ck;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_cancelled_by_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_no_show_by_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_attended_by_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_updated_by_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_created_by_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_package_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_member_same_gym_fk;
ALTER TABLE classes_class_booking DROP CONSTRAINT IF EXISTS class_book_occurrence_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0005_booking_foundation"),
        ("trainers", "0018_booking_foundation"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
