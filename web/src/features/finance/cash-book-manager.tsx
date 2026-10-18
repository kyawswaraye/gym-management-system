"use client";

import { useEffect, useState } from "react";

import {
  adjustCashSession,
  getCashSession,
  listCashSessions,
  listCashTransactions,
  openCashSession,
  reconcileCashSession,
} from "@/features/finance/client";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney, subtractMoney } from "@/features/finance/money";
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
import type { CashSession, Direction } from "@/types/api";

const PAGE = "25";

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function CashBookManager({
  canOpen,
  canAdjust,
  canReconcile,
  currency,
  defaultDate,
}: {
  canOpen: boolean;
  canAdjust: boolean;
  canReconcile: boolean;
  currency: string;
  defaultDate: string;
}) {
  const [status, setStatus] = useState("");
  const [registerCode, setRegisterCode] = useState("");
  const [businessDate, setBusinessDate] = useState("");
  const [applied, setApplied] = useState("||");
  const [selectedId, setSelectedId] = useState("");
  const [session, setSession] = useState<CashSession | null>(null);
  const [detailError, setDetailError] = useState("");
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [mode, setMode] = useState<"adjust" | "reconcile" | null>(null);
  const [actualClosing, setActualClosing] = useState("");

  const sessions = useCursorPages(applied, (after) => {
    const [filterStatus, filterRegister, filterDate] = applied.split("|");
    return listCashSessions({
      ...(filterStatus ? { status: filterStatus } : {}),
      ...(filterRegister ? { register_code: filterRegister } : {}),
      ...(filterDate ? { business_date: filterDate } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    });
  });
  const movements = useCursorPages(selectedId || "none", (after) => {
    if (!selectedId) {
      return Promise.resolve({
        data: [],
        meta: { has_more: false, next_after: null },
      });
    }
    return listCashTransactions({
      cash_session_id: selectedId,
      limit: PAGE,
      ...(after ? { after } : {}),
    });
  });

  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    const timer = window.setTimeout(() => {
      void getCashSession(selectedId)
        .then((row) => {
          if (!active) return;
          setSession(row);
          setDetailError("");
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setSession(null);
          setDetailError(
            financeError(caught, "The session could not be loaded."),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, sessions.rows]);

  const preview =
    session && actualClosing
      ? subtractMoney(
          canonicalMoney(actualClosing, { allowZero: true }) ?? "",
          session.expected_closing,
        )
      : null;

  async function refreshSelected(id: string) {
    const row = await getCashSession(id);
    setSession(row);
    sessions.reload();
    movements.reload();
  }

  return (
    <div className="grid gap-6">
      <p className="max-w-3xl text-sm text-slate-600">
        Sessions shown are only those the server returns for your access.
        Reception staff see sessions they opened. Totals below are the
        server&apos;s figures.
      </p>
      <form
        className="grid gap-3 md:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          setApplied(
            `${status}|${registerCode.trim().toUpperCase()}|${businessDate}`,
          );
        }}
      >
        <Field label="Status">
          <SelectInput
            name="status"
            onChange={(event) => setStatus(event.target.value)}
            value={status}
          >
            <option value="">Any status</option>
            <option value="OPEN">Open</option>
            <option value="CLOSED">Closed</option>
            <option value="VOID">Void</option>
          </SelectInput>
        </Field>
        <Field label="Register code">
          <TextInput
            name="register_code"
            onChange={(event) => setRegisterCode(event.target.value)}
            value={registerCode}
          />
        </Field>
        <Field label="Business date">
          <TextInput
            name="business_date"
            onChange={(event) => setBusinessDate(event.target.value)}
            type="date"
            value={businessDate}
          />
        </Field>
        <button className="button self-end" type="submit">
          Apply filters
        </button>
      </form>
      <Alert message={sessions.error} />
      {sessions.pending && <Loading label="Loading cash sessions." />}
      {!sessions.pending && sessions.rows.length === 0 && (
        <Empty label="No cash sessions were returned." />
      )}
      {sessions.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Cash sessions</caption>
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3">Register</th>
                <th className="p-3">Business date</th>
                <th className="p-3">Expected closing</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {sessions.rows.map((row) => (
                <tr className="border-t border-slate-100" key={row.id}>
                  <td className="p-3">
                    <button
                      className="underline"
                      onClick={() => {
                        setSelectedId(row.id);
                        setMode(null);
                        setFormError("");
                      }}
                      type="button"
                    >
                      {row.register_code}
                    </button>
                  </td>
                  <td className="p-3">{row.business_date}</td>
                  <td className="p-3">
                    {row.expected_closing} {row.currency}
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
        bounded={sessions.bounded}
        hasMore={sessions.hasMore}
        index={sessions.index}
        onNext={() => void sessions.goNext()}
        onPrevious={() => void sessions.goPrevious()}
        pending={sessions.pending}
      />
      {canOpen && (
        <form
          className="grid gap-3 rounded-lg border border-slate-200 p-4 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            const formElement = event.currentTarget;
            const form = new FormData(formElement);
            const opening = canonicalMoney(text(form, "opening_cash"), {
              allowZero: true,
            });
            if (!opening) {
              setFields({
                opening_cash: "Enter an exact amount such as 0.00.",
              });
              return;
            }
            setPending(true);
            void openCashSession({
              register_code: text(form, "register_code").toUpperCase(),
              business_date: text(form, "business_date"),
              opening_cash: opening,
            })
              .then(async (result) => {
                setFormError("");
                setFields({});
                formElement.reset();
                setSelectedId(result.session.id);
                await refreshSelected(result.session.id);
              })
              .catch((caught: unknown) => {
                setFields(fieldMap(caught));
                setFormError(
                  financeError(caught, "The session could not be opened."),
                );
              })
              .finally(() => setPending(false));
          }}
        >
          <h2 className="text-lg font-bold md:col-span-2">Open session</h2>
          <Alert message={formError} />
          <Field error={fields.register_code} label="Register code">
            <TextInput name="register_code" required />
          </Field>
          <Field error={fields.business_date} label="Business date">
            <TextInput
              defaultValue={defaultDate}
              name="business_date"
              required
              type="date"
            />
          </Field>
          <Field
            error={fields.opening_cash}
            label={`Opening cash (${currency})`}
          >
            <TextInput inputMode="decimal" name="opening_cash" required />
          </Field>
          <p className="text-sm text-slate-500">
            Currency is the gym currency, {currency}. The server records it.
          </p>
          <button className="button" disabled={pending} type="submit">
            Open session
          </button>
        </form>
      )}
      {selectedId && (
        <section aria-labelledby="session-detail-title" className="grid gap-4">
          <h2 className="text-lg font-bold" id="session-detail-title">
            Session detail
          </h2>
          <Alert message={detailError} />
          {session && (
            <ServerFacts
              rows={[
                { label: "Register", value: session.register_code },
                { label: "Business date", value: session.business_date },
                { label: "Status", value: session.status },
                { label: "Currency", value: session.currency },
                { label: "Opening cash", value: session.opening_cash },
                { label: "Cash sales", value: session.cash_sales },
                { label: "Cash expenses", value: session.cash_expenses },
                { label: "Manual net", value: session.manual_net },
                { label: "Expected closing", value: session.expected_closing },
                {
                  label: "Actual closing",
                  value: session.actual_closing ?? "Not closed",
                },
                {
                  label: "Difference",
                  value: session.difference ?? "Not closed",
                },
                {
                  label: "Difference reason",
                  value: session.difference_reason || "None",
                },
              ]}
            />
          )}
          <h3 className="font-semibold">Movement history</h3>
          <Alert message={movements.error} />
          {movements.pending && <Loading label="Loading movements." />}
          {!movements.pending && movements.rows.length === 0 && (
            <Empty label="No movements were returned for this session." />
          )}
          {movements.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Cash movements</caption>
                <thead className="bg-slate-50">
                  <tr>
                    <th className="p-3">Direction</th>
                    <th className="p-3">Source</th>
                    <th className="p-3">Amount</th>
                    <th className="p-3">Business date</th>
                    <th className="p-3">Description</th>
                  </tr>
                </thead>
                <tbody>
                  {movements.rows.map((row) => (
                    <tr className="border-t border-slate-100" key={row.id}>
                      <td className="p-3">{row.direction}</td>
                      <td className="p-3">{row.source_type}</td>
                      <td className="p-3">
                        {row.amount} {row.currency}
                      </td>
                      <td className="p-3">{row.business_date}</td>
                      <td className="p-3">{row.description || row.reason}</td>
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
          {session?.status === "OPEN" && canAdjust && mode !== "adjust" && (
            <button
              className="underline"
              onClick={() => setMode("adjust")}
              type="button"
            >
              Manual adjustment
            </button>
          )}
          {session?.status === "OPEN" &&
            canReconcile &&
            mode !== "reconcile" && (
              <button
                className="underline"
                onClick={() => setMode("reconcile")}
                type="button"
              >
                Reconcile and close
              </button>
            )}
          {mode === "adjust" && session && canAdjust && (
            <CommandForm
              confirmLabel="Confirm adjustment"
              error={formError}
              onCancel={() => setMode(null)}
              onSubmit={(form) => {
                const amount = canonicalMoney(text(form, "amount"));
                const direction = text(form, "direction");
                if (!amount || (direction !== "IN" && direction !== "OUT")) {
                  setFields({ amount: "Enter an exact amount such as 1.00." });
                  return;
                }
                const body = {
                  direction: direction as Direction,
                  amount,
                  currency: text(form, "currency").toUpperCase(),
                  business_date: text(form, "business_date"),
                  description: text(form, "description"),
                  reason: text(form, "reason"),
                };
                setPending(true);
                void adjustCashSession(session.id, body)
                  .then(async () => {
                    setMode(null);
                    setFormError("");
                    setFields({});
                    await refreshSelected(session.id);
                  })
                  .catch((caught: unknown) => {
                    setFields(fieldMap(caught));
                    setFormError(
                      financeError(
                        caught,
                        "The adjustment could not be recorded.",
                      ),
                    );
                  })
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Manual adjustment"
            >
              <Field error={fields.direction} label="Direction">
                <SelectInput name="direction" required>
                  <option value="">Select direction</option>
                  <option value="IN">In</option>
                  <option value="OUT">Out</option>
                </SelectInput>
              </Field>
              <Field error={fields.amount} label="Amount">
                <TextInput inputMode="decimal" name="amount" required />
              </Field>
              <Field error={fields.currency} label="Currency">
                <TextInput
                  defaultValue={session.currency}
                  name="currency"
                  required
                />
              </Field>
              <Field error={fields.business_date} label="Business date">
                <TextInput
                  defaultValue={session.business_date}
                  name="business_date"
                  required
                  type="date"
                />
              </Field>
              <Field error={fields.description} label="Description">
                <TextInput name="description" />
              </Field>
              <ReasonField error={fields.reason} label="Reason" />
            </CommandForm>
          )}
          {mode === "reconcile" && session && canReconcile && (
            <CommandForm
              confirmLabel="Confirm close"
              error={formError}
              onCancel={() => setMode(null)}
              onSubmit={(form) => {
                const actual = canonicalMoney(text(form, "actual_closing"), {
                  allowZero: true,
                });
                if (!actual) {
                  setFields({
                    actual_closing: "Enter an exact amount such as 0.00.",
                  });
                  return;
                }
                const difference = subtractMoney(
                  actual,
                  session.expected_closing,
                );
                const reason = text(form, "difference_reason");
                if (difference && difference !== "0.00" && !reason) {
                  setFields({
                    difference_reason: "Enter the reason for the difference.",
                  });
                  return;
                }
                setPending(true);
                void reconcileCashSession(session.id, {
                  actual_closing: actual,
                  difference_reason: reason,
                })
                  .then(async (result) => {
                    setMode(null);
                    setFormError("");
                    setFields({});
                    setActualClosing("");
                    setSession(result.session);
                    sessions.reload();
                  })
                  .catch((caught: unknown) => {
                    setFields(fieldMap(caught));
                    setFormError(
                      financeError(caught, "The session could not be closed."),
                    );
                  })
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Reconcile and close"
            >
              <p className="text-sm text-slate-600">
                Server expected closing: {session.expected_closing}{" "}
                {session.currency}.
              </p>
              <Field error={fields.actual_closing} label="Actual closing">
                <TextInput
                  inputMode="decimal"
                  name="actual_closing"
                  onChange={(event) => setActualClosing(event.target.value)}
                  required
                />
              </Field>
              {preview && (
                <p className="text-sm text-slate-600">
                  Preview difference: {preview}. The server calculates the
                  difference when the session closes.
                </p>
              )}
              <Field error={fields.difference_reason} label="Difference reason">
                <textarea className="input min-h-24" name="difference_reason" />
              </Field>
            </CommandForm>
          )}
        </section>
      )}
    </div>
  );
}
