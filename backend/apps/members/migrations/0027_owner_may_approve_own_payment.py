"""Let the protected owner post a payment they recorded.

A one-person gym has no second approver. Managers still cannot post a payment
they created or submitted. The database guard matches that exception.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  original_status text;
  original_type text;
  original_amount numeric;
  original_currency text;
  original_member uuid;
  original_membership uuid;
  original_purpose text;
  original_method text;
  original_legacy boolean;
  actor_is_super boolean;
  owner_closes_own boolean;
BEGIN
  IF NEW.currency !~ '^[A-Z]{3}$' OR NEW.amount <= 0 THEN
    RAISE EXCEPTION 'payment_guard: amount and currency are invalid';
  END IF;
  IF NEW.purpose NOT IN ('MEMBERSHIP', 'PT', 'PRODUCT', 'CLASS', 'OTHER')
     OR NEW.method NOT IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER')
     OR NEW.entry_type NOT IN ('CHARGE', 'REFUND', 'REVERSAL')
     OR NEW.status NOT IN (
       'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
     ) THEN
    RAISE EXCEPTION 'payment_guard: payment classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    IF NOT (
      NEW.status = 'POSTED'
      AND NEW.entry_type IN ('REFUND', 'REVERSAL')
      AND NEW.original_payment_id IS NOT NULL
    ) THEN
      SELECT rolsuper INTO actor_is_super
      FROM pg_catalog.pg_roles
      WHERE rolname = current_user;
      IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'payment_guard: runtime inserts must start as drafts';
      END IF;
    END IF;
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'payment_guard: currency must match the gym';
  END IF;
  IF NEW.purpose = 'MEMBERSHIP' THEN
    IF NEW.member_id IS NULL OR NEW.membership_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM public.members_membership
      WHERE gym_id = NEW.gym_id
        AND id = NEW.membership_id
        AND member_id = NEW.member_id
    ) THEN
      RAISE EXCEPTION 'payment_guard: membership purpose requires the member sale';
    END IF;
  ELSIF NEW.purpose = 'OTHER' AND NEW.membership_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: other purpose has no membership';
  END IF;
  IF NEW.legacy_unlinked_cash AND TG_OP = 'INSERT' THEN
    original_legacy := false;
    IF NEW.entry_type = 'REVERSAL' AND NEW.original_payment_id IS NOT NULL THEN
      SELECT legacy_unlinked_cash INTO original_legacy
      FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    END IF;
    IF original_legacy IS NOT TRUE THEN
      SELECT rolsuper INTO actor_is_super
      FROM pg_catalog.pg_roles
      WHERE rolname = current_user;
      IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'payment_guard: legacy cash marker is migration-only';
      END IF;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.entry_type IS DISTINCT FROM OLD.entry_type
    OR NEW.original_payment_id IS DISTINCT FROM OLD.original_payment_id
    OR NEW.legacy_unlinked_cash IS DISTINCT FROM OLD.legacy_unlinked_cash
  ) THEN
    RAISE EXCEPTION 'payment_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.reversal_payment_id IS DISTINCT FROM OLD.reversal_payment_id THEN
    IF NOT (
      OLD.reversal_payment_id IS NULL
      AND NEW.reversal_payment_id IS NOT NULL
      AND OLD.status = 'POSTED'
      AND NEW.status = 'VOID'
    ) THEN
      RAISE EXCEPTION 'payment_guard: reversal linkage is immutable';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'payment_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'payment_guard: terminal payment is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'payment_guard: posted payment is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
    OR NEW.purpose IS DISTINCT FROM OLD.purpose
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.method IS DISTINCT FROM OLD.method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.received_at IS DISTINCT FROM OLD.received_at
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
    OR NEW.cash_session_id IS DISTINCT FROM OLD.cash_session_id
    OR NEW.cash_transaction_id IS DISTINCT FROM OLD.cash_transaction_id
  ) THEN
    RAISE EXCEPTION 'payment_guard: void cannot change posted facts';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
    OR NEW.purpose IS DISTINCT FROM OLD.purpose
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.method IS DISTINCT FROM OLD.method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.received_at IS DISTINCT FROM OLD.received_at
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'payment_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'payment_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.entry_type = 'CHARGE' AND NEW.original_payment_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: a charge has no original';
  END IF;
  IF NEW.entry_type = 'CHARGE'
     AND NEW.reversal_payment_id IS NOT NULL
     AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'payment_guard: reversal linkage belongs on a voided charge';
  END IF;
  IF NEW.reversal_payment_id IS NOT NULL AND NEW.reversal_payment_id = NEW.id THEN
    RAISE EXCEPTION 'payment_guard: payment cannot reference itself';
  END IF;
  IF NEW.entry_type = 'REFUND' THEN
    SELECT status, entry_type INTO original_status, original_type
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF original_type IS DISTINCT FROM 'CHARGE'
       OR (NEW.status = 'POSTED' AND original_status IS DISTINCT FROM 'POSTED')
       OR (
         NEW.status IS DISTINCT FROM 'POSTED'
         AND original_status NOT IN ('POSTED', 'VOID')
       ) THEN
      RAISE EXCEPTION 'payment_guard: original must be a posted charge';
    END IF;
  END IF;
  IF NEW.entry_type = 'REVERSAL' THEN
    SELECT status, entry_type, amount, currency, member_id, membership_id,
           purpose, method, legacy_unlinked_cash
      INTO original_status, original_type, original_amount, original_currency,
           original_member, original_membership, original_purpose, original_method,
           original_legacy
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF original_type IS DISTINCT FROM 'CHARGE'
       OR original_status NOT IN ('POSTED', 'VOID') THEN
      RAISE EXCEPTION 'payment_guard: reversal original must be a charge';
    END IF;
    IF NEW.status = 'POSTED' AND (
       original_amount IS DISTINCT FROM NEW.amount
       OR original_currency IS DISTINCT FROM NEW.currency
       OR original_member IS DISTINCT FROM NEW.member_id
       OR original_membership IS DISTINCT FROM NEW.membership_id
       OR original_purpose IS DISTINCT FROM NEW.purpose
       OR original_method IS DISTINCT FROM NEW.method
    ) THEN
      RAISE EXCEPTION 'payment_guard: reversal does not match the original';
    END IF;
  END IF;
  owner_closes_own := false;
  IF NEW.approved_by_membership_id IS NOT NULL THEN
    SELECT EXISTS (
      SELECT 1
      FROM public.tenants_gym_membership_role assignment
      JOIN public.tenants_role role
        ON role.id = assignment.role_id
       AND role.gym_id = assignment.gym_id
      JOIN public.tenants_system_role_template template
        ON template.id = role.source_template_id
      WHERE assignment.gym_id = NEW.gym_id
        AND assignment.gym_membership_id = NEW.approved_by_membership_id
        AND role.is_active
        AND role.key = 'OWNER'
        AND template.key = 'OWNER'
    ) INTO owner_closes_own;
  END IF;
  IF NEW.status = 'POSTED' AND NEW.entry_type = 'CHARGE' AND NOT NEW.legacy_unlinked_cash AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR (
      NOT owner_closes_own
      AND (
        NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
        OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
      )
    )
  ) THEN
    RAISE EXCEPTION 'payment_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL OR NEW.voided_by_membership_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.cancellation_reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL OR NEW.voided_by_membership_id IS NULL
    OR btrim(NEW.void_reason) = ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: void evidence does not match';
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.status = 'VOID'
     AND NEW.entry_type = 'CHARGE'
     AND NEW.reversal_payment_id IS NULL THEN
    RAISE EXCEPTION 'payment_guard: void requires a reversal';
  END IF;
  RETURN NEW;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  original_status text;
  original_type text;
  original_amount numeric;
  original_currency text;
  original_member uuid;
  original_membership uuid;
  original_purpose text;
  original_method text;
  original_legacy boolean;
  actor_is_super boolean;
BEGIN
  IF NEW.currency !~ '^[A-Z]{3}$' OR NEW.amount <= 0 THEN
    RAISE EXCEPTION 'payment_guard: amount and currency are invalid';
  END IF;
  IF NEW.purpose NOT IN ('MEMBERSHIP', 'PT', 'PRODUCT', 'CLASS', 'OTHER')
     OR NEW.method NOT IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER')
     OR NEW.entry_type NOT IN ('CHARGE', 'REFUND', 'REVERSAL')
     OR NEW.status NOT IN (
       'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
     ) THEN
    RAISE EXCEPTION 'payment_guard: payment classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    IF NOT (
      NEW.status = 'POSTED'
      AND NEW.entry_type IN ('REFUND', 'REVERSAL')
      AND NEW.original_payment_id IS NOT NULL
    ) THEN
      SELECT rolsuper INTO actor_is_super
      FROM pg_catalog.pg_roles
      WHERE rolname = current_user;
      IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'payment_guard: runtime inserts must start as drafts';
      END IF;
    END IF;
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'payment_guard: currency must match the gym';
  END IF;
  IF NEW.purpose = 'MEMBERSHIP' THEN
    IF NEW.member_id IS NULL OR NEW.membership_id IS NULL OR NOT EXISTS (
      SELECT 1 FROM public.members_membership
      WHERE gym_id = NEW.gym_id
        AND id = NEW.membership_id
        AND member_id = NEW.member_id
    ) THEN
      RAISE EXCEPTION 'payment_guard: membership purpose requires the member sale';
    END IF;
  ELSIF NEW.purpose = 'OTHER' AND NEW.membership_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: other purpose has no membership';
  END IF;
  IF NEW.legacy_unlinked_cash AND TG_OP = 'INSERT' THEN
    original_legacy := false;
    IF NEW.entry_type = 'REVERSAL' AND NEW.original_payment_id IS NOT NULL THEN
      SELECT legacy_unlinked_cash INTO original_legacy
      FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    END IF;
    IF original_legacy IS NOT TRUE THEN
      SELECT rolsuper INTO actor_is_super
      FROM pg_catalog.pg_roles
      WHERE rolname = current_user;
      IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
        RAISE EXCEPTION 'payment_guard: legacy cash marker is migration-only';
      END IF;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.entry_type IS DISTINCT FROM OLD.entry_type
    OR NEW.original_payment_id IS DISTINCT FROM OLD.original_payment_id
    OR NEW.legacy_unlinked_cash IS DISTINCT FROM OLD.legacy_unlinked_cash
  ) THEN
    RAISE EXCEPTION 'payment_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.reversal_payment_id IS DISTINCT FROM OLD.reversal_payment_id THEN
    IF NOT (
      OLD.reversal_payment_id IS NULL
      AND NEW.reversal_payment_id IS NOT NULL
      AND OLD.status = 'POSTED'
      AND NEW.status = 'VOID'
    ) THEN
      RAISE EXCEPTION 'payment_guard: reversal linkage is immutable';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'payment_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'payment_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'payment_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'payment_guard: terminal payment is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'payment_guard: posted payment is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
    OR NEW.purpose IS DISTINCT FROM OLD.purpose
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.method IS DISTINCT FROM OLD.method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.received_at IS DISTINCT FROM OLD.received_at
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
    OR NEW.cash_session_id IS DISTINCT FROM OLD.cash_session_id
    OR NEW.cash_transaction_id IS DISTINCT FROM OLD.cash_transaction_id
  ) THEN
    RAISE EXCEPTION 'payment_guard: void cannot change posted facts';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.membership_id IS DISTINCT FROM OLD.membership_id
    OR NEW.purpose IS DISTINCT FROM OLD.purpose
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.method IS DISTINCT FROM OLD.method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.received_at IS DISTINCT FROM OLD.received_at
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'payment_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'payment_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.entry_type = 'CHARGE' AND NEW.original_payment_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: a charge has no original';
  END IF;
  IF NEW.entry_type = 'CHARGE'
     AND NEW.reversal_payment_id IS NOT NULL
     AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'payment_guard: reversal linkage belongs on a voided charge';
  END IF;
  IF NEW.reversal_payment_id IS NOT NULL AND NEW.reversal_payment_id = NEW.id THEN
    RAISE EXCEPTION 'payment_guard: payment cannot reference itself';
  END IF;
  IF NEW.entry_type = 'REFUND' THEN
    SELECT status, entry_type INTO original_status, original_type
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF original_type IS DISTINCT FROM 'CHARGE'
       OR (NEW.status = 'POSTED' AND original_status IS DISTINCT FROM 'POSTED')
       OR (
         NEW.status IS DISTINCT FROM 'POSTED'
         AND original_status NOT IN ('POSTED', 'VOID')
       ) THEN
      RAISE EXCEPTION 'payment_guard: original must be a posted charge';
    END IF;
  END IF;
  IF NEW.entry_type = 'REVERSAL' THEN
    SELECT status, entry_type, amount, currency, member_id, membership_id,
           purpose, method, legacy_unlinked_cash
      INTO original_status, original_type, original_amount, original_currency,
           original_member, original_membership, original_purpose, original_method,
           original_legacy
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF original_type IS DISTINCT FROM 'CHARGE'
       OR original_status NOT IN ('POSTED', 'VOID') THEN
      RAISE EXCEPTION 'payment_guard: reversal original must be a charge';
    END IF;
    IF NEW.status = 'POSTED' AND (
       original_amount IS DISTINCT FROM NEW.amount
       OR original_currency IS DISTINCT FROM NEW.currency
       OR original_member IS DISTINCT FROM NEW.member_id
       OR original_membership IS DISTINCT FROM NEW.membership_id
       OR original_purpose IS DISTINCT FROM NEW.purpose
       OR original_method IS DISTINCT FROM NEW.method
    ) THEN
      RAISE EXCEPTION 'payment_guard: reversal does not match the original';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND NEW.entry_type = 'CHARGE' AND NOT NEW.legacy_unlinked_cash AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
  ) THEN
    RAISE EXCEPTION 'payment_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL OR NEW.voided_by_membership_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.cancellation_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.cancellation_reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'payment_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL OR NEW.voided_by_membership_id IS NULL
    OR btrim(NEW.void_reason) = ''
  ) THEN
    RAISE EXCEPTION 'payment_guard: void evidence does not match';
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.status = 'VOID'
     AND NEW.entry_type = 'CHARGE'
     AND NEW.reversal_payment_id IS NULL THEN
    RAISE EXCEPTION 'payment_guard: void requires a reversal';
  END IF;
  RETURN NEW;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0026_member_device_rls_and_guards"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
