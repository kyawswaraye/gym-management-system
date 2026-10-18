from django.db import migrations, models

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment_retail()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.retail_sale_id IS DISTINCT FROM OLD.retail_sale_id THEN
    RAISE EXCEPTION 'payment_guard: retail sale link is immutable';
  END IF;
  IF NEW.purpose = 'PRODUCT' THEN
    IF NEW.retail_sale_id IS NULL OR NEW.membership_id IS NOT NULL THEN
      RAISE EXCEPTION 'payment_guard: product purpose requires the retail sale';
    END IF;
    IF NEW.entry_type = 'CHARGE' THEN
      IF NOT EXISTS (
        SELECT 1
        FROM public.sales_retail_sale sale
        WHERE sale.gym_id = NEW.gym_id
          AND sale.id = NEW.retail_sale_id
          AND sale.status IN ('PENDING_APPROVAL', 'POSTED')
          AND sale.member_id IS NOT DISTINCT FROM NEW.member_id
          AND sale.currency = NEW.currency
          AND sale.payment_method = NEW.method
          AND sale.business_date = NEW.business_date
          AND sale.total_amount = NEW.amount
          AND sale.reference_number = NEW.reference_number
      ) THEN
        RAISE EXCEPTION 'payment_guard: product purpose requires the retail sale';
      END IF;
    ELSIF NEW.entry_type = 'REVERSAL' THEN
      IF NOT EXISTS (
        SELECT 1
        FROM public.sales_retail_sale sale
        JOIN public.members_payment original
          ON original.gym_id = sale.gym_id
         AND original.id = NEW.original_payment_id
        WHERE sale.gym_id = NEW.gym_id
          AND sale.id = NEW.retail_sale_id
          AND sale.status IN ('POSTED', 'VOID')
          AND original.retail_sale_id = sale.id
          AND original.entry_type = 'CHARGE'
          AND original.purpose = 'PRODUCT'
          AND sale.member_id IS NOT DISTINCT FROM NEW.member_id
          AND sale.currency = NEW.currency
          AND sale.payment_method = NEW.method
          AND sale.total_amount = NEW.amount
          AND sale.reference_number = NEW.reference_number
      ) THEN
        RAISE EXCEPTION 'payment_guard: product reversal requires the retail sale';
      END IF;
    ELSE
      RAISE EXCEPTION 'payment_guard: product purpose requires the retail sale';
    END IF;
  ELSIF NEW.retail_sale_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: only a product payment links a retail sale';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_payment_retail_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_status text;
  current_purpose text;
  current_sale uuid;
  current_amount numeric(18, 2);
  current_type text;
  current_original uuid;
  current_reversal uuid;
BEGIN
  SELECT status, purpose, retail_sale_id, amount, entry_type, original_payment_id,
         reversal_payment_id
    INTO current_status, current_purpose, current_sale, current_amount, current_type,
         current_original, current_reversal
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF NOT FOUND OR current_sale IS NULL THEN
    RETURN NULL;
  END IF;
  IF current_purpose IS DISTINCT FROM 'PRODUCT' THEN
    RAISE EXCEPTION 'payment_guard: only a product payment links a retail sale';
  END IF;
  IF current_type = 'CHARGE' AND current_status = 'POSTED' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = current_sale
        AND sale.status = 'POSTED'
        AND sale.payment_id = NEW.id
        AND sale.total_amount = current_amount
    ) THEN
      RAISE EXCEPTION 'payment_guard: retail payment is not the posted sale payment';
    END IF;
  ELSIF current_type = 'CHARGE' AND current_status = 'VOID' THEN
    IF current_reversal IS NULL OR NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment reversal
        ON reversal.gym_id = sale.gym_id
       AND reversal.id = current_reversal
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = current_sale
        AND sale.status = 'VOID'
        AND sale.payment_id = NEW.id
        AND reversal.original_payment_id = NEW.id
        AND reversal.entry_type = 'REVERSAL'
        AND reversal.status = 'POSTED'
        AND reversal.retail_sale_id = current_sale
        AND reversal.amount = current_amount
        AND reversal.business_date = sale.void_business_date
    ) THEN
      RAISE EXCEPTION 'payment_guard: a voided product charge requires its retail reversal';
    END IF;
  ELSIF current_type = 'REVERSAL' AND current_status = 'POSTED' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.members_payment original
      JOIN public.sales_retail_sale sale
        ON sale.gym_id = original.gym_id
       AND sale.id = original.retail_sale_id
      WHERE original.gym_id = NEW.gym_id
        AND original.id = current_original
        AND original.status = 'VOID'
        AND original.entry_type = 'CHARGE'
        AND original.reversal_payment_id = NEW.id
        AND sale.status = 'VOID'
        AND sale.payment_id = original.id
        AND sale.id = current_sale
    ) THEN
      RAISE EXCEPTION 'payment_guard: a product reversal requires the voided retail sale';
    END IF;
  ELSE
    RAISE EXCEPTION 'payment_guard: a retail link requires a posted product payment';
  END IF;
  RETURN NULL;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment_retail()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.retail_sale_id IS DISTINCT FROM OLD.retail_sale_id THEN
    RAISE EXCEPTION 'payment_guard: retail sale link is immutable';
  END IF;
  IF NEW.purpose = 'PRODUCT' THEN
    IF NEW.retail_sale_id IS NULL OR NEW.membership_id IS NOT NULL THEN
      RAISE EXCEPTION 'payment_guard: product purpose requires the retail sale';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.retail_sale_id
        AND sale.status IN ('PENDING_APPROVAL', 'POSTED')
        AND sale.member_id IS NOT DISTINCT FROM NEW.member_id
        AND sale.currency = NEW.currency
        AND sale.payment_method = NEW.method
        AND sale.business_date = NEW.business_date
        AND sale.total_amount = NEW.amount
        AND sale.reference_number = NEW.reference_number
    ) THEN
      RAISE EXCEPTION 'payment_guard: product purpose requires the retail sale';
    END IF;
  ELSIF NEW.retail_sale_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: only a product payment links a retail sale';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE OR REPLACE FUNCTION app_payment_retail_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_status text;
  current_purpose text;
  current_sale uuid;
  current_amount numeric(18, 2);
BEGIN
  SELECT status, purpose, retail_sale_id, amount
    INTO current_status, current_purpose, current_sale, current_amount
  FROM public.members_payment
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  IF current_sale IS NULL THEN
    RETURN NULL;
  END IF;
  IF current_purpose IS DISTINCT FROM 'PRODUCT'
     OR current_status IS DISTINCT FROM 'POSTED' THEN
    RAISE EXCEPTION 'payment_guard: a retail link requires a posted product payment';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.sales_retail_sale sale
    WHERE sale.gym_id = NEW.gym_id
      AND sale.id = current_sale
      AND sale.status = 'POSTED'
      AND sale.payment_id = NEW.id
      AND sale.total_amount = current_amount
  ) THEN
    RAISE EXCEPTION 'payment_guard: retail payment is not the posted sale payment';
  END IF;
  RETURN NULL;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0019_payment_receipt_runtime_reversal"),
        ("sales", "0003_retail_sale_void"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="payment",
            name="payment_one_retail_sale_uniq",
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                condition=models.Q(entry_type="CHARGE"),
                fields=("gym", "retail_sale"),
                name="payment_one_retail_sale_uniq",
            ),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
