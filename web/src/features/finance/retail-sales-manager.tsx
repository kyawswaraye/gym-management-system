"use client";
import { useTx } from "@/i18n/use-tx";

import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { FormDialog } from "@/components/form-dialog";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveRetailSale,
  cancelRetailSale,
  createRetailSale,
  getRetailSale,
  listProducts,
  listRetailSales,
  rejectRetailSale,
  returnRetailSale,
  submitRetailSale,
  updateRetailSale,
  voidRetailSale,
} from "@/features/finance/client";
import { fieldMap, financeError } from "@/features/finance/errors";
import {
  canonicalMoney,
  canonicalQuantity,
  formatCents,
  moneyCents,
  previewLineAmount,
} from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { listMembers } from "@/features/operations/client";
import { actionButtonClass } from "@/lib/ui/action-button";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  Member,
  PaymentMethod,
  Product,
  RetailSale,
  RetailSaleApproval,
  RetailSaleLineWriteRequest,
  RetailSaleReturn,
  RetailSaleVoid,
  ReturnRetailSaleRequest,
  VoidRetailSaleRequest,
} from "@/types/api";

const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;
const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const fieldTone =
  "[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600";
const STATUS_FILTERS = [
  ["", "All"],
  ["DRAFT", "Draft"],
  ["PENDING_APPROVAL", "Pending"],
  ["POSTED", "Posted"],
  ["REJECTED", "Rejected"],
  ["CANCELLED", "Cancelled"],
  ["VOID", "Void"],
] as const;

type LineDraft = {
  key: string;
  productId: string;
  quantity: string;
  discount: string;
};

type SaleAction =
  "submit" | "approve" | "reject" | "cancel" | "void" | "return" | null;

function pretty(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function statusTone(status: string): string {
  const tones: Record<string, string> = {
    DRAFT: "bg-slate-100 text-slate-700",
    PENDING_APPROVAL: "bg-amber-100 text-amber-800",
    POSTED: "bg-emerald-100 text-emerald-800",
    REJECTED: "bg-red-100 text-red-700",
    CANCELLED: "bg-slate-200 text-slate-600",
    VOID: "bg-rose-100 text-rose-700",
  };
  return tones[status] ?? "bg-slate-100 text-slate-600";
}

function statusLabel(status: string): string {
  if (status === "PENDING_APPROVAL") return "Pending";
  return pretty(status);
}

function StatusPill({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold tracking-wide ${statusTone(status)}`}
    >
      <span className="sr-only">{tx(statusLabel(status))}</span>
      {tx(statusLabel(status))}
    </span>
  );
}

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function quantityScale(value: string): bigint | null {
  const canonical = canonicalQuantity(value, { allowZero: true });
  if (!canonical || canonical.startsWith("-")) return null;
  return BigInt(canonical.replace(".", ""));
}

function blankLine(): LineDraft {
  return {
    key: crypto.randomUUID(),
    productId: "",
    quantity: "1.000",
    discount: "0.00",
  };
}

function memberLabel(
  members: readonly Member[],
  memberId: string | null,
  tx: (text: string) => string,
): string {
  if (!memberId) return tx("Walk-in");
  const member = members.find((item) => item.id === memberId);
  if (!member) return tx("Member");
  return `${member.member_number} · ${member.first_name} ${member.last_name}`;
}

function lineSummary(
  sale: RetailSale,
  tx: (text: string, vars?: Record<string, string | number>) => string,
): string {
  const names = sale.lines
    .map((line) => line.product_name)
    .filter((name) => name.trim());
  if (names.length === 0) return tx("No products");
  if (names.length === 1) return names[0] ?? tx("1 product");
  return tx("{name} + {count} more", {
    name: names[0] ?? "",
    count: names.length - 1,
  });
}

function previewTotal(
  lines: readonly LineDraft[],
  products: readonly Product[],
): string | null {
  let total = 0n;
  let count = 0;
  for (const line of lines) {
    const product = products.find((item) => item.id === line.productId);
    if (!product) continue;
    const preview = previewLineAmount(
      line.quantity,
      product.sale_price,
      line.discount,
    );
    const cents = preview ? moneyCents(preview) : null;
    if (cents === null) continue;
    total += cents;
    count += 1;
  }
  if (count === 0) return null;
  return formatCents(total);
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

function ActionPanel({
  title,
  hint,
  pending,
  error,
  confirmLabel,
  onSubmit,
  onCancel,
  children,
}: {
  title: string;
  hint?: string;
  pending: boolean;
  error: string;
  confirmLabel: string;
  onSubmit: (form: FormData) => void;
  onCancel: () => void;
  children?: ReactNode;
}) {
  const tx = useTx();
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(new FormData(event.currentTarget));
  }

  return (
    <FormDialog
      description={hint}
      onClose={onCancel}
      onSubmit={submit}
      pending={pending}
      title={title}
    >
      <Alert message={error} />
      <div className={fieldTone}>{children}</div>
      <div>
        <button
          className="button button-cancel"
          disabled={pending}
          onClick={onCancel}
          type="button"
        >
          {" "}
          {tx("Back")}{" "}
        </button>
        <button
          className={actionButtonClass(confirmLabel)}
          disabled={pending}
          type="submit"
        >
          {tx(confirmLabel)}
        </button>
      </div>
    </FormDialog>
  );
}

function StatusFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const tx = useTx();
  const groupRef = useRef<HTMLDivElement>(null);

  function move(event: KeyboardEvent<HTMLDivElement>, delta: number) {
    event.preventDefault();
    const index = STATUS_FILTERS.findIndex(([item]) => item === value);
    const next =
      STATUS_FILTERS[
        (index + delta + STATUS_FILTERS.length) % STATUS_FILTERS.length
      ]?.[0] ?? "";
    onChange(next);
    window.requestAnimationFrame(() => {
      groupRef.current
        ?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
        ?.focus();
    });
  }

  return (
    <div
      aria-label={tx("Status")}
      className="flex flex-wrap gap-1.5"
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          move(event, 1);
        }
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          move(event, -1);
        }
      }}
      ref={groupRef}
      role="radiogroup"
    >
      {STATUS_FILTERS.map(([item, label]) => {
        const selected = value === item;
        return (
          <button
            aria-checked={selected}
            className={
              selected
                ? "rounded-full bg-slate-900 px-3 py-1.5 text-xs font-bold text-white shadow-sm"
                : "rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-slate-200 hover:bg-slate-200"
            }
            key={label}
            onClick={() => onChange(item)}
            role="radio"
            tabIndex={selected ? 0 : -1}
            type="button"
          >
            {tx(label)}
          </button>
        );
      })}
    </div>
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

export function RetailSalesManager({
  canCreate,
  canUpdate,
  canSubmit,
  canApprove,
  canVoid,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  currency: string;
  defaultDate: string;
}) {
  const tx = useTx();
  const [applied, setApplied] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [sale, setSale] = useState<RetailSale | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [lines, setLines] = useState<LineDraft[]>([blankLine()]);
  const [memberId, setMemberId] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("CASH");
  const [businessDate, setBusinessDate] = useState(defaultDate);
  const [reference, setReference] = useState("");
  const [editing, setEditing] = useState(false);
  const [creatingSale, setCreatingSale] = useState(false);
  const [action, setAction] = useState<SaleAction>(null);
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [approval, setApproval] = useState<RetailSaleApproval | null>(null);
  const [voidResult, setVoidResult] = useState<RetailSaleVoid | null>(null);
  const [returnResult, setReturnResult] = useState<RetailSaleReturn | null>(
    null,
  );
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const sales = useCursorPages(applied, (after) =>
    listRetailSales({
      ...(applied ? { status: applied } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const sessions = useOpenCashSessions(
    action === "approve" && sale
      ? sale.business_date
      : action === "void" ||
          (action === "return" && sale?.payment_method === "CASH")
        ? correctionDate
        : businessDate,
  );

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void Promise.all([
        listMembers({ limit: 25 }),
        listProducts({ is_active: "true", limit: "100" }),
      ])
        .then(([memberPage, productPage]) => {
          if (!active) return;
          setMembers(memberPage.data);
          setProducts(productPage.data);
        })
        .catch(() => {
          if (!active) return;
          setMembers([]);
          setProducts([]);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getRetailSale(selectedId)
        .then((row) => {
          if (!active) return;
          setSale(row);
          setLines(
            row.lines.map((line) => ({
              key: line.id,
              productId: line.product_id,
              quantity: line.quantity,
              discount: line.discount,
            })),
          );
          setMemberId(row.member_id ?? "");
          setMethod(row.payment_method as PaymentMethod);
          setBusinessDate(row.business_date);
          setReference(row.reference_number);
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setSale(null);
          setFormError(
            financeError(caught, tx("The sale could not be loaded.")),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, sales.rows, tx]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  function clearOutcome() {
    setApproval(null);
    setVoidResult(null);
    setReturnResult(null);
  }

  function selectSale(id: string) {
    if (id !== selectedId) clearOutcome();
    setSelectedId(id);
    setAction(null);
    setEditing(false);
    setFormError("");
    setFields({});
  }

  function startCreate() {
    setEditing(false);
    setCreatingSale(true);
    setAction(null);
    setFormError("");
    setFields({});
    setLines([blankLine()]);
    setMemberId("");
    setMethod("CASH");
    setBusinessDate(defaultDate);
    setReference("");
    setSelectedId("");
    setSale(null);
    clearOutcome();
  }

  function linePayload(): RetailSaleLineWriteRequest[] | null {
    if (lines.length < 1 || lines.length > 100) {
      setFormError("Enter from 1 to 100 product lines.");
      return null;
    }
    const ids = lines.map((line) => line.productId);
    if (ids.some((id) => !id) || new Set(ids).size !== ids.length) {
      setFormError("Choose each product once.");
      return null;
    }
    const payload: RetailSaleLineWriteRequest[] = [];
    for (const line of lines) {
      const quantity = canonicalQuantity(line.quantity);
      const discount = canonicalMoney(line.discount, { allowZero: true });
      if (!quantity || !discount) {
        setFields({
          quantity:
            "Enter quantity with three decimals and discount with two decimals.",
        });
        return null;
      }
      payload.push({ product_id: line.productId, quantity, discount });
    }
    return payload;
  }

  const editorOpen =
    (canCreate && creatingSale && !editing) ||
    (editing && canUpdate && sale?.status === "DRAFT");
  const showPaging = sales.index > 0 || sales.hasMore || sales.bounded;
  const estimate = previewTotal(lines, products);
  const canEditDraft = sale?.status === "DRAFT" && canUpdate;
  const canSubmitDraft = sale?.status === "DRAFT" && canSubmit;
  const canDecide = sale?.status === "PENDING_APPROVAL" && canApprove;
  const canCancelDraft = sale?.status === "DRAFT" && canUpdate;
  const canVoidPosted =
    sale?.status === "POSTED" && canVoid && sale.return_count === 0;
  const canReturnPosted = sale?.status === "POSTED" && canVoid;
  const hasActions = Boolean(
    canEditDraft ||
    canSubmitDraft ||
    canDecide ||
    canCancelDraft ||
    canVoidPosted ||
    canReturnPosted,
  );

  return (
    <div className="grid gap-6">
      {editorOpen ? (
        <FormDialog
          description={
            editing
              ? tx("Changes stay on this draft until you submit it.")
              : tx(
                  "Save a draft first. Prices and totals are calculated by the server.",
                )
          }
          onClose={() => {
            if (pending) return;
            setCreatingSale(false);
            setEditing(false);
            setFormError("");
          }}
          onSubmit={(event) => {
            event.preventDefault();
            const payload = linePayload();
            if (!payload) return;
            const body = {
              business_date: businessDate,
              payment_method: method,
              lines: payload,
              ...(memberId ? { member_id: memberId } : { member_id: null }),
              ...(reference ? { reference_number: reference } : {}),
            };
            setPending(true);
            const save =
              editing && sale
                ? updateRetailSale(sale.id, body)
                : createRetailSale({
                    business_date: businessDate,
                    payment_method: method,
                    lines: payload,
                    ...(memberId ? { member_id: memberId } : {}),
                    ...(reference ? { reference_number: reference } : {}),
                  });
            void save
              .then((saved) => {
                setSale(saved);
                setSelectedId(saved.id);
                setEditing(false);
                setCreatingSale(false);
                clearOutcome();
                sales.reload();
              })
              .catch((caught: unknown) =>
                fail(caught, "The sale could not be saved."),
              )
              .finally(() => setPending(false));
          }}
          pending={pending}
          title={editing ? tx("Edit draft sale") : tx("New retail sale")}
          wide
        >
          <div className={`grid gap-4 ${fieldTone}`}>
            <Alert message={formError} />
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Field label="Member">
                  <SelectInput
                    onChange={(event) => setMemberId(event.target.value)}
                    value={memberId}
                  >
                    <option value="">{tx("Walk-in")}</option>
                    {members.map((member) => (
                      <option key={member.id} value={member.id}>
                        {member.member_number} — {member.first_name}{" "}
                        {member.last_name}
                      </option>
                    ))}
                  </SelectInput>
                </Field>
              </div>
              <Field label="Business date">
                <TextInput
                  onChange={(event) => setBusinessDate(event.target.value)}
                  type="date"
                  value={businessDate}
                />
              </Field>
              <Field label="Payment method">
                <SelectInput
                  onChange={(event) =>
                    setMethod(event.target.value as PaymentMethod)
                  }
                  value={method}
                >
                  {METHODS.map((item) => (
                    <option key={item} value={item}>
                      {tx(pretty(item))}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <div className="sm:col-span-2">
                <Field label="Reference">
                  <TextInput
                    onChange={(event) => setReference(event.target.value)}
                    value={reference}
                  />
                </Field>
              </div>
            </div>
            <div className="grid gap-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                  {" "}
                  {tx("Products")}{" "}
                </p>
                <button
                  className="button button-cancel button-sm"
                  disabled={lines.length >= 100}
                  onClick={() =>
                    setLines((current) => [...current, blankLine()])
                  }
                  type="button"
                >
                  {" "}
                  {tx("Add product line")}{" "}
                </button>
              </div>
              {lines.map((line, index) => {
                const product = products.find(
                  (item) => item.id === line.productId,
                );
                const preview = product
                  ? previewLineAmount(
                      line.quantity,
                      product.sale_price,
                      line.discount,
                    )
                  : null;
                return (
                  <div
                    className="rounded-xl border border-slate-200 bg-slate-50/80 p-3"
                    key={line.key}
                  >
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-xs font-bold tracking-wide text-slate-400 uppercase">
                        {" "}
                        {tx("Line")} {index + 1}
                      </p>
                      <button
                        className="button button-delete button-sm"
                        disabled={lines.length === 1}
                        onClick={() =>
                          setLines((current) =>
                            current.filter((item) => item.key !== line.key),
                          )
                        }
                        type="button"
                      >
                        {" "}
                        {tx("Remove line")}{" "}
                      </button>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="sm:col-span-2">
                        <Field label="Product">
                          <SelectInput
                            onChange={(event) =>
                              setLines((current) =>
                                current.map((item) =>
                                  item.key === line.key
                                    ? { ...item, productId: event.target.value }
                                    : item,
                                ),
                              )
                            }
                            value={line.productId}
                          >
                            <option value="">{tx("Select a product")}</option>
                            {products.map((item) => (
                              <option key={item.id} value={item.id}>
                                {item.sku} — {item.name} · {item.sale_price}
                              </option>
                            ))}
                          </SelectInput>
                        </Field>
                      </div>
                      <Field error={fields.quantity} label="Quantity">
                        <TextInput
                          inputMode="decimal"
                          onChange={(event) =>
                            setLines((current) =>
                              current.map((item) =>
                                item.key === line.key
                                  ? { ...item, quantity: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          value={line.quantity}
                        />
                      </Field>
                      <Field label={tx("Discount ({currency})", { currency: tx(currency) })}>
                        <TextInput
                          inputMode="decimal"
                          onChange={(event) =>
                            setLines((current) =>
                              current.map((item) =>
                                item.key === line.key
                                  ? { ...item, discount: event.target.value }
                                  : item,
                              ),
                            )
                          }
                          value={line.discount}
                        />
                      </Field>
                    </div>
                    {preview ? (
                      <p className="mt-3 text-sm text-slate-600">
                        {product ? (
                          <span className="mr-2 font-medium text-slate-500">
                            {product.sale_price} {currency} {tx("each.")}
                          </span>
                        ) : null}{" "}
                        {tx("Preview only:")} {preview}
                        {tx(
                          ". The server calculates the line snapshots and sale totals.",
                        )}{" "}
                      </p>
                    ) : null}
                  </div>
                );
              })}
              {estimate ? (
                <div className="flex items-end justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                  <div>
                    <p className="text-xs font-medium text-slate-500">
                      {" "}
                      {tx("Estimated total")}{" "}
                    </p>
                    <p className="mt-1 text-2xl font-black tracking-tight text-slate-900 tabular-nums">
                      {estimate}
                    </p>
                  </div>
                  <p className="pb-1 text-sm font-semibold text-slate-500">
                    {currency}
                  </p>
                </div>
              ) : null}
            </div>
          </div>
          <div>
            <button
              className="button button-edit"
              disabled={pending}
              type="submit"
            >
              {editing ? tx("Save sale changes") : tx("Save draft")}
            </button>
          </div>
        </FormDialog>
      ) : null}

      <div
        className={
          sale || selectedId
            ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(22rem,0.9fr)]"
            : "grid"
        }
      >
        <section
          aria-busy={sales.pending}
          aria-labelledby="retail-ledger-title"
          className={cardClass}
        >
          <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2
                  className="text-lg font-bold text-slate-900"
                  id="retail-ledger-title"
                >
                  {" "}
                  {tx("Sales")}{" "}
                </h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  {" "}
                  {tx("Select a sale to review, submit, or approve it.")}{" "}
                </p>
              </div>
              {canCreate ? (
                <button
                  className="button button-create shrink-0"
                  onClick={startCreate}
                  type="button"
                >
                  {" "}
                  {tx("New retail sale")}{" "}
                </button>
              ) : null}
            </div>
            <div className="mt-4">
              <StatusFilter onChange={setApplied} value={applied} />
            </div>
          </div>

          {sales.pending && sales.rows.length === 0 ? (
            <LoadingRows label="Loading retail sales." />
          ) : null}
          {sales.pending && sales.rows.length > 0 ? (
            <p
              className="px-5 pt-4 text-sm font-medium text-slate-600"
              role="status"
            >
              {" "}
              {tx("Loading retail sales.")}{" "}
            </p>
          ) : null}
          {!sales.pending && sales.error ? (
            <div className="p-4 sm:p-5">
              <Alert message={sales.error} />
            </div>
          ) : null}
          {!sales.pending && !sales.error && sales.rows.length === 0 ? (
            <EmptyState
              detail={
                applied
                  ? tx("Try another status.")
                  : tx("Drafts you save will show up here.")
              }
              title={
                applied
                  ? tx("No sales match this status.")
                  : tx("No retail sales yet.")
              }
            />
          ) : null}
          {sales.rows.length > 0 ? (
            <div
              className={`overflow-x-auto ${sales.pending ? "opacity-60" : ""}`}
            >
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{tx("Retail sales")}</caption>
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Sale")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-right text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Total")}{" "}
                    </th>
                    <th
                      className="hidden px-5 py-2.5 text-xs font-semibold md:table-cell"
                      scope="col"
                    >
                      {" "}
                      {tx("Method")}{" "}
                    </th>
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
                  {sales.rows.map((row) => {
                    const selected = selectedId === row.id;
                    return (
                      <tr
                        className={
                          selected
                            ? "bg-[#e7f3ff] shadow-[inset_3px_0_0_#0866ff]"
                            : "hover:bg-slate-50"
                        }
                        key={row.id}
                      >
                        <td className="px-5 py-3">
                          <button
                            aria-current={selected ? "true" : undefined}
                            aria-label={row.business_date}
                            className="block text-left font-semibold"
                            onClick={() => selectSale(row.id)}
                            type="button"
                          >
                            <span className="font-semibold">
                              {row.business_date}
                            </span>
                            <span className="mt-0.5 block text-xs font-normal">
                              {lineSummary(row, tx)}
                            </span>
                          </button>
                        </td>
                        <td className="px-5 py-3 text-right font-semibold whitespace-nowrap text-slate-900 tabular-nums">
                          {row.total_amount}{" "}
                          <span className="font-medium text-slate-400">
                            {tx(row.currency)}
                          </span>
                        </td>
                        <td className="hidden px-5 py-3 whitespace-nowrap text-slate-600 md:table-cell">
                          {tx(pretty(row.payment_method))}
                        </td>
                        <td className="px-5 py-3">
                          <StatusPill status={row.status} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
          {showPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={sales.bounded}
                hasMore={sales.hasMore}
                index={sales.index}
                onNext={() => void sales.goNext()}
                onPrevious={() => void sales.goPrevious()}
                pending={sales.pending}
              />
            </div>
          ) : null}
        </section>

        {sale || selectedId ? (
          <section
            aria-labelledby="sale-detail-title"
            className={`${cardClass} border-t-[3px] border-t-amber-500 xl:sticky xl:top-4`}
          >
            <h2 className="sr-only" id="sale-detail-title">
              {" "}
              {tx("Sale detail")}{" "}
            </h2>
            {!sale && !formError ? (
              <div className="grid gap-3 p-5" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading the sale.")}{" "}
                </p>
                <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
                <div className="grid grid-cols-2 gap-2">
                  <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                  <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                </div>
              </div>
            ) : null}
            {!sale && formError ? (
              <div className="p-5">
                <Alert message={formError} />
              </div>
            ) : null}
            {sale ? (
              <>
                <div className="px-5 pt-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold tracking-[0.16em] text-slate-400 uppercase">
                        {" "}
                        {tx("Sale")}{" "}
                      </p>
                      <p className="mt-1 text-sm text-slate-500">
                        {sale.business_date} · {tx(pretty(sale.payment_method))} ·{" "}
                        {memberLabel(members, sale.member_id, tx)}
                      </p>
                    </div>
                    <StatusPill status={sale.status} />
                  </div>
                  <div className="mt-4 flex items-end justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                    <div>
                      <p className="text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Total")}{" "}
                      </p>
                      <p className="mt-1 text-2xl font-black tracking-tight text-slate-900 tabular-nums">
                        {sale.total_amount}
                      </p>
                    </div>
                    <p className="pb-1 text-sm font-semibold text-slate-500">
                      {tx(sale.currency)}
                    </p>
                  </div>
                </div>
                {formError && action === null && !editorOpen ? (
                  <div className="px-5 pt-4">
                    <Alert message={formError} />
                  </div>
                ) : null}
                <div className="px-5 py-4">
                  <FactGrid
                    rows={[
                      { label: tx("Subtotal"), value: sale.subtotal },
                      { label: tx("Discount"), value: sale.discount_total },
                      { label: "COGS", value: sale.cogs_total },
                      {
                        label: tx("Gross profit"),
                        value: sale.gross_profit_total,
                      },
                      {
                        label: tx("Returned amount"),
                        value: sale.returned_amount,
                      },
                      {
                        label: tx("Returns"),
                        value: String(sale.return_count),
                      },
                      {
                        label: tx("Payment"),
                        value: sale.payment_id ?? "None",
                      },
                      {
                        label: tx("Receipt"),
                        value: sale.receipt_id ?? "None",
                      },
                      ...(sale.reference_number
                        ? [
                            {
                              label: tx("Reference"),
                              value: sale.reference_number,
                            },
                          ]
                        : []),
                    ]}
                  />
                </div>
                <div className="border-t border-slate-100 px-5 py-4">
                  <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                    {" "}
                    {tx("Products")}{" "}
                  </p>
                  <ul className="grid gap-2">
                    {sale.lines.map((line) => (
                      <li
                        className="rounded-xl bg-slate-50 px-3 py-3 ring-1 ring-slate-200/80"
                        key={line.id}
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <p className="font-semibold text-slate-900">
                            {line.product_name}
                          </p>
                          <p className="text-sm font-semibold text-slate-900 tabular-nums">
                            {line.line_revenue}
                          </p>
                        </div>
                        <p className="mt-0.5 text-xs text-slate-400">
                          {line.sku}
                        </p>
                        <p className="mt-2 text-xs leading-5 text-slate-600">
                          {tx("sold")} {line.quantity}
                          <span className="px-1.5 text-slate-300">·</span>
                          {tx("returned")} {line.returned_quantity}
                          <span className="px-1.5 text-slate-300">·</span>
                          {tx("remaining")} {line.remaining_quantity}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
                {sale.rejection_reason ? (
                  <div className="px-5 pb-4">
                    <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-900">
                      <span className="mb-1 block text-xs font-semibold text-red-700">
                        {" "}
                        {tx("Rejection reason")}{" "}
                      </span>
                      {sale.rejection_reason}
                    </p>
                  </div>
                ) : null}
                {sale.void_reason ? (
                  <div className="px-5 pb-4">
                    <p className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                      <span className="mb-1 block text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Void reason")}{" "}
                      </span>
                      {sale.void_reason}
                    </p>
                  </div>
                ) : null}
                {hasActions ? (
                  <div className="flex flex-wrap gap-2 border-t border-slate-100 px-5 py-4">
                    {canEditDraft ? (
                      <button
                        className="button button-edit"
                        onClick={() => {
                          setEditing(true);
                          setCreatingSale(false);
                          setAction(null);
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Edit draft")}{" "}
                      </button>
                    ) : null}
                    {canSubmitDraft ? (
                      <button
                        className="button"
                        onClick={() => {
                          setAction("submit");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Submit")}{" "}
                      </button>
                    ) : null}
                    {canDecide ? (
                      <button
                        className="button button-success"
                        onClick={() => {
                          setAction("approve");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Approve")}{" "}
                      </button>
                    ) : null}
                    {canDecide ? (
                      <button
                        className="button button-warn"
                        onClick={() => {
                          setAction("reject");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Reject")}{" "}
                      </button>
                    ) : null}
                    {canCancelDraft ? (
                      <button
                        className="button button-cancel"
                        onClick={() => {
                          setAction("cancel");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Cancel draft")}{" "}
                      </button>
                    ) : null}
                    {canVoidPosted ? (
                      <button
                        className="button button-delete"
                        onClick={() => {
                          setCorrectionDate(defaultDate);
                          setAction("void");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Void sale")}{" "}
                      </button>
                    ) : null}
                    {canReturnPosted ? (
                      <button
                        className="button button-warn"
                        onClick={() => {
                          setCorrectionDate(defaultDate);
                          setAction("return");
                          setFormError("");
                        }}
                        type="button"
                      >
                        {" "}
                        {tx("Partial return")}{" "}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                {action === "submit" ? (
                  <ActionPanel
                    confirmLabel="Confirm submit"
                    error={formError}
                    hint="This sends the draft for approval."
                    onCancel={() => setAction(null)}
                    onSubmit={() => {
                      setPending(true);
                      void submitRetailSale(sale.id)
                        .then((row) => {
                          setSale(row);
                          setAction(null);
                          clearOutcome();
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The sale could not be submitted."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Submit this sale?")}
                  />
                ) : null}
                {action === "approve" ? (
                  <ActionPanel
                    confirmLabel="Confirm approval"
                    error={formError}
                    hint="Approval posts the sale, the payment, and the receipt."
                    onCancel={() => setAction(null)}
                    onSubmit={(form) => {
                      const body =
                        sale.payment_method === "CASH"
                          ? { cash_session_id: text(form, "cash_session_id") }
                          : {};
                      if (
                        sale.payment_method === "CASH" &&
                        !body.cash_session_id
                      ) {
                        setFields({
                          cash_session_id: "Select an open cash session.",
                        });
                        return;
                      }
                      setPending(true);
                      void approveRetailSale(sale.id, body)
                        .then((result) => {
                          setApproval(result);
                          setVoidResult(null);
                          setReturnResult(null);
                          setSale(result.retail_sale);
                          setAction(null);
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The sale could not be approved."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Approve retail sale")}
                  >
                    {sale.payment_method === "CASH" ? (
                      <CashSessionSelect
                        collectionError={sessions.error}
                        currency={sale.currency}
                        error={fields.cash_session_id}
                        label="Open cash session"
                        pending={sessions.pending}
                        sessions={sessions.sessions}
                      />
                    ) : (
                      <p className="text-sm text-slate-600">
                        {" "}
                        {tx("No cash session is sent for this method.")}{" "}
                      </p>
                    )}
                  </ActionPanel>
                ) : null}
                {action === "reject" ? (
                  <ActionPanel
                    confirmLabel="Confirm rejection"
                    error={formError}
                    hint="The sale stays unposted and keeps your reason."
                    onCancel={() => setAction(null)}
                    onSubmit={(form) => {
                      setPending(true);
                      void rejectRetailSale(sale.id, {
                        rejection_reason: text(form, "rejection_reason"),
                      })
                        .then((row) => {
                          setSale(row);
                          setAction(null);
                          clearOutcome();
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The sale could not be rejected."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Reject sale")}
                  >
                    <ReasonField
                      label="Rejection reason"
                      name="rejection_reason"
                    />
                  </ActionPanel>
                ) : null}
                {action === "cancel" ? (
                  <ActionPanel
                    confirmLabel="Confirm cancellation"
                    error={formError}
                    hint="A cancelled draft cannot be submitted."
                    onCancel={() => setAction(null)}
                    onSubmit={(form) => {
                      setPending(true);
                      void cancelRetailSale(sale.id, {
                        reason: text(form, "reason"),
                      })
                        .then((row) => {
                          setSale(row);
                          setAction(null);
                          clearOutcome();
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The draft could not be cancelled."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Cancel sale draft")}
                  >
                    <ReasonField label="Cancellation reason" />
                  </ActionPanel>
                ) : null}
                {action === "void" ? (
                  <ActionPanel
                    confirmLabel="Confirm void"
                    error={formError}
                    hint="This reverses the full sale, including stock."
                    onCancel={() => setAction(null)}
                    onSubmit={(form) => {
                      const body: VoidRetailSaleRequest = {
                        reason: text(form, "reason"),
                      };
                      const date = text(form, "business_date");
                      if (date) body.business_date = date;
                      if (sale.payment_method === "CASH") {
                        const sessionId = text(form, "cash_session_id");
                        if (!sessionId) {
                          setFields({
                            cash_session_id:
                              "Select the destination open session.",
                          });
                          return;
                        }
                        body.cash_session_id = sessionId;
                      }
                      setPending(true);
                      void voidRetailSale(sale.id, body)
                        .then((result) => {
                          setVoidResult(result);
                          setApproval(null);
                          setReturnResult(null);
                          setSale(result.retail_sale);
                          setAction(null);
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The sale could not be voided."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Void the full sale")}
                  >
                    <ReasonField label="Void reason" />
                    <Field label="Correction date">
                      <TextInput
                        defaultValue={defaultDate}
                        name="business_date"
                        onChange={(event) =>
                          setCorrectionDate(event.target.value)
                        }
                        type="date"
                      />
                    </Field>
                    {sale.payment_method === "CASH" ? (
                      <CashSessionSelect
                        collectionError={sessions.error}
                        currency={sale.currency}
                        error={fields.cash_session_id}
                        label="Destination open session"
                        pending={sessions.pending}
                        sessions={sessions.sessions}
                      />
                    ) : null}
                  </ActionPanel>
                ) : null}
                {action === "return" ? (
                  <ActionPanel
                    confirmLabel="Confirm return"
                    error={formError}
                    hint="Enter a quantity for each line you are returning. Leave a line blank to keep it. The server calculates the refund."
                    onCancel={() => setAction(null)}
                    onSubmit={(form) => {
                      setFields({});
                      setFormError("");
                      const chosen: ReturnRetailSaleRequest["lines"] = [];
                      const nextFields: Record<string, string> = {};
                      for (const line of sale.lines) {
                        const raw = text(form, `quantity_${line.id}`);
                        if (!raw) continue;
                        const quantity = canonicalQuantity(raw);
                        const entered = quantity
                          ? quantityScale(quantity)
                          : null;
                        const remaining = quantityScale(
                          line.remaining_quantity,
                        );
                        if (
                          !quantity ||
                          entered === null ||
                          remaining === null
                        ) {
                          nextFields[line.id] =
                            "Enter a positive quantity with up to three decimal places.";
                          continue;
                        }
                        if (entered > remaining) {
                          nextFields[line.id] =
                            tx("Enter no more than the remaining {quantity}.", {
                              quantity: line.remaining_quantity,
                            });
                          continue;
                        }
                        chosen.push({ line_id: line.id, quantity });
                      }
                      if (Object.keys(nextFields).length > 0) {
                        setFields(nextFields);
                        return;
                      }
                      if (chosen.length < 1) {
                        setFormError(
                          "Enter a quantity for at least one remaining line.",
                        );
                        return;
                      }
                      const body: ReturnRetailSaleRequest = {
                        reason: text(form, "reason"),
                        lines: chosen,
                      };
                      const date = text(form, "business_date");
                      if (date) body.business_date = date;
                      if (sale.payment_method === "CASH") {
                        const sessionId = text(form, "cash_session_id");
                        if (sessionId) body.cash_session_id = sessionId;
                      }
                      setPending(true);
                      void returnRetailSale(sale.id, body)
                        .then((result) => {
                          setReturnResult(result);
                          setApproval(null);
                          setVoidResult(null);
                          setSale(result.retail_sale);
                          setAction(null);
                          sales.reload();
                        })
                        .catch((caught: unknown) =>
                          fail(caught, "The return could not be recorded."),
                        )
                        .finally(() => setPending(false));
                    }}
                    pending={pending}
                    title={tx("Return part of the sale")}
                  >
                    {pending ? (
                      <p
                        className="text-sm font-medium text-slate-600"
                        role="status"
                      >
                        {" "}
                        {tx("Returning the sale")}{" "}
                      </p>
                    ) : null}
                    {sale.lines.map((line) => {
                      const remaining = quantityScale(line.remaining_quantity);
                      const descriptionId = `return-line-${line.id}`;
                      const inputId = `return-quantity-${line.id}`;
                      const errorId = `return-error-${line.id}`;
                      const error = fields[line.id];
                      return (
                        <div
                          className="rounded-xl border border-slate-200 bg-slate-50 p-3"
                          key={line.id}
                        >
                          <p className="text-sm font-semibold text-slate-900">
                            {line.product_name}
                          </p>
                          <p
                            className="mt-1 text-xs text-slate-500"
                            id={descriptionId}
                          >
                            {" "}
                            {tx("Sold")} {line.quantity}
                            {tx(", returned")} {line.returned_quantity}
                            {tx(", remaining")} {line.remaining_quantity}
                          </p>
                          <label className="field mt-3" htmlFor={inputId}>
                            {" "}
                            {tx("Return quantity for")} {line.product_name}
                          </label>
                          <TextInput
                            aria-describedby={
                              error
                                ? `${descriptionId} ${errorId}`
                                : descriptionId
                            }
                            aria-invalid={error ? true : undefined}
                            disabled={remaining === 0n}
                            id={inputId}
                            inputMode="decimal"
                            name={`quantity_${line.id}`}
                          />
                          {error ? (
                            <span
                              className="text-sm text-red-700"
                              id={errorId}
                              role="alert"
                            >
                              {tx(error)}
                            </span>
                          ) : null}
                        </div>
                      );
                    })}
                    <ReasonField error={fields.reason} label="Return reason" />
                    <Field error={fields.business_date} label="Business date">
                      <TextInput
                        defaultValue={defaultDate}
                        name="business_date"
                        onChange={(event) =>
                          setCorrectionDate(event.target.value)
                        }
                        type="date"
                      />
                    </Field>
                    {sale.payment_method === "CASH" ? (
                      <CashSessionSelect
                        collectionError={sessions.error}
                        currency={sale.currency}
                        error={fields.cash_session_id}
                        label="Destination open session"
                        pending={sessions.pending}
                        required={false}
                        sessions={sessions.sessions}
                      />
                    ) : null}
                    {sale.payment_method === "CASH" ? (
                      <p className="text-sm leading-6 text-slate-500">
                        {" "}
                        {tx(
                          "Choose an open session only when this return refunds cash. Leave it blank when the server refund is zero.",
                        )}{" "}
                      </p>
                    ) : null}
                  </ActionPanel>
                ) : null}
                {approval ? (
                  <div className="border-t border-emerald-100 bg-emerald-50/60 px-5 py-4">
                    <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-emerald-700 uppercase">
                      {" "}
                      {tx("Posted")}{" "}
                    </p>
                    <FactGrid
                      rows={[
                        {
                          label: tx("Posted payment"),
                          value: approval.payment.id,
                        },
                        {
                          label: tx("Issued receipt"),
                          value: approval.receipt.receipt_number,
                        },
                        {
                          label: tx("Cash movement"),
                          value: approval.cash_transaction?.id ?? "None",
                        },
                      ]}
                    />
                  </div>
                ) : null}
                {voidResult ? (
                  <div className="border-t border-slate-100 bg-slate-50/80 px-5 py-4">
                    <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase">
                      {" "}
                      {tx("Void result")}{" "}
                    </p>
                    <FactGrid
                      rows={[
                        {
                          label: tx("Reversal payment"),
                          value: voidResult.reversal_payment.id,
                        },
                        {
                          label: tx("Reversal receipt"),
                          value: voidResult.reversal_receipt.receipt_number,
                        },
                        {
                          label: tx("Cash movement"),
                          value: voidResult.cash_transaction?.id ?? "None",
                        },
                        {
                          label: tx("Stock returns"),
                          value: String(voidResult.stock_movements.length),
                        },
                      ]}
                    />
                  </div>
                ) : null}
                {returnResult ? (
                  <div className="border-t border-amber-100 bg-amber-50/50 px-5 py-4">
                    <p className="mb-3 text-xs font-semibold tracking-[0.14em] text-amber-800 uppercase">
                      {" "}
                      {tx("Return result")}{" "}
                    </p>
                    <FactGrid
                      rows={[
                        {
                          label: tx("Refund amount"),
                          value: returnResult.retail_return.refund_amount,
                        },
                        {
                          label: tx("Refund payment"),
                          value: returnResult.refund_payment?.id ?? "None",
                        },
                        {
                          label: tx("Correction receipt"),
                          value:
                            returnResult.correction_receipt?.receipt_number ??
                            "None",
                        },
                        {
                          label: tx("Cash movement"),
                          value: returnResult.cash_transaction?.id ?? "None",
                        },
                        {
                          label: tx("Stock returns"),
                          value: String(returnResult.stock_movements.length),
                        },
                      ]}
                    />
                  </div>
                ) : null}
              </>
            ) : null}
          </section>
        ) : null}
      </div>
    </div>
  );
}
