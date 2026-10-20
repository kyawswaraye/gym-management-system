"""Guard schedules and occurrences, and reserve the booking seam.

``app_class_occurrence_blocks_regeneration`` and
``app_class_occurrence_has_terminal_booking`` return false until ClassBooking
exists. The next increment replaces those functions. Occurrence history columns
stay as they are. ``app_cancel_scheduled_class_bookings`` is the no-ledger
cancel hook for future BOOKED rows.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE FUNCTION app_class_weekdays_valid(days smallint[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $valid$
  SELECT days IS NOT NULL
    AND cardinality(days) BETWEEN 1 AND 7
    AND days <@ ARRAY[0, 1, 2, 3, 4, 5, 6]::smallint[]
    AND days = (
      ARRAY(SELECT DISTINCT unnest(days) ORDER BY 1)
    )::smallint[];
$valid$;

ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_span_ck
  CHECK (effective_until <= effective_from + 365);
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_weekday_ck
  CHECK (app_class_weekdays_valid(weekdays));
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_timezone_ck
  CHECK (timezone <> '');
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_location_ck
  CHECK (
    location <> ''
    AND location = regexp_replace(btrim(location), '[[:space:]]+', ' ', 'g')
    AND normalized_location = upper(location)
  );
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_class_type_same_gym_fk
  FOREIGN KEY (gym_id, class_type_id)
  REFERENCES classes_class_type (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_schedule
  ADD CONSTRAINT class_sched_ended_by_same_gym_fk
  FOREIGN KEY (gym_id, ended_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_location_ck
  CHECK (
    location <> ''
    AND location = regexp_replace(btrim(location), '[[:space:]]+', ' ', 'g')
    AND normalized_location = upper(location)
  );
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_schedule_same_gym_fk
  FOREIGN KEY (gym_id, class_schedule_id)
  REFERENCES classes_class_schedule (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_class_type_same_gym_fk
  FOREIGN KEY (gym_id, class_type_id)
  REFERENCES classes_class_type (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_trainer_overlap_excl
  EXCLUDE USING gist (
    gym_id WITH =,
    trainer_staff_profile_id WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  )
  WHERE (status = 'SCHEDULED');
ALTER TABLE classes_class_occurrence
  ADD CONSTRAINT class_occ_location_overlap_excl
  EXCLUDE USING gist (
    gym_id WITH =,
    normalized_location WITH =,
    tstzrange(starts_at, ends_at, '[)') WITH &&
  )
  WHERE (status = 'SCHEDULED' AND location_is_exclusive);

CREATE FUNCTION app_class_occurrence_blocks_regeneration(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $blocked$
  -- Replaced when ClassBooking exists. A BOOKED, ATTENDED, or NO_SHOW row
  -- will make regeneration keep this occurrence.
  SELECT false;
$blocked$;

CREATE FUNCTION app_class_occurrence_has_terminal_booking(
  target_gym uuid,
  target_occurrence uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $terminal$
  -- Replaced when ClassBooking exists. ATTENDED or NO_SHOW will block cancel.
  SELECT false;
$terminal$;

CREATE FUNCTION app_cancel_scheduled_class_bookings(
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
  -- Replaced when ClassBooking exists. That body cancels BOOKED rows and
  -- writes no session-ledger entry.
  RETURN;
END;
$cancel$;

CREATE FUNCTION app_guard_class_schedule()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NOT app_class_weekdays_valid(NEW.weekdays) THEN
    RAISE EXCEPTION 'class_schedule_guard: weekdays must be distinct values from 0 to 6';
  END IF;
  IF NEW.effective_until < NEW.effective_from
     OR NEW.effective_until > NEW.effective_from + 365 THEN
    RAISE EXCEPTION 'class_schedule_guard: inclusive range is at most 366 days';
  END IF;
  IF NEW.local_end_time <= NEW.local_start_time THEN
    RAISE EXCEPTION 'class_schedule_guard: end time must follow the start time';
  END IF;
  IF NEW.timezone = '' THEN
    RAISE EXCEPTION 'class_schedule_guard: timezone snapshot is required';
  END IF;
  IF NEW.location = ''
     OR NEW.location IS DISTINCT FROM regexp_replace(btrim(NEW.location), '[[:space:]]+', ' ', 'g')
     OR NEW.normalized_location IS DISTINCT FROM upper(NEW.location) THEN
    RAISE EXCEPTION 'class_schedule_guard: location is not normalized';
  END IF;
  IF NEW.capacity < 1 THEN
    RAISE EXCEPTION 'class_schedule_guard: capacity must be positive';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'ACTIVE'
       OR NEW.ended_at IS NOT NULL
       OR NEW.ended_by_membership_id IS NOT NULL
       OR btrim(NEW.reason) <> '' THEN
      RAISE EXCEPTION 'class_schedule_guard: new schedules start active';
    END IF;
    IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
      RAISE EXCEPTION 'class_schedule_guard: actors must match on create';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.timezone IS DISTINCT FROM OLD.timezone THEN
    RAISE EXCEPTION 'class_schedule_guard: identity columns are immutable';
  END IF;
  IF OLD.status <> 'ACTIVE' THEN
    RAISE EXCEPTION 'class_schedule_guard: closed schedules are immutable';
  END IF;
  IF NEW.status = 'ACTIVE' THEN
    IF NEW.ended_at IS NOT NULL
       OR NEW.ended_by_membership_id IS NOT NULL
       OR btrim(NEW.reason) <> '' THEN
      RAISE EXCEPTION 'class_schedule_guard: an active schedule has no end evidence';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status IN ('ENDED', 'CANCELLED') THEN
    IF NEW.ended_at IS NULL
       OR NEW.ended_by_membership_id IS NULL
       OR btrim(NEW.reason) = ''
       OR NEW.class_type_id IS DISTINCT FROM OLD.class_type_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.weekdays IS DISTINCT FROM OLD.weekdays
       OR NEW.local_start_time IS DISTINCT FROM OLD.local_start_time
       OR NEW.local_end_time IS DISTINCT FROM OLD.local_end_time
       OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
       OR NEW.effective_until IS DISTINCT FROM OLD.effective_until
       OR NEW.location IS DISTINCT FROM OLD.location
       OR NEW.location_is_exclusive IS DISTINCT FROM OLD.location_is_exclusive
       OR NEW.capacity IS DISTINCT FROM OLD.capacity THEN
      RAISE EXCEPTION 'class_schedule_guard: closed schedule evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'class_schedule_guard: schedule status transition is invalid';
END;
$guard$;

CREATE FUNCTION app_guard_class_schedule_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'class_schedule_guard: schedules cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_assert_class_schedule_set(schedule_id uuid)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $assert$
DECLARE
  schedule classes_class_schedule%ROWTYPE;
  boundary date;
  local_now time;
BEGIN
  SELECT * INTO schedule
  FROM classes_class_schedule
  WHERE id = schedule_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  boundary := (now() AT TIME ZONE schedule.timezone)::date;
  local_now := (now() AT TIME ZONE schedule.timezone)::time;
  IF (
    SELECT count(*)
    FROM classes_class_occurrence AS occurrence
    WHERE occurrence.gym_id = schedule.gym_id
      AND occurrence.class_schedule_id = schedule.id
  ) > 400 THEN
    RAISE EXCEPTION 'class_schedule_guard: schedule is too large';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM classes_class_occurrence AS occurrence
    WHERE occurrence.gym_id = schedule.gym_id
      AND occurrence.class_schedule_id = schedule.id
      AND occurrence.local_date = ANY (schedule.skipped_local_dates)
  ) THEN
    RAISE EXCEPTION 'class_schedule_guard: a skipped date still has an occurrence';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM generate_series(
      schedule.effective_from, schedule.effective_until, interval '1 day'
    ) AS series(day)
    WHERE (EXTRACT(ISODOW FROM series.day)::integer - 1) = ANY (schedule.weekdays)
      AND (
        series.day::date > boundary
        OR (
          series.day::date = boundary
          AND schedule.local_start_time > local_now
        )
      )
      AND NOT EXISTS (
        SELECT 1
        FROM classes_class_occurrence AS occurrence
        WHERE occurrence.gym_id = schedule.gym_id
          AND occurrence.class_schedule_id = schedule.id
          AND occurrence.local_date = series.day::date
      )
      AND NOT (series.day::date = ANY (schedule.skipped_local_dates))
  ) THEN
    RAISE EXCEPTION 'class_schedule_guard: generated occurrence set is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM classes_class_occurrence AS occurrence
    WHERE occurrence.gym_id = schedule.gym_id
      AND occurrence.class_schedule_id = schedule.id
      AND occurrence.status = 'SCHEDULED'
      AND occurrence.starts_at > now()
      AND (
        occurrence.class_type_id IS DISTINCT FROM schedule.class_type_id
        OR occurrence.trainer_staff_profile_id IS DISTINCT FROM schedule.trainer_staff_profile_id
        OR occurrence.location IS DISTINCT FROM schedule.location
        OR occurrence.normalized_location IS DISTINCT FROM schedule.normalized_location
        OR occurrence.location_is_exclusive IS DISTINCT FROM schedule.location_is_exclusive
        OR occurrence.capacity IS DISTINCT FROM schedule.capacity
        OR NOT (
          (EXTRACT(ISODOW FROM occurrence.local_date)::integer - 1) = ANY (schedule.weekdays)
        )
        OR occurrence.local_date < schedule.effective_from
        OR occurrence.local_date > schedule.effective_until
      )
  ) THEN
    RAISE EXCEPTION 'class_schedule_guard: future occurrence evidence does not match the schedule';
  END IF;
END;
$assert$;

CREATE FUNCTION app_guard_class_schedule_set()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  PERFORM app_assert_class_schedule_set(NEW.id);
  RETURN NULL;
END;
$guard$;

CREATE FUNCTION app_guard_class_occurrence()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.ends_at <= NEW.starts_at THEN
    RAISE EXCEPTION 'class_occurrence_guard: end must follow the start';
  END IF;
  IF NEW.capacity < 1 THEN
    RAISE EXCEPTION 'class_occurrence_guard: capacity must be positive';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'SCHEDULED'
       OR NEW.cancelled_at IS NOT NULL
       OR NEW.cancelled_by_membership_id IS NOT NULL
       OR btrim(NEW.reason) <> '' THEN
      RAISE EXCEPTION 'class_occurrence_guard: new occurrences start scheduled';
    END IF;
    IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
      RAISE EXCEPTION 'class_occurrence_guard: actors must match on create';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.class_schedule_id IS DISTINCT FROM OLD.class_schedule_id
     OR NEW.class_type_id IS DISTINCT FROM OLD.class_type_id
     OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
     OR NEW.location IS DISTINCT FROM OLD.location
     OR NEW.normalized_location IS DISTINCT FROM OLD.normalized_location
     OR NEW.location_is_exclusive IS DISTINCT FROM OLD.location_is_exclusive
     OR NEW.capacity IS DISTINCT FROM OLD.capacity
     OR NEW.starts_at IS DISTINCT FROM OLD.starts_at
     OR NEW.ends_at IS DISTINCT FROM OLD.ends_at
     OR NEW.local_date IS DISTINCT FROM OLD.local_date THEN
    RAISE EXCEPTION 'class_occurrence_guard: concrete instants are immutable';
  END IF;
  IF OLD.status <> 'SCHEDULED' THEN
    RAISE EXCEPTION 'class_occurrence_guard: closed occurrences are immutable';
  END IF;
  IF NEW.status = 'CANCELLED' THEN
    IF NEW.cancelled_at IS NULL
       OR NEW.cancelled_by_membership_id IS NULL
       OR btrim(NEW.reason) = '' THEN
      RAISE EXCEPTION 'class_occurrence_guard: cancellation evidence is incomplete';
    END IF;
    IF app_class_occurrence_has_terminal_booking(NEW.gym_id, NEW.id) THEN
      RAISE EXCEPTION 'class_occurrence_guard: attended or no-show bookings block cancel';
    END IF;
    PERFORM app_cancel_scheduled_class_bookings(
      NEW.gym_id,
      NEW.id,
      NEW.cancelled_by_membership_id,
      NEW.cancelled_at,
      NEW.reason
    );
    RETURN NEW;
  END IF;
  IF NEW.status = 'COMPLETED' THEN
    RAISE EXCEPTION 'class_occurrence_guard: completed is not published';
  END IF;
  RAISE EXCEPTION 'class_occurrence_guard: occurrence status transition is invalid';
END;
$guard$;

CREATE FUNCTION app_guard_class_occurrence_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF OLD.status <> 'SCHEDULED' OR OLD.starts_at <= now() THEN
    RAISE EXCEPTION 'class_occurrence_guard: only a future scheduled occurrence can be deleted';
  END IF;
  IF app_class_occurrence_blocks_regeneration(OLD.gym_id, OLD.id) THEN
    RAISE EXCEPTION 'class_occurrence_guard: a booked occurrence cannot be deleted';
  END IF;
  RETURN OLD;
END;
$guard$;

CREATE FUNCTION app_guard_class_occurrence_set()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM app_assert_class_schedule_set(OLD.class_schedule_id);
  ELSE
    PERFORM app_assert_class_schedule_set(NEW.class_schedule_id);
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE TRIGGER class_schedule_guard_trg
BEFORE INSERT OR UPDATE ON classes_class_schedule
FOR EACH ROW EXECUTE FUNCTION app_guard_class_schedule();

CREATE TRIGGER class_schedule_delete_trg
BEFORE DELETE ON classes_class_schedule
FOR EACH ROW EXECUTE FUNCTION app_guard_class_schedule_delete();

CREATE CONSTRAINT TRIGGER class_schedule_set_trg
AFTER INSERT OR UPDATE ON classes_class_schedule
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_guard_class_schedule_set();

CREATE TRIGGER class_occurrence_guard_trg
BEFORE INSERT OR UPDATE ON classes_class_occurrence
FOR EACH ROW EXECUTE FUNCTION app_guard_class_occurrence();

CREATE TRIGGER class_occurrence_delete_trg
BEFORE DELETE ON classes_class_occurrence
FOR EACH ROW EXECUTE FUNCTION app_guard_class_occurrence_delete();

CREATE CONSTRAINT TRIGGER class_occurrence_set_trg
AFTER INSERT OR DELETE OR UPDATE ON classes_class_occurrence
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_guard_class_occurrence_set();

ALTER TABLE classes_class_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes_class_schedule FORCE ROW LEVEL SECURITY;
CREATE POLICY classes_class_schedule_select ON classes_class_schedule
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY classes_class_schedule_insert ON classes_class_schedule
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_schedule_update ON classes_class_schedule
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_schedule_delete ON classes_class_schedule
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE classes_class_occurrence ENABLE ROW LEVEL SECURITY;
ALTER TABLE classes_class_occurrence FORCE ROW LEVEL SECURITY;
CREATE POLICY classes_class_occurrence_select ON classes_class_occurrence
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY classes_class_occurrence_insert ON classes_class_occurrence
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_occurrence_update ON classes_class_occurrence
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY classes_class_occurrence_delete ON classes_class_occurrence
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE classes_class_schedule FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE classes_class_schedule TO gym_runtime;
REVOKE ALL ON TABLE classes_class_occurrence FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE classes_class_occurrence TO gym_runtime;

REVOKE ALL ON FUNCTION app_class_weekdays_valid(smallint[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_class_occurrence_blocks_regeneration(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_class_occurrence_has_terminal_booking(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_cancel_scheduled_class_bookings(uuid, uuid, uuid, timestamp with time zone, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_schedule() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_schedule_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_assert_class_schedule_set(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_schedule_set() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_occurrence() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_occurrence_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_class_occurrence_set() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_class_weekdays_valid(smallint[]) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_class_occurrence_blocks_regeneration(uuid, uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_class_occurrence_has_terminal_booking(uuid, uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_cancel_scheduled_class_bookings(uuid, uuid, uuid, timestamp with time zone, text) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_schedule() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_schedule_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_assert_class_schedule_set(uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_schedule_set() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_occurrence() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_occurrence_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_class_occurrence_set() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_class_occurrence_set() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_occurrence_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_occurrence() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_schedule_set() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_assert_class_schedule_set(uuid) FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_schedule_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_class_schedule() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_cancel_scheduled_class_bookings(uuid, uuid, uuid, timestamp with time zone, text) FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_class_occurrence_has_terminal_booking(uuid, uuid) FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_class_occurrence_blocks_regeneration(uuid, uuid) FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_class_weekdays_valid(smallint[]) FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE classes_class_occurrence FROM gym_runtime;
REVOKE ALL ON TABLE classes_class_schedule FROM gym_runtime;
DROP POLICY IF EXISTS classes_class_occurrence_delete ON classes_class_occurrence;
DROP POLICY IF EXISTS classes_class_occurrence_update ON classes_class_occurrence;
DROP POLICY IF EXISTS classes_class_occurrence_insert ON classes_class_occurrence;
DROP POLICY IF EXISTS classes_class_occurrence_select ON classes_class_occurrence;
ALTER TABLE classes_class_occurrence NO FORCE ROW LEVEL SECURITY;
ALTER TABLE classes_class_occurrence DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS classes_class_schedule_delete ON classes_class_schedule;
DROP POLICY IF EXISTS classes_class_schedule_update ON classes_class_schedule;
DROP POLICY IF EXISTS classes_class_schedule_insert ON classes_class_schedule;
DROP POLICY IF EXISTS classes_class_schedule_select ON classes_class_schedule;
ALTER TABLE classes_class_schedule NO FORCE ROW LEVEL SECURITY;
ALTER TABLE classes_class_schedule DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS class_occurrence_set_trg ON classes_class_occurrence;
DROP TRIGGER IF EXISTS class_occurrence_delete_trg ON classes_class_occurrence;
DROP TRIGGER IF EXISTS class_occurrence_guard_trg ON classes_class_occurrence;
DROP TRIGGER IF EXISTS class_schedule_set_trg ON classes_class_schedule;
DROP TRIGGER IF EXISTS class_schedule_delete_trg ON classes_class_schedule;
DROP TRIGGER IF EXISTS class_schedule_guard_trg ON classes_class_schedule;
DROP FUNCTION IF EXISTS app_guard_class_occurrence_set();
DROP FUNCTION IF EXISTS app_guard_class_occurrence_delete();
DROP FUNCTION IF EXISTS app_guard_class_occurrence();
DROP FUNCTION IF EXISTS app_guard_class_schedule_set();
DROP FUNCTION IF EXISTS app_assert_class_schedule_set(uuid);
DROP FUNCTION IF EXISTS app_guard_class_schedule_delete();
DROP FUNCTION IF EXISTS app_guard_class_schedule();
DROP FUNCTION IF EXISTS app_cancel_scheduled_class_bookings(uuid, uuid, uuid, timestamp with time zone, text);
DROP FUNCTION IF EXISTS app_class_occurrence_has_terminal_booking(uuid, uuid);
DROP FUNCTION IF EXISTS app_class_occurrence_blocks_regeneration(uuid, uuid);
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_weekday_ck;
DROP FUNCTION IF EXISTS app_class_weekdays_valid(smallint[]);
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_location_overlap_excl;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_trainer_overlap_excl;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_cancelled_by_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_updated_by_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_created_by_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_trainer_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_class_type_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_schedule_same_gym_fk;
ALTER TABLE classes_class_occurrence DROP CONSTRAINT IF EXISTS class_occ_location_ck;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_ended_by_same_gym_fk;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_updated_by_same_gym_fk;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_created_by_same_gym_fk;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_trainer_same_gym_fk;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_class_type_same_gym_fk;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_location_ck;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_timezone_ck;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_weekday_ck;
ALTER TABLE classes_class_schedule DROP CONSTRAINT IF EXISTS class_sched_span_ck;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0003_class_schedule_and_occurrence"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
