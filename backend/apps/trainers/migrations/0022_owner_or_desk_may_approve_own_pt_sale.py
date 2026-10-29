"""Let the owner, or a combined manager and receptionist, approve their own PT sale.

A manager who does not also hold the protected receptionist role still cannot
approve or reject a sale they created or submitted. A receptionist alone cannot.
"""

from django.db import migrations

FORWARD_SQL = r"""

CREATE OR REPLACE FUNCTION public.app_membership_may_self_approve(
  p_gym_id uuid,
  p_membership_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog, public
AS $fn$
  SELECT EXISTS (
    SELECT 1
    FROM public.tenants_gym_membership_role assignment
    JOIN public.tenants_role role
      ON role.id = assignment.role_id
     AND role.gym_id = assignment.gym_id
    JOIN public.tenants_system_role_template template
      ON template.id = role.source_template_id
    WHERE assignment.gym_id = p_gym_id
      AND assignment.gym_membership_id = p_membership_id
      AND role.is_active
      AND role.key = 'OWNER'
      AND template.key = 'OWNER'
  )
  OR (
    EXISTS (
      SELECT 1
      FROM public.tenants_gym_membership_role assignment
      JOIN public.tenants_role role
        ON role.id = assignment.role_id
       AND role.gym_id = assignment.gym_id
      JOIN public.tenants_system_role_template template
        ON template.id = role.source_template_id
      WHERE assignment.gym_id = p_gym_id
        AND assignment.gym_membership_id = p_membership_id
        AND role.is_active
        AND role.key = 'MANAGER'
        AND template.key = 'MANAGER'
    )
    AND EXISTS (
      SELECT 1
      FROM public.tenants_gym_membership_role assignment
      JOIN public.tenants_role role
        ON role.id = assignment.role_id
       AND role.gym_id = assignment.gym_id
      JOIN public.tenants_system_role_template template
        ON template.id = role.source_template_id
      WHERE assignment.gym_id = p_gym_id
        AND assignment.gym_membership_id = p_membership_id
        AND role.is_active
        AND role.key = 'RECEPTIONIST'
        AND template.key = 'RECEPTIONIST'
    )
  );
$fn$;

REVOKE ALL ON FUNCTION public.app_membership_may_self_approve(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.app_membership_may_self_approve(uuid, uuid)
  TO gym_runtime, gym_migration;

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
       OR (
         (
           NEW.approved_by_membership_id = NEW.created_by_membership_id
           OR NEW.approved_by_membership_id = NEW.submitted_by_membership_id
         )
         AND NOT public.app_membership_may_self_approve(
           NEW.gym_id, NEW.approved_by_membership_id
         )
       ) THEN
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
       OR (
         (
           NEW.rejected_by_membership_id = NEW.created_by_membership_id
           OR NEW.rejected_by_membership_id = NEW.submitted_by_membership_id
         )
         AND NOT public.app_membership_may_self_approve(
           NEW.gym_id, NEW.rejected_by_membership_id
         )
       ) THEN
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

"""

REVERSE_SQL = r"""
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

DROP FUNCTION IF EXISTS public.app_membership_may_self_approve(uuid, uuid);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0021_pt_commission_payout_window"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
