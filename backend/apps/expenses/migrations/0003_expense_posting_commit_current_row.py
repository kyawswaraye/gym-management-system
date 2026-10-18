from django.db import migrations

# The deferred trigger queues every insert and update. NEW is that event's row,
# so a draft insert in the same transaction as a later cash movement used to
# fail even when the committed row was posted. The check now reads the row the
# transaction will commit. Public expense commands are unchanged.

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_expense_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_row public.expenses_expense%ROWTYPE;
BEGIN
  SELECT * INTO current_row
  FROM public.expenses_expense
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF current_row.status = 'POSTED' THEN
    IF current_row.payment_method = 'CASH' THEN
      IF current_row.cash_session_id IS NULL OR current_row.cash_transaction_id IS NULL THEN
        RAISE EXCEPTION 'expense_posting: cash expense requires cash links';
      END IF;
      IF (
        SELECT count(*) FROM public.cashbook_cash_transaction txn
        JOIN public.cashbook_cash_session session
          ON session.gym_id = txn.gym_id
         AND session.id = txn.cash_session_id
        WHERE txn.gym_id = current_row.gym_id
          AND txn.id = current_row.cash_transaction_id
          AND txn.cash_session_id = current_row.cash_session_id
          AND session.status = 'OPEN'
          AND txn.source_type = 'EXPENSE'
          AND txn.source_id = current_row.id
          AND txn.direction = 'OUT'
          AND txn.amount = current_row.amount
          AND txn.currency = current_row.currency
          AND txn.business_date = current_row.business_date
      ) <> 1 THEN
        RAISE EXCEPTION 'expense_posting: cash movement mismatch';
      END IF;
      IF (
        SELECT count(*) FROM public.cashbook_cash_transaction
        WHERE gym_id = current_row.gym_id
          AND source_type = 'EXPENSE'
          AND source_id = current_row.id
      ) <> 1 THEN
        RAISE EXCEPTION 'expense_posting: cash expense requires exactly one movement';
      END IF;
      IF current_row.created_with_reception_scope AND current_row.created_by_membership_id IS DISTINCT FROM (
        SELECT opened_by_membership_id
        FROM public.cashbook_cash_session
        WHERE gym_id = current_row.gym_id AND id = current_row.cash_session_id
      ) THEN
        RAISE EXCEPTION 'expense_posting: reception till mismatch';
      END IF;
      IF current_row.submitted_with_reception_scope AND current_row.submitted_by_membership_id IS DISTINCT FROM (
        SELECT opened_by_membership_id
        FROM public.cashbook_cash_session
        WHERE gym_id = current_row.gym_id AND id = current_row.cash_session_id
      ) THEN
        RAISE EXCEPTION 'expense_posting: reception till mismatch';
      END IF;
    ELSIF current_row.cash_session_id IS NOT NULL OR current_row.cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'expense_posting: noncash expense prohibits cash links';
    ELSIF EXISTS (
      SELECT 1 FROM public.cashbook_cash_transaction
      WHERE gym_id = current_row.gym_id AND source_type = 'EXPENSE' AND source_id = current_row.id
    ) THEN
      RAISE EXCEPTION 'expense_posting: noncash expense cannot have a movement';
    END IF;
  ELSIF current_row.status IN ('REJECTED', 'CANCELLED', 'DRAFT') AND (
    current_row.cash_session_id IS NOT NULL
    OR current_row.cash_transaction_id IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM public.cashbook_cash_transaction
      WHERE gym_id = current_row.gym_id AND source_type = 'EXPENSE' AND source_id = current_row.id
    )
  ) THEN
    RAISE EXCEPTION 'expense_posting: unposted expense has cash evidence';
  END IF;
  RETURN NULL;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_expense_posting_commit()
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
"""


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0002_rls_integrity_and_grants"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
