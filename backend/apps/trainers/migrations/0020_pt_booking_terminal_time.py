"""Reject future PT attendance and no-show at the booking guard.

Attendance requires statement time at or after starts_at. No-show requires
statement time at or after ends_at. Reverse restores the 0019 function body.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_pt_booking()
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
    IF NEW.status = 'ATTENDED' AND statement_timestamp() < NEW.starts_at THEN
      RAISE EXCEPTION 'pt_booking_guard: attendance is before the start';
    END IF;
    IF NEW.status = 'NO_SHOW' AND statement_timestamp() < NEW.ends_at THEN
      RAISE EXCEPTION 'pt_booking_guard: no-show is before the end';
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
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_pt_booking()
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
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0019_booking_ledger_links"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
