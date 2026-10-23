"use client";

import { useEffect, useState } from "react";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveRetailSale,
  cancelRetailSale,
  createRetailSale,
  getRetailSale,
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
  previewLineAmount,
} from "@/features/finance/money";
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
import { listMembers } from "@/features/operations/client";
import { listProducts } from "@/features/finance/client";
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

type LineDraft = {
  key: string;
  productId: string;
  quantity: string;
  discount: string;
};

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
  const [status, setStatus] = useState("");
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
  const [action, setAction] = useState<
    "submit" | "approve" | "reject" | "cancel" | "void" | "return" | null
  >(null);
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
          setFormError(financeError(caught, "The sale could not be loaded."));
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, sales.rows]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
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

  return (
    <div className="grid gap-6">
      <form
        className="flex flex-wrap gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(status);
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
        <button className="button self-end" type="submit">
          Apply filters
        </button>
      </form>
      <Alert message={sales.error || formError} />
      {sales.pending && <Loading label="Loading retail sales." />}
      {!sales.pending && sales.rows.length === 0 && (
        <Empty label="No retail sales were returned." />
      )}
      {sales.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Retail sales</caption>
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3">Date</th>
                <th className="p-3">Server total</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {sales.rows.map((row) => (
                <tr className="border-t border-slate-100" key={row.id}>
                  <td className="p-3">
                    <button
                      className="underline"
                      onClick={() => {
                        setSelectedId(row.id);
                        setAction(null);
                        setEditing(false);
                      }}
                      type="button"
                    >
                      {row.business_date}
                    </button>
                  </td>
                  <td className="p-3">
                    {row.total_amount} {row.currency}
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
        bounded={sales.bounded}
        hasMore={sales.hasMore}
        index={sales.index}
        onNext={() => void sales.goNext()}
        onPrevious={() => void sales.goPrevious()}
        pending={sales.pending}
      />
      {(canCreate || (editing && canUpdate && sale?.status === "DRAFT")) && (
        <form
          className="grid gap-3"
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
                sales.reload();
              })
              .catch((caught: unknown) =>
                fail(caught, "The sale could not be saved."),
              )
              .finally(() => setPending(false));
          }}
        >
          <h2 className="text-lg font-bold">
            {editing ? "Edit draft sale" : "New retail sale"}
          </h2>
          <Field label="Member">
            <SelectInput
              onChange={(event) => setMemberId(event.target.value)}
              value={memberId}
            >
              <option value="">Walk-in</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.member_number} — {member.first_name}{" "}
                  {member.last_name}
                </option>
              ))}
            </SelectInput>
          </Field>
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
                  {item}
                </option>
              ))}
            </SelectInput>
          </Field>
          <Field label="Reference">
            <TextInput
              onChange={(event) => setReference(event.target.value)}
              value={reference}
            />
          </Field>
          {lines.map((line) => {
            const product = products.find((item) => item.id === line.productId);
            const preview = product
              ? previewLineAmount(
                  line.quantity,
                  product.sale_price,
                  line.discount,
                )
              : null;
            return (
              <div
                className="grid gap-3 rounded-lg border border-slate-200 p-3 md:grid-cols-2"
                key={line.key}
              >
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
                    <option value="">Select a product</option>
                    {products.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.sku} — {item.name}
                      </option>
                    ))}
                  </SelectInput>
                </Field>
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
                <Field label={`Discount (${currency})`}>
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
                {preview && (
                  <p className="text-sm text-slate-600">
                    Preview only: {preview}. The server calculates the line
                    snapshots and sale totals.
                  </p>
                )}
                <button
                  className="underline tone-delete"
                  onClick={() =>
                    setLines((current) =>
                      current.filter((item) => item.key !== line.key),
                    )
                  }
                  type="button"
                >
                  Remove line
                </button>
              </div>
            );
          })}
          <button
            className="underline tone-create"
            disabled={lines.length >= 100}
            onClick={() => setLines((current) => [...current, blankLine()])}
            type="button"
          >
            Add product line
          </button>
          <button
            className="button button-edit"
            disabled={pending}
            type="submit"
          >
            {editing ? "Save sale changes" : "Save draft"}
          </button>
        </form>
      )}
      {sale && (
        <section aria-labelledby="sale-detail-title" className="grid gap-3">
          <h2 className="text-lg font-bold" id="sale-detail-title">
            Sale detail
          </h2>
          <ServerFacts
            rows={[
              { label: "Status", value: sale.status },
              { label: "Subtotal", value: sale.subtotal },
              { label: "Discount total", value: sale.discount_total },
              {
                label: "Total",
                value: `${sale.total_amount} ${sale.currency}`,
              },
              { label: "COGS", value: sale.cogs_total },
              { label: "Gross profit", value: sale.gross_profit_total },
              { label: "Returned amount", value: sale.returned_amount },
              { label: "Returns", value: String(sale.return_count) },
              { label: "Payment", value: sale.payment_id ?? "None" },
              { label: "Receipt", value: sale.receipt_id ?? "None" },
            ]}
          />
          <ul>
            {sale.lines.map((line) => (
              <li key={line.id}>
                {line.product_name}: sold {line.quantity}, returned{" "}
                {line.returned_quantity}, remaining {line.remaining_quantity},
                revenue {line.line_revenue}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap gap-3">
            {sale.status === "DRAFT" && canUpdate && (
              <button
                className="underline tone-edit"
                onClick={() => setEditing(true)}
                type="button"
              >
                Edit draft
              </button>
            )}
            {sale.status === "DRAFT" && canSubmit && (
              <button
                className="underline"
                onClick={() => setAction("submit")}
                type="button"
              >
                Submit
              </button>
            )}
            {sale.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline tone-success"
                onClick={() => setAction("approve")}
                type="button"
              >
                Approve
              </button>
            )}
            {sale.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline tone-warn"
                onClick={() => setAction("reject")}
                type="button"
              >
                Reject
              </button>
            )}
            {sale.status === "DRAFT" && canUpdate && (
              <button
                className="underline tone-cancel"
                onClick={() => setAction("cancel")}
                type="button"
              >
                Cancel draft
              </button>
            )}
            {sale.status === "POSTED" && canVoid && sale.return_count === 0 && (
              <button
                className="underline tone-delete"
                onClick={() => {
                  setCorrectionDate(defaultDate);
                  setAction("void");
                }}
                type="button"
              >
                Void sale
              </button>
            )}
            {sale.status === "POSTED" && canVoid && (
              <button
                className="underline tone-delete"
                onClick={() => {
                  setCorrectionDate(defaultDate);
                  setAction("return");
                }}
                type="button"
              >
                Partial return
              </button>
            )}
          </div>
          {action === "submit" && (
            <CommandForm
              confirmLabel="Confirm submit"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={() => {
                setPending(true);
                void submitRetailSale(sale.id)
                  .then((row) => {
                    setSale(row);
                    setAction(null);
                    sales.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The sale could not be submitted."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Submit this sale?"
            />
          )}
          {action === "approve" && (
            <CommandForm
              confirmLabel="Confirm approval"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                const body =
                  sale.payment_method === "CASH"
                    ? { cash_session_id: text(form, "cash_session_id") }
                    : {};
                if (sale.payment_method === "CASH" && !body.cash_session_id) {
                  setFields({
                    cash_session_id: "Select an open cash session.",
                  });
                  return;
                }
                setPending(true);
                void approveRetailSale(sale.id, body)
                  .then((result) => {
                    setApproval(result);
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
              title="Approve retail sale"
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
                <p>No cash session is sent for this method.</p>
              )}
            </CommandForm>
          )}
          {action === "reject" && (
            <CommandForm
              confirmLabel="Confirm rejection"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                setPending(true);
                void rejectRetailSale(sale.id, {
                  rejection_reason: text(form, "rejection_reason"),
                })
                  .then((row) => {
                    setSale(row);
                    setAction(null);
                    sales.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The sale could not be rejected."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Reject sale"
            >
              <ReasonField label="Rejection reason" name="rejection_reason" />
            </CommandForm>
          )}
          {action === "cancel" && (
            <CommandForm
              confirmLabel="Confirm cancellation"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                setPending(true);
                void cancelRetailSale(sale.id, { reason: text(form, "reason") })
                  .then((row) => {
                    setSale(row);
                    setAction(null);
                    sales.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The draft could not be cancelled."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Cancel sale draft"
            >
              <ReasonField label="Cancellation reason" />
            </CommandForm>
          )}
          {action === "void" && (
            <CommandForm
              confirmLabel="Confirm void"
              error={formError}
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
                      cash_session_id: "Select the destination open session.",
                    });
                    return;
                  }
                  body.cash_session_id = sessionId;
                }
                setPending(true);
                void voidRetailSale(sale.id, body)
                  .then((result) => {
                    setVoidResult(result);
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
              title="Void the full sale"
            >
              <ReasonField label="Void reason" />
              <Field label="Correction date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  onChange={(event) => setCorrectionDate(event.target.value)}
                  type="date"
                />
              </Field>
              {sale.payment_method === "CASH" && (
                <CashSessionSelect
                  collectionError={sessions.error}
                  currency={sale.currency}
                  error={fields.cash_session_id}
                  label="Destination open session"
                  pending={sessions.pending}
                  sessions={sessions.sessions}
                />
              )}
            </CommandForm>
          )}
          {action === "return" && (
            <CommandForm
              confirmLabel="Confirm return"
              error={formError}
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
                  const entered = quantity ? quantityScale(quantity) : null;
                  const remaining = quantityScale(line.remaining_quantity);
                  if (!quantity || entered === null || remaining === null) {
                    nextFields[line.id] =
                      "Enter a positive quantity with up to three decimal places.";
                    continue;
                  }
                  if (entered > remaining) {
                    nextFields[line.id] =
                      `Enter no more than the remaining ${line.remaining_quantity}.`;
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
              title="Return part of the sale"
            >
              <p>
                Enter a quantity for each line you are returning. Leave a line
                blank to keep it. The server calculates the refund.
              </p>
              {sale.lines.map((line) => {
                const remaining = quantityScale(line.remaining_quantity);
                const descriptionId = `return-line-${line.id}`;
                const inputId = `return-quantity-${line.id}`;
                const errorId = `return-error-${line.id}`;
                const error = fields[line.id];
                return (
                  <div key={line.id}>
                    <p id={descriptionId}>
                      Sold {line.quantity}, returned {line.returned_quantity},
                      remaining {line.remaining_quantity}
                    </p>
                    <label className="field" htmlFor={inputId}>
                      Return quantity for {line.product_name}
                    </label>
                    <TextInput
                      aria-describedby={
                        error ? `${descriptionId} ${errorId}` : descriptionId
                      }
                      aria-invalid={error ? true : undefined}
                      disabled={remaining === 0n}
                      id={inputId}
                      inputMode="decimal"
                      name={`quantity_${line.id}`}
                    />
                    {error && (
                      <span
                        className="text-sm text-red-700"
                        id={errorId}
                        role="alert"
                      >
                        {error}
                      </span>
                    )}
                  </div>
                );
              })}
              <ReasonField error={fields.reason} label="Return reason" />
              <Field error={fields.business_date} label="Business date">
                <TextInput
                  defaultValue={defaultDate}
                  name="business_date"
                  onChange={(event) => setCorrectionDate(event.target.value)}
                  type="date"
                />
              </Field>
              {sale.payment_method === "CASH" && (
                <CashSessionSelect
                  collectionError={sessions.error}
                  currency={sale.currency}
                  error={fields.cash_session_id}
                  label="Destination open session"
                  pending={sessions.pending}
                  required={false}
                  sessions={sessions.sessions}
                />
              )}
              {sale.payment_method === "CASH" && (
                <p>
                  Choose an open session only when this return refunds cash.
                  Leave it blank when the server refund is zero.
                </p>
              )}
              {pending && <Loading label="Returning the sale" />}
            </CommandForm>
          )}
          {approval && (
            <ServerFacts
              rows={[
                { label: "Posted payment", value: approval.payment.id },
                {
                  label: "Issued receipt",
                  value: approval.receipt.receipt_number,
                },
                {
                  label: "Cash movement",
                  value: approval.cash_transaction?.id ?? "None",
                },
              ]}
            />
          )}
          {voidResult && (
            <ServerFacts
              rows={[
                {
                  label: "Reversal payment",
                  value: voidResult.reversal_payment.id,
                },
                {
                  label: "Reversal receipt",
                  value: voidResult.reversal_receipt.receipt_number,
                },
                {
                  label: "Cash movement",
                  value: voidResult.cash_transaction?.id ?? "None",
                },
                {
                  label: "Stock returns",
                  value: String(voidResult.stock_movements.length),
                },
              ]}
            />
          )}
          {returnResult && (
            <ServerFacts
              rows={[
                {
                  label: "Refund amount",
                  value: returnResult.retail_return.refund_amount,
                },
                {
                  label: "Refund payment",
                  value: returnResult.refund_payment?.id ?? "None",
                },
                {
                  label: "Correction receipt",
                  value:
                    returnResult.correction_receipt?.receipt_number ?? "None",
                },
                {
                  label: "Cash movement",
                  value: returnResult.cash_transaction?.id ?? "None",
                },
                {
                  label: "Stock returns",
                  value: String(returnResult.stock_movements.length),
                },
              ]}
            />
          )}
        </section>
      )}
    </div>
  );
}
