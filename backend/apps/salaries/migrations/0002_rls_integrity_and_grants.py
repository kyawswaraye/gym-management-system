from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_submitted_by_same_gym_fk
  FOREIGN KEY (gym_id, submitted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_approved_by_same_gym_fk
  FOREIGN KEY (gym_id, approved_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_rejected_by_same_gym_fk
  FOREIGN KEY (gym_id, rejected_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_posted_by_same_gym_fk
  FOREIGN KEY (gym_id, posted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_description_nonblank_ck
  CHECK (btrim(description) <> '');

ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_period_same_gym_fk
  FOREIGN KEY (gym_id, salary_period_id)
  REFERENCES salaries_salary_period (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_staff_same_gym_fk
  FOREIGN KEY (gym_id, staff_profile_id)
  REFERENCES tenants_staff_profile (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_session_same_gym_fk
  FOREIGN KEY (gym_id, cash_session_id)
  REFERENCES cashbook_cash_session (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_expense_same_gym_fk
  FOREIGN KEY (gym_id, expense_id)
  REFERENCES expenses_expense (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE salaries_staff_salary_line
  ADD CONSTRAINT salline_description_nonblank_ck
  CHECK (btrim(description) <> '');

CREATE FUNCTION app_guard_salary_period()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
BEGIN
  IF NEW.period_end < NEW.period_start OR btrim(NEW.description) = '' THEN
    RAISE EXCEPTION 'salary_guard: period dates or description are invalid';
  END IF;
  IF NEW.status NOT IN (
    'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
  ) THEN
    RAISE EXCEPTION 'salary_guard: salary classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'salary_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'salary_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE
       AND current_user IS DISTINCT FROM 'gym_migration' THEN
      RAISE EXCEPTION 'salary_guard: void is reserved to the migration role';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'salary_guard: terminal salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'salary_guard: posted salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.period_start IS DISTINCT FROM OLD.period_start
    OR NEW.period_end IS DISTINCT FROM OLD.period_end
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.description IS DISTINCT FROM OLD.description
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'salary_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'salary_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
  ) THEN
    RAISE EXCEPTION 'salary_guard: approver must post and differ from creator and submitter';
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
  ) THEN
    RAISE EXCEPTION 'salary_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: cancellation evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_salary_period_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
  RAISE EXCEPTION 'salary_guard: salary periods cannot be deleted';
END;
$$;

CREATE TRIGGER salary_period_guard_trg
  BEFORE INSERT OR UPDATE ON salaries_salary_period
  FOR EACH ROW EXECUTE FUNCTION app_guard_salary_period();
CREATE TRIGGER salary_period_delete_trg
  BEFORE DELETE ON salaries_salary_period
  FOR EACH ROW EXECUTE FUNCTION app_guard_salary_period_delete();

CREATE FUNCTION app_guard_salary_line()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_status text;
BEGIN
  IF NEW.amount <= 0 OR NEW.currency !~ '^[A-Z]{3}$' OR btrim(NEW.description) = '' THEN
    RAISE EXCEPTION 'salary_line_guard: amount, currency, or description is invalid';
  END IF;
  IF NEW.payment_method NOT IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER') THEN
    RAISE EXCEPTION 'salary_line_guard: payment method is invalid';
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'salary_line_guard: currency must match the gym';
  END IF;
  SELECT status INTO parent_status
  FROM public.salaries_salary_period
  WHERE gym_id = NEW.gym_id AND id = NEW.salary_period_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'salary_line_guard: period must belong to the gym';
  END IF;
  IF TG_OP = 'INSERT' AND parent_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'salary_line_guard: lines can be added only to a draft';
  END IF;
  IF TG_OP = 'INSERT' AND (NEW.expense_id IS NOT NULL OR NEW.cash_session_id IS NOT NULL) THEN
    RAISE EXCEPTION 'salary_line_guard: a new line cannot carry financial links';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.salary_period_id IS DISTINCT FROM OLD.salary_period_id
  ) THEN
    RAISE EXCEPTION 'salary_line_guard: identity columns are immutable';
  END IF;
  IF parent_status = 'DRAFT' AND (
    NEW.expense_id IS NOT NULL OR NEW.cash_session_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_line_guard: draft lines cannot carry financial links';
  END IF;
  IF parent_status IN ('PENDING_APPROVAL', 'REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'salary_line_guard: lines are frozen';
  END IF;
  IF parent_status = 'POSTED' AND TG_OP = 'UPDATE' THEN
    IF OLD.expense_id IS NOT NULL THEN
      RAISE EXCEPTION 'salary_line_guard: posted line is immutable';
    END IF;
    IF NEW.expense_id IS NULL THEN
      RAISE EXCEPTION 'salary_line_guard: posted line requires an expense';
    END IF;
    IF NEW.staff_profile_id IS DISTINCT FROM OLD.staff_profile_id
       OR NEW.amount IS DISTINCT FROM OLD.amount
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
       OR NEW.external_reference IS DISTINCT FROM OLD.external_reference
       OR NEW.description IS DISTINCT FROM OLD.description THEN
      RAISE EXCEPTION 'salary_line_guard: business fields are frozen';
    END IF;
    IF NEW.payment_method = 'CASH' AND NEW.cash_session_id IS NULL THEN
      RAISE EXCEPTION 'salary_line_guard: cash line requires a cash session';
    END IF;
    IF NEW.payment_method <> 'CASH' AND NEW.cash_session_id IS NOT NULL THEN
      RAISE EXCEPTION 'salary_line_guard: noncash line prohibits a cash session';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_salary_line_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_status text;
BEGIN
  SELECT status INTO parent_status
  FROM public.salaries_salary_period
  WHERE gym_id = OLD.gym_id AND id = OLD.salary_period_id;
  IF parent_status IS DISTINCT FROM 'DRAFT'
     OR OLD.expense_id IS NOT NULL
     OR OLD.cash_session_id IS NOT NULL THEN
    RAISE EXCEPTION 'salary_line_guard: only an unlinked draft line can be replaced';
  END IF;
  RETURN OLD;
END;
$guard$;

CREATE TRIGGER salary_line_guard_trg
  BEFORE INSERT OR UPDATE ON salaries_staff_salary_line
  FOR EACH ROW EXECUTE FUNCTION app_guard_salary_line();
CREATE TRIGGER salary_line_delete_trg
  BEFORE DELETE ON salaries_staff_salary_line
  FOR EACH ROW EXECUTE FUNCTION app_guard_salary_line_delete();

CREATE FUNCTION app_salary_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.status = 'POSTED' THEN
    IF (
      SELECT count(*) FROM public.salaries_staff_salary_line
      WHERE gym_id = NEW.gym_id AND salary_period_id = NEW.id
    ) < 1 THEN
      RAISE EXCEPTION 'salary_posting: a posted period requires lines';
    END IF;
    IF (
      SELECT count(DISTINCT expense_id) FROM public.salaries_staff_salary_line
      WHERE gym_id = NEW.gym_id AND salary_period_id = NEW.id
    ) IS DISTINCT FROM (
      SELECT count(*) FROM public.salaries_staff_salary_line
      WHERE gym_id = NEW.gym_id AND salary_period_id = NEW.id
    ) THEN
      RAISE EXCEPTION 'salary_posting: each line requires its own expense';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.salaries_staff_salary_line line
      WHERE line.gym_id = NEW.gym_id
        AND line.salary_period_id = NEW.id
        AND (
          line.expense_id IS NULL
          OR NOT EXISTS (
            SELECT 1
            FROM public.expenses_expense expense
            JOIN public.tenants_expense_category category
              ON category.gym_id = expense.gym_id
             AND category.id = expense.category_id
            WHERE expense.gym_id = line.gym_id
              AND expense.id = line.expense_id
              AND expense.status = 'POSTED'
              AND category.system_key = 'STAFF_SALARY'
              AND expense.amount = line.amount
              AND expense.currency = line.currency
              AND expense.payment_method = line.payment_method
              AND expense.business_date = NEW.business_date
              AND expense.description = line.description
              AND expense.created_by_membership_id = NEW.created_by_membership_id
              AND expense.submitted_by_membership_id = NEW.submitted_by_membership_id
              AND expense.approved_by_membership_id = NEW.approved_by_membership_id
              AND expense.posted_by_membership_id = NEW.posted_by_membership_id
              AND expense.policy_note = 'Salary period ' || NEW.id::text || ' line ' || line.id::text
          )
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: each line requires one matching salary expense';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.salaries_staff_salary_line line
      JOIN public.expenses_expense expense
        ON expense.gym_id = line.gym_id
       AND expense.id = line.expense_id
      WHERE line.gym_id = NEW.gym_id
        AND line.salary_period_id = NEW.id
        AND line.payment_method = 'CASH'
        AND (
          line.cash_session_id IS NULL
          OR expense.cash_session_id IS DISTINCT FROM line.cash_session_id
          OR expense.payment_method IS DISTINCT FROM 'CASH'
          OR (
            SELECT count(*)
            FROM public.cashbook_cash_transaction txn
            JOIN public.cashbook_cash_session session
              ON session.gym_id = txn.gym_id
             AND session.id = txn.cash_session_id
            WHERE txn.gym_id = line.gym_id
              AND txn.id = expense.cash_transaction_id
              AND txn.cash_session_id = line.cash_session_id
              AND session.status = 'OPEN'
              AND session.currency = line.currency
              AND session.business_date = NEW.business_date
              AND txn.source_type = 'EXPENSE'
              AND txn.source_id = expense.id
              AND txn.direction = 'OUT'
              AND txn.amount = line.amount
              AND txn.currency = line.currency
              AND txn.business_date = NEW.business_date
          ) <> 1
          OR (
            SELECT count(*) FROM public.cashbook_cash_transaction
            WHERE gym_id = line.gym_id
              AND source_type = 'EXPENSE'
              AND source_id = expense.id
          ) <> 1
          OR (
            NEW.created_with_reception_scope
            AND NEW.created_by_membership_id IS DISTINCT FROM (
              SELECT opened_by_membership_id
              FROM public.cashbook_cash_session
              WHERE gym_id = line.gym_id AND id = line.cash_session_id
            )
          )
          OR (
            NEW.submitted_with_reception_scope
            AND NEW.submitted_by_membership_id IS DISTINCT FROM (
              SELECT opened_by_membership_id
              FROM public.cashbook_cash_session
              WHERE gym_id = line.gym_id AND id = line.cash_session_id
            )
          )
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: cash line movement mismatch';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.salaries_staff_salary_line line
      JOIN public.expenses_expense expense
        ON expense.gym_id = line.gym_id
       AND expense.id = line.expense_id
      WHERE line.gym_id = NEW.gym_id
        AND line.salary_period_id = NEW.id
        AND line.payment_method <> 'CASH'
        AND (
          line.cash_session_id IS NOT NULL
          OR expense.cash_session_id IS NOT NULL
          OR expense.cash_transaction_id IS NOT NULL
          OR EXISTS (
            SELECT 1 FROM public.cashbook_cash_transaction
            WHERE gym_id = line.gym_id
              AND source_type = 'EXPENSE'
              AND source_id = expense.id
          )
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: noncash line prohibits cash links';
    END IF;
  ELSIF NEW.status IN ('DRAFT', 'PENDING_APPROVAL', 'REJECTED', 'CANCELLED') AND EXISTS (
    SELECT 1 FROM public.salaries_staff_salary_line
    WHERE gym_id = NEW.gym_id
      AND salary_period_id = NEW.id
      AND (expense_id IS NOT NULL OR cash_session_id IS NOT NULL)
  ) THEN
    RAISE EXCEPTION 'salary_posting: unposted period has financial links';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER salary_posting_commit_trg
  AFTER INSERT OR UPDATE ON salaries_salary_period
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_salary_posting_commit();

ALTER TABLE salaries_salary_period ENABLE ROW LEVEL SECURITY;
ALTER TABLE salaries_salary_period FORCE ROW LEVEL SECURITY;
CREATE POLICY salaries_salary_period_select ON salaries_salary_period
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY salaries_salary_period_insert ON salaries_salary_period
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY salaries_salary_period_update ON salaries_salary_period
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY salaries_salary_period_delete ON salaries_salary_period
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE salaries_staff_salary_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE salaries_staff_salary_line FORCE ROW LEVEL SECURITY;
CREATE POLICY salaries_staff_salary_line_select ON salaries_staff_salary_line
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY salaries_staff_salary_line_insert ON salaries_staff_salary_line
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY salaries_staff_salary_line_update ON salaries_staff_salary_line
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY salaries_staff_salary_line_delete ON salaries_staff_salary_line
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE salaries_salary_period FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE salaries_salary_period TO gym_runtime;
REVOKE ALL ON TABLE salaries_staff_salary_line FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE salaries_staff_salary_line TO gym_runtime;

REVOKE ALL ON FUNCTION app_guard_salary_period() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_salary_period_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_salary_line() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_salary_line_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_salary_posting_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_salary_period() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_salary_period_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_salary_line() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_salary_line_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_salary_posting_commit() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_salary_posting_commit() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_salary_line_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_salary_line() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_salary_period_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_salary_period() FROM gym_runtime, gym_migration;
DROP TRIGGER IF EXISTS salary_posting_commit_trg ON salaries_salary_period;
DROP TRIGGER IF EXISTS salary_line_delete_trg ON salaries_staff_salary_line;
DROP TRIGGER IF EXISTS salary_line_guard_trg ON salaries_staff_salary_line;
DROP TRIGGER IF EXISTS salary_period_delete_trg ON salaries_salary_period;
DROP TRIGGER IF EXISTS salary_period_guard_trg ON salaries_salary_period;
DROP FUNCTION IF EXISTS app_salary_posting_commit();
DROP FUNCTION IF EXISTS app_guard_salary_line_delete();
DROP FUNCTION IF EXISTS app_guard_salary_line();
DROP FUNCTION IF EXISTS app_guard_salary_period_delete();
DROP FUNCTION IF EXISTS app_guard_salary_period();
DROP POLICY IF EXISTS salaries_staff_salary_line_delete ON salaries_staff_salary_line;
DROP POLICY IF EXISTS salaries_staff_salary_line_update ON salaries_staff_salary_line;
DROP POLICY IF EXISTS salaries_staff_salary_line_insert ON salaries_staff_salary_line;
DROP POLICY IF EXISTS salaries_staff_salary_line_select ON salaries_staff_salary_line;
DROP POLICY IF EXISTS salaries_salary_period_delete ON salaries_salary_period;
DROP POLICY IF EXISTS salaries_salary_period_update ON salaries_salary_period;
DROP POLICY IF EXISTS salaries_salary_period_insert ON salaries_salary_period;
DROP POLICY IF EXISTS salaries_salary_period_select ON salaries_salary_period;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_description_nonblank_ck;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_updated_by_same_gym_fk;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_created_by_same_gym_fk;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_expense_same_gym_fk;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_session_same_gym_fk;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_staff_same_gym_fk;
ALTER TABLE salaries_staff_salary_line DROP CONSTRAINT IF EXISTS salline_period_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_description_nonblank_ck;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_posted_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_cancelled_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_rejected_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_approved_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_submitted_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_updated_by_same_gym_fk;
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_created_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("salaries", "0001_salary_period_and_line"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
