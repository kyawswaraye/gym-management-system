"""Tie a PT payment to its sale without disturbing legacy PT rows.

A new PT charge must name the sale. A legacy PT row with no sale link keeps
the public payment workflow. A linked row can leave the sale only when its
draft is cancelled.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE members_payment
  ADD CONSTRAINT payment_pt_sale_same_gym_fk
  FOREIGN KEY (gym_id, pt_sale_id)
  REFERENCES trainers_pt_sale (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_payment_pt()
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

CREATE TRIGGER payment_pt_guard_trg
BEFORE INSERT OR UPDATE ON members_payment
FOR EACH ROW EXECUTE FUNCTION app_guard_payment_pt();

REVOKE ALL ON FUNCTION app_guard_payment_pt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_payment_pt() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_payment_pt() FROM gym_runtime, gym_migration;
DROP TRIGGER IF EXISTS payment_pt_guard_trg ON members_payment;
DROP FUNCTION IF EXISTS app_guard_payment_pt();
ALTER TABLE members_payment DROP CONSTRAINT IF EXISTS payment_pt_sale_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("members", "0022_pt_sale_payment_link"),
        ("trainers", "0007_pt_sale_payment_link"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
