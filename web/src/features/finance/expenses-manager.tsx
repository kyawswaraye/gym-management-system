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

import { formatAppDate } from "@/lib/date";
import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveExpense,
  cancelExpense,
  createExpense,
  createExpenseCategory,
  getExpense,
  listExpenseCategories,
  listExpenses,
  rejectExpense,
  submitExpense,
  updateExpense,
  updateExpenseCategory,
  voidExpense,
} from "@/features/finance/client";
import { FormDialog } from "@/components/form-dialog";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  ReceiptEvidenceField,
  ReceiptEvidencePreview,
} from "@/features/finance/receipt-evidence-field";
import { uploadExpenseReceipt } from "@/features/finance/receipt-file";
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
  Expense,
  ExpenseCategory,
  ExpenseWriteRequest,
  PaymentMethod,
  VoidExpenseRequest,
} from "@/types/api";

const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const;
const STATUSES = [
  ["", "All"],
  ["DRAFT", "Draft"],
  ["PENDING_APPROVAL", "Pending approval"],
  ["POSTED", "Posted"],
  ["REJECTED", "Rejected"],
  ["VOID", "Void"],
  ["CANCELLED", "Cancelled"],
] as const;
const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const fieldTone =
  "[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600";
const SECTIONS = [
  ["expenses", "Expenses"],
  ["categories", "Categories"],
] as const;

type ExpenseSection = (typeof SECTIONS)[number][0];

function viewTabClass(selected: boolean): string {
  const base = "min-h-10 rounded-full px-4 text-sm font-bold";
  return selected
    ? `${base} bg-slate-900 text-white`
    : `${base} border border-slate-300 bg-slate-100 text-slate-700 hover:bg-slate-200`;
}

type ExpenseAction =
  "edit" | "submit" | "approve" | "reject" | "cancel" | "void" | null;
type ErrorScope = "category" | "expense" | "detail";

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
    Active: "bg-emerald-100 text-emerald-800",
    Inactive: "bg-slate-200 text-slate-600",
    Protected: "bg-amber-100 text-amber-900",
  };
  return tones[status] ?? "bg-slate-100 text-slate-600";
}

function StatusPill({ status }: { status: string }) {
  const tx = useTx();
  return (
    <span
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold tracking-wide ${statusTone(status)}`}
    >
      {tx(pretty(status))}
    </span>
  );
}

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function protectedCategory(category: ExpenseCategory | undefined): boolean {
  return Boolean(category?.system_key);
}

function salaryCategory(category: ExpenseCategory | undefined): boolean {
  return (
    category?.system_key === "STAFF_SALARY" ||
    category?.system_key === "PT_COMMISSION"
  );
}

function categoryStatus(category: ExpenseCategory): string {
  if (protectedCategory(category)) return "Protected";
  return category.is_active ? "Active" : "Inactive";
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
    const index = STATUSES.findIndex(([item]) => item === value);
    const next =
      STATUSES[(index + delta + STATUSES.length) % STATUSES.length]?.[0] ?? "";
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
      {STATUSES.map(([item, label]) => {
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

function ExpenseFields({
  canUploadReceipt,
  categories,
  currency,
  defaultDate,
  expense,
  fields,
  wide = false,
}: {
  canUploadReceipt: boolean;
  categories: readonly ExpenseCategory[];
  currency: string;
  defaultDate: string;
  expense?: Expense;
  fields: Record<string, string>;
  wide?: boolean;
}) {
  const tx = useTx();
  const span = wide ? "sm:col-span-2" : undefined;
  return (
    <div className={wide ? "grid gap-4 sm:grid-cols-2" : "grid gap-4"}>
      <div className={span}>
        <Field error={fields.description} label="Description">
          <TextInput
            defaultValue={expense?.description}
            name="description"
            required
          />
        </Field>
      </div>
      <Field error={fields.category_id} label="Category">
        <SelectInput
          defaultValue={expense?.category_id ?? ""}
          name="category_id"
          required
        >
          <option value="">{tx("Select a category")}</option>
          {categories.map((category) => (
            <option
              disabled={salaryCategory(category)}
              key={category.id}
              value={category.id}
            >
              {category.name}
              {protectedCategory(category) ? " (protected)" : ""}
              {!category.is_active ? " (inactive)" : ""}
            </option>
          ))}
        </SelectInput>
      </Field>
      <Field error={fields.business_date} label="Business date">
        <TextInput
          defaultValue={expense?.business_date ?? defaultDate}
          name="business_date"
          required
          type="date"
        />
      </Field>
      <Field
        error={fields.amount}
        label={tx("Amount ({currency})", { currency: tx(currency) })}
      >
        <TextInput
          defaultValue={expense?.amount}
          inputMode="decimal"
          name="amount"
          required
        />
      </Field>
      <Field error={fields.payment_method} label="Payment method">
        <SelectInput
          defaultValue={expense?.payment_method ?? "CASH"}
          name="payment_method"
          required
        >
          {METHODS.map((item) => (
            <option key={item} value={item}>
              {tx(pretty(item))}
            </option>
          ))}
        </SelectInput>
      </Field>
      <div className={span}>
        <Field label="Policy note">
          <TextInput defaultValue={expense?.policy_note} name="policy_note" />
        </Field>
      </div>
      {canUploadReceipt ? (
        <div className={span}>
          <ReceiptEvidenceField
            attachedFileId={expense?.receipt_file_asset_id ?? null}
          />
        </div>
      ) : null}
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

export function ExpensesManager({
  canCreate,
  canUpdate,
  canSubmit,
  canApprove,
  canVoid,
  canManageCategories,
  canUploadReceipt,
  gymId,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canUpdate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canVoid: boolean;
  canManageCategories: boolean;
  canUploadReceipt: boolean;
  gymId: string;
  currency: string;
  defaultDate: string;
}) {
  const tx = useTx();
  const [view, setView] = useState<ExpenseSection>("expenses");
  const [applied, setApplied] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [expense, setExpense] = useState<Expense | null>(null);
  const [editingCategory, setEditingCategory] =
    useState<ExpenseCategory | null>(null);
  const [recording, setRecording] = useState(false);
  const [creatingCategory, setCreatingCategory] = useState(false);
  const [action, setAction] = useState<ExpenseAction>(null);
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [formError, setFormError] = useState("");
  const [errorScope, setErrorScope] = useState<ErrorScope | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const categories = useCursorPages("categories", (after) =>
    listExpenseCategories({ limit: "100", ...(after ? { after } : {}) }),
  );
  const expenses = useCursorPages(applied, (after) =>
    listExpenses({
      ...(applied ? { status: applied } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const detail = expense?.id === selectedId ? expense : null;
  const sessions = useOpenCashSessions(
    action === "approve" && detail
      ? detail.business_date
      : action === "void"
        ? correctionDate
        : "",
  );
  const categoryById = new Map(categories.rows.map((row) => [row.id, row]));
  const selectedCategory = detail
    ? categoryById.get(detail.category_id)
    : undefined;

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getExpense(selectedId)
        .then((row) => {
          if (active) setExpense(row);
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setExpense(null);
          setFormError(
            financeError(caught, tx("The expense could not be loaded.")),
          );
          setErrorScope("detail");
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, expenses.rows, tx]);

  useEffect(() => {
    if (!editingCategory) return undefined;
    document.getElementById("category-editor")?.scrollIntoView?.({
      block: "nearest",
    });
    return undefined;
  }, [editingCategory]);

  function fail(caught: unknown, fallback: string, scope: ErrorScope) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
    setErrorScope(scope);
  }

  function clearFeedback() {
    setFormError("");
    setFields({});
    setErrorScope(null);
  }

  function selectExpense(id: string) {
    setSelectedId(id);
    setAction(null);
    clearFeedback();
  }

  function openAction(next: Exclude<ExpenseAction, null>) {
    setAction(next);
    clearFeedback();
    if (next === "void") setCorrectionDate(defaultDate);
  }

  async function expenseBody(
    form: FormData,
    businessDate: string,
  ): Promise<ExpenseWriteRequest | null> {
    const amount = canonicalMoney(text(form, "amount"));
    if (!amount) {
      setFields({ amount: "Enter an exact amount such as 10.00." });
      return null;
    }
    const file = form.get("receipt");
    let receiptId = text(form, "receipt_file_asset_id");
    if (file instanceof File && file.size > 0 && canUploadReceipt) {
      receiptId = await uploadExpenseReceipt(gymId, file);
    }
    return {
      category_id: text(form, "category_id"),
      business_date: text(form, "business_date") || businessDate,
      description: text(form, "description"),
      amount,
      currency,
      payment_method: text(form, "payment_method") as PaymentMethod,
      ...(receiptId ? { receipt_file_asset_id: receiptId } : {}),
      ...(text(form, "policy_note")
        ? { policy_note: text(form, "policy_note") }
        : {}),
    };
  }

  const expenseError = errorScope === "expense" ? formError : "";
  const categoryError = errorScope === "category" ? formError : "";
  const detailError = errorScope === "detail" ? formError : "";
  const showPaging = expenses.index > 0 || expenses.hasMore || expenses.bounded;
  const showCategoryPaging =
    categories.index > 0 || categories.hasMore || categories.bounded;
  const salaryLocked = salaryCategory(selectedCategory);
  const canEditDraft = detail?.status === "DRAFT" && canUpdate;
  const canSubmitDraft = detail?.status === "DRAFT" && canSubmit;
  const canDecide = detail?.status === "PENDING_APPROVAL" && canApprove;
  const canCancelDraft = detail?.status === "DRAFT" && canUpdate;
  const canVoidPosted =
    detail?.status === "POSTED" &&
    canVoid &&
    !salaryLocked &&
    detail.original_expense_id === null;
  const hasRowActions = Boolean(
    canEditDraft ||
    canSubmitDraft ||
    canDecide ||
    canCancelDraft ||
    canVoidPosted,
  );

  return (
    <div className="grid gap-6">
      <div
        aria-label={tx("Expense sections")}
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
            onClick={() => setView(id)}
            role="tab"
            type="button"
          >
            {tx(label)}
          </button>
        ))}
      </div>
      {view === "expenses" ? (
        <div
          aria-labelledby="expenses-tab"
          className="grid gap-6"
          id="expenses-panel"
          role="tabpanel"
        >
          {canCreate && recording ? (
            <FormDialog
              description="Save a draft first. Submit it from the ledger when the amount and receipt are ready."
              onClose={() => {
                if (pending) return;
                setRecording(false);
                clearFeedback();
              }}
              onSubmit={(event) => {
                event.preventDefault();
                const formElement = event.currentTarget;
                const form = new FormData(formElement);
                setPending(true);
                void expenseBody(form, defaultDate)
                  .then((body) => {
                    if (!body) return null;
                    return createExpense(body);
                  })
                  .then((saved) => {
                    if (!saved) return;
                    formElement.reset();
                    setRecording(false);
                    setAction(null);
                    setSelectedId(saved.id);
                    setExpense(saved);
                    clearFeedback();
                    expenses.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The expense could not be saved.", "expense"),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title={tx("Record an expense")}
              wide
            >
              <div className={fieldTone}>
                <Alert message={expenseError} />
                <ExpenseFields
                  canUploadReceipt={canUploadReceipt}
                  categories={categories.rows}
                  currency={currency}
                  defaultDate={defaultDate}
                  fields={fields}
                  wide
                />
              </div>
              <div>
                <button
                  className="button button-edit"
                  disabled={pending}
                  type="submit"
                >
                  {" "}
                  {tx("Save draft")}{" "}
                </button>
              </div>
            </FormDialog>
          ) : null}

          <div
            className={
              selectedId
                ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.9fr)]"
                : "grid"
            }
          >
            <section
              aria-busy={expenses.pending}
              aria-labelledby="expense-list-title"
              className={cardClass}
            >
              <div className="border-b border-slate-100 px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2
                      className="text-lg font-bold text-slate-900"
                      id="expense-list-title"
                    >
                      {" "}
                      {tx("Expense ledger")}{" "}
                    </h2>
                    <p className="mt-1 text-sm leading-6 text-slate-500">
                      {" "}
                      {tx(
                        "Select a row to review, submit, or approve it.",
                      )}{" "}
                    </p>
                  </div>
                  {canCreate ? (
                    <button
                      className="button button-create"
                      onClick={() => {
                        setRecording(true);
                        setAction(null);
                        clearFeedback();
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Record an expense")}{" "}
                    </button>
                  ) : null}
                </div>
                <div className="mt-4">
                  <StatusFilter onChange={setApplied} value={applied} />
                </div>
              </div>

              {expenses.pending && expenses.rows.length === 0 ? (
                <LoadingRows label="Loading expenses." />
              ) : null}
              {expenses.pending && expenses.rows.length > 0 ? (
                <p
                  className="px-5 pt-4 text-sm font-medium text-slate-600"
                  role="status"
                >
                  {" "}
                  {tx("Loading expenses.")}{" "}
                </p>
              ) : null}
              {!expenses.pending && expenses.error ? (
                <div className="p-4 sm:p-5">
                  <Alert message={expenses.error} />
                </div>
              ) : null}
              {!expenses.pending &&
              !expenses.error &&
              expenses.rows.length === 0 ? (
                <div className="px-4 py-14 text-center sm:px-5">
                  <p className="font-semibold text-slate-800">
                    {applied
                      ? tx("No expenses match this status.")
                      : tx("No expenses yet.")}
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    {applied
                      ? tx("Try another status.")
                      : tx("Drafts you save will show up here.")}
                  </p>
                </div>
              ) : null}
              {expenses.rows.length > 0 ? (
                <div
                  className={`overflow-x-auto ${expenses.pending ? "opacity-60" : ""}`}
                >
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">{tx("Expenses")}</caption>
                    <thead className="border-b border-slate-200 text-slate-500">
                      <tr>
                        <th
                          className="px-5 py-2.5 text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("Description")}{" "}
                        </th>
                        <th
                          className="hidden px-5 py-2.5 text-xs font-semibold sm:table-cell"
                          scope="col"
                        >
                          {" "}
                          {tx("Category")}{" "}
                        </th>
                        <th
                          className="px-5 py-2.5 text-right text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("Amount")}{" "}
                        </th>
                        <th
                          className="hidden px-5 py-2.5 text-xs font-semibold lg:table-cell"
                          scope="col"
                        >
                          {" "}
                          {tx("Method")}{" "}
                        </th>
                        <th
                          className="hidden px-5 py-2.5 text-xs font-semibold lg:table-cell"
                          scope="col"
                        >
                          {" "}
                          {tx("Date")}{" "}
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
                      {expenses.rows.map((row) => {
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
                                className="text-left font-semibold"
                                onClick={() => selectExpense(row.id)}
                                type="button"
                              >
                                {tx(row.description)}
                              </button>
                            </td>
                            <td className="hidden px-5 py-3 text-slate-600 sm:table-cell">
                              {categoryById.get(row.category_id)?.name ?? "—"}
                            </td>
                            <td className="px-5 py-3 text-right font-semibold whitespace-nowrap text-slate-900 tabular-nums">
                              {row.amount}{" "}
                              <span className="font-medium text-slate-400">
                                {tx(row.currency)}
                              </span>
                            </td>
                            <td className="hidden px-5 py-3 whitespace-nowrap text-slate-600 lg:table-cell">
                              {tx(pretty(row.payment_method))}
                            </td>
                            <td className="hidden px-5 py-3 whitespace-nowrap text-slate-600 lg:table-cell">
                              {formatAppDate(row.business_date)}
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
                    bounded={expenses.bounded}
                    hasMore={expenses.hasMore}
                    index={expenses.index}
                    onNext={() => void expenses.goNext()}
                    onPrevious={() => void expenses.goPrevious()}
                    pending={expenses.pending}
                  />
                </div>
              ) : null}
            </section>

            {selectedId ? (
              <section
                aria-labelledby="expense-detail-title"
                className={`${cardClass} border-t-[3px] border-t-amber-500 xl:sticky xl:top-4`}
                id="expense-detail"
              >
                {!detail ? (
                  <h2 className="sr-only" id="expense-detail-title">
                    {" "}
                    {tx("Expense")}{" "}
                  </h2>
                ) : null}
                {!detail && !detailError ? (
                  <div className="grid gap-3 p-5" role="status">
                    <p className="text-sm font-medium text-slate-600">
                      {" "}
                      {tx("Loading the expense.")}{" "}
                    </p>
                    <div className="h-24 animate-pulse rounded-xl bg-slate-100" />
                    <div className="grid grid-cols-2 gap-2">
                      <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                      <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
                    </div>
                  </div>
                ) : null}
                {detailError ? (
                  <div className="p-5">
                    <Alert message={detailError} />
                  </div>
                ) : null}
                {detail ? (
                  <>
                    <div className="px-5 pt-5">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h2
                            className="text-xs font-semibold tracking-[0.16em] text-slate-400 uppercase"
                            id="expense-detail-title"
                          >
                            {" "}
                            {tx("Expense")}{" "}
                          </h2>
                          <p className="mt-1 text-xl font-black tracking-tight break-words text-slate-900">
                            {tx(detail.description)}
                          </p>
                          <p className="mt-0.5 text-sm text-slate-500">
                            {selectedCategory?.name ?? "Category unavailable"}
                          </p>
                        </div>
                        <StatusPill status={detail.status} />
                      </div>
                      <div className="mt-4 flex items-end justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                        <div>
                          <p className="text-xs font-medium text-slate-500">
                            {" "}
                            {tx("Amount")}{" "}
                          </p>
                          <p className="mt-1 text-2xl font-black tracking-tight text-slate-900 tabular-nums">
                            {detail.amount}
                          </p>
                        </div>
                        <p className="pb-1 text-sm font-semibold text-slate-500">
                          {tx(detail.currency)}
                        </p>
                      </div>
                    </div>
                    <div className="px-5 py-4">
                      <FactGrid
                        rows={[
                          {
                            label: tx("Method"),
                            value: tx(pretty(detail.payment_method)),
                          },
                          {
                            label: tx("Business date"),
                            value: formatAppDate(detail.business_date),
                          },
                          {
                            label: tx("Receipt"),
                            value: detail.receipt_file_asset_id
                              ? tx("Attached")
                              : tx("None"),
                          },
                          {
                            label: tx("Cash session"),
                            value: detail.cash_session_id ?? "None",
                          },
                        ]}
                      />
                    </div>
                    {detail.receipt_file_asset_id ? (
                      <div className="px-5 pb-4">
                        <p className="mb-2 text-xs font-medium text-slate-500">
                          {tx("Receipt evidence")}
                        </p>
                        <ReceiptEvidencePreview
                          fileId={detail.receipt_file_asset_id}
                        />
                      </div>
                    ) : null}
                    {detail.policy_note ? (
                      <div className="px-5 pb-4">
                        <p className="rounded-xl bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700 ring-1 ring-slate-200/80">
                          <span className="mb-1 block text-xs font-medium text-slate-500">
                            {" "}
                            {tx("Policy note")}{" "}
                          </span>
                          {detail.policy_note}
                        </p>
                      </div>
                    ) : null}
                    {detail.rejection_reason ? (
                      <div className="px-5 pb-4">
                        <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-900">
                          <span className="mb-1 block text-xs font-semibold text-red-700">
                            {" "}
                            {tx("Rejection reason")}{" "}
                          </span>
                          {detail.rejection_reason}
                        </p>
                      </div>
                    ) : null}
                    {detail.reason &&
                    (detail.status === "VOID" ||
                      detail.status === "CANCELLED") ? (
                      <div className="px-5 pb-4">
                        <p className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700">
                          <span className="mb-1 block text-xs font-medium text-slate-500">
                            {" "}
                            {tx("Reason")}{" "}
                          </span>
                          {detail.reason}
                        </p>
                      </div>
                    ) : null}
                    {salaryLocked ? (
                      <div className="px-5 pb-4">
                        <p
                          className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950"
                          role="note"
                        >
                          {" "}
                          {tx(
                            "This expense uses a protected salary or commission category. Void the salary period instead of voiding it here.",
                          )}{" "}
                        </p>
                      </div>
                    ) : null}
                    {hasRowActions || action ? (
                      <div className="grid gap-3 border-t border-slate-100 px-5 py-4">
                        {action === null && hasRowActions ? (
                          <div className="flex flex-wrap gap-2">
                            {canEditDraft ? (
                              <button
                                className="button button-edit"
                                onClick={() => openAction("edit")}
                                type="button"
                              >
                                {" "}
                                {tx("Edit draft")}{" "}
                              </button>
                            ) : null}
                            {canSubmitDraft ? (
                              <button
                                className="button"
                                onClick={() => openAction("submit")}
                                type="button"
                              >
                                {" "}
                                {tx("Submit")}{" "}
                              </button>
                            ) : null}
                            {canDecide ? (
                              <button
                                className="button button-success"
                                onClick={() => openAction("approve")}
                                type="button"
                              >
                                {" "}
                                {tx("Approve")}{" "}
                              </button>
                            ) : null}
                            {canDecide ? (
                              <button
                                className="button button-warn"
                                onClick={() => openAction("reject")}
                                type="button"
                              >
                                {" "}
                                {tx("Reject")}{" "}
                              </button>
                            ) : null}
                            {canCancelDraft ? (
                              <button
                                className="button button-cancel"
                                onClick={() => openAction("cancel")}
                                type="button"
                              >
                                {" "}
                                {tx("Cancel draft")}{" "}
                              </button>
                            ) : null}
                            {canVoidPosted ? (
                              <button
                                className="button button-delete"
                                onClick={() => openAction("void")}
                                type="button"
                              >
                                {" "}
                                {tx("Void")}{" "}
                              </button>
                            ) : null}
                          </div>
                        ) : null}
                        {action === "edit" && canUpdate ? (
                          <FormDialog
                            description="Changes stay on this draft until you submit it."
                            key={detail.id}
                            onClose={() => {
                              if (pending) return;
                              setAction(null);
                              clearFeedback();
                            }}
                            onSubmit={(event) => {
                              event.preventDefault();
                              const form = new FormData(event.currentTarget);
                              setPending(true);
                              void expenseBody(form, detail.business_date)
                                .then((body) => {
                                  if (!body) return null;
                                  return updateExpense(detail.id, body);
                                })
                                .then((saved) => {
                                  if (!saved) return;
                                  setAction(null);
                                  setExpense(saved);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The expense could not be saved.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Edit draft")}
                            wide
                          >
                            <Alert message={expenseError} />
                            <div className={fieldTone}>
                              <ExpenseFields
                                canUploadReceipt={canUploadReceipt}
                                categories={categories.rows}
                                currency={currency}
                                defaultDate={defaultDate}
                                expense={detail}
                                fields={fields}
                              />
                            </div>
                            <div>
                              <button
                                className="button button-cancel"
                                disabled={pending}
                                onClick={() => setAction(null)}
                                type="button"
                              >
                                {" "}
                                {tx("Back")}{" "}
                              </button>
                              <button
                                className="button button-edit"
                                disabled={pending}
                                type="submit"
                              >
                                {" "}
                                {tx("Save changes")}{" "}
                              </button>
                            </div>
                          </FormDialog>
                        ) : null}
                        {action === "submit" ? (
                          <ActionPanel
                            confirmLabel="Confirm submit"
                            error={expenseError}
                            hint="This sends the draft for approval."
                            onCancel={() => setAction(null)}
                            onSubmit={() => {
                              setPending(true);
                              void submitExpense(detail.id)
                                .then((row) => {
                                  setExpense(row);
                                  setAction(null);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The expense could not be submitted.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Submit this expense?")}
                          />
                        ) : null}
                        {action === "approve" ? (
                          <ActionPanel
                            confirmLabel="Confirm approval"
                            error={expenseError}
                            hint="Approval posts the expense."
                            onCancel={() => setAction(null)}
                            onSubmit={(form) => {
                              const body =
                                detail.payment_method === "CASH"
                                  ? {
                                      cash_session_id: text(
                                        form,
                                        "cash_session_id",
                                      ),
                                    }
                                  : {};
                              if (
                                detail.payment_method === "CASH" &&
                                !body.cash_session_id
                              ) {
                                setFields({
                                  cash_session_id:
                                    "Select an open cash session.",
                                });
                                setFormError("");
                                return;
                              }
                              setPending(true);
                              void approveExpense(detail.id, body)
                                .then((result) => {
                                  setExpense(result.expense);
                                  setAction(null);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The expense could not be approved.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Approve expense")}
                          >
                            {detail.payment_method === "CASH" ? (
                              <CashSessionSelect
                                collectionError={sessions.error}
                                currency={detail.currency}
                                error={fields.cash_session_id}
                                label="Open cash session"
                                pending={sessions.pending}
                                sessions={sessions.sessions}
                              />
                            ) : (
                              <p className="text-sm text-slate-600">
                                {" "}
                                {tx(
                                  "No cash session is sent for this method.",
                                )}{" "}
                              </p>
                            )}
                          </ActionPanel>
                        ) : null}
                        {action === "reject" ? (
                          <ActionPanel
                            confirmLabel="Confirm rejection"
                            error={expenseError}
                            hint="The expense stays unposted and keeps your reason."
                            onCancel={() => setAction(null)}
                            onSubmit={(form) => {
                              setPending(true);
                              void rejectExpense(detail.id, {
                                rejection_reason: text(
                                  form,
                                  "rejection_reason",
                                ),
                              })
                                .then((row) => {
                                  setExpense(row);
                                  setAction(null);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The expense could not be rejected.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Reject expense")}
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
                            error={expenseError}
                            hint="A cancelled draft cannot be submitted."
                            onCancel={() => setAction(null)}
                            onSubmit={(form) => {
                              setPending(true);
                              void cancelExpense(detail.id, {
                                reason: text(form, "reason"),
                              })
                                .then((row) => {
                                  setExpense(row);
                                  setAction(null);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The draft could not be cancelled.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Cancel draft")}
                          >
                            <ReasonField label="Cancellation reason" />
                          </ActionPanel>
                        ) : null}
                        {action === "void" && !salaryLocked ? (
                          <ActionPanel
                            confirmLabel="Confirm void"
                            error={expenseError}
                            hint="Voiding posts a reversal. The original row stays in the ledger."
                            onCancel={() => setAction(null)}
                            onSubmit={(form) => {
                              const body: VoidExpenseRequest = {
                                reason: text(form, "reason"),
                              };
                              const date = text(form, "business_date");
                              if (date) body.business_date = date;
                              if (detail.payment_method === "CASH") {
                                const sessionId = text(form, "cash_session_id");
                                if (!sessionId) {
                                  setFields({
                                    cash_session_id:
                                      "Select the destination open session.",
                                  });
                                  setFormError("");
                                  return;
                                }
                                body.cash_session_id = sessionId;
                              }
                              setPending(true);
                              void voidExpense(detail.id, body)
                                .then((result) => {
                                  setExpense(result.original_expense);
                                  setAction(null);
                                  clearFeedback();
                                  expenses.reload();
                                })
                                .catch((caught: unknown) =>
                                  fail(
                                    caught,
                                    "The expense could not be voided.",
                                    "expense",
                                  ),
                                )
                                .finally(() => setPending(false));
                            }}
                            pending={pending}
                            title={tx("Void expense")}
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
                            {detail.payment_method === "CASH" ? (
                              <CashSessionSelect
                                collectionError={sessions.error}
                                currency={detail.currency}
                                error={fields.cash_session_id}
                                label="Destination open session"
                                pending={sessions.pending}
                                sessions={sessions.sessions}
                              />
                            ) : null}
                          </ActionPanel>
                        ) : null}
                      </div>
                    ) : null}
                  </>
                ) : null}
              </section>
            ) : null}
          </div>
        </div>
      ) : null}

      {view === "categories" ? (
        <section
          aria-labelledby="categories-tab"
          className={cardClass}
          id="categories-panel"
          role="tabpanel"
        >
          <div className="border-b border-slate-100 px-5 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <h2
                className="text-lg font-bold text-slate-900"
                id="categories-title"
              >
                {" "}
                {tx("Categories")}{" "}
              </h2>
              {categories.rows.length > 0 ? (
                <p className="text-xs font-semibold text-slate-400 tabular-nums">
                  {categories.rows.length}
                </p>
              ) : null}
            </div>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
              {" "}
              {tx(
                "Protected salary and commission categories stay fixed. Custom categories can be renamed or archived here.",
              )}{" "}
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
            <div className="px-5 py-10 text-center">
              <p className="font-semibold text-slate-800">
                {" "}
                {tx("No categories were returned.")}{" "}
              </p>
            </div>
          ) : null}
          {editingCategory && canManageCategories ? (
            <FormDialog
              description={editingCategory.code}
              onClose={() => {
                if (pending) return;
                setEditingCategory(null);
                clearFeedback();
              }}
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                const order = text(form, "display_order") || "0";
                if (!/^\d+$/.test(order)) {
                  setFields({ display_order: "Enter a whole number." });
                  setErrorScope("category");
                  return;
                }
                setPending(true);
                void updateExpenseCategory(editingCategory.id, {
                  name: text(form, "name"),
                  display_order: Number(order),
                  is_active: form.get("is_active") === "on",
                })
                  .then(() => {
                    setEditingCategory(null);
                    clearFeedback();
                    categories.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(
                      caught,
                      "The category could not be updated.",
                      "category",
                    ),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title={tx("Update {name}", { name: editingCategory.name })}
            >
              <div className={fieldTone}>
                <Alert message={categoryError} />
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Name">
                    <TextInput
                      defaultValue={editingCategory.name}
                      name="name"
                      required
                    />
                  </Field>
                  <Field error={fields.display_order} label="Display order">
                    <TextInput
                      defaultValue={String(editingCategory.display_order)}
                      name="display_order"
                      type="number"
                    />
                  </Field>
                </div>
                <label className="flex min-h-11 items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700">
                  <input
                    className="size-4 accent-slate-800"
                    defaultChecked={editingCategory.is_active}
                    name="is_active"
                    type="checkbox"
                  />{" "}
                  {tx("Active")}{" "}
                </label>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <button
                    className="button button-cancel"
                    disabled={pending}
                    onClick={() => {
                      setEditingCategory(null);
                      clearFeedback();
                    }}
                    type="button"
                  >
                    {" "}
                    {tx("Back")}{" "}
                  </button>
                  <button
                    className="button button-edit"
                    disabled={pending}
                    type="submit"
                  >
                    {" "}
                    {tx("Save category")}{" "}
                  </button>
                </div>
              </div>
            </FormDialog>
          ) : null}
          {categories.rows.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {categories.rows.map((category) => (
                <li
                  className="flex flex-wrap items-center gap-3 px-5 py-3"
                  key={category.id}
                >
                  <div className="min-w-0 flex-1 basis-40">
                    <p className="truncate font-semibold text-slate-900">
                      {category.name}
                    </p>
                    <p className="mt-0.5 font-mono text-xs tracking-wide text-slate-400">
                      {category.code}
                    </p>
                  </div>
                  <StatusPill status={categoryStatus(category)} />
                  {canManageCategories && !protectedCategory(category) ? (
                    <button
                      className="button button-sm button-edit"
                      onClick={() => {
                        setEditingCategory(category);
                        clearFeedback();
                      }}
                      type="button"
                    >
                      {" "}
                      {tx("Edit")} {category.name}
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {showCategoryPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
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
          {canManageCategories ? (
            <div className="border-t border-slate-100 px-5 py-4">
              <button
                className="button button-create"
                onClick={() => {
                  setCreatingCategory(true);
                  setEditingCategory(null);
                  clearFeedback();
                }}
                type="button"
              >
                {" "}
                {tx("New custom category")}{" "}
              </button>
            </div>
          ) : null}
          {canManageCategories && creatingCategory ? (
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
                const order = text(form, "display_order") || "0";
                if (!/^\d+$/.test(order)) {
                  setFields({ display_order: "Enter a whole number." });
                  setFormError("");
                  setErrorScope("category");
                  return;
                }
                setPending(true);
                void createExpenseCategory({
                  name: text(form, "name"),
                  display_order: Number(order),
                })
                  .then(() => {
                    formElement.reset();
                    setCreatingCategory(false);
                    clearFeedback();
                    categories.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(
                      caught,
                      "The category could not be created.",
                      "category",
                    ),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title={tx("New custom category")}
            >
              <div className={`grid gap-4 ${fieldTone}`}>
                <Alert message={categoryError} />
                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_9rem]">
                  <Field error={fields.name} label="Name">
                    <TextInput name="name" required />
                  </Field>
                  <Field
                    error={editingCategory ? undefined : fields.display_order}
                    label="Display order"
                  >
                    <TextInput
                      defaultValue="0"
                      name="display_order"
                      type="number"
                    />
                  </Field>
                </div>
              </div>
              <div className="flex justify-end border-t border-slate-100 px-5 py-3">
                <button
                  className="button button-create"
                  disabled={pending}
                  type="submit"
                >
                  {" "}
                  {tx("Create category")}{" "}
                </button>
              </div>
            </FormDialog>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
