from django.db import migrations, models

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_retail_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
BEGIN
  IF NEW.currency !~ '^[A-Z]{3}$' OR NEW.total_amount <= 0 THEN
    RAISE EXCEPTION 'retail_guard: amount and currency are invalid';
  END IF;
  IF NEW.status NOT IN (
    'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
  ) OR NEW.payment_method NOT IN (
    'CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER'
  ) THEN
    RAISE EXCEPTION 'retail_guard: sale classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'retail_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'retail_guard: currency must match the gym';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.currency IS DISTINCT FROM OLD.currency
  ) THEN
    RAISE EXCEPTION 'retail_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'retail_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'retail_guard: terminal retail sale is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'retail_guard: posted retail sale is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
    OR NEW.subtotal IS DISTINCT FROM OLD.subtotal
    OR NEW.discount_total IS DISTINCT FROM OLD.discount_total
    OR NEW.total_amount IS DISTINCT FROM OLD.total_amount
    OR NEW.cogs_total IS DISTINCT FROM OLD.cogs_total
    OR NEW.gross_profit_total IS DISTINCT FROM OLD.gross_profit_total
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'retail_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.payment_id IS DISTINCT FROM OLD.payment_id
    OR NEW.receipt_id IS DISTINCT FROM OLD.receipt_id
  ) AND NOT (
    OLD.status = 'PENDING_APPROVAL'
    AND NEW.status = 'POSTED'
    AND OLD.payment_id IS NULL
    AND OLD.receipt_id IS NULL
    AND NEW.payment_id IS NOT NULL
    AND NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: payment links are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'retail_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
    OR NOT public.app_retail_approver_ok(NEW.gym_id, NEW.approved_by_membership_id)
  ) THEN
    RAISE EXCEPTION 'retail_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
    OR NOT public.app_retail_approver_ok(NEW.gym_id, NEW.rejected_by_membership_id)
  ) THEN
    RAISE EXCEPTION 'retail_guard: rejector must differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL OR NEW.voided_by_membership_id IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.payment_id IS NULL OR NEW.receipt_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL OR NEW.voided_by_membership_id IS NULL
    OR NEW.void_business_date IS NULL
    OR NEW.void_business_date < NEW.business_date
    OR btrim(NEW.void_reason) = ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: void evidence does not match';
  END IF;
  IF NEW.status IS DISTINCT FROM 'VOID' AND NEW.void_business_date IS NOT NULL THEN
    RAISE EXCEPTION 'retail_guard: void evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;


CREATE OR REPLACE FUNCTION app_retail_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_status text;
BEGIN
  SELECT status INTO current_status
  FROM public.sales_retail_sale
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  PERFORM public.app_retail_totals_match(NEW.gym_id, NEW.id);
  IF current_status = 'POSTED' THEN
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      WHERE line.gym_id = NEW.gym_id
        AND line.sale_id = NEW.id
        AND (
          SELECT count(*)
          FROM public.inventory_stock_movement movement
          WHERE movement.gym_id = line.gym_id
            AND movement.retail_sale_line_id = line.id
            AND movement.movement_type = 'SALE'
            AND movement.product_id = line.product_id
            AND movement.quantity = -line.quantity
            AND movement.unit_cost_snapshot = line.unit_cost_snapshot
            AND movement.business_date = NEW.business_date
            AND movement.reverses_movement_id IS NULL
        ) <> 1
    ) THEN
      RAISE EXCEPTION 'retail_posting: each posted line requires one sale movement';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment payment
        ON payment.gym_id = sale.gym_id
       AND payment.id = sale.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND payment.retail_sale_id = sale.id
        AND payment.purpose = 'PRODUCT'
        AND payment.entry_type = 'CHARGE'
        AND payment.status = 'POSTED'
        AND payment.amount = sale.total_amount
        AND payment.currency = sale.currency
        AND payment.method = sale.payment_method
        AND payment.business_date = sale.business_date
        AND payment.member_id IS NOT DISTINCT FROM sale.member_id
        AND payment.membership_id IS NULL
        AND payment.reference_number = sale.reference_number
        AND payment.created_by_membership_id = sale.created_by_membership_id
        AND payment.submitted_by_membership_id = sale.submitted_by_membership_id
        AND payment.approved_by_membership_id = sale.approved_by_membership_id
        AND payment.posted_by_membership_id = sale.posted_by_membership_id
    ) OR (
      SELECT count(*) FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND retail_sale_id = NEW.id
    ) <> 1 THEN
      RAISE EXCEPTION 'retail_posting: product payment does not match the sale';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_receipt receipt
        ON receipt.gym_id = sale.gym_id
       AND receipt.id = sale.receipt_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.payment_id = sale.payment_id
        AND receipt.status = 'ISSUED'
        AND receipt.amount = sale.total_amount
        AND receipt.currency = sale.currency
        AND receipt.method = sale.payment_method
        AND receipt.business_date = sale.business_date
    ) OR (
      SELECT count(*)
      FROM public.members_receipt receipt
      JOIN public.sales_retail_sale sale
        ON sale.gym_id = receipt.gym_id
       AND sale.payment_id = receipt.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.status = 'ISSUED'
    ) <> 1 THEN
      RAISE EXCEPTION 'retail_posting: receipt does not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method = 'CASH'
        AND NOT EXISTS (
          SELECT 1
          FROM public.members_payment payment
          JOIN public.cashbook_cash_transaction txn
            ON txn.gym_id = payment.gym_id
           AND txn.id = payment.cash_transaction_id
          JOIN public.cashbook_cash_session session
            ON session.gym_id = txn.gym_id
           AND session.id = txn.cash_session_id
          WHERE payment.gym_id = sale.gym_id
            AND payment.id = sale.payment_id
            AND session.status = 'OPEN'
            AND session.currency = sale.currency
            AND session.business_date = sale.business_date
            AND txn.source_type = 'PAYMENT'
            AND txn.source_id = payment.id
            AND txn.direction = 'IN'
            AND txn.amount = sale.total_amount
            AND txn.currency = sale.currency
            AND txn.business_date = sale.business_date
            AND (
              NOT sale.created_with_reception_scope
              OR session.opened_by_membership_id = sale.created_by_membership_id
            )
            AND (
              NOT sale.submitted_with_reception_scope
              OR session.opened_by_membership_id = sale.submitted_by_membership_id
            )
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: cash movement does not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment payment
        ON payment.gym_id = sale.gym_id
       AND payment.id = sale.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method <> 'CASH'
        AND (
          payment.cash_session_id IS NOT NULL
          OR payment.cash_transaction_id IS NOT NULL
          OR EXISTS (
            SELECT 1 FROM public.cashbook_cash_transaction
            WHERE gym_id = payment.gym_id
              AND source_type = 'PAYMENT'
              AND source_id = payment.id
          )
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: noncash sale prohibits cash links';
    END IF;
  ELSIF current_status = 'VOID' THEN
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      JOIN public.sales_retail_sale sale
        ON sale.gym_id = line.gym_id
       AND sale.id = line.sale_id
      WHERE line.gym_id = NEW.gym_id
        AND line.sale_id = NEW.id
        AND (
          (
            SELECT count(*)
            FROM public.inventory_stock_movement movement
            WHERE movement.gym_id = line.gym_id
              AND movement.retail_sale_line_id = line.id
              AND movement.movement_type = 'SALE'
              AND movement.product_id = line.product_id
              AND movement.quantity = -line.quantity
              AND movement.unit_cost_snapshot = line.unit_cost_snapshot
              AND movement.business_date = sale.business_date
              AND movement.reverses_movement_id IS NULL
          ) <> 1
          OR (
            SELECT count(*)
            FROM public.inventory_stock_movement movement
            WHERE movement.gym_id = line.gym_id
              AND movement.retail_sale_line_id = line.id
              AND movement.movement_type = 'SALE_RETURN'
              AND movement.product_id = line.product_id
              AND movement.quantity = line.quantity
              AND movement.unit_cost_snapshot = line.unit_cost_snapshot
              AND movement.business_date = sale.void_business_date
              AND movement.reverses_movement_id = (
                SELECT original.id
                FROM public.inventory_stock_movement original
                WHERE original.gym_id = line.gym_id
                  AND original.retail_sale_line_id = line.id
                  AND original.movement_type = 'SALE'
              )
          ) <> 1
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: each voided line requires one sale return';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment charge
        ON charge.gym_id = sale.gym_id
       AND charge.id = sale.payment_id
      JOIN public.members_payment reversal
        ON reversal.gym_id = charge.gym_id
       AND reversal.id = charge.reversal_payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND charge.retail_sale_id = sale.id
        AND charge.purpose = 'PRODUCT'
        AND charge.entry_type = 'CHARGE'
        AND charge.status = 'VOID'
        AND charge.amount = sale.total_amount
        AND charge.currency = sale.currency
        AND charge.method = sale.payment_method
        AND charge.business_date = sale.business_date
        AND charge.member_id IS NOT DISTINCT FROM sale.member_id
        AND reversal.original_payment_id = charge.id
        AND reversal.entry_type = 'REVERSAL'
        AND reversal.status = 'POSTED'
        AND reversal.purpose = 'PRODUCT'
        AND reversal.retail_sale_id = sale.id
        AND reversal.amount = charge.amount
        AND reversal.currency = charge.currency
        AND reversal.method = charge.method
        AND reversal.business_date = sale.void_business_date
        AND reversal.member_id IS NOT DISTINCT FROM sale.member_id
        AND (
          SELECT count(*) FROM public.members_payment
          WHERE gym_id = sale.gym_id AND original_payment_id = charge.id
        ) = 1
    ) THEN
      RAISE EXCEPTION 'retail_posting: product payment reversal does not match the sale';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_receipt receipt
        ON receipt.gym_id = sale.gym_id
       AND receipt.id = sale.receipt_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.payment_id = sale.payment_id
        AND receipt.status = 'VOID'
    ) OR NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment charge
        ON charge.gym_id = sale.gym_id
       AND charge.id = sale.payment_id
      JOIN public.members_payment reversal
        ON reversal.gym_id = charge.gym_id
       AND reversal.id = charge.reversal_payment_id
      JOIN public.members_receipt receipt
        ON receipt.gym_id = reversal.gym_id
       AND receipt.payment_id = reversal.id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.status = 'ISSUED'
        AND receipt.amount = reversal.amount
        AND receipt.currency = reversal.currency
        AND receipt.method = reversal.method
        AND receipt.business_date = reversal.business_date
    ) THEN
      RAISE EXCEPTION 'retail_posting: void receipts do not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method = 'CASH'
        AND NOT EXISTS (
          SELECT 1
          FROM public.members_payment charge
          JOIN public.members_payment reversal
            ON reversal.gym_id = charge.gym_id
           AND reversal.id = charge.reversal_payment_id
          JOIN public.cashbook_cash_transaction txn
            ON txn.gym_id = reversal.gym_id
           AND txn.id = reversal.cash_transaction_id
          JOIN public.cashbook_cash_session session
            ON session.gym_id = txn.gym_id
           AND session.id = txn.cash_session_id
          WHERE charge.gym_id = sale.gym_id
            AND charge.id = sale.payment_id
            AND session.status = 'OPEN'
            AND session.currency = sale.currency
            AND session.business_date = sale.void_business_date
            AND txn.source_type = 'PAYMENT'
            AND txn.source_id = reversal.id
            AND txn.direction = 'OUT'
            AND txn.amount = sale.total_amount
            AND txn.currency = sale.currency
            AND txn.business_date = sale.void_business_date
            AND txn.reversal_of_id = charge.cash_transaction_id
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: cash reversal does not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment charge
        ON charge.gym_id = sale.gym_id
       AND charge.id = sale.payment_id
      JOIN public.members_payment reversal
        ON reversal.gym_id = charge.gym_id
       AND reversal.id = charge.reversal_payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method <> 'CASH'
        AND (
          reversal.cash_session_id IS NOT NULL
          OR reversal.cash_transaction_id IS NOT NULL
          OR EXISTS (
            SELECT 1 FROM public.cashbook_cash_transaction
            WHERE gym_id = reversal.gym_id
              AND source_type = 'PAYMENT'
              AND source_id = reversal.id
          )
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: noncash void prohibits cash links';
    END IF;
  ELSIF current_status IN ('DRAFT', 'PENDING_APPROVAL', 'REJECTED', 'CANCELLED') THEN
    IF EXISTS (
      SELECT 1 FROM public.sales_retail_sale
      WHERE gym_id = NEW.gym_id
        AND id = NEW.id
        AND (payment_id IS NOT NULL OR receipt_id IS NOT NULL)
    ) OR EXISTS (
      SELECT 1 FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND retail_sale_id = NEW.id
    ) OR EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      JOIN public.inventory_stock_movement movement
        ON movement.gym_id = line.gym_id
       AND movement.retail_sale_line_id = line.id
      WHERE line.gym_id = NEW.gym_id AND line.sale_id = NEW.id
    ) THEN
      RAISE EXCEPTION 'retail_posting: unposted sale has financial or stock effects';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;

"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_retail_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
BEGIN
  IF NEW.currency !~ '^[A-Z]{3}$' OR NEW.total_amount <= 0 THEN
    RAISE EXCEPTION 'retail_guard: amount and currency are invalid';
  END IF;
  IF NEW.status NOT IN (
    'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
  ) OR NEW.payment_method NOT IN (
    'CASH', 'BANK_TRANSFER', 'CARD', 'MOBILE_PAYMENT', 'OTHER'
  ) THEN
    RAISE EXCEPTION 'retail_guard: sale classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'retail_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF NEW.currency IS DISTINCT FROM (
    SELECT currency FROM public.tenants_gym WHERE id = NEW.gym_id
  ) THEN
    RAISE EXCEPTION 'retail_guard: currency must match the gym';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.currency IS DISTINCT FROM OLD.currency
  ) THEN
    RAISE EXCEPTION 'retail_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE
       AND current_user IS DISTINCT FROM 'gym_migration' THEN
      RAISE EXCEPTION 'retail_guard: void is reserved to the migration role';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'retail_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'retail_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'retail_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'retail_guard: terminal retail sale is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'retail_guard: posted retail sale is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.member_id IS DISTINCT FROM OLD.member_id
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.payment_method IS DISTINCT FROM OLD.payment_method
    OR NEW.reference_number IS DISTINCT FROM OLD.reference_number
    OR NEW.subtotal IS DISTINCT FROM OLD.subtotal
    OR NEW.discount_total IS DISTINCT FROM OLD.discount_total
    OR NEW.total_amount IS DISTINCT FROM OLD.total_amount
    OR NEW.cogs_total IS DISTINCT FROM OLD.cogs_total
    OR NEW.gross_profit_total IS DISTINCT FROM OLD.gross_profit_total
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'retail_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.payment_id IS DISTINCT FROM OLD.payment_id
    OR NEW.receipt_id IS DISTINCT FROM OLD.receipt_id
  ) AND NOT (
    OLD.status = 'PENDING_APPROVAL'
    AND NEW.status = 'POSTED'
    AND OLD.payment_id IS NULL
    AND OLD.receipt_id IS NULL
    AND NEW.payment_id IS NOT NULL
    AND NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: payment links are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'retail_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
    OR NOT public.app_retail_approver_ok(NEW.gym_id, NEW.approved_by_membership_id)
  ) THEN
    RAISE EXCEPTION 'retail_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
    OR NOT public.app_retail_approver_ok(NEW.gym_id, NEW.rejected_by_membership_id)
  ) THEN
    RAISE EXCEPTION 'retail_guard: rejector must differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL OR NEW.voided_by_membership_id IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.payment_id IS NULL OR NEW.receipt_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.void_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR NEW.payment_id IS NOT NULL OR NEW.receipt_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'retail_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL OR NEW.voided_by_membership_id IS NULL
    OR btrim(NEW.void_reason) = ''
  ) THEN
    RAISE EXCEPTION 'retail_guard: void evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;


CREATE OR REPLACE FUNCTION app_retail_posting_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  current_status text;
BEGIN
  SELECT status INTO current_status
  FROM public.sales_retail_sale
  WHERE gym_id = NEW.gym_id AND id = NEW.id;
  PERFORM public.app_retail_totals_match(NEW.gym_id, NEW.id);
  IF current_status = 'POSTED' THEN
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      WHERE line.gym_id = NEW.gym_id
        AND line.sale_id = NEW.id
        AND (
          SELECT count(*)
          FROM public.inventory_stock_movement movement
          WHERE movement.gym_id = line.gym_id
            AND movement.retail_sale_line_id = line.id
            AND movement.movement_type = 'SALE'
            AND movement.product_id = line.product_id
            AND movement.quantity = -line.quantity
            AND movement.unit_cost_snapshot = line.unit_cost_snapshot
            AND movement.business_date = NEW.business_date
            AND movement.reverses_movement_id IS NULL
        ) <> 1
    ) THEN
      RAISE EXCEPTION 'retail_posting: each posted line requires one sale movement';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment payment
        ON payment.gym_id = sale.gym_id
       AND payment.id = sale.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND payment.retail_sale_id = sale.id
        AND payment.purpose = 'PRODUCT'
        AND payment.entry_type = 'CHARGE'
        AND payment.status = 'POSTED'
        AND payment.amount = sale.total_amount
        AND payment.currency = sale.currency
        AND payment.method = sale.payment_method
        AND payment.business_date = sale.business_date
        AND payment.member_id IS NOT DISTINCT FROM sale.member_id
        AND payment.membership_id IS NULL
        AND payment.reference_number = sale.reference_number
        AND payment.created_by_membership_id = sale.created_by_membership_id
        AND payment.submitted_by_membership_id = sale.submitted_by_membership_id
        AND payment.approved_by_membership_id = sale.approved_by_membership_id
        AND payment.posted_by_membership_id = sale.posted_by_membership_id
    ) OR (
      SELECT count(*) FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND retail_sale_id = NEW.id
    ) <> 1 THEN
      RAISE EXCEPTION 'retail_posting: product payment does not match the sale';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_receipt receipt
        ON receipt.gym_id = sale.gym_id
       AND receipt.id = sale.receipt_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.payment_id = sale.payment_id
        AND receipt.status = 'ISSUED'
        AND receipt.amount = sale.total_amount
        AND receipt.currency = sale.currency
        AND receipt.method = sale.payment_method
        AND receipt.business_date = sale.business_date
    ) OR (
      SELECT count(*)
      FROM public.members_receipt receipt
      JOIN public.sales_retail_sale sale
        ON sale.gym_id = receipt.gym_id
       AND sale.payment_id = receipt.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND receipt.status = 'ISSUED'
    ) <> 1 THEN
      RAISE EXCEPTION 'retail_posting: receipt does not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method = 'CASH'
        AND NOT EXISTS (
          SELECT 1
          FROM public.members_payment payment
          JOIN public.cashbook_cash_transaction txn
            ON txn.gym_id = payment.gym_id
           AND txn.id = payment.cash_transaction_id
          JOIN public.cashbook_cash_session session
            ON session.gym_id = txn.gym_id
           AND session.id = txn.cash_session_id
          WHERE payment.gym_id = sale.gym_id
            AND payment.id = sale.payment_id
            AND session.status = 'OPEN'
            AND session.currency = sale.currency
            AND session.business_date = sale.business_date
            AND txn.source_type = 'PAYMENT'
            AND txn.source_id = payment.id
            AND txn.direction = 'IN'
            AND txn.amount = sale.total_amount
            AND txn.currency = sale.currency
            AND txn.business_date = sale.business_date
            AND (
              NOT sale.created_with_reception_scope
              OR session.opened_by_membership_id = sale.created_by_membership_id
            )
            AND (
              NOT sale.submitted_with_reception_scope
              OR session.opened_by_membership_id = sale.submitted_by_membership_id
            )
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: cash movement does not match the sale';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM public.sales_retail_sale sale
      JOIN public.members_payment payment
        ON payment.gym_id = sale.gym_id
       AND payment.id = sale.payment_id
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.id
        AND sale.payment_method <> 'CASH'
        AND (
          payment.cash_session_id IS NOT NULL
          OR payment.cash_transaction_id IS NOT NULL
          OR EXISTS (
            SELECT 1 FROM public.cashbook_cash_transaction
            WHERE gym_id = payment.gym_id
              AND source_type = 'PAYMENT'
              AND source_id = payment.id
          )
        )
    ) THEN
      RAISE EXCEPTION 'retail_posting: noncash sale prohibits cash links';
    END IF;
  ELSIF current_status IN ('DRAFT', 'PENDING_APPROVAL', 'REJECTED', 'CANCELLED') THEN
    IF EXISTS (
      SELECT 1 FROM public.sales_retail_sale
      WHERE gym_id = NEW.gym_id
        AND id = NEW.id
        AND (payment_id IS NOT NULL OR receipt_id IS NOT NULL)
    ) OR EXISTS (
      SELECT 1 FROM public.members_payment
      WHERE gym_id = NEW.gym_id AND retail_sale_id = NEW.id
    ) OR EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      JOIN public.inventory_stock_movement movement
        ON movement.gym_id = line.gym_id
       AND movement.retail_sale_line_id = line.id
      WHERE line.gym_id = NEW.gym_id AND line.sale_id = NEW.id
    ) THEN
      RAISE EXCEPTION 'retail_posting: unposted sale has financial or stock effects';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;

"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0002_rls_integrity_and_grants"),
    ]

    operations = [
        migrations.AddField(
            model_name="retailsale",
            name="void_business_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
