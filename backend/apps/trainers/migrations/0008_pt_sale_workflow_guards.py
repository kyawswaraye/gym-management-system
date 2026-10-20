"""Open draft, submit, approve, reject, and cancel for a PT sale.

Posted sales require one accrued commission. A linked payment must be posted
with one receipt, and a cash payment must have one inbound movement. Rejected
sales have none of those effects.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_reason_ck;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_reason_ck
  CHECK (
    (
      status IN ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED')
      AND reason = ''
    )
    OR (
      status = 'CANCELLED'
      AND reason <> ''
      AND reason = regexp_replace(btrim(reason), '[[:space:]]+', ' ', 'g')
    )
  );
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_rejection_reason_ck
  CHECK (
    (
      status = 'REJECTED'
      AND rejection_reason <> ''
      AND rejection_reason = regexp_replace(
        btrim(rejection_reason), '[[:space:]]+', ' ', 'g'
      )
    )
    OR (status <> 'REJECTED' AND rejection_reason = '')
  );
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_payment_same_gym_fk
  FOREIGN KEY (gym_id, payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_submitted_by_same_gym_fk
  FOREIGN KEY (gym_id, submitted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_approved_by_same_gym_fk
  FOREIGN KEY (gym_id, approved_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_rejected_by_same_gym_fk
  FOREIGN KEY (gym_id, rejected_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_reversal_same_gym_fk
  FOREIGN KEY (gym_id, reversal_pt_sale_id)
  REFERENCES trainers_pt_sale (gym_id, id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION app_guard_pt_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status IN ('CANCELLED', 'POSTED', 'REJECTED') THEN
    RAISE EXCEPTION 'pt_sale_guard: closed sales are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED' THEN
    IF NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.service_date IS DISTINCT FROM OLD.service_date
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.gross_fee IS DISTINCT FROM OLD.gross_fee
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
       OR NEW.payment_id IS NOT NULL
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.submitted_at IS NOT NULL
       OR NEW.cancelled_at IS NULL
       OR NEW.cancelled_by_membership_id IS NULL
       OR btrim(NEW.reason) = '' THEN
      RAISE EXCEPTION 'pt_sale_guard: cancellation evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.service_date IS DISTINCT FROM OLD.service_date
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.gross_fee IS DISTINCT FROM OLD.gross_fee
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.submitted_at IS NULL
       OR NEW.submitted_by_membership_id IS NULL
       OR NEW.reason <> '' THEN
      RAISE EXCEPTION 'pt_sale_guard: submission evidence is incomplete';
    END IF;
  ELSIF TG_OP = 'UPDATE' AND OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED' THEN
    IF NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.service_date IS DISTINCT FROM OLD.service_date
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.gross_fee IS DISTINCT FROM OLD.gross_fee
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
       OR NEW.submitted_by_membership_id IS DISTINCT FROM OLD.submitted_by_membership_id
       OR NEW.approved_at IS NULL
       OR NEW.approved_by_membership_id IS NULL
       OR NEW.posted_at IS NULL
       OR NEW.approved_by_membership_id = NEW.created_by_membership_id
       OR NEW.approved_by_membership_id = NEW.submitted_by_membership_id THEN
      RAISE EXCEPTION 'pt_sale_guard: posting evidence is incomplete';
    END IF;
  ELSIF TG_OP = 'UPDATE' AND OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED' THEN
    IF NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
       OR NEW.service_date IS DISTINCT FROM OLD.service_date
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.gross_fee IS DISTINCT FROM OLD.gross_fee
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
       OR NEW.submitted_by_membership_id IS DISTINCT FROM OLD.submitted_by_membership_id
       OR NEW.rejected_at IS NULL
       OR NEW.rejected_by_membership_id IS NULL
       OR btrim(NEW.rejection_reason) = ''
       OR NEW.posted_at IS NOT NULL
       OR NEW.rejected_by_membership_id = NEW.created_by_membership_id
       OR NEW.rejected_by_membership_id = NEW.submitted_by_membership_id THEN
      RAISE EXCEPTION 'pt_sale_guard: rejection evidence is incomplete';
    END IF;
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    RAISE EXCEPTION 'pt_sale_guard: status transition is not allowed';
  END IF;
  IF TG_OP = 'INSERT' AND (
    NEW.status <> 'DRAFT'
    OR NEW.payment_id IS NOT NULL
    OR NEW.submitted_at IS NOT NULL
    OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL
    OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL
    OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.rejection_reason <> ''
    OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL
    OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.reversal_pt_sale_id IS NOT NULL
    OR NEW.reason <> ''
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: inserts must be drafts';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL
    OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL
    OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL
    OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.rejection_reason <> ''
    OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL
    OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.reversal_pt_sale_id IS NOT NULL
    OR NEW.reason <> ''
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: draft evidence must stay empty';
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
      WHERE gym_id = NEW.gym_id AND id = NEW.trainer_staff_profile_id
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: commission snapshot must match the trainer';
    END IF;
  ELSIF NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id THEN
    IF NEW.commission_percent_snapshot IS DISTINCT FROM (
      SELECT default_commission_percent
      FROM tenants_staff_profile
      WHERE gym_id = NEW.gym_id AND id = NEW.trainer_staff_profile_id
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

CREATE FUNCTION app_pt_sale_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale public.trainers_pt_sale%ROWTYPE;
  payment public.members_payment%ROWTYPE;
  commission_count integer;
  receipt_count integer;
BEGIN
  SELECT * INTO sale FROM public.trainers_pt_sale WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  SELECT count(*) INTO commission_count
  FROM public.trainers_pt_commission
  WHERE gym_id = sale.gym_id AND pt_sale_id = sale.id;
  IF sale.status = 'POSTED' THEN
    IF commission_count <> 1 THEN
      RAISE EXCEPTION 'pt_sale_guard: a posted sale requires one commission';
    END IF;
    IF sale.payment_id IS NULL THEN
      IF EXISTS (
        SELECT 1 FROM public.members_payment
        WHERE gym_id = sale.gym_id AND pt_sale_id = sale.id AND status <> 'CANCELLED'
      ) THEN
        RAISE EXCEPTION 'pt_sale_guard: posted sale payment link is missing';
      END IF;
    ELSE
      SELECT * INTO payment
      FROM public.members_payment
      WHERE gym_id = sale.gym_id AND id = sale.payment_id;
      IF NOT FOUND
         OR payment.pt_sale_id IS DISTINCT FROM sale.id
         OR payment.purpose IS DISTINCT FROM 'PT'
         OR payment.entry_type IS DISTINCT FROM 'CHARGE'
         OR payment.status IS DISTINCT FROM 'POSTED'
         OR payment.member_id IS DISTINCT FROM sale.member_id
         OR payment.membership_id IS NOT NULL
         OR payment.currency IS DISTINCT FROM sale.currency THEN
        RAISE EXCEPTION 'pt_sale_guard: posted sale payment is inconsistent';
      END IF;
      SELECT count(*) INTO receipt_count
      FROM public.members_receipt
      WHERE gym_id = sale.gym_id AND payment_id = payment.id AND status = 'ISSUED';
      IF receipt_count <> 1 THEN
        RAISE EXCEPTION 'pt_sale_guard: posted sale requires one receipt';
      END IF;
      IF payment.method = 'CASH' THEN
        IF payment.cash_transaction_id IS NULL OR NOT EXISTS (
          SELECT 1 FROM public.cashbook_cash_transaction AS movement
          WHERE movement.gym_id = sale.gym_id
            AND movement.id = payment.cash_transaction_id
            AND movement.source_type = 'PAYMENT'
            AND movement.source_id = payment.id
            AND movement.direction = 'IN'
        ) THEN
          RAISE EXCEPTION 'pt_sale_guard: cash payment requires one movement';
        END IF;
      ELSIF payment.cash_transaction_id IS NOT NULL THEN
        RAISE EXCEPTION 'pt_sale_guard: non-cash payment has no movement';
      END IF;
    END IF;
  ELSIF sale.status = 'REJECTED' THEN
    IF commission_count <> 0 THEN
      RAISE EXCEPTION 'pt_sale_guard: a rejected sale has no commission';
    END IF;
    IF sale.payment_id IS NULL THEN
      IF EXISTS (
        SELECT 1 FROM public.members_payment
        WHERE gym_id = sale.gym_id AND pt_sale_id = sale.id
      ) THEN
        RAISE EXCEPTION 'pt_sale_guard: rejected sale payment is inconsistent';
      END IF;
    ELSE
      SELECT * INTO payment
      FROM public.members_payment
      WHERE gym_id = sale.gym_id AND id = sale.payment_id;
      IF NOT FOUND
         OR payment.status IS DISTINCT FROM 'REJECTED'
         OR payment.pt_sale_id IS DISTINCT FROM sale.id
         OR payment.cash_transaction_id IS NOT NULL
         OR EXISTS (
           SELECT 1 FROM public.members_receipt
           WHERE gym_id = sale.gym_id AND payment_id = payment.id
         ) THEN
        RAISE EXCEPTION 'pt_sale_guard: rejected sale payment is inconsistent';
      END IF;
    END IF;
  ELSIF commission_count <> 0 THEN
    RAISE EXCEPTION 'pt_sale_guard: commission exists only for a posted sale';
  ELSIF sale.status = 'DRAFT' AND sale.payment_id IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.members_payment
      WHERE gym_id = sale.gym_id
        AND id = sale.payment_id
        AND pt_sale_id = sale.id
        AND status = 'DRAFT'
        AND purpose = 'PT'
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: draft sale payment is inconsistent';
    END IF;
  ELSIF sale.status = 'PENDING_APPROVAL' AND sale.payment_id IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.members_payment
      WHERE gym_id = sale.gym_id
        AND id = sale.payment_id
        AND pt_sale_id = sale.id
        AND status = 'PENDING_APPROVAL'
        AND purpose = 'PT'
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: submitted sale payment is inconsistent';
    END IF;
  ELSIF sale.status = 'CANCELLED' AND sale.payment_id IS NOT NULL THEN
    RAISE EXCEPTION 'pt_sale_guard: a cancelled sale has no payment';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER pt_sale_commit_trg
AFTER INSERT OR UPDATE ON trainers_pt_sale
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_pt_sale_commit();

ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_sale_same_gym_fk
  FOREIGN KEY (gym_id, pt_sale_id)
  REFERENCES trainers_pt_sale (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_trainer_same_gym_fk
  FOREIGN KEY (gym_id, trainer_staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_amounts_ck
  CHECK (
    commission_amount = round(gross_fee_snapshot * commission_percent_snapshot / 100, 2)
    AND gym_share = gross_fee_snapshot - commission_amount
    AND outstanding_amount = commission_amount - paid_amount
    AND paid_amount <= commission_amount
    AND currency ~ '^[A-Z]{3}$'
  );

CREATE FUNCTION app_guard_pt_commission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'pt_commission_guard: commission rows are immutable';
  END IF;
  IF NEW.status <> 'ACCRUED'
     OR NEW.paid_amount <> 0
     OR NEW.paid_at IS NOT NULL
     OR NEW.commission_expense_id IS NOT NULL
     OR NEW.reversal_commission_id IS NOT NULL THEN
    RAISE EXCEPTION 'pt_commission_guard: only an accrued commission can be inserted';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM trainers_pt_sale AS sale
    WHERE sale.gym_id = NEW.gym_id
      AND sale.id = NEW.pt_sale_id
      AND sale.status = 'POSTED'
      AND sale.trainer_staff_profile_id = NEW.trainer_staff_profile_id
      AND sale.gross_fee = NEW.gross_fee_snapshot
      AND sale.commission_percent_snapshot = NEW.commission_percent_snapshot
      AND sale.currency = NEW.currency
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: commission does not match the posted sale';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_pt_commission_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'pt_commission_guard: commissions cannot be deleted';
END;
$guard$;

CREATE TRIGGER pt_commission_guard_trg
BEFORE INSERT OR UPDATE ON trainers_pt_commission
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_commission();
CREATE TRIGGER pt_commission_delete_trg
BEFORE DELETE ON trainers_pt_commission
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_commission_delete();

ALTER TABLE trainers_pt_commission ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_commission FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_pt_commission_select ON trainers_pt_commission
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_insert ON trainers_pt_commission
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_update ON trainers_pt_commission
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_delete ON trainers_pt_commission
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_pt_commission FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE trainers_pt_commission TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_pt_commission() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_commission_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_pt_sale_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_pt_sale_commit() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_pt_sale_commit() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_commission_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_commission() FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_pt_commission FROM gym_runtime;
DROP POLICY IF EXISTS trainers_pt_commission_delete ON trainers_pt_commission;
DROP POLICY IF EXISTS trainers_pt_commission_update ON trainers_pt_commission;
DROP POLICY IF EXISTS trainers_pt_commission_insert ON trainers_pt_commission;
DROP POLICY IF EXISTS trainers_pt_commission_select ON trainers_pt_commission;
ALTER TABLE trainers_pt_commission NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_commission DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS pt_commission_delete_trg ON trainers_pt_commission;
DROP TRIGGER IF EXISTS pt_commission_guard_trg ON trainers_pt_commission;
DROP FUNCTION IF EXISTS app_guard_pt_commission_delete();
DROP FUNCTION IF EXISTS app_guard_pt_commission();
ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_amounts_ck;
ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_created_by_same_gym_fk;
ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_trainer_same_gym_fk;
ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_sale_same_gym_fk;
DROP TRIGGER IF EXISTS pt_sale_commit_trg ON trainers_pt_sale;
DROP FUNCTION IF EXISTS app_pt_sale_commit();
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_reversal_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_rejected_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_approved_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_submitted_by_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_payment_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_rejection_reason_ck;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0007_pt_sale_payment_link"),
        ("members", "0023_pt_payment_guard"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
