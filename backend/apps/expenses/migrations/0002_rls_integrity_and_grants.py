from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_protect_system_expense_category()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.system_key IS NOT NULL AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.system_key IS DISTINCT FROM OLD.system_key
    OR NEW.code IS DISTINCT FROM OLD.code
    OR NEW.name IS DISTINCT FROM OLD.name
    OR NEW.normalized_name IS DISTINCT FROM OLD.normalized_name
    OR NEW.display_order IS DISTINCT FROM OLD.display_order
    OR NEW.is_active IS DISTINCT FROM OLD.is_active
    OR NEW.archived_at IS DISTINCT FROM OLD.archived_at
  ) THEN
    RAISE EXCEPTION 'protected expense category is immutable';
  END IF;
  RETURN NEW;
END;
$$;

ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_category_same_gym_fk
  FOREIGN KEY (gym_id, category_id)
  REFERENCES tenants_expense_category (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_file_same_gym_fk
  FOREIGN KEY (gym_id, receipt_file_asset_id)
  REFERENCES files_file_asset (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_reversal_same_gym_fk
  FOREIGN KEY (gym_id, reversal_expense_id)
  REFERENCES expenses_expense (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_cash_session_same_gym_fk
  FOREIGN KEY (gym_id, cash_session_id)
  REFERENCES cashbook_cash_session (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_cash_txn_same_gym_fk
  FOREIGN KEY (gym_id, cash_transaction_id)
  REFERENCES cashbook_cash_transaction (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_submitted_by_same_gym_fk
  FOREIGN KEY (gym_id, submitted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_approved_by_same_gym_fk
  FOREIGN KEY (gym_id, approved_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_rejected_by_same_gym_fk
  FOREIGN KEY (gym_id, rejected_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE expenses_expense
  ADD CONSTRAINT expense_posted_by_same_gym_fk
  FOREIGN KEY (gym_id, posted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE UNIQUE INDEX IF NOT EXISTS expense_one_reversal_idx
  ON expenses_expense (gym_id, reversal_expense_id)
  WHERE reversal_expense_id IS NOT NULL;

ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_code_format_ck;
ALTER TABLE tenants_expense_category ADD CONSTRAINT expcat_code_format_ck
  CHECK (code ~ '^[A-Z][A-Z0-9_]{0,31}$');
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_name_nonblank_ck;
ALTER TABLE tenants_expense_category ADD CONSTRAINT expcat_name_nonblank_ck
  CHECK (btrim(name) <> '' AND btrim(normalized_name) <> '');
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_created_by_same_gym_fk;
ALTER TABLE tenants_expense_category ADD CONSTRAINT expcat_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_updated_by_same_gym_fk;
ALTER TABLE tenants_expense_category ADD CONSTRAINT expcat_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_expense()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
BEGIN
  IF NEW.currency !~ '^[A-Z]{3}$' OR NEW.amount <= 0 THEN
    RAISE EXCEPTION 'expense_guard: amount and currency are invalid';
  END IF;
  IF NEW.payment_method NOT IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER')
     OR NEW.status NOT IN (
       'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'VOID', 'CANCELLED'
     ) THEN
    RAISE EXCEPTION 'expense_guard: expense classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'expense_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'expense_guard: currency must match the gym';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.tenants_expense_category
    WHERE gym_id = NEW.gym_id AND id = NEW.category_id
  ) THEN
    RAISE EXCEPTION 'expense_guard: category must belong to the gym';
  END IF;
  IF NEW.receipt_file_asset_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.files_file_asset
    WHERE gym_id = NEW.gym_id
      AND id = NEW.receipt_file_asset_id
      AND purpose = 'EXPENSE_RECEIPT'
  ) THEN
    RAISE EXCEPTION 'expense_guard: receipt must be a same-gym expense file';
  END IF;
  IF NEW.status IN ('PENDING_APPROVAL', 'POSTED')
     AND NEW.receipt_file_asset_id IS NOT NULL
     AND NOT EXISTS (
       SELECT 1 FROM public.files_file_asset
       WHERE gym_id = NEW.gym_id
         AND id = NEW.receipt_file_asset_id
         AND purpose = 'EXPENSE_RECEIPT'
         AND scan_status = 'CLEAN'
         AND retention_status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'expense_guard: receipt must be clean before submit or post';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'expense_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE
       AND current_user IS DISTINCT FROM 'gym_migration' THEN
      RAISE EXCEPTION 'expense_guard: void is reserved to the migration role';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'expense_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'expense_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'expense_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'expense_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'expense_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'expense_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'expense_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'expense_guard: terminal expense is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'expense_guard: posted expense is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.category_id IS DISTINCT FROM OLD.category_id
    OR NEW.description IS DISTINCT FROM OLD.description
    OR NEW.amount IS DISTINCT FROM OLD.amount
    OR NEW.currency IS DISTINCT FROM OLD.currency
    OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.receipt_file_asset_id IS DISTINCT FROM OLD.receipt_file_asset_id
    OR NEW.policy_note IS DISTINCT FROM OLD.policy_note
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'expense_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'expense_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
  ) THEN
    RAISE EXCEPTION 'expense_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'expense_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'expense_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'expense_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'expense_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'expense_guard: cancellation evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_expense_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  RAISE EXCEPTION 'expense_guard: expenses cannot be deleted';
END;
$$;

CREATE TRIGGER expense_guard_trg
  BEFORE INSERT OR UPDATE ON expenses_expense
  FOR EACH ROW EXECUTE FUNCTION app_guard_expense();
CREATE TRIGGER expense_delete_trg
  BEFORE DELETE ON expenses_expense
  FOR EACH ROW EXECUTE FUNCTION app_guard_expense_delete();

CREATE FUNCTION app_expense_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.status = 'POSTED' THEN
    IF NEW.payment_method = 'CASH' THEN
      IF NEW.cash_session_id IS NULL OR NEW.cash_transaction_id IS NULL THEN
        RAISE EXCEPTION 'expense_posting: cash expense requires cash links';
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
          AND txn.source_type = 'EXPENSE'
          AND txn.source_id = NEW.id
          AND txn.direction = 'OUT'
          AND txn.amount = NEW.amount
          AND txn.currency = NEW.currency
          AND txn.business_date = NEW.business_date
      ) <> 1 THEN
        RAISE EXCEPTION 'expense_posting: cash movement mismatch';
      END IF;
      IF (
        SELECT count(*) FROM public.cashbook_cash_transaction
        WHERE gym_id = NEW.gym_id
          AND source_type = 'EXPENSE'
          AND source_id = NEW.id
      ) <> 1 THEN
        RAISE EXCEPTION 'expense_posting: cash expense requires exactly one movement';
      END IF;
      IF NEW.created_with_reception_scope AND NEW.created_by_membership_id IS DISTINCT FROM (
        SELECT opened_by_membership_id
        FROM public.cashbook_cash_session
        WHERE gym_id = NEW.gym_id AND id = NEW.cash_session_id
      ) THEN
        RAISE EXCEPTION 'expense_posting: reception till mismatch';
      END IF;
      IF NEW.submitted_with_reception_scope AND NEW.submitted_by_membership_id IS DISTINCT FROM (
        SELECT opened_by_membership_id
        FROM public.cashbook_cash_session
        WHERE gym_id = NEW.gym_id AND id = NEW.cash_session_id
      ) THEN
        RAISE EXCEPTION 'expense_posting: reception till mismatch';
      END IF;
    ELSIF NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'expense_posting: noncash expense prohibits cash links';
    ELSIF EXISTS (
      SELECT 1 FROM public.cashbook_cash_transaction
      WHERE gym_id = NEW.gym_id AND source_type = 'EXPENSE' AND source_id = NEW.id
    ) THEN
      RAISE EXCEPTION 'expense_posting: noncash expense cannot have a movement';
    END IF;
  ELSIF NEW.status IN ('REJECTED', 'CANCELLED', 'DRAFT') AND (
    NEW.cash_session_id IS NOT NULL
    OR NEW.cash_transaction_id IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM public.cashbook_cash_transaction
      WHERE gym_id = NEW.gym_id AND source_type = 'EXPENSE' AND source_id = NEW.id
    )
  ) THEN
    RAISE EXCEPTION 'expense_posting: unposted expense has cash evidence';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER expense_posting_commit_trg
  AFTER INSERT OR UPDATE ON expenses_expense
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_expense_posting_commit();

CREATE FUNCTION app_expense_cash_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  exp_method text;
  exp_status text;
  exp_txn uuid;
BEGIN
  IF NEW.source_type IS DISTINCT FROM 'EXPENSE' OR NEW.source_id IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT payment_method, status, cash_transaction_id
    INTO exp_method, exp_status, exp_txn
  FROM public.expenses_expense
  WHERE gym_id = NEW.gym_id AND id = NEW.source_id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF exp_method <> 'CASH' THEN
    RAISE EXCEPTION 'expense_posting: expense cannot accept this cash movement';
  END IF;
  IF exp_status = 'POSTED' AND exp_txn IS DISTINCT FROM NEW.id THEN
    RAISE EXCEPTION 'expense_posting: cash movement mismatch';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER expense_cash_commit_trg
  AFTER INSERT OR UPDATE ON cashbook_cash_transaction
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_expense_cash_commit();

ALTER TABLE expenses_expense ENABLE ROW LEVEL SECURITY;
ALTER TABLE expenses_expense FORCE ROW LEVEL SECURITY;
CREATE POLICY expenses_expense_select ON expenses_expense
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY expenses_expense_insert ON expenses_expense
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY expenses_expense_update ON expenses_expense
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY expenses_expense_delete ON expenses_expense
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE expenses_expense FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE expenses_expense TO gym_runtime;

REVOKE ALL ON FUNCTION app_guard_expense() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_expense_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_expense_posting_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_expense_cash_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_expense() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_expense_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_expense_posting_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_expense_cash_commit() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_protect_system_expense_category()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.system_key IS NOT NULL AND (
    NEW.system_key IS DISTINCT FROM OLD.system_key
    OR NEW.code IS DISTINCT FROM OLD.code
    OR NEW.is_active IS FALSE
  ) THEN
    RAISE EXCEPTION 'protected expense category identity and active state are immutable';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS expense_cash_commit_trg ON cashbook_cash_transaction;
DROP TRIGGER IF EXISTS expense_posting_commit_trg ON expenses_expense;
DROP TRIGGER IF EXISTS expense_delete_trg ON expenses_expense;
DROP TRIGGER IF EXISTS expense_guard_trg ON expenses_expense;
DROP FUNCTION IF EXISTS app_expense_cash_commit();
DROP FUNCTION IF EXISTS app_expense_posting_commit();
DROP FUNCTION IF EXISTS app_guard_expense_delete();
DROP FUNCTION IF EXISTS app_guard_expense();
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_posted_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_cancelled_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_rejected_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_approved_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_submitted_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_updated_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_created_by_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_cash_txn_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_cash_session_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_reversal_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_file_same_gym_fk;
ALTER TABLE expenses_expense DROP CONSTRAINT IF EXISTS expense_category_same_gym_fk;
DROP INDEX IF EXISTS expense_one_reversal_idx;
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_updated_by_same_gym_fk;
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_created_by_same_gym_fk;
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_name_nonblank_ck;
ALTER TABLE tenants_expense_category DROP CONSTRAINT IF EXISTS expcat_code_format_ck;
DROP POLICY IF EXISTS expenses_expense_delete ON expenses_expense;
DROP POLICY IF EXISTS expenses_expense_update ON expenses_expense;
DROP POLICY IF EXISTS expenses_expense_insert ON expenses_expense;
DROP POLICY IF EXISTS expenses_expense_select ON expenses_expense;
ALTER TABLE expenses_expense NO FORCE ROW LEVEL SECURITY;
ALTER TABLE expenses_expense DISABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE expenses_expense FROM gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0001_expense_receipt_and_expense"),
        ("files", "0004_expense_receipt_and_expense"),
        ("members", "0017_payment_receipt_hardening"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
