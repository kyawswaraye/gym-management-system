from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_expense_reversal_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_row public.expenses_expense%ROWTYPE;
  linked public.expenses_expense%ROWTYPE;
BEGIN
  SELECT * INTO current_row
  FROM public.expenses_expense
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF current_row.status = 'VOID' THEN
    IF current_row.reversal_expense_id IS NULL
       OR current_row.voided_at IS NULL
       OR current_row.voided_by_membership_id IS NULL
       OR btrim(current_row.reason) = '' THEN
      RAISE EXCEPTION 'expense_reversal: void requires a reversal';
    END IF;
    SELECT * INTO linked
    FROM public.expenses_expense
    WHERE gym_id = current_row.gym_id AND id = current_row.reversal_expense_id;
    IF NOT FOUND
       OR linked.original_expense_id IS DISTINCT FROM current_row.id
       OR linked.status IS DISTINCT FROM 'POSTED'
       OR linked.amount IS DISTINCT FROM current_row.amount
       OR linked.currency IS DISTINCT FROM current_row.currency
       OR linked.category_id IS DISTINCT FROM current_row.category_id
       OR linked.payment_method IS DISTINCT FROM current_row.payment_method THEN
      RAISE EXCEPTION 'expense_reversal: reversal does not match the original';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.tenants_expense_category category
      WHERE category.gym_id = current_row.gym_id
        AND category.id = current_row.category_id
        AND category.system_key = 'STAFF_SALARY'
    ) THEN
      IF NOT EXISTS (
        SELECT 1
        FROM public.salaries_staff_salary_line line
        JOIN public.salaries_salary_period period
          ON period.gym_id = line.gym_id
         AND period.id = line.salary_period_id
        WHERE line.gym_id = current_row.gym_id
          AND line.expense_id = current_row.id
          AND period.status = 'VOID'
          AND period.void_business_date = linked.business_date
      ) THEN
        RAISE EXCEPTION 'expense_reversal: salary void requires the voided period';
      END IF;
    END IF;
  END IF;
  IF current_row.original_expense_id IS NOT NULL AND current_row.status = 'POSTED' THEN
    SELECT * INTO linked
    FROM public.expenses_expense
    WHERE gym_id = current_row.gym_id AND id = current_row.original_expense_id;
    IF NOT FOUND
       OR linked.status IS DISTINCT FROM 'VOID'
       OR linked.reversal_expense_id IS DISTINCT FROM current_row.id
       OR linked.amount IS DISTINCT FROM current_row.amount
       OR linked.currency IS DISTINCT FROM current_row.currency
       OR linked.category_id IS DISTINCT FROM current_row.category_id
       OR linked.payment_method IS DISTINCT FROM current_row.payment_method THEN
      RAISE EXCEPTION 'expense_reversal: posted reversal requires the voided original link';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_expense_reversal_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_row public.expenses_expense%ROWTYPE;
  linked public.expenses_expense%ROWTYPE;
BEGIN
  SELECT * INTO current_row
  FROM public.expenses_expense
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF current_row.status = 'VOID' THEN
    IF current_row.reversal_expense_id IS NULL
       OR current_row.voided_at IS NULL
       OR current_row.voided_by_membership_id IS NULL
       OR btrim(current_row.reason) = '' THEN
      RAISE EXCEPTION 'expense_reversal: void requires a reversal';
    END IF;
    SELECT * INTO linked
    FROM public.expenses_expense
    WHERE gym_id = current_row.gym_id AND id = current_row.reversal_expense_id;
    IF NOT FOUND
       OR linked.original_expense_id IS DISTINCT FROM current_row.id
       OR linked.status IS DISTINCT FROM 'POSTED'
       OR linked.amount IS DISTINCT FROM current_row.amount
       OR linked.currency IS DISTINCT FROM current_row.currency
       OR linked.category_id IS DISTINCT FROM current_row.category_id
       OR linked.payment_method IS DISTINCT FROM current_row.payment_method THEN
      RAISE EXCEPTION 'expense_reversal: reversal does not match the original';
    END IF;
  END IF;
  IF current_row.original_expense_id IS NOT NULL AND current_row.status = 'POSTED' THEN
    SELECT * INTO linked
    FROM public.expenses_expense
    WHERE gym_id = current_row.gym_id AND id = current_row.original_expense_id;
    IF NOT FOUND
       OR linked.status IS DISTINCT FROM 'VOID'
       OR linked.reversal_expense_id IS DISTINCT FROM current_row.id
       OR linked.amount IS DISTINCT FROM current_row.amount
       OR linked.currency IS DISTINCT FROM current_row.currency
       OR linked.category_id IS DISTINCT FROM current_row.category_id
       OR linked.payment_method IS DISTINCT FROM current_row.payment_method THEN
      RAISE EXCEPTION 'expense_reversal: posted reversal requires the voided original link';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("expenses", "0004_correction_runtime_reversal"),
        ("salaries", "0003_salary_period_void"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
