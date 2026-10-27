"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useRef, useState, type FormEvent } from "react";

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
import { FormDialog } from "@/components/form-dialog";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney, canonicalQuantity } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { actionButtonClass } from "@/lib/ui/action-button";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  MovementType,
  Product,
  ProductCategory,
  StockAdjustRequest,
} from "@/types/api";

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
const SECTIONS = [
  ["catalog", "Catalog"],
  ["low", "Low stock"],
  ["movements", "Stock movements"],
  ["categories", "Categories"],
] as const;
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const fieldTone =
  "[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600";
const MONEY_ERROR =
  "Enter exact money with two decimals and quantity with three decimals.";

type InventorySection = (typeof SECTIONS)[number][0];

function viewTabClass(selected: boolean): string {
  const base = "min-h-10 rounded-full px-4 text-sm font-bold";
  return selected
    ? `${base} bg-slate-900 text-white`
    : `${base} border border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200`;
}

function pretty(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function statusTone(status: string): string {
  const tones: Record<string, string> = {
    Active: "bg-emerald-100 text-emerald-800",
    Inactive: "bg-slate-200 text-slate-600",
  };
  return tones[status] ?? "bg-slate-100 text-slate-600";
}

function isLow(row: Product): boolean {
  const onHand = Number(row.quantity_on_hand);
  const threshold = Number(row.low_stock_threshold);
  if (!Number.isFinite(onHand) || !Number.isFinite(threshold)) return false;
  return onHand <= threshold;
}

function quantityTone(value: string): string {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount === 0) return "text-slate-700";
  return amount < 0 ? "text-rose-700" : "text-emerald-700";
}

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function namedFieldError(
  message: string,
  fieldErrors: Record<string, string>,
  labels: Record<string, string>,
): string {
  const parts = Object.entries(fieldErrors).map(
    ([key, value]) => `${labels[key] ?? key}: ${value}`,
  );
  return parts.length > 0 ? parts.join(" ") : message;
}

function StatusPill({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold tracking-wide ${statusTone(status)}`}
    >
      {tx(status)}
    </span>
  );
}

function FactGrid({ rows }: { rows: Array<{ label: string; value: string }> }) {
  const tx = useTx();
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-xs text-slate-500">{tx(row.label)}</dt>
          <dd className="mt-0.5 text-sm font-semibold break-words text-slate-900">
            <IdentifierValue value={row.value} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function LoadingRows({ label }: { label: string }) {
  const tx = useTx();
  return (
    <div className="grid gap-3 p-4 sm:p-5" role="status">
      <p className="text-sm font-medium text-slate-600">{tx(label)}</p>
      <div className="grid gap-2">
        {[0, 1, 2].map((item) => (
          <div
            className="h-14 animate-pulse rounded-xl bg-slate-100"
            key={item}
          />
        ))}
      </div>
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  const tx = useTx();
  return (
    <div className="px-5 py-14 text-center">
      <p className="font-semibold text-slate-800">{tx(title)}</p>
      <p className="mt-1 text-sm text-slate-500">{tx(detail)}</p>
    </div>
  );
}

function ProductFields({
  product,
  categories,
  fields,
}: {
  product: Product | null;
  categories: ProductCategory[];
  fields: Record<string, string>;
}) {
  const tx = useTx();
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field error={fields.barcode} label="Barcode">
        <TextInput defaultValue={product?.barcode ?? ""} name="barcode" />
      </Field>
      <Field error={fields.name} label="Name">
        <TextInput defaultValue={product?.name ?? ""} name="name" required />
      </Field>
      <Field error={fields.category_id} label="Category">
        <SelectInput
          defaultValue={product?.category_id ?? ""}
          name="category_id"
          required
        >
          <option value="">{tx("Select a category")}</option>
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </SelectInput>
      </Field>
      <Field error={fields.unit} label="Unit">
        <TextInput
          defaultValue={product?.unit ?? "PIECE"}
          name="unit"
          required
        />
      </Field>
      <Field error={fields.sale_price} label="Sale price">
        <TextInput
          defaultValue={product?.sale_price ?? ""}
          inputMode="decimal"
          name="sale_price"
          placeholder="0.00"
          required
        />
      </Field>
      <Field error={fields.current_unit_cost} label="Current unit cost">
        <TextInput
          defaultValue={product?.current_unit_cost ?? ""}
          inputMode="decimal"
          name="current_unit_cost"
          placeholder="0.00"
          required
        />
      </Field>
      <Field error={fields.default_unit_cost} label="Default unit cost">
        <TextInput
          defaultValue={product?.default_unit_cost ?? ""}
          inputMode="decimal"
          name="default_unit_cost"
          placeholder="0.00"
          required
        />
      </Field>
      <Field error={fields.low_stock_threshold} label="Low-stock threshold">
        <TextInput
          defaultValue={product?.low_stock_threshold ?? "0.000"}
          inputMode="decimal"
          name="low_stock_threshold"
          placeholder="0.000"
          required
        />
      </Field>
      {product ? (
        <label className="flex min-h-11 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm font-medium text-slate-700 sm:col-span-2">
          <input
            defaultChecked={product.is_active}
            name="is_active"
            type="checkbox"
          />{" "}
          {tx("Active")}{" "}
        </label>
      ) : null}
    </div>
  );
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
  const tx = useTx();
  const [view, setView] = useState<InventorySection>("catalog");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [appliedCategory, setAppliedCategory] = useState("");
  const [movementType, setMovementType] = useState("");
  const [appliedMovement, setAppliedMovement] = useState("|");
  const [selectedId, setSelectedId] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingCategory, setCreatingCategory] = useState(false);
  const [editing, setEditing] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const createDialogRef = useRef<HTMLFormElement>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const createWasOpen = useRef(false);
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
    if (creating) {
      createWasOpen.current = true;
      createDialogRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!createWasOpen.current) return;
    createWasOpen.current = false;
    createButtonRef.current?.focus();
  }, [creating]);

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
            financeError(caught, tx("The product could not be loaded.")),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, products.rows, tx]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  function money(form: FormData, name: string): string | null {
    return canonicalMoney(text(form, name), { allowZero: true });
  }

  function clearFeedback() {
    setFormError("");
    setFields({});
  }

  function closeCreate() {
    if (pending) return;
    setCreating(false);
    clearFeedback();
  }

  function openSection(next: InventorySection) {
    setView(next);
    clearFeedback();
    if (next !== "catalog" && next !== "low") {
      setCreating(false);
      setEditing(false);
      setAdjusting(false);
    }
  }

  function selectProduct(id: string) {
    if (id !== selectedId) setProduct(null);
    setSelectedId(id);
    setEditing(false);
    setAdjusting(false);
    clearFeedback();
  }

  function saveProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sale = money(form, "sale_price");
    const current = money(form, "current_unit_cost");
    const fallback = money(form, "default_unit_cost");
    const threshold = canonicalQuantity(text(form, "low_stock_threshold"), {
      allowZero: true,
    });
    if (!sale || !current || !fallback || !threshold) {
      setFields({ sale_price: MONEY_ERROR });
      setFormError(MONEY_ERROR);
      return;
    }
    const barcode = text(form, "barcode");
    clearFeedback();
    setPending(true);
    const save =
      editing && product
        ? updateProduct(product.id, {
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
        setCreating(false);
        clearFeedback();
        products.reload();
      })
      .catch((caught: unknown) =>
        fail(caught, "The product could not be saved."),
      )
      .finally(() => setPending(false));
  }

  const activeRows = view === "low" ? lowStock : products;
  const showProducts = view === "catalog" || view === "low";
  const categoryById = new Map(
    categories.rows.map((category) => [category.id, category.name]),
  );
  const productById = new Map(
    [...products.rows, ...lowStock.rows].map((row) => [row.id, row]),
  );
  const showProductPaging =
    activeRows.index > 0 || activeRows.hasMore || activeRows.bounded;
  const showMovementPaging =
    movements.index > 0 || movements.hasMore || movements.bounded;
  const showCategoryPaging =
    categories.index > 0 || categories.hasMore || categories.bounded;
  const movementFiltered = appliedMovement !== "|";
  const productCopy =
    view === "low"
      ? {
          title: tx("Low stock"),
          detail: "These products are at or below their threshold.",
          loading: "Loading low stock.",
        }
      : {
          title: tx("Products"),
          detail: "Select a row to see stock, price, and sales.",
          loading: "Loading products.",
        };
  const productEmpty = appliedCategory
    ? view === "low"
      ? {
          title: tx("No low-stock products in this category."),
          detail: "Try another category, or check the full catalog.",
        }
      : {
          title: tx("No products in this category."),
          detail: "Try another category, or add a product.",
        }
    : view === "low"
      ? {
          title: tx("Nothing is low on stock."),
          detail: "Products at or below their threshold will show up here.",
        }
      : {
          title: tx("No products yet."),
          detail: "Add a category, then create the first product.",
        };

  return (
    <div className="grid gap-6">
      <div
        aria-label={tx("Inventory sections")}
        className="flex flex-wrap gap-2"
        role="tablist"
      >
        {SECTIONS.map(([id, label]) => (
          <button
            aria-controls={`${id}-panel`}
            aria-selected={view === id}
            className={viewTabClass(view === id)}
            id={`${id}-tab`}
            key={id}
            onClick={() => openSection(id)}
            role="tab"
            type="button"
          >
            {tx(label)}
          </button>
        ))}
      </div>

      {view === "categories" ? (
        <section
          aria-labelledby="categories-tab"
          className={cardClass}
          id="categories-panel"
          role="tabpanel"
        >
          <div className="border-b border-slate-100 px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2
                className="text-lg font-bold text-slate-900"
                id="product-categories-title"
              >
                {" "}
                {tx("Product categories")}{" "}
              </h2>
              {canManage ? (
                <button
                  className="button button-create"
                  onClick={() => {
                    setCreatingCategory(true);
                    clearFeedback();
                  }}
                  type="button"
                >
                  {" "}
                  {tx("New category")}{" "}
                </button>
              ) : null}
              {categories.rows.length > 0 ? (
                <p className="text-xs font-semibold text-slate-400 tabular-nums">
                  {categories.rows.length}
                </p>
              ) : null}
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
              {" "}
              {tx("Group products here before they go into the catalog.")}{" "}
            </p>
          </div>
          {categories.error ? (
            <div className="px-5 pt-4">
              <Alert message={categories.error} />
            </div>
          ) : null}
          {categories.pending && categories.rows.length === 0 ? (
            <LoadingRows label="Loading categories." />
          ) : null}
          {!categories.pending &&
          !categories.error &&
          categories.rows.length === 0 ? (
            <EmptyState
              detail="Create one before adding products."
              title={tx("No categories yet.")}
            />
          ) : null}
          {categories.rows.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {categories.rows.map((category) => (
                <li
                  className={`flex items-center justify-between gap-4 px-5 py-3.5 ${
                    category.is_active ? "" : "bg-slate-50"
                  }`}
                  key={category.id}
                >
                  <div className="min-w-0">
                    <p className="font-semibold text-slate-900">
                      {category.name}
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-slate-500">
                      {category.code}
                    </p>
                  </div>
                  <StatusPill
                    status={category.is_active ? tx("Active") : tx("Inactive")}
                  />
                </li>
              ))}
            </ul>
          ) : null}
          {showCategoryPaging ? (
            <div className="border-t border-slate-100 px-5 py-4 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={categories.bounded}
                hasMore={categories.hasMore}
                index={categories.index}
                onNext={() => void categories.goNext()}
                onPrevious={() => void categories.goPrevious()}
                pending={categories.pending}
              />
            </div>
          ) : null}
          {canManage && creatingCategory ? (
            <FormDialog
              description="A code is assigned from the name."
              onClose={() => {
                if (pending) return;
                setCreatingCategory(false);
                clearFeedback();
              }}
              onSubmit={(event) => {
                event.preventDefault();
                const formElement = event.currentTarget;
                const form = new FormData(formElement);
                clearFeedback();
                setPending(true);
                void createProductCategory({
                  name: text(form, "name"),
                  is_active: true,
                })
                  .then(() => {
                    formElement.reset();
                    setCreatingCategory(false);
                    clearFeedback();
                    categories.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The category could not be created."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title={tx("New category")}
            >
              <div className={fieldTone}>
                <Alert
                  message={namedFieldError(formError, fields, {
                    name: "Category name",
                    code: "Category code",
                  })}
                />

                <div className="grid gap-4 sm:grid-cols-[minmax(0,20rem)_auto] sm:items-end">
                  <Field error={fields.name} label="Category name">
                    <TextInput name="name" required />
                  </Field>
                  <button
                    className="button button-create"
                    disabled={pending}
                    type="submit"
                  >
                    {" "}
                    {tx("Create category")}{" "}
                  </button>
                </div>
              </div>
            </FormDialog>
          ) : null}
        </section>
      ) : null}

      {showProducts ? (
        <div
          aria-labelledby={`${view}-tab`}
          className="grid gap-6"
          id={`${view}-panel`}
          role="tabpanel"
        >
          {canManage && creating && !editing ? (
            <div
              className="fixed inset-0 z-50 grid overflow-hidden p-2 sm:place-items-center sm:p-6"
              onMouseDown={(event) => {
                if (event.target !== event.currentTarget) return;
                closeCreate();
              }}
              role="presentation"
            >
              <form
                aria-describedby="new-product-description"
                aria-labelledby="new-product-title"
                aria-modal="true"
                className={`flex h-full max-h-full min-h-0 w-full flex-col overflow-y-auto overscroll-contain sm:h-auto sm:max-h-[min(90dvh,48rem)] sm:max-w-2xl ${fieldTone}`}
                onKeyDown={(event) => {
                  if (event.key !== "Escape" || pending) return;
                  event.preventDefault();
                  closeCreate();
                }}
                onSubmit={saveProduct}
                ref={createDialogRef}
                role="dialog"
                tabIndex={-1}
              >
                <div className="sticky top-0 z-10 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
                  <h2
                    className="text-xl font-bold text-slate-900"
                    id="new-product-title"
                  >
                    {" "}
                    {tx("New product")}{" "}
                  </h2>
                  <p
                    className="mt-1 text-sm leading-6 text-slate-500"
                    id="new-product-description"
                  >
                    {" "}
                    {tx(
                      "A SKU is assigned from the name. Prices use two decimals, such as 12.50. Quantities use three, such as 1.000.",
                    )}{" "}
                  </p>
                </div>
                <div className="grid gap-4 px-5 py-4 sm:px-6">
                  <Alert message={formError} />
                  {!categories.pending && categories.rows.length === 0 ? (
                    <p className="text-sm text-slate-500">
                      {" "}
                      {tx("Add a category before creating a product.")}{" "}
                    </p>
                  ) : null}
                  <ProductFields
                    categories={categories.rows}
                    fields={fields}
                    product={null}
                  />
                </div>
                <div className="sticky bottom-0 z-10 mt-auto flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 bg-white px-5 py-4 sm:px-6">
                  <button
                    className="button button-cancel"
                    disabled={pending}
                    onClick={closeCreate}
                    type="button"
                  >
                    {" "}
                    {tx("Cancel")}{" "}
                  </button>
                  <button
                    className={actionButtonClass("Create product")}
                    disabled={pending}
                    type="submit"
                  >
                    {" "}
                    {tx("Create product")}{" "}
                  </button>
                </div>
              </form>
            </div>
          ) : null}

          <div
            className={
              selectedId
                ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(20rem,0.85fr)]"
                : "grid"
            }
          >
            <section aria-busy={activeRows.pending} className={cardClass}>
              <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-bold text-slate-900">
                      {tx(productCopy.title)}
                    </h2>
                    <p className="mt-1 text-sm leading-6 text-slate-500">
                      {productCopy.detail}
                    </p>
                  </div>
                  {canManage && !editing ? (
                    <button
                      className="button button-create shrink-0"
                      onClick={() => {
                        setCreating(true);
                        setEditing(false);
                        setAdjusting(false);
                        clearFeedback();
                      }}
                      ref={createButtonRef}
                      type="button"
                    >
                      {" "}
                      {tx("New product")}{" "}
                    </button>
                  ) : null}
                </div>
                <div className={`mt-4 max-w-sm ${fieldTone}`}>
                  <Field label="Category">
                    <SelectInput
                      onChange={(event) => {
                        const value = event.target.value;
                        setCategoryFilter(value);
                        setAppliedCategory(value);
                      }}
                      value={categoryFilter}
                    >
                      <option value="">{tx("All categories")}</option>
                      {categories.rows.map((category) => (
                        <option key={category.id} value={category.id}>
                          {category.name}
                        </option>
                      ))}
                    </SelectInput>
                  </Field>
                </div>
              </div>

              {categories.error ? (
                <div className="px-5 pt-4">
                  <Alert message={categories.error} />
                </div>
              ) : null}
              {activeRows.pending && activeRows.rows.length === 0 ? (
                <LoadingRows label={productCopy.loading} />
              ) : null}
              {activeRows.pending && activeRows.rows.length > 0 ? (
                <p
                  className="px-5 pt-4 text-sm font-medium text-slate-600"
                  role="status"
                >
                  {productCopy.loading}
                </p>
              ) : null}
              {!activeRows.pending && activeRows.error ? (
                <div className="p-4 sm:p-5">
                  <Alert message={activeRows.error} />
                </div>
              ) : null}
              {!activeRows.pending &&
              !activeRows.error &&
              activeRows.rows.length === 0 ? (
                <EmptyState
                  detail={productEmpty.detail}
                  title={productEmpty.title}
                />
              ) : null}
              {activeRows.rows.length > 0 ? (
                <div
                  className={`overflow-x-auto ${activeRows.pending ? "opacity-60" : ""}`}
                >
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">
                      {view === "low" ? tx("Low stock") : tx("Products")}
                    </caption>
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr>
                        <th
                          className="px-5 py-2.5 text-xs font-semibold"
                          scope="col"
                        >
                          {tx("SKU")}
                        </th>
                        <th
                          className="px-5 py-2.5 text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("Name")}{" "}
                        </th>
                        <th
                          className="px-5 py-2.5 text-right text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("On hand")}{" "}
                        </th>
                        {view === "low" ? (
                          <th
                            className="hidden px-5 py-2.5 text-right text-xs font-semibold sm:table-cell"
                            scope="col"
                          >
                            {" "}
                            {tx("Threshold")}{" "}
                          </th>
                        ) : (
                          <th
                            className="hidden px-5 py-2.5 text-right text-xs font-semibold md:table-cell"
                            scope="col"
                          >
                            {" "}
                            {tx("Sale price")}{" "}
                          </th>
                        )}
                        <th
                          className="px-5 py-2.5 text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("Status")}{" "}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {activeRows.rows.map((row) => {
                        const selected = selectedId === row.id;
                        const low = isLow(row);
                        return (
                          <tr
                            className={
                              selected
                                ? "cursor-pointer bg-[#e7f3ff] shadow-[inset_3px_0_0_#0866ff]"
                                : "cursor-pointer hover:bg-slate-50"
                            }
                            onClick={() => selectProduct(row.id)}
                            key={row.id}
                          >
                            <td className="px-5 py-3">
                              <button
                                aria-current={selected ? "true" : undefined}
                                className="font-mono text-sm font-semibold"
                                onClick={() => selectProduct(row.id)}
                                type="button"
                              >
                                {row.sku}
                              </button>
                            </td>
                            <td className="px-5 py-3">
                              <p className="font-medium text-slate-900">
                                {row.name}
                              </p>
                              <p className="mt-0.5 text-xs text-slate-500">
                                {categoryById.get(row.category_id) ?? "—"}
                              </p>
                            </td>
                            <td className="px-5 py-3 text-right">
                              <span
                                className={`font-semibold tabular-nums ${
                                  low ? "text-amber-800" : "text-slate-900"
                                }`}
                              >
                                {row.quantity_on_hand}
                              </span>
                              {view === "catalog" && low ? (
                                <span className="mt-1 block text-[11px] font-bold tracking-wide text-amber-700 uppercase">
                                  {" "}
                                  {tx("Low")}{" "}
                                </span>
                              ) : null}
                            </td>
                            {view === "low" ? (
                              <td className="hidden px-5 py-3 text-right text-slate-600 tabular-nums sm:table-cell">
                                {row.low_stock_threshold}
                              </td>
                            ) : (
                              <td className="hidden px-5 py-3 text-right font-medium text-slate-700 tabular-nums md:table-cell">
                                {row.sale_price}
                              </td>
                            )}
                            <td className="px-5 py-3">
                              <StatusPill
                                status={
                                  row.is_active ? tx("Active") : tx("Inactive")
                                }
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
              {showProductPaging ? (
                <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
                  <CursorControls
                    bounded={activeRows.bounded}
                    hasMore={activeRows.hasMore}
                    index={activeRows.index}
                    onNext={() => void activeRows.goNext()}
                    onPrevious={() => void activeRows.goPrevious()}
                    pending={activeRows.pending}
                  />
                </div>
              ) : null}
            </section>

            {selectedId ? (
              <section
                aria-labelledby="product-detail-title"
                className="rounded-2xl border border-slate-200 border-t-[3px] border-t-amber-500 bg-white shadow-sm xl:sticky xl:top-24 xl:max-h-[calc(100dvh-7.5rem)] xl:overflow-y-auto"
                id="product-detail"
              >
                {!product && !formError ? (
                  <div className="grid gap-3 p-5" role="status">
                    <h2 className="sr-only" id="product-detail-title">
                      {" "}
                      {tx("Product")}{" "}
                    </h2>
                    <p className="text-sm font-medium text-slate-600">
                      {" "}
                      {tx("Loading the product.")}{" "}
                    </p>
                    <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
                    <div className="grid grid-cols-2 gap-2">
                      <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                      <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                    </div>
                  </div>
                ) : null}
                {!product && formError ? (
                  <div className="p-5">
                    <h2 className="sr-only" id="product-detail-title">
                      {" "}
                      {tx("Product")}{" "}
                    </h2>
                    <Alert message={formError} />
                  </div>
                ) : null}
                {product ? (
                  <>
                    <div className="px-5 pt-5">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-xs font-semibold tracking-[0.16em] text-slate-400 uppercase">
                            {" "}
                            {tx("Product")}{" "}
                          </p>
                          <h2
                            className="mt-1 text-xl font-black tracking-tight break-words text-slate-900"
                            id="product-detail-title"
                          >
                            {product.name}
                          </h2>
                          <p className="mt-0.5 font-mono text-sm text-slate-500">
                            {product.sku}
                          </p>
                        </div>
                        <StatusPill
                          status={
                            product.is_active ? tx("Active") : tx("Inactive")
                          }
                        />
                      </div>
                      <div
                        className={`mt-4 flex items-end justify-between gap-3 rounded-xl px-4 py-3 ring-1 ${
                          isLow(product)
                            ? "bg-amber-50 ring-amber-200"
                            : "bg-slate-50 ring-slate-200/80"
                        }`}
                      >
                        <div>
                          <p className="text-xs font-medium text-slate-500">
                            {" "}
                            {tx("Quantity on hand")}{" "}
                          </p>
                          <p
                            className={`mt-1 text-2xl font-black tracking-tight tabular-nums ${
                              isLow(product)
                                ? "text-amber-800"
                                : "text-slate-900"
                            }`}
                          >
                            {product.quantity_on_hand}
                          </p>
                          {isLow(product) ? (
                            <p className="mt-1 text-xs font-medium text-amber-800">
                              {" "}
                              {tx("At or below the threshold of")}{" "}
                              {product.low_stock_threshold}.
                            </p>
                          ) : null}
                        </div>
                        <p className="pb-1 text-sm font-semibold text-slate-500">
                          {tx(pretty(product.unit))}
                        </p>
                      </div>
                    </div>
                    <div className="px-5 py-4">
                      <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                        {" "}
                        {tx("Pricing")}{" "}
                      </p>
                      <FactGrid
                        rows={[
                          {
                            label: tx("Sale price"),
                            value: product.sale_price,
                          },
                          {
                            label: tx("Current unit cost"),
                            value: product.current_unit_cost,
                          },
                          {
                            label: tx("Default unit cost"),
                            value: product.default_unit_cost,
                          },
                          {
                            label: tx("Low-stock threshold"),
                            value: product.low_stock_threshold,
                          },
                          {
                            label: tx("Category"),
                            value:
                              categoryById.get(product.category_id) ??
                              "Unavailable",
                          },
                          ...(product.barcode
                            ? [{ label: tx("Barcode"), value: product.barcode }]
                            : []),
                        ]}
                      />
                    </div>
                    <div className="border-t border-slate-100 px-5 py-4">
                      <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                        {" "}
                        {tx("Sales totals")}{" "}
                      </p>
                      <FactGrid
                        rows={[
                          {
                            label: tx("Sold quantity"),
                            value: product.sold_quantity,
                          },
                          { label: tx("Revenue"), value: product.revenue },
                          {
                            label: tx("Cost of goods sold"),
                            value: product.cost_of_goods_sold,
                          },
                          {
                            label: tx("Gross profit"),
                            value: product.gross_profit,
                          },
                        ]}
                      />

                      <p className="mt-3 text-xs leading-5 text-slate-500">
                        {" "}
                        {tx(
                          "These stock and money totals are returned by the server.",
                        )}{" "}
                      </p>
                    </div>
                    {(canManage || canAdjust) && !editing && !adjusting ? (
                      <div className="flex flex-wrap gap-2 border-t border-slate-100 px-5 py-4">
                        {canManage ? (
                          <button
                            className="button button-edit"
                            onClick={() => {
                              setEditing(true);
                              setCreating(false);
                              setAdjusting(false);
                              clearFeedback();
                            }}
                            type="button"
                          >
                            {" "}
                            {tx("Edit product")}{" "}
                          </button>
                        ) : null}
                        {canAdjust ? (
                          <button
                            className="button"
                            onClick={() => {
                              setAdjusting(true);
                              setEditing(false);
                              clearFeedback();
                            }}
                            type="button"
                          >
                            {" "}
                            {tx("Adjust stock")}{" "}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {editing && canManage ? (
                      <FormDialog
                        description={tx(
                          "SKU {sku}. Prices use two decimals. Quantities use three.",
                          { sku: product.sku },
                        )}
                        key={product.id}
                        onClose={() => {
                          if (pending) return;
                          setEditing(false);
                          clearFeedback();
                        }}
                        onSubmit={saveProduct}
                        pending={pending}
                        title={tx("Edit product")}
                        wide
                      >
                        <Alert message={formError} />
                        <div className={fieldTone}>
                          <ProductFields
                            categories={categories.rows}
                            fields={fields}
                            product={product}
                          />

                          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
                            <button
                              className="button button-cancel"
                              disabled={pending}
                              onClick={() => {
                                setEditing(false);
                                clearFeedback();
                              }}
                              type="button"
                            >
                              {" "}
                              {tx("Cancel")}{" "}
                            </button>
                            <button
                              className={actionButtonClass("Save product")}
                              disabled={pending}
                              type="submit"
                            >
                              {" "}
                              {tx("Save product")}{" "}
                            </button>
                          </div>
                        </div>
                      </FormDialog>
                    ) : null}
                    {adjusting && canAdjust ? (
                      <FormDialog
                        description="Use three decimals, such as 1.000. A leading minus removes stock."
                        onClose={() => {
                          if (pending) return;
                          setAdjusting(false);
                          clearFeedback();
                        }}
                        onSubmit={(event) => {
                          event.preventDefault();
                          const form = new FormData(event.currentTarget);
                          const quantity = canonicalQuantity(
                            text(form, "quantity"),
                            { allowNegative: true },
                          );
                          const movement = text(form, "movement_type");
                          if (
                            !quantity ||
                            quantity === "0.000" ||
                            !MANUAL_TYPES.includes(movement as MovementType)
                          ) {
                            setFields({
                              quantity:
                                "Enter a non-zero quantity with three decimals.",
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
                          clearFeedback();
                          setPending(true);
                          void adjustStock(body)
                            .then((result) => {
                              setProduct(result.product);
                              setAdjusting(false);
                              clearFeedback();
                              products.reload();
                              movements.reload();
                            })
                            .catch((caught: unknown) =>
                              fail(caught, "The stock could not be adjusted."),
                            )
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Manual stock adjustment")}
                      >
                        <Alert message={formError} />
                        <div className={fieldTone}>
                          <div className="grid gap-4 sm:grid-cols-2">
                            <Field error={fields.quantity} label="Quantity">
                              <TextInput
                                inputMode="decimal"
                                name="quantity"
                                placeholder="0.000"
                                required
                              />
                            </Field>
                            <Field
                              error={fields.movement_type}
                              label="Movement type"
                            >
                              <SelectInput name="movement_type" required>
                                <option value="">{tx("Select a type")}</option>
                                {MANUAL_TYPES.map((item) => (
                                  <option key={item} value={item}>
                                    {tx(pretty(item))}
                                  </option>
                                ))}
                              </SelectInput>
                            </Field>
                            <Field
                              error={fields.business_date}
                              label="Business date"
                            >
                              <TextInput
                                defaultValue={defaultDate}
                                name="business_date"
                                required
                                type="date"
                              />
                            </Field>
                            <div className="sm:col-span-2">
                              <ReasonField
                                error={fields.reason}
                                label="Reason"
                              />
                            </div>
                          </div>
                          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 pt-3">
                            <button
                              className="button button-cancel"
                              disabled={pending}
                              onClick={() => {
                                setAdjusting(false);
                                clearFeedback();
                              }}
                              type="button"
                            >
                              {" "}
                              {tx("Back")}{" "}
                            </button>
                            <button
                              className={actionButtonClass(
                                "Confirm adjustment",
                              )}
                              disabled={pending}
                              type="submit"
                            >
                              {" "}
                              {tx("Confirm adjustment")}{" "}
                            </button>
                          </div>
                        </div>
                      </FormDialog>
                    ) : null}
                  </>
                ) : null}
              </section>
            ) : null}
          </div>
        </div>
      ) : null}

      {view === "movements" ? (
        <section
          aria-labelledby="movements-tab"
          className={cardClass}
          id="movements-panel"
          role="tabpanel"
        >
          <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
            <h2 className="text-lg font-bold text-slate-900">
              {" "}
              {tx("Stock movements")}{" "}
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
              {" "}
              {tx("Purchases, sales, damage, and manual corrections.")}{" "}
            </p>
            <form
              className={`mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] xl:items-end ${fieldTone}`}
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
                  <option value="">{tx("Any product")}</option>
                  {products.rows.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.sku} — {row.name}
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
                  <option value="">{tx("Any type")}</option>
                  {HISTORY_TYPES.map((item) => (
                    <option key={item} value={item}>
                      {tx(pretty(item))}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <Field label="Business date">
                <TextInput name="business_date" type="date" />
              </Field>
              <div className="flex items-end justify-end sm:col-span-2 xl:col-span-1">
                <button className="button" type="submit">
                  {" "}
                  {tx("Apply filters")}{" "}
                </button>
              </div>
            </form>
          </div>
          {movements.pending && movements.rows.length === 0 ? (
            <LoadingRows label="Loading stock movements." />
          ) : null}
          {movements.pending && movements.rows.length > 0 ? (
            <p
              className="px-5 pt-4 text-sm font-medium text-slate-600"
              role="status"
            >
              {" "}
              {tx("Loading stock movements.")}{" "}
            </p>
          ) : null}
          {!movements.pending && movements.error ? (
            <div className="p-4 sm:p-5">
              <Alert message={movements.error} />
            </div>
          ) : null}
          {!movements.pending &&
          !movements.error &&
          movements.rows.length === 0 ? (
            <EmptyState
              detail={
                movementFiltered
                  ? tx("Try another product, type, or date.")
                  : tx("Purchases, sales, and adjustments will show up here.")
              }
              title={
                movementFiltered
                  ? tx("No movements match these filters.")
                  : tx("No stock movements yet.")
              }
            />
          ) : null}
          {movements.rows.length > 0 ? (
            <div
              className={`overflow-x-auto ${movements.pending ? "opacity-60" : ""}`}
            >
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{tx("Stock movements")}</caption>
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Product")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Type")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-right text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Quantity")}{" "}
                    </th>
                    <th
                      className="hidden px-5 py-2.5 text-right text-xs font-semibold sm:table-cell"
                      scope="col"
                    >
                      {" "}
                      {tx("Unit cost")}{" "}
                    </th>
                    <th
                      className="hidden px-5 py-2.5 text-xs font-semibold md:table-cell"
                      scope="col"
                    >
                      {" "}
                      {tx("Date")}{" "}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {movements.rows.map((row) => {
                    const known = productById.get(row.product_id);
                    return (
                      <tr className="hover:bg-slate-50" key={row.id}>
                        <td className="px-5 py-3">
                          {known ? (
                            <div>
                              <p className="font-mono text-xs font-semibold text-slate-800">
                                {known.sku}
                              </p>
                              <p className="mt-0.5 text-xs text-slate-500">
                                {known.name}
                              </p>
                            </div>
                          ) : (
                            <IdentifierValue value={row.product_id} />
                          )}
                        </td>
                        <td className="px-5 py-3">
                          <p className="font-medium text-slate-900">
                            {tx(pretty(row.movement_type))}
                          </p>
                          {row.reason ? (
                            <p className="mt-0.5 max-w-xs text-xs leading-5 text-slate-500">
                              {row.reason}
                            </p>
                          ) : null}
                        </td>
                        <td
                          className={`px-5 py-3 text-right font-semibold tabular-nums ${quantityTone(row.quantity)}`}
                        >
                          {row.quantity}
                        </td>
                        <td className="hidden px-5 py-3 text-right text-slate-600 tabular-nums sm:table-cell">
                          {row.unit_cost_snapshot}
                        </td>
                        <td className="hidden px-5 py-3 whitespace-nowrap text-slate-600 md:table-cell">
                          {row.business_date}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {showMovementPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={movements.bounded}
                hasMore={movements.hasMore}
                index={movements.index}
                onNext={() => void movements.goNext()}
                onPrevious={() => void movements.goPrevious()}
                pending={movements.pending}
              />
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
