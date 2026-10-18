from django.db import migrations, models

FORWARD_SQL = r"""
ALTER TABLE salaries_salary_period
  ADD CONSTRAINT salper_voided_by_same_gym_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION app_guard_salary_period()
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
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL
    OR NEW.voided_by_membership_id IS NULL
    OR NEW.void_business_date IS NULL
    OR NEW.void_business_date < NEW.business_date
    OR btrim(NEW.reason) = ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  IF NEW.status IS DISTINCT FROM 'VOID' AND (
    NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
    OR NEW.void_business_date IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;


CREATE OR REPLACE FUNCTION app_salary_posting_commit()
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
  ELSIF NEW.status = 'VOID' THEN
    IF (
      SELECT count(*) FROM public.salaries_staff_salary_line
      WHERE gym_id = NEW.gym_id AND salary_period_id = NEW.id
    ) < 1 THEN
      RAISE EXCEPTION 'salary_posting: a void period requires lines';
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
            FROM public.expenses_expense original
            JOIN public.tenants_expense_category category
              ON category.gym_id = original.gym_id
             AND category.id = original.category_id
            JOIN public.expenses_expense reversal
              ON reversal.gym_id = original.gym_id
             AND reversal.id = original.reversal_expense_id
            WHERE original.gym_id = line.gym_id
              AND original.id = line.expense_id
              AND original.status = 'VOID'
              AND category.system_key = 'STAFF_SALARY'
              AND original.amount = line.amount
              AND original.currency = line.currency
              AND original.payment_method = line.payment_method
              AND original.business_date = NEW.business_date
              AND reversal.original_expense_id = original.id
              AND reversal.status = 'POSTED'
              AND reversal.category_id = original.category_id
              AND reversal.amount = original.amount
              AND reversal.currency = original.currency
              AND reversal.payment_method = original.payment_method
              AND reversal.business_date = NEW.void_business_date
              AND reversal.policy_note = original.policy_note
              AND (
                SELECT count(*) FROM public.expenses_expense
                WHERE gym_id = original.gym_id AND original_expense_id = original.id
              ) = 1
          )
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: each line requires one salary reversal';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.salaries_staff_salary_line line
      JOIN public.expenses_expense original
        ON original.gym_id = line.gym_id
       AND original.id = line.expense_id
      JOIN public.expenses_expense reversal
        ON reversal.gym_id = original.gym_id
       AND reversal.id = original.reversal_expense_id
      WHERE line.gym_id = NEW.gym_id
        AND line.salary_period_id = NEW.id
        AND line.payment_method = 'CASH'
        AND (
          original.cash_transaction_id IS NULL
          OR reversal.cash_session_id IS NULL
          OR reversal.cash_transaction_id IS NULL
          OR (
            SELECT count(*)
            FROM public.cashbook_cash_transaction txn
            JOIN public.cashbook_cash_session session
              ON session.gym_id = txn.gym_id
             AND session.id = txn.cash_session_id
            WHERE txn.gym_id = reversal.gym_id
              AND txn.id = reversal.cash_transaction_id
              AND txn.cash_session_id = reversal.cash_session_id
              AND session.status = 'OPEN'
              AND session.currency = line.currency
              AND session.business_date = NEW.void_business_date
              AND txn.source_type = 'EXPENSE'
              AND txn.source_id = reversal.id
              AND txn.direction = 'IN'
              AND txn.amount = line.amount
              AND txn.currency = line.currency
              AND txn.business_date = NEW.void_business_date
              AND txn.reversal_of_id = original.cash_transaction_id
          ) <> 1
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: cash line reversal mismatch';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.salaries_staff_salary_line line
      JOIN public.expenses_expense original
        ON original.gym_id = line.gym_id
       AND original.id = line.expense_id
      JOIN public.expenses_expense reversal
        ON reversal.gym_id = original.gym_id
       AND reversal.id = original.reversal_expense_id
      WHERE line.gym_id = NEW.gym_id
        AND line.salary_period_id = NEW.id
        AND line.payment_method <> 'CASH'
        AND (
          reversal.cash_session_id IS NOT NULL
          OR reversal.cash_transaction_id IS NOT NULL
          OR EXISTS (
            SELECT 1 FROM public.cashbook_cash_transaction
            WHERE gym_id = reversal.gym_id
              AND source_type = 'EXPENSE'
              AND source_id = reversal.id
          )
        )
    ) THEN
      RAISE EXCEPTION 'salary_posting: noncash reversal prohibits cash links';
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

"""

REVERSE_SQL = r"""
ALTER TABLE salaries_salary_period DROP CONSTRAINT IF EXISTS salper_voided_by_same_gym_fk;

CREATE OR REPLACE FUNCTION app_guard_salary_period()
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


CREATE OR REPLACE FUNCTION app_salary_posting_commit()
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

"""


class Migration(migrations.Migration):
    dependencies = [
        ("salaries", "0002_rls_integrity_and_grants"),
        ("expenses", "0004_correction_runtime_reversal"),
    ]

    operations = [
        migrations.AddField(
            model_name="salaryperiod",
            name="voided_by_membership_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="salaryperiod",
            name="void_business_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
