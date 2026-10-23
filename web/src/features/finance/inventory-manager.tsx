"use client";

import { useEffect, useState } from "react";

import {
  adjustStock,
  createProduct,
  createProductCategory,
  getProduct,
  listLowStock,
  listProductCategories,
  listProducts,
  listStockMovements,
  updateProduct,
} from "@/features/finance/client";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney, canonicalQuantity } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CommandForm,
  CursorControls,
  Empty,
  Field,
  Loading,
  ReasonField,
  SelectInput,
  ServerFacts,
  StatusText,
  TextInput,
} from "@/features/finance/ui";
import { actionButtonClass } from "@/lib/ui/action-button";
import type { MovementType, Product, StockAdjustRequest } from "@/types/api";

const MANUAL_TYPES = [
  "OPENING",
  "PURCHASE",
  "DAMAGE",
  "LOSS",
  "ADJUSTMENT",
  "CORRECTION",
] as const satisfies readonly MovementType[];
const HISTORY_TYPES = [...MANUAL_TYPES, "SALE", "SALE_RETURN"] as const;
const PAGE = "25";

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function InventoryManager({
  canManage,
  canAdjust,
  defaultDate,
}: {
  canManage: boolean;
  canAdjust: boolean;
  defaultDate: string;
}) {
  const [view, setView] = useState<"catalog" | "low" | "movements">("catalog");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [appliedCategory, setAppliedCategory] = useState("");
  const [movementType, setMovementType] = useState("");
  const [appliedMovement, setAppliedMovement] = useState("|");
  const [selectedId, setSelectedId] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  const [editing, setEditing] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const categories = useCursorPages("categories", (after) =>
    listProductCategories({ limit: "100", ...(after ? { after } : {}) }),
  );
  const products = useCursorPages(`catalog:${appliedCategory}`, (after) =>
    listProducts({
      ...(appliedCategory ? { category_id: appliedCategory } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const lowStock = useCursorPages(`low:${appliedCategory}`, (after) =>
    listLowStock({
      ...(appliedCategory ? { category_id: appliedCategory } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const movements = useCursorPages(appliedMovement, (after) => {
    const [productId, type, date] = appliedMovement.split("|");
    return listStockMovements({
      ...(productId ? { product_id: productId } : {}),
      ...(type ? { movement_type: type } : {}),
      ...(date ? { business_date: date } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    });
  });

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getProduct(selectedId)
        .then((row) => {
          if (active) setProduct(row);
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setProduct(null);
          setFormError(
            financeError(caught, "The product could not be loaded."),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, products.rows]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  function money(form: FormData, name: string): string | null {
    return canonicalMoney(text(form, name), { allowZero: true });
  }

  const activeRows = view === "low" ? lowStock : products;

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap gap-3">
        <button
          className="underline"
          onClick={() => setView("catalog")}
          type="button"
        >
          Catalog
        </button>
        <button
          className="underline"
          onClick={() => setView("low")}
          type="button"
        >
          Low stock
        </button>
        <button
          className="underline"
          onClick={() => setView("movements")}
          type="button"
        >
          Stock movements
        </button>
      </div>
      <section
        aria-labelledby="product-categories-title"
        className="grid gap-3"
      >
        <h2 className="text-lg font-bold" id="product-categories-title">
          Product categories
        </h2>
        <ul className="grid gap-1">
          {categories.rows.map((category) => (
            <li key={category.id}>
              {category.code} — {category.name}
            </li>
          ))}
        </ul>
        {canManage && (
          <form
            className="grid gap-3 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              const formElement = event.currentTarget;
              const form = new FormData(formElement);
              setPending(true);
              void createProductCategory({
                code: text(form, "code").toUpperCase(),
                name: text(form, "name"),
                is_active: true,
              })
                .then(() => {
                  formElement.reset();
                  categories.reload();
                })
                .catch((caught: unknown) =>
                  fail(caught, "The category could not be created."),
                )
                .finally(() => setPending(false));
            }}
          >
            <Field label="Category code">
              <TextInput name="code" required />
            </Field>
            <Field label="Category name">
              <TextInput name="name" required />
            </Field>
            <button
              className="button button-create"
              disabled={pending}
              type="submit"
            >
              Create category
            </button>
          </form>
        )}
      </section>
      {view !== "movements" && (
        <section className="grid gap-4">
          <form
            className="flex flex-wrap gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setAppliedCategory(categoryFilter);
            }}
          >
            <Field label="Category">
              <SelectInput
                onChange={(event) => setCategoryFilter(event.target.value)}
                value={categoryFilter}
              >
                <option value="">All categories</option>
                {categories.rows.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <button className="button self-end" type="submit">
              Apply filters
            </button>
          </form>
          <Alert message={activeRows.error || formError} />
          {activeRows.pending && <Loading label="Loading products." />}
          {!activeRows.pending && activeRows.rows.length === 0 && (
            <Empty label="No products were returned." />
          )}
          {activeRows.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">
                  {view === "low" ? "Low stock" : "Products"}
                </caption>
                <thead className="bg-slate-50">
                  <tr>
                    <th className="p-3">SKU</th>
                    <th className="p-3">Name</th>
                    <th className="p-3">On hand</th>
                    <th className="p-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {activeRows.rows.map((row) => (
                    <tr className="border-t border-slate-100" key={row.id}>
                      <td className="p-3">
                        <button
                          className="underline"
                          onClick={() => {
                            setSelectedId(row.id);
                            setEditing(false);
                          }}
                          type="button"
                        >
                          {row.sku}
                        </button>
                      </td>
                      <td className="p-3">{row.name}</td>
                      <td className="p-3">{row.quantity_on_hand}</td>
                      <td className="p-3">
                        <StatusText
                          status={row.is_active ? "Active" : "Inactive"}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <CursorControls
            bounded={activeRows.bounded}
            hasMore={activeRows.hasMore}
            index={activeRows.index}
            onNext={() => void activeRows.goNext()}
            onPrevious={() => void activeRows.goPrevious()}
            pending={activeRows.pending}
          />
        </section>
      )}
      {view === "movements" && (
        <section className="grid gap-4">
          <form
            className="grid gap-3 md:grid-cols-4"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              setAppliedMovement(
                `${text(form, "product_id")}|${text(form, "movement_type")}|${text(form, "business_date")}`,
              );
            }}
          >
            <Field label="Product">
              <SelectInput name="product_id">
                <option value="">Any product</option>
                {products.rows.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.sku}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Movement type">
              <SelectInput
                name="movement_type"
                onChange={(event) => setMovementType(event.target.value)}
                value={movementType}
              >
                <option value="">Any type</option>
                {HISTORY_TYPES.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Business date">
              <TextInput name="business_date" type="date" />
            </Field>
            <button className="button self-end" type="submit">
              Apply filters
            </button>
          </form>
          <Alert message={movements.error} />
          {movements.rows.length === 0 && !movements.pending && (
            <Empty label="No stock movements were returned." />
          )}
          {movements.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Stock movements</caption>
                <thead className="bg-slate-50">
                  <tr>
                    <th className="p-3">Type</th>
                    <th className="p-3">Quantity</th>
                    <th className="p-3">Unit cost</th>
                    <th className="p-3">Date</th>
                  </tr>
                </thead>
                <tbody>
                  {movements.rows.map((row) => (
                    <tr className="border-t border-slate-100" key={row.id}>
                      <td className="p-3">{row.movement_type}</td>
                      <td className="p-3">{row.quantity}</td>
                      <td className="p-3">{row.unit_cost_snapshot}</td>
                      <td className="p-3">{row.business_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <CursorControls
            bounded={movements.bounded}
            hasMore={movements.hasMore}
            index={movements.index}
            onNext={() => void movements.goNext()}
            onPrevious={() => void movements.goPrevious()}
            pending={movements.pending}
          />
        </section>
      )}
      {canManage && (
        <form
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const sale = money(form, "sale_price");
            const current = money(form, "current_unit_cost");
            const fallback = money(form, "default_unit_cost");
            const threshold = canonicalQuantity(
              text(form, "low_stock_threshold"),
              { allowZero: true },
            );
            if (!sale || !current || !fallback || !threshold) {
              setFields({
                sale_price:
                  "Enter exact money with two decimals and quantity with three decimals.",
              });
              return;
            }
            const barcode = text(form, "barcode");
            setPending(true);
            const save =
              editing && product
                ? updateProduct(product.id, {
                    sku: text(form, "sku").toUpperCase(),
                    name: text(form, "name"),
                    category_id: text(form, "category_id"),
                    unit: text(form, "unit").toUpperCase(),
                    sale_price: sale,
                    current_unit_cost: current,
                    default_unit_cost: fallback,
                    low_stock_threshold: threshold,
                    is_active: form.get("is_active") === "on",
                    barcode: barcode || null,
                  })
                : createProduct({
                    sku: text(form, "sku").toUpperCase(),
                    name: text(form, "name"),
                    category_id: text(form, "category_id"),
                    unit: text(form, "unit").toUpperCase(),
                    sale_price: sale,
                    current_unit_cost: current,
                    default_unit_cost: fallback,
                    low_stock_threshold: threshold,
                    is_active: true,
                    ...(barcode ? { barcode } : {}),
                  });
            void save
              .then((saved) => {
                setProduct(saved);
                setSelectedId(saved.id);
                setEditing(false);
                products.reload();
              })
              .catch((caught: unknown) =>
                fail(caught, "The product could not be saved."),
              )
              .finally(() => setPending(false));
          }}
        >
          <h2 className="text-lg font-bold md:col-span-2">
            {editing ? "Edit product" : "New product"}
          </h2>
          <Alert message={formError} />
          <Field error={fields.sku} label="SKU">
            <TextInput
              defaultValue={editing ? product?.sku : ""}
              name="sku"
              required
            />
          </Field>
          <Field error={fields.barcode} label="Barcode">
            <TextInput
              defaultValue={editing ? (product?.barcode ?? "") : ""}
              name="barcode"
            />
          </Field>
          <Field error={fields.name} label="Name">
            <TextInput
              defaultValue={editing ? product?.name : ""}
              name="name"
              required
            />
          </Field>
          <Field error={fields.category_id} label="Category">
            <SelectInput
              defaultValue={editing ? product?.category_id : ""}
              name="category_id"
              required
            >
              <option value="">Select a category</option>
              {categories.rows.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field error={fields.unit} label="Unit">
            <TextInput
              defaultValue={editing ? product?.unit : "PIECE"}
              name="unit"
              required
            />
          </Field>
          <Field error={fields.sale_price} label="Sale price">
            <TextInput
              defaultValue={editing ? product?.sale_price : ""}
              inputMode="decimal"
              name="sale_price"
              required
            />
          </Field>
          <Field error={fields.current_unit_cost} label="Current unit cost">
            <TextInput
              defaultValue={editing ? product?.current_unit_cost : ""}
              inputMode="decimal"
              name="current_unit_cost"
              required
            />
          </Field>
          <Field error={fields.default_unit_cost} label="Default unit cost">
            <TextInput
              defaultValue={editing ? product?.default_unit_cost : ""}
              inputMode="decimal"
              name="default_unit_cost"
              required
            />
          </Field>
          <Field error={fields.low_stock_threshold} label="Low-stock threshold">
            <TextInput
              defaultValue={editing ? product?.low_stock_threshold : "0.000"}
              inputMode="decimal"
              name="low_stock_threshold"
              required
            />
          </Field>
          {editing && (
            <label className="field">
              <input
                defaultChecked={product?.is_active}
                name="is_active"
                type="checkbox"
              />{" "}
              Active
            </label>
          )}
          <button
            className={actionButtonClass(
              editing ? "Save product" : "Create product",
            )}
            disabled={pending}
            type="submit"
          >
            {editing ? "Save product" : "Create product"}
          </button>
        </form>
      )}
      {product && (
        <section aria-labelledby="product-detail-title" className="grid gap-3">
          <h2 className="text-lg font-bold" id="product-detail-title">
            {product.name}
          </h2>
          <ServerFacts
            rows={[
              { label: "Quantity on hand", value: product.quantity_on_hand },
              { label: "Sold quantity", value: product.sold_quantity },
              { label: "Revenue", value: product.revenue },
              {
                label: "Cost of goods sold",
                value: product.cost_of_goods_sold,
              },
              { label: "Gross profit", value: product.gross_profit },
            ]}
          />
          <p className="text-sm text-slate-600">
            These stock and money totals are returned by the server.
          </p>
          {canManage && (
            <button
              className="underline tone-edit"
              onClick={() => setEditing(true)}
              type="button"
            >
              Edit product
            </button>
          )}
          {canAdjust && (
            <button
              className="underline"
              onClick={() => setAdjusting(true)}
              type="button"
            >
              Adjust stock
            </button>
          )}
          {adjusting && canAdjust && (
            <CommandForm
              confirmLabel="Confirm adjustment"
              error={formError}
              onCancel={() => setAdjusting(false)}
              onSubmit={(form) => {
                const quantity = canonicalQuantity(text(form, "quantity"), {
                  allowNegative: true,
                });
                const movement = text(form, "movement_type");
                if (
                  !quantity ||
                  quantity === "0.000" ||
                  !MANUAL_TYPES.includes(movement as MovementType)
                ) {
                  setFields({
                    quantity: "Enter a non-zero quantity with three decimals.",
                  });
                  return;
                }
                const body: StockAdjustRequest = {
                  product_id: product.id,
                  quantity,
                  movement_type: movement as MovementType,
                  business_date: text(form, "business_date"),
                  reason: text(form, "reason"),
                };
                setPending(true);
                void adjustStock(body)
                  .then((result) => {
                    setProduct(result.product);
                    setAdjusting(false);
                    products.reload();
                    movements.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The stock could not be adjusted."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Manual stock adjustment"
            >
              <Field error={fields.quantity} label="Quantity">
                <TextInput inputMode="decimal" name="quantity" required />
              </Field>
              <Field error={fields.movement_type} label="Movement type">
                <SelectInput name="movement_type" required>
                  <option value="">Select a type</option>
                  {MANUAL_TYPES.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <Field label="Business date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  required
                  type="date"
                />
              </Field>
              <ReasonField label="Reason" />
            </CommandForm>
          )}
        </section>
      )}
    </div>
  );
}
