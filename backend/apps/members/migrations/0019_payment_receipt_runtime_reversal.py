from django.db import migrations

# Runtime may void a posted charge only together with its posted reversal.
# A session setting is not an authorization boundary. A raw status change
# without the reversal, void evidence, and required cash link still fails.

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


CREATE OR REPLACE FUNCTION app_payment_posting_commit()
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
        SELECT count(*) FROM public.cashbook_cash_transaction txn
        JOIN public.cashbook_cash_session session
          ON session.gym_id = txn.gym_id
         AND session.id = txn.cash_session_id
        WHERE txn.gym_id = NEW.gym_id
          AND txn.id = NEW.cash_transaction_id
          AND txn.cash_session_id = NEW.cash_session_id
          AND session.status = 'OPEN'
          AND txn.source_type = 'PAYMENT'
          AND txn.source_id = NEW.id
          AND txn.direction = CASE WHEN NEW.entry_type = 'CHARGE' THEN 'IN' ELSE 'OUT' END
          AND txn.amount = NEW.amount
          AND txn.currency = NEW.currency
          AND txn.business_date = NEW.business_date
      ) <> 1 THEN
        RAISE EXCEPTION 'payment_posting: cash movement mismatch';
      END IF;
      IF NEW.entry_type = 'REVERSAL' AND (
        SELECT txn.reversal_of_id
        FROM public.cashbook_cash_transaction txn
        WHERE txn.gym_id = NEW.gym_id AND txn.id = NEW.cash_transaction_id
      ) IS DISTINCT FROM (
        SELECT cash_transaction_id
        FROM public.members_payment
        WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id
      ) THEN
        RAISE EXCEPTION 'payment_reversal: cash reversal must link the original movement';
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


CREATE OR REPLACE FUNCTION app_payment_reversal_commit()
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
       OR linked_purpose IS DISTINCT FROM NEW.purpose
       OR linked_method IS DISTINCT FROM NEW.method THEN
      RAISE EXCEPTION 'payment_reversal: reversal does not match the original';
    END IF;
    -- Legacy unlinked cash has no original movement. Its reversal must not
    -- invent one. Every other cash reversal needs one OUT movement that points
    -- at the original movement and lands in an open session.
    IF linked_legacy IS TRUE THEN
      IF NEW.legacy_unlinked_cash IS NOT TRUE
         OR NEW.cash_session_id IS NOT NULL
         OR NEW.cash_transaction_id IS NOT NULL
         OR EXISTS (
           SELECT 1 FROM public.cashbook_cash_transaction
           WHERE gym_id = NEW.gym_id AND source_type = 'PAYMENT' AND source_id = NEW.id
         ) THEN
        RAISE EXCEPTION 'payment_reversal: legacy cash reversal cannot invent a movement';
      END IF;
    ELSIF linked_method = 'CASH' THEN
      IF NEW.legacy_unlinked_cash IS TRUE THEN
        RAISE EXCEPTION 'payment_reversal: legacy cash reversal cannot invent a movement';
      END IF;
      SELECT count(*) INTO movement_count
      FROM public.cashbook_cash_transaction txn
      JOIN public.cashbook_cash_session session
        ON session.gym_id = txn.gym_id
       AND session.id = txn.cash_session_id
      WHERE txn.gym_id = NEW.gym_id
        AND txn.source_type = 'PAYMENT'
        AND txn.source_id = NEW.id
        AND txn.direction = 'OUT'
        AND txn.amount = NEW.amount
        AND txn.currency = NEW.currency
        AND session.status = 'OPEN'
        AND txn.reversal_of_id = (
          SELECT cash_transaction_id
          FROM public.members_payment
          WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id
        );
      IF movement_count <> 1 THEN
        RAISE EXCEPTION 'payment_reversal: cash reversal requires an out movement';
      END IF;
    ELSIF NEW.cash_session_id IS NOT NULL
          OR NEW.cash_transaction_id IS NOT NULL
          OR NEW.legacy_unlinked_cash THEN
      RAISE EXCEPTION 'payment_reversal: noncash reversal prohibits cash links';
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



CREATE FUNCTION app_payment_refundable_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  charge_id uuid;
  charge_amount numeric;
  charge_type text;
  used numeric;
BEGIN
  IF NEW.entry_type = 'CHARGE' AND NEW.status IN ('POSTED', 'VOID') THEN
    charge_id := NEW.id;
    charge_amount := NEW.amount;
    charge_type := 'CHARGE';
  ELSIF NEW.entry_type IN ('REFUND', 'REVERSAL') AND NEW.status = 'POSTED' THEN
    SELECT id, amount, entry_type
      INTO charge_id, charge_amount, charge_type
    FROM public.members_payment
    WHERE gym_id = NEW.gym_id AND id = NEW.original_payment_id;
  ELSE
    RETURN NULL;
  END IF;
  IF charge_type IS DISTINCT FROM 'CHARGE' THEN
    RAISE EXCEPTION 'payment_refund: original must be a charge';
  END IF;
  SELECT COALESCE(SUM(amount), 0) INTO used
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id
    AND original_payment_id = charge_id
    AND status = 'POSTED'
    AND entry_type IN ('REFUND', 'REVERSAL');
  IF used > charge_amount THEN
    RAISE EXCEPTION 'payment_refund: refundable balance exceeded';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER payment_refundable_commit_trg
  AFTER INSERT OR UPDATE ON members_payment
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_payment_refundable_commit();

CREATE OR REPLACE FUNCTION app_receipt_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  pay_status text;
  issued_count integer;
BEGIN
  -- A document-only receipt void leaves the payment posted and economic facts unchanged.
  IF NEW.status = 'VOID' THEN
    RETURN NULL;
  END IF;
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

REVOKE ALL ON FUNCTION app_payment_refundable_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_payment_refundable_commit() TO gym_runtime, gym_migration;

"""

REVERSE_SQL = r"""

DROP TRIGGER IF EXISTS payment_refundable_commit_trg ON members_payment;
DROP FUNCTION IF EXISTS app_payment_refundable_commit();
CREATE OR REPLACE FUNCTION app_guard_payment()
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


CREATE OR REPLACE FUNCTION app_payment_posting_commit()
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


CREATE OR REPLACE FUNCTION app_payment_reversal_commit()
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

CREATE OR REPLACE FUNCTION app_receipt_posting_commit()
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


"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0018_retail_sale_foundation"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
