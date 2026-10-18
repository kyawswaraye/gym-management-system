"use client";

import { useEffect, useState } from "react";

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
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import { uploadExpenseReceipt } from "@/features/finance/receipt-file";
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
const PAGE = "25";

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
  const [status, setStatus] = useState("");
  const [applied, setApplied] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [expense, setExpense] = useState<Expense | null>(null);
  const [editingCategory, setEditingCategory] =
    useState<ExpenseCategory | null>(null);
  const [action, setAction] = useState<
    "edit" | "submit" | "approve" | "reject" | "cancel" | "void" | null
  >(null);
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [formError, setFormError] = useState("");
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
  const sessions = useOpenCashSessions(
    action === "approve" && expense
      ? expense.business_date
      : action === "void"
        ? correctionDate
        : "",
  );
  const categoryById = new Map(categories.rows.map((row) => [row.id, row]));

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
            financeError(caught, "The expense could not be loaded."),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, expenses.rows]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  async function expenseBody(
    form: FormData,
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
      business_date: text(form, "business_date"),
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

  const selectedCategory = expense
    ? categoryById.get(expense.category_id)
    : undefined;

  return (
    <div className="grid gap-8">
      <section aria-labelledby="categories-title" className="grid gap-4">
        <h2 className="text-lg font-bold" id="categories-title">
          Categories
        </h2>
        <p className="text-sm text-slate-600">
          Protected categories keep their system code and cannot be renamed or
          archived here.
        </p>
        <Alert message={categories.error} />
        {categories.rows.length === 0 && !categories.pending && (
          <Empty label="No categories were returned." />
        )}
        <ul className="grid gap-2">
          {categories.rows.map((category) => (
            <li className="flex flex-wrap items-center gap-3" key={category.id}>
              <span>{category.name}</span>
              <span className="text-sm text-slate-500">{category.code}</span>
              {protectedCategory(category) ? (
                <StatusText status="Protected" />
              ) : (
                <StatusText
                  status={category.is_active ? "Active" : "Inactive"}
                />
              )}
              {canManageCategories && !protectedCategory(category) && (
                <button
                  className="underline"
                  onClick={() => setEditingCategory(category)}
                  type="button"
                >
                  Edit {category.name}
                </button>
              )}
            </li>
          ))}
        </ul>
        {canManageCategories && (
          <form
            className="grid gap-3 md:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              const formElement = event.currentTarget;
              const form = new FormData(formElement);
              const order = text(form, "display_order") || "0";
              if (!/^\d+$/.test(order)) {
                setFields({ display_order: "Enter a whole number." });
                return;
              }
              setPending(true);
              void createExpenseCategory({
                code: text(form, "code").toUpperCase(),
                name: text(form, "name"),
                display_order: Number(order),
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
            <h3 className="font-semibold md:col-span-3">New custom category</h3>
            <Field label="Code">
              <TextInput name="code" required />
            </Field>
            <Field label="Name">
              <TextInput name="name" required />
            </Field>
            <Field error={fields.display_order} label="Display order">
              <TextInput defaultValue="0" name="display_order" type="number" />
            </Field>
            <button className="button" disabled={pending} type="submit">
              Create category
            </button>
          </form>
        )}
        {editingCategory && canManageCategories && (
          <CommandForm
            confirmLabel="Save category"
            error={formError}
            onCancel={() => setEditingCategory(null)}
            onSubmit={(form) => {
              setPending(true);
              void updateExpenseCategory(editingCategory.id, {
                name: text(form, "name"),
                display_order: Number(text(form, "display_order") || "0"),
                is_active: form.get("is_active") === "on",
              })
                .then(() => {
                  setEditingCategory(null);
                  categories.reload();
                })
                .catch((caught: unknown) =>
                  fail(caught, "The category could not be updated."),
                )
                .finally(() => setPending(false));
            }}
            pending={pending}
            title={`Update ${editingCategory.name}`}
          >
            <Field label="Name">
              <TextInput
                defaultValue={editingCategory.name}
                name="name"
                required
              />
            </Field>
            <Field label="Display order">
              <TextInput
                defaultValue={String(editingCategory.display_order)}
                name="display_order"
                type="number"
              />
            </Field>
            <label className="field">
              <input
                defaultChecked={editingCategory.is_active}
                name="is_active"
                type="checkbox"
              />{" "}
              Active
            </label>
          </CommandForm>
        )}
      </section>
      <section aria-labelledby="expense-list-title" className="grid gap-4">
        <h2 className="text-lg font-bold" id="expense-list-title">
          Expense list
        </h2>
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
                "VOID",
                "CANCELLED",
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
        <Alert message={expenses.error || formError} />
        {expenses.pending && <Loading label="Loading expenses." />}
        {!expenses.pending && expenses.rows.length === 0 && (
          <Empty label="No expenses were returned." />
        )}
        {expenses.rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Expenses</caption>
              <thead className="bg-slate-50">
                <tr>
                  <th className="p-3">Description</th>
                  <th className="p-3">Amount</th>
                  <th className="p-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {expenses.rows.map((row) => (
                  <tr className="border-t border-slate-100" key={row.id}>
                    <td className="p-3">
                      <button
                        className="underline"
                        onClick={() => {
                          setSelectedId(row.id);
                          setAction(null);
                        }}
                        type="button"
                      >
                        {row.description}
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
          bounded={expenses.bounded}
          hasMore={expenses.hasMore}
          index={expenses.index}
          onNext={() => void expenses.goNext()}
          onPrevious={() => void expenses.goPrevious()}
          pending={expenses.pending}
        />
        {(canCreate || (action === "edit" && canUpdate)) && (
          <form
            className="grid gap-3 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              setPending(true);
              void expenseBody(form)
                .then((body) => {
                  if (!body) return null;
                  return action === "edit" && expense
                    ? updateExpense(expense.id, body)
                    : createExpense(body);
                })
                .then((saved) => {
                  if (!saved) return;
                  setAction(null);
                  setSelectedId(saved.id);
                  setExpense(saved);
                  expenses.reload();
                })
                .catch((caught: unknown) =>
                  fail(caught, "The expense could not be saved."),
                )
                .finally(() => setPending(false));
            }}
          >
            <h3 className="font-semibold md:col-span-2">
              {action === "edit" ? "Edit draft" : "New expense"}
            </h3>
            <Field error={fields.category_id} label="Category">
              <SelectInput
                name="category_id"
                required
                defaultValue={expense?.category_id}
              >
                <option value="">Select a category</option>
                {categories.rows.map((category) => (
                  <option
                    disabled={salaryCategory(category)}
                    key={category.id}
                    value={category.id}
                  >
                    {category.name}
                    {protectedCategory(category) ? " (protected)" : ""}
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
            <Field error={fields.description} label="Description">
              <TextInput
                defaultValue={expense?.description}
                name="description"
                required
              />
            </Field>
            <Field error={fields.amount} label={`Amount (${currency})`}>
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
                    {item}
                  </option>
                ))}
              </SelectInput>
            </Field>
            <Field label="Policy note">
              <TextInput
                defaultValue={expense?.policy_note}
                name="policy_note"
              />
            </Field>
            {canUploadReceipt && (
              <Field label="Receipt evidence">
                <input
                  accept="image/jpeg,image/png,image/webp"
                  className="input"
                  name="receipt"
                  type="file"
                />
              </Field>
            )}
            <button className="button" disabled={pending} type="submit">
              {action === "edit" ? "Save changes" : "Save draft"}
            </button>
          </form>
        )}
        {expense && (
          <div className="grid gap-3">
            <ServerFacts
              rows={[
                {
                  label: "Amount",
                  value: `${expense.amount} ${expense.currency}`,
                },
                { label: "Status", value: expense.status },
                { label: "Method", value: expense.payment_method },
                { label: "Business date", value: expense.business_date },
                {
                  label: "Cash session",
                  value: expense.cash_session_id ?? "None",
                },
              ]}
            />
            {salaryCategory(selectedCategory) && (
              <p role="note">
                This expense uses a protected salary or commission category.
                Void the salary period instead of voiding it here.
              </p>
            )}
            <div className="flex flex-wrap gap-3">
              {expense.status === "DRAFT" && canUpdate && (
                <button
                  className="underline"
                  onClick={() => setAction("edit")}
                  type="button"
                >
                  Edit draft
                </button>
              )}
              {expense.status === "DRAFT" && canSubmit && (
                <button
                  className="underline"
                  onClick={() => setAction("submit")}
                  type="button"
                >
                  Submit
                </button>
              )}
              {expense.status === "PENDING_APPROVAL" && canApprove && (
                <button
                  className="underline"
                  onClick={() => setAction("approve")}
                  type="button"
                >
                  Approve
                </button>
              )}
              {expense.status === "PENDING_APPROVAL" && canApprove && (
                <button
                  className="underline"
                  onClick={() => setAction("reject")}
                  type="button"
                >
                  Reject
                </button>
              )}
              {expense.status === "DRAFT" && canUpdate && (
                <button
                  className="underline"
                  onClick={() => setAction("cancel")}
                  type="button"
                >
                  Cancel draft
                </button>
              )}
              {expense.status === "POSTED" &&
                canVoid &&
                !salaryCategory(selectedCategory) && (
                  <button
                    className="underline"
                    onClick={() => setAction("void")}
                    type="button"
                  >
                    Void
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
                  void submitExpense(expense.id)
                    .then((row) => {
                      setExpense(row);
                      setAction(null);
                      expenses.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The expense could not be submitted."),
                    )
                    .finally(() => setPending(false));
                }}
                pending={pending}
                title="Submit this expense?"
              />
            )}
            {action === "approve" && (
              <CommandForm
                confirmLabel="Confirm approval"
                error={formError}
                onCancel={() => setAction(null)}
                onSubmit={(form) => {
                  const body =
                    expense.payment_method === "CASH"
                      ? { cash_session_id: text(form, "cash_session_id") }
                      : {};
                  if (
                    expense.payment_method === "CASH" &&
                    !body.cash_session_id
                  ) {
                    setFields({
                      cash_session_id: "Select an open cash session.",
                    });
                    return;
                  }
                  setPending(true);
                  void approveExpense(expense.id, body)
                    .then((result) => {
                      setExpense(result.expense);
                      setAction(null);
                      expenses.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The expense could not be approved."),
                    )
                    .finally(() => setPending(false));
                }}
                pending={pending}
                title="Approve expense"
              >
                {expense.payment_method === "CASH" ? (
                  <CashSessionSelect
                    currency={expense.currency}
                    error={fields.cash_session_id}
                    label="Open cash session"
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
                  void rejectExpense(expense.id, {
                    rejection_reason: text(form, "rejection_reason"),
                  })
                    .then((row) => {
                      setExpense(row);
                      setAction(null);
                      expenses.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The expense could not be rejected."),
                    )
                    .finally(() => setPending(false));
                }}
                pending={pending}
                title="Reject expense"
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
                  void cancelExpense(expense.id, {
                    reason: text(form, "reason"),
                  })
                    .then((row) => {
                      setExpense(row);
                      setAction(null);
                      expenses.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The draft could not be cancelled."),
                    )
                    .finally(() => setPending(false));
                }}
                pending={pending}
                title="Cancel draft"
              >
                <ReasonField label="Cancellation reason" />
              </CommandForm>
            )}
            {action === "void" && !salaryCategory(selectedCategory) && (
              <CommandForm
                confirmLabel="Confirm void"
                error={formError}
                onCancel={() => setAction(null)}
                onSubmit={(form) => {
                  const body: VoidExpenseRequest = {
                    reason: text(form, "reason"),
                  };
                  const date = text(form, "business_date");
                  if (date) body.business_date = date;
                  if (expense.payment_method === "CASH") {
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
                  void voidExpense(expense.id, body)
                    .then((result) => {
                      setExpense(result.original_expense);
                      setAction(null);
                      expenses.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The expense could not be voided."),
                    )
                    .finally(() => setPending(false));
                }}
                pending={pending}
                title="Void expense"
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
                {expense.payment_method === "CASH" && (
                  <CashSessionSelect
                    currency={expense.currency}
                    error={fields.cash_session_id}
                    label="Destination open session"
                    sessions={sessions.sessions}
                  />
                )}
              </CommandForm>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
