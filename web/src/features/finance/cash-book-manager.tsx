"use client";
import { useTx } from "@/i18n/use-tx";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import {
  adjustCashSession,
  getCashSession,
  listCashSessions,
  listCashTransactions,
  openCashSession,
  reconcileCashSession,
} from "@/features/finance/client";
import { FormDialog } from "@/components/form-dialog";
import { fieldMap, financeError } from "@/features/finance/errors";
import { canonicalMoney, subtractMoney } from "@/features/finance/money";
import { useCursorPages } from "@/features/finance/paging";
import {
  Alert,
  CursorControls,
  Field,
  ReasonField,
  SelectInput,
  TextInput,
} from "@/features/finance/ui";
import type { CashSession, Direction } from "@/types/api";

const PAGE = "25";
const cardClass =
  "overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm";
const fieldTone =
  "[&_.field]:text-sm [&_.field]:font-medium [&_.field]:text-slate-600";

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
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
    OPEN: "bg-emerald-100 text-emerald-800",
    CLOSED: "bg-slate-200 text-slate-700",
    VOID: "bg-rose-100 text-rose-700",
  };
  return tones[status] ?? "bg-slate-100 text-slate-600";
}

function quietFigure(value: string): boolean {
  return value === "0.00" || value === "0" || value === "0.0";
}

function figureClass(
  value: string,
  tone: "neutral" | "in" | "out" | "difference",
): string {
  if (value === "Not closed" || value === "None") return "text-slate-500";
  if (tone === "difference") {
    if (value.startsWith("-")) return "text-rose-700";
    if (!quietFigure(value)) return "text-emerald-700";
  }
  if (!quietFigure(value) && tone === "in") return "text-emerald-700";
  if (!quietFigure(value) && tone === "out") return "text-rose-700";
  return "text-slate-900";
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

function DirectionPill({ direction }: { direction: string }) {
  const tx = useTx();
  const inbound = direction === "IN";
  return (
    <span
      className={`inline-flex rounded-full px-2 py-0.5 text-[0.68rem] font-bold tracking-wide ${
        inbound
          ? "bg-emerald-100 text-emerald-800"
          : "bg-rose-100 text-rose-700"
      }`}
    >
      {inbound ? tx("In") : direction === "OUT" ? tx("Out") : tx(pretty(direction))}
    </span>
  );
}

function MoneyFigure({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "in" | "out" | "difference";
}) {
  const tx = useTx();
  return (
    <div>
      <dt className="text-xs text-slate-500">{tx(label)}</dt>
      <dd
        className={`mt-0.5 text-sm font-semibold tabular-nums ${figureClass(value, tone)}`}
      >
        {value}
      </dd>
    </div>
  );
}

function ActionPanel({
  title,
  hint,
  pending,
  error,
  confirmLabel,
  confirmClass,
  onSubmit,
  onCancel,
  children,
}: {
  title: string;
  hint?: string;
  pending: boolean;
  error: string;
  confirmLabel: string;
  confirmClass: string;
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
      <div className={`grid gap-4 ${fieldTone}`}>{children}</div>
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
        <button className={confirmClass} disabled={pending} type="submit">
          {tx(confirmLabel)}
        </button>
      </div>
    </FormDialog>
  );
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
  const tx = useTx();
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
  const [opening, setOpening] = useState(false);
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
            financeError(caught, tx("The session could not be loaded.")),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, sessions.rows, tx]);

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

  function begin(next: "adjust" | "reconcile") {
    setMode(next);
    setFormError("");
    setFields({});
    if (next === "reconcile") setActualClosing("");
  }

  const filtered = applied !== "||";
  const showSessionPaging =
    sessions.index > 0 || sessions.hasMore || sessions.bounded;
  const showMovementPaging =
    movements.index > 0 || movements.hasMore || movements.bounded;
  const closed =
    session !== null &&
    (session.status !== "OPEN" ||
      Boolean(session.actual_closing) ||
      Boolean(session.difference) ||
      Boolean(session.difference_reason));

  return (
    <div className="grid gap-6">
      <div
        className={
          selectedId
            ? "grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(22rem,0.82fr)]"
            : "grid gap-6"
        }
      >
        <section
          aria-busy={sessions.pending}
          aria-labelledby="cash-sessions-title"
          className={cardClass}
        >
          <div className="flex flex-col gap-4 border-b border-slate-100 px-4 py-4 sm:px-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2
                className="text-lg font-bold text-slate-900"
                id="cash-sessions-title"
              >
                {" "}
                {tx("Sessions")}{" "}
              </h2>
              {canOpen ? (
                <button
                  className="button button-create"
                  onClick={() => {
                    setOpening(true);
                    setFormError("");
                    setFields({});
                  }}
                  type="button"
                >
                  {" "}
                  {tx("Open session")}{" "}
                </button>
              ) : null}
            </div>
            <p className="max-w-2xl text-sm leading-6 text-slate-500">
              {" "}
              {tx(
                "You see the sessions your access allows. Reception staff see the sessions they opened. Totals are the server's figures.",
              )}{" "}
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setApplied(
                  `${status}|${registerCode.trim().toUpperCase()}|${businessDate}`,
                );
              }}
            >
              <div
                className={`grid gap-3 sm:grid-cols-2 sm:items-end [&_.field]:text-xs [&_.field]:font-medium [&_.field]:text-slate-500 ${
                  selectedId ? "" : "xl:grid-cols-[1fr_1fr_1fr_auto]"
                }`}
              >
                <Field label="Status">
                  <SelectInput
                    name="status"
                    onChange={(event) => setStatus(event.target.value)}
                    value={status}
                  >
                    <option value="">{tx("Any status")}</option>
                    <option value="OPEN">{tx("Open")}</option>
                    <option value="CLOSED">{tx("Closed")}</option>
                    <option value="VOID">{tx("Void")}</option>
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
                <button
                  className="button w-full sm:w-auto sm:justify-self-start"
                  type="submit"
                >
                  {" "}
                  {tx("Apply filters")}{" "}
                </button>
              </div>
            </form>
          </div>

          {sessions.pending && sessions.rows.length === 0 ? (
            <div className="grid gap-3 p-4 sm:p-5" role="status">
              <p className="text-sm font-medium text-slate-600">
                {" "}
                {tx("Loading cash sessions.")}{" "}
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
          {sessions.pending && sessions.rows.length > 0 ? (
            <p
              className="px-5 pt-4 text-sm font-medium text-slate-600"
              role="status"
            >
              {" "}
              {tx("Loading cash sessions.")}{" "}
            </p>
          ) : null}
          {!sessions.pending && sessions.error ? (
            <div className="p-4 sm:p-5">
              <Alert message={sessions.error} />
            </div>
          ) : null}
          {!sessions.pending &&
          !sessions.error &&
          sessions.rows.length === 0 ? (
            <div className="px-4 py-16 text-center sm:px-5">
              <p className="font-semibold text-slate-800">
                {" "}
                {tx("No cash sessions were returned.")}{" "}
              </p>
              <p className="mt-1 text-sm text-slate-500">
                {filtered
                  ? tx("Try another status, register, or date.")
                  : tx("Open a session to start the register.")}
              </p>
            </div>
          ) : null}
          {sessions.rows.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[36rem] text-left text-sm">
                <caption className="sr-only">{tx("Cash sessions")}</caption>
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Register")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Business date")}{" "}
                    </th>
                    <th
                      className="px-5 py-2.5 text-right text-xs font-semibold"
                      scope="col"
                    >
                      {" "}
                      {tx("Expected closing")}{" "}
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
                  {sessions.rows.map((row) => {
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
                            className="text-left font-semibold tracking-wide"
                            onClick={() => {
                              setSelectedId(row.id);
                              setMode(null);
                              setFormError("");
                              setFields({});
                            }}
                            type="button"
                          >
                            {row.register_code}
                          </button>
                        </td>
                        <td className="px-5 py-3 whitespace-nowrap text-slate-600">
                          {row.business_date}
                        </td>
                        <td className="px-5 py-3 text-right font-semibold whitespace-nowrap text-slate-900 tabular-nums">
                          {row.expected_closing}{" "}
                          <span className="font-medium text-slate-400">
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
          {showSessionPaging ? (
            <div className="border-t border-slate-100 px-4 py-4 sm:px-5 [&_nav]:border-t-0 [&_nav]:pt-0">
              <CursorControls
                bounded={sessions.bounded}
                hasMore={sessions.hasMore}
                index={sessions.index}
                onNext={() => void sessions.goNext()}
                onPrevious={() => void sessions.goPrevious()}
                pending={sessions.pending}
              />
            </div>
          ) : null}
        </section>

        {selectedId ? (
          <section
            aria-labelledby="session-detail-title"
            className={`${cardClass} border-t-[3px] border-t-amber-500`}
          >
            {session ? null : (
              <h2 className="sr-only" id="session-detail-title">
                {" "}
                {tx("Session detail")}{" "}
              </h2>
            )}
            {!session && !detailError ? (
              <div className="grid gap-3 p-5" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading the session.")}{" "}
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
            {session ? (
              <>
                <div className="px-5 pt-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2
                        className="text-xs font-semibold tracking-[0.16em] text-slate-400 uppercase"
                        id="session-detail-title"
                      >
                        {" "}
                        {tx("Session detail")}{" "}
                      </h2>
                      <p className="mt-1 text-2xl font-black tracking-tight text-slate-900">
                        {session.register_code}
                      </p>
                      <p className="mt-0.5 text-sm text-slate-500">
                        {session.business_date}
                      </p>
                    </div>
                    <StatusPill status={session.status} />
                  </div>
                  <div className="mt-4 flex items-end justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 ring-1 ring-slate-200/80">
                    <div>
                      <p className="text-xs font-medium text-slate-500">
                        {" "}
                        {tx("Expected closing")}{" "}
                      </p>
                      <p className="mt-1 text-2xl font-black tracking-tight text-slate-900 tabular-nums">
                        {session.expected_closing}
                      </p>
                    </div>
                    <p className="pb-1 text-sm font-semibold text-slate-500">
                      {tx(session.currency)}
                    </p>
                  </div>
                </div>
                <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-5 py-4 sm:grid-cols-4 xl:grid-cols-2">
                  <MoneyFigure
                    label="Opening cash"
                    tone="neutral"
                    value={session.opening_cash}
                  />
                  <MoneyFigure
                    label="Cash sales"
                    tone="in"
                    value={session.cash_sales}
                  />
                  <MoneyFigure
                    label="Cash expenses"
                    tone="out"
                    value={session.cash_expenses}
                  />
                  <MoneyFigure label="Manual net" value={session.manual_net} />
                </dl>
                {closed ? (
                  <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-t border-slate-100 px-5 py-4">
                    <MoneyFigure
                      label="Actual closing"
                      value={session.actual_closing ?? "Not closed"}
                    />
                    <MoneyFigure
                      label="Difference"
                      tone="difference"
                      value={session.difference ?? "Not closed"}
                    />
                    <div className="col-span-2">
                      <dt className="text-xs text-slate-500">
                        {" "}
                        {tx("Difference reason")}{" "}
                      </dt>
                      <dd className="mt-0.5 text-sm font-medium break-words text-slate-800">
                        {session.difference_reason || "None"}
                      </dd>
                    </div>
                  </dl>
                ) : null}
                {session.status === "OPEN" && (canAdjust || canReconcile) ? (
                  <div className="grid gap-3 border-t border-slate-100 px-5 py-4">
                    <div className="flex flex-wrap gap-2">
                      {canAdjust && mode !== "adjust" ? (
                        <button
                          className="button button-edit"
                          onClick={() => begin("adjust")}
                          type="button"
                        >
                          {" "}
                          {tx("Manual adjustment")}{" "}
                        </button>
                      ) : null}
                      {canReconcile && mode !== "reconcile" ? (
                        <button
                          className="button button-success"
                          onClick={() => begin("reconcile")}
                          type="button"
                        >
                          {" "}
                          {tx("Reconcile and close")}{" "}
                        </button>
                      ) : null}
                    </div>
                    {mode === "adjust" && canAdjust ? (
                      <ActionPanel
                        confirmClass="button"
                        confirmLabel="Confirm adjustment"
                        error={formError}
                        hint="The amount is recorded on this open session. The server updates the totals."
                        onCancel={() => setMode(null)}
                        onSubmit={(form) => {
                          const amount = canonicalMoney(text(form, "amount"));
                          const direction = text(form, "direction");
                          if (
                            !amount ||
                            (direction !== "IN" && direction !== "OUT")
                          ) {
                            setFields({
                              amount: "Enter an exact amount such as 1.00.",
                            });
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
                                  tx("The adjustment could not be recorded."),
                                ),
                              );
                            })
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Manual adjustment")}
                      >
                        <Field error={fields.direction} label="Direction">
                          <SelectInput name="direction" required>
                            <option value="">{tx("Select direction")}</option>
                            <option value="IN">{tx("In")}</option>
                            <option value="OUT">{tx("Out")}</option>
                          </SelectInput>
                        </Field>
                        <Field error={fields.amount} label="Amount">
                          <TextInput
                            inputMode="decimal"
                            name="amount"
                            required
                          />
                        </Field>
                        <Field error={fields.currency} label="Currency">
                          <TextInput
                            defaultValue={session.currency}
                            name="currency"
                            required
                          />
                        </Field>
                        <Field
                          error={fields.business_date}
                          label="Business date"
                        >
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
                      </ActionPanel>
                    ) : null}
                    {mode === "reconcile" && canReconcile ? (
                      <ActionPanel
                        confirmClass="button button-success"
                        confirmLabel="Confirm close"
                        error={formError}
                        hint={tx("Server expected closing: {amount} {currency}.", {
                          amount: session.expected_closing,
                          currency: tx(session.currency),
                        })}
                        onCancel={() => {
                          setMode(null);
                          setActualClosing("");
                        }}
                        onSubmit={(form) => {
                          const actual = canonicalMoney(
                            text(form, "actual_closing"),
                            { allowZero: true },
                          );
                          if (!actual) {
                            setFields({
                              actual_closing:
                                "Enter an exact amount such as 0.00.",
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
                              difference_reason:
                                "Enter the reason for the difference.",
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
                                financeError(
                                  caught,
                                  tx("The session could not be closed."),
                                ),
                              );
                            })
                            .finally(() => setPending(false));
                        }}
                        pending={pending}
                        title={tx("Reconcile and close")}
                      >
                        <Field
                          error={fields.actual_closing}
                          label="Actual closing"
                        >
                          <TextInput
                            inputMode="decimal"
                            name="actual_closing"
                            onChange={(event) =>
                              setActualClosing(event.target.value)
                            }
                            required
                            value={actualClosing}
                          />
                        </Field>
                        {preview ? (
                          <p className="rounded-xl bg-white px-3 py-2.5 text-sm text-slate-600 ring-1 ring-slate-200">
                            {" "}
                            {tx("Preview difference:")}{" "}
                            <span
                              className={`font-semibold tabular-nums ${figureClass(preview, "difference")}`}
                            >
                              {preview}
                            </span>{" "}
                            {tx(
                              ". The server calculates the difference when the session closes.",
                            )}{" "}
                          </p>
                        ) : null}
                        <Field
                          error={fields.difference_reason}
                          label="Difference reason"
                        >
                          <textarea
                            className="input min-h-24"
                            name="difference_reason"
                          />
                        </Field>
                      </ActionPanel>
                    ) : null}
                  </div>
                ) : null}
              </>
            ) : null}
          </section>
        ) : null}

        {selectedId ? (
          <section
            aria-busy={movements.pending}
            aria-labelledby="movement-history-title"
            className={`${cardClass} xl:col-span-2`}
          >
            <div className="border-b border-slate-100 px-5 py-4">
              <h2
                className="text-base font-bold text-slate-900"
                id="movement-history-title"
              >
                {" "}
                {tx("Movement history")}{" "}
              </h2>
              {session ? (
                <p className="mt-1 text-sm text-slate-500">
                  {session.register_code} · {session.business_date}
                </p>
              ) : (
                <p className="mt-1 text-sm text-slate-500">
                  {" "}
                  {tx(
                    "Sales, expenses, and manual adjustments for this session.",
                  )}{" "}
                </p>
              )}
            </div>
            {movements.pending && movements.rows.length === 0 ? (
              <div className="grid gap-3 p-4 sm:p-5" role="status">
                <p className="text-sm font-medium text-slate-600">
                  {" "}
                  {tx("Loading movements.")}{" "}
                </p>
                <div className="h-14 animate-pulse rounded-xl bg-slate-100" />
              </div>
            ) : null}
            {movements.pending && movements.rows.length > 0 ? (
              <p
                className="px-5 pt-4 text-sm font-medium text-slate-600"
                role="status"
              >
                {" "}
                {tx("Loading movements.")}{" "}
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
              <div className="px-4 py-14 text-center sm:px-5">
                <p className="font-semibold text-slate-800">
                  {" "}
                  {tx("No movements were returned for this session.")}{" "}
                </p>
                <p className="mt-1 text-sm text-slate-500">
                  {" "}
                  {tx(
                    "Sales, expenses, and manual adjustments will show up here.",
                  )}{" "}
                </p>
              </div>
            ) : null}
            {movements.rows.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[40rem] text-left text-sm">
                  <caption className="sr-only">{tx("Cash movements")}</caption>
                  <thead className="border-b border-slate-200 text-slate-500">
                    <tr>
                      <th
                        className="px-5 py-2.5 text-xs font-semibold"
                        scope="col"
                      >
                        {" "}
                        {tx("Movement")}{" "}
                      </th>
                      <th
                        className="px-5 py-2.5 text-right text-xs font-semibold"
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
                        {tx("Business date")}{" "}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {movements.rows.map((row) => {
                      const note = row.description || row.reason;
                      const outbound = row.direction === "OUT";
                      return (
                        <tr className="hover:bg-slate-50" key={row.id}>
                          <td className="px-5 py-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <DirectionPill direction={row.direction} />
                              <span className="font-medium text-slate-800">
                                {tx(pretty(row.source_type))}
                              </span>
                            </div>
                            {note ? (
                              <p className="mt-1 max-w-xl text-slate-500">
                                {note}
                              </p>
                            ) : null}
                          </td>
                          <td className="px-5 py-3 text-right whitespace-nowrap">
                            <span
                              className={`font-semibold tabular-nums ${
                                outbound ? "text-rose-700" : "text-emerald-700"
                              }`}
                            >
                              {outbound ? "−" : "+"}
                              {row.amount}
                            </span>
                            <span className="ml-1.5 text-xs font-medium text-slate-400">
                              {tx(row.currency)}
                            </span>
                          </td>
                          <td className="px-5 py-3 whitespace-nowrap text-slate-600">
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

      {canOpen && opening ? (
        <FormDialog
          description={tx(
            "The session opens on the main register. Currency is the gym currency, {currency}, and the server records it.",
            { currency: tx(currency) },
          )}
          onClose={() => {
            if (pending) return;
            setOpening(false);
            setFormError("");
            setFields({});
          }}
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
              business_date: text(form, "business_date"),
              opening_cash: opening,
            })
              .then(async (result) => {
                setFormError("");
                setFields({});
                formElement.reset();
                setOpening(false);
                setSelectedId(result.session.id);
                await refreshSelected(result.session.id);
              })
              .catch((caught: unknown) => {
                setFields(fieldMap(caught));
                setFormError(
                  financeError(caught, tx("The session could not be opened.")),
                );
              })
              .finally(() => setPending(false));
          }}
          pending={pending}
          title={tx("Open session")}
        >
          <div className={`grid gap-4 sm:grid-cols-2 ${fieldTone}`}>
            {formError ? (
              <div className="sm:col-span-2">
                <Alert message={formError} />
              </div>
            ) : null}
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
              label={tx("Opening cash ({currency})", { currency: tx(currency) })}
            >
              <TextInput inputMode="decimal" name="opening_cash" required />
            </Field>
          </div>
          <div className="flex justify-end border-t border-slate-100 px-5 py-3">
            <button
              className="button button-create"
              disabled={pending}
              type="submit"
            >
              {" "}
              {tx("Open session")}{" "}
            </button>
          </div>
        </FormDialog>
      ) : null}
    </div>
  );
}
