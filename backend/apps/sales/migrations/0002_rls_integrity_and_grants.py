from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_payment_same_gym_fk
  FOREIGN KEY (gym_id, payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_receipt_same_gym_fk
  FOREIGN KEY (gym_id, receipt_id)
  REFERENCES members_receipt (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_submitted_by_same_gym_fk
  FOREIGN KEY (gym_id, submitted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_approved_by_same_gym_fk
  FOREIGN KEY (gym_id, approved_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_rejected_by_same_gym_fk
  FOREIGN KEY (gym_id, rejected_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_posted_by_same_gym_fk
  FOREIGN KEY (gym_id, posted_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_cancelled_by_same_gym_fk
  FOREIGN KEY (gym_id, cancelled_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale
  ADD CONSTRAINT rsale_voided_by_same_gym_fk
  FOREIGN KEY (gym_id, voided_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE sales_retail_sale_line
  ADD CONSTRAINT rsline_sale_same_gym_fk
  FOREIGN KEY (gym_id, sale_id)
  REFERENCES sales_retail_sale (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale_line
  ADD CONSTRAINT rsline_product_same_gym_fk
  FOREIGN KEY (gym_id, product_id)
  REFERENCES inventory_product (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale_line
  ADD CONSTRAINT rsline_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale_line
  ADD CONSTRAINT rsline_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE sales_retail_sale_line
  ADD CONSTRAINT rsline_formula_ck CHECK (
    discount <= round(quantity * unit_price_snapshot, 2)
    AND line_revenue = round(quantity * unit_price_snapshot, 2) - discount
    AND cost_of_goods_sold = round(quantity * unit_cost_snapshot, 2)
    AND gross_profit = line_revenue - cost_of_goods_sold
  );

CREATE FUNCTION app_retail_approver_ok(target_gym uuid, target_membership uuid)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF target_gym IS NULL OR target_gym IS DISTINCT FROM public.app_current_gym_id() THEN
    RAISE EXCEPTION 'retail_guard: approver check is outside the active gym'
      USING ERRCODE = '42501';
  END IF;
  IF public.app_has_protected_reception(target_gym, target_membership) THEN
    RETURN false;
  END IF;
  RETURN EXISTS (
    SELECT 1
    FROM public.tenants_gym_membership_role link
    JOIN public.tenants_role role
      ON role.gym_id = link.gym_id
     AND role.id = link.role_id
    JOIN public.tenants_system_role_template template
      ON template.id = role.source_template_id
    WHERE link.gym_id = public.app_current_gym_id()
      AND link.gym_membership_id = target_membership
      AND role.is_active
      AND role.key IN ('MANAGER', 'OWNER')
      AND template.key = role.key
  );
END;
$$;

CREATE FUNCTION app_guard_retail_sale()
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

CREATE FUNCTION app_guard_retail_sale_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'retail_guard: retail sales cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_guard_retail_line()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_status text;
BEGIN
  IF NEW.quantity <= 0 OR NEW.discount < 0 THEN
    RAISE EXCEPTION 'retail_line_guard: quantity and discount are invalid';
  END IF;
  IF btrim(NEW.sku_snapshot) = '' OR btrim(NEW.name_snapshot) = '' THEN
    RAISE EXCEPTION 'retail_line_guard: product snapshot is required';
  END IF;
  SELECT status INTO parent_status
  FROM public.sales_retail_sale
  WHERE gym_id = NEW.gym_id AND id = NEW.sale_id;
  IF parent_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'retail_line_guard: lines are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.sale_id IS DISTINCT FROM OLD.sale_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'retail_line_guard: identity columns are immutable';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_retail_line_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  parent_status text;
BEGIN
  SELECT status INTO parent_status
  FROM public.sales_retail_sale
  WHERE gym_id = OLD.gym_id AND id = OLD.sale_id;
  IF parent_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'retail_line_guard: only a draft line can be replaced';
  END IF;
  RETURN OLD;
END;
$guard$;

CREATE FUNCTION app_retail_totals_match(target_gym uuid, target_sale uuid)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  expected_subtotal numeric(18, 2);
  expected_discount numeric(18, 2);
  expected_total numeric(18, 2);
  expected_cogs numeric(18, 2);
  expected_profit numeric(18, 2);
  line_count integer;
  stored_subtotal numeric(18, 2);
  stored_discount numeric(18, 2);
  stored_total numeric(18, 2);
  stored_cogs numeric(18, 2);
  stored_profit numeric(18, 2);
BEGIN
  SELECT
    COALESCE(SUM(round(quantity * unit_price_snapshot, 2)), 0),
    COALESCE(SUM(discount), 0),
    COALESCE(SUM(line_revenue), 0),
    COALESCE(SUM(cost_of_goods_sold), 0),
    COALESCE(SUM(gross_profit), 0),
    COUNT(*)
  INTO
    expected_subtotal, expected_discount, expected_total,
    expected_cogs, expected_profit, line_count
  FROM public.sales_retail_sale_line
  WHERE gym_id = target_gym AND sale_id = target_sale;
  SELECT subtotal, discount_total, total_amount, cogs_total, gross_profit_total
    INTO stored_subtotal, stored_discount, stored_total, stored_cogs, stored_profit
  FROM public.sales_retail_sale
  WHERE gym_id = target_gym AND id = target_sale;
  IF line_count < 1 OR line_count > 100
     OR stored_subtotal IS DISTINCT FROM expected_subtotal
     OR stored_discount IS DISTINCT FROM expected_discount
     OR stored_total IS DISTINCT FROM expected_total
     OR stored_cogs IS DISTINCT FROM expected_cogs
     OR stored_profit IS DISTINCT FROM expected_profit THEN
    RAISE EXCEPTION 'retail_guard: sale totals do not match the lines';
  END IF;
END;
$guard$;

CREATE FUNCTION app_retail_line_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.app_retail_totals_match(OLD.gym_id, OLD.sale_id);
  ELSE
    PERFORM public.app_retail_totals_match(NEW.gym_id, NEW.sale_id);
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE FUNCTION app_retail_posting_commit()
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

CREATE TRIGGER retail_sale_guard_trg
  BEFORE INSERT OR UPDATE ON sales_retail_sale
  FOR EACH ROW EXECUTE FUNCTION app_guard_retail_sale();
CREATE TRIGGER retail_sale_delete_trg
  BEFORE DELETE ON sales_retail_sale
  FOR EACH ROW EXECUTE FUNCTION app_guard_retail_sale_delete();
CREATE TRIGGER retail_line_guard_trg
  BEFORE INSERT OR UPDATE ON sales_retail_sale_line
  FOR EACH ROW EXECUTE FUNCTION app_guard_retail_line();
CREATE TRIGGER retail_line_delete_trg
  BEFORE DELETE ON sales_retail_sale_line
  FOR EACH ROW EXECUTE FUNCTION app_guard_retail_line_delete();
CREATE CONSTRAINT TRIGGER retail_line_commit_trg
  AFTER INSERT OR UPDATE OR DELETE ON sales_retail_sale_line
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_retail_line_commit();
CREATE CONSTRAINT TRIGGER retail_posting_commit_trg
  AFTER INSERT OR UPDATE ON sales_retail_sale
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION app_retail_posting_commit();

ALTER TABLE sales_retail_sale ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_retail_sale FORCE ROW LEVEL SECURITY;
CREATE POLICY sales_retail_sale_select ON sales_retail_sale
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_insert ON sales_retail_sale
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_update ON sales_retail_sale
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_delete ON sales_retail_sale
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE sales_retail_sale_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_retail_sale_line FORCE ROW LEVEL SECURITY;
CREATE POLICY sales_retail_sale_line_select ON sales_retail_sale_line
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_line_insert ON sales_retail_sale_line
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_line_update ON sales_retail_sale_line
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sales_retail_sale_line_delete ON sales_retail_sale_line
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE sales_retail_sale FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE sales_retail_sale TO gym_runtime;
REVOKE ALL ON TABLE sales_retail_sale_line FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE sales_retail_sale_line TO gym_runtime;

REVOKE ALL ON FUNCTION app_retail_approver_ok(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_retail_sale() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_retail_sale_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_retail_line() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_retail_line_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_retail_totals_match(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_retail_line_commit() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_retail_posting_commit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_retail_approver_ok(uuid, uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_retail_sale() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_retail_sale_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_retail_line() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_retail_line_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_retail_totals_match(uuid, uuid) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_retail_line_commit() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_retail_posting_commit() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_retail_posting_commit() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_retail_line_commit() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_retail_totals_match(uuid, uuid) FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_retail_line_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_retail_line() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_retail_sale_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_retail_sale() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_retail_approver_ok(uuid, uuid) FROM gym_runtime, gym_migration;
DROP TRIGGER IF EXISTS retail_posting_commit_trg ON sales_retail_sale;
DROP TRIGGER IF EXISTS retail_line_commit_trg ON sales_retail_sale_line;
DROP TRIGGER IF EXISTS retail_line_delete_trg ON sales_retail_sale_line;
DROP TRIGGER IF EXISTS retail_line_guard_trg ON sales_retail_sale_line;
DROP TRIGGER IF EXISTS retail_sale_delete_trg ON sales_retail_sale;
DROP TRIGGER IF EXISTS retail_sale_guard_trg ON sales_retail_sale;
DROP FUNCTION IF EXISTS app_retail_posting_commit();
DROP FUNCTION IF EXISTS app_retail_line_commit();
DROP FUNCTION IF EXISTS app_retail_totals_match(uuid, uuid);
DROP FUNCTION IF EXISTS app_guard_retail_line_delete();
DROP FUNCTION IF EXISTS app_guard_retail_line();
DROP FUNCTION IF EXISTS app_guard_retail_sale_delete();
DROP FUNCTION IF EXISTS app_guard_retail_sale();
DROP FUNCTION IF EXISTS app_retail_approver_ok(uuid, uuid);
DROP POLICY IF EXISTS sales_retail_sale_line_delete ON sales_retail_sale_line;
DROP POLICY IF EXISTS sales_retail_sale_line_update ON sales_retail_sale_line;
DROP POLICY IF EXISTS sales_retail_sale_line_insert ON sales_retail_sale_line;
DROP POLICY IF EXISTS sales_retail_sale_line_select ON sales_retail_sale_line;
DROP POLICY IF EXISTS sales_retail_sale_delete ON sales_retail_sale;
DROP POLICY IF EXISTS sales_retail_sale_update ON sales_retail_sale;
DROP POLICY IF EXISTS sales_retail_sale_insert ON sales_retail_sale;
DROP POLICY IF EXISTS sales_retail_sale_select ON sales_retail_sale;
ALTER TABLE sales_retail_sale_line DROP CONSTRAINT IF EXISTS rsline_formula_ck;
ALTER TABLE sales_retail_sale_line DROP CONSTRAINT IF EXISTS rsline_updated_by_same_gym_fk;
ALTER TABLE sales_retail_sale_line DROP CONSTRAINT IF EXISTS rsline_created_by_same_gym_fk;
ALTER TABLE sales_retail_sale_line DROP CONSTRAINT IF EXISTS rsline_product_same_gym_fk;
ALTER TABLE sales_retail_sale_line DROP CONSTRAINT IF EXISTS rsline_sale_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_voided_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_cancelled_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_posted_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_rejected_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_approved_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_submitted_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_updated_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_created_by_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_receipt_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_payment_same_gym_fk;
ALTER TABLE sales_retail_sale DROP CONSTRAINT IF EXISTS rsale_member_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0001_retail_sale_foundation"),
        ("members", "0018_retail_sale_foundation"),
        ("inventory", "0003_retail_sale_foundation"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
