"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { getCsrfToken } from "@/features/auth/client";
import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approvePayment,
  cancelPayment,
  createPayment,
  downloadReceiptDocument,
  getPayment,
  listPayments,
  listReceipts,
  refundPayment,
  rejectPayment,
  submitPayment,
  updatePayment,
  voidPayment,
  voidReceipt,
} from "@/features/finance/client";
import { FormDialog } from "@/components/form-dialog";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import { listMembers, listMemberships } from "@/features/operations/client";
import { actionButtonClass } from "@/lib/ui/action-button";
import { IdentifierValue } from "@/lib/ui/identifier";
import type {
  ApprovePaymentRequest,
  Member,
  Membership,
  Payment,
  PaymentCorrection,
  PaymentMethod,
  PaymentPurpose,
  PaymentUpdateRequest,
  PaymentWriteRequest,
  Receipt,
  RefundPaymentRequest,
  VoidPaymentRequest,
} from "@/types/api";

const METHODS = [
  "CASH",
  "BANK_TRANSFER",
  "CARD",
  "MOBILE_PAYMENT",
  "OTHER",
] as const satisfies readonly PaymentMethod[];
const STATUSES = [
  "DRAFT",
  "PENDING_APPROVAL",
  "POSTED",
  "REJECTED",
  "CANCELLED",
  "VOID",
] as const;
const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";

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
    DRAFT: "bg-slate-100 text-slate-700",
    PENDING_APPROVAL: "bg-amber-100 text-amber-800",
    POSTED: "bg-emerald-100 text-emerald-800",
    REJECTED: "bg-red-100 text-red-700",
    CANCELLED: "bg-slate-200 text-slate-600",
    VOID: "bg-rose-100 text-rose-700",
    ISSUED: "bg-emerald-100 text-emerald-800",
    VOIDED: "bg-rose-100 text-rose-700",
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

const PRETTY_FACTS = new Set(["Purpose", "Method", "Entry", "Status"]);

function FactGrid({ rows }: { rows: Array<{ label: string; value: string }> }) {
  const tx = useTx();
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="text-xs text-slate-500">{tx(row.label)}</dt>
          <dd className="mt-0.5 text-sm font-semibold break-words text-slate-900">
            <IdentifierValue
              value={tx(
                PRETTY_FACTS.has(row.label) ? pretty(row.value) : row.value,
              )}
            />
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
      {children}
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

type Action =
  | "submit"
  | "approve"
  | "reject"
  | "cancel"
  | "void"
  | "refund"
  | "edit"
  | null;

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function productCorrection(payment: Payment): boolean {
  return payment.purpose === "PRODUCT" || payment.retail_sale_id !== null;
}

function legacyUnlinkedCash(payment: Payment): boolean {
  return (
    payment.status === "POSTED" &&
    payment.method === "CASH" &&
    payment.cash_transaction_id === null
  );
}

function openDocument(html: string) {
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (!opened) {
    const link = document.createElement("a");
    link.href = url;
    link.download = "receipt.html";
    link.rel = "noopener";
    link.click();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function PaymentsManager({
  canCreate,
  canSubmit,
  canApprove,
  canUpdate,
  canVoid,
  canRefund,
  canViewReceipts,
  canVoidReceipt,
  currency,
  defaultDate,
}: {
  canCreate: boolean;
  canSubmit: boolean;
  canApprove: boolean;
  canUpdate: boolean;
  canVoid: boolean;
  canRefund: boolean;
  canViewReceipts: boolean;
  canVoidReceipt: boolean;
  currency: string;
  defaultDate: string;
}) {
  const tx = useTx();
  const [status, setStatus] = useState("");
  const [section, setSection] = useState<"payments" | "receipts">("payments");
  const [method, setMethod] = useState("");
  const [purpose, setPurpose] = useState("");
  const [applied, setApplied] = useState("||");
  const [selectedId, setSelectedId] = useState("");
  const [payment, setPayment] = useState<Payment | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [memberSearch, setMemberSearch] = useState("");
  const [draftMember, setDraftMember] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [recording, setRecording] = useState(false);
  const [refundMethod, setRefundMethod] =
    useState<PaymentMethod>("BANK_TRANSFER");
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [correction, setCorrection] = useState<PaymentCorrection | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [receiptAction, setReceiptAction] = useState(false);

  const payments = useCursorPages(applied, (after) => {
    const [filterStatus, filterMethod, filterPurpose] = applied.split("|");
    return listPayments({
      ...(filterStatus ? { status: filterStatus } : {}),
      ...(filterMethod ? { method: filterMethod } : {}),
      ...(filterPurpose ? { purpose: filterPurpose } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    });
  });
  const receipts = useCursorPages(
    canViewReceipts ? "receipts" : "hidden",
    (after) => {
      if (!canViewReceipts) {
        return Promise.resolve({
          data: [],
          meta: { has_more: false, next_after: null },
        });
      }
      return listReceipts({ limit: PAGE, ...(after ? { after } : {}) });
    },
  );
  const sessions = useOpenCashSessions(
    action === "approve" && payment
      ? payment.business_date
      : action === "void" || action === "refund"
        ? correctionDate
        : "",
  );

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void listMembers({
        limit: 25,
        ...(memberSearch ? { search: memberSearch } : {}),
      })
        .then((page) => {
          if (active) setMembers(page.data);
        })
        .catch(() => {
          if (active) setMembers([]);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [memberSearch]);

  useEffect(() => {
    if (!draftMember) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void listMemberships({ member_id: draftMember, limit: 25 })
        .then((page) => {
          if (active) setMemberships(page.data);
        })
        .catch(() => {
          if (active) setMemberships([]);
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [draftMember]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getPayment(selectedId)
        .then((row) => {
          if (!active) return;
          setPayment(row);
          setFormError("");
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setPayment(null);
          setFormError(
            financeError(caught, tx("The payment could not be loaded.")),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, payments.rows, tx]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  function clearFeedback() {
    setFields({});
    setFormError("");
  }

  async function afterChange(id: string) {
    setAction(null);
    setFields({});
    setFormError("");
    payments.reload();
    receipts.reload();
    const row = await getPayment(id);
    setPayment(row);
    setSelectedId(id);
  }

  function draftBody(
    form: FormData,
    editing: boolean,
  ): PaymentWriteRequest | PaymentUpdateRequest | null {
    const amount = canonicalMoney(text(form, "amount"));
    const draftPurpose = text(form, "purpose");
    if (
      !amount ||
      (draftPurpose !== "MEMBERSHIP" && draftPurpose !== "OTHER")
    ) {
      setFields({ amount: "Enter an exact amount such as 10.00." });
      return null;
    }
    const memberId = text(form, "member_id");
    const membershipId = text(form, "membership_id");
    const reference = text(form, "reference_number");
    const shared = {
      purpose: draftPurpose as PaymentPurpose,
      description: text(form, "description"),
      amount,
      currency,
      method: text(form, "method") as PaymentMethod,
      business_date: text(form, "business_date"),
      ...(reference ? { reference_number: reference } : {}),
      ...(memberId ? { member_id: memberId } : {}),
    };
    if (draftPurpose === "MEMBERSHIP") {
      if (!memberId || !membershipId) {
        setFields({ membership_id: "Select the member and membership." });
        return null;
      }
      return { ...shared, membership_id: membershipId };
    }
    return editing ? { ...shared, membership_id: null } : shared;
  }

  const filtered = applied !== "||";
  const showPaymentPaging =
    payments.index > 0 || payments.hasMore || payments.bounded;

  return (
    <div className="grid gap-6">
      {canViewReceipts ? (
        <div
          aria-label={tx("Payment sections")}
          className="flex flex-wrap gap-2"
          role="tablist"
        >
          {(
            [
              ["payments", "Payments"],
              ["receipts", "Receipts"],
            ] as const
          ).map(([id, label]) => (
            <button
              aria-controls={`${id}-panel`}
              aria-selected={section === id}
              className={viewTabClass(section === id)}
              id={`${id}-tab`}
              key={id}
              onClick={() => setSection(id)}
              role="tab"
              type="button"
            >
              {tx(label)}
            </button>
          ))}
        </div>
      ) : null}
      {section === "payments" || !canViewReceipts ? (
        <div
          aria-labelledby={canViewReceipts ? "payments-tab" : undefined}
          className="grid gap-6"
          id={canViewReceipts ? "payments-panel" : undefined}
          role={canViewReceipts ? "tabpanel" : undefined}
        >
          <div
            className={
              payment
                ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(22rem,0.9fr)]"
                : "grid"
            }
          >
            <section
              aria-busy={payments.pending}
              aria-labelledby="payment-ledger-title"
              className={cardClass}
            >
              <div
                className={`flex flex-col gap-4 border-b border-slate-200 px-4 py-4 sm:px-5 ${payment ? "" : "xl:flex-row xl:items-end xl:justify-between"}`}
              >
                <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
                  <h2
                    className="text-lg font-bold text-slate-900"
                    id="payment-ledger-title"
                  >
                    {" "}
                    {tx("Payment ledger")}{" "}
                  </h2>
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
                      {tx("Record payment")}{" "}
                    </button>
                  ) : null}
                </div>
                <form
                  className={`min-w-0 ${payment ? "" : "xl:max-w-3xl xl:flex-1"}`}
                  onSubmit={(event) => {
                    event.preventDefault();
                    setApplied(`${status}|${method}|${purpose}`);
                  }}
                >
                  <div
                    className={`grid gap-3 sm:grid-cols-2 sm:items-end [&_.field]:text-xs [&_.field]:font-medium [&_.field]:text-slate-500 ${payment ? "" : "xl:grid-cols-4"}`}
                  >
                    <Field label="Status">
                      <SelectInput
                        onChange={(event) => setStatus(event.target.value)}
                        value={status}
                      >
                        <option value="">{tx("Any status")}</option>
                        {STATUSES.map((item) => (
                          <option key={item} value={item}>
                            {tx(pretty(item))}
                          </option>
                        ))}
                      </SelectInput>
                    </Field>
                    <Field label="Method">
                      <SelectInput
                        onChange={(event) => setMethod(event.target.value)}
                        value={method}
                      >
                        <option value="">{tx("Any method")}</option>
                        {METHODS.map((item) => (
                          <option key={item} value={item}>
                            {tx(pretty(item))}
                          </option>
                        ))}
                      </SelectInput>
                    </Field>
                    <Field label="Purpose">
                      <SelectInput
                        onChange={(event) => setPurpose(event.target.value)}
                        value={purpose}
                      >
                        <option value="">{tx("Any purpose")}</option>
                        <option value="MEMBERSHIP">{tx("Membership")}</option>
                        <option value="OTHER">{tx("Other")}</option>
                        <option value="PRODUCT">{tx("Product")}</option>
                      </SelectInput>
                    </Field>
                    <button className="button w-full sm:w-auto" type="submit">
                      {" "}
                      {tx("Apply filters")}{" "}
                    </button>
                  </div>
                </form>
              </div>

              {payments.pending && payments.rows.length === 0 ? (
                <div className="grid gap-3 p-4 sm:p-5" role="status">
                  <p className="text-sm font-medium text-slate-600">
                    {" "}
                    {tx("Loading payments.")}{" "}
                  </p>
                  <div className="grid gap-2">
                    {[0, 1, 2].map((item) => (
                      <div
                        className="h-14 animate-pulse rounded-xl bg-slate-100"
                        key={item}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
              {payments.pending && payments.rows.length > 0 ? (
                <p
                  className="px-5 pt-4 text-sm font-medium text-slate-600"
                  role="status"
                >
                  {" "}
                  {tx("Loading payments.")}{" "}
                </p>
              ) : null}
              {!payments.pending && payments.error ? (
                <div className="p-4 sm:p-5">
                  <Alert message={payments.error} />
                </div>
              ) : null}
              {!payments.pending &&
              !payments.error &&
              payments.rows.length === 0 ? (
                <div className="px-4 py-12 text-center sm:px-5">
                  <p className="font-semibold text-slate-800">
                    {" "}
                    {tx("No payments were returned.")}{" "}
                  </p>
                  <p className="mt-1 text-sm text-slate-500">
                    {filtered
                      ? tx("Try another status, method, or purpose.")
                      : tx(
                          "Recorded drafts and posted payments will show up here.",
                        )}
                  </p>
                </div>
              ) : null}
              {payments.rows.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <caption className="sr-only">{tx("Payments")}</caption>
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
                          className="px-5 py-2.5 text-xs font-semibold"
                          scope="col"
                        >
                          {" "}
                          {tx("Amount")}{" "}
                        </th>
                        <th
                          className="px-5 py-2.5 text-xs font-semibold"
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
                      {payments.rows.map((row) => {
                        const selected = selectedId === row.id;
                        return (
                          <tr
                            className={
                              selected ? "bg-amber-50" : "hover:bg-slate-50"
                            }
                            key={row.id}
                          >
                            <td className="px-5 py-3">
                              <button
                                aria-current={selected ? "true" : undefined}
                                className="text-left font-semibold"
                                onClick={() => {
                                  setSelectedId(row.id);
                                  setAction(null);
                                  setCorrection(null);
                                }}
                                type="button"
                              >
                                {tx(row.description)}
                              </button>
                            </td>
                            <td className="px-5 py-3 font-semibold whitespace-nowrap text-slate-900 tabular-nums">
                              {row.amount}{" "}
                              <span className="font-medium text-slate-500">
                                {tx(row.currency)}
                              </span>
                            </td>
                            <td className="px-5 py-3 whitespace-nowrap text-slate-600">
                              {tx(pretty(row.method))}
                            </td>
                            <td className="px-5 py-3 whitespace-nowrap text-slate-600">
                              {row.business_date}
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
              {showPaymentPaging ? (
                <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
                  <CursorControls
                    bounded={payments.bounded}
                    hasMore={payments.hasMore}
                    index={payments.index}
                    onNext={() => void payments.goNext()}
                    onPrevious={() => void payments.goPrevious()}
                    pending={payments.pending}
                  />
                </div>
              ) : null}
            </section>

            {payment && (
              <section
                aria-labelledby="payment-detail-title"
                className={`${cardClass} xl:sticky xl:top-4`}
              >
                <div className="flex items-start justify-between gap-3 px-5 pt-4">
                  <div className="min-w-0">
                    <h2
                      className="text-xs font-semibold tracking-[0.14em] text-slate-400 uppercase"
                      id="payment-detail-title"
                    >
                      {" "}
                      {tx("Payment detail")}{" "}
                    </h2>
                    <p className="mt-1 text-lg font-bold break-words text-slate-900">
                      {tx(payment.description)}
                    </p>
                    <p className="mt-0.5 text-sm font-semibold text-slate-600 tabular-nums">
                      {payment.amount} {tx(payment.currency)}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-1 font-mono text-[0.68rem] font-bold tracking-wide ${statusTone(payment.status)}`}
                  >
                    {tx(payment.status)}
                  </span>
                </div>
                {formError && (action === "edit" || action === null) ? (
                  <div className="px-5 pt-3">
                    <Alert message={formError} />
                  </div>
                ) : null}
                <div className="px-5 pt-1 pb-4">
                  <FactGrid
                    rows={[
                      { label: tx("Purpose"), value: payment.purpose },
                      { label: tx("Method"), value: payment.method },
                      {
                        label: tx("Business date"),
                        value: payment.business_date,
                      },
                      { label: tx("Entry"), value: payment.entry_type },
                      {
                        label: tx("Cash session"),
                        value: payment.cash_session_id ?? "None",
                      },
                    ]}
                  />
                </div>
                <div className="grid gap-3 border-t border-slate-100 px-5 py-4">
                  {productCorrection(payment) && (
                    <p
                      className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950"
                      role="note"
                    >
                      {" "}
                      {tx(
                        "RETAIL_CORRECTION_UNAVAILABLE. This product payment cannot be voided or refunded here. Use the retail sale full void so the payment, receipt, and stock return stay together.",
                      )}{" "}
                    </p>
                  )}
                  {correction && (
                    <p
                      className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900"
                      role="status"
                    >
                      {" "}
                      {tx("Refundable amount returned by the server:")}{" "}
                      {correction.refundable_amount} {tx(payment.currency)}.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2">
                    {payment.status === "DRAFT" && canUpdate && (
                      <button
                        className="button button-edit"
                        onClick={() => setAction("edit")}
                        type="button"
                      >
                        {" "}
                        {tx("Edit draft")}{" "}
                      </button>
                    )}
                    {payment.status === "DRAFT" && canSubmit && (
                      <button
                        className="button"
                        onClick={() => setAction("submit")}
                        type="button"
                      >
                        {" "}
                        {tx("Submit")}{" "}
                      </button>
                    )}
                    {payment.status === "PENDING_APPROVAL" && canApprove && (
                      <button
                        className="button button-success"
                        onClick={() => setAction("approve")}
                        type="button"
                      >
                        {" "}
                        {tx("Approve")}{" "}
                      </button>
                    )}
                    {payment.status === "PENDING_APPROVAL" && canApprove && (
                      <button
                        className="button button-warn"
                        onClick={() => setAction("reject")}
                        type="button"
                      >
                        {" "}
                        {tx("Reject")}{" "}
                      </button>
                    )}
                    {payment.status === "DRAFT" && canUpdate && (
                      <button
                        className="button button-cancel"
                        onClick={() => setAction("cancel")}
                        type="button"
                      >
                        {" "}
                        {tx("Cancel draft")}{" "}
                      </button>
                    )}
                    {payment.status === "POSTED" &&
                      canVoid &&
                      !productCorrection(payment) && (
                        <button
                          className="button button-delete"
                          onClick={() => {
                            setCorrectionDate(defaultDate);
                            setAction("void");
                          }}
                          type="button"
                        >
                          {" "}
                          {tx("Void")}{" "}
                        </button>
                      )}
                    {payment.status === "POSTED" &&
                      canRefund &&
                      !productCorrection(payment) && (
                        <button
                          className="button button-warn"
                          onClick={() => {
                            setCorrectionDate(defaultDate);
                            setAction("refund");
                          }}
                          type="button"
                        >
                          {" "}
                          {tx("Refund")}{" "}
                        </button>
                      )}
                  </div>
                  {action === "edit" && canUpdate && (
                    <FormDialog
                      onClose={() => {
                        if (pending) return;
                        setAction(null);
                        clearFeedback();
                      }}
                      onSubmit={(event) => {
                        event.preventDefault();
                        const body = draftBody(
                          new FormData(event.currentTarget),
                          true,
                        );
                        if (!body) return;
                        setPending(true);
                        void updatePayment(payment.id, body)
                          .then(() => afterChange(payment.id))
                          .catch((caught: unknown) =>
                            fail(caught, "The draft could not be updated."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Edit draft")}
                      wide
                    >
                      <Alert message={formError} />
                      <div className="[&_.field]:text-sm [&_.field]:font-semibold [&_.field]:text-slate-700">
                        <PaymentFields
                          currency={currency}
                          defaultDate={payment.business_date}
                          fields={fields}
                          memberSearch={memberSearch}
                          members={members}
                          memberships={memberships}
                          onMember={setDraftMember}
                          onSearch={setMemberSearch}
                          payment={payment}
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
                  )}
                  {action === "submit" && (
                    <ActionPanel
                      confirmLabel="Confirm submit"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={() => {
                        setPending(true);
                        void submitPayment(payment.id)
                          .then(() => afterChange(payment.id))
                          .catch((caught: unknown) =>
                            fail(caught, "The payment could not be submitted."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Submit this draft for approval?")}
                    />
                  )}
                  {action === "approve" && (
                    <ActionPanel
                      confirmLabel="Confirm approval"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={(form) => {
                        const body: ApprovePaymentRequest = {};
                        if (payment.method === "CASH") {
                          const sessionId = text(form, "cash_session_id");
                          if (!sessionId) {
                            setFields({
                              cash_session_id: "Select an open cash session.",
                            });
                            return;
                          }
                          body.cash_session_id = sessionId;
                        }
                        setPending(true);
                        void approvePayment(payment.id, body)
                          .then(() => afterChange(payment.id))
                          .catch((caught: unknown) =>
                            fail(caught, "The payment could not be approved."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Approve payment")}
                    >
                      {payment.method === "CASH" ? (
                        <CashSessionSelect
                          collectionError={sessions.error}
                          currency={payment.currency}
                          error={fields.cash_session_id}
                          label="Open cash session"
                          pending={sessions.pending}
                          sessions={sessions.sessions}
                        />
                      ) : (
                        <p className="text-sm text-slate-600">
                          {" "}
                          {tx(
                            "This is not a cash payment, so no cash session is sent.",
                          )}{" "}
                        </p>
                      )}
                    </ActionPanel>
                  )}
                  {action === "reject" && (
                    <ActionPanel
                      confirmLabel="Confirm rejection"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={(form) => {
                        const reason = text(form, "rejection_reason");
                        if (!reason) return;
                        setPending(true);
                        void rejectPayment(payment.id, {
                          rejection_reason: reason,
                        })
                          .then(() => afterChange(payment.id))
                          .catch((caught: unknown) =>
                            fail(caught, "The payment could not be rejected."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Reject payment")}
                    >
                      <ReasonField
                        error={fields.rejection_reason}
                        label="Rejection reason"
                        name="rejection_reason"
                      />
                    </ActionPanel>
                  )}
                  {action === "cancel" && (
                    <ActionPanel
                      confirmLabel="Confirm cancellation"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={(form) => {
                        const reason = text(form, "reason");
                        if (!reason) return;
                        setPending(true);
                        void cancelPayment(payment.id, { reason })
                          .then(() => afterChange(payment.id))
                          .catch((caught: unknown) =>
                            fail(caught, "The draft could not be cancelled."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Cancel draft")}
                    >
                      <ReasonField
                        error={fields.reason}
                        label="Cancellation reason"
                      />
                    </ActionPanel>
                  )}
                  {action === "void" && !productCorrection(payment) && (
                    <ActionPanel
                      confirmLabel="Confirm void"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={(form) => {
                        const reason = text(form, "reason");
                        if (!reason) return;
                        const date = text(form, "business_date");
                        const body: VoidPaymentRequest = { reason };
                        if (date) body.business_date = date;
                        if (
                          payment.method === "CASH" &&
                          !legacyUnlinkedCash(payment)
                        ) {
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
                        void voidPayment(payment.id, body)
                          .then(async (result) => {
                            setCorrection(result);
                            await afterChange(payment.id);
                          })
                          .catch((caught: unknown) =>
                            fail(caught, "The payment could not be voided."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Void payment")}
                    >
                      <ReasonField error={fields.reason} label="Void reason" />
                      <Field
                        error={fields.business_date}
                        label="Correction date"
                      >
                        <TextInput
                          defaultValue={defaultDate}
                          name="business_date"
                          onChange={(event) =>
                            setCorrectionDate(event.target.value)
                          }
                          type="date"
                        />
                      </Field>
                      {legacyUnlinkedCash(payment) ? (
                        <p className="text-sm text-slate-600">
                          {" "}
                          {tx(
                            "This posted cash payment has no linked movement, so the void does not name a destination session.",
                          )}{" "}
                        </p>
                      ) : payment.method === "CASH" ? (
                        <CashSessionSelect
                          collectionError={sessions.error}
                          currency={payment.currency}
                          error={fields.cash_session_id}
                          label="Destination open session"
                          pending={sessions.pending}
                          sessions={sessions.sessions}
                        />
                      ) : null}
                    </ActionPanel>
                  )}
                  {action === "refund" && !productCorrection(payment) && (
                    <ActionPanel
                      confirmLabel="Confirm refund"
                      error={formError}
                      onCancel={() => setAction(null)}
                      onSubmit={(form) => {
                        const amount = canonicalMoney(text(form, "amount"));
                        const reason = text(form, "reason");
                        const refundedMethod = text(
                          form,
                          "method",
                        ) as PaymentMethod;
                        const date = text(form, "business_date");
                        if (!amount || !reason || !date) {
                          setFields({
                            amount:
                              "Enter an exact refund amount such as 1.00.",
                          });
                          return;
                        }
                        const reference = text(form, "reference_number");
                        const body: RefundPaymentRequest = {
                          amount,
                          reason,
                          method: refundedMethod,
                          business_date: date,
                          ...(reference ? { reference_number: reference } : {}),
                        };
                        if (refundedMethod === "CASH") {
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
                        void refundPayment(payment.id, body)
                          .then(async (result) => {
                            setCorrection(result);
                            await afterChange(payment.id);
                          })
                          .catch((caught: unknown) =>
                            fail(caught, "The refund could not be recorded."),
                          )
                          .finally(() => setPending(false));
                      }}
                      pending={pending}
                      title={tx("Refund payment")}
                    >
                      {correction && (
                        <p>
                          {" "}
                          {tx("Refundable amount returned by the server:")}{" "}
                          {correction.refundable_amount}.
                        </p>
                      )}
                      <Field error={fields.amount} label="Refund amount">
                        <TextInput inputMode="decimal" name="amount" required />
                      </Field>
                      <ReasonField
                        error={fields.reason}
                        label="Refund reason"
                      />
                      <Field error={fields.method} label="Refund method">
                        <SelectInput
                          name="method"
                          onChange={(event) =>
                            setRefundMethod(event.target.value as PaymentMethod)
                          }
                          required
                          value={refundMethod}
                        >
                          {METHODS.map((item) => (
                            <option key={item} value={item}>
                              {tx(pretty(item))}
                            </option>
                          ))}
                        </SelectInput>
                      </Field>
                      <Field error={fields.business_date} label="Business date">
                        <TextInput
                          defaultValue={defaultDate}
                          name="business_date"
                          onChange={(event) =>
                            setCorrectionDate(event.target.value)
                          }
                          required
                          type="date"
                        />
                      </Field>
                      <Field label="Reference">
                        <TextInput name="reference_number" />
                      </Field>
                      {refundMethod === "CASH" && (
                        <CashSessionSelect
                          collectionError={sessions.error}
                          currency={payment.currency}
                          error={fields.cash_session_id}
                          label="Destination open session"
                          pending={sessions.pending}
                          sessions={sessions.sessions}
                        />
                      )}
                    </ActionPanel>
                  )}
                </div>
              </section>
            )}
          </div>
          {canCreate && recording ? (
            <FormDialog
              onClose={() => {
                if (pending) return;
                setRecording(false);
                clearFeedback();
              }}
              onSubmit={(event) => {
                event.preventDefault();
                const formElement = event.currentTarget;
                const body = draftBody(new FormData(formElement), false);
                if (!body) return;
                setPending(true);
                void createPayment(body as PaymentWriteRequest)
                  .then(async (created) => {
                    formElement.reset();
                    setDraftMember("");
                    setRecording(false);
                    await afterChange(created.id);
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The draft could not be saved."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title={tx("Record payment")}
              wide
            >
              <Alert message={formError} />
              <div className="[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600">
                <PaymentFields
                  currency={currency}
                  defaultDate={defaultDate}
                  fields={fields}
                  memberSearch={memberSearch}
                  members={members}
                  memberships={memberships}
                  onMember={setDraftMember}
                  onSearch={setMemberSearch}
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
        </div>
      ) : null}
      {canViewReceipts && section === "receipts" ? (
        <section
          aria-busy={receipts.pending}
          aria-labelledby="receipts-tab"
          className={cardClass}
          id="receipts-panel"
          role="tabpanel"
        >
          <div className="border-b border-slate-200 px-5 py-3">
            <h2
              className="text-base font-bold text-slate-900"
              id="receipts-title"
            >
              {" "}
              {tx("Receipts")}{" "}
            </h2>
          </div>
          {receipts.pending && receipts.rows.length === 0 ? (
            <div className="grid gap-3 p-4 sm:p-5" role="status">
              <p className="text-sm font-medium text-slate-600">
                {" "}
                {tx("Loading receipts.")}{" "}
              </p>
              <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
            </div>
          ) : null}
          {receipts.pending && receipts.rows.length > 0 ? (
            <p
              className="px-5 pt-4 text-sm font-medium text-slate-600"
              role="status"
            >
              {" "}
              {tx("Loading receipts.")}{" "}
            </p>
          ) : null}
          {!receipts.pending && receipts.error ? (
            <div className="p-4 sm:p-5">
              <Alert message={receipts.error} />
            </div>
          ) : null}
          {!receipts.pending &&
          !receipts.error &&
          receipts.rows.length === 0 ? (
            <div className="px-4 py-12 text-center sm:px-5">
              <p className="font-semibold text-slate-800">
                {" "}
                {tx("No receipts were returned.")}{" "}
              </p>
              <p className="mt-1 text-sm text-slate-500">
                {" "}
                {tx("A receipt appears here after a payment is posted.")}{" "}
              </p>
            </div>
          ) : null}
          {receipts.rows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">{tx("Receipts")}</caption>
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Number")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Amount")}{" "}
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
                  {receipts.rows.map((row) => {
                    const selected = receipt?.id === row.id;
                    return (
                      <tr
                        className={
                          selected ? "bg-amber-50" : "hover:bg-slate-50"
                        }
                        key={row.id}
                      >
                        <td className="px-5 py-3">
                          <button
                            aria-current={selected ? "true" : undefined}
                            className="font-semibold"
                            onClick={() => {
                              setReceipt(row);
                              setReceiptAction(false);
                            }}
                            type="button"
                          >
                            {row.receipt_number}
                          </button>
                        </td>
                        <td className="px-5 py-3 font-semibold whitespace-nowrap text-slate-900 tabular-nums">
                          {row.amount}{" "}
                          <span className="font-medium text-slate-500">
                            {tx(row.currency)}
                          </span>
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
          {receipts.index > 0 || receipts.hasMore || receipts.bounded ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={receipts.bounded}
                hasMore={receipts.hasMore}
                index={receipts.index}
                onNext={() => void receipts.goNext()}
                onPrevious={() => void receipts.goPrevious()}
                pending={receipts.pending}
              />
            </div>
          ) : null}
          {receipt && (
            <div className="grid gap-3 border-t border-slate-100 px-5 py-4">
              <p className="text-sm font-semibold text-slate-900">
                {receipt.receipt_number}
              </p>
              <FactGrid
                rows={[
                  { label: tx("Member"), value: receipt.member_name || "None" },
                  {
                    label: tx("Amount"),
                    value: `${receipt.amount} ${tx(receipt.currency)}`,
                  },
                  { label: tx("Status"), value: receipt.status },
                  { label: tx("Business date"), value: receipt.business_date },
                ]}
              />
              <div className="flex flex-wrap gap-2">
                <button
                  className="button"
                  onClick={() => {
                    setPending(true);
                    void getCsrfToken()
                      .then(() => downloadReceiptDocument(receipt.id))
                      .then(openDocument)
                      .catch((caught: unknown) =>
                        fail(caught, "The receipt could not be downloaded."),
                      )
                      .finally(() => setPending(false));
                  }}
                  type="button"
                >
                  {" "}
                  {tx("Download receipt")}{" "}
                </button>
                {receipt.status === "ISSUED" && canVoidReceipt && (
                  <button
                    className="button button-delete"
                    onClick={() => setReceiptAction(true)}
                    type="button"
                  >
                    {" "}
                    {tx("Void receipt document")}{" "}
                  </button>
                )}
              </div>
              {receiptAction && canVoidReceipt && (
                <ActionPanel
                  confirmLabel="Confirm receipt void"
                  error={formError}
                  onCancel={() => setReceiptAction(false)}
                  onSubmit={(form) => {
                    const reason = text(form, "reason");
                    if (!reason) return;
                    setPending(true);
                    void voidReceipt(receipt.id, { reason })
                      .then(async (updated) => {
                        setReceipt(updated);
                        setReceiptAction(false);
                        receipts.reload();
                      })
                      .catch((caught: unknown) =>
                        fail(caught, "The receipt could not be voided."),
                      )
                      .finally(() => setPending(false));
                  }}
                  pending={pending}
                  title={tx("Void only this receipt document")}
                >
                  <p className="text-sm text-slate-600">
                    {" "}
                    {tx(
                      "This voids the document. It does not change the payment, cash, or stock.",
                    )}{" "}
                  </p>
                  <ReasonField
                    error={fields.reason}
                    label="Receipt void reason"
                  />
                </ActionPanel>
              )}
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}

function PaymentFields({
  currency,
  defaultDate,
  fields,
  members,
  memberships,
  memberSearch,
  onMember,
  onSearch,
  payment,
  wide = false,
}: {
  currency: string;
  defaultDate: string;
  fields: Record<string, string>;
  members: readonly Member[];
  memberships: readonly Membership[];
  memberSearch: string;
  onMember: (id: string) => void;
  onSearch: (value: string) => void;
  payment?: Payment;
  wide?: boolean;
}) {
  const tx = useTx();
  return (
    <div
      className={
        wide
          ? "grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
          : "grid gap-4 sm:grid-cols-2"
      }
    >
      <Field error={fields.purpose} label="Purpose">
        <SelectInput
          defaultValue={payment?.purpose === "OTHER" ? "OTHER" : "MEMBERSHIP"}
          name="purpose"
          required
        >
          <option value="MEMBERSHIP">{tx("Membership")}</option>
          <option value="OTHER">{tx("Other")}</option>
        </SelectInput>
      </Field>
      <Field label="Find member">
        <TextInput
          onChange={(event) => onSearch(event.target.value)}
          placeholder={tx("Name or member number")}
          value={memberSearch}
        />
      </Field>
      <Field error={fields.member_id} label="Member">
        <SelectInput
          defaultValue={payment?.member_id ?? ""}
          name="member_id"
          onChange={(event) => onMember(event.target.value)}
        >
          <option value="">{tx("No member")}</option>
          {members.map((member) => (
            <option key={member.id} value={member.id}>
              {member.member_number} — {member.first_name} {member.last_name}
            </option>
          ))}
        </SelectInput>
      </Field>
      <Field error={fields.membership_id} label="Membership">
        <SelectInput
          defaultValue={payment?.membership_id ?? ""}
          name="membership_id"
        >
          <option value="">{tx("No membership")}</option>
          {memberships.map((row) => (
            <option key={row.id} value={row.id}>
              {row.plan_name} · {tx(row.status)} · {row.final_amount} {tx(row.currency)}
            </option>
          ))}
        </SelectInput>
      </Field>
      <Field error={fields.description} label="Description">
        <TextInput
          defaultValue={payment?.description ?? ""}
          name="description"
          required
        />
      </Field>
      <Field error={fields.amount} label={tx("Amount ({currency})", { currency: tx(currency) })}>
        <TextInput
          defaultValue={payment?.amount ?? ""}
          inputMode="decimal"
          name="amount"
          required
        />
      </Field>
      <Field error={fields.method} label="Method">
        <SelectInput
          defaultValue={payment?.method ?? "CASH"}
          name="method"
          required
        >
          {METHODS.map((item) => (
            <option key={item} value={item}>
              {tx(pretty(item))}
            </option>
          ))}
        </SelectInput>
      </Field>
      <Field error={fields.business_date} label="Business date">
        <TextInput
          defaultValue={payment?.business_date ?? defaultDate}
          name="business_date"
          required
          type="date"
        />
      </Field>
      <Field error={fields.reference_number} label="Reference">
        <TextInput
          defaultValue={payment?.reference_number ?? ""}
          name="reference_number"
        />
      </Field>
    </div>
  );
}
