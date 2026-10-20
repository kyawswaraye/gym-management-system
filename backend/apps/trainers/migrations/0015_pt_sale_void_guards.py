"""Allow one posted PT sale to become void with its linked effects.

Runtime may move a posted sale to void only with actor, time, and reason.
The same commit must void the commission after every payout child is void,
reverse a linked payment, and refund an unconsumed sale grant. Attendance,
no-show, and expiry ledger rows stay unpublished. Reversing this migration
restores the trigger functions and checks from before these void rules.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_voided_by_same_gym_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_reason_ck;
ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_reason_ck
  CHECK (
    (
      status IN ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED')
      AND reason = ''
    )
    OR (
      status IN ('CANCELLED', 'VOID')
      AND reason <> ''
      AND reason = regexp_replace(btrim(reason), '[[:space:]]+', ' ', 'g')
    )
  );

ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_amounts_ck;
ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_amounts_ck
  CHECK (
    commission_amount = round(gross_fee_snapshot * commission_percent_snapshot / 100, 2)
    AND gym_share = gross_fee_snapshot - commission_amount
    AND paid_amount <= commission_amount
    AND currency ~ '^[A-Z]{3}$'
    AND (
      (status = 'VOID' AND paid_amount = 0 AND outstanding_amount = 0)
      OR outstanding_amount = commission_amount - paid_amount
    )
  );

CREATE OR REPLACE FUNCTION app_guard_pt_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status IN ('CANCELLED', 'REJECTED', 'VOID') THEN
    RAISE EXCEPTION 'pt_sale_guard: closed sales are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'pt_sale_guard: closed sales are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' THEN
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
       OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
       OR NEW.approved_by_membership_id IS DISTINCT FROM OLD.approved_by_membership_id
       OR NEW.posted_at IS DISTINCT FROM OLD.posted_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.voided_at IS NULL
       OR NEW.voided_by_membership_id IS NULL
       OR btrim(NEW.reason) = ''
       OR NEW.reversal_pt_sale_id IS NOT NULL
       OR NEW.cancelled_at IS NOT NULL
       OR NEW.cancelled_by_membership_id IS NOT NULL THEN
      RAISE EXCEPTION 'pt_sale_guard: void evidence is incomplete';
    END IF;
    RETURN NEW;
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
    OR NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
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
    OR NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
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

CREATE OR REPLACE FUNCTION app_pt_sale_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale public.trainers_pt_sale%ROWTYPE;
  payment public.members_payment%ROWTYPE;
  reversal public.members_payment%ROWTYPE;
  commission_count integer;
  receipt_count integer;
  package_count integer;
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
  ELSIF sale.status = 'VOID' THEN
    IF sale.voided_at IS NULL
       OR sale.voided_by_membership_id IS NULL
       OR btrim(sale.reason) = ''
       OR sale.reversal_pt_sale_id IS NOT NULL
       OR commission_count <> 1 THEN
      RAISE EXCEPTION 'pt_sale_guard: void evidence is incomplete';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.trainers_pt_commission AS commission
      WHERE commission.gym_id = sale.gym_id
        AND commission.pt_sale_id = sale.id
        AND commission.status = 'VOID'
        AND commission.paid_amount = 0
        AND commission.outstanding_amount = 0
        AND commission.paid_at IS NULL
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: voided sale commission is not void';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.trainers_pt_commission_payment AS payout
      JOIN public.trainers_pt_commission AS commission
        ON commission.gym_id = payout.gym_id
       AND commission.id = payout.pt_commission_id
      WHERE commission.gym_id = sale.gym_id
        AND commission.pt_sale_id = sale.id
        AND payout.status IS DISTINCT FROM 'VOID'
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: voided sale still has a posted payout';
    END IF;
    IF sale.payment_id IS NULL THEN
      IF EXISTS (
        SELECT 1 FROM public.members_payment
        WHERE gym_id = sale.gym_id AND pt_sale_id = sale.id AND status <> 'CANCELLED'
      ) THEN
        RAISE EXCEPTION 'pt_sale_guard: voided sale payment is inconsistent';
      END IF;
    ELSE
      SELECT * INTO payment
      FROM public.members_payment
      WHERE gym_id = sale.gym_id AND id = sale.payment_id;
      IF NOT FOUND
         OR payment.pt_sale_id IS DISTINCT FROM sale.id
         OR payment.purpose IS DISTINCT FROM 'PT'
         OR payment.entry_type IS DISTINCT FROM 'CHARGE'
         OR payment.status IS DISTINCT FROM 'VOID'
         OR payment.reversal_payment_id IS NULL
         OR payment.voided_at IS NULL THEN
        RAISE EXCEPTION 'pt_sale_guard: voided sale payment is inconsistent';
      END IF;
      SELECT * INTO reversal
      FROM public.members_payment
      WHERE gym_id = sale.gym_id AND id = payment.reversal_payment_id;
      IF NOT FOUND
         OR reversal.original_payment_id IS DISTINCT FROM payment.id
         OR reversal.entry_type IS DISTINCT FROM 'REVERSAL'
         OR reversal.status IS DISTINCT FROM 'POSTED'
         OR reversal.purpose IS DISTINCT FROM 'PT'
         OR reversal.pt_sale_id IS NOT NULL
         OR reversal.amount IS DISTINCT FROM payment.amount
         OR reversal.method IS DISTINCT FROM payment.method THEN
        RAISE EXCEPTION 'pt_sale_guard: voided sale payment reversal is missing';
      END IF;
      IF EXISTS (
        SELECT 1 FROM public.members_receipt
        WHERE gym_id = sale.gym_id AND payment_id = payment.id AND status = 'ISSUED'
      ) OR NOT EXISTS (
        SELECT 1 FROM public.members_receipt
        WHERE gym_id = sale.gym_id AND payment_id = reversal.id AND status = 'ISSUED'
      ) THEN
        RAISE EXCEPTION 'pt_sale_guard: voided sale receipt evidence is incomplete';
      END IF;
      IF payment.method = 'CASH' THEN
        IF NOT EXISTS (
          SELECT 1 FROM public.cashbook_cash_transaction AS movement
          WHERE movement.gym_id = sale.gym_id
            AND movement.id = reversal.cash_transaction_id
            AND movement.direction = 'OUT'
            AND movement.source_type = 'PAYMENT'
            AND movement.source_id = reversal.id
            AND movement.reversal_of_id = payment.cash_transaction_id
            AND movement.amount = payment.amount
        ) THEN
          RAISE EXCEPTION 'pt_sale_guard: voided cash payment requires one reversal movement';
        END IF;
      ELSIF reversal.cash_transaction_id IS NOT NULL OR payment.cash_transaction_id IS NOT NULL THEN
        RAISE EXCEPTION 'pt_sale_guard: voided non-cash payment has a movement';
      END IF;
    END IF;
    SELECT count(*) INTO package_count
    FROM public.trainers_member_service_package
    WHERE gym_id = sale.gym_id AND pt_sale_id = sale.id;
    IF package_count > 1 THEN
      RAISE EXCEPTION 'pt_sale_guard: voided sale has more than one package';
    END IF;
    IF package_count = 1 AND NOT EXISTS (
      SELECT 1
      FROM public.trainers_member_service_package AS package
      JOIN public.trainers_service_session_ledger AS grant_row
        ON grant_row.gym_id = package.gym_id
       AND grant_row.member_service_package_id = package.id
       AND grant_row.reason = 'GRANT'
      JOIN public.trainers_service_session_ledger AS refund_row
        ON refund_row.gym_id = package.gym_id
       AND refund_row.member_service_package_id = package.id
       AND refund_row.reason = 'REFUND'
       AND refund_row.reverses_entry_id = grant_row.id
       AND refund_row.session_delta = -package.granted_sessions
      WHERE package.gym_id = sale.gym_id
        AND package.pt_sale_id = sale.id
        AND package.status = 'VOID'
        AND package.source = 'PT_SALE'
        AND (
          SELECT COALESCE(sum(session_delta), 0)
          FROM public.trainers_service_session_ledger AS entry
          WHERE entry.gym_id = package.gym_id
            AND entry.member_service_package_id = package.id
        ) = 0
    ) THEN
      RAISE EXCEPTION 'pt_sale_guard: voided sale package refund is incomplete';
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

CREATE OR REPLACE FUNCTION app_guard_pt_commission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'ACCRUED'
       OR NEW.paid_amount <> 0
       OR NEW.paid_at IS NOT NULL THEN
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
  END IF;
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'pt_commission_guard: commission rows are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
     OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
     OR NEW.gross_fee_snapshot IS DISTINCT FROM OLD.gross_fee_snapshot
     OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
     OR NEW.commission_amount IS DISTINCT FROM OLD.commission_amount
     OR NEW.gym_share IS DISTINCT FROM OLD.gym_share
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'pt_commission_guard: payout may change only payment totals';
  END IF;
  IF NEW.status = 'VOID' THEN
    IF NEW.paid_amount <> 0
       OR NEW.outstanding_amount <> 0
       OR NEW.paid_at IS NOT NULL
       OR EXISTS (
         SELECT 1 FROM trainers_pt_commission_payment
         WHERE gym_id = NEW.gym_id
           AND pt_commission_id = NEW.id
           AND status = 'POSTED'
       ) THEN
      RAISE EXCEPTION 'pt_commission_guard: void commission evidence is incomplete';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID') THEN
    RAISE EXCEPTION 'pt_commission_guard: status is not payable';
  END IF;
  IF NEW.paid_amount IS DISTINCT FROM COALESCE((
    SELECT sum(amount)
    FROM trainers_pt_commission_payment
    WHERE gym_id = NEW.gym_id
      AND pt_commission_id = NEW.id
      AND status = 'POSTED'
  ), 0) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid amount must match posted payouts';
  END IF;
  IF NEW.outstanding_amount IS DISTINCT FROM NEW.commission_amount - NEW.paid_amount THEN
    RAISE EXCEPTION 'pt_commission_guard: outstanding amount is wrong';
  END IF;
  IF NEW.status = 'ACCRUED' AND (NEW.paid_amount <> 0 OR NEW.paid_at IS NOT NULL) THEN
    RAISE EXCEPTION 'pt_commission_guard: accrued commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PARTIALLY_PAID' AND (
    NEW.paid_amount <= 0
    OR NEW.paid_amount >= NEW.commission_amount
    OR NEW.paid_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: partial commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PAID' AND (
    NEW.paid_amount <> NEW.commission_amount
    OR NEW.paid_amount <= 0
    OR NEW.paid_at IS NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid commission evidence is incomplete';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_check_pt_commission_aggregates()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent trainers_pt_commission%ROWTYPE;
  paid numeric;
  gym uuid;
  commission_id uuid;
BEGIN
  IF TG_TABLE_NAME = 'trainers_pt_commission' THEN
    gym := NEW.gym_id;
    commission_id := NEW.id;
  ELSE
    gym := NEW.gym_id;
    commission_id := NEW.pt_commission_id;
  END IF;
  SELECT * INTO parent
  FROM trainers_pt_commission
  WHERE gym_id = gym AND id = commission_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  SELECT COALESCE(sum(amount), 0) INTO paid
  FROM trainers_pt_commission_payment
  WHERE gym_id = gym
    AND pt_commission_id = commission_id
    AND status = 'POSTED';
  IF parent.status = 'VOID' THEN
    IF parent.paid_amount <> 0
       OR parent.outstanding_amount <> 0
       OR parent.paid_at IS NOT NULL
       OR paid <> 0 THEN
      RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
    END IF;
    RETURN NULL;
  END IF;
  IF parent.paid_amount IS DISTINCT FROM paid
     OR parent.outstanding_amount IS DISTINCT FROM (parent.commission_amount - parent.paid_amount)
     OR parent.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID')
     OR (
       parent.status = 'ACCRUED'
       AND (parent.paid_amount <> 0 OR parent.paid_at IS NOT NULL)
     )
     OR (
       parent.status = 'PARTIALLY_PAID'
       AND (
         parent.paid_amount <= 0
         OR parent.paid_amount >= parent.commission_amount
         OR parent.paid_at IS NOT NULL
       )
     )
     OR (
       parent.status = 'PAID'
       AND (
         parent.paid_amount <> parent.commission_amount
         OR parent.paid_amount <= 0
         OR parent.paid_at IS NULL
       )
     )
  THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_guard_member_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  definition trainers_service_package%ROWTYPE;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status = 'VOID' THEN
    RAISE EXCEPTION 'member_package_guard: void is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status = 'VOID' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.service_package_id IS DISTINCT FROM OLD.service_package_id
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.package_type IS DISTINCT FROM OLD.package_type
       OR NEW.package_code IS DISTINCT FROM OLD.package_code
       OR NEW.package_name IS DISTINCT FROM OLD.package_name
       OR NEW.granted_sessions IS DISTINCT FROM OLD.granted_sessions
       OR NEW.amount_snapshot IS DISTINCT FROM OLD.amount_snapshot
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.validity_value IS DISTINCT FROM OLD.validity_value
       OR NEW.validity_unit IS DISTINCT FROM OLD.validity_unit
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
       OR NEW.complimentary IS DISTINCT FROM OLD.complimentary
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.source IS DISTINCT FROM 'PT_SALE'
       OR NEW.pt_sale_id IS NULL THEN
      RAISE EXCEPTION 'member_package_guard: only status can change';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_service_session_ledger
      WHERE gym_id = NEW.gym_id
        AND member_service_package_id = NEW.id
        AND reason = 'REFUND'
        AND session_delta = -NEW.granted_sessions
    ) THEN
      RAISE EXCEPTION 'member_package_guard: void requires a full refund';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status = 'VOID' THEN
    RAISE EXCEPTION 'member_package_guard: void is not published';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.service_package_id IS DISTINCT FROM OLD.service_package_id
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.package_type IS DISTINCT FROM OLD.package_type
       OR NEW.package_code IS DISTINCT FROM OLD.package_code
       OR NEW.package_name IS DISTINCT FROM OLD.package_name
       OR NEW.granted_sessions IS DISTINCT FROM OLD.granted_sessions
       OR NEW.amount_snapshot IS DISTINCT FROM OLD.amount_snapshot
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.validity_value IS DISTINCT FROM OLD.validity_value
       OR NEW.validity_unit IS DISTINCT FROM OLD.validity_unit
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
       OR NEW.complimentary IS DISTINCT FROM OLD.complimentary
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.id IS DISTINCT FROM OLD.id THEN
      RAISE EXCEPTION 'member_package_guard: only status can change';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO definition
  FROM trainers_service_package
  WHERE gym_id = NEW.gym_id AND id = NEW.service_package_id;
  IF NOT FOUND OR NOT definition.is_active THEN
    RAISE EXCEPTION 'member_package_guard: package is not active';
  END IF;
  IF NEW.package_type IS DISTINCT FROM definition.type
     OR NEW.package_code IS DISTINCT FROM definition.code
     OR NEW.package_name IS DISTINCT FROM definition.name
     OR NEW.granted_sessions IS DISTINCT FROM definition.session_count
     OR NEW.amount_snapshot IS DISTINCT FROM definition.price
     OR NEW.currency IS DISTINCT FROM definition.currency
     OR NEW.validity_value IS DISTINCT FROM definition.validity_value
     OR NEW.validity_unit IS DISTINCT FROM definition.validity_unit THEN
    RAISE EXCEPTION 'member_package_guard: snapshot does not match the package';
  END IF;
  IF NEW.valid_until IS DISTINCT FROM app_inclusive_until(
    NEW.valid_from, NEW.validity_value, NEW.validity_unit
  ) THEN
    RAISE EXCEPTION 'member_package_guard: validity window is not inclusive';
  END IF;
  IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
    RAISE EXCEPTION 'member_package_guard: actors must match on create';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM members_member AS member
    WHERE member.gym_id = NEW.gym_id
      AND member.id = NEW.member_id
      AND member.status = 'ACTIVE'
  ) THEN
    RAISE EXCEPTION 'member_package_guard: member is not active';
  END IF;
  IF NEW.source = 'PT_SALE' THEN
    IF NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a sale grant is not complimentary';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_pt_sale AS sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.pt_sale_id
        AND sale.member_id = NEW.member_id
        AND sale.service_package_id = NEW.service_package_id
        AND sale.status = 'POSTED'
        AND sale.package_price_snapshot = NEW.amount_snapshot
        AND sale.service_date = NEW.valid_from
        AND sale.currency = NEW.currency
    ) THEN
      RAISE EXCEPTION 'member_package_guard: sale is not a posted package sale';
    END IF;
    IF NEW.payment_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM members_payment AS payment
      WHERE payment.gym_id = NEW.gym_id
        AND payment.id = NEW.payment_id
        AND payment.pt_sale_id = NEW.pt_sale_id
        AND payment.member_id = NEW.member_id
        AND payment.status = 'POSTED'
    ) THEN
      RAISE EXCEPTION 'member_package_guard: payment does not belong to the posted sale';
    END IF;
  ELSIF NEW.source = 'ADMIN_GRANT' THEN
    IF NEW.pt_sale_id IS NOT NULL OR NEW.payment_id IS NOT NULL THEN
      RAISE EXCEPTION 'member_package_guard: an admin grant has no sale or payment';
    END IF;
    IF NEW.package_type = 'PT' AND NOT NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a PT admin grant must be complimentary';
    END IF;
  ELSE
    RAISE EXCEPTION 'member_package_guard: source is not a grant source';
  END IF;
  RETURN NEW;
END;
$guard$;

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

CREATE OR REPLACE FUNCTION app_check_member_package_balance()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  package_id uuid;
  gym uuid;
  pkg trainers_member_service_package%ROWTYPE;
  total bigint;
  grant_count bigint;
  grant_delta bigint;
  today date;
  expected text;
  gym_tz text;
BEGIN
  IF TG_TABLE_NAME = 'trainers_service_session_ledger' THEN
    package_id := NEW.member_service_package_id;
    gym := NEW.gym_id;
  ELSE
    package_id := NEW.id;
    gym := NEW.gym_id;
  END IF;
  SELECT * INTO pkg
  FROM trainers_member_service_package
  WHERE gym_id = gym AND id = package_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'session_ledger_guard: package is missing';
  END IF;
  SELECT COALESCE(sum(session_delta), 0),
         count(*) FILTER (WHERE reason = 'GRANT'),
         COALESCE(sum(session_delta) FILTER (WHERE reason = 'GRANT'), 0)
    INTO total, grant_count, grant_delta
  FROM trainers_service_session_ledger
  WHERE gym_id = gym AND member_service_package_id = package_id;
  IF grant_count <> 1 OR grant_delta <> pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: a package needs exactly one grant';
  END IF;
  IF total < 0 OR total > pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: balance is outside the granted sessions';
  END IF;
  IF pkg.status = 'VOID' THEN
    IF total <> 0 THEN
      RAISE EXCEPTION 'session_ledger_guard: balance is outside the granted sessions';
    END IF;
    expected := 'VOID';
  ELSE
    SELECT timezone INTO gym_tz FROM tenants_gym WHERE id = gym;
    today := (CURRENT_TIMESTAMP AT TIME ZONE gym_tz)::date;
    IF total = 0 THEN
      expected := 'EXHAUSTED';
    ELSIF today > pkg.valid_until THEN
      expected := 'EXPIRED';
    ELSE
      expected := 'ACTIVE';
    END IF;
  END IF;
  IF pkg.status IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'session_ledger_guard: package status does not match the ledger';
  END IF;
  RETURN NULL;
END;
$guard$;
"""

REVERSE_SQL = r"""
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

CREATE OR REPLACE FUNCTION app_pt_sale_commit()
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

CREATE OR REPLACE FUNCTION app_guard_pt_commission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'ACCRUED'
       OR NEW.paid_amount <> 0
       OR NEW.paid_at IS NOT NULL THEN
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
  END IF;
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'pt_commission_guard: commission rows are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
     OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
     OR NEW.gross_fee_snapshot IS DISTINCT FROM OLD.gross_fee_snapshot
     OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
     OR NEW.commission_amount IS DISTINCT FROM OLD.commission_amount
     OR NEW.gym_share IS DISTINCT FROM OLD.gym_share
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'pt_commission_guard: payout may change only payment totals';
  END IF;
  IF NEW.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID') THEN
    RAISE EXCEPTION 'pt_commission_guard: status is not payable';
  END IF;
  IF NEW.paid_amount IS DISTINCT FROM COALESCE((
    SELECT sum(amount)
    FROM trainers_pt_commission_payment
    WHERE gym_id = NEW.gym_id
      AND pt_commission_id = NEW.id
      AND status = 'POSTED'
  ), 0) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid amount must match posted payouts';
  END IF;
  IF NEW.outstanding_amount IS DISTINCT FROM NEW.commission_amount - NEW.paid_amount THEN
    RAISE EXCEPTION 'pt_commission_guard: outstanding amount is wrong';
  END IF;
  IF NEW.status = 'ACCRUED' AND (NEW.paid_amount <> 0 OR NEW.paid_at IS NOT NULL) THEN
    RAISE EXCEPTION 'pt_commission_guard: accrued commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PARTIALLY_PAID' AND (
    NEW.paid_amount <= 0
    OR NEW.paid_amount >= NEW.commission_amount
    OR NEW.paid_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: partial commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PAID' AND (
    NEW.paid_amount <> NEW.commission_amount
    OR NEW.paid_amount <= 0
    OR NEW.paid_at IS NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid commission evidence is incomplete';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_check_pt_commission_aggregates()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent trainers_pt_commission%ROWTYPE;
  paid numeric;
  gym uuid;
  commission_id uuid;
BEGIN
  IF TG_TABLE_NAME = 'trainers_pt_commission' THEN
    gym := NEW.gym_id;
    commission_id := NEW.id;
  ELSE
    gym := NEW.gym_id;
    commission_id := NEW.pt_commission_id;
  END IF;
  SELECT * INTO parent
  FROM trainers_pt_commission
  WHERE gym_id = gym AND id = commission_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  SELECT COALESCE(sum(amount), 0) INTO paid
  FROM trainers_pt_commission_payment
  WHERE gym_id = gym
    AND pt_commission_id = commission_id
    AND status = 'POSTED';
  IF parent.paid_amount IS DISTINCT FROM paid
     OR parent.outstanding_amount IS DISTINCT FROM (parent.commission_amount - parent.paid_amount)
     OR parent.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID')
     OR (
       parent.status = 'ACCRUED'
       AND (parent.paid_amount <> 0 OR parent.paid_at IS NOT NULL)
     )
     OR (
       parent.status = 'PARTIALLY_PAID'
       AND (
         parent.paid_amount <= 0
         OR parent.paid_amount >= parent.commission_amount
         OR parent.paid_at IS NOT NULL
       )
     )
     OR (
       parent.status = 'PAID'
       AND (
         parent.paid_amount <> parent.commission_amount
         OR parent.paid_amount <= 0
         OR parent.paid_at IS NULL
       )
     )
  THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_guard_member_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  definition trainers_service_package%ROWTYPE;
BEGIN
  IF NEW.status = 'VOID' OR (TG_OP = 'UPDATE' AND OLD.status = 'VOID') THEN
    RAISE EXCEPTION 'member_package_guard: void is not published';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.service_package_id IS DISTINCT FROM OLD.service_package_id
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.package_type IS DISTINCT FROM OLD.package_type
       OR NEW.package_code IS DISTINCT FROM OLD.package_code
       OR NEW.package_name IS DISTINCT FROM OLD.package_name
       OR NEW.granted_sessions IS DISTINCT FROM OLD.granted_sessions
       OR NEW.amount_snapshot IS DISTINCT FROM OLD.amount_snapshot
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.validity_value IS DISTINCT FROM OLD.validity_value
       OR NEW.validity_unit IS DISTINCT FROM OLD.validity_unit
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
       OR NEW.complimentary IS DISTINCT FROM OLD.complimentary
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.id IS DISTINCT FROM OLD.id THEN
      RAISE EXCEPTION 'member_package_guard: only status can change';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO definition
  FROM trainers_service_package
  WHERE gym_id = NEW.gym_id AND id = NEW.service_package_id;
  IF NOT FOUND OR NOT definition.is_active THEN
    RAISE EXCEPTION 'member_package_guard: package is not active';
  END IF;
  IF NEW.package_type IS DISTINCT FROM definition.type
     OR NEW.package_code IS DISTINCT FROM definition.code
     OR NEW.package_name IS DISTINCT FROM definition.name
     OR NEW.granted_sessions IS DISTINCT FROM definition.session_count
     OR NEW.amount_snapshot IS DISTINCT FROM definition.price
     OR NEW.currency IS DISTINCT FROM definition.currency
     OR NEW.validity_value IS DISTINCT FROM definition.validity_value
     OR NEW.validity_unit IS DISTINCT FROM definition.validity_unit THEN
    RAISE EXCEPTION 'member_package_guard: snapshot does not match the package';
  END IF;
  IF NEW.valid_until IS DISTINCT FROM app_inclusive_until(
    NEW.valid_from, NEW.validity_value, NEW.validity_unit
  ) THEN
    RAISE EXCEPTION 'member_package_guard: validity window is not inclusive';
  END IF;
  IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
    RAISE EXCEPTION 'member_package_guard: actors must match on create';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM members_member AS member
    WHERE member.gym_id = NEW.gym_id
      AND member.id = NEW.member_id
      AND member.status = 'ACTIVE'
  ) THEN
    RAISE EXCEPTION 'member_package_guard: member is not active';
  END IF;
  IF NEW.source = 'PT_SALE' THEN
    IF NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a sale grant is not complimentary';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_pt_sale AS sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.pt_sale_id
        AND sale.member_id = NEW.member_id
        AND sale.service_package_id = NEW.service_package_id
        AND sale.status = 'POSTED'
        AND sale.package_price_snapshot = NEW.amount_snapshot
        AND sale.service_date = NEW.valid_from
        AND sale.currency = NEW.currency
    ) THEN
      RAISE EXCEPTION 'member_package_guard: sale is not a posted package sale';
    END IF;
    IF NEW.payment_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM members_payment AS payment
      WHERE payment.gym_id = NEW.gym_id
        AND payment.id = NEW.payment_id
        AND payment.pt_sale_id = NEW.pt_sale_id
        AND payment.member_id = NEW.member_id
        AND payment.status = 'POSTED'
    ) THEN
      RAISE EXCEPTION 'member_package_guard: payment does not belong to the posted sale';
    END IF;
  ELSIF NEW.source = 'ADMIN_GRANT' THEN
    IF NEW.pt_sale_id IS NOT NULL OR NEW.payment_id IS NOT NULL THEN
      RAISE EXCEPTION 'member_package_guard: an admin grant has no sale or payment';
    END IF;
    IF NEW.package_type = 'PT' AND NOT NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a PT admin grant must be complimentary';
    END IF;
  ELSE
    RAISE EXCEPTION 'member_package_guard: source is not a grant source';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_guard_session_ledger()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  granted integer;
  original_delta integer;
  original_reason text;
  original_package uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'session_ledger_guard: ledger entries are append-only';
  END IF;
  IF NEW.reason IN ('ATTENDED', 'POLICY_NO_SHOW', 'REFUND', 'EXPIRY') THEN
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
       OR NEW.session_delta IS DISTINCT FROM (-original_delta) THEN
      RAISE EXCEPTION 'session_ledger_guard: correction target is not eligible';
    END IF;
  ELSE
    RAISE EXCEPTION 'session_ledger_guard: reason is not a ledger reason';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_check_member_package_balance()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  package_id uuid;
  gym uuid;
  pkg trainers_member_service_package%ROWTYPE;
  total bigint;
  grant_count bigint;
  grant_delta bigint;
  today date;
  expected text;
  gym_tz text;
BEGIN
  IF TG_TABLE_NAME = 'trainers_service_session_ledger' THEN
    package_id := NEW.member_service_package_id;
    gym := NEW.gym_id;
  ELSE
    package_id := NEW.id;
    gym := NEW.gym_id;
  END IF;
  SELECT * INTO pkg
  FROM trainers_member_service_package
  WHERE gym_id = gym AND id = package_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'session_ledger_guard: package is missing';
  END IF;
  SELECT COALESCE(sum(session_delta), 0),
         count(*) FILTER (WHERE reason = 'GRANT'),
         COALESCE(sum(session_delta) FILTER (WHERE reason = 'GRANT'), 0)
    INTO total, grant_count, grant_delta
  FROM trainers_service_session_ledger
  WHERE gym_id = gym AND member_service_package_id = package_id;
  IF grant_count <> 1 OR grant_delta <> pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: a package needs exactly one grant';
  END IF;
  IF total < 0 OR total > pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: balance is outside the granted sessions';
  END IF;
  SELECT timezone INTO gym_tz FROM tenants_gym WHERE id = gym;
  today := (CURRENT_TIMESTAMP AT TIME ZONE gym_tz)::date;
  IF total = 0 THEN
    expected := 'EXHAUSTED';
  ELSIF today > pkg.valid_until THEN
    expected := 'EXPIRED';
  ELSE
    expected := 'ACTIVE';
  END IF;
  IF pkg.status IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'session_ledger_guard: package status does not match the ledger';
  END IF;
  RETURN NULL;
END;
$guard$;

REVOKE ALL ON FUNCTION app_guard_pt_sale() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_pt_sale_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_commission() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_check_pt_commission_aggregates() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_member_package() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_session_ledger() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_check_member_package_balance() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_pt_sale() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_pt_sale_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_check_pt_commission_aggregates() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_member_package() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_session_ledger() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_check_member_package_balance() TO gym_runtime, gym_migration;

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
ALTER TABLE trainers_pt_commission DROP CONSTRAINT IF EXISTS pt_comm_amounts_ck;
ALTER TABLE trainers_pt_commission
  ADD CONSTRAINT pt_comm_amounts_ck
  CHECK (
    commission_amount = round(gross_fee_snapshot * commission_percent_snapshot / 100, 2)
    AND gym_share = gross_fee_snapshot - commission_amount
    AND outstanding_amount = commission_amount - paid_amount
    AND paid_amount <= commission_amount
    AND currency ~ '^[A-Z]{3}$'
  );
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_voided_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0014_pt_sale_void_fields"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
