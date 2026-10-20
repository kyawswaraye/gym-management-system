"""Keep a commission payout inside the sale date through gym-local today.

Reverse restores the 0010 payment guard body.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_pt_commission_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_currency text;
  service_date date;
  gym_tz text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'POSTED'
       OR NEW.voided_at IS NOT NULL
       OR NEW.voided_by_membership_id IS NOT NULL
       OR NEW.void_reason <> ''
       OR NEW.reversal_expense_id IS NOT NULL
       OR NEW.reversal_cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: only a posted payout can be inserted';
    END IF;
    SELECT commission.currency, sale.service_date, gym.timezone
      INTO parent_currency, service_date, gym_tz
    FROM trainers_pt_commission AS commission
    JOIN trainers_pt_sale AS sale
      ON sale.gym_id = commission.gym_id
     AND sale.id = commission.pt_sale_id
    JOIN tenants_gym AS gym
      ON gym.id = commission.gym_id
    WHERE commission.gym_id = NEW.gym_id
      AND commission.id = NEW.pt_commission_id;
    IF parent_currency IS NULL OR service_date IS NULL OR gym_tz IS NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: commission is missing';
    END IF;
    IF NEW.business_date < service_date
       OR NEW.business_date > (statement_timestamp() AT TIME ZONE gym_tz)::date THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: payout date is outside the sale window';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM expenses_expense AS expense
      JOIN tenants_expense_category AS category
        ON category.gym_id = expense.gym_id
       AND category.id = expense.category_id
      WHERE expense.gym_id = NEW.gym_id
        AND expense.id = NEW.expense_id
        AND expense.status = 'POSTED'
        AND expense.original_expense_id IS NULL
        AND expense.amount = NEW.amount
        AND expense.payment_method = NEW.payment_method
        AND expense.business_date = NEW.business_date
        AND expense.currency = parent_currency
        AND category.system_key = 'PT_COMMISSION'
    ) THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: expense does not match the payout';
    END IF;
    IF NEW.payment_method = 'CASH' THEN
      IF NOT EXISTS (
        SELECT 1
        FROM cashbook_cash_transaction AS movement
        WHERE movement.gym_id = NEW.gym_id
          AND movement.id = NEW.cash_transaction_id
          AND movement.cash_session_id = NEW.cash_session_id
          AND movement.direction = 'OUT'
          AND movement.source_type = 'EXPENSE'
          AND movement.source_id = NEW.expense_id
          AND movement.amount = NEW.amount
          AND movement.reversal_of_id IS NULL
      ) THEN
        RAISE EXCEPTION 'pt_commission_payment_guard: cash movement does not match the payout';
      END IF;
    ELSIF NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: non-cash payout cannot name a cash session';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP <> 'UPDATE'
     OR OLD.status <> 'POSTED'
     OR NEW.status <> 'VOID' THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: payout rows are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.pt_commission_id IS DISTINCT FROM OLD.pt_commission_id
     OR NEW.amount IS DISTINCT FROM OLD.amount
     OR NEW.business_date IS DISTINCT FROM OLD.business_date
     OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
     OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
     OR NEW.policy_note IS DISTINCT FROM OLD.policy_note
     OR NEW.expense_id IS DISTINCT FROM OLD.expense_id
     OR NEW.cash_session_id IS DISTINCT FROM OLD.cash_session_id
     OR NEW.cash_transaction_id IS DISTINCT FROM OLD.cash_transaction_id
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.voided_at IS NULL
     OR NEW.voided_by_membership_id IS NULL
     OR btrim(NEW.void_reason) = ''
     OR NEW.reversal_expense_id IS NULL THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: void evidence is incomplete';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM expenses_expense AS reversal
    WHERE reversal.gym_id = NEW.gym_id
      AND reversal.id = NEW.reversal_expense_id
      AND reversal.original_expense_id = NEW.expense_id
      AND reversal.status = 'POSTED'
      AND reversal.amount = NEW.amount
  ) THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: reversal expense does not match the payout';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM expenses_expense AS original
    WHERE original.gym_id = NEW.gym_id
      AND original.id = NEW.expense_id
      AND original.status = 'VOID'
      AND original.reversal_expense_id = NEW.reversal_expense_id
  ) THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: original expense is not voided';
  END IF;
  IF NEW.payment_method = 'CASH' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM cashbook_cash_transaction AS movement
      WHERE movement.gym_id = NEW.gym_id
        AND movement.id = NEW.reversal_cash_transaction_id
        AND movement.direction = 'IN'
        AND movement.source_type = 'EXPENSE'
        AND movement.source_id = NEW.reversal_expense_id
        AND movement.reversal_of_id = NEW.cash_transaction_id
        AND movement.amount = NEW.amount
    ) THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: reversal cash movement does not match';
    END IF;
  ELSIF NEW.reversal_cash_transaction_id IS NOT NULL THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: non-cash reversal cannot name cash';
  END IF;
  RETURN NEW;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_pt_commission_payment()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_currency text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'POSTED'
       OR NEW.voided_at IS NOT NULL
       OR NEW.voided_by_membership_id IS NOT NULL
       OR NEW.void_reason <> ''
       OR NEW.reversal_expense_id IS NOT NULL
       OR NEW.reversal_cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: only a posted payout can be inserted';
    END IF;
    SELECT currency INTO parent_currency
    FROM trainers_pt_commission
    WHERE gym_id = NEW.gym_id AND id = NEW.pt_commission_id;
    IF parent_currency IS NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: commission is missing';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM expenses_expense AS expense
      JOIN tenants_expense_category AS category
        ON category.gym_id = expense.gym_id
       AND category.id = expense.category_id
      WHERE expense.gym_id = NEW.gym_id
        AND expense.id = NEW.expense_id
        AND expense.status = 'POSTED'
        AND expense.original_expense_id IS NULL
        AND expense.amount = NEW.amount
        AND expense.payment_method = NEW.payment_method
        AND expense.business_date = NEW.business_date
        AND expense.currency = parent_currency
        AND category.system_key = 'PT_COMMISSION'
    ) THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: expense does not match the payout';
    END IF;
    IF NEW.payment_method = 'CASH' THEN
      IF NOT EXISTS (
        SELECT 1
        FROM cashbook_cash_transaction AS movement
        WHERE movement.gym_id = NEW.gym_id
          AND movement.id = NEW.cash_transaction_id
          AND movement.cash_session_id = NEW.cash_session_id
          AND movement.direction = 'OUT'
          AND movement.source_type = 'EXPENSE'
          AND movement.source_id = NEW.expense_id
          AND movement.amount = NEW.amount
          AND movement.reversal_of_id IS NULL
      ) THEN
        RAISE EXCEPTION 'pt_commission_payment_guard: cash movement does not match the payout';
      END IF;
    ELSIF NEW.cash_session_id IS NOT NULL OR NEW.cash_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: non-cash payout cannot name a cash session';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP <> 'UPDATE'
     OR OLD.status <> 'POSTED'
     OR NEW.status <> 'VOID' THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: payout rows are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.pt_commission_id IS DISTINCT FROM OLD.pt_commission_id
     OR NEW.amount IS DISTINCT FROM OLD.amount
     OR NEW.business_date IS DISTINCT FROM OLD.business_date
     OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
     OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
     OR NEW.policy_note IS DISTINCT FROM OLD.policy_note
     OR NEW.expense_id IS DISTINCT FROM OLD.expense_id
     OR NEW.cash_session_id IS DISTINCT FROM OLD.cash_session_id
     OR NEW.cash_transaction_id IS DISTINCT FROM OLD.cash_transaction_id
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.voided_at IS NULL
     OR NEW.voided_by_membership_id IS NULL
     OR btrim(NEW.void_reason) = ''
     OR NEW.reversal_expense_id IS NULL THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: void evidence is incomplete';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM expenses_expense AS reversal
    WHERE reversal.gym_id = NEW.gym_id
      AND reversal.id = NEW.reversal_expense_id
      AND reversal.original_expense_id = NEW.expense_id
      AND reversal.status = 'POSTED'
      AND reversal.amount = NEW.amount
  ) THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: reversal expense does not match the payout';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM expenses_expense AS original
    WHERE original.gym_id = NEW.gym_id
      AND original.id = NEW.expense_id
      AND original.status = 'VOID'
      AND original.reversal_expense_id = NEW.reversal_expense_id
  ) THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: original expense is not voided';
  END IF;
  IF NEW.payment_method = 'CASH' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM cashbook_cash_transaction AS movement
      WHERE movement.gym_id = NEW.gym_id
        AND movement.id = NEW.reversal_cash_transaction_id
        AND movement.direction = 'IN'
        AND movement.source_type = 'EXPENSE'
        AND movement.source_id = NEW.reversal_expense_id
        AND movement.reversal_of_id = NEW.cash_transaction_id
        AND movement.amount = NEW.amount
    ) THEN
      RAISE EXCEPTION 'pt_commission_payment_guard: reversal cash movement does not match';
    END IF;
  ELSIF NEW.reversal_cash_transaction_id IS NOT NULL THEN
    RAISE EXCEPTION 'pt_commission_payment_guard: non-cash reversal cannot name cash';
  END IF;
  RETURN NEW;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0020_pt_booking_terminal_time"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
