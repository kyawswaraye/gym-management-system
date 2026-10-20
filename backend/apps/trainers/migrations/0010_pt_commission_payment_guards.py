"""Guard commission payout children and widen parent payout updates.

Parent rows may change only paid amount, outstanding amount, status, and paid
time. A child is inserted posted and may move to void only with reversal
evidence. Runtime may not delete either table.
"""

from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_commission_fk
  FOREIGN KEY (gym_id, pt_commission_id)
  REFERENCES trainers_pt_commission (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_expense_fk
  FOREIGN KEY (gym_id, expense_id)
  REFERENCES expenses_expense (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_cash_session_fk
  FOREIGN KEY (gym_id, cash_session_id)
  REFERENCES cashbook_cash_session (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_cash_txn_fk
  FOREIGN KEY (gym_id, cash_transaction_id)
  REFERENCES cashbook_cash_transaction (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_reversal_expense_fk
  FOREIGN KEY (gym_id, reversal_expense_id)
  REFERENCES expenses_expense (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_reversal_cash_fk
  FOREIGN KEY (gym_id, reversal_cash_transaction_id)
  REFERENCES cashbook_cash_transaction (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_created_by_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_voided_by_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_method_ck
  CHECK (
    payment_method IN ('CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER')
  );
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_reference_ck
  CHECK (
    reference_number = ''
    OR reference_number = regexp_replace(btrim(reference_number), '[[:space:]]+', ' ', 'g')
  );
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_note_ck
  CHECK (
    policy_note = ''
    OR policy_note = regexp_replace(btrim(policy_note), '[[:space:]]+', ' ', 'g')
  );
ALTER TABLE trainers_pt_commission_payment
  ADD CONSTRAINT pt_comm_pay_evidence_ck
  CHECK (
    (
      status = 'POSTED'
      AND voided_at IS NULL
      AND voided_by_membership_id IS NULL
      AND void_reason = ''
      AND reversal_expense_id IS NULL
      AND reversal_cash_transaction_id IS NULL
    )
    OR (
      status = 'VOID'
      AND voided_at IS NOT NULL
      AND voided_by_membership_id IS NOT NULL
      AND void_reason <> ''
      AND void_reason = regexp_replace(btrim(void_reason), '[[:space:]]+', ' ', 'g')
      AND reversal_expense_id IS NOT NULL
      AND (
        (payment_method = 'CASH' AND reversal_cash_transaction_id IS NOT NULL)
        OR (payment_method <> 'CASH' AND reversal_cash_transaction_id IS NULL)
      )
    )
  );

CREATE OR REPLACE FUNCTION app_guard_pt_commission()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'ACCRUED'
       OR NEW.paid_amount <> 0
       OR NEW.paid_at IS NOT NULL THEN
      RAISE EXCEPTION 'pt_commission_guard: only an accrued commission can be inserted';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM trainers_pt_sale AS sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.pt_sale_id
        AND sale.status = 'POSTED'
        AND sale.trainer_staff_profile_id = NEW.trainer_staff_profile_id
        AND sale.gross_fee = NEW.gross_fee_snapshot
        AND sale.commission_percent_snapshot = NEW.commission_percent_snapshot
        AND sale.currency = NEW.currency
    ) THEN
      RAISE EXCEPTION 'pt_commission_guard: commission does not match the posted sale';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP <> 'UPDATE' THEN
    RAISE EXCEPTION 'pt_commission_guard: commission rows are immutable';
  END IF;
  IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
     OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
     OR NEW.trainer_staff_profile_id IS DISTINCT FROM OLD.trainer_staff_profile_id
     OR NEW.gross_fee_snapshot IS DISTINCT FROM OLD.gross_fee_snapshot
     OR NEW.commission_percent_snapshot IS DISTINCT FROM OLD.commission_percent_snapshot
     OR NEW.commission_amount IS DISTINCT FROM OLD.commission_amount
     OR NEW.gym_share IS DISTINCT FROM OLD.gym_share
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'pt_commission_guard: payout may change only payment totals';
  END IF;
  IF NEW.status NOT IN ('ACCRUED', 'PARTIALLY_PAID', 'PAID') THEN
    RAISE EXCEPTION 'pt_commission_guard: status is not payable';
  END IF;
  IF NEW.paid_amount IS DISTINCT FROM COALESCE((
    SELECT sum(amount)
    FROM trainers_pt_commission_payment
    WHERE gym_id = NEW.gym_id
      AND pt_commission_id = NEW.id
      AND status = 'POSTED'
  ), 0) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid amount must match posted payouts';
  END IF;
  IF NEW.outstanding_amount IS DISTINCT FROM NEW.commission_amount - NEW.paid_amount THEN
    RAISE EXCEPTION 'pt_commission_guard: outstanding amount is wrong';
  END IF;
  IF NEW.status = 'ACCRUED' AND (NEW.paid_amount <> 0 OR NEW.paid_at IS NOT NULL) THEN
    RAISE EXCEPTION 'pt_commission_guard: accrued commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PARTIALLY_PAID' AND (
    NEW.paid_amount <= 0
    OR NEW.paid_amount >= NEW.commission_amount
    OR NEW.paid_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: partial commission evidence is incomplete';
  END IF;
  IF NEW.status = 'PAID' AND (
    NEW.paid_amount <> NEW.commission_amount
    OR NEW.paid_amount <= 0
    OR NEW.paid_at IS NULL
  ) THEN
    RAISE EXCEPTION 'pt_commission_guard: paid commission evidence is incomplete';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_pt_commission_payment()
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

CREATE FUNCTION app_guard_pt_commission_payment_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'pt_commission_payment_guard: payouts cannot be deleted';
END;
$guard$;

CREATE TRIGGER pt_commission_payment_guard_trg
BEFORE INSERT OR UPDATE ON trainers_pt_commission_payment
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_commission_payment();
CREATE TRIGGER pt_commission_payment_delete_trg
BEFORE DELETE ON trainers_pt_commission_payment
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_commission_payment_delete();

ALTER TABLE trainers_pt_commission_payment ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_pt_commission_payment FORCE ROW LEVEL SECURITY;
CREATE POLICY trainers_pt_commission_payment_select ON trainers_pt_commission_payment
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_payment_insert ON trainers_pt_commission_payment
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_payment_update ON trainers_pt_commission_payment
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY trainers_pt_commission_payment_delete ON trainers_pt_commission_payment
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_pt_commission FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_pt_commission TO gym_runtime;
REVOKE ALL ON TABLE trainers_pt_commission_payment FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_pt_commission_payment TO gym_runtime;
REVOKE ALL ON FUNCTION app_guard_pt_commission() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_commission_payment() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_commission_payment_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission_payment() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_commission_payment_delete() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_guard_pt_commission_payment_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_commission_payment() FROM gym_runtime, gym_migration;
DROP TRIGGER IF EXISTS pt_commission_payment_delete_trg ON trainers_pt_commission_payment;
DROP TRIGGER IF EXISTS pt_commission_payment_guard_trg ON trainers_pt_commission_payment;
DROP FUNCTION IF EXISTS app_guard_pt_commission_payment_delete();
DROP FUNCTION IF EXISTS app_guard_pt_commission_payment();
DROP POLICY IF EXISTS trainers_pt_commission_payment_delete ON trainers_pt_commission_payment;
DROP POLICY IF EXISTS trainers_pt_commission_payment_update ON trainers_pt_commission_payment;
DROP POLICY IF EXISTS trainers_pt_commission_payment_insert ON trainers_pt_commission_payment;
DROP POLICY IF EXISTS trainers_pt_commission_payment_select ON trainers_pt_commission_payment;
ALTER TABLE trainers_pt_commission_payment DISABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE trainers_pt_commission_payment FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE trainers_pt_commission TO gym_runtime;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0009_pt_commission_payment"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
