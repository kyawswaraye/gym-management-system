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
import { formatAppDate } from "@/lib/date";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveRetailSale,
  cancelRetailSale,
  createRetailSale,
  getRetailSale,
  getProduct,
  listProductCategories,
  listProducts,
  listRetailSales,
  rejectRetailSale,
  returnRetailSale,
  submitRetailSale,
  updateRetailSale,
  voidRetailSale,
} from "@/features/finance/client";
import { fieldMap, financeError } from "@/features/finance/errors";
import { ReceiptPrintLink } from "@/features/finance/receipt-preview";
import {
  canonicalMoney,
  canonicalQuantity,
  formatCents,
  moneyCents,
  previewLineAmount,
} from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  findScannedProduct,
  ProductSearch,
} from "@/features/finance/product-search";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { getFileDownloadUrl, operationGet } from "@/features/operations/client";
import { MemberSearch } from "@/features/operations/member-search";
import { actionButtonClass } from "@/lib/ui/action-button";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  Member,
  PaymentMethod,
  Product,
  ProductCategory,
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

function formatMilli(scale: bigint): string {
  const whole = scale / 1000n;
  const fraction = (scale % 1000n).toString().padStart(3, "0");
  return `${whole}.${fraction}`;
}

function blankLine(): LineDraft {
  return {
    key: crypto.randomUUID(),
    productId: "",
    quantity: "1.000",
    discount: "0.00",
  };
}

function memberChoiceLabel(member: {
  member_number: string;
  first_name: string;
  last_name: string;
}): string {
  return `${member.member_number} — ${member.first_name} ${member.last_name}`;
}

function saleMemberLabel(
  member: Member | null,
  memberId: string | null,
  tx: (text: string) => string,
): string {
  if (!memberId) return tx("Walk-in");
  if (member && member.id === memberId) return memberChoiceLabel(member);
  return tx("Member");
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
  productsById: ReadonlyMap<string, Product>,
): string | null {
  let total = 0n;
  let count = 0;
  for (const line of lines) {
    const product = productsById.get(line.productId);
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

function SaleThumb({
  assetId,
  className,
}: {
  assetId: string | null;
  className: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!assetId) return undefined;
    let cancelled = false;
    void getFileDownloadUrl(assetId)
      .then((download) => {
        if (!cancelled) setUrl(download.url);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [assetId]);
  return (
    <span
      aria-hidden
      className={`${className} inline-grid aspect-square shrink-0 place-items-center overflow-hidden bg-[#eef3f9] align-middle text-[#8ea0b8]`}
    >
      {url ? (
        <img
          alt=""
          className="block size-full min-h-0 min-w-0 object-cover"
          src={url}
        />
      ) : (
        <svg
          className="h-1/2 w-1/2"
          fill="none"
          height="24"
          preserveAspectRatio="xMidYMid meet"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="1.6"
          viewBox="0 0 24 24"
          width="24"
        >
          <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5z" />
          <path d="M12 12 4 8.5" />
          <path d="m12 12 8-3.5" />
          <path d="M12 12v8" />
        </svg>
      )}
    </span>
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
  canViewReceipts = false,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  canViewReceipts?: boolean;
  currency: string;
  defaultDate: string;
}) {
  const tx = useTx();
  const [applied, setApplied] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [sale, setSale] = useState<RetailSale | null>(null);
  const [productsById, setProductsById] = useState<
    ReadonlyMap<string, Product>
  >(() => new Map());
  const [lines, setLines] = useState<LineDraft[]>([blankLine()]);
  const [memberId, setMemberId] = useState("");
  const [selectedMember, setSelectedMember] = useState<Member | null>(null);
  const [method, setMethod] = useState<PaymentMethod>("CASH");
  const [businessDate, setBusinessDate] = useState(defaultDate);
  const [reference, setReference] = useState("");
  const [editing, setEditing] = useState(false);
  const [creatingSale, setCreatingSale] = useState(canCreate);
  const [catalog, setCatalog] = useState<Product[]>([]);
  const [catalogCategories, setCatalogCategories] = useState<ProductCategory[]>(
    [],
  );
  const [categoryId, setCategoryId] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const [activeProductId, setActiveProductId] = useState("");
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
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getRetailSale(selectedId)
        .then(async (row) => {
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
          const loaded = await Promise.all(
            row.lines.map((line) =>
              getProduct(line.product_id).catch(() => null),
            ),
          );
          if (!active) return;
          setProductsById((current) => {
            const next = new Map(current);
            for (const product of loaded) {
              if (product) next.set(product.id, product);
            }
            return next;
          });
          if (!row.member_id) {
            setSelectedMember(null);
            return;
          }
          try {
            const member = await operationGet<Member>(
              `members/${row.member_id}/`,
            );
            if (!active) return;
            setSelectedMember(member);
          } catch {
            if (!active) return;
            setSelectedMember(null);
          }
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setSale(null);
          setSelectedMember(null);
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

  function rememberProduct(product: Product) {
    setProductsById((current) => {
      if (current.get(product.id) === product) return current;
      const next = new Map(current);
      next.set(product.id, product);
      return next;
    });
  }

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
    setSelectedMember(null);
    setMethod("CASH");
    setBusinessDate(defaultDate);
    setReference("");
    setSelectedId("");
    setSale(null);
    setActiveProductId("");
    clearOutcome();
  }

  function closeEditor() {
    if (pending) return;
    setCreatingSale(false);
    setEditing(false);
    setFormError("");
  }

  function addProduct(product: Product) {
    rememberProduct(product);
    setActiveProductId(product.id);
    setLines((current) => {
      const existing = current.find((line) => line.productId === product.id);
      if (existing) {
        const scale = quantityScale(existing.quantity) ?? 0n;
        return current.map((line) =>
          line.productId === product.id
            ? { ...line, quantity: formatMilli(scale + 1000n) }
            : line,
        );
      }
      const empty = current.find((line) => !line.productId);
      if (empty) {
        return current.map((line) =>
          line.key === empty.key ? { ...line, productId: product.id } : line,
        );
      }
      if (current.length >= 100) return current;
      return [...current, { ...blankLine(), productId: product.id }];
    });
  }

  function stepLine(key: string, delta: bigint) {
    setLines((current) => {
      const line = current.find((item) => item.key === key);
      if (!line) return current;
      const next = (quantityScale(line.quantity) ?? 0n) + delta;
      if (next <= 0n) {
        const rest = current.filter((item) => item.key !== key);
        return rest.length > 0 ? rest : [blankLine()];
      }
      return current.map((item) =>
        item.key === key ? { ...item, quantity: formatMilli(next) } : item,
      );
    });
  }

  function removeLine(key: string) {
    setLines((current) => {
      const rest = current.filter((line) => line.key !== key);
      return rest.length > 0 ? rest : [blankLine()];
    });
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
  const shelf = categoryId
    ? catalog.filter((item) => item.category_id === categoryId)
    : catalog;
  const ticketLines = lines.filter((line) => line.productId);

  useEffect(() => {
    if (!editorOpen) return undefined;
    let active = true;
    void (async () => {
      try {
        const productsPage = await listProducts({
          is_active: "true",
          limit: "100",
        });
        let categories: ProductCategory[] = [];
        try {
          categories = (await listProductCategories({ limit: "100" })).data;
        } catch {
          categories = [];
        }
        if (!active) return;
        setCatalog(productsPage.data);
        setCatalogCategories(categories);
        setCatalogError("");
        setProductsById((current) => {
          const next = new Map(current);
          for (const product of productsPage.data) {
            next.set(product.id, product);
          }
          return next;
        });
      } catch (caught) {
        if (!active) return;
        setCatalogError(financeError(caught, tx("Unable to search products.")));
      } finally {
        if (active) setCatalogLoaded(true);
      }
    })();
    return () => {
      active = false;
    };
  }, [editorOpen, tx]);

  const addProductRef = useRef(addProduct);
  useEffect(() => {
    addProductRef.current = addProduct;
  });

  useEffect(() => {
    if (!editorOpen) return undefined;
    let buffer = "";
    let last = 0;
    function onScanKey(event: globalThis.KeyboardEvent) {
      const target = event.target;
      if (
        target instanceof HTMLInputElement &&
        target.name === "product_search"
      ) {
        return;
      }
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement
      ) {
        buffer = "";
        return;
      }
      if (event.key === "Enter") {
        const code = buffer;
        const recent = Date.now() - last < 100;
        buffer = "";
        if (!recent || code.length < 3) return;
        event.preventDefault();
        event.stopPropagation();
        void findScannedProduct(code)
          .then((product) => {
            if (product) {
              addProductRef.current(product);
              setFormError("");
            } else {
              setFormError("No matching products.");
            }
            document
              .querySelector<HTMLInputElement>('input[name="product_search"]')
              ?.focus();
          })
          .catch((caught: unknown) => {
            setFormError(
              financeError(caught, "Unable to search products."),
            );
          });
        return;
      }
      if (
        event.key.length !== 1 ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      ) {
        return;
      }
      const now = Date.now();
      if (now - last > 50) buffer = "";
      buffer += event.key;
      last = now;
    }
    window.addEventListener("keydown", onScanKey, true);
    return () => window.removeEventListener("keydown", onScanKey, true);
  }, [editorOpen]);

  const showPaging = sales.index > 0 || sales.hasMore || sales.bounded;
  const estimate = previewTotal(lines, productsById);
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
        <form
          className="grid rounded-2xl border border-slate-200 bg-white shadow-sm lg:h-[calc(100dvh-8.5rem)] lg:grid-cols-[minmax(0,1.15fr)_23rem]"
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            const target = event.target;
            if (
              target instanceof HTMLInputElement &&
              target.name === "product_search"
            ) {
              event.preventDefault();
            }
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
        >
          <div className="flex min-h-[28rem] flex-col border-b border-slate-200 lg:min-h-0 lg:border-r lg:border-b-0">
            <div className="relative z-20 border-b border-slate-100 p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <h2 className="text-lg font-bold text-slate-900">
                  {editing ? tx("Edit draft sale") : tx("New retail sale")}
                </h2>
                <button
                  className="button button-cancel button-sm"
                  disabled={pending}
                  onClick={closeEditor}
                  type="button"
                >
                  {tx("Close")}
                </button>
              </div>
              <ProductSearch
                autoFocus
                disabled={pending}
                label="Product"
                onSelect={addProduct}
                selected={productsById.get(activeProductId) ?? null}
                showEmptySelected={false}
              />
              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                <button
                  aria-pressed={categoryId === ""}
                  className={
                    categoryId === ""
                      ? "shrink-0 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-bold text-white"
                      : "shrink-0 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-slate-200"
                  }
                  onClick={() => setCategoryId("")}
                  type="button"
                >
                  {tx("All categories")}
                </button>
                {catalogCategories.map((category) => (
                  <button
                    aria-pressed={categoryId === category.id}
                    className={
                      categoryId === category.id
                        ? "shrink-0 rounded-full bg-slate-900 px-3 py-1.5 text-xs font-bold text-white"
                        : "shrink-0 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600 ring-1 ring-slate-200"
                    }
                    key={category.id}
                    onClick={() => setCategoryId(category.id)}
                    type="button"
                  >
                    {category.name}
                  </button>
                ))}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {!catalogLoaded && !catalogError ? (
                <p className="text-sm font-medium text-slate-600" role="status">
                  {tx("Searching products.")}
                </p>
              ) : null}
              {catalogError ? <Alert message={catalogError} /> : null}
              {catalogLoaded && shelf.length === 0 ? (
                <div className="px-2 py-16 text-center">
                  <p className="font-semibold text-slate-800">
                    {tx("No products to sell yet.")}
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    {tx("Tap a product to add it.")}
                  </p>
                </div>
              ) : catalogLoaded ? (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
                  {shelf.map((item) => {
                    const inCart = lines.find(
                      (line) => line.productId === item.id,
                    );
                    const badge = inCart
                      ? (quantityScale(inCart.quantity) ?? 0n) % 1000n === 0n
                        ? (
                            (quantityScale(inCart.quantity) ?? 0n) / 1000n
                          ).toString()
                        : inCart.quantity
                      : "";
                    return (
                      <button
                        className={`relative overflow-hidden rounded-2xl border bg-white text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md ${
                          inCart
                            ? "border-[#0866ff] ring-2 ring-[#0866ff]"
                            : "border-slate-200"
                        }`}
                        disabled={pending}
                        key={item.id}
                        onClick={() => addProduct(item)}
                        type="button"
                      >
                        <SaleThumb
                          assetId={item.image_asset_id}
                          className="w-full rounded-none"
                        />
                        {badge ? (
                          <span className="absolute top-2 right-2 grid size-7 place-items-center rounded-full bg-[#0866ff] text-xs font-bold text-white">
                            {badge}
                          </span>
                        ) : null}
                        <span className="block p-3">
                          <span className="line-clamp-2 block text-sm font-semibold text-slate-900">
                            {item.name}
                          </span>
                          <span className="mt-1 block text-sm font-bold text-slate-900 tabular-nums">
                            {item.sale_price}{" "}
                            <span className="font-medium text-slate-400">
                              {tx(currency)}
                            </span>
                          </span>
                          <span className="mt-0.5 block text-xs text-slate-500">
                            {tx("On hand")} {item.quantity_on_hand}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>
          <aside className="flex min-h-0 flex-col bg-slate-50">
            <div className={`min-h-0 flex-1 space-y-4 overflow-y-auto p-4 ${fieldTone}`}>
              <Alert message={formError} />
              {ticketLines.length > 0 ? (
                <ul className="grid gap-3">
                  {ticketLines.map((line) => {
                    const product = productsById.get(line.productId) ?? null;
                    const preview = product
                      ? previewLineAmount(
                          line.quantity,
                          product.sale_price,
                          line.discount,
                        )
                      : null;
                    return (
                      <li
                        className="rounded-2xl bg-white p-3 ring-1 ring-slate-200"
                        key={line.key}
                      >
                        <div className="flex items-start gap-3">
                          <SaleThumb
                            assetId={product?.image_asset_id ?? null}
                            className="size-14 rounded-xl"
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-2">
                              <p className="font-semibold text-slate-900">
                                {product?.name ?? tx("Product")}
                              </p>
                              <button
                                className="text-xs font-semibold text-slate-500 hover:text-red-700"
                                onClick={() => removeLine(line.key)}
                                type="button"
                              >
                                {tx("Remove line")}
                              </button>
                            </div>
                            <p className="text-xs text-slate-500">
                              {product
                                ? `${product.sale_price} ${currency}`
                                : line.productId}
                            </p>
                          </div>
                        </div>
                        <div className="mt-3">
                          <label
                            className="mb-1.5 block text-sm font-medium text-slate-600"
                            htmlFor={`quantity-${line.key}`}
                          >
                            {tx("Quantity")}
                          </label>
                          <div className="flex items-center gap-2">
                            <button
                              aria-label={tx("Remove one")}
                              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-slate-100 text-lg font-bold text-slate-800 ring-1 ring-slate-200"
                              onClick={() => stepLine(line.key, -1000n)}
                              type="button"
                            >
                              −
                            </button>
                            <div className="min-w-0 flex-1">
                              <TextInput
                                decimalPlaces={3}
                                id={`quantity-${line.key}`}
                                inputMode="decimal"
                                onChange={(event) =>
                                  setLines((current) =>
                                    current.map((item) =>
                                      item.key === line.key
                                        ? {
                                            ...item,
                                            quantity: event.target.value,
                                          }
                                        : item,
                                    ),
                                  )
                                }
                                value={line.quantity}
                              />
                            </div>
                            <button
                              aria-label={tx("Add one")}
                              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-slate-900 text-lg font-bold text-white"
                              onClick={() => stepLine(line.key, 1000n)}
                              type="button"
                            >
                              +
                            </button>
                          </div>
                          {fields.quantity ? (
                            <p className="mt-1.5 text-sm text-red-700">
                              {tx(fields.quantity)}
                            </p>
                          ) : null}
                        </div>
                        <div className="mt-3">
                          <Field
                            label={tx("Discount ({currency})", {
                              currency: tx(currency),
                            })}
                          >
                            <TextInput
                              inputMode="decimal"
                              onChange={(event) =>
                                setLines((current) =>
                                  current.map((item) =>
                                    item.key === line.key
                                      ? {
                                          ...item,
                                          discount: event.target.value,
                                        }
                                      : item,
                                  ),
                                )
                              }
                              value={line.discount}
                            />
                          </Field>
                        </div>
                        {preview ? (
                          <p className="mt-2 text-right text-sm font-semibold text-slate-900 tabular-nums">
                            {preview}
                          </p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
              <MemberSearch
                activeOnly={false}
                disabled={pending}
                emptySelectedLabel="Walk-in"
                formatMember={memberChoiceLabel}
                label="Member"
                onSelect={(member) => {
                  setSelectedMember(member);
                  setMemberId(member.id);
                }}
                selected={selectedMember}
                showEmptySelected
              />
              {selectedMember ? (
                <button
                  className="button button-cancel w-fit"
                  disabled={pending}
                  onClick={() => {
                    setSelectedMember(null);
                    setMemberId("");
                  }}
                  type="button"
                >
                  {tx("Clear to walk-in")}
                </button>
              ) : null}
              <Field label="Business date">
                <TextInput
                  onChange={(event) => setBusinessDate(event.target.value)}
                  type="date"
                  value={businessDate}
                />
              </Field>
              <details className="rounded-xl bg-white px-3 py-2 ring-1 ring-slate-200">
                <summary className="cursor-pointer text-sm font-semibold text-slate-700">
                  {tx("Reference")}
                </summary>
                <div className="pt-3">
                  <Field label="Reference">
                    <TextInput
                      onChange={(event) => setReference(event.target.value)}
                      value={reference}
                    />
                  </Field>
                </div>
              </details>
            </div>
            <div className="border-t border-slate-200 bg-white p-4">
              <div
                aria-label={tx("Payment method")}
                className="grid grid-cols-2 gap-2"
                role="group"
              >
                {METHODS.map((item) => {
                  const selected = method === item;
                  return (
                    <button
                      aria-pressed={selected}
                      className={
                        selected
                          ? "min-h-11 rounded-xl bg-slate-900 px-3 text-sm font-bold text-white"
                          : "min-h-11 rounded-xl bg-white px-3 text-sm font-semibold text-slate-700 ring-1 ring-slate-200 hover:bg-slate-100"
                      }
                      key={item}
                      onClick={() => setMethod(item)}
                      type="button"
                    >
                      {tx(pretty(item))}
                    </button>
                  );
                })}
              </div>

              <div className="mt-4 flex items-end justify-between gap-3">
                <p className="text-sm font-medium text-slate-500">
                  {tx("Estimated total")}
                </p>
                <p className="text-3xl font-black tracking-tight text-slate-900 tabular-nums">
                  {estimate ?? "0.00"}
                  <span className="ml-2 text-sm font-semibold text-slate-400">
                    {tx(currency)}
                  </span>
                </p>
              </div>
              <button
                className="button button-create mt-3 min-h-12 w-full text-base"
                disabled={pending}
                type="submit"
              >
                {editing ? tx("Save sale changes") : tx("Save draft")}
              </button>
            </div>
          </aside>
        </form>
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
                            aria-label={formatAppDate(row.business_date)}
                            className="block text-left font-semibold"
                            onClick={() => selectSale(row.id)}
                            type="button"
                          >
                            <span className="font-semibold">
                              {formatAppDate(row.business_date)}
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
                        {formatAppDate(sale.business_date)} ·{" "}
                        {tx(pretty(sale.payment_method))} ·{" "}
                        {saleMemberLabel(selectedMember, sale.member_id, tx)}
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
                {canViewReceipts &&
                sale.status === "POSTED" &&
                sale.receipt_id ? (
                  <div className="px-5 pb-4">
                    <ReceiptPrintLink receiptId={sale.receipt_id} />
                  </div>
                ) : null}
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
                          nextFields[line.id] = tx(
                            "Enter no more than the remaining {quantity}.",
                            {
                              quantity: line.remaining_quantity,
                            },
                          );
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
                            decimalPlaces={3}
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
                    {canViewReceipts &&
                    approval.receipt.status === "ISSUED" &&
                    approval.receipt.id.toLowerCase() !==
                      sale.receipt_id?.toLowerCase() ? (
                      <div className="mt-3">
                        <ReceiptPrintLink receiptId={approval.receipt.id} />
                      </div>
                    ) : null}
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
                    {canViewReceipts &&
                    voidResult.reversal_receipt.status === "ISSUED" ? (
                      <div className="mt-3">
                        <ReceiptPrintLink
                          receiptId={voidResult.reversal_receipt.id}
                        />
                      </div>
                    ) : null}
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
                    {canViewReceipts &&
                    returnResult.correction_receipt?.status === "ISSUED" &&
                    returnResult.correction_receipt.id.toLowerCase() !==
                      sale.receipt_id?.toLowerCase() ? (
                      <div className="mt-3">
                        <ReceiptPrintLink
                          receiptId={returnResult.correction_receipt.id}
                        />
                      </div>
                    ) : null}
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
