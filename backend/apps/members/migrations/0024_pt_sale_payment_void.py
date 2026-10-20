"""Let sale void reverse a linked PT charge without a public payment command.

The original charge stays linked to the sale and may become void while the
sale is still posted. The reversal is a PT payment with no sale link, and it
must name that charge. A new PT charge still requires the sale.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment_pt()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale_status text;
  sale_member uuid;
  sale_currency text;
  sale_payment uuid;
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.pt_sale_id IS NOT NULL
     AND NEW.pt_sale_id IS NULL
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'CANCELLED'
     AND OLD.purpose = 'PT' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id THEN
    RAISE EXCEPTION 'payment_guard: PT sale link is immutable';
  END IF;
  IF NEW.pt_sale_id IS NULL THEN
    IF TG_OP = 'INSERT' AND NEW.purpose = 'PT' AND NEW.entry_type = 'REVERSAL' THEN
      IF NOT EXISTS (
        SELECT 1
        FROM public.members_payment AS original
        WHERE original.gym_id = NEW.gym_id
          AND original.id = NEW.original_payment_id
          AND original.purpose = 'PT'
          AND original.entry_type = 'CHARGE'
          AND original.pt_sale_id IS NOT NULL
          AND original.member_id IS NOT DISTINCT FROM NEW.member_id
          AND original.amount = NEW.amount
          AND original.method = NEW.method
      ) THEN
        RAISE EXCEPTION 'payment_guard: a PT reversal requires the sale charge';
      END IF;
      RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' AND NEW.purpose = 'PT' THEN
      RAISE EXCEPTION 'payment_guard: a new PT payment requires the sale';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.purpose IS DISTINCT FROM 'PT'
     OR NEW.entry_type IS DISTINCT FROM 'CHARGE'
     OR NEW.membership_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment must be a PT charge';
  END IF;
  SELECT status, member_id, currency, payment_id
    INTO sale_status, sale_member, sale_currency, sale_payment
  FROM public.trainers_pt_sale
  WHERE gym_id = NEW.gym_id AND id = NEW.pt_sale_id;
  IF NOT FOUND
     OR sale_member IS DISTINCT FROM NEW.member_id
     OR sale_currency IS DISTINCT FROM NEW.currency THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  IF sale_payment IS NOT NULL AND sale_payment IS DISTINCT FROM NEW.id THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  IF NEW.status = 'DRAFT' AND sale_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'PENDING_APPROVAL' AND sale_status IS DISTINCT FROM 'PENDING_APPROVAL' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'POSTED' AND sale_status IS DISTINCT FROM 'POSTED' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'REJECTED' AND sale_status IS DISTINCT FROM 'REJECTED' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'VOID' AND sale_status NOT IN ('POSTED', 'VOID') THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'CANCELLED' AND NEW.pt_sale_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: a cancelled PT payment is unlinked';
  ELSIF NEW.status NOT IN ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'VOID') THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  RETURN NEW;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_payment_pt()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale_status text;
  sale_member uuid;
  sale_currency text;
  sale_payment uuid;
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.pt_sale_id IS NOT NULL
     AND NEW.pt_sale_id IS NULL
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'CANCELLED'
     AND OLD.purpose = 'PT' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id THEN
    RAISE EXCEPTION 'payment_guard: PT sale link is immutable';
  END IF;
  IF NEW.pt_sale_id IS NULL THEN
    IF TG_OP = 'INSERT' AND NEW.purpose = 'PT' THEN
      RAISE EXCEPTION 'payment_guard: a new PT payment requires the sale';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.purpose IS DISTINCT FROM 'PT'
     OR NEW.entry_type IS DISTINCT FROM 'CHARGE'
     OR NEW.membership_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment must be a PT charge';
  END IF;
  SELECT status, member_id, currency, payment_id
    INTO sale_status, sale_member, sale_currency, sale_payment
  FROM public.trainers_pt_sale
  WHERE gym_id = NEW.gym_id AND id = NEW.pt_sale_id;
  IF NOT FOUND
     OR sale_member IS DISTINCT FROM NEW.member_id
     OR sale_currency IS DISTINCT FROM NEW.currency THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  IF sale_payment IS NOT NULL AND sale_payment IS DISTINCT FROM NEW.id THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  IF NEW.status = 'DRAFT' AND sale_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'PENDING_APPROVAL' AND sale_status IS DISTINCT FROM 'PENDING_APPROVAL' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'POSTED' AND sale_status IS DISTINCT FROM 'POSTED' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'REJECTED' AND sale_status IS DISTINCT FROM 'REJECTED' THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  ELSIF NEW.status = 'CANCELLED' AND NEW.pt_sale_id IS NOT NULL THEN
    RAISE EXCEPTION 'payment_guard: a cancelled PT payment is unlinked';
  ELSIF NEW.status NOT IN ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED') THEN
    RAISE EXCEPTION 'payment_guard: PT sale payment does not match the sale';
  END IF;
  RETURN NEW;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0023_pt_payment_guard"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
