"""Recheck commission payout totals when the transaction commits.

The parent row guard runs only when that row is updated. A child insert or a
child void can therefore commit while the parent still shows the previous
paid amount. These deferred constraint triggers read the parent and the posted
children at commit and reject the transaction when the totals disagree.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE FUNCTION app_check_pt_commission_aggregates()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent trainers_pt_commission%ROWTYPE;
  paid numeric;
  gym uuid;
  commission_id uuid;
BEGIN
  IF TG_TABLE_NAME = 'trainers_pt_commission' THEN
    gym := NEW.gym_id;
    commission_id := NEW.id;
  ELSE
    gym := NEW.gym_id;
    commission_id := NEW.pt_commission_id;
  END IF;
  SELECT * INTO parent
  FROM trainers_pt_commission
  WHERE gym_id = gym AND id = commission_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  SELECT COALESCE(sum(amount), 0) INTO paid
  FROM trainers_pt_commission_payment
  WHERE gym_id = gym
    AND pt_commission_id = commission_id
    AND status = 'POSTED';
  IF parent.paid_amount IS DISTINCT FROM paid
     OR parent.outstanding_amount IS DISTINCT FROM (parent.commission_amount - parent.paid_amount)
     OR parent.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID')
     OR (
       parent.status = 'ACCRUED'
       AND (parent.paid_amount <> 0 OR parent.paid_at IS NOT NULL)
     )
     OR (
       parent.status = 'PARTIALLY_PAID'
       AND (
         parent.paid_amount <= 0
         OR parent.paid_amount >= parent.commission_amount
         OR parent.paid_at IS NOT NULL
       )
     )
     OR (
       parent.status = 'PAID'
       AND (
         parent.paid_amount <> parent.commission_amount
         OR parent.paid_amount <= 0
         OR parent.paid_at IS NULL
       )
     )
  THEN
    RAISE EXCEPTION 'pt_commission_guard: payout aggregates do not match posted children';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE CONSTRAINT TRIGGER pt_commission_payment_aggregate_trg
AFTER INSERT OR UPDATE OF status ON trainers_pt_commission_payment
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION app_check_pt_commission_aggregates();

CREATE CONSTRAINT TRIGGER pt_commission_aggregate_trg
AFTER UPDATE OF paid_amount, outstanding_amount, status, paid_at
ON trainers_pt_commission
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION app_check_pt_commission_aggregates();

REVOKE ALL ON FUNCTION app_check_pt_commission_aggregates() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_check_pt_commission_aggregates() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS pt_commission_aggregate_trg ON trainers_pt_commission;
DROP TRIGGER IF EXISTS pt_commission_payment_aggregate_trg ON trainers_pt_commission_payment;
REVOKE ALL ON FUNCTION app_check_pt_commission_aggregates() FROM gym_runtime, gym_migration;
DROP FUNCTION IF EXISTS app_check_pt_commission_aggregates();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0010_pt_commission_payment_guards"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
