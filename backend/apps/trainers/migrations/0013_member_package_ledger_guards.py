"""Guard granted packages and the append-only session ledger.

Sale package columns freeze outside a draft. Package definitions stay PT-only
until ClassType exists. Runtime may update a grant's status only. The ledger
accepts grant and correction inserts and rejects updates and deletes.
"""

from django.db import migrations

FORWARD_SQL = r"""
CREATE FUNCTION app_inclusive_until(start_date date, units integer, unit text)
RETURNS date
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog
AS $until$
DECLARE
  month_index integer;
  dest_year integer;
  dest_month integer;
  dest_day integer;
  last_day integer;
  months integer;
BEGIN
  IF units < 1 THEN
    RAISE EXCEPTION 'member_package_guard: validity must be positive';
  END IF;
  IF unit = 'DAY' THEN
    RETURN start_date + (units - 1);
  ELSIF unit = 'WEEK' THEN
    RETURN start_date + (units * 7 - 1);
  ELSIF unit IN ('MONTH', 'YEAR') THEN
    months := CASE WHEN unit = 'YEAR' THEN units * 12 ELSE units END;
    month_index := EXTRACT(MONTH FROM start_date)::integer - 1 + months;
    dest_year := EXTRACT(YEAR FROM start_date)::integer + month_index / 12;
    dest_month := (month_index % 12) + 1;
    last_day := EXTRACT(
      DAY FROM (make_date(dest_year, dest_month, 1) + INTERVAL '1 month - 1 day')
    )::integer;
    dest_day := LEAST(EXTRACT(DAY FROM start_date)::integer, last_day);
    RETURN make_date(dest_year, dest_month, dest_day) - 1;
  END IF;
  RAISE EXCEPTION 'member_package_guard: validity unit is not a calendar unit';
END;
$until$;

CREATE FUNCTION app_guard_pt_sale_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF (NEW.service_package_id IS NULL) IS DISTINCT FROM (NEW.package_price_snapshot IS NULL) THEN
    RAISE EXCEPTION 'pt_sale_guard: package selection is incomplete';
  END IF;
  IF TG_OP = 'UPDATE' AND (OLD.status <> 'DRAFT' OR NEW.status <> 'DRAFT') THEN
    IF NEW.service_package_id IS DISTINCT FROM OLD.service_package_id
       OR NEW.package_price_snapshot IS DISTINCT FROM OLD.package_price_snapshot THEN
      RAISE EXCEPTION 'pt_sale_guard: package selection is frozen';
    END IF;
  END IF;
  IF NEW.service_package_id IS NOT NULL AND NOT EXISTS (
    SELECT 1
    FROM trainers_service_package AS package
    WHERE package.gym_id = NEW.gym_id
      AND package.id = NEW.service_package_id
      AND package.type = 'PT'
      AND package.is_active
      AND package.class_type_id IS NULL
      AND package.currency = NEW.currency
      AND package.price = NEW.package_price_snapshot
  ) THEN
    RAISE EXCEPTION 'pt_sale_guard: package is not an eligible PT package';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_member_package()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  definition trainers_service_package%ROWTYPE;
BEGIN
  IF NEW.status = 'VOID' OR (TG_OP = 'UPDATE' AND OLD.status = 'VOID') THEN
    RAISE EXCEPTION 'member_package_guard: void is not published';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF NEW.gym_id IS DISTINCT FROM OLD.gym_id
       OR NEW.member_id IS DISTINCT FROM OLD.member_id
       OR NEW.service_package_id IS DISTINCT FROM OLD.service_package_id
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.pt_sale_id IS DISTINCT FROM OLD.pt_sale_id
       OR NEW.payment_id IS DISTINCT FROM OLD.payment_id
       OR NEW.package_type IS DISTINCT FROM OLD.package_type
       OR NEW.package_code IS DISTINCT FROM OLD.package_code
       OR NEW.package_name IS DISTINCT FROM OLD.package_name
       OR NEW.granted_sessions IS DISTINCT FROM OLD.granted_sessions
       OR NEW.amount_snapshot IS DISTINCT FROM OLD.amount_snapshot
       OR NEW.currency IS DISTINCT FROM OLD.currency
       OR NEW.validity_value IS DISTINCT FROM OLD.validity_value
       OR NEW.validity_unit IS DISTINCT FROM OLD.validity_unit
       OR NEW.valid_from IS DISTINCT FROM OLD.valid_from
       OR NEW.valid_until IS DISTINCT FROM OLD.valid_until
       OR NEW.complimentary IS DISTINCT FROM OLD.complimentary
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
       OR NEW.id IS DISTINCT FROM OLD.id THEN
      RAISE EXCEPTION 'member_package_guard: only status can change';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO definition
  FROM trainers_service_package
  WHERE gym_id = NEW.gym_id AND id = NEW.service_package_id;
  IF NOT FOUND OR NOT definition.is_active THEN
    RAISE EXCEPTION 'member_package_guard: package is not active';
  END IF;
  IF NEW.package_type IS DISTINCT FROM definition.type
     OR NEW.package_code IS DISTINCT FROM definition.code
     OR NEW.package_name IS DISTINCT FROM definition.name
     OR NEW.granted_sessions IS DISTINCT FROM definition.session_count
     OR NEW.amount_snapshot IS DISTINCT FROM definition.price
     OR NEW.currency IS DISTINCT FROM definition.currency
     OR NEW.validity_value IS DISTINCT FROM definition.validity_value
     OR NEW.validity_unit IS DISTINCT FROM definition.validity_unit THEN
    RAISE EXCEPTION 'member_package_guard: snapshot does not match the package';
  END IF;
  IF NEW.valid_until IS DISTINCT FROM app_inclusive_until(
    NEW.valid_from, NEW.validity_value, NEW.validity_unit
  ) THEN
    RAISE EXCEPTION 'member_package_guard: validity window is not inclusive';
  END IF;
  IF NEW.created_by_membership_id IS DISTINCT FROM NEW.updated_by_membership_id THEN
    RAISE EXCEPTION 'member_package_guard: actors must match on create';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM members_member AS member
    WHERE member.gym_id = NEW.gym_id
      AND member.id = NEW.member_id
      AND member.status = 'ACTIVE'
  ) THEN
    RAISE EXCEPTION 'member_package_guard: member is not active';
  END IF;
  IF NEW.source = 'PT_SALE' THEN
    IF NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a sale grant is not complimentary';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM trainers_pt_sale AS sale
      WHERE sale.gym_id = NEW.gym_id
        AND sale.id = NEW.pt_sale_id
        AND sale.member_id = NEW.member_id
        AND sale.service_package_id = NEW.service_package_id
        AND sale.status = 'POSTED'
        AND sale.package_price_snapshot = NEW.amount_snapshot
        AND sale.service_date = NEW.valid_from
        AND sale.currency = NEW.currency
    ) THEN
      RAISE EXCEPTION 'member_package_guard: sale is not a posted package sale';
    END IF;
    IF NEW.payment_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM members_payment AS payment
      WHERE payment.gym_id = NEW.gym_id
        AND payment.id = NEW.payment_id
        AND payment.pt_sale_id = NEW.pt_sale_id
        AND payment.member_id = NEW.member_id
        AND payment.status = 'POSTED'
    ) THEN
      RAISE EXCEPTION 'member_package_guard: payment does not belong to the posted sale';
    END IF;
  ELSIF NEW.source = 'ADMIN_GRANT' THEN
    IF NEW.pt_sale_id IS NOT NULL OR NEW.payment_id IS NOT NULL THEN
      RAISE EXCEPTION 'member_package_guard: an admin grant has no sale or payment';
    END IF;
    IF NEW.package_type = 'PT' AND NOT NEW.complimentary THEN
      RAISE EXCEPTION 'member_package_guard: a PT admin grant must be complimentary';
    END IF;
  ELSE
    RAISE EXCEPTION 'member_package_guard: source is not a grant source';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_member_package_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'member_package_guard: packages cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_guard_session_ledger()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  granted integer;
  original_delta integer;
  original_reason text;
  original_package uuid;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'session_ledger_guard: ledger entries are append-only';
  END IF;
  IF NEW.reason IN ('ATTENDED', 'POLICY_NO_SHOW', 'REFUND', 'EXPIRY') THEN
    RAISE EXCEPTION 'session_ledger_guard: this reason is not published';
  END IF;
  IF NEW.reason = 'GRANT' THEN
    SELECT granted_sessions INTO granted
    FROM trainers_member_service_package
    WHERE gym_id = NEW.gym_id AND id = NEW.member_service_package_id;
    IF granted IS NULL OR NEW.session_delta IS DISTINCT FROM granted THEN
      RAISE EXCEPTION 'session_ledger_guard: grant delta must equal granted sessions';
    END IF;
    IF NEW.pt_booking_id IS NOT NULL OR NEW.class_booking_id IS NOT NULL THEN
      RAISE EXCEPTION 'session_ledger_guard: a grant has no booking';
    END IF;
  ELSIF NEW.reason = 'CORRECTION' THEN
    SELECT session_delta, reason, member_service_package_id
      INTO original_delta, original_reason, original_package
    FROM trainers_service_session_ledger
    WHERE gym_id = NEW.gym_id AND id = NEW.reverses_entry_id;
    IF NOT FOUND
       OR original_package IS DISTINCT FROM NEW.member_service_package_id
       OR original_reason = 'GRANT'
       OR NEW.session_delta IS DISTINCT FROM (-original_delta) THEN
      RAISE EXCEPTION 'session_ledger_guard: correction target is not eligible';
    END IF;
  ELSE
    RAISE EXCEPTION 'session_ledger_guard: reason is not a ledger reason';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_session_ledger_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'session_ledger_guard: ledger entries are append-only';
END;
$guard$;

CREATE FUNCTION app_check_member_package_balance()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  package_id uuid;
  gym uuid;
  pkg trainers_member_service_package%ROWTYPE;
  total bigint;
  grant_count bigint;
  grant_delta bigint;
  today date;
  expected text;
  gym_tz text;
BEGIN
  IF TG_TABLE_NAME = 'trainers_service_session_ledger' THEN
    package_id := NEW.member_service_package_id;
    gym := NEW.gym_id;
  ELSE
    package_id := NEW.id;
    gym := NEW.gym_id;
  END IF;
  SELECT * INTO pkg
  FROM trainers_member_service_package
  WHERE gym_id = gym AND id = package_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'session_ledger_guard: package is missing';
  END IF;
  SELECT COALESCE(sum(session_delta), 0),
         count(*) FILTER (WHERE reason = 'GRANT'),
         COALESCE(sum(session_delta) FILTER (WHERE reason = 'GRANT'), 0)
    INTO total, grant_count, grant_delta
  FROM trainers_service_session_ledger
  WHERE gym_id = gym AND member_service_package_id = package_id;
  IF grant_count <> 1 OR grant_delta <> pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: a package needs exactly one grant';
  END IF;
  IF total < 0 OR total > pkg.granted_sessions THEN
    RAISE EXCEPTION 'session_ledger_guard: balance is outside the granted sessions';
  END IF;
  SELECT timezone INTO gym_tz FROM tenants_gym WHERE id = gym;
  today := (CURRENT_TIMESTAMP AT TIME ZONE gym_tz)::date;
  IF total = 0 THEN
    expected := 'EXHAUSTED';
  ELSIF today > pkg.valid_until THEN
    expected := 'EXPIRED';
  ELSE
    expected := 'ACTIVE';
  END IF;
  IF pkg.status IS DISTINCT FROM expected THEN
    RAISE EXCEPTION 'session_ledger_guard: package status does not match the ledger';
  END IF;
  RETURN NULL;
END;
$guard$;

ALTER TABLE trainers_pt_sale
  ADD CONSTRAINT pt_sale_package_same_gym_fk
  FOREIGN KEY (gym_id, service_package_id)
  REFERENCES trainers_service_package (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_member_same_gym_fk
  FOREIGN KEY (gym_id, member_id)
  REFERENCES members_member (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_package_same_gym_fk
  FOREIGN KEY (gym_id, service_package_id)
  REFERENCES trainers_service_package (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_sale_same_gym_fk
  FOREIGN KEY (gym_id, pt_sale_id)
  REFERENCES trainers_pt_sale (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_payment_same_gym_fk
  FOREIGN KEY (gym_id, payment_id)
  REFERENCES members_payment (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_member_service_package
  ADD CONSTRAINT mbr_svc_pkg_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

ALTER TABLE trainers_service_session_ledger
  ADD CONSTRAINT sess_ledger_package_same_gym_fk
  FOREIGN KEY (gym_id, member_service_package_id)
  REFERENCES trainers_member_service_package (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_service_session_ledger
  ADD CONSTRAINT sess_ledger_reversal_same_gym_fk
  FOREIGN KEY (gym_id, reverses_entry_id)
  REFERENCES trainers_service_session_ledger (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE trainers_service_session_ledger
  ADD CONSTRAINT sess_ledger_actor_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;

CREATE TRIGGER pt_sale_package_guard_trg
BEFORE INSERT OR UPDATE ON trainers_pt_sale
FOR EACH ROW EXECUTE FUNCTION app_guard_pt_sale_package();

CREATE TRIGGER mbr_svc_pkg_guard_trg
BEFORE INSERT OR UPDATE ON trainers_member_service_package
FOR EACH ROW EXECUTE FUNCTION app_guard_member_package();
CREATE TRIGGER mbr_svc_pkg_delete_trg
BEFORE DELETE ON trainers_member_service_package
FOR EACH ROW EXECUTE FUNCTION app_guard_member_package_delete();

CREATE TRIGGER sess_ledger_guard_trg
BEFORE INSERT OR UPDATE ON trainers_service_session_ledger
FOR EACH ROW EXECUTE FUNCTION app_guard_session_ledger();
CREATE TRIGGER sess_ledger_delete_trg
BEFORE DELETE ON trainers_service_session_ledger
FOR EACH ROW EXECUTE FUNCTION app_guard_session_ledger_delete();

CREATE CONSTRAINT TRIGGER mbr_svc_pkg_balance_trg
AFTER INSERT OR UPDATE OF status, valid_until ON trainers_member_service_package
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_check_member_package_balance();
CREATE CONSTRAINT TRIGGER sess_ledger_balance_trg
AFTER INSERT ON trainers_service_session_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION app_check_member_package_balance();

ALTER TABLE trainers_member_service_package ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_member_service_package FORCE ROW LEVEL SECURITY;
CREATE POLICY mbr_svc_pkg_select ON trainers_member_service_package
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY mbr_svc_pkg_insert ON trainers_member_service_package
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY mbr_svc_pkg_update ON trainers_member_service_package
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY mbr_svc_pkg_delete ON trainers_member_service_package
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE trainers_service_session_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE trainers_service_session_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY sess_ledger_select ON trainers_service_session_ledger
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY sess_ledger_insert ON trainers_service_session_ledger
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sess_ledger_update ON trainers_service_session_ledger
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY sess_ledger_delete ON trainers_service_session_ledger
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE trainers_member_service_package FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE trainers_member_service_package TO gym_runtime;
REVOKE ALL ON TABLE trainers_service_session_ledger FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE trainers_service_session_ledger TO gym_runtime;

REVOKE ALL ON FUNCTION app_inclusive_until(date, integer, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_pt_sale_package() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_member_package() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_member_package_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_session_ledger() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_session_ledger_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_check_member_package_balance() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_inclusive_until(date, integer, text) TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_pt_sale_package() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_member_package() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_member_package_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_session_ledger() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_session_ledger_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_check_member_package_balance() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_check_member_package_balance() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_session_ledger_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_session_ledger() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_member_package_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_member_package() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_pt_sale_package() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_inclusive_until(date, integer, text) FROM gym_runtime, gym_migration;
REVOKE ALL ON TABLE trainers_service_session_ledger FROM gym_runtime;
REVOKE ALL ON TABLE trainers_member_service_package FROM gym_runtime;
DROP POLICY IF EXISTS sess_ledger_delete ON trainers_service_session_ledger;
DROP POLICY IF EXISTS sess_ledger_update ON trainers_service_session_ledger;
DROP POLICY IF EXISTS sess_ledger_insert ON trainers_service_session_ledger;
DROP POLICY IF EXISTS sess_ledger_select ON trainers_service_session_ledger;
ALTER TABLE trainers_service_session_ledger NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_service_session_ledger DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mbr_svc_pkg_delete ON trainers_member_service_package;
DROP POLICY IF EXISTS mbr_svc_pkg_update ON trainers_member_service_package;
DROP POLICY IF EXISTS mbr_svc_pkg_insert ON trainers_member_service_package;
DROP POLICY IF EXISTS mbr_svc_pkg_select ON trainers_member_service_package;
ALTER TABLE trainers_member_service_package NO FORCE ROW LEVEL SECURITY;
ALTER TABLE trainers_member_service_package DISABLE ROW LEVEL SECURITY;
DROP TRIGGER IF EXISTS sess_ledger_balance_trg ON trainers_service_session_ledger;
DROP TRIGGER IF EXISTS mbr_svc_pkg_balance_trg ON trainers_member_service_package;
DROP TRIGGER IF EXISTS sess_ledger_delete_trg ON trainers_service_session_ledger;
DROP TRIGGER IF EXISTS sess_ledger_guard_trg ON trainers_service_session_ledger;
DROP TRIGGER IF EXISTS mbr_svc_pkg_delete_trg ON trainers_member_service_package;
DROP TRIGGER IF EXISTS mbr_svc_pkg_guard_trg ON trainers_member_service_package;
DROP TRIGGER IF EXISTS pt_sale_package_guard_trg ON trainers_pt_sale;
ALTER TABLE trainers_service_session_ledger DROP CONSTRAINT IF EXISTS sess_ledger_actor_same_gym_fk;
ALTER TABLE trainers_service_session_ledger DROP CONSTRAINT IF EXISTS sess_ledger_reversal_same_gym_fk;
ALTER TABLE trainers_service_session_ledger DROP CONSTRAINT IF EXISTS sess_ledger_package_same_gym_fk;
ALTER TABLE trainers_member_service_package
  DROP CONSTRAINT IF EXISTS mbr_svc_pkg_updated_by_same_gym_fk;
ALTER TABLE trainers_member_service_package
  DROP CONSTRAINT IF EXISTS mbr_svc_pkg_created_by_same_gym_fk;
ALTER TABLE trainers_member_service_package DROP CONSTRAINT IF EXISTS mbr_svc_pkg_payment_same_gym_fk;
ALTER TABLE trainers_member_service_package DROP CONSTRAINT IF EXISTS mbr_svc_pkg_sale_same_gym_fk;
ALTER TABLE trainers_member_service_package DROP CONSTRAINT IF EXISTS mbr_svc_pkg_package_same_gym_fk;
ALTER TABLE trainers_member_service_package DROP CONSTRAINT IF EXISTS mbr_svc_pkg_member_same_gym_fk;
ALTER TABLE trainers_pt_sale DROP CONSTRAINT IF EXISTS pt_sale_package_same_gym_fk;
DROP FUNCTION IF EXISTS app_check_member_package_balance();
DROP FUNCTION IF EXISTS app_guard_session_ledger_delete();
DROP FUNCTION IF EXISTS app_guard_session_ledger();
DROP FUNCTION IF EXISTS app_guard_member_package_delete();
DROP FUNCTION IF EXISTS app_guard_member_package();
DROP FUNCTION IF EXISTS app_guard_pt_sale_package();
DROP FUNCTION IF EXISTS app_inclusive_until(date, integer, text);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("trainers", "0012_pt_sale_package_and_ledger"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
