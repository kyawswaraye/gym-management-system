from django.db import migrations

FORWARD_SQL = r"""
ALTER TABLE inventory_product_category
  ADD CONSTRAINT prodcat_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product_category
  ADD CONSTRAINT prodcat_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product_category
  ADD CONSTRAINT prodcat_archived_by_same_gym_fk
  FOREIGN KEY (gym_id, archived_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product_category
  ADD CONSTRAINT prodcat_normalized_name_ck
  CHECK (
    normalized_name = upper(regexp_replace(btrim(name), '[[:space:]]+', ' ', 'g'))
    AND btrim(name) <> ''
  );

ALTER TABLE inventory_product
  ADD CONSTRAINT product_category_same_gym_fk
  FOREIGN KEY (gym_id, category_id)
  REFERENCES inventory_product_category (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product
  ADD CONSTRAINT product_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product
  ADD CONSTRAINT product_updated_by_same_gym_fk
  FOREIGN KEY (gym_id, updated_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product
  ADD CONSTRAINT product_archived_by_same_gym_fk
  FOREIGN KEY (gym_id, archived_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_product
  ADD CONSTRAINT product_name_btrim_ck
  CHECK (btrim(name) <> '');

ALTER TABLE inventory_stock_movement
  ADD CONSTRAINT stock_product_same_gym_fk
  FOREIGN KEY (gym_id, product_id)
  REFERENCES inventory_product (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_stock_movement
  ADD CONSTRAINT stock_created_by_same_gym_fk
  FOREIGN KEY (gym_id, created_by_membership_id)
  REFERENCES tenants_gym_membership (gym_id, id) ON DELETE RESTRICT;
ALTER TABLE inventory_stock_movement
  ADD CONSTRAINT stock_reversal_same_gym_fk
  FOREIGN KEY (gym_id, reverses_movement_id)
  REFERENCES inventory_stock_movement (gym_id, id) ON DELETE RESTRICT;

CREATE FUNCTION app_guard_product_category()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.code !~ '^[A-Z][A-Z0-9_]{0,31}$'
     OR btrim(NEW.name) = ''
     OR NEW.normalized_name IS DISTINCT FROM
        upper(regexp_replace(btrim(NEW.name), '[[:space:]]+', ' ', 'g')) THEN
    RAISE EXCEPTION 'category_guard: code or normalized name is invalid';
  END IF;
  IF NEW.is_active THEN
    IF NEW.archived_at IS NOT NULL OR NEW.archived_by_membership_id IS NOT NULL THEN
      RAISE EXCEPTION 'category_guard: active category has archive evidence';
    END IF;
  ELSIF NEW.archived_at IS NULL OR NEW.archived_by_membership_id IS NULL THEN
    RAISE EXCEPTION 'category_guard: archived category lacks evidence';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
    OR NEW.code IS DISTINCT FROM OLD.code
  ) THEN
    RAISE EXCEPTION 'category_guard: identity columns are immutable';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_product_category_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'category_guard: product categories cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_guard_product()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF NEW.sku !~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'
     OR NEW.unit !~ '^[A-Z][A-Z0-9_]{0,15}$'
     OR btrim(NEW.name) = ''
     OR (NEW.barcode IS NOT NULL AND NEW.barcode !~ '^[0-9A-Z]{1,64}$')
     OR NEW.sale_price < 0
     OR NEW.current_unit_cost < 0
     OR NEW.default_unit_cost < 0
     OR NEW.low_stock_threshold < 0 THEN
    RAISE EXCEPTION 'product_guard: catalog format or amount is invalid';
  END IF;
  IF NEW.is_active THEN
    IF NEW.archived_at IS NOT NULL OR NEW.archived_by_membership_id IS NOT NULL THEN
      RAISE EXCEPTION 'product_guard: active product has archive evidence';
    END IF;
  ELSIF NEW.archived_at IS NULL OR NEW.archived_by_membership_id IS NULL THEN
    RAISE EXCEPTION 'product_guard: archived product lacks evidence';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.gym_id IS DISTINCT FROM OLD.gym_id
    OR NEW.id IS DISTINCT FROM OLD.id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.created_by_membership_id IS DISTINCT FROM OLD.created_by_membership_id
  ) THEN
    RAISE EXCEPTION 'product_guard: identity columns are immutable';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_product_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'product_guard: products cannot be deleted';
END;
$guard$;

CREATE FUNCTION app_guard_stock_movement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'stock_guard: stock movements are append-only';
  END IF;
  IF NEW.movement_type NOT IN (
    'OPENING', 'PURCHASE', 'SALE', 'SALE_RETURN', 'DAMAGE', 'LOSS', 'ADJUSTMENT', 'CORRECTION'
  ) THEN
    RAISE EXCEPTION 'stock_guard: movement classification is invalid';
  END IF;
  IF NEW.movement_type IN ('SALE', 'SALE_RETURN') OR NEW.reverses_movement_id IS NOT NULL THEN
    RAISE EXCEPTION 'stock_guard: sale and reversal evidence is not available';
  END IF;
  IF NEW.movement_type IN ('OPENING', 'PURCHASE') AND NEW.quantity <= 0 THEN
    RAISE EXCEPTION 'stock_guard: opening and purchase quantities must be positive';
  END IF;
  IF NEW.movement_type IN ('DAMAGE', 'LOSS') AND NEW.quantity >= 0 THEN
    RAISE EXCEPTION 'stock_guard: damage and loss quantities must be negative';
  END IF;
  IF NEW.movement_type IN ('ADJUSTMENT', 'CORRECTION') AND NEW.quantity = 0 THEN
    RAISE EXCEPTION 'stock_guard: adjustment quantity must be non-zero';
  END IF;
  IF NEW.movement_type IN ('LOSS', 'ADJUSTMENT', 'CORRECTION') AND btrim(NEW.reason) = '' THEN
    RAISE EXCEPTION 'stock_guard: manual loss, adjustment, and correction require a reason';
  END IF;
  IF NEW.unit_cost_snapshot < 0 THEN
    RAISE EXCEPTION 'stock_guard: unit cost snapshot cannot be negative';
  END IF;
  RETURN NEW;
END;
$guard$;

CREATE FUNCTION app_guard_stock_movement_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
BEGIN
  RAISE EXCEPTION 'stock_guard: stock movements are append-only';
END;
$guard$;

CREATE FUNCTION app_stock_nonnegative()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  on_hand numeric(18, 3);
BEGIN
  PERFORM 1
  FROM public.inventory_product
  WHERE gym_id = NEW.gym_id AND id = NEW.product_id
  FOR UPDATE;
  SELECT COALESCE(SUM(quantity), 0) INTO on_hand
  FROM public.inventory_stock_movement
  WHERE gym_id = NEW.gym_id AND product_id = NEW.product_id;
  IF on_hand < 0 THEN
    RAISE EXCEPTION 'stock_guard: quantity on hand cannot be negative';
  END IF;
  RETURN NULL;
END;
$guard$;

CREATE TRIGGER product_category_guard_trg
  BEFORE INSERT OR UPDATE ON inventory_product_category
  FOR EACH ROW EXECUTE FUNCTION app_guard_product_category();
CREATE TRIGGER product_category_delete_trg
  BEFORE DELETE ON inventory_product_category
  FOR EACH ROW EXECUTE FUNCTION app_guard_product_category_delete();
CREATE TRIGGER product_guard_trg
  BEFORE INSERT OR UPDATE ON inventory_product
  FOR EACH ROW EXECUTE FUNCTION app_guard_product();
CREATE TRIGGER product_delete_trg
  BEFORE DELETE ON inventory_product
  FOR EACH ROW EXECUTE FUNCTION app_guard_product_delete();
CREATE TRIGGER stock_movement_guard_trg
  BEFORE INSERT OR UPDATE ON inventory_stock_movement
  FOR EACH ROW EXECUTE FUNCTION app_guard_stock_movement();
CREATE TRIGGER stock_movement_delete_trg
  BEFORE DELETE ON inventory_stock_movement
  FOR EACH ROW EXECUTE FUNCTION app_guard_stock_movement_delete();
CREATE TRIGGER stock_nonnegative_trg
  AFTER INSERT ON inventory_stock_movement
  FOR EACH ROW EXECUTE FUNCTION app_stock_nonnegative();

ALTER TABLE inventory_product_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_product_category FORCE ROW LEVEL SECURITY;
CREATE POLICY inventory_product_category_select ON inventory_product_category
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_category_insert ON inventory_product_category
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_category_update ON inventory_product_category
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_category_delete ON inventory_product_category
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE inventory_product ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_product FORCE ROW LEVEL SECURITY;
CREATE POLICY inventory_product_select ON inventory_product
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_insert ON inventory_product
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_update ON inventory_product
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_product_delete ON inventory_product
  FOR DELETE USING (gym_id = app_current_gym_id());

ALTER TABLE inventory_stock_movement ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_stock_movement FORCE ROW LEVEL SECURITY;
CREATE POLICY inventory_stock_movement_select ON inventory_stock_movement
  FOR SELECT USING (gym_id = app_current_gym_id());
CREATE POLICY inventory_stock_movement_insert ON inventory_stock_movement
  FOR INSERT WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_stock_movement_update ON inventory_stock_movement
  FOR UPDATE USING (gym_id = app_current_gym_id())
  WITH CHECK (gym_id = app_current_gym_id());
CREATE POLICY inventory_stock_movement_delete ON inventory_stock_movement
  FOR DELETE USING (gym_id = app_current_gym_id());

REVOKE ALL ON TABLE inventory_product_category FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE inventory_product_category TO gym_runtime;
REVOKE ALL ON TABLE inventory_product FROM gym_runtime;
GRANT SELECT, INSERT, UPDATE ON TABLE inventory_product TO gym_runtime;
REVOKE ALL ON TABLE inventory_stock_movement FROM gym_runtime;
GRANT SELECT, INSERT ON TABLE inventory_stock_movement TO gym_runtime;

REVOKE ALL ON FUNCTION app_guard_product_category() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_product_category_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_product() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_product_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_stock_movement() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_guard_stock_movement_delete() FROM PUBLIC;
REVOKE ALL ON FUNCTION app_stock_nonnegative() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app_guard_product_category() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_product_category_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_product() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_product_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_stock_movement() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_guard_stock_movement_delete() TO gym_runtime, gym_migration;
GRANT EXECUTE ON FUNCTION app_stock_nonnegative() TO gym_runtime, gym_migration;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION app_stock_nonnegative() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_stock_movement_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_stock_movement() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_product_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_product() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_product_category_delete() FROM gym_runtime, gym_migration;
REVOKE ALL ON FUNCTION app_guard_product_category() FROM gym_runtime, gym_migration;
DROP TRIGGER IF EXISTS stock_nonnegative_trg ON inventory_stock_movement;
DROP TRIGGER IF EXISTS stock_movement_delete_trg ON inventory_stock_movement;
DROP TRIGGER IF EXISTS stock_movement_guard_trg ON inventory_stock_movement;
DROP TRIGGER IF EXISTS product_delete_trg ON inventory_product;
DROP TRIGGER IF EXISTS product_guard_trg ON inventory_product;
DROP TRIGGER IF EXISTS product_category_delete_trg ON inventory_product_category;
DROP TRIGGER IF EXISTS product_category_guard_trg ON inventory_product_category;
DROP FUNCTION IF EXISTS app_stock_nonnegative();
DROP FUNCTION IF EXISTS app_guard_stock_movement_delete();
DROP FUNCTION IF EXISTS app_guard_stock_movement();
DROP FUNCTION IF EXISTS app_guard_product_delete();
DROP FUNCTION IF EXISTS app_guard_product();
DROP FUNCTION IF EXISTS app_guard_product_category_delete();
DROP FUNCTION IF EXISTS app_guard_product_category();
DROP POLICY IF EXISTS inventory_stock_movement_delete ON inventory_stock_movement;
DROP POLICY IF EXISTS inventory_stock_movement_update ON inventory_stock_movement;
DROP POLICY IF EXISTS inventory_stock_movement_insert ON inventory_stock_movement;
DROP POLICY IF EXISTS inventory_stock_movement_select ON inventory_stock_movement;
DROP POLICY IF EXISTS inventory_product_delete ON inventory_product;
DROP POLICY IF EXISTS inventory_product_update ON inventory_product;
DROP POLICY IF EXISTS inventory_product_insert ON inventory_product;
DROP POLICY IF EXISTS inventory_product_select ON inventory_product;
DROP POLICY IF EXISTS inventory_product_category_delete ON inventory_product_category;
DROP POLICY IF EXISTS inventory_product_category_update ON inventory_product_category;
DROP POLICY IF EXISTS inventory_product_category_insert ON inventory_product_category;
DROP POLICY IF EXISTS inventory_product_category_select ON inventory_product_category;
ALTER TABLE inventory_stock_movement DROP CONSTRAINT IF EXISTS stock_reversal_same_gym_fk;
ALTER TABLE inventory_stock_movement DROP CONSTRAINT IF EXISTS stock_created_by_same_gym_fk;
ALTER TABLE inventory_stock_movement DROP CONSTRAINT IF EXISTS stock_product_same_gym_fk;
ALTER TABLE inventory_product DROP CONSTRAINT IF EXISTS product_name_btrim_ck;
ALTER TABLE inventory_product DROP CONSTRAINT IF EXISTS product_archived_by_same_gym_fk;
ALTER TABLE inventory_product DROP CONSTRAINT IF EXISTS product_updated_by_same_gym_fk;
ALTER TABLE inventory_product DROP CONSTRAINT IF EXISTS product_created_by_same_gym_fk;
ALTER TABLE inventory_product DROP CONSTRAINT IF EXISTS product_category_same_gym_fk;
ALTER TABLE inventory_product_category DROP CONSTRAINT IF EXISTS prodcat_normalized_name_ck;
ALTER TABLE inventory_product_category DROP CONSTRAINT IF EXISTS prodcat_archived_by_same_gym_fk;
ALTER TABLE inventory_product_category DROP CONSTRAINT IF EXISTS prodcat_updated_by_same_gym_fk;
ALTER TABLE inventory_product_category DROP CONSTRAINT IF EXISTS prodcat_created_by_same_gym_fk;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_product_catalog_and_stock"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
