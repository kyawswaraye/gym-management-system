"""Link session-ledger booking ids to the booking tables and guard effects.

The existing uuid columns stay. Composite same-gym foreign keys are added.
Attendance and policy no-show rows are accepted only with one matching booking.
"""

import django.db.models.deletion
from django.db import migrations, models

FORWARD_SQL = r"""
ALTER TABLE trainers_service_session_ledger
  ADD CONSTRAINT sess_ledger_pt_booking_same_gym_fk
  FOREIGN KEY (gym_id, pt_booking_id)
  REFERENCES trainers_pt_booking (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_service_session_ledger
  ADD CONSTRAINT sess_ledger_class_booking_same_gym_fk
  FOREIGN KEY (gym_id, class_booking_id)
  REFERENCES classes_class_booking (gym_id, id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX sess_ledger_one_pt_consume_uq
  ON trainers_service_session_ledger (gym_id, pt_booking_id)
  WHERE pt_booking_id IS NOT NULL AND reason IN ('ATTENDED', 'POLICY_NO_SHOW');
CREATE UNIQUE INDEX sess_ledger_one_class_consume_uq
  ON trainers_service_session_ledger (gym_id, class_booking_id)
  WHERE class_booking_id IS NOT NULL AND reason IN ('ATTENDED', 'POLICY_NO_SHOW');

ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_package_same_gym_fk
  FOREIGN KEY (gym_id, member_service_package_id)
  REFERENCES trainers_member_service_package (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_attended_by_same_gym_fk
  FOREIGN KEY (gym_id, attended_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_no_show_by_same_gym_fk
  FOREIGN KEY (gym_id, no_show_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_meter_ck
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
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_trainer_overlap_excl
  EXCLUDE USING gist (
    gym_id WITH =,
    trainer_staff_profile_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  )
  WHERE (status <> 'CANCELLED');
ALTER TABLE trainers_pt_booking
  ADD CONSTRAINT pt_book_member_overlap_excl
  EXCLUDE USING gist (
    gym_id WITH =,
    member_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  )
  WHERE (status <> 'CANCELLED');

CREATE OR REPLACE FUNCTION app_guard_session_ledger()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  granted integer;
  package_source text;
  package_sale uuid;
  original_delta integer;
  original_reason text;
  original_package uuid;
  remaining integer;
  booking_package uuid;
  booking_unmetered boolean;
  booking_status text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'session_ledger_guard: ledger entries are append-only';
  END IF;
  IF NEW.reason = 'REFUND' THEN
    SELECT granted_sessions, source, pt_sale_id
      INTO granted, package_source, package_sale
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF granted IS NULL
       OR package_source IS DISTINCT FROM 'PT_SALE'
       OR package_sale IS NULL
       OR NEW.session_delta IS DISTINCT FROM (-granted)
       OR NEW.pt_booking_id IS NOT NULL
       OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: refund evidence is incomplete';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_service_session_ledger
      WHERE gym_id = NEW.gym_id
        AND id = NEW.reverses_entry_id
        AND member_service_package_id = NEW.member_service_package_id
        AND reason = 'GRANT'
        AND session_delta = granted
    ) THEN
      RAISE EXCEPTION 'session_ledger_guard: refund target is not the grant';
    END IF;
    SELECT COALESCE(sum(session_delta), 0) INTO remaining
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id
      AND member_service_package_id = NEW.member_service_package_id;
    IF remaining IS DISTINCT FROM granted THEN
      RAISE EXCEPTION 'session_ledger_guard: refund requires the full remaining grant';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.reason = 'EXPIRY' THEN
    RAISE EXCEPTION 'session_ledger_guard: this reason is not published';
  END IF;
  IF NEW.reason IN ('ATTENDED', 'POLICY_NO_SHOW') THEN
    IF NEW.session_delta IS DISTINCT FROM -1
       OR NEW.reverses_entry_id IS NOT NULL
       OR num_nonnulls(NEW.pt_booking_id, NEW.class_booking_id) IS DISTINCT FROM 1 THEN
      RAISE EXCEPTION 'session_ledger_guard: consumption evidence is incomplete';
    END IF;
    IF NEW.pt_booking_id IS NOT NULL THEN
      SELECT member_service_package_id, unmetered, status
        INTO booking_package, booking_unmetered, booking_status
      FROM trainers_pt_booking
      WHERE gym_id = NEW.gym_id AND id = NEW.pt_booking_id;
    ELSE
      SELECT member_service_package_id, unmetered, status
        INTO booking_package, booking_unmetered, booking_status
      FROM classes_class_booking
      WHERE gym_id = NEW.gym_id AND id = NEW.class_booking_id;
    END IF;
    IF booking_package IS NULL
       OR booking_package IS DISTINCT FROM NEW.member_service_package_id
       OR booking_unmetered
       OR booking_status NOT IN ('BOOKED', 'ATTENDED', 'NO_SHOW') THEN
      RAISE EXCEPTION 'session_ledger_guard: consumption booking is not eligible';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.reason = 'GRANT' THEN
    SELECT granted_sessions INTO granted
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF granted IS NULL OR NEW.session_delta IS DISTINCT FROM granted THEN
      RAISE EXCEPTION 'session_ledger_guard: grant delta must equal granted sessions';
    END IF;
    IF NEW.pt_booking_id IS NOT NULL OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: a grant has no booking';
    END IF;
  ELSIF NEW.reason = 'CORRECTION' THEN
    SELECT session_delta, reason, member_service_package_id
      INTO original_delta, original_reason, original_package
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id AND id = NEW.reverses_entry_id;
    IF NOT FOUND
       OR original_package IS DISTINCT FROM NEW.member_service_package_id
       OR original_reason = 'GRANT'
       OR original_reason = 'REFUND'
       OR NEW.session_delta IS DISTINCT FROM (-original_delta)
       OR NEW.pt_booking_id IS NOT NULL
       OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: correction target is not eligible';
    END IF;
  ELSE
    RAISE EXCEPTION 'session_ledger_guard: reason is not a ledger reason';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_pt_booking()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  package trainers_member_service_package%ROWTYPE;
  gym_tz text;
  local_day date;
BEGIN
  IF NEW.ends_at <= NEW.starts_at THEN
    RAISE EXCEPTION 'pt_booking_guard: end must follow the start';
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
      RAISE EXCEPTION 'pt_booking_guard: new bookings start booked';
    END IF;
    IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
      RAISE EXCEPTION 'pt_booking_guard: actors must match on create';
    END IF;
  ELSE
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.member_service_package_id IS DISTINCT FROM OLD.member_service_package_id
       OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
       OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
       OR NEW.unmetered IS DISTINCT FROM OLD.unmetered
       OR NEW.unmetered_reason IS DISTINCT FROM OLD.unmetered_reason THEN
      RAISE EXCEPTION 'pt_booking_guard: booking identity is immutable';
    END IF;
    IF OLD.status <> 'BOOKED' THEN
      RAISE EXCEPTION 'pt_booking_guard: closed bookings are immutable';
    END IF;
    IF NEW.status = 'ATTENDED' THEN
      IF NEW.attended_at IS NULL OR NEW.attended_by_membership_id IS NULL
         OR NEW.no_show_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL
         OR btrim(NEW.reason) <> '' THEN
        RAISE EXCEPTION 'pt_booking_guard: attendance evidence is incomplete';
      END IF;
    ELSIF NEW.status = 'NO_SHOW' THEN
      IF NEW.no_show_at IS NULL OR NEW.no_show_by_membership_id IS NULL
         OR NEW.attended_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL
         OR btrim(NEW.reason) <> '' THEN
        RAISE EXCEPTION 'pt_booking_guard: no-show evidence is incomplete';
      END IF;
    ELSIF NEW.status = 'CANCELLED' THEN
      IF NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
         OR NEW.attended_at IS NOT NULL OR NEW.no_show_at IS NOT NULL THEN
        RAISE EXCEPTION 'pt_booking_guard: cancellation evidence is incomplete';
      END IF;
    ELSE
      RAISE EXCEPTION 'pt_booking_guard: booking status transition is invalid';
    END IF;
    RETURN NEW;
  END IF;
  SELECT timezone INTO gym_tz FROM tenants_gym WHERE id = NEW.gym_id;
  local_day := (NEW.starts_at AT TIME ZONE gym_tz)::date;
  IF NOT EXISTS (
    SELECT 1
    FROM trainers_trainer_assignment AS assignment
    WHERE assignment.gym_id = NEW.gym_id
      AND assignment.trainer_staff_profile_id = NEW.trainer_staff_profile_id
      AND assignment.member_id = NEW.member_id
      AND assignment.status = 'ACTIVE'
      AND assignment.effective_from <= local_day
      AND (assignment.effective_until IS NULL OR assignment.effective_until >= local_day)
  ) THEN
    RAISE EXCEPTION 'pt_booking_guard: assignment is not effective';
  END IF;
  IF NOT NEW.unmetered THEN
    SELECT * INTO package
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF NOT FOUND
       OR package.member_id IS DISTINCT FROM NEW.member_id
       OR package.package_type IS DISTINCT FROM 'PT'
       OR package.status IS DISTINCT FROM 'ACTIVE'
       OR local_day < package.valid_from
       OR local_day > package.valid_until THEN
      RAISE EXCEPTION 'pt_booking_guard: package is not eligible';
    END IF;
    IF COALESCE((
      SELECT sum(session_delta)
      FROM trainers_service_session_ledger AS ledger
      WHERE ledger.gym_id = package.gym_id
        AND ledger.member_service_package_id = package.id
    ), 0) <= 0 THEN
      RAISE EXCEPTION 'pt_booking_guard: package has no remaining sessions';
    END IF;
  END IF;
  IF EXISTS (
    SELECT 1
    FROM classes_class_booking AS booking
    JOIN classes_class_occurrence AS occurrence
      ON occurrence.gym_id = booking.gym_id
     AND occurrence.id = booking.class_occurrence_id
    WHERE booking.gym_id = NEW.gym_id
      AND booking.member_id = NEW.member_id
      AND booking.status <> 'CANCELLED'
      AND tstzrange(occurrence.starts_at, occurrence.ends_at, '[)')
          && tstzrange(NEW.starts_at, NEW.ends_at, '[)')
  ) THEN
    RAISE EXCEPTION 'pt_booking_guard: member overlaps a class booking';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_pt_booking_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'pt_booking_guard: bookings cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_assert_booking_ledger_effect()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  consume boolean;
  expected_reason text;
  found integer;
  package_id uuid;
  is_unmetered boolean;
BEGIN
  IF TG_TABLE_NAME = 'trainers_service_session_ledger' THEN
    IF NEW.reason NOT IN ('ATTENDED', 'POLICY_NO_SHOW') THEN
      RETURN NULL;
    END IF;
    IF NEW.pt_booking_id IS NOT NULL THEN
      SELECT status, unmetered, member_service_package_id
        INTO expected_reason, is_unmetered, package_id
      FROM trainers_pt_booking
      WHERE gym_id = NEW.gym_id AND id = NEW.pt_booking_id;
      IF expected_reason = 'ATTENDED' AND NEW.reason IS DISTINCT FROM 'ATTENDED' THEN
        RAISE EXCEPTION 'booking_effect_guard: attendance effect does not match';
      END IF;
      IF expected_reason = 'NO_SHOW' AND NEW.reason IS DISTINCT FROM 'POLICY_NO_SHOW' THEN
        RAISE EXCEPTION 'booking_effect_guard: no-show effect does not match';
      END IF;
      IF expected_reason IS DISTINCT FROM 'ATTENDED' AND expected_reason IS DISTINCT FROM 'NO_SHOW' THEN
        RAISE EXCEPTION 'booking_effect_guard: consumption has no terminal booking';
      END IF;
      IF is_unmetered OR package_id IS DISTINCT FROM NEW.member_service_package_id THEN
        RAISE EXCEPTION 'booking_effect_guard: consumption booking is not eligible';
      END IF;
    ELSE
      SELECT status, unmetered, member_service_package_id
        INTO expected_reason, is_unmetered, package_id
      FROM classes_class_booking
      WHERE gym_id = NEW.gym_id AND id = NEW.class_booking_id;
      IF is_unmetered OR package_id IS DISTINCT FROM NEW.member_service_package_id THEN
        RAISE EXCEPTION 'booking_effect_guard: consumption booking is not eligible';
      END IF;
      IF expected_reason = 'ATTENDED' AND NEW.reason IS DISTINCT FROM 'ATTENDED' THEN
        RAISE EXCEPTION 'booking_effect_guard: attendance effect does not match';
      END IF;
      IF expected_reason = 'NO_SHOW' THEN
        IF NEW.reason IS DISTINCT FROM 'POLICY_NO_SHOW' THEN
          RAISE EXCEPTION 'booking_effect_guard: no-show effect does not match';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM tenants_gym_settings
          WHERE gym_id = NEW.gym_id AND class_no_show_consumes_session
        ) THEN
          RAISE EXCEPTION 'booking_effect_guard: no-show effect is not allowed';
        END IF;
      END IF;
      IF expected_reason IS DISTINCT FROM 'ATTENDED' AND expected_reason IS DISTINCT FROM 'NO_SHOW' THEN
        RAISE EXCEPTION 'booking_effect_guard: consumption has no terminal booking';
      END IF;
    END IF;
    RETURN NULL;
  END IF;
  IF TG_TABLE_NAME = 'trainers_pt_booking' THEN
    package_id := NEW.member_service_package_id;
    is_unmetered := NEW.unmetered;
    consume := NOT is_unmetered AND NEW.status IN ('ATTENDED', 'NO_SHOW');
    expected_reason := CASE WHEN NEW.status = 'ATTENDED' THEN 'ATTENDED' ELSE 'POLICY_NO_SHOW' END;
    SELECT count(*) INTO found
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id
      AND pt_booking_id = NEW.id
      AND reason IN ('ATTENDED', 'POLICY_NO_SHOW');
  ELSE
    package_id := NEW.member_service_package_id;
    is_unmetered := NEW.unmetered;
    consume := NEW.status = 'ATTENDED'
      OR (
        NEW.status = 'NO_SHOW'
        AND NOT is_unmetered
        AND EXISTS (
          SELECT 1 FROM tenants_gym_settings
          WHERE gym_id = NEW.gym_id AND class_no_show_consumes_session
        )
      );
    expected_reason := CASE WHEN NEW.status = 'ATTENDED' THEN 'ATTENDED' ELSE 'POLICY_NO_SHOW' END;
    SELECT count(*) INTO found
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id
      AND class_booking_id = NEW.id
      AND reason IN ('ATTENDED', 'POLICY_NO_SHOW');
  END IF;
  IF NEW.status IN ('BOOKED', 'CANCELLED') OR is_unmetered THEN
    IF found <> 0 THEN
      RAISE EXCEPTION 'booking_effect_guard: this booking has no session effect';
    END IF;
    RETURN NULL;
  END IF;
  IF consume AND found IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'booking_effect_guard: attendance effect is missing';
  END IF;
  IF NOT consume AND found <> 0 THEN
    RAISE EXCEPTION 'booking_effect_guard: no-show effect is not allowed';
  END IF;
  IF consume AND NOT EXISTS (
    SELECT 1 FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id
      AND session_delta = -1
      AND reason = expected_reason
      AND member_service_package_id = package_id
      AND (
        (TG_TABLE_NAME = 'trainers_pt_booking' AND pt_booking_id = NEW.id)
        OR (TG_TABLE_NAME = 'classes_class_booking' AND class_booking_id = NEW.id)
      )
  ) THEN
    RAISE EXCEPTION 'booking_effect_guard: attendance effect does not match';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE TRIGGER pt_booking_guard_trg
BEFORE INSERT OR UPDATE ON trainers_pt_booking
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_booking();
CREATE TRIGGER pt_booking_delete_trg
BEFORE DELETE ON trainers_pt_booking
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_booking_delete();
CREATE CONSTRAINT TRIGGER pt_book_effect_trg
AFTER INSERT OR UPDATE ON trainers_pt_booking
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_assert_booking_ledger_effect();
CREATE CONSTRAINT TRIGGER class_book_effect_trg
AFTER INSERT OR UPDATE ON classes_class_booking
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_assert_booking_ledger_effect();
CREATE CONSTRAINT TRIGGER sess_ledger_effect_trg
AFTER INSERT ON trainers_service_session_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_assert_booking_ledger_effect();

ALTER TABLE trainers_pt_booking ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_booking FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_pt_booking_select ON trainers_pt_booking
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_booking_insert ON trainers_pt_booking
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_booking_update ON trainers_pt_booking
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_booking_delete ON trainers_pt_booking
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_pt_booking FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_pt_booking TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_pt_booking() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_booking_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_assert_booking_ledger_effect() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_pt_booking() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_booking_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_assert_booking_ledger_effect() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_assert_booking_ledger_effect() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_booking_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_booking() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_pt_booking FROM gym_runtime;
DROP TRIGGER IF EXISTS sess_ledger_effect_trg ON trainers_service_session_ledger;
DROP TRIGGER IF EXISTS class_book_effect_trg ON classes_class_booking;
DROP TRIGGER IF EXISTS pt_book_effect_trg ON trainers_pt_booking;
DROP TRIGGER IF EXISTS pt_booking_delete_trg ON trainers_pt_booking;
DROP TRIGGER IF EXISTS pt_booking_guard_trg ON trainers_pt_booking;
DROP POLICY IF EXISTS trainers_pt_booking_delete ON trainers_pt_booking;
DROP POLICY IF EXISTS trainers_pt_booking_update ON trainers_pt_booking;
DROP POLICY IF EXISTS trainers_pt_booking_insert ON trainers_pt_booking;
DROP POLICY IF EXISTS trainers_pt_booking_select ON trainers_pt_booking;
ALTER TABLE trainers_pt_booking NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_booking DISABLE ROW LEVEL SECURITY;
DROP FUNCTION IF EXISTS app_assert_booking_ledger_effect();
DROP FUNCTION IF EXISTS app_guard_pt_booking_delete();
DROP FUNCTION IF EXISTS app_guard_pt_booking();
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_member_overlap_excl;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_trainer_overlap_excl;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_meter_ck;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_cancelled_by_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_no_show_by_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_attended_by_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_updated_by_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_created_by_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_package_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_member_same_gym_fk;
ALTER TABLE trainers_pt_booking DROP CONSTRAINT IF EXISTS pt_book_trainer_same_gym_fk;
DROP INDEX IF EXISTS sess_ledger_one_class_consume_uq;
DROP INDEX IF EXISTS sess_ledger_one_pt_consume_uq;
ALTER TABLE trainers_service_session_ledger
  DROP CONSTRAINT IF EXISTS sess_ledger_class_booking_same_gym_fk;
ALTER TABLE trainers_service_session_ledger
  DROP CONSTRAINT IF EXISTS sess_ledger_pt_booking_same_gym_fk;

CREATE OR REPLACE FUNCTION app_guard_session_ledger()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  granted integer;
  package_source text;
  package_sale uuid;
  original_delta integer;
  original_reason text;
  original_package uuid;
  remaining integer;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'session_ledger_guard: ledger entries are append-only';
  END IF;
  IF NEW.reason = 'REFUND' THEN
    SELECT granted_sessions, source, pt_sale_id
      INTO granted, package_source, package_sale
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF granted IS NULL
       OR package_source IS DISTINCT FROM 'PT_SALE'
       OR package_sale IS NULL
       OR NEW.session_delta IS DISTINCT FROM (-granted)
       OR NEW.pt_booking_id IS NOT NULL
       OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: refund evidence is incomplete';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_service_session_ledger
      WHERE gym_id = NEW.gym_id
        AND id = NEW.reverses_entry_id
        AND member_service_package_id = NEW.member_service_package_id
        AND reason = 'GRANT'
        AND session_delta = granted
    ) THEN
      RAISE EXCEPTION 'session_ledger_guard: refund target is not the grant';
    END IF;
    SELECT COALESCE(sum(session_delta), 0) INTO remaining
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id
      AND member_service_package_id = NEW.member_service_package_id;
    IF remaining IS DISTINCT FROM granted THEN
      RAISE EXCEPTION 'session_ledger_guard: refund requires the full remaining grant';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.reason IN ('ATTENDED', 'POLICY_NO_SHOW', 'EXPIRY') THEN
    RAISE EXCEPTION 'session_ledger_guard: this reason is not published';
  END IF;
  IF NEW.reason = 'GRANT' THEN
    SELECT granted_sessions INTO granted
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF granted IS NULL OR NEW.session_delta IS DISTINCT FROM granted THEN
      RAISE EXCEPTION 'session_ledger_guard: grant delta must equal granted sessions';
    END IF;
    IF NEW.pt_booking_id IS NOT NULL OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: a grant has no booking';
    END IF;
  ELSIF NEW.reason = 'CORRECTION' THEN
    SELECT session_delta, reason, member_service_package_id
      INTO original_delta, original_reason, original_package
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id AND id = NEW.reverses_entry_id;
    IF NOT FOUND
       OR original_package IS DISTINCT FROM NEW.member_service_package_id
       OR original_reason = 'GRANT'
       OR original_reason = 'REFUND'
       OR NEW.session_delta IS DISTINCT FROM (-original_delta) THEN
      RAISE EXCEPTION 'session_ledger_guard: correction target is not eligible';
    END IF;
  ELSE
    RAISE EXCEPTION 'session_ledger_guard: reason is not a ledger reason';
  END IF;
  RETURN NEW;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0006_class_booking_guards"),
        ("trainers", "0018_booking_foundation"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="servicesessionledger",
                    name="class_booking_id",
                ),
                migrations.RemoveField(
                    model_name="servicesessionledger",
                    name="pt_booking_id",
                ),
                migrations.AddField(
                    model_name="servicesessionledger",
                    name="class_booking",
                    field=models.ForeignKey(
                        blank=True,
                        db_column="class_booking_id",
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="ledger_entries",
                        to="classes.classbooking",
                    ),
                ),
                migrations.AddField(
                    model_name="servicesessionledger",
                    name="pt_booking",
                    field=models.ForeignKey(
                        blank=True,
                        db_column="pt_booking_id",
                        null=True,
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="ledger_entries",
                        to="trainers.ptbooking",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
            ],
        ),
    ]
