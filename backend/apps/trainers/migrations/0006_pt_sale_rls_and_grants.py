"""Lock draft PT sales to the creating gym.

Payment and reversal identifiers stay nullable uuids with no foreign key.
Checks keep them null until posting and reversal are published.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_currency_ck
  CHECK (currency ~ '^[A-Z]{3}$');
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_description_ck
  CHECK (
    description <> ''
    AND description = regexp_replace(btrim(description), '[[:space:]]+', ' ', 'g')
  );
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_reason_ck
  CHECK (
    (status = 'DRAFT' AND reason = '')
    OR (
      status = 'CANCELLED'
      AND reason <> ''
      AND reason = regexp_replace(btrim(reason), '[[:space:]]+', ' ', 'g')
    )
  );

CREATE FUNCTION app_guard_pt_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status = 'CANCELLED' THEN
    RAISE EXCEPTION 'pt_sale_guard: cancelled sales are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED' THEN
    IF NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.service_date IS DISTINCT FROM OLD.service_date
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.gross_fee IS DISTINCT FROM OLD.gross_fee
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.cancelled_at IS NULL
       OR NEW.cancelled_by_membership_id IS NULL
       OR btrim(NEW.reason) = '' THEN
      RAISE EXCEPTION 'pt_sale_guard: cancellation evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    RAISE EXCEPTION 'pt_sale_guard: only a draft can be cancelled';
  END IF;
  IF NEW.status <> 'DRAFT' THEN
    RAISE EXCEPTION 'pt_sale_guard: inserts must be drafts';
  END IF;
  IF NEW.payment_id IS NOT NULL
     OR NEW.submitted_at IS NOT NULL
     OR NEW.submitted_by_membership_id IS NOT NULL
     OR NEW.approved_at IS NOT NULL
     OR NEW.approved_by_membership_id IS NOT NULL
     OR NEW.rejected_at IS NOT NULL
     OR NEW.rejected_by_membership_id IS NOT NULL
     OR NEW.rejection_reason <> ''
     OR NEW.posted_at IS NOT NULL
     OR NEW.reversal_pt_sale_id IS NOT NULL
     OR NEW.cancelled_at IS NOT NULL
     OR NEW.cancelled_by_membership_id IS NOT NULL
     OR NEW.reason <> '' THEN
    RAISE EXCEPTION 'pt_sale_guard: unpublished sale evidence must stay empty';
  END IF;
  IF NEW.currency !~ '^[A-Z]{3}$'
     OR NEW.currency IS DISTINCT FROM (
       SELECT currency FROM tenants_gym WHERE id = NEW.gym_id
     ) THEN
    RAISE EXCEPTION 'pt_sale_guard: currency must match the gym';
  END IF;
  IF NEW.gross_fee <= 0 THEN
    RAISE EXCEPTION 'pt_sale_guard: gross fee must be positive';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM members_member AS member
    WHERE member.gym_id = NEW.gym_id
      AND member.id = NEW.member_id
      AND member.status = 'ACTIVE'
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: member is not active';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM tenants_staff_profile AS profile
    JOIN tenants_gym_membership AS membership
      ON membership.gym_id = profile.gym_id
     AND membership.id = profile.gym_membership_id
    JOIN tenants_gym_membership_role AS membership_role
      ON membership_role.gym_id = membership.gym_id
     AND membership_role.gym_membership_id = membership.id
    JOIN tenants_role AS role
      ON role.gym_id = membership_role.gym_id
     AND role.id = membership_role.role_id
    JOIN tenants_system_role_template AS template
      ON template.id = role.source_template_id
    WHERE profile.gym_id = NEW.gym_id
      AND profile.id = NEW.trainer_staff_profile_id
      AND profile.status = 'ACTIVE'
      AND membership.status = 'ACTIVE'
      AND role.is_active
      AND role.key = 'TRAINER'
      AND template.key = 'TRAINER'
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: trainer is not eligible';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.commission_percent_snapshot IS DISTINCT FROM (
      SELECT default_commission_percent
      FROM tenants_staff_profile
      WHERE gym_id = NEW.gym_id
        AND id = NEW.trainer_staff_profile_id
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: commission snapshot must match the trainer';
    END IF;
  ELSIF NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id THEN
    IF NEW.commission_percent_snapshot IS DISTINCT FROM (
      SELECT default_commission_percent
      FROM tenants_staff_profile
      WHERE gym_id = NEW.gym_id
        AND id = NEW.trainer_staff_profile_id
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: commission snapshot must match the trainer';
    END IF;
  ELSIF NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot THEN
    RAISE EXCEPTION 'pt_sale_guard: commission snapshot changes only with the trainer';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM trainers_trainer_assignment AS assignment
    WHERE assignment.gym_id = NEW.gym_id
      AND assignment.trainer_staff_profile_id = NEW.trainer_staff_profile_id
      AND assignment.member_id = NEW.member_id
      AND assignment.status = 'ACTIVE'
      AND assignment.effective_from <= NEW.service_date
      AND (
        assignment.effective_until IS NULL
        OR assignment.effective_until >= NEW.service_date
      )
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: assignment is not effective';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.gym_id IS DISTINCT FROM OLD.gym_id
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: sale provenance is immutable';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_pt_sale_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'pt_sale_guard: PT sales cannot be deleted';
END;
$guard$;

CREATE TRIGGER pt_sale_guard_trg
BEFORE INSERT OR UPDATE ON trainers_pt_sale
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_sale();

CREATE TRIGGER pt_sale_delete_trg
BEFORE DELETE ON trainers_pt_sale
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_sale_delete();

ALTER TABLE trainers_pt_sale ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_sale FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_pt_sale_select ON trainers_pt_sale
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_sale_insert ON trainers_pt_sale
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_sale_update ON trainers_pt_sale
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_sale_delete ON trainers_pt_sale
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_pt_sale FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_pt_sale TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_pt_sale() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_sale_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_pt_sale() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_sale_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_pt_sale_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_sale() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_pt_sale FROM gym_runtime;
DROP POLICY IF EXISTS trainers_pt_sale_delete ON trainers_pt_sale;
DROP POLICY IF EXISTS trainers_pt_sale_update ON trainers_pt_sale;
DROP POLICY IF EXISTS trainers_pt_sale_insert ON trainers_pt_sale;
DROP POLICY IF EXISTS trainers_pt_sale_select ON trainers_pt_sale;
ALTER TABLE trainers_pt_sale NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_sale DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS pt_sale_delete_trg ON trainers_pt_sale;
DROP TRIGGER IF EXISTS pt_sale_guard_trg ON trainers_pt_sale;
DROP FUNCTION IF EXISTS app_guard_pt_sale_delete();
DROP FUNCTION IF EXISTS app_guard_pt_sale();
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_reason_ck;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_description_ck;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_currency_ck;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_cancelled_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_updated_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_created_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_trainer_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_member_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0005_pt_sale"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
