"use client";

import { useEffect, useState } from "react";

import {
  CashSessionSelect,
  useOpenCashSessions,
} from "@/features/finance/cash-session-field";
import {
  approveSalaryPeriod,
  cancelSalaryPeriod,
  createSalaryPeriod,
  getSalaryPeriod,
  listSalaryPeriods,
  rejectSalaryPeriod,
  replaceSalaryLines,
  submitSalaryPeriod,
  updateSalaryPeriod,
  voidSalaryPeriod,
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
import { listStaff } from "@/features/operations/client";
import type {
  PaymentMethod,
  SalaryLineWriteRequest,
  SalaryPeriod,
  StaffRecord,
  VoidSalaryRequest,
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
  staffId: string;
  amount: string;
  method: PaymentMethod;
  reference: string;
  description: string;
};

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function blankLine(): LineDraft {
  return {
    key: crypto.randomUUID(),
    staffId: "",
    amount: "",
    method: "BANK_TRANSFER",
    reference: "",
    description: "",
  };
}

export function SalariesManager({
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
  const [period, setPeriod] = useState<SalaryPeriod | null>(null);
  const [staff, setStaff] = useState<StaffRecord[]>([]);
  const [lines, setLines] = useState<LineDraft[]>([blankLine()]);
  const [action, setAction] = useState<
    "edit" | "submit" | "approve" | "reject" | "cancel" | "void" | null
  >(null);
  const [correctionDate, setCorrectionDate] = useState(defaultDate);
  const [formError, setFormError] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const periods = useCursorPages(applied, (after) =>
    listSalaryPeriods({
      ...(applied ? { status: applied } : {}),
      limit: PAGE,
      ...(after ? { after } : {}),
    }),
  );
  const approvalDate =
    action === "approve" && period
      ? period.business_date
      : action === "void"
        ? correctionDate
        : "";
  const sessions = useOpenCashSessions(approvalDate);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      void listStaff({ limit: 100 })
        .then((page) => {
          if (active) setStaff(page.data);
        })
        .catch(() => {
          if (active) setStaff([]);
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
      void getSalaryPeriod(selectedId)
        .then((row) => {
          if (!active) return;
          setPeriod(row);
          setLines(
            row.lines.length
              ? row.lines.map((line) => ({
                  key: line.id,
                  staffId: line.staff_profile_id,
                  amount: line.amount,
                  method: line.payment_method as PaymentMethod,
                  reference: line.external_reference,
                  description: line.description,
                }))
              : [blankLine()],
          );
        })
        .catch((caught: unknown) => {
          if (!active) return;
          setPeriod(null);
          setFormError(
            financeError(caught, "The salary period could not be loaded."),
          );
        });
    }, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, periods.rows]);

  function fail(caught: unknown, fallback: string) {
    setFields(fieldMap(caught));
    setFormError(financeError(caught, fallback));
  }

  function linePayload(): SalaryLineWriteRequest[] | null {
    if (lines.length < 1 || lines.length > 200) {
      setFormError("Enter from 1 to 200 staff lines.");
      return null;
    }
    const staffIds = lines.map((line) => line.staffId);
    if (
      new Set(staffIds).size !== staffIds.length ||
      staffIds.some((id) => !id)
    ) {
      setFormError("Choose each staff member once.");
      return null;
    }
    const payload: SalaryLineWriteRequest[] = [];
    for (const line of lines) {
      const amount = canonicalMoney(line.amount);
      if (!amount) {
        setFields({ amount: "Enter an exact amount such as 100.00." });
        return null;
      }
      payload.push({
        staff_profile_id: line.staffId,
        amount,
        currency,
        payment_method: line.method,
        description: line.description,
        ...(line.reference ? { external_reference: line.reference } : {}),
      });
    }
    return payload;
  }

  const cashLines =
    period?.lines.filter((line) => line.payment_method === "CASH") ?? [];

  return (
    <div className="grid gap-6">
      <p className="max-w-3xl text-sm text-slate-600">
        Periods shown are only those the server returns for your access.
        Reception staff see their own drafts and submissions. Amounts are the
        server&apos;s line amounts.
      </p>
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
      <Alert message={periods.error || formError} />
      {periods.pending && <Loading label="Loading salary periods." />}
      {!periods.pending && periods.rows.length === 0 && (
        <Empty label="No salary periods were returned." />
      )}
      {periods.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Salary periods</caption>
            <thead className="bg-slate-50">
              <tr>
                <th className="p-3">Period</th>
                <th className="p-3">Business date</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {periods.rows.map((row) => (
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
                      {row.period_start} – {row.period_end}
                    </button>
                  </td>
                  <td className="p-3">{row.business_date}</td>
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
        bounded={periods.bounded}
        hasMore={periods.hasMore}
        index={periods.index}
        onNext={() => void periods.goNext()}
        onPrevious={() => void periods.goPrevious()}
        pending={periods.pending}
      />
      {canCreate && (
        <form
          className="grid gap-3 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            setPending(true);
            void createSalaryPeriod({
              period_start: text(form, "period_start"),
              period_end: text(form, "period_end"),
              business_date: text(form, "business_date"),
              description: text(form, "description"),
            })
              .then((created) => {
                setSelectedId(created.id);
                setPeriod(created);
                periods.reload();
              })
              .catch((caught: unknown) =>
                fail(caught, "The period could not be created."),
              )
              .finally(() => setPending(false));
          }}
        >
          <h2 className="text-lg font-bold md:col-span-2">New salary period</h2>
          <Field label="Period start">
            <TextInput
              defaultValue={defaultDate}
              name="period_start"
              required
              type="date"
            />
          </Field>
          <Field label="Period end">
            <TextInput
              defaultValue={defaultDate}
              name="period_end"
              required
              type="date"
            />
          </Field>
          <Field label="Business date">
            <TextInput
              defaultValue={defaultDate}
              name="business_date"
              required
              type="date"
            />
          </Field>
          <Field label="Description">
            <TextInput name="description" required />
          </Field>
          <button className="button" disabled={pending} type="submit">
            Save period
          </button>
        </form>
      )}
      {period && (
        <section aria-labelledby="salary-detail-title" className="grid gap-4">
          <h2 className="text-lg font-bold" id="salary-detail-title">
            Salary period
          </h2>
          <ServerFacts
            rows={[
              { label: "Start", value: period.period_start },
              { label: "End", value: period.period_end },
              { label: "Business date", value: period.business_date },
              { label: "Status", value: period.status },
              { label: "Description", value: period.description },
            ]}
          />
          {action === "edit" && canUpdate && period.status === "DRAFT" && (
            <form
              className="grid gap-3 md:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                setPending(true);
                void updateSalaryPeriod(period.id, {
                  period_start: text(form, "period_start"),
                  period_end: text(form, "period_end"),
                  business_date: text(form, "business_date"),
                  description: text(form, "description"),
                })
                  .then((updated) => {
                    setPeriod(updated);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The period could not be updated."),
                  )
                  .finally(() => setPending(false));
              }}
            >
              <Field label="Period start">
                <TextInput
                  defaultValue={period.period_start}
                  name="period_start"
                  required
                  type="date"
                />
              </Field>
              <Field label="Period end">
                <TextInput
                  defaultValue={period.period_end}
                  name="period_end"
                  required
                  type="date"
                />
              </Field>
              <Field label="Business date">
                <TextInput
                  defaultValue={period.business_date}
                  name="business_date"
                  required
                  type="date"
                />
              </Field>
              <Field label="Description">
                <TextInput
                  defaultValue={period.description}
                  name="description"
                  required
                />
              </Field>
              <button className="button" disabled={pending} type="submit">
                Save period changes
              </button>
            </form>
          )}
          <h3 className="font-semibold">Staff lines</h3>
          {period.lines.map((line) => (
            <p key={line.id}>
              {line.staff.staff_code} — {line.staff.display_name}: {line.amount}{" "}
              {line.currency} · {line.payment_method}
            </p>
          ))}
          {period.status === "DRAFT" && canUpdate && (
            <div className="grid gap-3">
              {lines.map((line, index) => (
                <div
                  className="grid gap-3 rounded-lg border border-slate-200 p-3 md:grid-cols-2"
                  key={line.key}
                >
                  <Field label="Staff">
                    <SelectInput
                      onChange={(event) =>
                        setLines((current) =>
                          current.map((item) =>
                            item.key === line.key
                              ? { ...item, staffId: event.target.value }
                              : item,
                          ),
                        )
                      }
                      value={line.staffId}
                    >
                      <option value="">Select staff</option>
                      {staff.map((person) => (
                        <option key={person.id} value={person.id}>
                          {person.staff_code} — {person.display_name}
                        </option>
                      ))}
                    </SelectInput>
                  </Field>
                  <Field
                    error={index === 0 ? fields.amount : undefined}
                    label={`Amount (${currency})`}
                  >
                    <TextInput
                      inputMode="decimal"
                      onChange={(event) =>
                        setLines((current) =>
                          current.map((item) =>
                            item.key === line.key
                              ? { ...item, amount: event.target.value }
                              : item,
                          ),
                        )
                      }
                      value={line.amount}
                    />
                  </Field>
                  <Field label="Method">
                    <SelectInput
                      onChange={(event) =>
                        setLines((current) =>
                          current.map((item) =>
                            item.key === line.key
                              ? {
                                  ...item,
                                  method: event.target.value as PaymentMethod,
                                }
                              : item,
                          ),
                        )
                      }
                      value={line.method}
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
                      onChange={(event) =>
                        setLines((current) =>
                          current.map((item) =>
                            item.key === line.key
                              ? { ...item, reference: event.target.value }
                              : item,
                          ),
                        )
                      }
                      value={line.reference}
                    />
                  </Field>
                  <Field label="Description">
                    <TextInput
                      onChange={(event) =>
                        setLines((current) =>
                          current.map((item) =>
                            item.key === line.key
                              ? { ...item, description: event.target.value }
                              : item,
                          ),
                        )
                      }
                      value={line.description}
                    />
                  </Field>
                  <button
                    className="underline"
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
              ))}
              <button
                className="underline"
                disabled={lines.length >= 200}
                onClick={() => setLines((current) => [...current, blankLine()])}
                type="button"
              >
                Add staff line
              </button>
              <button
                className="button"
                disabled={pending}
                onClick={() => {
                  const payload = linePayload();
                  if (!payload) return;
                  setPending(true);
                  void replaceSalaryLines(period.id, { lines: payload })
                    .then((updated) => {
                      setPeriod(updated);
                      periods.reload();
                    })
                    .catch((caught: unknown) =>
                      fail(caught, "The lines could not be replaced."),
                    )
                    .finally(() => setPending(false));
                }}
                type="button"
              >
                Replace staff lines
              </button>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            {period.status === "DRAFT" && canUpdate && (
              <button
                className="underline"
                onClick={() => setAction("edit")}
                type="button"
              >
                Edit period
              </button>
            )}
            {period.status === "DRAFT" && canSubmit && (
              <button
                className="underline"
                onClick={() => setAction("submit")}
                type="button"
              >
                Submit
              </button>
            )}
            {period.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline"
                onClick={() => setAction("approve")}
                type="button"
              >
                Approve
              </button>
            )}
            {period.status === "PENDING_APPROVAL" && canApprove && (
              <button
                className="underline"
                onClick={() => setAction("reject")}
                type="button"
              >
                Reject
              </button>
            )}
            {period.status === "DRAFT" && canUpdate && (
              <button
                className="underline"
                onClick={() => setAction("cancel")}
                type="button"
              >
                Cancel draft
              </button>
            )}
            {period.status === "POSTED" && canVoid && (
              <button
                className="underline"
                onClick={() => setAction("void")}
                type="button"
              >
                Void period
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
                void submitSalaryPeriod(period.id)
                  .then((row) => {
                    setPeriod(row);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The period could not be submitted."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Submit this salary period?"
            />
          )}
          {action === "approve" && (
            <CommandForm
              confirmLabel="Confirm approval"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                const mappings = cashLines.map((line) => ({
                  line_id: line.id,
                  cash_session_id: text(form, `session-${line.id}`),
                }));
                if (mappings.some((row) => !row.cash_session_id)) {
                  setFields({
                    cash_session_id: "Map every cash line to an open session.",
                  });
                  return;
                }
                setPending(true);
                void approveSalaryPeriod(
                  period.id,
                  mappings.length ? { cash_sessions: mappings } : {},
                )
                  .then(() => getSalaryPeriod(period.id))
                  .then((row) => {
                    setPeriod(row);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The period could not be approved."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Approve salary period"
            >
              {cashLines.length === 0 && <p>No cash line is mapped.</p>}
              {cashLines.map((line) => (
                <CashSessionSelect
                  currency={line.currency}
                  key={line.id}
                  label={`Session for ${line.staff.display_name}`}
                  name={`session-${line.id}`}
                  sessions={sessions.sessions}
                />
              ))}
              {period.lines
                .filter((line) => line.payment_method !== "CASH")
                .map((line) => (
                  <p key={line.id}>
                    {line.staff.display_name} is {line.payment_method} and is
                    not mapped to a session.
                  </p>
                ))}
            </CommandForm>
          )}
          {action === "reject" && (
            <CommandForm
              confirmLabel="Confirm rejection"
              error={formError}
              onCancel={() => setAction(null)}
              onSubmit={(form) => {
                setPending(true);
                void rejectSalaryPeriod(period.id, {
                  rejection_reason: text(form, "rejection_reason"),
                })
                  .then((row) => {
                    setPeriod(row);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The period could not be rejected."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Reject salary period"
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
                void cancelSalaryPeriod(period.id, {
                  reason: text(form, "reason"),
                })
                  .then((row) => {
                    setPeriod(row);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The draft could not be cancelled."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Cancel salary draft"
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
                const mappings = cashLines.map((line) => ({
                  line_id: line.id,
                  cash_session_id: text(form, `void-${line.id}`),
                }));
                if (mappings.some((row) => !row.cash_session_id)) {
                  setFields({
                    cash_session_id:
                      "Map every cash line to a destination session.",
                  });
                  return;
                }
                const body: VoidSalaryRequest = {
                  reason: text(form, "reason"),
                };
                const date = text(form, "business_date");
                if (date) body.business_date = date;
                if (mappings.length) body.cash_sessions = mappings;
                setPending(true);
                void voidSalaryPeriod(period.id, body)
                  .then((result) => {
                    setPeriod(result.salary_period);
                    setAction(null);
                    periods.reload();
                  })
                  .catch((caught: unknown) =>
                    fail(caught, "The period could not be voided."),
                  )
                  .finally(() => setPending(false));
              }}
              pending={pending}
              title="Void salary period"
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
              {cashLines.map((line) => (
                <CashSessionSelect
                  currency={line.currency}
                  key={line.id}
                  label={`Destination session for ${line.staff.display_name}`}
                  name={`void-${line.id}`}
                  sessions={sessions.sessions}
                />
              ))}
            </CommandForm>
          )}
        </section>
      )}
    </div>
  );
}
