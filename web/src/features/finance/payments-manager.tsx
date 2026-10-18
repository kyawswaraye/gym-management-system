"use client";

import { useEffect, useState } from "react";

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
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney } from "@/features/finance/money";
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
import { listMembers, listMemberships } from "@/features/operations/client";
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
const PAGE = "25";

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
  const [status, setStatus] = useState("");
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
            financeError(caught, "The payment could not be loaded."),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, payments.rows]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
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

  return (
    <div className="grid gap-6">
      <form
        className="grid gap-3 md:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(`${status}|${method}|${purpose}`);
        }}
      >
        <Field label="Status">
          <SelectInput
            onChange={(event) => setStatus(event.target.value)}
            value={status}
          >
            <option value="">Any status</option>
            {[
              "DRAFT",
              "PENDING_APPROVAL",
              "POSTED",
              "REJECTED",
              "CANCELLED",
              "VOID",
            ].map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </SelectInput>
        </Field>
        <Field label="Method">
          <SelectInput
            onChange={(event) => setMethod(event.target.value)}
            value={method}
          >
            <option value="">Any method</option>
            {METHODS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </SelectInput>
        </Field>
        <Field label="Purpose">
          <SelectInput
            onChange={(event) => setPurpose(event.target.value)}
            value={purpose}
          >
            <option value="">Any purpose</option>
            <option value="MEMBERSHIP">Membership</option>
            <option value="OTHER">Other</option>
            <option value="PRODUCT">Product</option>
          </SelectInput>
        </Field>
        <button className="button self-end" type="submit">
          Apply filters
        </button>
      </form>
      <Alert message={payments.error} />
      {payments.pending && <Loading label="Loading payments." />}
      {!payments.pending && payments.rows.length === 0 && (
        <Empty label="No payments were returned." />
      )}
      {payments.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Payments</caption>
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3">Description</th>
                <th className="p-3">Amount</th>
                <th className="p-3">Method</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {payments.rows.map((row) => (
                <tr className="border-t border-slate-100" key={row.id}>
                  <td className="p-3">
                    <button
                      className="underline"
                      onClick={() => {
                        setSelectedId(row.id);
                        setAction(null);
                        setCorrection(null);
                      }}
                      type="button"
                    >
                      {row.description}
                    </button>
                  </td>
                  <td className="p-3">
                    {row.amount} {row.currency}
                  </td>
                  <td className="p-3">{row.method}</td>
                  <td className="p-3">
                    <StatusText status={row.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <CursorControls
        bounded={payments.bounded}
        hasMore={payments.hasMore}
        index={payments.index}
        onNext={() => void payments.goNext()}
        onPrevious={() => void payments.goPrevious()}
        pending={payments.pending}
      />
      {canCreate && (
        <form
          className="grid gap-3 rounded-lg border border-slate-200 p-4 md:grid-cols-2"
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
                await afterChange(created.id);
              })
              .catch((caught: unknown) =>
                fail(caught, "The draft could not be saved."),
              )
              .finally(() => setPending(false));
          }}
        >
          <h2 className="text-lg font-bold md:col-span-2">Record payment</h2>
          <Alert message={action === null ? formError : ""} />
          <PaymentFields
            currency={currency}
            defaultDate={defaultDate}
            fields={fields}
            memberSearch={memberSearch}
            members={members}
            memberships={memberships}
            onMember={setDraftMember}
            onSearch={setMemberSearch}
          />
          <button className="button" disabled={pending} type="submit">
            Save draft
          </button>
        </form>
      )}
      {payment && (
        <section aria-labelledby="payment-detail-title" className="grid gap-4">
          <h2 className="text-lg font-bold" id="payment-detail-title">
            Payment detail
          </h2>
          <Alert message={formError} />
          <ServerFacts
            rows={[
              { label: "Description", value: payment.description },
              {
                label: "Amount",
                value: `${payment.amount} ${payment.currency}`,
              },
              { label: "Purpose", value: payment.purpose },
              { label: "Method", value: payment.method },
              { label: "Status", value: payment.status },
              { label: "Business date", value: payment.business_date },
              {
                label: "Cash session",
                value: payment.cash_session_id ?? "None",
              },
            ]}
          />
          {productCorrection(payment) && (
            <p
              className="rounded-lg border border-amber-200 bg-amber-50 p-4"
              role="note"
            >
              RETAIL_CORRECTION_UNAVAILABLE. This product payment cannot be
              voided or refunded here. Use the retail sale full void so the
              payment, receipt, and stock return stay together.
            </p>
          )}
          {correction && (
            <p role="status">
              Refundable amount returned by the server:{" "}
              {correction.refundable_amount} {payment.currency}.
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            {payment.status === "DRAFT" && canUpdate && (
              <button
                className="underline"
                onClick={() => setAction("edit")}
                type="button"
              >
                Edit draft
              </button>
            )}
            {payment.status === "DRAFT" && canSubmit && (
              <button
                className="underline"
                onClick={() => setAction("submit")}
                type="button"
              >
                Submit
              </button>
            )}
            {payment.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline"
                onClick={() => setAction("approve")}
                type="button"
              >
                Approve
              </button>
            )}
            {payment.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline"
                onClick={() => setAction("reject")}
                type="button"
              >
                Reject
              </button>
            )}
            {payment.status === "DRAFT" && canUpdate && (
              <button
                className="underline"
                onClick={() => setAction("cancel")}
                type="button"
              >
                Cancel draft
              </button>
            )}
            {payment.status === "POSTED" &&
              canVoid &&
              !productCorrection(payment) && (
                <button
                  className="underline"
                  onClick={() => setAction("void")}
                  type="button"
                >
                  Void
                </button>
              )}
            {payment.status === "POSTED" &&
              canRefund &&
              !productCorrection(payment) && (
                <button
                  className="underline"
                  onClick={() => setAction("refund")}
                  type="button"
                >
                  Refund
                </button>
              )}
          </div>
          {action === "edit" && canUpdate && (
            <form
              className="grid gap-3 md:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                const body = draftBody(new FormData(event.currentTarget), true);
                if (!body) return;
                setPending(true);
                void updatePayment(payment.id, body)
                  .then(() => afterChange(payment.id))
                  .catch((caught: unknown) =>
                    fail(caught, "The draft could not be updated."),
                  )
                  .finally(() => setPending(false));
              }}
            >
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
              <button className="button" disabled={pending} type="submit">
                Save changes
              </button>
            </form>
          )}
          {action === "submit" && (
            <CommandForm
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
              title="Submit this draft for approval?"
            />
          )}
          {action === "approve" && (
            <CommandForm
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
              title="Approve payment"
            >
              {payment.method === "CASH" ? (
                <CashSessionSelect
                  currency={payment.currency}
                  error={fields.cash_session_id}
                  label="Open cash session"
                  sessions={sessions.sessions}
                />
              ) : (
                <p className="text-sm text-slate-600">
                  This is not a cash payment, so no cash session is sent.
                </p>
              )}
              <Alert message={sessions.error} />
            </CommandForm>
          )}
          {action === "reject" && (
            <CommandForm
              confirmLabel="Confirm rejection"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                const reason = text(form, "rejection_reason");
                if (!reason) return;
                setPending(true);
                void rejectPayment(payment.id, { rejection_reason: reason })
                  .then(() => afterChange(payment.id))
                  .catch((caught: unknown) =>
                    fail(caught, "The payment could not be rejected."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Reject payment"
            >
              <ReasonField
                error={fields.rejection_reason}
                label="Rejection reason"
                name="rejection_reason"
              />
            </CommandForm>
          )}
          {action === "cancel" && (
            <CommandForm
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
              title="Cancel draft"
            >
              <ReasonField error={fields.reason} label="Cancellation reason" />
            </CommandForm>
          )}
          {action === "void" && !productCorrection(payment) && (
            <CommandForm
              confirmLabel="Confirm void"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                const reason = text(form, "reason");
                if (!reason) return;
                const date = text(form, "business_date");
                const body: VoidPaymentRequest = { reason };
                if (date) body.business_date = date;
                if (payment.method === "CASH" && !legacyUnlinkedCash(payment)) {
                  const sessionId = text(form, "cash_session_id");
                  if (!sessionId) {
                    setFields({
                      cash_session_id: "Select the destination open session.",
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
              title="Void payment"
            >
              <ReasonField error={fields.reason} label="Void reason" />
              <Field error={fields.business_date} label="Correction date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  onChange={(event) => setCorrectionDate(event.target.value)}
                  type="date"
                />
              </Field>
              {legacyUnlinkedCash(payment) ? (
                <p className="text-sm text-slate-600">
                  This posted cash payment has no linked movement, so the void
                  does not name a destination session.
                </p>
              ) : payment.method === "CASH" ? (
                <CashSessionSelect
                  currency={payment.currency}
                  error={fields.cash_session_id}
                  label="Destination open session"
                  sessions={sessions.sessions}
                />
              ) : null}
            </CommandForm>
          )}
          {action === "refund" && !productCorrection(payment) && (
            <CommandForm
              confirmLabel="Confirm refund"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                const amount = canonicalMoney(text(form, "amount"));
                const reason = text(form, "reason");
                const refundedMethod = text(form, "method") as PaymentMethod;
                const date = text(form, "business_date");
                if (!amount || !reason || !date) {
                  setFields({
                    amount: "Enter an exact refund amount such as 1.00.",
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
                      cash_session_id: "Select the destination open session.",
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
              title="Refund payment"
            >
              {correction && (
                <p>
                  Refundable amount returned by the server:{" "}
                  {correction.refundable_amount}.
                </p>
              )}
              <Field error={fields.amount} label="Refund amount">
                <TextInput inputMode="decimal" name="amount" required />
              </Field>
              <ReasonField error={fields.reason} label="Refund reason" />
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
                      {item}
                    </option>
                  ))}
                </SelectInput>
              </Field>
              <Field error={fields.business_date} label="Business date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  onChange={(event) => setCorrectionDate(event.target.value)}
                  required
                  type="date"
                />
              </Field>
              <Field label="Reference">
                <TextInput name="reference_number" />
              </Field>
              {refundMethod === "CASH" && (
                <CashSessionSelect
                  currency={payment.currency}
                  error={fields.cash_session_id}
                  label="Destination open session"
                  sessions={sessions.sessions}
                />
              )}
            </CommandForm>
          )}
        </section>
      )}
      {canViewReceipts && (
        <section aria-labelledby="receipts-title" className="grid gap-4">
          <h2 className="text-lg font-bold" id="receipts-title">
            Receipts
          </h2>
          <Alert message={receipts.error} />
          {receipts.pending && <Loading label="Loading receipts." />}
          {!receipts.pending && receipts.rows.length === 0 && (
            <Empty label="No receipts were returned." />
          )}
          {receipts.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Receipts</caption>
                <thead className="bg-slate-50">
                  <tr>
                    <th className="p-3">Number</th>
                    <th className="p-3">Amount</th>
                    <th className="p-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {receipts.rows.map((row) => (
                    <tr className="border-t border-slate-100" key={row.id}>
                      <td className="p-3">
                        <button
                          className="underline"
                          onClick={() => {
                            setReceipt(row);
                            setReceiptAction(false);
                          }}
                          type="button"
                        >
                          {row.receipt_number}
                        </button>
                      </td>
                      <td className="p-3">
                        {row.amount} {row.currency}
                      </td>
                      <td className="p-3">
                        <StatusText status={row.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <CursorControls
            bounded={receipts.bounded}
            hasMore={receipts.hasMore}
            index={receipts.index}
            onNext={() => void receipts.goNext()}
            onPrevious={() => void receipts.goPrevious()}
            pending={receipts.pending}
          />
          {receipt && (
            <div className="grid gap-3">
              <ServerFacts
                rows={[
                  { label: "Number", value: receipt.receipt_number },
                  { label: "Member", value: receipt.member_name || "None" },
                  {
                    label: "Amount",
                    value: `${receipt.amount} ${receipt.currency}`,
                  },
                  { label: "Status", value: receipt.status },
                  { label: "Business date", value: receipt.business_date },
                ]}
              />
              <button
                className="underline"
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
                Download receipt
              </button>
              {receipt.status === "ISSUED" && canVoidReceipt && (
                <button
                  className="underline"
                  onClick={() => setReceiptAction(true)}
                  type="button"
                >
                  Void receipt document
                </button>
              )}
              {receiptAction && canVoidReceipt && (
                <CommandForm
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
                  title="Void only this receipt document"
                >
                  <p className="text-sm text-slate-600">
                    This voids the document. It does not change the payment,
                    cash, or stock.
                  </p>
                  <ReasonField
                    error={fields.reason}
                    label="Receipt void reason"
                  />
                </CommandForm>
              )}
            </div>
          )}
        </section>
      )}
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
}) {
  return (
    <>
      <Field error={fields.purpose} label="Purpose">
        <SelectInput
          defaultValue={payment?.purpose === "OTHER" ? "OTHER" : "MEMBERSHIP"}
          name="purpose"
          required
        >
          <option value="MEMBERSHIP">Membership</option>
          <option value="OTHER">Other</option>
        </SelectInput>
      </Field>
      <Field label="Find member">
        <TextInput
          onChange={(event) => onSearch(event.target.value)}
          value={memberSearch}
        />
      </Field>
      <Field error={fields.member_id} label="Member">
        <SelectInput
          defaultValue={payment?.member_id ?? ""}
          name="member_id"
          onChange={(event) => onMember(event.target.value)}
        >
          <option value="">No member</option>
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
          <option value="">No membership</option>
          {memberships.map((row) => (
            <option key={row.id} value={row.id}>
              {row.plan_name} · {row.status} · {row.final_amount} {row.currency}
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
      <Field error={fields.amount} label={`Amount (${currency})`}>
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
              {item}
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
    </>
  );
}
