"""Let the owner, or a combined manager and receptionist, post their own retail sale.

A receptionist alone still cannot approve. A manager without receptionist still
cannot approve a sale they created or submitted.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_retail_approver_ok(target_gym uuid, target_membership uuid)
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

CREATE OR REPLACE FUNCTION app_guard_retail_sale()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
  owner_closes_own boolean;
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
  owner_closes_own := false;
  IF NEW.status = 'POSTED' AND NEW.approved_by_membership_id IS NOT NULL THEN
    SELECT (
      EXISTS (
        SELECT 1
        FROM public.tenants_gym_membership_role assignment
        JOIN public.tenants_role role
          ON role.id = assignment.role_id
         AND role.gym_id = assignment.gym_id
        JOIN public.tenants_system_role_template template
          ON template.id = role.source_template_id
        WHERE assignment.gym_id = NEW.gym_id
          AND assignment.gym_membership_id = NEW.approved_by_membership_id
          AND role.is_active
          AND role.key = 'OWNER'
          AND template.key = 'OWNER'
      )
      OR (
        EXISTS (
          SELECT 1
          FROM public.tenants_gym_membership_role assignment
          JOIN public.tenants_role role
            ON role.id = assignment.role_id
           AND role.gym_id = assignment.gym_id
          JOIN public.tenants_system_role_template template
            ON template.id = role.source_template_id
          WHERE assignment.gym_id = NEW.gym_id
            AND assignment.gym_membership_id = NEW.approved_by_membership_id
            AND role.is_active
            AND role.key = 'MANAGER'
            AND template.key = 'MANAGER'
        )
        AND EXISTS (
          SELECT 1
          FROM public.tenants_gym_membership_role assignment
          JOIN public.tenants_role role
            ON role.id = assignment.role_id
           AND role.gym_id = assignment.gym_id
          JOIN public.tenants_system_role_template template
            ON template.id = role.source_template_id
          WHERE assignment.gym_id = NEW.gym_id
            AND assignment.gym_membership_id = NEW.approved_by_membership_id
            AND role.is_active
            AND role.key = 'RECEPTIONIST'
            AND template.key = 'RECEPTIONIST'
        )
      )
    ) INTO owner_closes_own;
  ELSIF NEW.status = 'REJECTED' AND NEW.rejected_by_membership_id IS NOT NULL THEN
    SELECT (
      EXISTS (
        SELECT 1
        FROM public.tenants_gym_membership_role assignment
        JOIN public.tenants_role role
          ON role.id = assignment.role_id
         AND role.gym_id = assignment.gym_id
        JOIN public.tenants_system_role_template template
          ON template.id = role.source_template_id
        WHERE assignment.gym_id = NEW.gym_id
          AND assignment.gym_membership_id = NEW.rejected_by_membership_id
          AND role.is_active
          AND role.key = 'OWNER'
          AND template.key = 'OWNER'
      )
      OR (
        EXISTS (
          SELECT 1
          FROM public.tenants_gym_membership_role assignment
          JOIN public.tenants_role role
            ON role.id = assignment.role_id
           AND role.gym_id = assignment.gym_id
          JOIN public.tenants_system_role_template template
            ON template.id = role.source_template_id
          WHERE assignment.gym_id = NEW.gym_id
            AND assignment.gym_membership_id = NEW.rejected_by_membership_id
            AND role.is_active
            AND role.key = 'MANAGER'
            AND template.key = 'MANAGER'
        )
        AND EXISTS (
          SELECT 1
          FROM public.tenants_gym_membership_role assignment
          JOIN public.tenants_role role
            ON role.id = assignment.role_id
           AND role.gym_id = assignment.gym_id
          JOIN public.tenants_system_role_template template
            ON template.id = role.source_template_id
          WHERE assignment.gym_id = NEW.gym_id
            AND assignment.gym_membership_id = NEW.rejected_by_membership_id
            AND role.is_active
            AND role.key = 'RECEPTIONIST'
            AND template.key = 'RECEPTIONIST'
        )
      )
    ) INTO owner_closes_own;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR (
      NOT owner_closes_own
      AND (
        NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
        OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
      )
    )
    OR NOT public.app_retail_approver_ok(NEW.gym_id, NEW.approved_by_membership_id)
  ) THEN
    RAISE EXCEPTION 'retail_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    (
      NOT owner_closes_own
      AND (
        NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
        OR NEW.rejected_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
      )
    )
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
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' AND EXISTS (
    SELECT 1
    FROM public.sales_retail_return
    WHERE gym_id = NEW.gym_id AND sale_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'retail_guard: a returned sale cannot be voided';
  END IF;
  RETURN NEW;
END;
$guard$;

"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_retail_approver_ok(target_gym uuid, target_membership uuid)
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
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status = 'VOID' AND EXISTS (
    SELECT 1
    FROM public.sales_retail_return
    WHERE gym_id = NEW.gym_id AND sale_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'retail_guard: a returned sale cannot be voided';
  END IF;
  RETURN NEW;
END;
$guard$;

"""


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0004_retail_return_foundation"),
    ]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
