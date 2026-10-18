from django.db import migrations, models

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_stock_movement()
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
  IF NEW.movement_type = 'SALE_RETURN' THEN
    IF NEW.retail_sale_line_id IS NULL OR NEW.reverses_movement_id IS NULL OR NEW.quantity <= 0 THEN
      RAISE EXCEPTION 'stock_guard: sale return evidence does not match';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      JOIN public.inventory_stock_movement original
        ON original.gym_id = line.gym_id
       AND original.id = NEW.reverses_movement_id
      WHERE line.gym_id = NEW.gym_id
        AND line.id = NEW.retail_sale_line_id
        AND line.product_id = NEW.product_id
        AND line.quantity = NEW.quantity
        AND line.unit_cost_snapshot = NEW.unit_cost_snapshot
        AND original.movement_type = 'SALE'
        AND original.product_id = NEW.product_id
        AND original.retail_sale_line_id = line.id
        AND original.quantity = -NEW.quantity
        AND original.unit_cost_snapshot = NEW.unit_cost_snapshot
        AND original.reverses_movement_id IS NULL
    ) THEN
      RAISE EXCEPTION 'stock_guard: sale return evidence does not match';
    END IF;
  ELSIF NEW.reverses_movement_id IS NOT NULL THEN
    RAISE EXCEPTION 'stock_guard: reversal evidence is not available';
  ELSIF NEW.movement_type = 'SALE' THEN
    IF NEW.retail_sale_line_id IS NULL OR NEW.quantity >= 0 THEN
      RAISE EXCEPTION 'stock_guard: sale and reversal evidence is not available';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      WHERE line.gym_id = NEW.gym_id
        AND line.id = NEW.retail_sale_line_id
        AND line.product_id = NEW.product_id
        AND line.quantity = -NEW.quantity
        AND line.unit_cost_snapshot = NEW.unit_cost_snapshot
    ) THEN
      RAISE EXCEPTION 'stock_guard: sale evidence does not match the retail line';
    END IF;
  ELSIF NEW.retail_sale_line_id IS NOT NULL THEN
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

CREATE OR REPLACE FUNCTION app_stock_sale_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale_status text;
  correction_date date;
BEGIN
  IF NEW.movement_type = 'SALE' THEN
    SELECT sale.status INTO sale_status
    FROM public.sales_retail_sale_line line
    JOIN public.sales_retail_sale sale
      ON sale.gym_id = line.gym_id
     AND sale.id = line.sale_id
    WHERE line.gym_id = NEW.gym_id
      AND line.id = NEW.retail_sale_line_id;
    IF sale_status IS DISTINCT FROM 'POSTED' THEN
      RAISE EXCEPTION 'stock_guard: a sale movement requires a posted retail sale';
    END IF;
  ELSIF NEW.movement_type = 'SALE_RETURN' THEN
    SELECT sale.status, sale.void_business_date
      INTO sale_status, correction_date
    FROM public.sales_retail_sale_line line
    JOIN public.sales_retail_sale sale
      ON sale.gym_id = line.gym_id
     AND sale.id = line.sale_id
    WHERE line.gym_id = NEW.gym_id
      AND line.id = NEW.retail_sale_line_id;
    IF sale_status IS DISTINCT FROM 'VOID' OR correction_date IS DISTINCT FROM NEW.business_date THEN
      RAISE EXCEPTION 'stock_guard: a sale return requires the voided retail sale';
    END IF;
  END IF;
  RETURN NULL;
END;
$guard$;
"""

REVERSE_SQL = r"""
CREATE OR REPLACE FUNCTION app_guard_stock_movement()
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
  IF NEW.movement_type = 'SALE_RETURN' OR NEW.reverses_movement_id IS NOT NULL THEN
    RAISE EXCEPTION 'stock_guard: sale and reversal evidence is not available';
  END IF;
  IF NEW.movement_type = 'SALE' THEN
    IF NEW.retail_sale_line_id IS NULL OR NEW.quantity >= 0 THEN
      RAISE EXCEPTION 'stock_guard: sale and reversal evidence is not available';
    END IF;
    IF NOT EXISTS (
      SELECT 1
      FROM public.sales_retail_sale_line line
      WHERE line.gym_id = NEW.gym_id
        AND line.id = NEW.retail_sale_line_id
        AND line.product_id = NEW.product_id
        AND line.quantity = -NEW.quantity
        AND line.unit_cost_snapshot = NEW.unit_cost_snapshot
    ) THEN
      RAISE EXCEPTION 'stock_guard: sale evidence does not match the retail line';
    END IF;
  ELSIF NEW.retail_sale_line_id IS NOT NULL THEN
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

CREATE OR REPLACE FUNCTION app_stock_sale_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $guard$
DECLARE
  sale_status text;
BEGIN
  IF NEW.movement_type IS DISTINCT FROM 'SALE' THEN
    RETURN NULL;
  END IF;
  SELECT sale.status INTO sale_status
  FROM public.sales_retail_sale_line line
  JOIN public.sales_retail_sale sale
    ON sale.gym_id = line.gym_id
   AND sale.id = line.sale_id
  WHERE line.gym_id = NEW.gym_id
    AND line.id = NEW.retail_sale_line_id;
  IF sale_status IS DISTINCT FROM 'POSTED' THEN
    RAISE EXCEPTION 'stock_guard: a sale movement requires a posted retail sale';
  END IF;
  RETURN NULL;
END;
$guard$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_retail_sale_foundation"),
        ("sales", "0003_retail_sale_void"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="stockmovement",
            name="stock_quantity_sign_ck",
        ),
        migrations.RemoveConstraint(
            model_name="stockmovement",
            name="stock_evidence_ck",
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("movement_type__in", ["OPENING", "PURCHASE"]), ("quantity__gt", 0)),
                    models.Q(("movement_type__in", ["DAMAGE", "LOSS"]), ("quantity__lt", 0)),
                    models.Q(
                        ("movement_type__in", ["ADJUSTMENT", "CORRECTION"]),
                        models.Q(("quantity", 0), _negated=True),
                    ),
                    models.Q(("movement_type", "SALE"), ("quantity__lt", 0)),
                    models.Q(("movement_type", "SALE_RETURN"), ("quantity__gt", 0)),
                    _connector="OR",
                ),
                name="stock_quantity_sign_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        movement_type__in=[
                            "OPENING",
                            "PURCHASE",
                            "DAMAGE",
                            "LOSS",
                            "ADJUSTMENT",
                            "CORRECTION",
                        ],
                        reverses_movement__isnull=True,
                        retail_sale_line__isnull=True,
                    )
                    | models.Q(
                        movement_type="SALE",
                        quantity__lt=0,
                        reverses_movement__isnull=True,
                        retail_sale_line__isnull=False,
                    )
                    | models.Q(
                        movement_type="SALE_RETURN",
                        quantity__gt=0,
                        reverses_movement__isnull=False,
                        retail_sale_line__isnull=False,
                    )
                ),
                name="stock_evidence_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.UniqueConstraint(
                fields=("gym", "retail_sale_line"),
                condition=models.Q(movement_type="SALE_RETURN"),
                name="stock_one_return_line_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="stockmovement",
            constraint=models.UniqueConstraint(
                fields=("gym", "reverses_movement"),
                condition=models.Q(reverses_movement__isnull=False),
                name="stock_one_reversal_uniq",
            ),
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
