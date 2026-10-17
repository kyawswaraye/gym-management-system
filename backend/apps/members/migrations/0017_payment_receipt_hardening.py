# Strict payment and receipt constraints after legacy rows have been reconciled.

from django.db import migrations, models

FORWARD_SQL = r"""
ALTER TABLE members_receipt ALTER COLUMN sequence_key SET NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN sequence_value SET NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN sequence_year SET NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN gym_code SET NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN gym_name SET NOT NULL;

ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_currency_ck;
ALTER TABLE members_payment ADD CONSTRAINT payment_currency_ck
  CHECK (currency ~ '^[A-Z]{3}$');
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_purpose_ck;
ALTER TABLE members_payment ADD CONSTRAINT payment_purpose_ck
  CHECK (purpose IN ('MEMBERSHIP', 'PT', 'PRODUCT', 'CLASS', 'OTHER'));
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_method_ck;
ALTER TABLE members_payment ADD CONSTRAINT payment_method_ck
  CHECK (method IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER'));
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_entry_ck;
ALTER TABLE members_payment ADD CONSTRAINT payment_entry_ck
  CHECK (
    (
      entry_type = 'CHARGE'
      AND original_payment_id IS NULL
    )
    OR (
      entry_type IN ('REFUND', 'REVERSAL')
      AND original_payment_id IS NOT NULL
      AND original_payment_id <> id
    )
  );

ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_original_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_original_same_gym_fk
  FOREIGN KEY (gym_id, original_payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_reversal_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_reversal_same_gym_fk
  FOREIGN KEY (gym_id, reversal_payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cash_session_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_cash_session_same_gym_fk
  FOREIGN KEY (gym_id, cash_session_id)
  REFERENCES cashbook_cash_session (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cash_txn_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_cash_txn_same_gym_fk
  FOREIGN KEY (gym_id, cash_transaction_id)
  REFERENCES cashbook_cash_transaction (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_created_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_updated_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_submitted_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_submitted_by_same_gym_fk
  FOREIGN KEY (gym_id, submitted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_approved_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_approved_by_same_gym_fk
  FOREIGN KEY (gym_id, approved_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_rejected_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_rejected_by_same_gym_fk
  FOREIGN KEY (gym_id, rejected_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cancelled_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_posted_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_posted_by_same_gym_fk
  FOREIGN KEY (gym_id, posted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_voided_by_same_gym_fk;
ALTER TABLE members_payment ADD CONSTRAINT payment_voided_by_same_gym_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_currency_ck;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_currency_ck
  CHECK (currency ~ '^[A-Z]{3}$');
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_amount_positive_ck;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_amount_positive_ck
  CHECK (amount > 0);
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_replacement_same_gym_fk;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_replacement_same_gym_fk
  FOREIGN KEY (gym_id, replacement_receipt_id)
  REFERENCES members_receipt (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_file_same_gym_fk;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_file_same_gym_fk
  FOREIGN KEY (gym_id, rendered_file_asset_id)
  REFERENCES files_file_asset (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_created_by_same_gym_fk;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_voided_by_same_gym_fk;
ALTER TABLE members_receipt ADD CONSTRAINT receipt_voided_by_same_gym_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX IF NOT EXISTS payment_one_reversal_per_original_idx
  ON members_payment (gym_id, original_payment_id)
  WHERE entry_type = 'REVERSAL';

CREATE FUNCTION app_has_protected_reception(target_gym uuid, target_membership uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF target_gym IS NULL OR target_gym IS DISTINCT FROM public.app_current_gym_id() THEN
    RAISE EXCEPTION 'payment_guard: reception check is outside the active gym'
      USING ERRCODE = '42501';
  END IF;
  RETURN EXISTS (
    SELECT 1
    FROM public.tenants_gym_membership_role link
    JOIN public.tenants_role role
      ON role.gym_id = link.gym_id
     AND role.id = link.role_id
    JOIN public.tenants_system_role_template template
      ON template.id = role.source_template_id
    WHERE link.gym_id = public.app_current_gym_id()
      AND link.gym_membership_id = target_membership
      AND role.is_active
      AND role.key = 'RECEPTIONIST'
      AND template.key = 'RECEPTIONIST'
  );
END;
$$;

CREATE FUNCTION app_guard_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  original_status text;
  original_type text;
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
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'payment_guard: runtime inserts must start as drafts';
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
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'payment_guard: legacy cash marker is migration-only';
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
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE
       AND current_user IS DISTINCT FROM 'gym_migration' THEN
      RAISE EXCEPTION 'payment_guard: void is reserved to the migration role';
    END IF;
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
    SELECT status, entry_type INTO original_status, original_type
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF original_type IS DISTINCT FROM 'CHARGE'
       OR original_status NOT IN ('POSTED', 'VOID') THEN
      RAISE EXCEPTION 'payment_guard: reversal original must be a charge';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND NOT NEW.legacy_unlinked_cash AND (
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
  RETURN NEW;
END;
$guard$;

CREATE TRIGGER payment_guard_trg
  BEFORE INSERT OR UPDATE ON members_payment
  FOR EACH ROW EXECUTE FUNCTION app_guard_payment();

CREATE FUNCTION app_guard_payment_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'payment_guard: payments cannot be deleted';
END;
$guard$;

CREATE TRIGGER payment_delete_trg
  BEFORE DELETE ON members_payment
  FOR EACH ROW EXECUTE FUNCTION app_guard_payment_delete();

CREATE FUNCTION app_guard_receipt()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  pay_amount numeric;
  pay_currency text;
  pay_method text;
  pay_date date;
  pay_status text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'receipt_guard: receipts cannot be deleted';
  END IF;
  SELECT amount, currency, method, business_date, status
    INTO pay_amount, pay_currency, pay_method, pay_date, pay_status
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id AND id = NEW.payment_id;
  IF pay_status IS DISTINCT FROM 'POSTED' THEN
    RAISE EXCEPTION 'receipt_guard: receipt requires a posted payment';
  END IF;
  IF NEW.amount IS DISTINCT FROM pay_amount
     OR NEW.currency IS DISTINCT FROM pay_currency
     OR NEW.method IS DISTINCT FROM pay_method
     OR NEW.business_date IS DISTINCT FROM pay_date THEN
    RAISE EXCEPTION 'receipt_guard: receipt must match the payment';
  END IF;
  IF NEW.status = 'ISSUED' AND (
    NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
    OR btrim(COALESCE(NEW.void_reason, '')) <> ''
    OR NEW.replacement_receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'receipt_guard: issued receipt has void or replacement evidence';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL
    OR NEW.voided_by_membership_id IS NULL
    OR btrim(COALESCE(NEW.void_reason, '')) = ''
    OR NEW.replacement_receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'receipt_guard: void evidence does not match';
  END IF;
  IF NEW.status = 'REPLACED' AND NEW.replacement_receipt_id IS NULL THEN
    RAISE EXCEPTION 'receipt_guard: replacement evidence does not match';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
    OR NEW.receipt_number IS DISTINCT FROM OLD.receipt_number
    OR NEW.sequence_key IS DISTINCT FROM OLD.sequence_key
    OR NEW.sequence_value IS DISTINCT FROM OLD.sequence_value
    OR NEW.sequence_year IS DISTINCT FROM OLD.sequence_year
    OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
    OR NEW.gym_code IS DISTINCT FROM OLD.gym_code
    OR NEW.gym_name IS DISTINCT FROM OLD.gym_name
    OR NEW.gym_address IS DISTINCT FROM OLD.gym_address
    OR NEW.member_number IS DISTINCT FROM OLD.member_number
    OR NEW.member_name IS DISTINCT FROM OLD.member_name
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.method IS DISTINCT FROM OLD.method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.issuer_name IS DISTINCT FROM OLD.issuer_name
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  ) THEN
    RAISE EXCEPTION 'receipt_guard: receipt snapshots are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status AND NOT (
    (OLD.status = 'ISSUED' AND NEW.status = 'VOID')
    OR (OLD.status = 'ISSUED' AND NEW.status = 'REPLACED')
  ) THEN
    RAISE EXCEPTION 'receipt_guard: status transition is not allowed';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE TRIGGER receipt_guard_trg
  BEFORE INSERT OR UPDATE ON members_receipt
  FOR EACH ROW EXECUTE FUNCTION app_guard_receipt();

CREATE TRIGGER receipt_delete_trg
  BEFORE DELETE ON members_receipt
  FOR EACH ROW EXECUTE FUNCTION app_guard_receipt();

CREATE FUNCTION app_payment_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  issued_count integer;
BEGIN
  IF NEW.status = 'POSTED' THEN
    SELECT count(*) INTO issued_count
    FROM public.members_receipt
    WHERE gym_id = NEW.gym_id AND payment_id = NEW.id AND status = 'ISSUED';
    IF issued_count <> 1 THEN
      RAISE EXCEPTION 'payment_posting: posted payment requires exactly one issued receipt';
    END IF;
    IF NEW.method = 'CASH' AND NEW.legacy_unlinked_cash THEN
      IF NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL THEN
        RAISE EXCEPTION 'payment_posting: legacy unlinked cash cannot also have cash links';
      END IF;
      IF EXISTS (
        SELECT 1 FROM public.cashbook_cash_transaction
        WHERE gym_id = NEW.gym_id AND source_type = 'PAYMENT' AND source_id = NEW.id
      ) THEN
        RAISE EXCEPTION 'payment_posting: legacy unlinked cash cannot have a movement';
      END IF;
    ELSIF NEW.method = 'CASH' THEN
      IF NEW.cash_session_id IS NULL OR NEW.cash_transaction_id IS NULL THEN
        RAISE EXCEPTION 'payment_posting: cash payment requires cash links';
      END IF;
      IF (
        SELECT count(*) FROM public.cashbook_cash_transaction
        WHERE gym_id = NEW.gym_id
          AND id = NEW.cash_transaction_id
          AND cash_session_id = NEW.cash_session_id
          AND source_type = 'PAYMENT'
          AND source_id = NEW.id
          AND direction = CASE WHEN NEW.entry_type = 'CHARGE' THEN 'IN' ELSE 'OUT' END
          AND amount = NEW.amount
          AND currency = NEW.currency
          AND business_date = NEW.business_date
      ) <> 1 THEN
        RAISE EXCEPTION 'payment_posting: cash movement mismatch';
      END IF;
    ELSIF NEW.cash_session_id IS NOT NULL
          OR NEW.cash_transaction_id IS NOT NULL
          OR NEW.legacy_unlinked_cash THEN
      RAISE EXCEPTION 'payment_posting: noncash payment prohibits cash links';
    END IF;
  ELSIF NEW.status = 'REJECTED' AND (
    NEW.cash_session_id IS NOT NULL
    OR NEW.cash_transaction_id IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM public.members_receipt
      WHERE gym_id = NEW.gym_id AND payment_id = NEW.id
    )
  ) THEN
    RAISE EXCEPTION 'payment_posting: rejected payment has settlement evidence';
  ELSIF NEW.status = 'VOID' THEN
    SELECT count(*) INTO issued_count
    FROM public.members_receipt
    WHERE gym_id = NEW.gym_id AND payment_id = NEW.id AND status = 'ISSUED';
    IF issued_count <> 0 THEN
      RAISE EXCEPTION 'payment_posting: void payment cannot keep an issued receipt';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER payment_posting_commit_trg
  AFTER INSERT OR UPDATE ON members_payment
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_payment_posting_commit();

CREATE FUNCTION app_payment_reversal_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  linked_id uuid;
  linked_type text;
  linked_original uuid;
  linked_status text;
  linked_amount numeric;
  linked_currency text;
  linked_member uuid;
  linked_membership uuid;
  linked_purpose text;
  linked_method text;
  linked_legacy boolean;
  movement_count integer;
BEGIN
  IF NEW.entry_type = 'REVERSAL' AND NEW.status = 'POSTED' THEN
    SELECT reversal_payment_id, status, amount, currency, member_id, membership_id,
           purpose, method, legacy_unlinked_cash
      INTO linked_id, linked_status, linked_amount, linked_currency, linked_member,
           linked_membership, linked_purpose, linked_method, linked_legacy
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
    IF linked_id IS DISTINCT FROM NEW.id OR linked_status IS DISTINCT FROM 'VOID' THEN
      RAISE EXCEPTION 'payment_reversal: posted reversal requires the voided original link';
    END IF;
    IF linked_amount IS DISTINCT FROM NEW.amount
       OR linked_currency IS DISTINCT FROM NEW.currency
       OR linked_member IS DISTINCT FROM NEW.member_id
       OR linked_membership IS DISTINCT FROM NEW.membership_id
       OR linked_purpose IS DISTINCT FROM NEW.purpose THEN
      RAISE EXCEPTION 'payment_reversal: reversal does not match the original';
    END IF;
    -- A legacy unlinked cash reversal has no movement, the same exception as a
    -- legacy unlinked cash charge. Every other reversal of a cash charge needs
    -- one OUT movement of the same amount and currency.
    IF linked_method = 'CASH'
       AND linked_legacy IS NOT TRUE
       AND NEW.legacy_unlinked_cash IS NOT TRUE THEN
      SELECT count(*) INTO movement_count
      FROM public.cashbook_cash_transaction
      WHERE gym_id = NEW.gym_id
        AND source_type = 'PAYMENT'
        AND source_id = NEW.id
        AND direction = 'OUT'
        AND amount = NEW.amount
        AND currency = NEW.currency;
      IF movement_count <> 1 THEN
        RAISE EXCEPTION 'payment_reversal: cash reversal requires an out movement';
      END IF;
    END IF;
  END IF;
  IF NEW.reversal_payment_id IS NOT NULL THEN
    SELECT entry_type, original_payment_id, status, amount, currency,
           member_id, membership_id, purpose
      INTO linked_type, linked_original, linked_status, linked_amount, linked_currency,
           linked_member, linked_membership, linked_purpose
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.reversal_payment_id;
    IF linked_type IS DISTINCT FROM 'REVERSAL'
       OR linked_original IS DISTINCT FROM NEW.id
       OR linked_status IS DISTINCT FROM 'POSTED'
       OR linked_amount IS DISTINCT FROM NEW.amount
       OR linked_currency IS DISTINCT FROM NEW.currency
       OR linked_member IS DISTINCT FROM NEW.member_id
       OR linked_membership IS DISTINCT FROM NEW.membership_id
       OR linked_purpose IS DISTINCT FROM NEW.purpose THEN
      RAISE EXCEPTION 'payment_reversal: reversal link does not match';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER payment_reversal_commit_trg
  AFTER INSERT OR UPDATE ON members_payment
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_payment_reversal_commit();

CREATE FUNCTION app_receipt_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  pay_status text;
  issued_count integer;
BEGIN
  SELECT status INTO pay_status
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id AND id = NEW.payment_id;
  IF pay_status = 'POSTED' THEN
    SELECT count(*) INTO issued_count
    FROM public.members_receipt
    WHERE gym_id = NEW.gym_id AND payment_id = NEW.payment_id AND status = 'ISSUED';
    IF issued_count <> 1 THEN
      RAISE EXCEPTION 'payment_posting: posted payment requires exactly one issued receipt';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER receipt_posting_commit_trg
  AFTER INSERT OR UPDATE ON members_receipt
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_receipt_posting_commit();

CREATE FUNCTION app_payment_cash_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  pay_method text;
  pay_status text;
  pay_legacy boolean;
  pay_txn uuid;
BEGIN
  IF NEW.source_type IS DISTINCT FROM 'PAYMENT' OR NEW.source_id IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT method, status, legacy_unlinked_cash, cash_transaction_id
    INTO pay_method, pay_status, pay_legacy, pay_txn
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id AND id = NEW.source_id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF pay_method <> 'CASH' OR pay_legacy THEN
    RAISE EXCEPTION 'payment_posting: payment cannot accept this cash movement';
  END IF;
  IF pay_status = 'POSTED' AND pay_txn IS DISTINCT FROM NEW.id THEN
    RAISE EXCEPTION 'payment_posting: cash movement mismatch';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER payment_cash_commit_trg
  AFTER INSERT OR UPDATE ON cashbook_cash_transaction
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_payment_cash_commit();

ALTER TABLE members_payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE members_payment FORCE ROW LEVEL SECURITY;
ALTER TABLE members_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE members_receipt FORCE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE members_payment FROM gym_runtime;
REVOKE ALL ON TABLE members_receipt FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE members_payment TO gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE members_receipt TO gym_runtime;

REVOKE ALL ON FUNCTION app_has_protected_reception(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_payment() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_payment_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_receipt() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_payment_posting_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_payment_reversal_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_receipt_posting_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_payment_cash_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_has_protected_reception(uuid, uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_payment() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_payment_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_receipt() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_payment_posting_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_payment_reversal_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_receipt_posting_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_payment_cash_commit() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS payment_cash_commit_trg ON cashbook_cash_transaction;
DROP TRIGGER IF EXISTS receipt_posting_commit_trg ON members_receipt;
DROP TRIGGER IF EXISTS payment_reversal_commit_trg ON members_payment;
DROP TRIGGER IF EXISTS payment_posting_commit_trg ON members_payment;
DROP TRIGGER IF EXISTS receipt_delete_trg ON members_receipt;
DROP TRIGGER IF EXISTS receipt_guard_trg ON members_receipt;
DROP TRIGGER IF EXISTS payment_delete_trg ON members_payment;
DROP TRIGGER IF EXISTS payment_guard_trg ON members_payment;
DROP FUNCTION IF EXISTS app_payment_cash_commit();
DROP FUNCTION IF EXISTS app_receipt_posting_commit();
DROP FUNCTION IF EXISTS app_payment_reversal_commit();
DROP FUNCTION IF EXISTS app_payment_posting_commit();
DROP FUNCTION IF EXISTS app_guard_receipt();
DROP FUNCTION IF EXISTS app_guard_payment_delete();
DROP FUNCTION IF EXISTS app_guard_payment();
DROP FUNCTION IF EXISTS app_has_protected_reception(uuid, uuid);
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_currency_ck;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_purpose_ck;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_method_ck;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_entry_ck;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_original_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_reversal_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cash_session_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cash_txn_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_created_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_updated_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_submitted_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_approved_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_rejected_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_cancelled_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_posted_by_same_gym_fk;
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_voided_by_same_gym_fk;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_currency_ck;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_amount_positive_ck;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_replacement_same_gym_fk;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_file_same_gym_fk;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_created_by_same_gym_fk;
ALTER TABLE members_receipt DROP CONSTRAINT IF EXISTS receipt_voided_by_same_gym_fk;
DROP INDEX IF EXISTS payment_one_reversal_per_original_idx;
ALTER TABLE members_receipt ALTER COLUMN sequence_key DROP NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN sequence_value DROP NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN sequence_year DROP NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN gym_code DROP NOT NULL;
ALTER TABLE members_receipt ALTER COLUMN gym_name DROP NOT NULL;
"""


class Migration(migrations.Migration):
    dependencies = [("members", "0016_payment_receipt_legacy_reconcile")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="receipt",
                    name="sequence_key",
                    field=models.CharField(max_length=48),
                ),
                migrations.AlterField(
                    model_name="receipt",
                    name="sequence_value",
                    field=models.PositiveBigIntegerField(),
                ),
                migrations.AlterField(
                    model_name="receipt",
                    name="sequence_year",
                    field=models.PositiveIntegerField(),
                ),
                migrations.AlterField(
                    model_name="receipt",
                    name="gym_code",
                    field=models.CharField(max_length=32),
                ),
                migrations.AlterField(
                    model_name="receipt",
                    name="gym_name",
                    field=models.CharField(max_length=150),
                ),
            ],
            database_operations=[
                migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
            ],
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.UniqueConstraint(
                fields=("gym", "sequence_key", "sequence_value"),
                name="receipt_gym_sequence_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="receipt",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="ISSUED"),
                fields=("gym", "payment"),
                name="receipt_one_issued_per_payment",
            ),
        ),
    ]
