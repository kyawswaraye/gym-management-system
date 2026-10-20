"""Reject future class attendance and no-show at the booking guard.

Attendance requires statement time at or after the occurrence start. No-show
requires statement time at or after the occurrence end. Reverse restores the
0006 function body.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_class_booking()
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
    IF NEW.status = 'ATTENDED' AND statement_timestamp() < occurrence.starts_at THEN
      RAISE EXCEPTION 'class_booking_guard: attendance is before the start';
    END IF;
    IF NEW.status = 'NO_SHOW' AND statement_timestamp() < occurrence.ends_at THEN
      RAISE EXCEPTION 'class_booking_guard: no-show is before the end';
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
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_class_booking()
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
"""


class Migration(migrations.Migration):
    dependencies = [
        ("classes", "0006_class_booking_guards"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
