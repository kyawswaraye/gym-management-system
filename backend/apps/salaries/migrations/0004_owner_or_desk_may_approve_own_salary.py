"""Let the owner, or a combined manager and receptionist, post their own salary.

A manager who does not also hold the protected receptionist role still cannot
approve a salary period they created or submitted. A receptionist alone cannot
approve.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_salary_period()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
  owner_closes_own boolean;
BEGIN
  IF NEW.period_end < NEW.period_start OR btrim(NEW.description) = '' THEN
    RAISE EXCEPTION 'salary_guard: period dates or description are invalid';
  END IF;
  IF NEW.status NOT IN (
    'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
  ) THEN
    RAISE EXCEPTION 'salary_guard: salary classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'salary_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'salary_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'salary_guard: terminal salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'salary_guard: posted salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.period_start IS DISTINCT FROM OLD.period_start
    OR NEW.period_end IS DISTINCT FROM OLD.period_end
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.description IS DISTINCT FROM OLD.description
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'salary_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'salary_guard: status transition is not allowed';
    END IF;
  END IF;
  owner_closes_own := false;
  IF NEW.approved_by_membership_id IS NOT NULL THEN
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
  ) THEN
    RAISE EXCEPTION 'salary_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL
    OR NEW.voided_by_membership_id IS NULL
    OR NEW.void_business_date IS NULL
    OR NEW.void_business_date < NEW.business_date
    OR btrim(NEW.reason) = ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  IF NEW.status IS DISTINCT FROM 'VOID' AND (
    NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
    OR NEW.void_business_date IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_salary_period()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  actor_is_super boolean;
BEGIN
  IF NEW.period_end < NEW.period_start OR btrim(NEW.description) = '' THEN
    RAISE EXCEPTION 'salary_guard: period dates or description are invalid';
  END IF;
  IF NEW.status NOT IN (
    'DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REJECTED', 'CANCELLED', 'VOID'
  ) THEN
    RAISE EXCEPTION 'salary_guard: salary classification is invalid';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.status IS DISTINCT FROM 'DRAFT' THEN
    SELECT rolsuper INTO actor_is_super
    FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF COALESCE(actor_is_super, false) IS NOT TRUE THEN
      RAISE EXCEPTION 'salary_guard: runtime inserts must start as drafts';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'salary_guard: identity columns are immutable';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.created_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.created_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
    IF NEW.submitted_by_membership_id IS NULL THEN
      IF NEW.submitted_with_reception_scope THEN
        RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
      END IF;
    ELSIF NEW.submitted_with_reception_scope IS DISTINCT FROM
          public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.created_with_reception_scope IS DISTINCT FROM OLD.created_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.status = 'DRAFT'
     AND NEW.status = 'PENDING_APPROVAL' THEN
    IF NEW.submitted_with_reception_scope IS DISTINCT FROM
       public.app_has_protected_reception(NEW.gym_id, NEW.submitted_by_membership_id) THEN
      RAISE EXCEPTION 'salary_guard: reception provenance does not match the role';
    END IF;
  ELSIF TG_OP = 'UPDATE'
     AND NEW.submitted_with_reception_scope IS DISTINCT FROM OLD.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: reception provenance is immutable';
  END IF;
  IF NEW.status = 'DRAFT' AND NEW.submitted_with_reception_scope THEN
    RAISE EXCEPTION 'salary_guard: draft has no submission provenance';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IN ('REJECTED', 'CANCELLED', 'VOID') THEN
    RAISE EXCEPTION 'salary_guard: terminal salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'POSTED' AND NEW.status IS DISTINCT FROM 'VOID' THEN
    RAISE EXCEPTION 'salary_guard: posted salary period is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.period_start IS DISTINCT FROM OLD.period_start
    OR NEW.period_end IS DISTINCT FROM OLD.period_end
    OR NEW.business_date IS DISTINCT FROM OLD.business_date
    OR NEW.description IS DISTINCT FROM OLD.description
  ) AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    RAISE EXCEPTION 'salary_guard: business fields are editable only on a draft';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status IS DISTINCT FROM NEW.status THEN
    IF NOT (
      (OLD.status = 'DRAFT' AND NEW.status = 'PENDING_APPROVAL')
      OR (OLD.status = 'DRAFT' AND NEW.status = 'CANCELLED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'POSTED')
      OR (OLD.status = 'PENDING_APPROVAL' AND NEW.status = 'REJECTED')
      OR (OLD.status = 'POSTED' AND NEW.status = 'VOID')
    ) THEN
      RAISE EXCEPTION 'salary_guard: status transition is not allowed';
    END IF;
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.approved_by_membership_id IS DISTINCT FROM NEW.posted_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.created_by_membership_id
    OR NEW.approved_by_membership_id IS NOT DISTINCT FROM NEW.submitted_by_membership_id
  ) THEN
    RAISE EXCEPTION 'salary_guard: approver must post and differ from creator and submitter';
  END IF;
  IF NEW.status = 'DRAFT' AND (
    NEW.submitted_at IS NOT NULL OR NEW.submitted_by_membership_id IS NOT NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.rejected_by_membership_id IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.cancelled_by_membership_id IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.posted_by_membership_id IS NOT NULL
    OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: draft evidence does not match';
  END IF;
  IF NEW.status = 'PENDING_APPROVAL' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NOT NULL OR NEW.approved_by_membership_id IS NOT NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
    OR btrim(NEW.reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: submission evidence does not match';
  END IF;
  IF NEW.status = 'POSTED' AND (
    NEW.submitted_at IS NULL OR NEW.submitted_by_membership_id IS NULL
    OR NEW.approved_at IS NULL OR NEW.approved_by_membership_id IS NULL
    OR NEW.posted_at IS NULL OR NEW.posted_by_membership_id IS NULL
    OR NEW.rejected_at IS NOT NULL OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
    OR btrim(NEW.rejection_reason) <> ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: posting evidence does not match';
  END IF;
  IF NEW.status = 'REJECTED' AND (
    NEW.rejected_at IS NULL OR NEW.rejected_by_membership_id IS NULL
    OR btrim(NEW.rejection_reason) = ''
    OR NEW.approved_at IS NOT NULL OR NEW.posted_at IS NOT NULL
    OR NEW.cancelled_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: rejection evidence does not match';
  END IF;
  IF NEW.status = 'CANCELLED' AND (
    NEW.cancelled_at IS NULL OR NEW.cancelled_by_membership_id IS NULL
    OR btrim(NEW.reason) = ''
    OR NEW.submitted_at IS NOT NULL OR NEW.approved_at IS NOT NULL
    OR NEW.posted_at IS NOT NULL OR NEW.rejected_at IS NOT NULL OR NEW.voided_at IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: cancellation evidence does not match';
  END IF;
  IF NEW.status = 'VOID' AND (
    NEW.voided_at IS NULL
    OR NEW.voided_by_membership_id IS NULL
    OR NEW.void_business_date IS NULL
    OR NEW.void_business_date < NEW.business_date
    OR btrim(NEW.reason) = ''
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  IF NEW.status IS DISTINCT FROM 'VOID' AND (
    NEW.voided_at IS NOT NULL
    OR NEW.voided_by_membership_id IS NOT NULL
    OR NEW.void_business_date IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'salary_guard: void evidence does not match';
  END IF;
  RETURN NEW;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("salaries", "0003_salary_period_void"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
